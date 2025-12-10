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
    """Ultra-fast AFK system with advanced optimizations."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db_path = "data/afk.db"
        
        # High-performance caching
        self.afk_cache = TTLCache(ttl=600)  # 10 minute TTL
        self.nickname_cache: Dict[int, str] = {}  # user_id -> current nickname
        self.mention_cache: Dict[int, float] = {}  # user_id -> last mention time (rate limiting)
        
        # Bulk operation tracking
        self.bulk_nick_updates: Dict[int, List[Tuple[int, str]]] = defaultdict(list)
        self.last_bulk_flush = time.time()
        
        # Optimized queues with batch processing
        self.message_batch: List[discord.Message] = []
        self.command_batch: List[Tuple[commands.Context, str]] = []
        self.batch_size = 10
        self.batch_delay = 0.05  # 50ms
        
        # Performance counters (atomic updates)
        self.stats = {
            'messages': 0,
            'commands': 0,
            'db_reads': 0,
            'db_writes': 0,
            'cache_hits': 0,
            'cache_misses': 0,
            'mentions_sent': 0,
            'nick_changes': 0,
            'skipped_changes': 0
        }
        
        # Connection pooling for database
        self.db_pool = None
        self.is_initialized = False
        
        # Start optimized processing loop
        self.processing_task = None
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
        
        self.is_initialized = True
        self.processing_task = self.bot.loop.create_task(self._batch_processor())
        print("[AFK] Ultra-fast system initialized")

    async def _warm_cache(self):
        """Preload AFK users into cache for fast access."""
        async with self.db_pool.execute("SELECT user_id, reason, since FROM afk") as cursor:
            async for row in cursor:
                user_id, reason, since = row
                cache_key = f"afk_{user_id}"
                self.afk_cache.set(cache_key, (reason, since))
        
        print(f"[AFK] Cache warmed with {len([k for k in self.afk_cache._cache if k.startswith('afk_')])} entries")

    async def _batch_processor(self):
        """Process messages and commands in batches for maximum throughput."""
        while True:
            try:
                # Process message batch
                if self.message_batch:
                    messages = self.message_batch.copy()
                    self.message_batch.clear()
                    
                    # Pre-fetch all AFK statuses in one query
                    user_ids = [str(msg.author.id) for msg in messages if not msg.author.bot]
                    if user_ids:
                        await self._process_message_batch(messages, user_ids)
                
                # Process command batch
                if self.command_batch:
                    commands = self.command_batch.copy()
                    self.command_batch.clear()
                    await self._process_command_batch(commands)
                
                # Flush bulk nick updates if needed
                if time.time() - self.last_bulk_flush > 1.0:
                    await self._flush_bulk_nick_updates()
                
                await asyncio.sleep(self.batch_delay)
                
            except Exception as e:
                print(f"[AFK] Batch processor error: {e}")
                await asyncio.sleep(1)

    async def _process_message_batch(self, messages: List[discord.Message], user_ids: List[str]):
        """Process a batch of messages efficiently."""
        # Batch AFK status check
        placeholders = ','.join('?' * len(user_ids))
        query = f"SELECT user_id, reason, since FROM afk WHERE user_id IN ({placeholders})"
        
        async with self.db_pool.execute(query, user_ids) as cursor:
            afk_users = {row[0]: (row[1], row[2]) async for row in cursor}
        
        # Process each message
        for message in messages:
            if message.author.bot:
                continue
            
            user_id = message.author.id
            
            # Fast cache check first
            cache_key = f"afk_{user_id}"
            cached = self.afk_cache.get(cache_key)
            
            if cached:
                # User is AFK - check if they sent a message (should remove AFK)
                reason, since = cached
                await self._remove_afk_fast(message, user_id, reason, since)
                self.stats['cache_hits'] += 1
            elif user_id in afk_users:
                # Cache miss but DB hit
                reason, since = afk_users[user_id]
                self.afk_cache.set(cache_key, (reason, since))
                await self._remove_afk_fast(message, user_id, reason, since)
                self.stats['cache_misses'] += 1
            else:
                # Not AFK - check mentions
                if message.mentions:
                    await self._check_mentions_fast(message)
            
            self.stats['messages'] += 1

    async def _process_command_batch(self, commands: List[Tuple[commands.Context, str]]):
        """Process a batch of AFK commands efficiently."""
        updates = []
        cache_updates = []
        
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
                    
                    # Send update notification
                    await self._send_afk_update(ctx, user_id, reason, old_reason)
            else:
                # New AFK
                updates.append((user_id, reason))
                cache_updates.append((cache_key, (reason, datetime.now(timezone.utc).isoformat())))
                
                # Send new AFK notification and update nickname
                await self._send_new_afk(ctx, user_id, reason)
            
            self.stats['commands'] += 1
        
        # Batch database update
        if updates:
            await self._batch_set_afk(updates)
            
            # Update cache
            for cache_key, value in cache_updates:
                self.afk_cache.set(cache_key, value)

    async def _remove_afk_fast(self, message: discord.Message, user_id: int, reason: str, since: str):
        """Ultra-fast AFK removal."""
        # Remove from cache
        self.afk_cache.delete(f"afk_{user_id}")
        
        # Remove from database (async, don't wait)
        asyncio.create_task(self.db_pool.execute(
            "DELETE FROM afk WHERE user_id = ?", (user_id,)
        ))
        self.stats['db_writes'] += 1
        
        # Update nickname if needed
        if message.guild and message.author.display_name.startswith("[AFK]"):
            new_nick = afk_strip(message.author.display_name)
            if new_nick:
                self._queue_nick_update(message.guild.id, user_id, new_nick)
        
        # Send welcome back message
        since_dt = datetime.fromisoformat(since)
        duration = human_timedelta(since_dt)
        
        embed = discord.Embed(
            title="👋 Welcome Back!",
            description=f"{message.author.mention}, AFK removed.\nYou had been AFK for **{duration}**.",
            color=AFK_REMOVED_COLOR
        )
        
        # Don't wait for message send
        asyncio.create_task(message.channel.send(embed=embed))

    async def _check_mentions_fast(self, message: discord.Message):
        """Fast mention checking with rate limiting."""
        current_time = time.time()
        cache_entries = []
        
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
                cache_entries.append((user, reason, since))
                self.mention_cache[user.id] = current_time
            else:
                # Check database
                async with self.db_pool.execute(
                    "SELECT reason, since FROM afk WHERE user_id = ?", (user.id,)
                ) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        reason, since = row
                        self.afk_cache.set(cache_key, (reason, since))
                        cache_entries.append((user, reason, since))
                        self.mention_cache[user.id] = current_time
                        self.stats['db_reads'] += 1
        
        # Send all mention notifications at once
        if cache_entries:
            for user, reason, since in cache_entries:
                since_dt = datetime.fromisoformat(since)
                duration = human_timedelta(since_dt)
                
                embed = discord.Embed(
                    title=f"{afk_strip(user.display_name)} is AFK 💤",
                    description=f"**Reason:** {reason}\n**Since:** {duration} ago",
                    color=AFK_MENTION_COLOR
                )
                
                asyncio.create_task(message.channel.send(embed=embed))
                self.stats['mentions_sent'] += 1

    async def _batch_set_afk(self, updates: List[Tuple[int, str]]):
        """Batch update AFK status in database."""
        if not updates:
            return
        
        current_time = datetime.now(timezone.utc).isoformat()
        
        # Prepare batch insert/update
        values = [(user_id, reason, current_time, current_time) for user_id, reason in updates]
        
        # Use UPSERT for efficiency
        await self.db_pool.executemany("""
            INSERT INTO afk (user_id, reason, since, last_update) 
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET 
            reason = excluded.reason,
            last_update = excluded.last_update
        """, values)
        
        self.stats['db_writes'] += len(updates)

    async def _send_new_afk(self, ctx: commands.Context, user_id: int, reason: str):
        """Send new AFK notification."""
        try:
            await ctx.message.delete()
        except:
            pass
        
        # Update nickname
        if ctx.guild:
            current_nick = ctx.author.display_name
            if not current_nick.startswith("[AFK]"):
                new_nick = f"[AFK] {current_nick[:26]}" if len(current_nick) > 26 else f"[AFK] {current_nick}"
                self._queue_nick_update(ctx.guild.id, user_id, new_nick)
        
        embed = discord.Embed(
            title="✅ AFK Set",
            description=f"{ctx.author.mention}, you are now AFK: **{reason}**",
            color=AFK_SET_COLOR
        )
        
        msg = await ctx.send(embed=embed)
        asyncio.create_task(self._auto_delete_fast(msg, 20))

    async def _send_afk_update(self, ctx: commands.Context, user_id: int, new_reason: str, old_reason: str):
        """Send AFK update notification."""
        embed = discord.Embed(
            title="✏️ AFK Updated",
            description=f"{ctx.author.mention}, your AFK reason was updated to: **{new_reason}**",
            color=AFK_SET_COLOR
        )
        
        msg = await ctx.send(embed=embed)
        asyncio.create_task(self._auto_delete_fast(msg, 20))

    def _queue_nick_update(self, guild_id: int, user_id: int, new_nick: str):
        """Queue nickname update for bulk processing."""
        self.bulk_nick_updates[guild_id].append((user_id, new_nick))
        self.stats['nick_changes'] += 1
        
        # Auto-flush if batch is large
        if len(self.bulk_nick_updates[guild_id]) >= 5:
            asyncio.create_task(self._flush_guild_nick_updates(guild_id))

    async def _flush_bulk_nick_updates(self):
        """Flush all pending nickname updates."""
        self.last_bulk_flush = time.time()
        
        for guild_id in list(self.bulk_nick_updates.keys()):
            if self.bulk_nick_updates[guild_id]:
                await self._flush_guild_nick_updates(guild_id)

    async def _flush_guild_nick_updates(self, guild_id: int):
        """Process nickname updates for a specific guild."""
        updates = self.bulk_nick_updates.get(guild_id, [])
        if not updates:
            return
        
        # Clear updates before processing to avoid duplicates
        self.bulk_nick_updates[guild_id] = []
        
        guild = self.bot.get_guild(guild_id)
        if not guild or not guild.me.guild_permissions.manage_nicknames:
            return
        
        # Process updates in chunks
        for chunk in [updates[i:i + 3] for i in range(0, len(updates), 3)]:
            tasks = []
            
            for user_id, new_nick in chunk:
                member = guild.get_member(user_id)
                if member and member.display_name != new_nick:
                    # Check hierarchy
                    if member.top_role < guild.me.top_role:
                        tasks.append(self._safe_nick_change(member, new_nick))
                    else:
                        self.stats['skipped_changes'] += 1
            
            # Execute up to 3 changes concurrently
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            
            # Small delay between chunks to avoid rate limits
            if len(chunk) == 3:
                await asyncio.sleep(0.1)

    async def _safe_nick_change(self, member: discord.Member, new_nick: str):
        """Safe nickname change with error handling."""
        try:
            await member.edit(nick=new_nick)
            self.nickname_cache[member.id] = new_nick
        except (discord.Forbidden, discord.HTTPException):
            self.stats['skipped_changes'] += 1
        except Exception:
            pass

    async def _auto_delete_fast(self, message: discord.Message, delay: int):
        """Fast auto-delete without asyncio.sleep overhead."""
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
            
            # If batch is getting large, trigger immediate processing
            if len(self.command_batch) >= self.batch_size:
                asyncio.create_task(self._process_command_batch(
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
        embed.add_field(name="Messages/sec", value=f"{self.stats['messages'] / max(1, time.time() - self.bot.uptime):.1f}", inline=True)
        embed.add_field(name="Commands/sec", value=f"{self.stats['commands'] / max(1, time.time() - self.bot.uptime):.1f}", inline=True)
        
        # Cache efficiency
        total_lookups = self.stats['cache_hits'] + self.stats['cache_misses']
        hit_rate = (self.stats['cache_hits'] / total_lookups * 100) if total_lookups > 0 else 0
        embed.add_field(name="Cache Hit Rate", value=f"{hit_rate:.1f}%", inline=True)
        
        # DB stats
        embed.add_field(name="DB Reads", value=self.stats['db_reads'], inline=True)
        embed.add_field(name="DB Writes", value=self.stats['db_writes'], inline=True)
        embed.add_field(name="Reads/Writes", value=f"{self.stats['db_reads'] / max(1, self.stats['db_writes']):.1f}", inline=True)
        
        # Nickname stats
        embed.add_field(name="Nick Changes", value=self.stats['nick_changes'], inline=True)
        embed.add_field(name="Skipped Changes", value=self.stats['skipped_changes'], inline=True)
        embed.add_field(name="Mentions Sent", value=self.stats['mentions_sent'], inline=True)
        
        # Queue stats
        embed.add_field(name="Message Queue", value=len(self.message_batch), inline=True)
        embed.add_field(name="Command Queue", value=len(self.command_batch), inline=True)
        embed.add_field(name="Bulk Nick Queue", value=sum(len(v) for v in self.bulk_nick_updates.values()), inline=True)
        
        # Cache size
        cache_size = len([k for k in self.afk_cache._cache if k.startswith('afk_')])
        embed.add_field(name="AFK Cache Size", value=cache_size, inline=True)
        
        # Response time
        embed.set_footer(text=f"System uptime: {human_timedelta(datetime.fromtimestamp(self.bot.uptime))}")
        
        await ctx.reply(embed=embed, mention_author=False)

    @commands.command(name="afkclear")
    @commands.is_owner()
    async def afk_clear(self, ctx: commands.Context):
        """Clear all AFK statuses."""
        await self.db_pool.execute("DELETE FROM afk")
        self.afk_cache.clear()
        self.message_batch.clear()
        self.command_batch.clear()
        self.bulk_nick_updates.clear()
        
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
        
        # Batch message for processing
        self.message_batch.append(message)
        
        # Trigger immediate processing if batch is full
        if len(self.message_batch) >= self.batch_size:
            asyncio.create_task(self._process_message_batch(
                self.message_batch.copy(),
                [str(msg.author.id) for msg in self.message_batch if not msg.author.bot]
            ))
            self.message_batch.clear()
        
        # Process commands (non-blocking)
        asyncio.create_task(self.bot.process_commands(message))

    async def cog_unload(self):
        """Clean shutdown."""
        if self.processing_task:
            self.processing_task.cancel()
        
        # Flush any remaining updates
        await self._flush_bulk_nick_updates()
        
        if self.db_pool:
            await self.db_pool.close()
        
        print("[AFK] System shutdown complete")


async def setup(bot: commands.Bot):
    await bot.add_cog(AFK(bot))