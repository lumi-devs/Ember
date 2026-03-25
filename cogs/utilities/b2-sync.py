"""
b2_sync.py — Backblaze B2 data sync & backup cog
=================================================
Replaces the GitHub-based sync with proper object storage.

Env vars required:
    B2_KEY_ID      — Backblaze application key ID
    B2_APP_KEY     — Backblaze application key (secret)
    B2_ENDPOINT    — e.g. https://s3.us-west-004.backblazeb2.com
    B2_BUCKET      — bucket name
"""

import discord
from discord.ext import commands, tasks
from pathlib import Path
import os
import logging
import asyncio
from datetime import datetime, timezone
from typing import Optional

import boto3
from botocore.exceptions import ClientError

# ── Configuration ─────────────────────────────────────────────────────────────
B2_KEY_ID    = os.environ.get("B2_KEY_ID")
B2_APP_KEY   = os.environ.get("B2_APP_KEY")
B2_ENDPOINT  = os.environ.get("B2_ENDPOINT")
B2_BUCKET    = os.environ.get("B2_BUCKET")

DATA_FOLDER         = Path("data")
SYNC_INTERVAL_MINS  = 5     # live sync every 45s
HOURLY_BACKUP_SECS  = 3600    # snapshot every hour
BACKUP_RETAIN_DAYS  = 7       # delete snapshots older than 7 days

# Bucket layout:
#   live/                       ← always-latest mirror of data/
#     staff_analytics.db
#     confession.db
#     ...
#   backups/
#     2026-03-23/
#       14-00/
#         staff_analytics.db
#         ...

logger = logging.getLogger(__name__)


