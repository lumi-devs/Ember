from __future__ import annotations

import discord
from discord.ext import commands
import aiosqlite
import asyncio
from datetime import datetime, timezone
import time
from typing import Optional, Dict, Set, List, Tuple
from dataclasses import dataclass
import os

# Constants
AFK_SET_COLOR = discord.Color.orange()
AFK_REMOVED_COLOR = discord.Color.gold()
AFK_MENTION_COLOR = discord.Color.from_rgb(255, 200, 0)
NICK_PREFIX = "[AFK] "
DB_PATH = "data/afk.db"

# Commands that should never auto-remove AFK
_AFK_SAFE_COMMANDS = frozenset(("afk", "afklist", "afkstats", "afkclean"))


@dataclass(frozen=True)
class AFKEntry:
    """Immutable AFK entry for caching."""

    user_id: int
    reason: str
    since: datetime

    @property
    def human_readable_duration(self) -> str:
        """Human-readable time since AFK was set."""
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

        # Primary cache  – keyed by user_id
        self._afk_cache: Dict[int, AFKEntry] = {}

        # Guild cache – keyed by guild_id → set of AFK user_ids
        # FIX: populated during preload and set_afk, not just cleaned on removal
        self._guild_afk_cache: Dict[int, Set[int]] = {}

        # Rate limiting
        self._nick_edit_timestamps: Dict[int, float] = {}
        self._nick_edit_cooldown: float = 1.0

        # Per-channel cooldowns (mention notifications vs welcome-back are separate)
        # FIX: split into two dicts so welcome-back and mention paths don't block each other
        self._last_mention_notify: Dict[int, float] = {}    # channel_id → timestamp
        self._last_welcome_back: Dict[int, float] = {}     # channel_id → timestamp
        self._mention_cooldown: float = 5.0
        self._welcome_back_cooldown: float = 5.0

        # AFK removal cooldown (prevents removing AFK the instant it is set)
        self._afk_removal_cooldown: Dict[int, float] = {}
        self._min_afk_duration: float = 2.0

        # Per-channel message queue (rate-limits bot output)
        self._message_queue: Dict[int, asyncio.Queue] = {}
        # FIX: guard against the race where two concurrent messages both see the
        # channel missing and each spawn a processor task
        self._message_queue_lock = asyncio.Lock()
        self._message_limit_cooldown: float = 1.5

        # Performance tracking
        self.stats: Dict[str, int] = {
            "messages_processed": 0,
            "fast_path_skipped": 0,
            "afk_removed": 0,
            "mentions_checked": 0,
            "cleanup_passes": 0,
        }

        # Async write queue (DB writes are batched)
        self._write_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._batch_processor_task: Optional[asyncio.Task] = None

        # Periodic stale-entry cleanup
        self._cleanup_task: Optional[asyncio.Task] = None

        self.bot.loop.create_task(self._init_database())

    # ─── INITIALISATION ───────────────────────────────────────────

    async def _init_database(self) -> None:
        """Initialise the SQLite database, preload cache, start background tasks."""
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

        async with self._db_lock:
            self._db = await aiosqlite.connect(DB_PATH)
            self._db.row_factory = aiosqlite.Row

            await self._db.execute("""
                CREATE TABLE IF NOT EXISTS afk (
                    user_id TEXT PRIMARY KEY,
                    reason  TEXT NOT NULL DEFAULT '',
                    since   TEXT NOT NULL
                )
            """)
            await self._db.commit()

            await self._preload_cache()

            self._batch_processor_task = asyncio.create_task(self._batch_processor())
            self._cleanup_task = asyncio.create_task(self._periodic_cleanup())

        print(f"[AFK] Loaded {len(self._afk_cache)} entries")

    async def _preload_cache(self) -> None:
        """Load all AFK entries from the database into memory."""
        try:
            async with self._db.execute(
                "SELECT user_id, reason, since FROM afk"
            ) as cursor:
                async for row in cursor:
                    try:
                        user_id = int(row["user_id"])
                        since = datetime.fromisoformat(row["since"])
                        if since.tzinfo is None:
                            since = since.replace(tzinfo=timezone.utc)
                        entry = AFKEntry(user_id, row["reason"], since)
                        self._afk_cache[user_id] = entry
                        # FIX: guild_afk_cache is populated from the bot's member cache
                        # after the bot is ready; we can't do it here because guilds may
                        # not be loaded yet.  Instead, _get_guild_afk populates lazily.
                    except (ValueError, IndexError, KeyError):
                        continue
        except Exception as e:
            print(f"[AFK] Preload error: {e}")

    # ─── INTERNAL HELPERS ─────────────────────────────────────────

    def _get_guild_afk(self, guild_id: int) -> Set[int]:
        """Return (and lazily populate) the per-guild AFK user set."""
        if guild_id not in self._guild_afk_cache:
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                return set()
            member_ids = {m.id for m in guild.members}
            self._guild_afk_cache[guild_id] = {
                uid for uid in self._afk_cache if uid in member_ids
            }
        return self._guild_afk_cache[guild_id]

    async def _remove_afk_internal(self, user_id: int) -> None:
        """Remove an AFK entry from all caches and queue a DB deletion."""
        self._afk_cache.pop(user_id, None)

        for guild_set in self._guild_afk_cache.values():
            guild_set.discard(user_id)

        # Prune stale cooldown entries while we're here
        self._afk_removal_cooldown.pop(user_id, None)
        self._nick_edit_timestamps.pop(user_id, None)

        await self._write_queue.put(("remove_afk", user_id))

    async def _periodic_cleanup(self) -> None:
        """Hourly: drop AFK entries for users that are no longer visible to the bot."""
        while True:
            await asyncio.sleep(3600)
            self.stats["cleanup_passes"] += 1

            if not self._afk_cache:
                continue

            to_remove = [
                uid for uid in list(self._afk_cache)
                if self.bot.get_user(uid) is None
            ]
            for uid in to_remove:
                await self._remove_afk_internal(uid)
                print(f"[AFK] Cleaned up stale entry for user {uid}")

            # FIX: prune mention-cooldown dicts so they don't grow forever
            cutoff = time.time() - max(self._mention_cooldown, self._welcome_back_cooldown) * 2
            self._last_mention_notify  = {k: v for k, v in self._last_mention_notify.items()  if v > cutoff}
            self._last_welcome_back    = {k: v for k, v in self._last_welcome_back.items()    if v > cutoff}

    # ─── ON_MESSAGE ───────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Optimised message handler for AFK detection."""
        if message.author.bot:
            return

        self.stats["messages_processed"] += 1

        # Fast-path: nothing to do
        if not self._afk_cache and not message.mentions:
            self.stats["fast_path_skipped"] += 1
            await self.bot.process_commands(message)
            return

        author_id = message.author.id
        entry = self._afk_cache.get(author_id)

        if entry:
            # ── Determine whether this is a bot command ────────────
            is_command = False
            resolved_prefixes: Tuple[str, ...] = ()
            try:
                raw = await self.bot.get_prefix(message)
                resolved_prefixes = tuple(raw) if not isinstance(raw, str) else (raw,)
                is_command = message.content.startswith(resolved_prefixes)
            except Exception:
                fallback = ("!", "?", ".", "/")
                is_command = message.content.startswith(fallback)
                resolved_prefixes = fallback

            if is_command:
                # FIX: ALL commands (not just AFK-safe ones) skip AFK removal.
                # Previously, non-AFK commands fell through and wiped the AFK status.
                cmd_part = ""
                for prefix in resolved_prefixes:
                    if message.content.startswith(prefix):
                        rest = message.content[len(prefix):].strip()
                        cmd_part = rest.split()[0].lower() if rest else ""
                        break

                if cmd_part not in _AFK_SAFE_COMMANDS:
                    # Regular command – process it but do NOT remove AFK
                    await self.bot.process_commands(message)
                    return
                else:
                    # AFK-management command – let it fall through to process_commands
                    # at the bottom without removing AFK
                    await self.bot.process_commands(message)
                    return

            # ── Remove AFK (non-command message) ──────────────────
            current_time = time.time()
            last_set = self._afk_removal_cooldown.get(author_id, 0.0)

            if current_time - last_set >= self._min_afk_duration:
                afk_duration = entry.human_readable_duration  # capture before removal

                # FIX: use _remove_afk_internal so guild cache stays in sync
                await self._remove_afk_internal(author_id)
                self.stats["afk_removed"] += 1

                # Restore nickname
                if (
                    message.guild
                    and isinstance(message.author, discord.Member)
                    and message.author.display_name.startswith(NICK_PREFIX)
                ):
                    asyncio.create_task(
                        self._safe_edit_nick(
                            message.author,
                            message.author.display_name[len(NICK_PREFIX):],
                        )
                    )

                # FIX: use separate welcome-back cooldown, not the mention cooldown
                channel_id = message.channel.id
                now = time.time()
                if now - self._last_welcome_back.get(channel_id, 0.0) >= self._welcome_back_cooldown:
                    self._last_welcome_back[channel_id] = now
                    embed = discord.Embed(
                        title="👋 Welcome Back!",
                        description=(
                            f"{message.author.mention}, AFK removed.\n"
                            f"You were AFK for **{afk_duration}**."
                        ),
                        color=AFK_REMOVED_COLOR,
                    )
                    asyncio.create_task(self._queued_send(message.channel, embed, 20))

        # Always process commands
        await self.bot.process_commands(message)

        # ── Check mentions ─────────────────────────────────────────
        if message.mentions and self._afk_cache:
            self.stats["mentions_checked"] += 1

            current_time = time.time()
            # FIX: respect per-channel cooldown for mention notifications
            if current_time - self._last_mention_notify.get(message.channel.id, 0.0) >= self._mention_cooldown:
                # FIX: check ALL mentions, not just the first one
                for user in message.mentions:
                    if user.id == message.author.id:
                        continue
                    afk_entry = self._afk_cache.get(user.id)
                    if afk_entry:
                        display_name = user.display_name.removeprefix(NICK_PREFIX)
                        embed = discord.Embed(
                            title=f"{display_name} is AFK 💤",
                            description=(
                                f"**Reason:** {afk_entry.reason}\n"
                                f"**AFK for:** {afk_entry.human_readable_duration}"
                            ),
                            color=AFK_MENTION_COLOR,
                        )
                        asyncio.create_task(
                            self._queued_send(message.channel, embed, 600)
                        )
                        self._last_mention_notify[message.channel.id] = current_time
                        break  # one notification per message is enough

    # ─── COMMANDS ─────────────────────────────────────────────────

    @commands.command(name="afk")
    async def afk_command(self, ctx: commands.Context, *, reason: str = "AFK") -> None:
        """Set yourself as AFK."""
        try:
            await ctx.message.delete()
        except Exception:
            pass

        user = ctx.author
        existing = self._afk_cache.get(user.id)

        if existing:
            if existing.reason == reason:
                return  # No change needed
            # Update reason; preserve original since timestamp
            entry = AFKEntry(user.id, reason, existing.since)
            self._afk_cache[user.id] = entry
            # FIX: reset removal cooldown so an immediate message won't strip AFK
            self._afk_removal_cooldown[user.id] = time.time()
            # FIX: pass the canonical since time so the DB stores the correct value
            await self._write_queue.put(("set_afk", (user.id, reason, existing.since)))
            embed = discord.Embed(
                title="✏️ AFK Updated",
                description=f"{user.mention}, your AFK reason was updated to: **{reason}**",
                color=AFK_SET_COLOR,
            )
        else:
            since = datetime.now(timezone.utc)
            entry = AFKEntry(user.id, reason, since)
            self._afk_cache[user.id] = entry
            self._afk_removal_cooldown[user.id] = time.time()

            # FIX: populate guild cache when setting AFK
            if ctx.guild:
                self._get_guild_afk(ctx.guild.id).add(user.id)

            # FIX: pass the since datetime so the DB stores the correct time
            await self._write_queue.put(("set_afk", (user.id, reason, since)))

            if ctx.guild and isinstance(user, discord.Member):
                if not user.display_name.startswith(NICK_PREFIX):
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
    async def afk_list(self, ctx: commands.Context) -> None:
        """Show all users currently AFK (owner only)."""
        if not self._afk_cache:
            return await ctx.reply("No users are currently AFK.", mention_author=False)

        lines: List[str] = []
        for user_id, e in list(self._afk_cache.items())[:50]:
            user = self.bot.get_user(user_id)
            name = user.mention if user else f"Unknown ({user_id})"
            lines.append(f"{name} — `{e.reason}` *(for {e.human_readable_duration})*")

        embed = discord.Embed(
            title="📜 AFK List",
            description="\n".join(lines) or "No AFK users",
            color=AFK_MENTION_COLOR,
        )
        embed.set_footer(text=f"Total AFK users: {len(self._afk_cache)}")
        await ctx.reply(embed=embed, mention_author=False)

    @commands.command(name="afkstats")
    @commands.is_owner()
    async def afk_stats(self, ctx: commands.Context) -> None:
        """Show AFK system performance stats (owner only)."""
        total_msgs = self.stats["messages_processed"]
        efficiency = (
            self.stats["fast_path_skipped"] / total_msgs * 100
            if total_msgs > 0
            else 0.0
        )

        embed = discord.Embed(title="📊 AFK System Stats", color=discord.Color.blue())
        embed.add_field(name="Active AFK users",    value=len(self._afk_cache),                  inline=True)
        embed.add_field(name="Messages processed",  value=total_msgs,                            inline=True)
        embed.add_field(name="Fast-path skipped",   value=self.stats["fast_path_skipped"],       inline=True)
        embed.add_field(name="AFK removals",        value=self.stats["afk_removed"],             inline=True)
        embed.add_field(name="Mentions checked",    value=self.stats["mentions_checked"],        inline=True)
        embed.add_field(name="Write queue size",    value=self._write_queue.qsize(),             inline=True)
        embed.add_field(name="Cleanup passes",      value=self.stats["cleanup_passes"],          inline=True)
        embed.add_field(name="Fast-path efficiency",value=f"{efficiency:.1f}%",                  inline=True)
        await ctx.reply(embed=embed, mention_author=False)

    @commands.command(name="afkclean")
    @commands.is_owner()
    async def afk_clean(self, ctx: commands.Context) -> None:
        """Manually remove AFK entries for users no longer in any guild (owner only)."""
        await ctx.reply("🧹 Cleaning up AFK entries…", mention_author=False)
        removed = 0
        for user_id in list(self._afk_cache.keys()):
            if self.bot.get_user(user_id) is None:
                await self._remove_afk_internal(user_id)
                removed += 1
        await ctx.reply(f"✅ Removed {removed} stale AFK entries.", mention_author=False)

    # ─── UTILITY METHODS ──────────────────────────────────────────

    async def _queued_send(
        self,
        channel: discord.TextChannel,
        embed: discord.Embed,
        delete_after: Optional[int] = None,
    ) -> None:
        """Enqueue a message for a channel, spawning a processor if needed."""
        # FIX: use a lock to prevent a race condition where two concurrent
        # callers both find the channel absent and each create a processor task.
        async with self._message_queue_lock:
            if channel.id not in self._message_queue:
                self._message_queue[channel.id] = asyncio.Queue()
                asyncio.create_task(self._process_channel_queue(channel.id))

        await self._message_queue[channel.id].put((channel, embed, delete_after))

    async def _process_channel_queue(self, channel_id: int) -> None:
        """Drain the send queue for one channel, rate-limited."""
        queue = self._message_queue[channel_id]
        while not queue.empty():
            channel, embed, delete_after = await queue.get()
            try:
                msg = await channel.send(embed=embed)
                if delete_after:
                    asyncio.create_task(self._auto_delete_message(msg, delete_after))
            except Exception:
                pass
            finally:
                queue.task_done()
                await asyncio.sleep(self._message_limit_cooldown)

        # Remove the queue entry under the lock so _queued_send sees a clean state
        async with self._message_queue_lock:
            self._message_queue.pop(channel_id, None)

    async def _safe_edit_nick(self, member: discord.Member, new_nick: str) -> None:
        """Edit a member's nickname with a per-user rate limit."""
        current_time = time.time()
        if current_time - self._nick_edit_timestamps.get(member.id, 0.0) < self._nick_edit_cooldown:
            return
        try:
            await member.edit(nick=new_nick)
            self._nick_edit_timestamps[member.id] = current_time
        except Exception:
            pass

    async def _auto_delete_message(self, message: discord.Message, delay: int) -> None:
        """Delete a message after a delay, silently ignoring failures."""
        await asyncio.sleep(delay)
        try:
            await message.delete()
        except Exception:
            pass

    # ─── BATCH DB WRITER ──────────────────────────────────────────

    async def _batch_processor(self) -> None:
        """Collect write operations and flush them to SQLite in batches."""
        # FIX: batch stores (user_id, reason, since_iso) so the stored timestamp
        # matches what the cache holds, not when the batch happened to flush.
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

                if op_type is not None:
                    if op_type == "set_afk":
                        user_id, reason, since_dt = data
                        # FIX: use the provided since datetime, not now()
                        since_iso = since_dt.isoformat()
                        batch_inserts.append((str(user_id), reason, since_iso))
                    elif op_type == "remove_afk":
                        batch_deletes.append(str(data))
                    self._write_queue.task_done()

                should_flush = (
                    current_time - last_flush >= flush_interval
                    or len(batch_inserts) + len(batch_deletes) >= 50
                )
                if should_flush and (batch_inserts or batch_deletes):
                    await self._flush_batch(batch_inserts, batch_deletes)
                    batch_inserts.clear()
                    batch_deletes.clear()
                    last_flush = current_time

            except asyncio.CancelledError:
                if batch_inserts or batch_deletes:
                    await self._flush_batch(batch_inserts, batch_deletes)
                break
            except Exception as e:
                print(f"[AFK] Batch processor error: {e}")
                await asyncio.sleep(1)

    async def _flush_batch(
        self,
        inserts: List[Tuple[str, str, str]],
        deletes: List[str],
    ) -> None:
        """Write a batch of inserts/deletes to the database atomically."""
        if not inserts and not deletes:
            return

        async with self._db_lock:
            try:
                # FIX: don't manually issue BEGIN TRANSACTION — aiosqlite manages
                # isolation automatically; calling it explicitly double-nests and
                # can cause "cannot start a transaction within a transaction".
                if inserts:
                    await self._db.executemany(
                        "INSERT OR REPLACE INTO afk (user_id, reason, since) VALUES (?, ?, ?)",
                        inserts,
                    )
                if deletes:
                    placeholders = ",".join("?" for _ in deletes)
                    await self._db.execute(
                        f"DELETE FROM afk WHERE user_id IN ({placeholders})",
                        deletes,
                    )
                await self._db.commit()
            except Exception as e:
                print(f"[AFK] Flush error: {e}")
                try:
                    await self._db.rollback()
                except Exception:
                    pass

    # ─── SHUTDOWN ─────────────────────────────────────────────────

    async def cog_unload(self) -> None:
        """Graceful shutdown: cancel tasks, flush remaining writes, close DB."""
        for task in (self._batch_processor_task, self._cleanup_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except Exception:
                    pass

        # Drain whatever is still in the write queue
        if not self._write_queue.empty():
            try:
                await asyncio.wait_for(self._write_queue.join(), timeout=10.0)
            except asyncio.TimeoutError:
                print("[AFK] Warning: write queue did not drain within 10 s on unload")

        if self._db:
            try:
                await self._db.close()
            except Exception:
                pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(FastAFK(bot))
