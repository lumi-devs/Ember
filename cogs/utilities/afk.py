from __future__ import annotations

import discord
from discord.ext import commands
import aiosqlite
import asyncio
from datetime import datetime, timezone
import time
from typing import Optional, Dict, Set, List, Tuple, Any
from collections import deque
from dataclasses import dataclass
import os

# Constants
AFK_SET_COLOR = discord.Color.orange()
AFK_REMOVED_COLOR = discord.Color.gold()
AFK_MENTION_COLOR = discord.Color.from_rgb(255, 200, 0)
NICK_PREFIX = "[AFK] "
DB_PATH = "data/afk.db"


@dataclass(frozen=True)
class AFKEntry:
    """Immutable AFK entry for caching."""

    user_id: int
    reason: str
    since: datetime

    @property
    def human_readable_duration(self) -> str:
        """Human-readable time duration."""
        now = datetime.now(timezone.utc)
        delta = now - self.since

        if delta.days > 0:
            return f"{delta.days} day{'s' if delta.days != 1 else ''}"
        elif delta.seconds >= 3600:
            hours = delta.seconds // 3600
            return f"{hours} hour{'s' if hours != 1 else ''}"
        elif delta.seconds >= 60:
            minutes = delta.seconds // 60
            return f"{minutes} minute{'s' if minutes != 1 else ''}"
        else:
            return f"{delta.seconds} second{'s' if delta.seconds != 1 else ''}"


