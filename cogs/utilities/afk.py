# pragma: no cover
# pyright: basic
from __future__ import annotations

import discord
from discord.ext import commands
import aiosqlite
import asyncio
from datetime import datetime, timezone
import time
from typing import Optional, Dict, Tuple, Set, Deque, Any
from collections import deque
import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache, wraps
import os

# Constants - KEEP THESE THE SAME
AFK_SET_COLOR = discord.Color.orange()
AFK_REMOVED_COLOR = discord.Color.gold()
AFK_MENTION_COLOR = discord.Color.from_rgb(255, 200, 0)
NICK_PREFIX = "[AFK] "
DB_PATH = "data/afk.db"
CACHE_TTL = 300  # 5 minutes
MAX_CACHE_SIZE = 1000

@dataclass(frozen=True)
class AFKEntry:
    """Immutable AFK entry for caching."""
    user_id: int
    reason: str
    since: datetime
    
    @property
    def discord_timestamp(self) -> str:
        """Discord dynamic timestamp (relative)."""
        return f"<t:{int(self.since.timestamp())}:R>"
    
    @property
    def discord_timestamp_full(self) -> str:
        """Discord dynamic timestamp (full)."""
        return f"<t:{int(self.since.timestamp())}:F>"

class LRUCache:
    """Simple LRU cache with TTL."""
    def __init__(self, maxsize: int = 1000, ttl: int = 300):
        self.maxsize = maxsize
        self.ttl = ttl
        self.cache: Dict[str, Tuple[float, Any]] = {}
        self.order: Deque[str] = deque()
    
    def _cleanup(self):
        """Remove expired entries."""
        current_time = time.time()
        while self.order:
            key = self.order[0]
            if key not in self.cache:
                self.order.popleft()
            elif current_time - self.cache[key][0] > self.ttl:
                del self.cache[key]
                self.order.popleft()
            else:
                break
    
    def get(self, key: str) -> Optional[Any]:
        """Get value from cache."""
        self._cleanup()
        if key in self.cache:
            # Move to end (most recently used)
            self.order.remove(key)
            self.order.append(key)
            return self.cache[key][1]
        return None
    
    def set(self, key: str, value: Any):
        """Set value in cache."""
        self._cleanup()
        if key in self.cache:
            self.order.remove(key)
        elif len(self.cache) >= self.maxsize:
            # Remove least recently used
            oldest = self.order.popleft()
            del self.cache[oldest]
        
        self.cache[key] = (time.time(), value)
        self.order.append(key)
    
    def delete(self, key: str):
        """Delete key from cache."""
        if key in self.cache:
            del self.cache[key]
            if key in self.order:
                self.order.remove(key)

def cached_method(maxsize: int = 128):
    """Decorator for caching method results."""
    def decorator(func):
        cache = LRUCache(maxsize=maxsize, ttl=CACHE_TTL)
        
        @wraps(func)
        async def wrapper(self, *args, **kwargs):
            # Create cache key
            key_parts = [func.__name__, str(args), json.dumps(kwargs, sort_keys=True)]
            key = hashlib.md5("|".join(key_parts).encode()).hexdigest()
            
            # Check cache
            cached = cache.get(key)
            if cached is not None:
                return cached
            
            # Call function and cache result
            result = await func(self, *args, **kwargs)
            cache.set(key, result)
            return result
        
        # Add cache clearing method
        wrapper.clear_cache = cache.delete
        
        return wrapper
    return decorator

