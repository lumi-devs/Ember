import discord
from discord.ext import commands
import aiosqlite
import asyncio
import os
from datetime import datetime, timezone, timedelta
from typing import Optional, List

DB_PATH = "data/sticky_roles.db"
STICKY_ROLE_DAYS = 7
CLEANUP_INTERVAL_SECONDS = 3600


class StickyRoles(commands.Cog):
    """Remembers a member's roles when they leave and restores them if they rejoin in time."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._db: Optional[aiosqlite.Connection] = None
        self._db_lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None
        self.bot.loop.create_task(self._init_database())

    async def _init_database(self) -> None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        async with self._db_lock:
            self._db = await aiosqlite.connect(DB_PATH)
            self._db.row_factory = aiosqlite.Row
            await self._db.execute("""
                CREATE TABLE IF NOT EXISTS sticky_roles (
                    guild_id TEXT NOT NULL,
                    user_id  TEXT NOT NULL,
                    role_ids TEXT NOT NULL,
                    left_at  TEXT NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                )
            """)
            await self._db.commit()

        self._cleanup_task = asyncio.create_task(self._periodic_cleanup())

    async def _periodic_cleanup(self) -> None:
        """Purge entries older than the sticky window so the table doesn't grow forever."""
        while True:
            await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
            cutoff = (datetime.now(timezone.utc) - timedelta(days=STICKY_ROLE_DAYS)).isoformat()
            async with self._db_lock:
                try:
                    await self._db.execute(
                        "DELETE FROM sticky_roles WHERE left_at < ?", (cutoff,)
                    )
                    await self._db.commit()
                except Exception as e:
                    print(f"[StickyRoles] Cleanup error: {e}")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        """Record a leaving member's roles so they can be restored on rejoin."""
        role_ids = [
            str(role.id)
            for role in member.roles
            if not role.is_default() and not role.managed
        ]
        if not role_ids:
            return

        left_at = datetime.now(timezone.utc).isoformat()
        async with self._db_lock:
            try:
                await self._db.execute(
                    "INSERT OR REPLACE INTO sticky_roles (guild_id, user_id, role_ids, left_at) VALUES (?, ?, ?, ?)",
                    (str(member.guild.id), str(member.id), ",".join(role_ids), left_at),
                )
                await self._db.commit()
            except Exception as e:
                print(f"[StickyRoles] Failed to save roles for {member.id}: {e}")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """Restore a rejoining member's roles if they left within the sticky window."""
        async with self._db_lock:
            try:
                async with self._db.execute(
                    "SELECT role_ids, left_at FROM sticky_roles WHERE guild_id = ? AND user_id = ?",
                    (str(member.guild.id), str(member.id)),
                ) as cursor:
                    row = await cursor.fetchone()
            except Exception as e:
                print(f"[StickyRoles] Lookup error for {member.id}: {e}")
                return

        if row is None:
            return

        try:
            left_at = datetime.fromisoformat(row["left_at"])
        except ValueError:
            left_at = None

        async with self._db_lock:
            await self._db.execute(
                "DELETE FROM sticky_roles WHERE guild_id = ? AND user_id = ?",
                (str(member.guild.id), str(member.id)),
            )
            await self._db.commit()

        if left_at is None:
            return
        if datetime.now(timezone.utc) - left_at > timedelta(days=STICKY_ROLE_DAYS):
            return  # expired, nothing to restore

        role_ids = [int(rid) for rid in row["role_ids"].split(",") if rid]
        guild = member.guild
        roles_to_restore: List[discord.Role] = []
        for role_id in role_ids:
            role = guild.get_role(role_id)
            if role is None or role.managed or role.is_default():
                continue
            if guild.me.top_role <= role:
                continue  # bot can't assign roles at/above its own top role
            roles_to_restore.append(role)

        if not roles_to_restore:
            return

        try:
            await member.add_roles(*roles_to_restore, reason="Sticky roles: restored on rejoin")
        except discord.Forbidden:
            print(f"[StickyRoles] Missing permissions to restore roles for {member.id}")
        except discord.HTTPException as e:
            print(f"[StickyRoles] Failed to restore roles for {member.id}: {e}")

    async def cog_unload(self) -> None:
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except Exception:
                pass
        if self._db:
            await self._db.close()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StickyRoles(bot))
