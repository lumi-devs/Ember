import discord
from discord.ext import commands
import aiosqlite
import asyncio
from datetime import datetime, timezone
from collections import deque
import time
from typing import Optional, Dict, Tuple

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


class NicknameChange:
    """Represents a nickname change request with validation logic."""
    
    def __init__(self, member: discord.Member, new_nick: str, priority: int = 1):
        self.member = member
        self.new_nick = new_nick
        self.priority = priority  # Lower = higher priority
        self.created_at = time.time()
        self.attempts = 0
        
    def is_necessary(self, current_nick: Optional[str] = None) -> bool:
        """Check if this nickname change is actually needed."""
        if current_nick is None:
            current_nick = self.member.display_name
        
        # If nicknames already match, no change needed
        if current_nick == self.new_nick:
            return False
        
        # Special case: If we're trying to remove [AFK] prefix but it's not there
        if (self.new_nick == afk_strip(current_nick) and 
            not current_nick.startswith("[AFK]")):
            return False
        
        # Special case: If we're trying to add [AFK] prefix but it's already there
        if (self.new_nick == f"[AFK] {afk_strip(current_nick)}" and 
            current_nick.startswith("[AFK]")):
            return False
        
        return True
    
    def __lt__(self, other):
        """Priority queue ordering (lower priority number = higher priority)."""
        return self.priority < other.priority


