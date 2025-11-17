import discord
from discord.ext import commands
import aiosqlite
import asyncio
from datetime import datetime, timezone
from collections import deque
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
        self.db = None
        
        # Worker queue system
        self.message_queue = deque()
        self.command_queue = deque()
        self.db_queue = deque()
        
        # Locks for thread safety
        self.queue_locks = {
            'message': asyncio.Lock(),
            'command': asyncio.Lock(), 
            'db': asyncio.Lock()
        }
        
        # Worker tasks
        self.workers = {}
        self.worker_count = 3  # Number of parallel workers per queue
        
        # Rate limiting
        self.last_nick_edit = {}
        self.nick_edit_cooldown = 2.0  # seconds
        
        # Performance tracking
        self.stats = {
            'messages_processed': 0,
            'commands_processed': 0,
            'db_operations': 0,
            'members_cleaned': 0
        }
        
        # Member cleanup tracking
        self.last_member_cleanup = 0
        self.member_cleanup_interval = 3600  # 1 hour
        
        self.ready_event = asyncio.Event()
        self.bot.loop.create_task(self.init_system())

    async def init_system(self):
        """Initialize database and start worker queues."""
        self.db = await aiosqlite.connect(self.db_path)
        
        # Create optimized table without guild_id
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS afk (
                user_id TEXT PRIMARY KEY,
                reason TEXT,
                since TEXT
            )
        """)
        
        # Create indexes for faster queries
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_afk_user_id ON afk(user_id)")
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_afk_since ON afk(since)")
        
        await self.db.commit()
        
        # Start worker pools
        await self.start_workers()
        
        # Initial cleanup of members who left
        await self.cleanup_left_members()
        
        self.ready_event.set()
        print("[AFK] System initialized with worker queues and indexes.")

    async def start_workers(self):
        """Start worker pools for different queue types."""
        queue_types = ['message', 'command', 'db']
        
        for queue_type in queue_types:
            self.workers[queue_type] = []
            for i in range(self.worker_count):
                worker = asyncio.create_task(
                    self.worker_loop(queue_type, i)
                )
                self.workers[queue_type].append(worker)

    async def worker_loop(self, queue_type: str, worker_id: int):
        """Worker that processes items from its assigned queue."""
        while not self.bot.is_closed():
            try:
                # Get item from queue
                async with self.queue_locks[queue_type]:
                    queue = getattr(self, f'{queue_type}_queue')
                    item = queue.popleft() if queue else None
                
                if not item:
                    await asyncio.sleep(0.1)
                    continue
                
                # Process based on queue type
                if queue_type == 'message':
                    await self.process_message_item(item)
                elif queue_type == 'command':
                    await self.process_command_item(item)
                elif queue_type == 'db':
                    await self.process_db_item(item)
                
                # Small delay to prevent CPU spinning
                await asyncio.sleep(0.01)
                
            except IndexError:
                await asyncio.sleep(0.1)
            except Exception as e:
                print(f"[AFK] Worker {queue_type}-{worker_id} error: {e}")
                await asyncio.sleep(1)

    async def process_message_item(self, item):
        """Process a message from the message queue."""
        message, task_type = item
        
        try:
            # Periodic member cleanup check
            await self.maybe_cleanup_members()
            
            if task_type == 'check_afk_removal':
                await self._check_afk_removal(message)
            elif task_type == 'check_mentions':
                await self._check_mentions(message)
            
            self.stats['messages_processed'] += 1
        except Exception as e:
            print(f"[AFK] Message processing error: {e}")

    async def process_command_item(self, item):
        """Process a command from the command queue."""
        ctx, reason = item
        
        try:
            await self._execute_afk_command(ctx, reason)
            self.stats['commands_processed'] += 1
        except Exception as e:
            print(f"[AFK] Command processing error: {e}")

    async def process_db_item(self, item):
        """Process a database operation."""
        operation, args = item
        
        try:
            if operation == 'set_afk':
                await self._db_set_afk(*args)
            elif operation == 'remove_afk':
                await self._db_remove_afk(*args)
            elif operation == 'get_afk':
                return await self._db_get_afk(*args)
            elif operation == 'cleanup_members':
                await self._db_cleanup_members(*args)
            
            self.stats['db_operations'] += 1
        except Exception as e:
            print(f"[AFK] DB operation error: {e}")

    # ─── Member Cleanup System ────────────────────────
    async def maybe_cleanup_members(self):
        """Periodically clean up AFK data for members who left."""
        current_time = time.time()
        if current_time - self.last_member_cleanup < self.member_cleanup_interval:
            return
        
        await self.cleanup_left_members()
        self.last_member_cleanup = current_time

    async def cleanup_left_members(self):
        """Remove AFK data for members who left the server."""
        try:
            # Get all AFK users from database
            afk_users = await self.get_all_afk()
            if not afk_users:
                return

            users_to_remove = []
            
            for user_id, reason, since in afk_users:
                user_id_int = int(user_id)
                
                # Check if user is still in any guild the bot can see
                user_found = False
                for guild in self.bot.guilds:
                    member = guild.get_member(user_id_int)
                    if member:
                        user_found = True
                        break
                
                # User not found in any guild, remove AFK data
                if not user_found:
                    users_to_remove.append(user_id)
            
            # Remove AFK data for users who left
            if users_to_remove:
                await self._db_bulk_remove_afk(users_to_remove)
                self.stats['members_cleaned'] += len(users_to_remove)
                print(f"[AFK] Cleaned up {len(users_to_remove)} members who left")
                
        except Exception as e:
            print(f"[AFK] Member cleanup error: {e}")

    # ─── Queue-based DB Operations ─────────────────────
    async def set_afk(self, user_id: int, reason: str):
        """Queue AFK set operation."""
        async with self.queue_locks['db']:
            self.db_queue.append(('set_afk', (user_id, reason)))

    async def remove_afk(self, user_id: int):
        """Queue AFK removal operation."""
        async with self.queue_locks['db']:
            self.db_queue.append(('remove_afk', (user_id,)))

    async def get_afk(self, user_id: int):
        """Get AFK status (immediate, not queued)."""
        await self.ensure_db()
        async with self.db.execute(
            "SELECT reason, since FROM afk WHERE user_id = ?", (str(user_id),)
        ) as cursor:
            return await cursor.fetchone()

    async def get_all_afk(self):
        """Get all AFK users (immediate, not queued)."""
        await self.ensure_db()
        async with self.db.execute(
            "SELECT user_id, reason, since FROM afk"
        ) as cursor:
            return await cursor.fetchall()

    # ─── Direct DB Operations (for workers) ────────────
    async def _db_set_afk(self, user_id: int, reason: str):
        """Worker: Set AFK in database."""
        since = datetime.now(timezone.utc).isoformat()
        
        await self.db.execute(
            "REPLACE INTO afk (user_id, reason, since) VALUES (?, ?, ?)",
            (str(user_id), reason, since)
        )
        await self.db.commit()

    async def _db_remove_afk(self, user_id: int):
        """Worker: Remove AFK from database."""
        await self.db.execute("DELETE FROM afk WHERE user_id = ?", (str(user_id),))
        await self.db.commit()

    async def _db_bulk_remove_afk(self, user_ids: list):
        """Worker: Bulk remove AFK entries."""
        if not user_ids:
            return
            
        placeholders = ','.join('?' * len(user_ids))
        await self.db.execute(
            f"DELETE FROM afk WHERE user_id IN ({placeholders})",
            user_ids
        )
        await self.db.commit()

    async def _db_get_afk(self, user_id: int):
        """Worker: Get AFK from database."""
        async with self.db.execute(
            "SELECT reason, since FROM afk WHERE user_id = ?", (str(user_id),)
        ) as cursor:
            return await cursor.fetchone()

    async def _db_cleanup_members(self, user_ids: list):
        """Worker: Clean up multiple members."""
        await self._db_bulk_remove_afk(user_ids)

    async def ensure_db(self):
        """Wait until DB connection is ready."""
        await self.ready_event.wait()

    # ─── Rate Limited Nickname Edits ───────────────────
    async def safe_edit_nick(self, member: discord.Member, new_nick: str):
        """Rate-limited nickname editing."""
        current_time = time.time()
        user_cooldown = self.last_nick_edit.get(member.id, 0)
        
        if current_time - user_cooldown < self.nick_edit_cooldown:
            return
        
        try:
            await member.edit(nick=new_nick)
            self.last_nick_edit[member.id] = current_time
        except (discord.Forbidden, discord.HTTPException):
            pass

    # ─── Command Processing ────────────────────────────
    async def _execute_afk_command(self, ctx: commands.Context, reason: str):
        """Worker: Execute AFK command."""
        user = ctx.author
        
        # Delete command message
        try:
            await ctx.message.delete()
        except:
            pass

        # Set AFK without guild_id
        await self.set_afk(user.id, reason)

        # Update nickname if in guild
        if ctx.guild and not user.display_name.startswith("[AFK]"):
            await self.safe_edit_nick(user, f"[AFK] {user.display_name}")

        # Send confirmation
        embed = discord.Embed(
            title="✅ AFK Set",
            description=f"{user.mention}, you are now AFK: **{reason}**",
            color=AFK_SET_COLOR
        )
        msg = await ctx.send(embed=embed)
        
        # Auto-delete confirmation
        asyncio.create_task(self.auto_delete_message(msg, 20))

    # ─── Message Processing ────────────────────────────
    async def _check_afk_removal(self, message: discord.Message):
        """Worker: Check if user should be removed from AFK."""
        afk_entry = await self.get_afk(message.author.id)
        if not afk_entry:
            return

        await self.remove_afk(message.author.id)
        
        # Remove AFK from nickname
        if message.guild and message.author.display_name.startswith("[AFK]"):
            await self.safe_edit_nick(
                message.author,
                afk_strip(message.author.display_name)
            )

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
        # Add to command queue for processing
        async with self.queue_locks['command']:
            self.command_queue.append((ctx, reason))

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
            name = user.name if user else f"Unknown ({user_id})"
            since_dt = datetime.fromisoformat(since)
            duration = human_timedelta(since_dt)
            
            # Check if user is still in any guild
            status = "✅ In server" if user else "❌ Left server"
            lines.append(f"**{name}** — {reason} *(for {duration})* — {status}")

        embed = discord.Embed(
            title="📜 AFK List",
            description="\n".join(lines),
            color=AFK_MENTION_COLOR
        )
        await ctx.reply(embed=embed, mention_author=False)

    @commands.command(name="afkcleanup")
    @commands.is_owner()
    async def afk_cleanup(self, ctx: commands.Context):
        """Manually clean up AFK data for members who left."""
        before_count = len(await self.get_all_afk())
        await self.cleanup_left_members()
        after_count = len(await self.get_all_afk())
        
        cleaned = before_count - after_count
        embed = discord.Embed(
            title="🧹 AFK Cleanup Complete",
            description=f"Removed {cleaned} AFK entries for members who left the server.",
            color=discord.Color.green()
        )
        await ctx.reply(embed=embed, mention_author=False)

    @commands.command(name="afkstats")
    @commands.is_owner()
    async def afk_stats(self, ctx: commands.Context):
        """Show AFK system performance stats."""
        afk_count = len(await self.get_all_afk())
        
        embed = discord.Embed(
            title="📊 AFK System Stats",
            color=discord.Color.blue()
        )
        embed.add_field(name="Current AFK Users", value=afk_count, inline=True)
        embed.add_field(name="Messages Processed", value=self.stats['messages_processed'], inline=True)
        embed.add_field(name="Commands Processed", value=self.stats['commands_processed'], inline=True)
        embed.add_field(name="DB Operations", value=self.stats['db_operations'], inline=True)
        embed.add_field(name="Members Cleaned", value=self.stats['members_cleaned'], inline=True)
        
        queue_sizes = {
            'Message Queue': len(self.message_queue),
            'Command Queue': len(self.command_queue),
            'DB Queue': len(self.db_queue)
        }
        
        for queue_name, size in queue_sizes.items():
            embed.add_field(name=queue_name, value=size, inline=True)
        
        await ctx.reply(embed=embed, mention_author=False)

    # ─── Listeners ─────────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        # Queue message for processing
        async with self.queue_locks['message']:
            # Check for AFK removal
            self.message_queue.append((message, 'check_afk_removal'))
            # Check mentions
            if message.mentions:
                self.message_queue.append((message, 'check_mentions'))

        await self.bot.process_commands(message)

    async def cog_unload(self):
        """Clean shutdown of workers and database."""
        # Cancel all workers
        for queue_type, workers in self.workers.items():
            for worker in workers:
                worker.cancel()
        
        # Close database
        if self.db:
            await self.db.close()
            print("[AFK] System shut down cleanly.")


async def setup(bot: commands.Bot):
    await bot.add_cog(AFK(bot))