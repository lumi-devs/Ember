import discord
from discord.ext import commands
import aiosqlite
import asyncio
from datetime import datetime, timezone
from collections import deque
import os

AFK_SET_COLOR = discord.Color.orange()
AFK_REMOVED_COLOR = discord.Color.gold()
AFK_MENTION_COLOR = discord.Color.from_rgb(255, 200, 0)

def afk_strip(name: str) -> str:
    return name[len("[AFK]"):].lstrip() if name.startswith("[AFK]") else name

def human_timedelta(dt: datetime) -> str:
    now = datetime.now(timezone.utc)
    delta = now - dt
    days, seconds = delta.days, delta.seconds
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    
    parts = []
    if days: parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours: parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes: parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    if seconds and not parts: parts.append(f"{seconds} second{'s' if seconds != 1 else ''}")
    
    return " ".join(parts) if parts else "just now"

class AFK(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db_path = "data/afk.db"
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.db = None
        
        # Simple DB queue for writes only (reads are immediate)
        self.db_queue = deque()
        self.db_worker_task = None
        self.db_lock = asyncio.Lock()
        
        # Cache for frequent reads
        self.afk_cache = {}  # user_id -> (reason, since)
        self.cache_lock = asyncio.Lock()
        
        # Rate limiting
        self.last_operations = {}
        self.operation_cooldown = 0.1  # 100ms between operations
        
        self.bot.loop.create_task(self.init_system())

    async def init_system(self):
        """Initialize database and start worker."""
        self.db = await aiosqlite.connect(self.db_path)
        
        # Create table
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS afk (
                user_id TEXT PRIMARY KEY,
                reason TEXT,
                since TEXT
            )
        """)
        await self.db.commit()
        
        # Load cache
        await self.load_cache()
        
        # Start DB worker
        self.db_worker_task = asyncio.create_task(self.db_worker())
        
        print("[AFK] System initialized with cache and DB worker")

    async def load_cache(self):
        """Load all AFK data into cache."""
        async with self.db.execute("SELECT user_id, reason, since FROM afk") as cursor:
            rows = await cursor.fetchall()
            async with self.cache_lock:
                self.afk_cache = {int(user_id): (reason, since) for user_id, reason, since in rows}
        print(f"[AFK] Loaded {len(self.afk_cache)} AFK entries into cache")

    async def db_worker(self):
        """Single worker to process DB write operations."""
        while not self.bot.is_closed():
            try:
                if self.db_queue:
                    async with self.db_lock:
                        operation, args = self.db_queue.popleft()
                    
                    if operation == 'set_afk':
                        user_id, reason = args
                        since = datetime.now(timezone.utc).isoformat()
                        
                        await self.db.execute(
                            "REPLACE INTO afk (user_id, reason, since) VALUES (?, ?, ?)",
                            (str(user_id), reason, since)
                        )
                        await self.db.commit()
                        
                        # Update cache
                        async with self.cache_lock:
                            self.afk_cache[user_id] = (reason, since)
                            
                    elif operation == 'remove_afk':
                        user_id = args[0]
                        
                        await self.db.execute("DELETE FROM afk WHERE user_id = ?", (str(user_id),))
                        await self.db.commit()
                        
                        # Update cache
                        async with self.cache_lock:
                            self.afk_cache.pop(user_id, None)
                
                await asyncio.sleep(0.01)  # Small delay to prevent CPU spinning
                
            except Exception as e:
                print(f"[AFK] DB worker error: {e}")
                await asyncio.sleep(1)

    # ─── Database Operations ────────────────────────────
    async def set_afk(self, user_id: int, reason: str):
        """Queue AFK set operation."""
        async with self.db_lock:
            self.db_queue.append(('set_afk', (user_id, reason)))

    async def remove_afk(self, user_id: int):
        """Queue AFK removal operation."""
        async with self.db_lock:
            self.db_queue.append(('remove_afk', (user_id,)))

    async def get_afk(self, user_id: int):
        """Get AFK status from cache (immediate)."""
        async with self.cache_lock:
            return self.afk_cache.get(user_id)

    async def get_all_afk(self):
        """Get all AFK users from cache (immediate)."""
        async with self.cache_lock:
            return [(user_id, reason, since) for user_id, (reason, since) in self.afk_cache.items()]

    async def rate_limit(self, user_id: int):
        """Simple rate limiting."""
        current_time = asyncio.get_event_loop().time()
        last_op = self.last_operations.get(user_id, 0)
        
        if current_time - last_op < self.operation_cooldown:
            await asyncio.sleep(self.operation_cooldown)
        
        self.last_operations[user_id] = asyncio.get_event_loop().time()

    # ─── Commands ──────────────────────────────────────
    @commands.command(name="afk")
    async def afk(self, ctx: commands.Context, *, reason: str = "AFK"):
        """Set yourself as AFK."""
        user = ctx.author
        
        # Rate limit
        await self.rate_limit(user.id)
        
        # Delete command message
        try:
            await ctx.message.delete()
        except:
            pass

        await self.set_afk(user.id, reason)

        # Update nickname
        if ctx.guild:
            try:
                current_nick = user.display_name
                if not current_nick.startswith("[AFK]"):
                    new_nick = f"[AFK] {current_nick}"
                    await user.edit(nick=new_nick)
            except:
                pass

        embed = discord.Embed(
            title="✅ AFK Set",
            description=f"{user.mention}, you are now AFK: **{reason}**",
            color=AFK_SET_COLOR
        )
        msg = await ctx.send(embed=embed)
        
        # Auto-delete after 20 seconds
        await asyncio.sleep(20)
        try:
            await msg.delete()
        except:
            pass

    @commands.command(name="afklist")
    @commands.is_owner()
    async def afk_list(self, ctx: commands.Context):
        """Show all users currently AFK."""
        afk_users = await self.get_all_afk()
        if not afk_users:
            return await ctx.reply("No users are currently AFK.", mention_author=False)

        lines = []
        for user_id, reason, since in afk_users:
            user = self.bot.get_user(user_id)
            name = user.mention if user else f"Unknown ({user_id})"
            since_dt = datetime.fromisoformat(since)
            duration = human_timedelta(since_dt)
            
            lines.append(f"{name} — **{reason}** *(for {duration})*")

        embed = discord.Embed(
            title="📜 AFK List",
            description="\n".join(lines),
            color=AFK_MENTION_COLOR
        )
        await ctx.reply(embed=embed, mention_author=False)

    @commands.command(name="afkstats")
    @commands.is_owner()
    async def afk_stats(self, ctx: commands.Context):
        """Show AFK system stats."""
        afk_count = len(await self.get_all_afk())
        queue_size = len(self.db_queue)
        
        embed = discord.Embed(
            title="📊 AFK System Stats",
            color=discord.Color.blue()
        )
        embed.add_field(name="Current AFK Users", value=afk_count, inline=True)
        embed.add_field(name="DB Queue Size", value=queue_size, inline=True)
        embed.add_field(name="Cache Size", value=len(self.afk_cache), inline=True)
        
        await ctx.reply(embed=embed, mention_author=False)

    # ─── Listeners ─────────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.content:
            return

        # Rate limit
        await self.rate_limit(message.author.id)

        # Check if message author is AFK and remove AFK
        afk_entry = await self.get_afk(message.author.id)
        if afk_entry:
            await self.remove_afk(message.author.id)
            
            # Remove AFK from nickname
            if message.guild and message.author.display_name.startswith("[AFK]"):
                try:
                    new_nick = afk_strip(message.author.display_name)
                    await message.author.edit(nick=new_nick)
                except:
                    pass

            # Send welcome back message
            since_dt = datetime.fromisoformat(afk_entry[1])
            duration = human_timedelta(since_dt)
            embed = discord.Embed(
                title="👋 Welcome Back!",
                description=f"{message.author.mention}, AFK removed.\nYou had been AFK for **{duration}**.",
                color=AFK_REMOVED_COLOR
            )
            msg = await message.channel.send(embed=embed)
            
            # Auto-delete after 20 seconds
            asyncio.create_task(self.auto_delete_message(msg, 20))

        # Check mentions for AFK users
        if message.mentions:
            for user in message.mentions:
                if user.id == message.author.id:
                    continue
                    
                afk_entry = await self.get_afk(user.id)
                if afk_entry:
                    reason, since = afk_entry
                    since_dt = datetime.fromisoformat(since)
                    duration = human_timedelta(since_dt)
                    embed = discord.Embed(
                        title=f"{afk_strip(user.display_name)} is AFK 💤",
                        description=f"**Reason:** {reason}\n**Since:** {duration} ago",
                        color=AFK_MENTION_COLOR
                    )
                    msg = await message.channel.send(embed=embed)
                    
                    # Auto-delete after 10 minutes
                    asyncio.create_task(self.auto_delete_message(msg, 600))

    async def auto_delete_message(self, message: discord.Message, delay: int):
        """Auto-delete a message after delay."""
        await asyncio.sleep(delay)
        try:
            await message.delete()
        except:
            pass

    async def cog_unload(self):
        """Clean shutdown."""
        if self.db_worker_task:
            self.db_worker_task.cancel()
        if self.db:
            await self.db.close()
        print("[AFK] System shut down cleanly.")

async def setup(bot: commands.Bot):
    await bot.add_cog(AFK(bot))