class FastAFK(commands.Cog):
    """High-performance AFK system with optimizations for busy chats."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._db: Optional[aiosqlite.Connection] = None
        self._db_lock = asyncio.Lock()

        # Primary cache - direct dictionary access
        self._afk_cache: Dict[int, AFKEntry] = {}

        # Guild cache for quick lookups
        self._guild_afk_cache: Dict[int, Set[int]] = {}

        # Rate limiting
        self._nick_edit_timestamps: Dict[int, float] = {}
        self._nick_edit_cooldown = 1

        # Message cooldown
        self._last_mentions: Dict[int, float] = {}
        self._mention_cooldown = 5.0

        # AFK removal cooldown
        self._afk_removal_cooldown: Dict[int, float] = {}
        self._min_afk_duration = 2.0

        # Message queue for controlled responses
        self._message_queue: Dict[int, asyncio.Queue] = {}
        self._message_limit_cooldown = 1.5

        # Performance tracking
        self.stats = {
            "messages_processed": 0,
            "fast_path_skipped": 0,
            "afk_removed": 0,
            "mentions_checked": 0,
            "cleanup_passes": 0,
        }

        # Batch processing
        self._write_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._batch_processor_task: Optional[asyncio.Task] = None

        # Cleanup task for stale entries
        self._cleanup_task: Optional[asyncio.Task] = None

        self.bot.loop.create_task(self._init_database())

    async def _init_database(self):
        """Initialize database."""
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

        async with self._db_lock:
            self._db = await aiosqlite.connect(DB_PATH)

            # Keep existing table structure
            await self._db.execute("""
                CREATE TABLE IF NOT EXISTS afk (
                    user_id TEXT PRIMARY KEY,
                    reason TEXT,
                    since TEXT
                )
            """)

            await self._db.commit()

            # Preload cache
            await self._preload_cache()

            # Start background tasks
            self._batch_processor_task = asyncio.create_task(self._batch_processor())
            self._cleanup_task = asyncio.create_task(self._periodic_cleanup())

            print(f"[AFK] Loaded {len(self._afk_cache)} entries")

    async def _preload_cache(self):
        """Preload AFK entries."""
        try:
            async with self._db.execute(
                "SELECT user_id, reason, since FROM afk"
            ) as cursor:
                async for row in cursor:
                    try:
                        user_id = int(row[0])
                        since = datetime.fromisoformat(row[2])
                        self._afk_cache[user_id] = AFKEntry(user_id, row[1], since)
                    except (ValueError, IndexError):
                        continue
        except Exception as e:
            print(f"[AFK] Preload error: {e}")

    async def _periodic_cleanup(self):
        """Periodically clean up stale AFK entries (users who left)."""
        while True:
            await asyncio.sleep(3600)  # Run every hour
            self.stats["cleanup_passes"] += 1

            if not self._afk_cache:
                continue

            # OPTIMIZED: Only check users that are in cache, not all guild members
            to_remove = []
            for user_id in self._afk_cache:
                # Check if user still exists in any guild
                user = self.bot.get_user(user_id)
                if not user:
                    to_remove.append(user_id)

            # Remove them
            for user_id in to_remove:
                await self._remove_afk_internal(user_id)
                print(f"[AFK] Cleaned up entry for left user: {user_id}")

    async def _remove_afk_internal(self, user_id: int):
        """Internal AFK removal without cooldown checks."""
        if user_id in self._afk_cache:
            del self._afk_cache[user_id]

        # Update guild cache
        for guild_id in self._guild_afk_cache:
            self._guild_afk_cache[guild_id].discard(user_id)

        # Queue for deletion
        await self._write_queue.put(("remove_afk", user_id))

    # ─── OPTIMIZED ON_MESSAGE ─────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Ultra-optimized message handler."""
        if message.author.bot:
            return

        self.stats["messages_processed"] += 1

        # FAST PATH: If no AFK users and no mentions, just process commands
        if not self._afk_cache and not message.mentions:
            self.stats["fast_path_skipped"] += 1
            await self.bot.process_commands(message)
            return

        # Check if author is AFK - DIRECT CACHE LOOKUP
        entry = self._afk_cache.get(message.author.id)

        if entry:
            # FIXED: Proper command prefix detection
            is_command = False
            try:
                prefixes = await self.bot.get_prefix(message)
                is_command = message.content.startswith(tuple(prefixes))
            except:
                # Fallback to simple check if something fails
                is_command = message.content.startswith(("!", "?", ".", "/"))

            if is_command:
                # Check if it's an AFK command (shouldn't remove AFK)
                content = message.content.strip()
                for prefix in prefixes if "prefixes" in locals() else ("!",):
                    if content.startswith(prefix):
                        cmd_part = (
                            content[len(prefix) :].split()[0].lower()
                            if content[len(prefix) :]
                            else ""
                        )
                        if cmd_part in ("afk", "afklist", "afkstats"):
                            # Don't remove AFK, just process command
                            await self.bot.process_commands(message)
                            return
                        break

            # Remove AFK for non-command messages
            current_time = time.time()
            last_set = self._afk_removal_cooldown.get(message.author.id, 0)

            if current_time - last_set >= self._min_afk_duration:
                # Remove from cache immediately
                del self._afk_cache[message.author.id]

                # Queue DB deletion
                await self._write_queue.put(("remove_afk", message.author.id))
                self._afk_removal_cooldown[message.author.id] = current_time
                self.stats["afk_removed"] += 1

                # Update nickname
                if message.guild and message.author.display_name.startswith(
                    NICK_PREFIX
                ):
                    asyncio.create_task(
                        self._safe_edit_nick(
                            message.author,
                            message.author.display_name[len(NICK_PREFIX) :],
                        )
                    )

                # Send welcome back message (non-blocking, only create embed if needed)
                if self._can_send_message(message.channel.id):
                    embed = discord.Embed(
                        title="👋 Welcome Back!",
                        description=f"{message.author.mention}, AFK removed.\nYou were AFK for **{entry.human_readable_duration}**.",
                        color=AFK_REMOVED_COLOR,
                    )
                    asyncio.create_task(self._queued_send(message.channel, embed, 20))

        # Process commands (always)
        await self.bot.process_commands(message)

        # Check mentions (only if there are mentions AND cache has entries)
        if message.mentions and self._afk_cache:
            self.stats["mentions_checked"] += 1

            # Rate limit mention messages per channel
            current_time = time.time()
            last_mention = self._last_mentions.get(message.channel.id, 0)

            if current_time - last_mention >= self._mention_cooldown:
                # OPTIMIZED: Check only first mention (enough for most cases)
                user = message.mentions[0]
                entry = self._afk_cache.get(user.id)
                if entry and user.id != message.author.id:
                    # OPTIMIZED: Avoid embed creation if channel is rate limited
                    embed = discord.Embed(
                        title=f"{user.display_name.replace(NICK_PREFIX, '')} is AFK 💤",
                        description=f"**Reason:** {entry.reason}\n**AFK for:** {entry.human_readable_duration}",
                        color=AFK_MENTION_COLOR,
                    )
                    asyncio.create_task(self._queued_send(message.channel, embed, 600))
                    self._last_mentions[message.channel.id] = current_time

    def _can_send_message(self, channel_id: int) -> bool:
        """Quick check if we can send a message to this channel."""
        current_time = time.time()
        last_mention = self._last_mentions.get(channel_id, 0)
        return current_time - last_mention >= self._mention_cooldown

    # ─── COMMANDS ─────────────────────────────────────────────────

    @commands.command(name="afk")
    async def afk_command(self, ctx: commands.Context, *, reason: str = "AFK"):
        """Set yourself as AFK."""
        try:
            await ctx.message.delete()
        except:
            pass

        user = ctx.author
        existing = self._afk_cache.get(user.id)

        if existing:
            if existing.reason != reason:
                # Update AFK
                entry = AFKEntry(user.id, reason, datetime.now(timezone.utc))
                self._afk_cache[user.id] = entry
                await self._write_queue.put(("set_afk", (user.id, reason)))

                embed = discord.Embed(
                    title="✏️ AFK Updated",
                    description=f"{user.mention}, your AFK reason was updated to: **{reason}**",
                    color=AFK_SET_COLOR,
                )
            else:
                return
        else:
            # Set new AFK
            entry = AFKEntry(user.id, reason, datetime.now(timezone.utc))
            self._afk_cache[user.id] = entry
            self._afk_removal_cooldown[user.id] = time.time()

            await self._write_queue.put(("set_afk", (user.id, reason)))

            # Update nickname
            if ctx.guild and not user.display_name.startswith(NICK_PREFIX):
                asyncio.create_task(
                    self._safe_edit_nick(user, f"{NICK_PREFIX}{user.display_name}")
                )

            embed = discord.Embed(
                title="✅ AFK Set",
                description=f"{user.mention}, you are now AFK: **{reason}**",
                color=AFK_SET_COLOR,
            )

        msg = await ctx.send(embed=embed)
        asyncio.create_task(self._auto_delete_message(msg, 20))

    @commands.command(name="afklist")
    @commands.is_owner()
    async def afk_list(self, ctx: commands.Context):
        """Show all users currently AFK."""
        if not self._afk_cache:
            return await ctx.reply("No users are currently AFK.", mention_author=False)

        lines = []
        for user_id, entry in list(self._afk_cache.items())[:50]:  # Limit to 50
            user = self.bot.get_user(user_id)
            name = user.mention if user else f"Unknown ({user_id})"
            lines.append(
                f"{name} — `{entry.reason}` *(for {entry.human_readable_duration})*"
            )

        embed = discord.Embed(
            title="📜 AFK List",
            description="\n".join(lines) if lines else "No AFK users",
            color=AFK_MENTION_COLOR,
        )
        embed.set_footer(text=f"Total AFK users: {len(self._afk_cache)}")
        await ctx.reply(embed=embed, mention_author=False)

    @commands.command(name="afkstats")
    @commands.is_owner()
    async def afk_stats(self, ctx: commands.Context):
        """Show AFK system stats."""
        embed = discord.Embed(title="📊 AFK System Stats", color=discord.Color.blue())

        embed.add_field(
            name="Active AFK Users", value=len(self._afk_cache), inline=True
        )
        embed.add_field(
            name="Messages Processed",
            value=self.stats["messages_processed"],
            inline=True,
        )
        embed.add_field(
            name="Fast-path Skipped", value=self.stats["fast_path_skipped"], inline=True
        )
        embed.add_field(
            name="AFK Removals", value=self.stats["afk_removed"], inline=True
        )
        embed.add_field(name="Queue Size", value=self._write_queue.qsize(), inline=True)
        embed.add_field(
            name="Cleanup Passes", value=self.stats["cleanup_passes"], inline=True
        )

        # Calculate efficiency
        total_msgs = self.stats["messages_processed"]
        efficiency = (
            (self.stats["fast_path_skipped"] / total_msgs * 100)
            if total_msgs > 0
            else 0
        )
        embed.add_field(
            name="Fast-path Efficiency", value=f"{efficiency:.1f}%", inline=True
        )

        await ctx.reply(embed=embed, mention_author=False)

    @commands.command(name="afkclean")
    @commands.is_owner()
    async def afk_clean(self, ctx: commands.Context):
        """Manually clean up AFK entries for users who left."""
        await ctx.reply("🧹 Cleaning up AFK entries...", mention_author=False)

        # OPTIMIZED: Only check cached users
        removed = 0
        for user_id in list(self._afk_cache.keys()):
            user = self.bot.get_user(user_id)
            if not user:
                await self._remove_afk_internal(user_id)
                removed += 1

        await ctx.reply(
            f"✅ Removed {removed} stale AFK entries.", mention_author=False
        )

    # ─── UTILITY METHODS ──────────────────────────────────────────

    async def _queued_send(
        self,
        channel: discord.TextChannel,
        embed: discord.Embed,
        delete_after: Optional[int] = None,
    ):
        """Queue a message to be sent."""
        if channel.id not in self._message_queue:
            self._message_queue[channel.id] = asyncio.Queue()
            asyncio.create_task(self._process_channel_queue(channel.id))

        await self._message_queue[channel.id].put((channel, embed, delete_after))

    async def _process_channel_queue(self, channel_id: int):
        """Process queued messages for a channel."""
        queue = self._message_queue[channel_id]
        while not queue.empty():
            channel, embed, delete_after = await queue.get()
            try:
                msg = await channel.send(embed=embed)
                if delete_after:
                    asyncio.create_task(self._auto_delete_message(msg, delete_after))
            except:
                pass
            finally:
                queue.task_done()
                await asyncio.sleep(self._message_limit_cooldown)

        del self._message_queue[channel_id]

    async def _safe_edit_nick(self, member: discord.Member, new_nick: str):
        """Rate-limited nickname editing."""
        current_time = time.time()
        last_edit = self._nick_edit_timestamps.get(member.id, 0)

        if current_time - last_edit < self._nick_edit_cooldown:
            return

        try:
            await member.edit(nick=new_nick)
            self._nick_edit_timestamps[member.id] = current_time
        except:
            pass

    async def _auto_delete_message(self, message: discord.Message, delay: int):
        """Auto-delete a message."""
        await asyncio.sleep(delay)
        try:
            await message.delete()
        except:
            pass

    async def _batch_processor(self):
        """Process DB writes in batches."""
        batch_inserts: List[Tuple[str, str, str]] = []
        batch_deletes: List[str] = []
        last_flush = time.time()
        flush_interval = 5.0

        while True:
            try:
                try:
                    op_type, data = await asyncio.wait_for(
                        self._write_queue.get(), timeout=flush_interval
                    )
                except asyncio.TimeoutError:
                    op_type = None

                current_time = time.time()

                if op_type:
                    if op_type == "set_afk":
                        user_id, reason = data
                        since = datetime.now(timezone.utc).isoformat()
                        batch_inserts.append((str(user_id), reason, since))
                    elif op_type == "remove_afk":
                        batch_deletes.append(str(data))
                    self._write_queue.task_done()

                if (
                    current_time - last_flush >= flush_interval
                    or len(batch_inserts) + len(batch_deletes) >= 50
                ):
                    if batch_inserts or batch_deletes:
                        await self._flush_batch(batch_inserts, batch_deletes)
                        batch_inserts.clear()
                        batch_deletes.clear()
                        last_flush = current_time

            except asyncio.CancelledError:
                if batch_inserts or batch_deletes:
                    await self._flush_batch(batch_inserts, batch_deletes)
                break
            except Exception as e:
                print(f"[AFK] Batch error: {e}")
                await asyncio.sleep(1)

    async def _flush_batch(self, inserts: List[Tuple], deletes: List[str]):
        """Flush batch to database."""
        if not inserts and not deletes:
            return

        async with self._db_lock:
            try:
                await self._db.execute("BEGIN TRANSACTION")

                if inserts:
                    await self._db.executemany(
                        "REPLACE INTO afk (user_id, reason, since) VALUES (?, ?, ?)",
                        inserts,
                    )

                if deletes:
                    placeholders = ",".join("?" for _ in deletes)
                    await self._db.execute(
                        f"DELETE FROM afk WHERE user_id IN ({placeholders})", deletes
                    )

                await self._db.commit()
            except:
                await self._db.rollback()

    async def cog_unload(self):
        """Clean shutdown."""
        for task in [self._batch_processor_task, self._cleanup_task]:
            if task:
                task.cancel()
                try:
                    await task
                except:
                    pass

        if self._write_queue.qsize() > 0:
            await self._write_queue.join()

        if self._db:
            await self._db.close()


async def setup(bot: commands.Bot):
    await bot.add_cog(FastAFK(bot))
