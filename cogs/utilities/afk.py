import discord
from discord.ext import commands
import aiosqlite
import asyncio
from datetime import datetime, timezone
from typing import Optional, Dict, List, Tuple

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
    """AFK system with persistent storage."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db_path = "data/afk.db"
        self.db: Optional[aiosqlite.Connection] = None
        
<<<<<<< HEAD
        # Worker queue system - fix: use asyncio.Queue for proper async handling
        self.message_queue = asyncio.Queue()
        self.command_queue = asyncio.Queue()
        self.db_queue = asyncio.Queue()
=======
        # In-memory cache for AFK users
        self.afk_users: Dict[int, Tuple[str, str]] = {}
        
        # Rate limiting for mentions
        self.mention_cooldowns = {}
>>>>>>> 8d6ce26f1b24fe4e87fa8cec6e876e005a5377cd
        
        # Performance tracking
        self.stats = {
            'messages_processed': 0,
            'commands_processed': 0,
            'db_operations': 0
        }
        
<<<<<<< HEAD
        # Rate limiting
        self.last_nick_edit = {}
        self.nick_edit_cooldown = 0.5  # seconds
        
        # Track running state
        self.is_running = False
        
        self.bot.loop.create_task(self.init_system())

    async def init_system(self):
        """Initialize database and start worker queues."""
        # Create data directory if it doesn't exist
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
        print("[AFK] System initialized with worker queues.")

    async def start_workers(self):
        """Start worker pools for different queue types."""
        queue_types = [
            ('message', self.message_worker, 2),
            ('command', self.command_worker, 1),
            ('db', self.db_worker, 1)
        ]
        
        self.worker_tasks = []
        for queue_name, worker_func, count in queue_types:
            for i in range(count):
                task = asyncio.create_task(worker_func(f"{queue_name}-{i}"))
                self.worker_tasks.append(task)

    async def message_worker(self, worker_id: str):
        """Worker that processes message queue items."""
        print(f"[AFK] Started message worker {worker_id}")
        
        while self.is_running:
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
                    
                    # Set result on future if provided
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
=======
        self.bot.loop.create_task(self.init_db())

    async def init_db(self):
        """Initialize database."""
        try:
            self.db = await aiosqlite.connect(self.db_path)
            await self.db.execute("""
                CREATE TABLE IF NOT EXISTS afk (
                    user_id INTEGER PRIMARY KEY,
                    reason TEXT,
                    since TEXT
                )
            """)
            await self.db.commit()
            
            # Load all AFK users into memory
            await self.load_afk_users()
            print(f"[AFK] Loaded {len(self.afk_users)} AFK users from database.")
        except Exception as e:
            print(f"[AFK] Database initialization error: {e}")

    async def load_afk_users(self):
        """Load all AFK users from database into memory."""
        if not self.db:
            return
            
        async with self.db.execute("SELECT user_id, reason, since FROM afk") as cursor:
            rows = await cursor.fetchall()
            for row in rows:
                self.afk_users[row[0]] = (row[1], row[2])

    async def set_afk(self, user_id: int, reason: str) -> None:
        """Set user as AFK."""
>>>>>>> 8d6ce26f1b24fe4e87fa8cec6e876e005a5377cd
        since = datetime.now(timezone.utc).isoformat()
        
        # Update database
        await self.db.execute(
            "INSERT OR REPLACE INTO afk (user_id, reason, since) VALUES (?, ?, ?)",
            (user_id, reason, since)
        )
        await self.db.commit()
<<<<<<< HEAD
        return True
=======
        
        # Update memory cache
        self.afk_users[user_id] = (reason, since)
        self.stats['db_operations'] += 1
>>>>>>> 8d6ce26f1b24fe4e87fa8cec6e876e005a5377cd

    async def remove_afk(self, user_id: int) -> None:
        """Remove user from AFK."""
        # Update database
        await self.db.execute("DELETE FROM afk WHERE user_id = ?", (user_id,))
        await self.db.commit()
<<<<<<< HEAD
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

    # ─── Rate Limited Nickname Edits ───────────────────
    async def safe_edit_nick(self, member: discord.Member, new_nick: str):
        """Rate-limited nickname editing."""
        current_time = time.time()
        user_cooldown = self.last_nick_edit.get(member.id, 0)
        
        if current_time - user_cooldown < self.nick_edit_cooldown:
            # Schedule for later
            asyncio.create_task(self.delayed_nick_edit(member, new_nick, user_cooldown + self.nick_edit_cooldown - current_time))
            return
=======
>>>>>>> 8d6ce26f1b24fe4e87fa8cec6e876e005a5377cd
        
        # Update memory cache
        self.afk_users.pop(user_id, None)
        self.stats['db_operations'] += 1

    async def safe_edit_nick(self, member: discord.Member, new_nick: str) -> bool:
        """Safely edit nickname with error handling."""
        try:
            # Don't edit if it's the same
            if member.display_name == new_nick:
                return True
                
            await member.edit(nick=new_nick[:32])  # Discord's limit
            return True
        except discord.Forbidden:
            # No permission
            return False
        except discord.HTTPException:
            # Other errors
            return False

<<<<<<< HEAD
    async def delayed_nick_edit(self, member: discord.Member, new_nick: str, delay: float):
        """Edit nickname after delay."""
        await asyncio.sleep(delay)
        try:
            await member.edit(nick=new_nick)
            self.last_nick_edit[member.id] = time.time()
        except (discord.Forbidden, discord.HTTPException):
            pass

    # ─── Command Processing ────────────────────────────
    async def _execute_afk_command(self, ctx: commands.Context, reason: str):
        """Worker: Execute AFK command."""
        user = ctx.author
        
        # Check if already AFK
        existing = await self.get_afk(user.id)
        if existing:
            # Already AFK, update if reason changed
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
            # Set new AFK
            await self.set_afk(user.id, reason)
=======
    # ─── Commands ──────────────────────────────────────
    @commands.command(name="afk")
    async def afk(self, ctx: commands.Context, *, reason: str = "AFK"):
        """Set yourself as AFK."""
        user = ctx.author
        
        # Check if already AFK
        if user.id in self.afk_users:
            return await ctx.reply("You are already AFK!", mention_author=False, delete_after=5)
        
        # Delete command message
        try:
            await ctx.message.delete()
        except:
            pass

        await self.set_afk(user.id, reason)
>>>>>>> 8d6ce26f1b24fe4e87fa8cec6e876e005a5377cd

        # Update nickname if in guild
        if ctx.guild and not user.display_name.startswith("[AFK]"):
            new_nick = f"[AFK] {user.display_name[:27]}"  # 32 - 5 for [AFK]
            await self.safe_edit_nick(user, new_nick)

<<<<<<< HEAD
        # Send confirmation if not already AFK with same reason
        if not existing or existing[0] != reason:
            embed = embed if 'embed' in locals() else discord.Embed(
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
        await self.command_queue.put((ctx, reason))
=======
        # Send confirmation
        embed = discord.Embed(
            title="✅ AFK Set",
            description=f"{user.mention}, you are now AFK: **{reason}**",
            color=AFK_SET_COLOR
        )
        await ctx.send(embed=embed, delete_after=20)
        
        self.stats['commands_processed'] += 1
>>>>>>> 8d6ce26f1b24fe4e87fa8cec6e876e005a5377cd

    @commands.command(name="afklist")
    @commands.is_owner()
    async def afk_list(self, ctx: commands.Context):
        """Show all users currently AFK (bot owner only)."""
        if not self.afk_users:
            return await ctx.reply("No users are currently AFK.", mention_author=False)
        
        await ctx.typing()
        
        lines = []
<<<<<<< HEAD
        for user_id, reason, since in afk_users:
            user = self.bot.get_user(int(user_id))
            name = user.mention if user else f"Unknown ({user_id})"
            since_dt = datetime.fromisoformat(since)
            duration = human_timedelta(since_dt)
            lines.append(f"{name} — `{reason}` *(for {duration})*")
=======
        unknown_users = []
        
        # Process users in batches
        for user_id, (reason, since) in self.afk_users.items():
            # Try to get user from bot cache first
            user = self.bot.get_user(user_id)
            if user:
                name = user.name
            else:
                # If not in cache, we'll try to fetch later
                unknown_users.append((user_id, reason, since))
                continue
            
            since_dt = datetime.fromisoformat(since)
            duration = human_timedelta(since_dt)
            lines.append(f"**{name}** — {reason} *(for {duration})*")
        
        # Try to fetch unknown users from API (with rate limiting)
        for user_id, reason, since in unknown_users:
            try:
                user = await self.bot.fetch_user(user_id)
                name = user.name
            except discord.NotFound:
                # User doesn't exist or bot can't see them
                name = f"Unknown ({user_id})"
            except discord.HTTPException:
                # Rate limit or other error
                name = f"Unknown ({user_id})"
            
            since_dt = datetime.fromisoformat(since)
            duration = human_timedelta(since_dt)
            lines.append(f"**{name}** — {reason} *(for {duration})*")
            
            # Small delay to avoid rate limits
            await asyncio.sleep(0.1)
>>>>>>> 8d6ce26f1b24fe4e87fa8cec6e876e005a5377cd

        # Split into pages if too long
        chunks = [lines[i:i + 10] for i in range(0, len(lines), 10)]
        
        for i, chunk in enumerate(chunks):
            embed = discord.Embed(
                title=f"📜 AFK List (Page {i+1}/{len(chunks)})",
                description="\n".join(chunk),
                color=AFK_MENTION_COLOR
            )
            if i == 0:
                await ctx.reply(embed=embed, mention_author=False)
            else:
                await ctx.send(embed=embed)

    @commands.command(name="afkclean", aliases=["cleanafk"])
    @commands.has_permissions(administrator=True)
    async def afk_clean(self, ctx: commands.Context):
        """Remove AFK status for users who left the server (Admin only)."""
        await ctx.typing()
        
        if not self.afk_users:
            return await ctx.reply("No users are AFK.", mention_author=False)
        
        # Get all members in the guild
        guild_member_ids = {member.id for member in ctx.guild.members}
        
        # Find AFK users not in the guild
        users_to_remove = []
        for user_id in self.afk_users.keys():
            if user_id not in guild_member_ids:
                users_to_remove.append(user_id)
        
        if not users_to_remove:
            return await ctx.reply("✅ All AFK users are still in the server.", mention_author=False)
        
        # Remove from database
        placeholders = ','.join('?' for _ in users_to_remove)
        await self.db.execute(f"DELETE FROM afk WHERE user_id IN ({placeholders})", users_to_remove)
        await self.db.commit()
        
        # Remove from memory
        for user_id in users_to_remove:
            self.afk_users.pop(user_id, None)
        
        embed = discord.Embed(
            title="🧹 AFK Cleanup Complete",
            description=f"Removed AFK status for **{len(users_to_remove)}** user(s) who left the server.",
            color=discord.Color.green()
        )
        embed.set_footer(text=f"Total AFK users: {len(afk_users)}")
        await ctx.reply(embed=embed, mention_author=False)

    @commands.command(name="afkstats")
    @commands.is_owner()
    async def afk_stats(self, ctx: commands.Context):
        """Show AFK system statistics."""
        embed = discord.Embed(
            title="📊 AFK System Stats",
            color=discord.Color.blue()
        )
        embed.add_field(name="Messages Processed", value=self.stats['messages_processed'], inline=True)
        embed.add_field(name="Commands Processed", value=self.stats['commands_processed'], inline=True)
        embed.add_field(name="DB Operations", value=self.stats['db_operations'], inline=True)
<<<<<<< HEAD
        
        # Get queue sizes
        queue_sizes = {
            'Message Queue': self.message_queue.qsize(),
            'Command Queue': self.command_queue.qsize(),
            'DB Queue': self.db_queue.qsize()
        }
        
        for queue_name, size in queue_sizes.items():
            embed.add_field(name=queue_name, value=size, inline=True)
        
        embed.set_footer(text=f"Active worker tasks: {len(self.worker_tasks)}")
=======
        embed.add_field(name="Active AFK Users", value=len(self.afk_users), inline=True)
>>>>>>> 8d6ce26f1b24fe4e87fa8cec6e876e005a5377cd
        await ctx.reply(embed=embed, mention_author=False)

    # ─── Listeners ─────────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
<<<<<<< HEAD
        if message.author.bot:
            return

        # Don't process if it's an AFK command (let the command handler do it)
        ctx = await self.bot.get_context(message)
        if ctx.valid and ctx.command == self.afk:
            await self.bot.process_commands(message)
            return

        # Queue message for processing
        # Check for AFK removal
        await self.message_queue.put((message, 'check_afk_removal'))
        
        # Check mentions
        if message.mentions:
            await self.message_queue.put((message, 'check_mentions'))

=======
        if message.author.bot or not message.content:
            return await self.bot.process_commands(message)
        
        self.stats['messages_processed'] += 1
        
        # Check if message author is AFK (remove AFK)
        if message.author.id in self.afk_users:
            reason, since = self.afk_users[message.author.id]
            
            # Remove AFK
            await self.remove_afk(message.author.id)
            
            # Remove AFK from nickname
            if message.guild and message.author.display_name.startswith("[AFK]"):
                await self.safe_edit_nick(
                    message.author,
                    afk_strip(message.author.display_name)
                )
            
            # Send welcome back message
            since_dt = datetime.fromisoformat(since)
            duration = human_timedelta(since_dt)
            embed = discord.Embed(
                title="👋 Welcome Back!",
                description=f"{message.author.mention}, AFK removed.\nYou were AFK for **{duration}**.",
                color=AFK_REMOVED_COLOR
            )
            await message.channel.send(embed=embed, delete_after=20)
        
        # Check mentions for AFK users (with cooldown to avoid spam)
        if message.mentions:
            current_time = datetime.now().timestamp()
            
            for user in message.mentions:
                if user.id in self.afk_users:
                    # Check cooldown (5 seconds per user per channel)
                    cooldown_key = (message.channel.id, user.id)
                    last_mention = self.mention_cooldowns.get(cooldown_key, 0)
                    
                    if current_time - last_mention < 5:
                        continue
                    
                    self.mention_cooldowns[cooldown_key] = current_time
                    
                    reason, since = self.afk_users[user.id]
                    since_dt = datetime.fromisoformat(since)
                    duration = human_timedelta(since_dt)
                    
                    embed = discord.Embed(
                        title=f"{afk_strip(user.display_name)} is AFK 💤",
                        description=f"**Reason:** {reason}\n**Since:** {duration} ago",
                        color=AFK_MENTION_COLOR
                    )
                    await message.channel.send(embed=embed, delete_after=60)
                    break  # Only show for first AFK user mentioned
        
        # Process commands
>>>>>>> 8d6ce26f1b24fe4e87fa8cec6e876e005a5377cd
        await self.bot.process_commands(message)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """Automatically remove AFK when user leaves server."""
        if member.id in self.afk_users:
            await self.remove_afk(member.id)

    async def cog_unload(self):
<<<<<<< HEAD
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
=======
        """Clean shutdown."""
>>>>>>> 8d6ce26f1b24fe4e87fa8cec6e876e005a5377cd
        if self.db:
            await self.db.close()
            print("[AFK] Database closed cleanly.")


async def setup(bot: commands.Bot):
    await bot.add_cog(AFK(bot))