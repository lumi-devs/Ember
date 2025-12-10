import discord
from discord.ext import commands
import aiosqlite
import asyncio
from datetime import datetime, timezone
import time
from typing import Optional, Dict, Tuple, Any, List
import hashlib
from collections import defaultdict
import uvloop
asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())  # Use uvloop for faster I/O

AFK_SET_COLOR = discord.Color.orange()
AFK_REMOVED_COLOR = discord.Color.gold()
AFK_MENTION_COLOR = discord.Color.from_rgb(255, 200, 0)

# Global cache with TTL
class TTLCache:
    """Simple TTL cache for AFK data."""
    def __init__(self, ttl: float = 300):
        self._cache: Dict[str, Tuple[Any, float]] = {}
        self.ttl = ttl
        
    def get(self, key: str) -> Optional[Any]:
        if key not in self._cache:
            return None
        value, timestamp = self._cache[key]
        if time.time() - timestamp > self.ttl:
            del self._cache[key]
            return None
        return value
    
    def set(self, key: str, value: Any):
        self._cache[key] = (value, time.time())
    
    def delete(self, key: str):
        self._cache.pop(key, None)
    
    def clear(self):
        self._cache.clear()


def afk_strip(name: str) -> str:
    """Ultra-fast AFK strip - optimized for speed."""
    return name[6:] if name.startswith("[AFK] ") else (name[5:] if name.startswith("[AFK]") else name)


def human_timedelta(dt: datetime) -> str:
    """Optimized human-readable time difference."""
    now = datetime.now(timezone.utc)
    delta = now - dt
    total_seconds = int(delta.total_seconds())
    
    if total_seconds < 60:
        return f"{total_seconds} second{'s' if total_seconds != 1 else ''}"
    
    minutes = total_seconds // 60
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''}"
    
    days = hours // 24
    return f"{days} day{'s' if days != 1 else ''}"


class FastNicknameChange:
    """Optimized nickname change request."""
    
    __slots__ = ('member_id', 'guild_id', 'new_nick', 'priority', 'created_at', 'attempts', 'hash')
    
    def __init__(self, member: discord.Member, new_nick: str, priority: int = 1):
        self.member_id = member.id
        self.guild_id = member.guild.id
        self.new_nick = new_nick
        self.priority = priority
        self.created_at = time.time()
        self.attempts = 0
        self.hash = hashlib.md5(f"{member.id}{new_nick}".encode()).hexdigest()[:8]
    
    @property
    def is_expired(self) -> bool:
        return time.time() - self.created_at > 30  # 30 second expiry
    
    def __lt__(self, other):
        return self.priority < other.priority