class AFK(commands.Cog):
    """High-performance AFK system with caching - COMPATIBLE with existing DB."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._db: Optional[aiosqlite.Connection] = None
        self._db_lock = asyncio.Lock()
        
        # Caches
        self._afk_cache: Dict[int, AFKEntry] = {}  # User ID -> AFKEntry
        self._guild_afk_cache: Dict[int, Set[int]] = {}  # Guild ID -> Set of AFK user IDs
        
        # Rate limiting
        self._nick_edit_timestamps: Dict[int, float] = {}
        self._nick_edit_cooldown = 0.5
        
        # Message cooldown
        self._last_mentions: Dict[int, float] = {}  # Channel ID -> timestamp
        self._mention_cooldown = 5.0  # 5 seconds between mention messages per channel
        
        # Performance tracking
        self.stats = {
            'messages_processed': 0,
            'cache_hits': 0,
            'cache_misses': 0,
            'db_queries': 0
        }
        
        # Batch processing for writes
        self._write_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._batch_processor_task: Optional[asyncio.Task] = None
        
        self.bot.loop.create_task(self._init_database())
    
    async def _init_database(self):
        """Initialize database with COMPATIBLE structure."""
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        
        async with self._db_lock:
            self._db = await aiosqlite.connect(DB_PATH)
            
            # Use PRAGMA optimizations that won't break compatibility
            await self._db.execute("PRAGMA journal_mode = WAL")
            await self._db.execute("PRAGMA synchronous = NORMAL")
            await self._db.execute("PRAGMA cache_size = -2000")
            await self._db.execute("PRAGMA temp_store = MEMORY")
            
            # KEEP EXISTING TABLE STRUCTURE - do NOT modify
            await self._db.execute("""
                CREATE TABLE IF NOT EXISTS afk (
                    user_id TEXT PRIMARY KEY,  -- KEEP as TEXT for compatibility
                    reason TEXT,
                    since TEXT
                )
            """)
            
            # Try to create index if it doesn't exist (won't fail if exists)
            try:
                await self._db.execute("""
                    CREATE INDEX IF NOT EXISTS idx_afk_since 
                    ON afk(since)
                """)
            except:
                pass  # Index might already exist in different form
            
            await self._db.commit()
            
            # Preload cache from existing data
            await self._preload_cache()
            
            # Start batch processor for writes only
            self._batch_processor_task = asyncio.create_task(self._batch_processor())
            
            print(f"[AFK] Database initialized with {len(self._afk_cache)} cached AFK entries")
    
    async def _preload_cache(self):
        """Preload AFK entries from existing database."""
        try:
            async with self._db.execute("SELECT user_id, reason, since FROM afk") as cursor:
                async for row in cursor:
                    user_id_str, reason, since_str = row
                    try:
                        user_id = int(user_id_str)
                        since = datetime.fromisoformat(since_str)
                        self._afk_cache[user_id] = AFKEntry(user_id, reason, since)
                        
                        # Update guild cache
                        for guild in self.bot.guilds:
                            if guild.get_member(user_id):
                                if guild.id not in self._guild_afk_cache:
                                    self._guild_afk_cache[guild.id] = set()
                                self._guild_afk_cache[guild.id].add(user_id)
                    except (ValueError, AttributeError) as e:
                        print(f"[AFK] Skipping invalid cache entry {row}: {e}")
        except Exception as e:
            print(f"[AFK] Error preloading cache: {e}")
    
    async def _batch_processor(self):
        """Process write operations in batches."""
        batch_inserts: list[Tuple[str, str, str]] = []
        batch_deletes: list[str] = []
        
        last_flush = time.time()
        flush_interval = 5.0
        
        while True:
            try:
                # Get operation from queue with timeout
                try:
                    op_type, data = await asyncio.wait_for(
                        self._write_queue.get(),
                        timeout=flush_interval
                    )
                except asyncio.TimeoutError:
                    # Timeout means no items, just check if we need to flush
                    op_type = None
                    data = None
                
                current_time = time.time()
                
                # Process operation if we got one
                if op_type:
                    if op_type == 'set_afk':
                        user_id, reason = data
                        since = datetime.now(timezone.utc).isoformat()
                        batch_inserts.append((str(user_id), reason, since))
                    elif op_type == 'remove_afk':
                        batch_deletes.append(str(data))
                
                # Flush if we have enough operations or time has passed
                should_flush = (
                    current_time - last_flush >= flush_interval or 
                    len(batch_inserts) + len(batch_deletes) >= 50
                )
                
                if should_flush and (batch_inserts or batch_deletes):
                    await self._flush_batch(batch_inserts, batch_deletes)
                    batch_inserts.clear()
                    batch_deletes.clear()
                    last_flush = current_time
                
                # Mark task as done AFTER processing (only if we got an item)
                if op_type:
                    self._write_queue.task_done()
                    
            except asyncio.CancelledError:
                # Flush on shutdown
                if batch_inserts or batch_deletes:
                    await self._flush_batch(batch_inserts, batch_deletes)
                break
            except Exception as e:
                print(f"[AFK] Batch processor error: {e}")
                await asyncio.sleep(1)
    
    async def _flush_batch(self, inserts: list, deletes: list):
        """Flush batch to database."""
        if not inserts and not deletes:
            return
        
        async with self._db_lock:
            try:
                await self._db.execute("BEGIN TRANSACTION")
                
                if inserts:
                    await self._db.executemany(
                        "REPLACE INTO afk (user_id, reason, since) VALUES (?, ?, ?)",
                        inserts
                    )
                
                if deletes:
                    # Use parameterized query for safety
                    placeholders = ','.join('?' for _ in deletes)
                    await self._db.execute(
                        f"DELETE FROM afk WHERE user_id IN ({placeholders})",
                        deletes
                    )
                
                await self._db.commit()
                self.stats['db_queries'] += 1
                
            except Exception as e:
                await self._db.rollback()
                print(f"[AFK] Batch flush error: {e}")
    
    # ─── CORE AFK METHODS (COMPATIBLE) ─────────────────────────────
    
    async def get_afk(self, user_id: int) -> Optional[AFKEntry]:
        """Get AFK entry - cached."""
        # Check memory cache first
        if user_id in self._afk_cache:
            self.stats['cache_hits'] += 1
            return self._afk_cache[user_id]
        
        self.stats['cache_misses'] += 1
        
        # Query database
        async with self._db_lock:
            async with self._db.execute(
                "SELECT reason, since FROM afk WHERE user_id = ?",
                (str(user_id),)
            ) as cursor:
                row = await cursor.fetchone()
        
        if row:
            reason, since_str = row
            since = datetime.fromisoformat(since_str)
            entry = AFKEntry(user_id, reason, since)
            self._afk_cache[user_id] = entry
            return entry
        
        return None
    
    async def set_afk(self, user_id: int, reason: str):
        """Set user as AFK - COMPATIBLE with existing data."""
        entry = AFKEntry(user_id, reason, datetime.now(timezone.utc))
        self._afk_cache[user_id] = entry
        
        # Update guild cache
        for guild in self.bot.guilds:
            if guild.get_member(user_id):
                if guild.id not in self._guild_afk_cache:
                    self._guild_afk_cache[guild.id] = set()
                self._guild_afk_cache[guild.id].add(user_id)
        
        # Queue for batch write
        await self._write_queue.put(('set_afk', (user_id, reason)))
    
    async def remove_afk(self, user_id: int):
        """Remove user from AFK."""
        if user_id in self._afk_cache:
            del self._afk_cache[user_id]
        
        # Update guild cache
        for guild_id in self._guild_afk_cache:
            self._guild_afk_cache[guild_id].discard(user_id)
        
        # Queue for batch delete
        await self._write_queue.put(('remove_afk', user_id))
    
    async def get_all_afk(self):
        """Get all AFK users - COMPATIBLE with existing method."""
        # Return from cache first
        if self._afk_cache:
            return [(str(uid), entry.reason, entry.since.isoformat()) 
                   for uid, entry in self._afk_cache.items()]
        
        # Fallback to database
        async with self._db_lock:
            async with self._db.execute(
                "SELECT user_id, reason, since FROM afk"
            ) as cursor:
                return await cursor.fetchall()
    
    # ─── UTILITY METHODS ───────────────────────────────────────────
    
    async def _safe_edit_nick(self, member: discord.Member, new_nick: str):
        """Rate-limited nickname editing."""
        current_time = time.time()
        last_edit = self._nick_edit_timestamps.get(member.id, 0)
        
        if current_time - last_edit < self._nick_edit_cooldown:
            # Schedule for later
            asyncio.create_task(
                self._delayed_nick_edit(member, new_nick, 
                                      self._nick_edit_cooldown - (current_time - last_edit))
            )
            return
        
        try:
            await member.edit(nick=new_nick)
            self._nick_edit_timestamps[member.id] = current_time
        except (discord.Forbidden, discord.HTTPException):
            pass
    
    async def _delayed_nick_edit(self, member: discord.Member, new_nick: str, delay: float):
        """Edit nickname after delay."""
        await asyncio.sleep(delay)
        try:
            await member.edit(nick=new_nick)
            self._nick_edit_timestamps[member.id] = time.time()
        except (discord.Forbidden, discord.HTTPException):
            pass
    
    @staticmethod
    def _strip_afk_nick(nick: str) -> str:
        """Strip AFK prefix from nickname - COMPATIBLE with existing."""
        if nick.startswith(NICK_PREFIX):
            return nick[len(NICK_PREFIX):]
        return nick
    
    async def _auto_delete_message(self, message: discord.Message, delay: int):
        """Auto-delete a message after delay."""
        await asyncio.sleep(delay)
        try:
            await message.delete()
        except:
            pass
    
    # ─── COMMANDS (COMPATIBLE WITH ORIGINAL) ───────────────────────
    
    @commands.command(name="afk")
    async def afk_command(self, ctx: commands.Context, *, reason: str = "AFK"):
        """Set yourself as AFK."""
        # Delete command message
        try:
            await ctx.message.delete()
        except:
            pass
        
        user = ctx.author
        existing = await self.get_afk(user.id)
        
        if existing:
            # Already AFK, update if reason changed
            if existing.reason != reason:
                await self.set_afk(user.id, reason)
                embed = discord.Embed(
                    title="✏️ AFK Updated",
                    description=f"{user.mention}, your AFK reason was updated to: **{reason}**",
                    color=AFK_SET_COLOR
                )
            else:
                return  # Already AFK with same reason
        else:
            # Set new AFK
            await self.set_afk(user.id, reason)
            
            # Update nickname if in guild
            if ctx.guild and not user.display_name.startswith(NICK_PREFIX):
                await self._safe_edit_nick(user, f"{NICK_PREFIX}{user.display_name}")
            
            embed = discord.Embed(
                title="✅ AFK Set",
                description=f"{user.mention}, you are now AFK: **{reason}**\nSet {existing.discord_timestamp}",
                color=AFK_SET_COLOR
            )
        
        msg = await ctx.send(embed=embed)
        asyncio.create_task(self._auto_delete_message(msg, 20))
    
    @commands.command(name="afklist")
    @commands.is_owner()
    async def afk_list(self, ctx: commands.Context):
        """Show all users currently AFK - COMPATIBLE output."""
        afk_users = await self.get_all_afk()
        if not afk_users:
            return await ctx.reply("No users are currently AFK.", mention_author=False)
        
        lines = []
        for user_id_str, reason, since_str in afk_users:
            try:
                user_id = int(user_id_str)
                user = self.bot.get_user(user_id)
                name = user.mention if user else f"Unknown ({user_id})"
                
                since_dt = datetime.fromisoformat(since_str)
                timestamp = int(since_dt.timestamp())
                discord_time = f"<t:{timestamp}:R>"
                
                lines.append(f"{name} — `{reason}` ({discord_time})")
            except (ValueError, AttributeError):
                continue
        
        embed = discord.Embed(
            title="📜 AFK List",
            description="\n".join(lines),
            color=AFK_MENTION_COLOR
        )
        embed.set_footer(text=f"Total AFK users: {len(lines)}")
        await ctx.reply(embed=embed, mention_author=False)
    
    @commands.command(name="afkstats")
    @commands.is_owner()
    async def afk_stats(self, ctx: commands.Context):
        """Show AFK system performance stats."""
        embed = discord.Embed(
            title="📊 AFK System Stats",
            color=discord.Color.blue()
        )
        
        embed.add_field(name="AFK Users", value=len(self._afk_cache), inline=True)
        embed.add_field(name="Cache Hits", value=self.stats['cache_hits'], inline=True)
        embed.add_field(name="Cache Misses", value=self.stats['cache_misses'], inline=True)
        embed.add_field(name="DB Queries", value=self.stats['db_queries'], inline=True)
        embed.add_field(name="Queue Size", value=self._write_queue.qsize(), inline=True)
        
        # Calculate hit ratio
        total = self.stats['cache_hits'] + self.stats['cache_misses']
        ratio = (self.stats['cache_hits'] / total * 100) if total > 0 else 0
        embed.add_field(name="Cache Hit Ratio", value=f"{ratio:.1f}%", inline=True)
        
        embed.set_footer(text=f"Guilds tracked: {len(self._guild_afk_cache)}")
        await ctx.reply(embed=embed, mention_author=False)
    
    # ─── LISTENERS (COMPATIBLE) ────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        
        self.stats['messages_processed'] += 1
        
        # Check if this is a command (check for bot prefix)
        content = message.content.strip()
        is_command = content.startswith(tuple(await self.bot.get_prefix(message)))
        
        # Only check for AFK removal if this is NOT a command
        # This prevents instant removal when using .afk command
        if not is_command:
            entry = await self.get_afk(message.author.id)
            if entry:
                await self.remove_afk(message.author.id)
                
                # Remove AFK from nickname
                if message.guild and message.author.display_name.startswith(NICK_PREFIX):
                    await self._safe_edit_nick(
                        message.author,
                        self._strip_afk_nick(message.author.display_name)
                    )
                
                # Send welcome back message
                embed = discord.Embed(
                    title="👋 Welcome Back!",
                    description=f"{message.author.mention}, AFK removed.\nYou had been AFK for {entry.discord_timestamp}.",
                    color=AFK_REMOVED_COLOR
                )
                msg = await message.channel.send(embed=embed)
                asyncio.create_task(self._auto_delete_message(msg, 20))
        
        # Process commands (always)
        await self.bot.process_commands(message)
        
        # Check mentions for AFK users (even in commands)
        if message.mentions:
            # Rate limit mention messages per channel
            current_time = time.time()
            last_mention = self._last_mentions.get(message.channel.id, 0)
            
            if current_time - last_mention >= self._mention_cooldown:
                for user in message.mentions:
                    entry = await self.get_afk(user.id)
                    if entry:
                        embed = discord.Embed(
                            title=f"{self._strip_afk_nick(user.display_name)} is AFK 💤",
                            description=f"**Reason:** {entry.reason}\n**Set {entry.discord_timestamp}**",
                            color=AFK_MENTION_COLOR
                        )
                        msg = await message.channel.send(embed=embed)
                        asyncio.create_task(self._auto_delete_message(msg, 600))
                        
                        self._last_mentions[message.channel.id] = current_time
                        break  # Only show first AFK user per message
    
    async def cog_unload(self):
        """Clean shutdown."""
        # Stop batch processor
        if self._batch_processor_task:
            self._batch_processor_task.cancel()
            try:
                await self._batch_processor_task
            except asyncio.CancelledError:
                pass
        
        # Flush remaining writes
        if self._write_queue.qsize() > 0:
            print(f"[AFK] Flushing {self._write_queue.qsize()} pending writes...")
            await self._write_queue.join()
        
        # Close database
        if self._db:
            await self._db.close()
        
        print("[AFK] System shut down cleanly.")

async def setup(bot: commands.Bot):
    await bot.add_cog(AFK(bot))