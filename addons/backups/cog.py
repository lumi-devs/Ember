import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path

import discord
from discord.ext import commands, tasks

from core import EmberCog, Translator, cog_i18n
from utils.cards import make_error_card, make_info_card, make_success_card, make_warning_card

from . import config
from .b2_manager import B2BackupManager

_ = Translator("backups", __file__)

log = logging.getLogger("ember.backups")


@cog_i18n(_)
class BackupCog(EmberCog):
    """Cog for managing database backups to Backblaze B2."""

    def __init__(self, bot):
        super().__init__(bot)
        self.b2_manager = B2BackupManager()
        self.last_backup = None

    async def cog_load(self):
        if config.ENABLE_SCHEDULED_BACKUP:
            self.scheduled_backup.start()

    async def cog_unload(self) -> None:
        if self.scheduled_backup.is_running():
            self.scheduled_backup.cancel()

    @commands.group(name="backup", invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def backup_cmd(self, ctx: commands.Context):
        """Backup management commands. Use !backup now to create a backup."""
        await ctx.send(
            view=make_info_card(
                "📦 Backup Commands",
                "`!backup now` — Create immediate backup\n"
                "`!backup status` — Show backup status\n"
                "`!backup list` — List available backups\n"
                "`!backup cleanup` — Remove old backups\n"
                "`!backup restore <file_id>` — Download a backup",
            )
        )

    @backup_cmd.command(name="now")
    @commands.has_permissions(administrator=True)
    async def backup_now(self, ctx: commands.Context):
        """Create a backup immediately."""
        message = await ctx.send(view=make_info_card("📦 Backup", "🔄 Starting backup..."))

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, lambda: asyncio.run(self._do_backup()))

        if result.get("success"):
            uploaded_count = len(result.get("uploaded", []))
            failed_count = len(result.get("failed", []))

            lines = [f"Successfully backed up **{uploaded_count}** file(s)."]

            if result.get("uploaded"):
                files_text = "\n".join(f"• `{f['name']}`" for f in result["uploaded"][:10])
                if len(result["uploaded"]) > 10:
                    files_text += f"\n*...and {len(result['uploaded']) - 10} more*"
                lines.append(files_text)

            if failed_count > 0:
                lines.append(f"⚠️ {failed_count} file(s) failed to upload.")

            self.last_backup = datetime.now(UTC)
            await message.edit(view=make_success_card("✅ Backup Complete", "\n\n".join(lines)))
        else:
            await message.edit(
                view=make_error_card("❌ Backup Failed", result.get("error", "Unknown error"))
            )

    async def _do_backup(self) -> dict:
        """Perform the actual backup."""
        result = await self.b2_manager.backup_all_databases()

        if result.get("success"):
            await asyncio.get_event_loop().run_in_executor(
                None, self.b2_manager.cleanup_old_backups
            )

        return result

    @backup_cmd.command(name="status")
    @commands.has_permissions(administrator=True)
    async def backup_status(self, ctx: commands.Context):
        """Show backup system status."""
        status = self.b2_manager.get_backup_status()

        configured = "✅ Configured" if status["configured"] else "❌ Not configured"
        authorized = "✅ Authorized" if status["authorized"] else "❌ Not authorized"
        bucket = status["bucket"] or "Not set"
        last = discord.utils.format_dt(self.last_backup, style="R") if self.last_backup else "Never"
        retention = f"{status['retention_days']} days"
        interval = f"{status['interval_minutes']} minutes"

        data_dir = Path(status["data_dir"])
        db_info = (
            f"{data_dir} ({len(list(data_dir.glob('*.db')))} databases)"
            if data_dir.exists()
            else str(data_dir)
        )

        body = (
            f"**Configuration:** {configured}\n"
            f"**B2 Authorization:** {authorized}\n"
            f"**B2 Bucket:** {bucket}\n"
            f"**Last Backup:** {last}\n"
            f"**Retention Period:** {retention}\n"
            f"**Backup Interval:** {interval}\n"
            f"**Data Directory:** {db_info}"
        )

        await ctx.send(view=make_info_card("📦 Backup Status", body))

    @backup_cmd.command(name="list")
    @commands.has_permissions(administrator=True)
    async def backup_list(self, ctx: commands.Context, limit: int = 10):
        """List available backups."""
        message = await ctx.send(view=make_info_card("📦 Backups", "🔄 Fetching backup list..."))

        loop = asyncio.get_event_loop()
        backups = await loop.run_in_executor(None, self.b2_manager.list_backups, limit)

        if not backups:
            await message.edit(
                view=make_warning_card("📦 No Backups", "No backups found or B2 not configured.")
            )
            return

        by_date: dict = {}
        for backup in backups:
            date_str = backup["upload_time"].strftime("%Y-%m-%d")
            if date_str not in by_date:
                by_date[date_str] = []
            by_date[date_str].append(backup)

        lines = [f"**{len(backups)} backup(s) found** (showing most recent)"]
        for date, files in sorted(by_date.items(), reverse=True)[:5]:
            file_text = "\n".join(
                f"• `{b['name']}` ({b['size'] / 1024 / 1024:.1f} MB)" for b in files[:3]
            )
            if len(files) > 3:
                file_text += f"\n*...and {len(files) - 3} more*"
            lines.append(f"**{date}**\n{file_text}")

        await message.edit(
            view=make_info_card(
                "📦 Available Backups",
                "\n\n".join(lines),
                footer="Use !backup restore <file_id> to download a backup",
            )
        )

    @backup_cmd.command(name="cleanup")
    @commands.has_permissions(administrator=True)
    async def backup_cleanup(self, ctx: commands.Context, days: int = None):
        """Remove backups older than the specified days (default: retention period)."""
        if days is None:
            days = config.BACKUP_RETENTION_DAYS

        message = await ctx.send(
            view=make_info_card("🗑️ Cleanup", f"🔄 Cleaning up backups older than {days} days...")
        )

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, self.b2_manager.cleanup_old_backups, days)

        if result.get("success"):
            await message.edit(
                view=make_success_card(
                    "🗑️ Cleanup Complete",
                    f"Deleted **{result.get('count', 0)}** old backup(s).",
                )
            )
        else:
            await message.edit(
                view=make_error_card("❌ Cleanup Failed", result.get("error", "Unknown error"))
            )

    @backup_cmd.command(name="restore")
    @commands.has_permissions(administrator=True)
    async def backup_restore(self, ctx: commands.Context, file_id: str, *, filename: str = None):
        """Download a backup file by its file ID."""
        message = await ctx.send(view=make_info_card("📦 Restore", "🔄 Downloading backup..."))

        if not filename:
            filename = f"backup_{file_id[:8]}.db"

        dest_path = Path("backups") / filename
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        loop = asyncio.get_event_loop()
        success = await loop.run_in_executor(
            None,
            lambda: asyncio.run(self.b2_manager.download_backup(file_id, dest_path)),
        )

        if success:
            await message.edit(
                view=make_success_card("✅ Download Complete", f"Backup saved to: `{dest_path}`")
            )
        else:
            await message.edit(
                view=make_error_card("❌ Download Failed", "Check file ID and B2 configuration.")
            )

    @tasks.loop(minutes=config.BACKUP_INTERVAL_MINUTES)
    async def scheduled_backup(self):
        """Automatically backup databases on schedule."""
        if not config.is_backup_configured():
            return

        self.logger.info("🔄 Running scheduled backup...")

        try:
            result = await self._do_backup()

            if result.get("success"):
                self.last_backup = datetime.now(UTC)
                self.logger.info(
                    f"✅ Scheduled backup complete: {len(result.get('uploaded', []))} files"
                )
            else:
                self.logger.error(f"❌ Scheduled backup failed: {result.get('error')}")

        except Exception as e:
            self.logger.error(f"❌ Scheduled backup error: {e}")

    @scheduled_backup.before_loop
    async def before_scheduled_backup(self):
        """Wait for bot to be ready before starting scheduled backups."""
        await self.bot.wait_until_ready()
        await asyncio.sleep(60)
        self.logger.info(
            f"⏰ Scheduled backup enabled (interval: {config.BACKUP_INTERVAL_MINUTES} minutes)"
        )


setup = BackupCog.make_setup()