class AFK(commands.Cog):
    """High-performance AFK system with smart worker queues."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db_path = "data/afk.db"
        self.db = None
        
        # Worker queue system
        self.message_queue = asyncio.Queue()
        self.command_queue = asyncio.Queue()
        self.db_queue = asyncio.Queue()
        self.nick_queue = asyncio.PriorityQueue()  # Priority queue for nickname changes
        
        # Performance tracking
        self.stats = {
            'messages_processed': 0,
            'commands_processed': 0,
            'db_operations': 0,
            'nick_changes': 0,
            'nick_skipped': 0,
            'nick_errors': 0
        }
        
        # Nickname change tracking and rate limiting
        self.nick_cooldown = 0.5  # seconds between changes for same user
        self.nick_retry_delay = 2.0  # seconds before retry after error
        self.max_attempts = 3
        
        # Track current and pending nicknames to avoid redundant changes
        self.pending_nicks: Dict[int, Tuple[str, float]] = {}  # user_id -> (nickname, timestamp)
        self.last_nick_attempt: Dict[int, float] = {}  # user_id -> timestamp
        self.nick_errors: Dict[int, int] = {}  # user_id -> error count
        
        # Track running state
        self.is_running = False
        
        self.bot.loop.create_task(self.init_system())

    async def init_system(self):
        """Initialize database and start worker queues."""
        import os
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        self.db = await aiosqlite.connect(self.db_path)
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS afk (
                user_id TEXT PRIMARY KEY,
                reason TEXT,
                since TEXT
            )
        """)
        await self.db.commit()
        
        # Start worker pools
        self.is_running = True
        await self.start_workers()
        print("[AFK] System initialized with smart worker queues.")

    async def start_workers(self):
        """Start worker pools for different queue types."""
        queue_types = [
            ('message', self.message_worker, 2),
            ('command', self.command_worker, 1),
            ('db', self.db_worker, 1),
            ('nick', self.nick_worker, 2)  # 2 workers for nickname changes
        ]
        
        self.worker_tasks = []
        for queue_name, worker_func, count in queue_types:
            for i in range(count):
                task = asyncio.create_task(worker_func(f"{queue_name}-{i}"))
                self.worker_tasks.append(task)

    async def nick_worker(self, worker_id: str):
        """Smart worker that processes nickname changes."""
        print(f"[AFK] Started nick worker {worker_id}")
        
        while self.is_running:
            try:
                # Get nickname change from priority queue
                try:
                    nick_change: NicknameChange = await asyncio.wait_for(
                        self.nick_queue.get(),
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue
                
                await self._process_nickname_change(nick_change, worker_id)
                self.nick_queue.task_done()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[AFK] Nick worker {worker_id} error: {e}")
                await asyncio.sleep(1)

    async def _process_nickname_change(self, nick_change: NicknameChange, worker_id: str):
        """Process a single nickname change with smart validation."""
        member = nick_change.member
        user_id = member.id
        current_time = time.time()
        
        # Check if member is still valid
        if not member or not member.guild:
            self.stats['nick_skipped'] += 1
            return
        
        # Get current nickname from member (live check)
        current_nick = member.display_name
        
        # Check if change is still necessary
        if not nick_change.is_necessary(current_nick):
            self.stats['nick_skipped'] += 1
            
            # Clean up tracking
            if user_id in self.pending_nicks:
                del self.pending_nicks[user_id]
            return
        
        # Rate limiting check
        last_attempt = self.last_nick_attempt.get(user_id, 0)
        time_since_last = current_time - last_attempt
        
        if time_since_last < self.nick_cooldown:
            # Too soon, requeue with delay
            nick_change.priority = 2  # Lower priority for delayed attempts
            nick_change.attempts += 1
            
            if nick_change.attempts < self.max_attempts:
                # Calculate delay and requeue
                delay = self.nick_cooldown - time_since_last
                asyncio.create_task(self.delayed_queue_nick(nick_change, delay))
            
            self.nick_queue.task_done()
            return
        
        # Check if we have error history for this user
        error_count = self.nick_errors.get(user_id, 0)
        if error_count > 2:
            # Too many errors, skip for now
            self.stats['nick_skipped'] += 1
            self.nick_queue.task_done()
            return
        
        try:
            # Attempt the nickname change
            self.last_nick_attempt[user_id] = current_time
            
            # Check permissions
            if not member.guild.me.guild_permissions.manage_nicknames:
                raise discord.Forbidden
            
            # Check hierarchy (can't edit higher roles)
            if member.top_role >= member.guild.me.top_role:
                raise discord.Forbidden
            
            # Check nickname length limits
            if len(nick_change.new_nick) > 32:
                # Truncate if too long
                new_nick = nick_change.new_nick[:29] + "..."
                nick_change.new_nick = new_nick
            
            # Perform the actual change
            await member.edit(nick=nick_change.new_nick)
            
            # Update tracking
            self.pending_nicks[user_id] = (nick_change.new_nick, current_time)
            self.nick_errors.pop(user_id, None)  # Clear error history on success
            
            self.stats['nick_changes'] += 1
            print(f"[AFK] {worker_id}: Changed {member}'s nickname")
            
        except (discord.Forbidden, discord.HTTPException) as e:
            # Handle errors gracefully
            self.stats['nick_errors'] += 1
            
            # Track error count
            self.nick_errors[user_id] = error_count + 1
            
            # Clean up pending tracking
            if user_id in self.pending_nicks:
                del self.pending_nicks[user_id]
            
            print(f"[AFK] {worker_id}: Failed to change {member}'s nickname: {e}")
            
            # Retry logic for certain errors
            if isinstance(e, discord.HTTPException) and e.status == 429:  # Rate limit
                nick_change.priority = 0  # High priority for rate limit retry
                nick_change.attempts += 1
                
                if nick_change.attempts < self.max_attempts:
                    asyncio.create_task(self.delayed_queue_nick(nick_change, self.nick_retry_delay))
        
        except Exception as e:
            # Unexpected error
            print(f"[AFK] {worker_id}: Unexpected error changing nickname: {e}")
            if user_id in self.pending_nicks:
                del self.pending_nicks[user_id]

    async def delayed_queue_nick(self, nick_change: NicknameChange, delay: float):
        """Requeue a nickname change after delay."""
        await asyncio.sleep(delay)
        await self.queue_nickname_change(nick_change.member, nick_change.new_nick, nick_change.priority)

    async def queue_nickname_change(self, member: discord.Member, new_nick: str, priority: int = 1):
        """Smart queueing of nickname changes with deduplication."""
        user_id = member.id
        current_time = time.time()
        
        # Check if we already have a pending change for this user
        if user_id in self.pending_nicks:
            pending_nick, pending_time = self.pending_nicks[user_id]
            
            # If it's the same nickname request, update timestamp
            if pending_nick == new_nick:
                self.pending_nicks[user_id] = (new_nick, current_time)
                return
            
            # If new request is older than 5 seconds, replace it
            if current_time - pending_time > 5:
                self.pending_nicks[user_id] = (new_nick, current_time)
            else:
                # Keep the existing pending request
                return
        else:
            # New request
            self.pending_nicks[user_id] = (new_nick, current_time)
        
        # Create and queue the change
        nick_change = NicknameChange(member, new_nick, priority)
        await self.nick_queue.put(nick_change)
        print(f"[AFK] Queued nickname change for {member}: {new_nick[:20]}...")

    async def message_worker(self, worker_id: str):
        """Worker that processes message queue items."""
        print(f"[AFK] Started message worker {worker_id}")
        
        while self.is_running:
            try:
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
                await asyncio.sleep(1)

    async def command_worker(self, worker_id: str):
        """Worker that processes command queue items."""
        print(f"[AFK] Started command worker {worker_id}")
        
        while self.is_running:
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
                print(f"[AFK] Command worker {worker_id} error: {e}")
                await asyncio.sleep(1)

    async def db_worker(self, worker_id: str):
        """Worker that processes database queue items."""
        print(f"[AFK] Started DB worker {worker_id}")
        
        while self.is_running:
            try:
                try:
                    operation, args, future = await asyncio.wait_for(
                        self.db_queue.get(),
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue
                
                try:
                    if operation == 'set_afk':
                        result = await self._db_set_afk(*args)
                    elif operation == 'remove_afk':
                        result = await self._db_remove_afk(*args)
                    elif operation == 'get_afk':
                        result = await self._db_get_afk(*args)
                    elif operation == 'get_all_afk':
                        result = await self._db_get_all_afk()
                    
                    self.stats['db_operations'] += 1
                    
                    if future and not future.done():
                        future.set_result(result)
                        
                except Exception as e:
                    if future and not future.done():
                        future.set_exception(e)
                
                self.db_queue.task_done()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[AFK] DB worker {worker_id} error: {e}")
                await asyncio.sleep(1)

    # ─── Queue-based DB Operations ─────────────────────
    async def set_afk(self, user_id: int, reason: str):
        """Queue AFK set operation."""
        await self.db_queue.put(('set_afk', (user_id, reason), None))

    async def remove_afk(self, user_id: int):
        """Queue AFK removal operation."""
        await self.db_queue.put(('remove_afk', (user_id,), None))

    async def get_afk(self, user_id: int):
        """Get AFK status (queued with future)."""
        future = asyncio.Future()
        await self.db_queue.put(('get_afk', (user_id,), future))
        return await future

    async def get_all_afk(self):
        """Get all AFK users (queued with future)."""
        future = asyncio.Future()
        await self.db_queue.put(('get_all_afk', (), future))
        return await future

    # ─── Direct DB Operations (for workers) ────────────
    async def _db_set_afk(self, user_id: int, reason: str):
        """Worker: Set AFK in database."""
        since = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            "REPLACE INTO afk (user_id, reason, since) VALUES (?, ?, ?)",
            (str(user_id), reason, since)
        )
        await self.db.commit()
        return True

    async def _db_remove_afk(self, user_id: int):
        """Worker: Remove AFK from database."""
        await self.db.execute("DELETE FROM afk WHERE user_id = ?", (str(user_id),))
        await self.db.commit()
        return True

    async def _db_get_afk(self, user_id: int):
        """Worker: Get AFK from database."""
        async with self.db.execute(
            "SELECT reason, since FROM afk WHERE user_id = ?", (str(user_id),)
        ) as cursor:
            return await cursor.fetchone()

    async def _db_get_all_afk(self):
        """Worker: Get all AFK users from database."""
        async with self.db.execute("SELECT user_id, reason, since FROM afk") as cursor:
            return await cursor.fetchall()

    # ─── Command Processing ────────────────────────────
    async def _execute_afk_command(self, ctx: commands.Context, reason: str):
        """Worker: Execute AFK command."""
        user = ctx.author
        
        # Delete command message
        try:
            await ctx.message.delete()
        except:
            pass
        
        # Check if already AFK
        existing = await self.get_afk(user.id)
        if existing:
            old_reason = existing[0]
            if old_reason != reason:
                await self.set_afk(user.id, reason)
                embed = discord.Embed(
                    title="✏️ AFK Updated",
                    description=f"{user.mention}, your AFK reason was updated to: **{reason}**",
                    color=AFK_SET_COLOR
                )
            else:
                return  # Already AFK with same reason
        else:
            await self.set_afk(user.id, reason)

        # Queue nickname change if in guild
        if ctx.guild:
            # Check current nickname before queuing
            if not user.display_name.startswith("[AFK]"):
                new_nick = f"[AFK] {user.display_name[:26]}" if len(user.display_name) > 26 else f"[AFK] {user.display_name}"
                await self.queue_nickname_change(user, new_nick, priority=0)  # High priority

        # Send confirmation if not already AFK with same reason
        if not existing or existing[0] != reason:
            if 'embed' not in locals():
                embed = discord.Embed(
                    title="✅ AFK Set",
                    description=f"{user.mention}, you are now AFK: **{reason}**",
                    color=AFK_SET_COLOR
                )
            msg = await ctx.send(embed=embed)
            asyncio.create_task(self.auto_delete_message(msg, 20))

    # ─── Message Processing ────────────────────────────
    async def _check_afk_removal(self, message: discord.Message):
        """Worker: Check if user should be removed from AFK."""
        afk_entry = await self.get_afk(message.author.id)
        if not afk_entry:
            return

        await self.remove_afk(message.author.id)
        
        # Queue nickname removal
        if message.guild and message.author.display_name.startswith("[AFK]"):
            new_nick = afk_strip(message.author.display_name)
            if new_nick:  # Don't set empty nickname
                await self.queue_nickname_change(message.author, new_nick, priority=0)
        
        # Send welcome back message
        since_dt = datetime.fromisoformat(afk_entry[1])
        duration = human_timedelta(since_dt)
        embed = discord.Embed(
            title="👋 Welcome Back!",
            description=f"{message.author.mention}, AFK removed.\nYou had been AFK for **{duration}**.",
            color=AFK_REMOVED_COLOR
        )
        msg = await message.channel.send(embed=embed)
        asyncio.create_task(self.auto_delete_message(msg, 20))

    async def _check_mentions(self, message: discord.Message):
        """Worker: Check mentions for AFK users."""
        for user in message.mentions:
            entry = await self.get_afk(user.id)
            if entry:
                reason, since = entry
                since_dt = datetime.fromisoformat(since)
                duration = human_timedelta(since_dt)
                embed = discord.Embed(
                    title=f"{afk_strip(user.display_name)} is AFK 💤",
                    description=f"**Reason:** {reason}\n**Since:** {duration} ago",
                    color=AFK_MENTION_COLOR
                )
                msg = await message.channel.send(embed=embed)
                asyncio.create_task(self.auto_delete_message(msg, 600))

    # ─── Utility Methods ───────────────────────────────
    async def auto_delete_message(self, message: discord.Message, delay: int):
        """Auto-delete a message after delay."""
        await asyncio.sleep(delay)
        try:
            await message.delete()
        except:
            pass

    # ─── Commands ──────────────────────────────────────
    @commands.command(name="afk")
    async def afk(self, ctx: commands.Context, *, reason: str = "AFK"):
        """Set yourself as AFK (queued)."""
        await self.command_queue.put((ctx, reason))

    @commands.command(name="afklist")
    @commands.is_owner()
    async def afk_list(self, ctx: commands.Context):
        """Show all users currently AFK (bot owner only)."""
        afk_users = await self.get_all_afk()
        if not afk_users:
            return await ctx.reply("No users are currently AFK.", mention_author=False)

        lines = []
        for user_id, reason, since in afk_users:
            user = self.bot.get_user(int(user_id))
            name = user.mention if user else f"Unknown ({user_id})"
            since_dt = datetime.fromisoformat(since)
            duration = human_timedelta(since_dt)
            lines.append(f"{name} — `{reason}` *(for {duration})*")

        embed = discord.Embed(
            title="📜 AFK List",
            description="\n".join(lines),
            color=AFK_MENTION_COLOR
        )
        embed.set_footer(text=f"Total AFK users: {len(afk_users)}")
        await ctx.reply(embed=embed, mention_author=False)

    @commands.command(name="afkstats")
    @commands.is_owner()
    async def afk_stats(self, ctx: commands.Context):
        """Show AFK system performance stats."""
        embed = discord.Embed(
            title="📊 AFK System Stats",
            color=discord.Color.blue()
        )
        
        # Core stats
        embed.add_field(name="Messages Processed", value=self.stats['messages_processed'], inline=True)
        embed.add_field(name="Commands Processed", value=self.stats['commands_processed'], inline=True)
        embed.add_field(name="DB Operations", value=self.stats['db_operations'], inline=True)
        
        # Nickname stats
        embed.add_field(name="Nick Changes", value=self.stats['nick_changes'], inline=True)
        embed.add_field(name="Nick Skipped", value=self.stats['nick_skipped'], inline=True)
        embed.add_field(name="Nick Errors", value=self.stats['nick_errors'], inline=True)
        
        # Queue sizes
        queue_sizes = {
            'Message Queue': self.message_queue.qsize(),
            'Command Queue': self.command_queue.qsize(),
            'DB Queue': self.db_queue.qsize(),
            'Nick Queue': self.nick_queue.qsize(),
            'Pending Nicks': len(self.pending_nicks)
        }
        
        for queue_name, size in queue_sizes.items():
            embed.add_field(name=queue_name, value=size, inline=True)
        
        embed.set_footer(text=f"Active workers: {len(self.worker_tasks)} | Errors: {sum(self.nick_errors.values())}")
        await ctx.reply(embed=embed, mention_author=False)

    @commands.command(name="clearnickqueue")
    @commands.is_owner()
    async def clear_nick_queue(self, ctx: commands.Context):
        """Clear the nickname queue (bot owner only)."""
        # Clear the queue
        while not self.nick_queue.empty():
            try:
                self.nick_queue.get_nowait()
                self.nick_queue.task_done()
            except asyncio.QueueEmpty:
                break
        
        # Clear tracking dictionaries
        self.pending_nicks.clear()
        self.nick_errors.clear()
        
        await ctx.reply("✅ Nickname queue and tracking cleared.", mention_author=False)

    # ─── Listeners ─────────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        # Queue message for processing
        await self.message_queue.put((message, 'check_afk_removal'))
        
        # Check mentions
        if message.mentions:
            await self.message_queue.put((message, 'check_mentions'))

        await self.bot.process_commands(message)

    async def cog_unload(self):
        """Clean shutdown of workers and database."""
        self.is_running = False
        
        # Cancel all worker tasks
        for task in self.worker_tasks:
            task.cancel()
        
        # Wait for tasks to complete
        try:
            await asyncio.gather(*self.worker_tasks, return_exceptions=True)
        except:
            pass
        
        # Close database
        if self.db:
            await self.db.close()
            print("[AFK] System shut down cleanly.")


async def setup(bot: commands.Bot):
    await bot.add_cog(AFK(bot))