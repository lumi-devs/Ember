import discord
from discord.ext import commands
import aiosqlite
import asyncio
from datetime import datetime, timezone
from typing import Optional, Dict, List, Tuple
import time

AFK_SET_COLOR = discord.Color.orange()
AFK_REMOVED_COLOR = discord.Color.gold()
AFK_MENTION_COLOR = discord.Color.from_rgb(255, 200, 0)


def afk_strip(name: str) -> str:
    """Remove [AFK] prefix from nick if present."""
    return name[len("[AFK]"):].lstrip() if name.startswith("[AFK]") else name


def human_timedelta(dt: datetime) -> str:
    """Return human-readable time difference."""
    now = datetime.now(timezone.utc)
    delta = now - dt
    days, seconds = delta.days, delta.seconds
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    parts = []
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    if seconds and not parts:
        parts.append(f"{seconds} second{'s' if seconds != 1 else ''}")
    return " ".join(parts) if parts else "just now"


class AFK(commands.Cog):
    """High-performance AFK system with worker queues and persistent storage."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db_path = "data/afk.db"
        self.db: Optional[aiosqlite.Connection] = None
        
        # Worker queue system
        self.message_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self.command_queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self.db_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        
        # Cache for AFK data to reduce DB queries
        self.afk_cache: Dict[int, Tuple[str, str]] = {}
        
        # Rate limiting
        self.last_nick_edit: Dict[int, float] = {}
        self.nick_edit_cooldown = 2.0  # Increased for safety
        
        # Performance tracking
        self.stats = {
            'messages_processed': 0,
            'commands_processed': 0,
            'db_operations': 0,
            'cache_hits': 0,
            'cache_misses': 0
        }
        
        self.ready_event = asyncio.Event()
        self.workers: List[asyncio.Task] = []
        self.is_shutting_down = False
        
        # Start initialization
        self.bot.loop.create_task(self.init_system())

    async def init_system(self):
        """Initialize database and start worker queues."""
        try:
            self.db = await aiosqlite.connect(self.db_path)
            self.db.row_factory = aiosqlite.Row
            await self.db.execute("""
                CREATE TABLE IF NOT EXISTS afk (
                    user_id INTEGER PRIMARY KEY,
                    reason TEXT,
                    since TEXT
                )
            """)
            await self.db.commit()
            
            # Load cache from database
            await self.load_cache()
            
            # Start worker pools
            await self.start_workers()
            self.ready_event.set()
            print("[AFK] System initialized with worker queues.")
        except Exception as e:
            print(f"[AFK] Initialization error: {e}")
            raise

    async def load_cache(self):
        """Load AFK data from database into cache."""
        if not self.db:
            return
            
        async with self.db.execute("SELECT user_id, reason, since FROM afk") as cursor:
            rows = await cursor.fetchall()
            for row in rows:
                self.afk_cache[row['user_id']] = (row['reason'], row['since'])

    async def start_workers(self):
        """Start worker pools for different queue types."""
        # Message workers
        for i in range(3):  # More workers for message processing
            self.workers.append(asyncio.create_task(self.message_worker(i)))
        
        # Command worker
        self.workers.append(asyncio.create_task(self.command_worker()))
        
        # DB worker
        self.workers.append(asyncio.create_task(self.db_worker()))
        
        # Cleanup worker (runs periodically)
        self.workers.append(asyncio.create_task(self.cleanup_worker()))

    async def message_worker(self, worker_id: int):
        """Worker that processes message queue items."""
        print(f"[AFK] Started message worker {worker_id}")
        
        while not self.is_shutting_down:
            try:
                # Get item from queue with timeout
                try:
                    message, task_type = await asyncio.wait_for(
                        self.message_queue.get(), 
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue
                
                if task_type == 'check_afk_removal':
                    await self._check_afk_removal(message)
                elif task_type == 'check_mentions':
                    await self._check_mentions(message)
                
                self.stats['messages_processed'] += 1
                self.message_queue.task_done()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[AFK] Message worker {worker_id} error: {e}")
                await asyncio.sleep(0.1)

    async def command_worker(self):
        """Worker that processes command queue items."""
        print(f"[AFK] Started command worker")
        
        while not self.is_shutting_down:
            try:
                try:
                    ctx, reason = await asyncio.wait_for(
                        self.command_queue.get(), 
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue
                
                await self._execute_afk_command(ctx, reason)
                self.stats['commands_processed'] += 1
                self.command_queue.task_done()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[AFK] Command worker error: {e}")
                await asyncio.sleep(0.1)

    async def db_worker(self):
        """Worker that processes database operations."""
        print(f"[AFK] Started DB worker")
        
        while not self.is_shutting_down:
            try:
                try:
                    operation, args, future = await asyncio.wait_for(
                        self.db_queue.get(), 
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue
                
                result = None
                if operation == 'set_afk':
                    result = await self._db_set_afk(*args)
                elif operation == 'remove_afk':
                    result = await self._db_remove_afk(*args)
                elif operation == 'get_afk':
                    result = await self._db_get_afk(*args)
                elif operation == 'remove_left_users':
                    result = await self._db_remove_left_users(*args)
                
                if future and not future.done():
                    future.set_result(result)
                
                self.stats['db_operations'] += 1
                self.db_queue.task_done()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[AFK] DB worker error: {e}")
                if future and not future.done():
                    future.set_exception(e)
                await asyncio.sleep(0.1)

    async def cleanup_worker(self):
        """Periodic cleanup worker."""
        print(f"[AFK] Started cleanup worker")
        
        while not self.is_shutting_down:
            try:
                # Clean old nick edit cooldowns every 5 minutes
                await asyncio.sleep(300)
                
                current_time = time.time()
                expired = [
                    user_id for user_id, last_time in self.last_nick_edit.items()
                    if current_time - last_time > 600  # 10 minutes
                ]
                for user_id in expired:
                    del self.last_nick_edit[user_id]
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[AFK] Cleanup worker error: {e}")

    # ─── Queue-based DB Operations ─────────────────────
    async def set_afk(self, user_id: int, reason: str) -> None:
        """Queue AFK set operation."""
        future = asyncio.Future()
        await self.db_queue.put(('set_afk', (user_id, reason), future))
        await future  # Wait for completion
        
        # Update cache
        since = datetime.now(timezone.utc).isoformat()
        self.afk_cache[user_id] = (reason, since)

    async def remove_afk(self, user_id: int) -> None:
        """Queue AFK removal operation."""
        future = asyncio.Future()
        await self.db_queue.put(('remove_afk', (user_id,), future))
        await future  # Wait for completion
        
        # Update cache
        self.afk_cache.pop(user_id, None)

    async def get_afk(self, user_id: int) -> Optional[Tuple[str, str]]:
        """Get AFK status (cached)."""
        # Check cache first
        if user_id in self.afk_cache:
            self.stats['cache_hits'] += 1
            return self.afk_cache[user_id]
        
        self.stats['cache_misses'] += 1
        # If not in cache, query database
        future = asyncio.Future()
        await self.db_queue.put(('get_afk', (user_id,), future))
        result = await future
        
        if result:
            self.afk_cache[user_id] = (result[0], result[1])
        
        return result

    async def get_all_afk(self) -> List[Tuple[int, str, str]]:
        """Get all AFK users."""
        await self.ensure_db()
        async with self.db.execute("SELECT user_id, reason, since FROM afk") as cursor:
            rows = await cursor.fetchall()
            return [(row['user_id'], row['reason'], row['since']) for row in rows]

    # ─── Direct DB Operations (for workers) ────────────
    async def _db_set_afk(self, user_id: int, reason: str) -> None:
        """Worker: Set AFK in database."""
        since = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            "INSERT OR REPLACE INTO afk (user_id, reason, since) VALUES (?, ?, ?)",
            (user_id, reason, since)
        )
        await self.db.commit()

    async def _db_remove_afk(self, user_id: int) -> None:
        """Worker: Remove AFK from database."""
        await self.db.execute("DELETE FROM afk WHERE user_id = ?", (user_id,))
        await self.db.commit()

    async def _db_get_afk(self, user_id: int) -> Optional[Tuple[str, str]]:
        """Worker: Get AFK from database."""
        async with self.db.execute(
            "SELECT reason, since FROM afk WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return (row['reason'], row['since']) if row else None

    async def _db_remove_left_users(self, user_ids: List[int]) -> int:
        """Worker: Remove AFK entries for users who left the server."""
        if not user_ids:
            return 0
            
        # Create parameter placeholders
        placeholders = ','.join('?' for _ in user_ids)
        query = f"DELETE FROM afk WHERE user_id IN ({placeholders})"
        
        await self.db.execute(query, user_ids)
        await self.db.commit()
        
        # Return count of removed users
        return self.db.total_changes

    async def ensure_db(self):
        """Wait until DB connection is ready."""
        await self.ready_event.wait()

    # ─── Rate Limited Nickname Edits ───────────────────
    async def safe_edit_nick(self, member: discord.Member, new_nick: str) -> bool:
        """Rate-limited nickname editing with proper error handling."""
        current_time = time.time()
        user_cooldown = self.last_nick_edit.get(member.id, 0)
        
        # Check cooldown
        if current_time - user_cooldown < self.nick_edit_cooldown:
            return False
        
        # Check if nickname actually needs changing
        if member.display_name == new_nick:
            return True
        
        try:
            await member.edit(nick=new_nick[:32])  # Discord limit
            self.last_nick_edit[member.id] = current_time
            return True
        except discord.Forbidden:
            # Bot doesn't have permission
            pass
        except discord.HTTPException as e:
            # Log but don't crash on HTTP errors
            if e.status != 403:  # Don't log permission errors
                print(f"[AFK] Nick edit failed for {member}: {e}")
        
        return False

    # ─── Command Processing ────────────────────────────
    async def _execute_afk_command(self, ctx: commands.Context, reason: str):
        """Worker: Execute AFK command."""
        user = ctx.author
        
        # Check if already AFK
        if user.id in self.afk_cache:
            return
        
        # Delete command message (non-critical)
        try:
            await ctx.message.delete(delay=0.1)
        except:
            pass

        await self.set_afk(user.id, reason)

        # Update nickname if in guild
        if ctx.guild:
            new_nick = f"[AFK] {user.display_name[:27]}"  # 32 - 5 for [AFK]
            await self.safe_edit_nick(user, new_nick)

        # Send confirmation
        embed = discord.Embed(
            title="✅ AFK Set",
            description=f"{user.mention}, you are now AFK: **{reason}**",
            color=AFK_SET_COLOR
        )
        msg = await ctx.send(embed=embed, delete_after=20)

    # ─── Message Processing ────────────────────────────
    async def _check_afk_removal(self, message: discord.Message):
        """Worker: Check if user should be removed from AFK."""
        if message.author.id not in self.afk_cache:
            return

        await self.remove_afk(message.author.id)
        
        # Remove AFK from nickname
        if message.guild and message.author.display_name.startswith("[AFK]"):
            await self.safe_edit_nick(
                message.author,
                afk_strip(message.author.display_name)
            )

        # Send welcome back message
        since_str = self.afk_cache.get(message.author.id, ('', ''))[1]
        if since_str:
            since_dt = datetime.fromisoformat(since_str)
            duration = human_timedelta(since_dt)
            embed = discord.Embed(
                title="👋 Welcome Back!",
                description=f"{message.author.mention}, AFK removed.\nYou were AFK for **{duration}**.",
                color=AFK_REMOVED_COLOR
            )
            await message.channel.send(embed=embed, delete_after=20)

    async def _check_mentions(self, message: discord.Message):
        """Worker: Check mentions for AFK users."""
        for user in message.mentions:
            if user.id in self.afk_cache:
                reason, since = self.afk_cache[user.id]
                since_dt = datetime.fromisoformat(since)
                duration = human_timedelta(since_dt)
                embed = discord.Embed(
                    title=f"{afk_strip(user.display_name)} is AFK 💤",
                    description=f"**Reason:** {reason}\n**Since:** {duration} ago",
                    color=AFK_MENTION_COLOR
                )
                await message.channel.send(embed=embed, delete_after=600)
                break  # Only show first mentioned AFK user

    # ─── New Command: Clean Left Users ─────────────────
    @commands.command(name="afkclean", aliases=["cleanafk"])
    @commands.has_permissions(administrator=True)
    async def afk_clean(self, ctx: commands.Context):
        """Remove AFK status for users who left the server (Admin only)."""
        await ctx.typing()
        
        # Get all AFK users from cache
        afk_user_ids = list(self.afk_cache.keys())
        
        # Check which users are no longer in the server
        left_user_ids = []
        for user_id in afk_user_ids:
            user = ctx.guild.get_member(user_id)
            if not user:
                left_user_ids.append(user_id)
        
        if not left_user_ids:
            return await ctx.reply("✅ No AFK users have left the server.", mention_author=False)
        
        # Remove from database
        future = asyncio.Future()
        await self.db_queue.put(('remove_left_users', (left_user_ids,), future))
        removed_count = await future
        
        # Remove from cache
        for user_id in left_user_ids:
            self.afk_cache.pop(user_id, None)
        
        embed = discord.Embed(
            title="🧹 AFK Cleanup Complete",
            description=f"Removed AFK status for **{removed_count}** user(s) who left the server.",
            color=discord.Color.green()
        )
        await ctx.reply(embed=embed, mention_author=False)

    # ─── Commands ──────────────────────────────────────
    @commands.command(name="afk")
    async def afk(self, ctx: commands.Context, *, reason: str = "AFK"):
        """Set yourself as AFK (queued)."""
        # Quick check before queueing
        if ctx.author.id in self.afk_cache:
            return await ctx.reply("You are already AFK!", mention_author=False, delete_after=5)
        
        # Add to command queue for processing
        await self.command_queue.put((ctx, reason))
        await ctx.typing()

    @commands.command(name="afklist")
    @commands.is_owner()
    async def afk_list(self, ctx: commands.Context):
        """Show all users currently AFK (bot owner only)."""
        if not self.afk_cache:
            return await ctx.reply("No users are currently AFK.", mention_author=False)

        lines = []
        for user_id, (reason, since) in self.afk_cache.items():
            user = self.bot.get_user(user_id)
            name = user.name if user else f"Unknown ({user_id})"
            since_dt = datetime.fromisoformat(since)
            duration = human_timedelta(since_dt)
            lines.append(f"**{name}** — {reason} *(for {duration})*")

        # Split into pages if too long
        chunks = [lines[i:i + 10] for i in range(0, len(lines), 10)]
        
        for i, chunk in enumerate(chunks):
            embed = discord.Embed(
                title=f"📜 AFK List (Page {i+1}/{len(chunks)})",
                description="\n".join(chunk),
                color=AFK_MENTION_COLOR
            )
            await ctx.reply(embed=embed, mention_author=False)

    @commands.command(name="afkstats")
    @commands.is_owner()
    async def afk_stats(self, ctx: commands.Context):
        """Show AFK system performance stats."""
        embed = discord.Embed(
            title="📊 AFK System Stats",
            color=discord.Color.blue()
        )
        
        # Performance stats
        embed.add_field(name="Messages Processed", value=self.stats['messages_processed'], inline=True)
        embed.add_field(name="Commands Processed", value=self.stats['commands_processed'], inline=True)
        embed.add_field(name="DB Operations", value=self.stats['db_operations'], inline=True)
        
        # Cache stats
        total_lookups = self.stats['cache_hits'] + self.stats['cache_misses']
        cache_hit_rate = (self.stats['cache_hits'] / total_lookups * 100) if total_lookups > 0 else 0
        embed.add_field(name="Cache Hits", value=f"{self.stats['cache_hits']} ({cache_hit_rate:.1f}%)", inline=True)
        embed.add_field(name="Cache Misses", value=self.stats['cache_misses'], inline=True)
        embed.add_field(name="Active AFK Users", value=len(self.afk_cache), inline=True)
        
        # Queue stats
        embed.add_field(name="Message Queue", value=self.message_queue.qsize(), inline=True)
        embed.add_field(name="Command Queue", value=self.command_queue.qsize(), inline=True)
        embed.add_field(name="DB Queue", value=self.db_queue.qsize(), inline=True)
        
        await ctx.reply(embed=embed, mention_author=False)

    # ─── Listeners ─────────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.content:
            return

        # Check if user is AFK and sent a message
        if message.author.id in self.afk_cache:
            await self.message_queue.put((message, 'check_afk_removal'))
        
        # Check for AFK mentions (only if there are mentions)
        if message.mentions:
            await self.message_queue.put((message, 'check_mentions'))

        # Process commands
        await self.bot.process_commands(message)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """Automatically remove AFK when user leaves server."""
        if member.id in self.afk_cache:
            await self.remove_afk(member.id)

    async def cog_unload(self):
        """Clean shutdown of workers and database."""
        self.is_shutting_down = True
        
        # Wait for queues to empty with timeout
        try:
            await asyncio.wait_for(
                asyncio.gather(
                    self.message_queue.join(),
                    self.command_queue.join(),
                    self.db_queue.join()
                ),
                timeout=5.0
            )
        except asyncio.TimeoutError:
            print("[AFK] Queues didn't empty in time, forcing shutdown")
        
        # Cancel all workers
        for worker in self.workers:
            worker.cancel()
        
        # Wait for workers to finish
        if self.workers:
            await asyncio.gather(*self.workers, return_exceptions=True)
        
        # Close database
        if self.db:
            await self.db.close()
        
        print("[AFK] System shut down cleanly.")


async def setup(bot: commands.Bot):
    await bot.add_cog(AFK(bot))