class AFK(commands.Cog):
    """Ultra-fast AFK system with instant message checking."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db_path = "data/afk.db"
        
        # High-performance caching
        self.afk_cache = TTLCache(ttl=600)  # 10 minute TTL
        self.mention_cache: Dict[int, float] = {}  # user_id -> last mention time (rate limiting)
        
        # Nickname queue for batch processing
        self.nick_queue = asyncio.PriorityQueue()
        self.nick_worker_task = None
        
        # Performance counters
        self.stats = {
            'messages': 0,
            'commands': 0,
            'db_reads': 0,
            'db_writes': 0,
            'cache_hits': 0,
            'cache_misses': 0,
            'mentions_sent': 0,
            'nick_changes': 0,
            'nick_errors': 0,
            'skipped_changes': 0
        }
        
        # Connection pooling for database
        self.db_pool = None
        self.is_initialized = False
        
        # Nickname rate limiting
        self.nick_cooldown: Dict[int, float] = {}
        self.last_nick_check = time.time()
        
        # Command batch processing (commands only)
        self.command_batch: List[Tuple[commands.Context, str]] = []
        self.batch_size = 5
        self.batch_processor_task = None
        
        # Start optimized processing
        self.bot.loop.create_task(self._fast_init())

    async def _fast_init(self):
        """Fast initialization with connection pooling."""
        import os
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        # Create connection pool for concurrent access
        self.db_pool = await aiosqlite.connect(
            self.db_path,
            isolation_level=None,  # Faster transactions
            cached_statements=128  # Cache prepared statements
        )
        
        # Enable WAL mode for concurrent reads/writes
        await self.db_pool.execute("PRAGMA journal_mode=WAL")
        await self.db_pool.execute("PRAGMA synchronous=NORMAL")  # Faster with WAL
        await self.db_pool.execute("PRAGMA cache_size=-2000")  # 2MB cache
        
        # Create optimized table with indexes
        await self.db_pool.execute("""
            CREATE TABLE IF NOT EXISTS afk (
                user_id INTEGER PRIMARY KEY,
                reason TEXT,
                since TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create indexes for fast lookups
        await self.db_pool.execute("CREATE INDEX IF NOT EXISTS idx_user_id ON afk(user_id)")
        await self.db_pool.execute("CREATE INDEX IF NOT EXISTS idx_since ON afk(since)")
        
        # Warm cache with current AFK users
        await self._warm_cache()
        
        # Start worker tasks
        self.nick_worker_task = asyncio.create_task(self._nick_worker())
        self.batch_processor_task = asyncio.create_task(self._batch_command_processor())
        
        self.is_initialized = True
        print("[AFK] Ultra-fast system initialized")

    async def _warm_cache(self):
        """Preload AFK users into cache for fast access."""
        async with self.db_pool.execute("SELECT user_id, reason, since FROM afk") as cursor:
            async for row in cursor:
                user_id, reason, since = row
                cache_key = f"afk_{user_id}"
                self.afk_cache.set(cache_key, (reason, since))
        
        print(f"[AFK] Cache warmed with {len([k for k in self.afk_cache._cache if k.startswith('afk_')])} entries")

    async def _nick_worker(self):
        """Process nickname changes with rate limiting."""
        while True:
            try:
                # Get nickname change from queue
                try:
                    nick_change = await asyncio.wait_for(self.nick_queue.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    await asyncio.sleep(0.05)
                    continue
                
                await self._process_single_nick_change(nick_change)
                self.nick_queue.task_done()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[AFK] Nick worker error: {e}")
                await asyncio.sleep(1)

    async def _process_single_nick_change(self, nick_change: FastNicknameChange):
        """Process a single nickname change efficiently."""
        current_time = time.time()
        member_id = nick_change.member_id
        guild_id = nick_change.guild_id
        
        # Check cooldown
        last_change = self.nick_cooldown.get(member_id, 0)
        if current_time - last_change < 0.5:  # 500ms cooldown
            # Requeue with delay
            nick_change.priority = 2
            nick_change.attempts += 1
            
            if nick_change.attempts < 3:
                asyncio.create_task(self._delayed_requeue(nick_change, 0.5 - (current_time - last_change)))
            return
        
        # Get guild and member
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        
        member = guild.get_member(member_id)
        if not member:
            return
        
        # Check if change is still needed
        current_nick = member.display_name
        if current_nick == nick_change.new_nick:
            self.stats['skipped_changes'] += 1
            return
        
        # Check permissions and hierarchy
        if not guild.me.guild_permissions.manage_nicknames:
            self.stats['nick_errors'] += 1
            return
        
        if member.top_role >= guild.me.top_role:
            self.stats['skipped_changes'] += 1
            return
        
        try:
            # Perform the change
            self.nick_cooldown[member_id] = current_time
            await member.edit(nick=nick_change.new_nick)
            self.stats['nick_changes'] += 1
            
        except (discord.Forbidden, discord.HTTPException):
            self.stats['nick_errors'] += 1
        except Exception:
            pass

    async def _delayed_requeue(self, nick_change: FastNicknameChange, delay: float):
        """Requeue a nickname change after delay."""
        await asyncio.sleep(delay)
        if not nick_change.is_expired:
            await self.nick_queue.put(nick_change)

    async def _batch_command_processor(self):
        """Process AFK commands in batches."""
        while True:
            try:
                await asyncio.sleep(0.05)  # Check every 50ms
                
                if not self.command_batch:
                    continue
                
                # Take all commands in batch
                commands = self.command_batch.copy()
                self.command_batch.clear()
                
                # Process them efficiently
                await self._process_command_batch_fast(commands)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[AFK] Command processor error: {e}")
                await asyncio.sleep(1)

    async def _process_command_batch_fast(self, commands: List[Tuple[commands.Context, str]]):
        """Process a batch of AFK commands efficiently."""
        updates = []
        cache_updates = []
        nickname_updates = []
        
        for ctx, reason in commands:
            user_id = ctx.author.id
            
            # Check cache first
            cache_key = f"afk_{user_id}"
            cached = self.afk_cache.get(cache_key)
            
            if cached:
                old_reason, _ = cached
                if old_reason != reason:
                    updates.append((user_id, reason))
                    cache_updates.append((cache_key, (reason, datetime.now(timezone.utc).isoformat())))
                    
                    # Update notification
                    asyncio.create_task(self._send_afk_update(ctx, old_reason, reason))
            else:
                # New AFK
                updates.append((user_id, reason))
                cache_updates.append((cache_key, (reason, datetime.now(timezone.utc).isoformat())))
                
                # Queue nickname update
                if ctx.guild:
                    current_nick = ctx.author.display_name
                    if not current_nick.startswith("[AFK]"):
                        new_nick = f"[AFK] {current_nick[:26]}" if len(current_nick) > 26 else f"[AFK] {current_nick}"
                        nickname_updates.append(FastNicknameChange(ctx.author, new_nick, priority=0))
                
                # Send new AFK notification
                asyncio.create_task(self._send_new_afk(ctx, reason))
            
            self.stats['commands'] += 1
        
        # Batch database update
        if updates:
            await self._batch_set_afk(updates)
            
            # Update cache
            for cache_key, value in cache_updates:
                self.afk_cache.set(cache_key, value)
        
        # Queue nickname updates
        for nick_update in nickname_updates:
            await self.nick_queue.put(nick_update)

    async def _batch_set_afk(self, updates: List[Tuple[int, str]]):
        """Batch update AFK status in database."""
        if not updates:
            return
        
        current_time = datetime.now(timezone.utc).isoformat()
        values = [(user_id, reason, current_time, current_time) for user_id, reason in updates]
        
        await self.db_pool.executemany("""
            INSERT INTO afk (user_id, reason, since, last_update) 
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET 
            reason = excluded.reason,
            last_update = excluded.last_update
        """, values)
        
        self.stats['db_writes'] += len(updates)

    # ─── Instant Message Processing ────────────────────
    async def _check_message_instant(self, message: discord.Message):
        """Check a single message instantly for AFK actions."""
        if message.author.bot:
            return
        
        user_id = message.author.id
        
        # Check if sender is AFK (remove AFK)
        cache_key = f"afk_{user_id}"
        cached = self.afk_cache.get(cache_key)
        
        if cached:
            # User is AFK - remove it
            reason, since = cached
            await self._remove_afk_instant(message, user_id, reason, since)
            self.stats['cache_hits'] += 1
        else:
            # Check database for cache miss
            async with self.db_pool.execute(
                "SELECT reason, since FROM afk WHERE user_id = ?", (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    reason, since = row
                    self.afk_cache.set(cache_key, (reason, since))
                    await self._remove_afk_instant(message, user_id, reason, since)
                    self.stats['cache_misses'] += 1
                    self.stats['db_reads'] += 1
        
        # Check mentions (always run, even if user was AFK)
        if message.mentions:
            await self._check_mentions_instant(message)
        
        self.stats['messages'] += 1

    async def _remove_afk_instant(self, message: discord.Message, user_id: int, reason: str, since: str):
        """Remove AFK status instantly."""
        # Remove from cache
        self.afk_cache.delete(f"afk_{user_id}")
        
        # Remove from database (async)
        asyncio.create_task(self.db_pool.execute(
            "DELETE FROM afk WHERE user_id = ?", (user_id,)
        ))
        self.stats['db_writes'] += 1
        
        # Queue nickname removal
        if message.guild and message.author.display_name.startswith("[AFK]"):
            new_nick = afk_strip(message.author.display_name)
            if new_nick:
                nick_change = FastNicknameChange(message.author, new_nick, priority=0)
                await self.nick_queue.put(nick_change)
        
        # Send welcome back message
        since_dt = datetime.fromisoformat(since)
        duration = human_timedelta(since_dt)
        
        embed = discord.Embed(
            title="👋 Welcome Back!",
            description=f"{message.author.mention}, AFK removed.\nYou had been AFK for **{duration}**.",
            color=AFK_REMOVED_COLOR
        )
        
        asyncio.create_task(message.channel.send(embed=embed))

    async def _check_mentions_instant(self, message: discord.Message):
        """Check mentions in a message instantly with rate limiting."""
        current_time = time.time()
        
        for user in message.mentions:
            if user.bot:
                continue
                
            # Rate limiting: 10 seconds per user
            last_mention = self.mention_cache.get(user.id, 0)
            if current_time - last_mention < 10:
                continue
            
            # Check cache first
            cache_key = f"afk_{user.id}"
            cached = self.afk_cache.get(cache_key)
            
            if cached:
                reason, since = cached
                self.mention_cache[user.id] = current_time
                await self._send_mention_notification(message.channel, user, reason, since)
            else:
                # Check database
                async with self.db_pool.execute(
                    "SELECT reason, since FROM afk WHERE user_id = ?", (user.id,)
                ) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        reason, since = row
                        self.afk_cache.set(cache_key, (reason, since))
                        self.mention_cache[user.id] = current_time
                        await self._send_mention_notification(message.channel, user, reason, since)
                        self.stats['db_reads'] += 1

    async def _send_mention_notification(self, channel, user, reason: str, since: str):
        """Send AFK mention notification."""
        since_dt = datetime.fromisoformat(since)
        duration = human_timedelta(since_dt)
        
        embed = discord.Embed(
            title=f"{afk_strip(user.display_name)} is AFK 💤",
            description=f"**Reason:** {reason}\n**Since:** {duration} ago",
            color=AFK_MENTION_COLOR
        )
        
        asyncio.create_task(channel.send(embed=embed))
        self.stats['mentions_sent'] += 1

    async def _send_new_afk(self, ctx: commands.Context, reason: str):
        """Send new AFK notification."""
        try:
            await ctx.message.delete()
        except:
            pass
        
        embed = discord.Embed(
            title="✅ AFK Set",
            description=f"{ctx.author.mention}, you are now AFK: **{reason}**",
            color=AFK_SET_COLOR
        )
        
        msg = await ctx.send(embed=embed)
        asyncio.create_task(self._auto_delete_fast(msg, 20))

    async def _send_afk_update(self, ctx: commands.Context, old_reason: str, new_reason: str):
        """Send AFK update notification."""
        embed = discord.Embed(
            title="✏️ AFK Updated",
            description=f"{ctx.author.mention}, your AFK reason was updated from **{old_reason}** to **{new_reason}**",
            color=AFK_SET_COLOR
        )
        
        msg = await ctx.send(embed=embed)
        asyncio.create_task(self._auto_delete_fast(msg, 20))

    async def _auto_delete_fast(self, message: discord.Message, delay: int):
        """Fast auto-delete."""
        await asyncio.sleep(delay)
        try:
            await message.delete()
        except:
            pass

    # ─── Commands ──────────────────────────────────────
    @commands.command(name="afk")
    async def afk(self, ctx: commands.Context, *, reason: str = "AFK"):
        """Set yourself as AFK (batched for performance)."""
        if not ctx.author.bot:
            self.command_batch.append((ctx, reason))
            
            # Trigger processing if batch is getting large
            if len(self.command_batch) >= self.batch_size:
                asyncio.create_task(self._process_command_batch_fast(
                    self.command_batch.copy()
                ))
                self.command_batch.clear()

    @commands.command(name="afklist")
    @commands.is_owner()
    async def afk_list(self, ctx: commands.Context):
        """Show all users currently AFK."""
        start_time = time.time()
        
        # Get from cache first
        cached_afk = []
        for key, (value, timestamp) in self.afk_cache._cache.items():
            if key.startswith("afk_"):
                user_id = int(key[4:])
                reason, since = value
                cached_afk.append((user_id, reason, since))
        
        if cached_afk:
            lines = []
            for user_id, reason, since in cached_afk[:25]:  # Limit to 25 for performance
                user = self.bot.get_user(user_id)
                name = user.mention if user else f"ID: {user_id}"
                since_dt = datetime.fromisoformat(since)
                duration = human_timedelta(since_dt)
                lines.append(f"{name} — `{reason}` *(for {duration})*")
            
            embed = discord.Embed(
                title="📜 AFK List (from cache)",
                description="\n".join(lines) if lines else "No AFK users in cache",
                color=AFK_MENTION_COLOR
            )
            embed.set_footer(text=f"Cached: {len(cached_afk)} users | {int((time.time() - start_time) * 1000)}ms")
        else:
            # Fallback to database
            async with self.db_pool.execute("SELECT user_id, reason, since FROM afk LIMIT 25") as cursor:
                rows = await cursor.fetchall()
            
            if not rows:
                return await ctx.reply("No users are currently AFK.", mention_author=False)
            
            lines = []
            for user_id, reason, since in rows:
                user = self.bot.get_user(user_id)
                name = user.mention if user else f"ID: {user_id}"
                since_dt = datetime.fromisoformat(since)
                duration = human_timedelta(since_dt)
                lines.append(f"{name} — `{reason}` *(for {duration})*")
            
            embed = discord.Embed(
                title="📜 AFK List",
                description="\n".join(lines),
                color=AFK_MENTION_COLOR
            )
            embed.set_footer(text=f"Total: {len(rows)} users | DB query: {int((time.time() - start_time) * 1000)}ms")
        
        await ctx.reply(embed=embed, mention_author=False)

    @commands.command(name="afkstats")
    @commands.is_owner()
    async def afk_stats(self, ctx: commands.Context):
        """Show AFK system performance stats."""
        embed = discord.Embed(
            title="⚡ AFK System Performance",
            color=discord.Color.green()
        )
        
        # Performance stats
        embed.add_field(name="Messages", value=self.stats['messages'], inline=True)
        embed.add_field(name="Commands", value=self.stats['commands'], inline=True)
        
        # Cache efficiency
        total_lookups = self.stats['cache_hits'] + self.stats['cache_misses']
        hit_rate = (self.stats['cache_hits'] / total_lookups * 100) if total_lookups > 0 else 0
        embed.add_field(name="Cache Hit Rate", value=f"{hit_rate:.1f}%", inline=True)
        
        # DB stats
        embed.add_field(name="DB Reads", value=self.stats['db_reads'], inline=True)
        embed.add_field(name="DB Writes", value=self.stats['db_writes'], inline=True)
        
        # Nickname stats
        embed.add_field(name="Nick Changes", value=self.stats['nick_changes'], inline=True)
        embed.add_field(name="Nick Errors", value=self.stats['nick_errors'], inline=True)
        embed.add_field(name="Skipped Changes", value=self.stats['skipped_changes'], inline=True)
        
        # Queue stats
        embed.add_field(name="Command Queue", value=len(self.command_batch), inline=True)
        embed.add_field(name="Nick Queue", value=self.nick_queue.qsize(), inline=True)
        embed.add_field(name="Mentions Sent", value=self.stats['mentions_sent'], inline=True)
        
        # Cache size
        cache_size = len([k for k in self.afk_cache._cache if k.startswith('afk_')])
        embed.add_field(name="AFK Cache Size", value=cache_size, inline=True)
        embed.add_field(name="Mention Cache", value=len(self.mention_cache), inline=True)
        
        # System info
        embed.add_field(name="Cooldown Tracking", value=len(self.nick_cooldown), inline=True)
        
        await ctx.reply(embed=embed, mention_author=False)

    @commands.command(name="afkclear")
    @commands.is_owner()
    async def afk_clear(self, ctx: commands.Context):
        """Clear all AFK statuses."""
        await self.db_pool.execute("DELETE FROM afk")
        self.afk_cache.clear()
        self.mention_cache.clear()
        self.nick_cooldown.clear()
        self.command_batch.clear()
        
        # Clear nickname queue
        while not self.nick_queue.empty():
            try:
                self.nick_queue.get_nowait()
                self.nick_queue.task_done()
            except asyncio.QueueEmpty:
                break
        
        embed = discord.Embed(
            title="🧹 AFK System Cleared",
            description="All AFK statuses, cache, and queues have been cleared.",
            color=discord.Color.red()
        )
        await ctx.reply(embed=embed, mention_author=False)

    # ─── Listeners ─────────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        
        # INSTANT message processing - no batching
        await self._check_message_instant(message)
        
        # Process commands (non-blocking)
        asyncio.create_task(self.bot.process_commands(message))

    async def cog_unload(self):
        """Clean shutdown."""
        if self.nick_worker_task:
            self.nick_worker_task.cancel()
        
        if self.batch_processor_task:
            self.batch_processor_task.cancel()
        
        # Wait for tasks to complete
        try:
            await asyncio.gather(
                self.nick_worker_task,
                self.batch_processor_task,
                return_exceptions=True
            )
        except:
            pass
        
        if self.db_pool:
            await self.db_pool.close()
        
        print("[AFK] System shutdown complete")


async def setup(bot: commands.Bot):
    await bot.add_cog(AFK(bot))