class B2Sync(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot    = bot
        self._s3    = None          # boto3 S3 client
        self._ready = False

        if all([B2_KEY_ID, B2_APP_KEY, B2_ENDPOINT, B2_BUCKET]):
            bot.loop.create_task(self._startup())
        else:
            logger.warning("B2 credentials not set — sync disabled. "
                           "Set B2_KEY_ID, B2_APP_KEY, B2_ENDPOINT, B2_BUCKET.")

    def cog_unload(self):
        self.live_sync_loop.cancel()
        self.hourly_backup_loop.cancel()

    # ── Startup ────────────────────────────────────────────────────────────────

    async def _startup(self):
        await self.bot.wait_until_ready()

        try:
            self._s3 = await asyncio.to_thread(lambda: boto3.client(
                "s3",
                endpoint_url          = B2_ENDPOINT,
                aws_access_key_id     = B2_KEY_ID,
                aws_secret_access_key = B2_APP_KEY,
            ))
            # Quick connectivity check
            await asyncio.to_thread(self._s3.list_objects_v2,
                                    Bucket=B2_BUCKET, MaxKeys=1)
            self._ready = True
            print("✅ Backblaze B2 connected")
        except Exception as e:
            logger.error(f"❌ B2 connection failed: {e}")
            return

        await self._recover_missing_files()
        self.live_sync_loop.start()
        self.hourly_backup_loop.start()

    # ── Local file helpers ─────────────────────────────────────────────────────

    def _local_files(self) -> list[Path]:
        """All valid files under data/, skipping SQLite temp files."""
        files = []
        if not DATA_FOLDER.exists():
            return files
        for root, _, filenames in os.walk(DATA_FOLDER):
            for name in filenames:
                if name.endswith(("-journal", "-wal", "-shm")) or name.startswith("."):
                    continue
                files.append(Path(root) / name)
        return files

    # ── S3 helpers ─────────────────────────────────────────────────────────────

    def _upload_sync(self, local_path: Path, bucket_key: str) -> bool:
        """Upload one file to B2. Returns True on success."""
        try:
            self._s3.upload_file(str(local_path), B2_BUCKET, bucket_key)
            return True
        except Exception as e:
            logger.error(f"B2 upload failed ({bucket_key}): {e}")
            return False

    def _download_sync(self, bucket_key: str, local_path: Path) -> bool:
        """Download one file from B2. Returns True on success."""
        try:
            local_path.parent.mkdir(parents=True, exist_ok=True)
            self._s3.download_file(B2_BUCKET, bucket_key, str(local_path))
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "404":
                return False
            logger.error(f"B2 download failed ({bucket_key}): {e}")
            return False
        except Exception as e:
            logger.error(f"B2 download failed ({bucket_key}): {e}")
            return False

    def _list_prefix(self, prefix: str) -> list[str]:
        """List all object keys under a prefix."""
        keys  = []
        token = None
        while True:
            kwargs = {"Bucket": B2_BUCKET, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            resp  = self._s3.list_objects_v2(**kwargs)
            keys += [o["Key"] for o in resp.get("Contents", [])]
            if resp.get("IsTruncated"):
                token = resp["NextContinuationToken"]
            else:
                break
        return keys

    def _delete_prefix_sync(self, prefix: str) -> int:
        """Delete all objects under a prefix. Returns count deleted."""
        keys = self._list_prefix(prefix)
        if not keys:
            return 0
        # B2 supports batch delete (up to 1000 per call)
        for i in range(0, len(keys), 1000):
            batch = [{"Key": k} for k in keys[i:i+1000]]
            self._s3.delete_objects(Bucket=B2_BUCKET, Delete={"Objects": batch})
        return len(keys)

    # ── Live mirror ────────────────────────────────────────────────────────────

    def _live_sync_all_sync(self):
        """Push every local data/ file to live/ in B2."""
        DATA_FOLDER.mkdir(exist_ok=True)
        for file_path in self._local_files():
            rel = file_path.relative_to(DATA_FOLDER).as_posix()
            self._upload_sync(file_path, f"live/{rel}")

    async def _live_sync_all(self):
        await asyncio.to_thread(self._live_sync_all_sync)

    # ── Startup recovery ───────────────────────────────────────────────────────

    async def _recover_missing_files(self):
        """Pull any live/ files from B2 that don't exist locally."""
        def pull():
            keys = self._list_prefix("live/")
            recovered = 0
            for key in keys:
                rel        = key[len("live/"):]     # strip "live/" prefix
                local_path = DATA_FOLDER / rel
                if not local_path.exists():
                    if self._download_sync(key, local_path):
                        print(f"  📥 Recovered: {local_path}")
                        recovered += 1
            return recovered

        count = await asyncio.to_thread(pull)
        if count:
            logger.info(f"Recovered {count} missing file(s) from B2")

    # ── Hourly backup ──────────────────────────────────────────────────────────

    def _hourly_backup_sync(self):
        """
        Snapshot data/ → backups/YYYY-MM-DD/HH-MM/ in B2.
        Then prune snapshots older than BACKUP_RETAIN_DAYS.
        """
        now       = datetime.now(tz=timezone.utc)
        date_str  = now.strftime("%Y-%m-%d")
        time_str  = now.strftime("%H-%M")
        prefix    = f"backups/{date_str}/{time_str}/"

        files = self._local_files()
        if not files:
            return

        pushed = 0
        for file_path in files:
            rel = file_path.relative_to(DATA_FOLDER).as_posix()
            if self._upload_sync(file_path, f"{prefix}{rel}"):
                pushed += 1

        if pushed:
            logger.info(f"Hourly backup → {prefix} ({pushed} files)")

        # Prune old backups
        self._prune_old_backups_sync(now)

    def _prune_old_backups_sync(self, now: datetime):
        """Delete backup day-prefixes older than BACKUP_RETAIN_DAYS."""
        try:
            # List all unique day-level prefixes under backups/
            resp = self._s3.list_objects_v2(
                Bucket=B2_BUCKET, Prefix="backups/", Delimiter="/"
            )
            day_prefixes = [p["Prefix"] for p in resp.get("CommonPrefixes", [])]

            for day_prefix in day_prefixes:
                # day_prefix looks like "backups/2026-03-16/"
                day_name = day_prefix.rstrip("/").split("/")[-1]
                try:
                    day_dt  = datetime.strptime(day_name, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                except ValueError:
                    continue

                age_days = (now - day_dt).days
                if age_days <= BACKUP_RETAIN_DAYS:
                    continue

                deleted = self._delete_prefix_sync(day_prefix)
                if deleted:
                    logger.info(f"Pruned old backup: {day_prefix} ({age_days}d old, {deleted} files)")

        except Exception as e:
            logger.error(f"Backup prune error: {e}")

    async def _hourly_backup(self):
        await asyncio.to_thread(self._hourly_backup_sync)

    # ── Task loops ─────────────────────────────────────────────────────────────

    @tasks.loop(minutes=SYNC_INTERVAL_MINS)
    async def live_sync_loop(self):
        if self._ready:
            await self._live_sync_all()

    @live_sync_loop.before_loop
    async def before_live_sync(self):
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=HOURLY_BACKUP_SECS)
    async def hourly_backup_loop(self):
        if self._ready:
            await self._hourly_backup()

    @hourly_backup_loop.before_loop
    async def before_hourly_backup(self):
        await self.bot.wait_until_ready()
        # Align first backup to the next clock hour (:00)
        now      = datetime.now(tz=timezone.utc)
        wait     = HOURLY_BACKUP_SECS - (now.minute * 60 + now.second)
        logger.info(f"First hourly backup in {wait // 60}m {wait % 60}s")
        await asyncio.sleep(wait)

    # ── Owner commands ─────────────────────────────────────────────────────────

    @commands.command(name="upload")
    @commands.is_owner()
    async def cmd_upload(self, ctx: commands.Context):
        """Push all local data files to B2 live mirror immediately."""
        if not self._ready:
            return await ctx.send("❌ B2 not connected.")
        msg = await ctx.send("⏳ Pushing all files to B2…")
        afk = self.bot.get_cog("AFK")
        if afk and hasattr(afk, "force_checkpoint"):
            await afk.force_checkpoint()
        await self._live_sync_all()
        await msg.edit(content="✅ Live mirror push complete.")

    @commands.command(name="backup")
    @commands.is_owner()
    async def cmd_backup(self, ctx: commands.Context):
        """Take an immediate backup snapshot right now."""
        if not self._ready:
            return await ctx.send("❌ B2 not connected.")
        msg   = await ctx.send("⏳ Taking backup snapshot…")
        await self._hourly_backup()
        stamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d / %H-%M UTC")
        await msg.edit(content=f"✅ Snapshot taken → `backups/{stamp}/`")

    @commands.command(name="b2pull")
    @commands.is_owner()
    async def cmd_pull(self, ctx: commands.Context):
        """Force pull latest files from B2 live mirror (overwrites local)."""
        if not self._ready:
            return await ctx.send("❌ B2 not connected.")
        msg = await ctx.send("⏳ Pulling from B2…")

        def force_pull():
            keys = self._list_prefix("live/")
            for key in keys:
                rel   = key[len("live/"):]
                local = DATA_FOLDER / rel
                self._download_sync(key, local)
            return len(keys)

        count = await asyncio.to_thread(force_pull)
        await msg.edit(content=f"✅ Pulled {count} file(s) from B2 live mirror.")

    @commands.command(name="b2restore")
    @commands.is_owner()
    async def cmd_restore(self, ctx: commands.Context, date: str, hour: str = None):
        """
        Restore from a backup snapshot.

        Usage:
          !b2restore 2026-03-23          → latest snapshot of that day
          !b2restore 2026-03-23 14-00    → specific hour
        """
        if not self._ready:
            return await ctx.send("❌ B2 not connected.")
        msg = await ctx.send(f"⏳ Looking up backup for `{date}`…")

        def find_and_restore():
            base = f"backups/{date}/"

            if not hour:
                # Find latest available hour for that day
                resp  = self._s3.list_objects_v2(
                    Bucket=B2_BUCKET, Prefix=base, Delimiter="/"
                )
                hours = sorted(
                    [p["Prefix"].rstrip("/").split("/")[-1]
                     for p in resp.get("CommonPrefixes", [])],
                    reverse=True,
                )
                if not hours:
                    return None, f"No snapshots found for `{date}`."
                target = hours[0]
            else:
                target = hour

            prefix = f"{base}{target}/"
            keys   = self._list_prefix(prefix)
            if not keys:
                return None, f"Snapshot `{prefix}` is empty or not found."

            restored = []
            for key in keys:
                rel   = key[len(prefix):]
                local = DATA_FOLDER / rel
                if self._download_sync(key, local):
                    restored.append(rel)

            return target, restored

        try:
            result, detail = await asyncio.to_thread(find_and_restore)
        except Exception as e:
            return await msg.edit(content=f"❌ Restore failed: {e}")

        if result is None:
            return await msg.edit(content=f"❌ {detail}")

        lines = "\n".join(f"  • `{f}`" for f in detail[:15])
        extra = f"\n  …and {len(detail)-15} more" if len(detail) > 15 else ""
        await msg.edit(content=(
            f"✅ Restored **{len(detail)}** file(s) from "
            f"`backups/{date}/{result}/`\n{lines}{extra}"
        ))

    @commands.command(name="b2backups")
    @commands.is_owner()
    async def cmd_list_backups(self, ctx: commands.Context):
        """Show available backup days and hourly snapshots."""
        if not self._ready:
            return await ctx.send("❌ B2 not connected.")

        def fetch():
            resp = self._s3.list_objects_v2(
                Bucket=B2_BUCKET, Prefix="backups/", Delimiter="/"
            )
            day_prefixes = sorted(
                [p["Prefix"] for p in resp.get("CommonPrefixes", [])],
                reverse=True,
            )
            result = []
            for day_prefix in day_prefixes[:7]:
                day_name = day_prefix.rstrip("/").split("/")[-1]
                hr_resp  = self._s3.list_objects_v2(
                    Bucket=B2_BUCKET, Prefix=day_prefix, Delimiter="/"
                )
                hours = sorted(
                    [p["Prefix"].rstrip("/").split("/")[-1]
                     for p in hr_resp.get("CommonPrefixes", [])],
                    reverse=True,
                )
                result.append((day_name, hours))
            return result

        data = await asyncio.to_thread(fetch)

        if not data:
            return await ctx.send("📦 No backups found yet.")

        embed = discord.Embed(
            title="📦 B2 Backup History",
            description=(
                f"Retaining **{BACKUP_RETAIN_DAYS}** days · "
                f"Snapshots every **1h** · Live sync every **{SYNC_INTERVAL_MINS}m**"
            ),
            color=discord.Color.orange(),
        )
        for day_name, hours in data:
            val = "  ".join(f"`{h}`" for h in hours[:12])
            if len(hours) > 12:
                val += f"  +{len(hours)-12} more"
            val = val or "*no snapshots yet*"
            embed.add_field(name=f"📅 {day_name}  ({len(hours)} snapshots)",
                            value=val, inline=False)

        await ctx.send(embed=embed)

    @commands.command(name="b2list")
    @commands.is_owner()
    async def cmd_list_files(self, ctx: commands.Context):
        """Show files currently tracked for live syncing."""
        files = self._local_files()
        if not files:
            return await ctx.send("📁 No files in data folder.")
        embed = discord.Embed(title="📁 Tracked Data Files", color=discord.Color.blue())
        rows  = [f"`{f.relative_to(DATA_FOLDER)}`" for f in files[:15]]
        if len(files) > 15:
            rows.append(f"…and {len(files)-15} more")
        embed.add_field(name=f"Total: {len(files)}", value="\n".join(rows))
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(B2Sync(bot))
