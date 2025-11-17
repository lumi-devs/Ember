import discord
from discord.ext import commands
import aiosqlite
from datetime import datetime, timezone
import os
import asyncio

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
        
        # Simple in-memory cache for reads
        self.afk_cache = {}
        self.cache_lock = asyncio.Lock()
        
        self.bot.loop.create_task(self.init_db())

    async def init_db(self):
        self.db = await aiosqlite.connect(self.db_path)
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS afk (
                user_id TEXT PRIMARY KEY,
                reason TEXT,
                since TEXT
            )
        """)
        await self.db.commit()
        
        # Load cache on startup
        await self.load_cache()
        print("[AFK] Database ready with cache")

    async def load_cache(self):
        """Load all AFK data into memory."""
        async with self.db.execute("SELECT user_id, reason, since FROM afk") as cursor:
            rows = await cursor.fetchall()
            async with self.cache_lock:
                self.afk_cache = {int(user_id): (reason, since) for user_id, reason, since in rows}

    async def set_afk(self, user_id: int, reason: str):
        since = datetime.now(timezone.utc).isoformat()
        
        # Update cache first (fast)
        async with self.cache_lock:
            self.afk_cache[user_id] = (reason, since)
        
        # Then update database (async)
        asyncio.create_task(self._db_set_afk(user_id, reason, since))

    async def _db_set_afk(self, user_id: int, reason: str, since: str):
        """Background database update."""
        await self.db.execute(
            "REPLACE INTO afk (user_id, reason, since) VALUES (?, ?, ?)",
            (str(user_id), reason, since)
        )
        await self.db.commit()

    async def remove_afk(self, user_id: int):
        # Update cache first (fast)
        async with self.cache_lock:
            self.afk_cache.pop(user_id, None)
        
        # Then update database (async)
        asyncio.create_task(self._db_remove_afk(user_id))

    async def _db_remove_afk(self, user_id: int):
        """Background database update."""
        await self.db.execute("DELETE FROM afk WHERE user_id = ?", (str(user_id),))
        await self.db.commit()

    async def get_afk(self, user_id: int):
        """Get from cache (instant)."""
        async with self.cache_lock:
            return self.afk_cache.get(user_id)

    async def get_all_afk(self):
        """Get all from cache (instant)."""
        async with self.cache_lock:
            return [(user_id, reason, since) for user_id, (reason, since) in self.afk_cache.items()]

    @commands.command(name="afk")
    async def afk(self, ctx: commands.Context, *, reason: str = "AFK"):
        user = ctx.author
        
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
        await ctx.send(embed=embed, delete_after=20)

    @commands.command(name="afklist")
    @commands.is_owner()
    async def afk_list(self, ctx: commands.Context):
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
        afk_count = len(await self.get_all_afk())
        
        embed = discord.Embed(
            title="📊 AFK Stats",
            description=f"**Current AFK Users:** {afk_count}",
            color=discord.Color.blue()
        )
        await ctx.reply(embed=embed, mention_author=False)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.content:
            return

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
            await message.channel.send(embed=embed, delete_after=20)

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
                    await message.channel.send(embed=embed, delete_after=600)

    async def cog_unload(self):
        if self.db:
            await self.db.close()

async def setup(bot: commands.Bot):
    await bot.add_cog(AFK(bot))