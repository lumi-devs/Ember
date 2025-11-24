import discord
from discord.ext import commands
import aiosqlite
import asyncio
from datetime import datetime, timezone

AFK_SET_COLOR = discord.Color.orange()
AFK_REMOVED_COLOR = discord.Color.gold()
AFK_MENTION_COLOR = discord.Color.from_rgb(255, 200, 0)


def human_timedelta(dt: datetime) -> str:
    """Return human-readable time difference."""
    now = datetime.now(timezone.utc)
    delta = now - dt
    
    if delta.days > 0:
        return f"{delta.days} day{'s' if delta.days != 1 else ''}"
    elif delta.seconds >= 3600:
        hours = delta.seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''}"
    elif delta.seconds >= 60:
        minutes = delta.seconds // 60
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    else:
        return "just now"


class AFK(commands.Cog):
    """Fast AFK system without complex queues."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db_path = "data/afk.db"
        self.db = None
        self.nick_cooldowns = {}

    async def init_db(self):
        """Initialize database."""
        self.db = await aiosqlite.connect(self.db_path)
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS afk (
                user_id TEXT PRIMARY KEY,
                reason TEXT,
                since TEXT
            )
        """)
        await self.db.commit()

    async def set_afk(self, user_id: int, reason: str):
        """Set AFK status."""
        since = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            "REPLACE INTO afk (user_id, reason, since) VALUES (?, ?, ?)",
            (str(user_id), reason, since)
        )
        await self.db.commit()

    async def remove_afk(self, user_id: int):
        """Remove AFK status."""
        await self.db.execute("DELETE FROM afk WHERE user_id = ?", (str(user_id),))
        await self.db.commit()

    async def get_afk(self, user_id: int):
        """Get AFK status."""
        async with self.db.execute(
            "SELECT reason, since FROM afk WHERE user_id = ?", (str(user_id),)
        ) as cursor:
            return await cursor.fetchone()

    async def get_all_afk(self):
        """Get all AFK users."""
        async with self.db.execute("SELECT user_id, reason, since FROM afk") as cursor:
            return await cursor.fetchall()

    async def safe_edit_nick(self, member: discord.Member, new_nick: str):
        """Safely edit nickname with cooldown."""
        current_time = discord.utils.utcnow().timestamp()
        if self.nick_cooldowns.get(member.id, 0) > current_time - 2:
            return
        
        try:
            await member.edit(nick=new_nick)
            self.nick_cooldowns[member.id] = current_time
        except (discord.Forbidden, discord.HTTPException):
            pass

    def afk_strip(self, name: str) -> str:
        """Remove [AFK] prefix from nick if present."""
        return name[len("[AFK]"):].lstrip() if name.startswith("[AFK]") else name

    @commands.command(name="afk")
    async def afk(self, ctx: commands.Context, *, reason: str = "AFK"):
        """Set yourself as AFK."""
        # Delete command message
        try:
            await ctx.message.delete()
        except:
            pass

        user = ctx.author
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
        await ctx.send(embed=embed, delete_after=10)

    @commands.command(name="afklist")
    @commands.is_owner()
    async def afk_list(self, ctx: commands.Context):
        """Show all users currently AFK."""
        afk_users = await self.get_all_afk()
        if not afk_users:
            return await ctx.reply("No users are currently AFK.", mention_author=False)

        lines = []
        for user_id, reason, since in afk_users:
            user = self.bot.get_user(int(user_id))
            name = user.name if user else f"Unknown ({user_id})"
            since_dt = datetime.fromisoformat(since)
            duration = human_timedelta(since_dt)
            lines.append(f"**{name}** — {reason} *(for {duration})*")

        embed = discord.Embed(
            title="📜 AFK List",
            description="\n".join(lines),
            color=AFK_MENTION_COLOR
        )
        await ctx.reply(embed=embed, mention_author=False)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        # Check if user should be removed from AFK
        afk_entry = await self.get_afk(message.author.id)
        if afk_entry:
            await self.remove_afk(message.author.id)
            
            # Remove AFK from nickname
            if message.guild and message.author.display_name.startswith("[AFK]"):
                await self.safe_edit_nick(
                    message.author,
                    self.afk_strip(message.author.display_name)
                )

            # Send welcome back message
            since_dt = datetime.fromisoformat(afk_entry[1])
            duration = human_timedelta(since_dt)
            embed = discord.Embed(
                title="👋 Welcome Back!",
                description=f"{message.author.mention}, AFK removed.\nYou were AFK for **{duration}**.",
                color=AFK_REMOVED_COLOR
            )
            await message.channel.send(embed=embed, delete_after=10)

        # Check mentions for AFK users
        for user in message.mentions:
            entry = await self.get_afk(user.id)
            if entry:
                reason, since = entry
                since_dt = datetime.fromisoformat(since)
                duration = human_timedelta(since_dt)
                embed = discord.Embed(
                    title=f"{self.afk_strip(user.display_name)} is AFK 💤",
                    description=f"**Reason:** {reason}\n**Since:** {duration} ago",
                    color=AFK_MENTION_COLOR
                )
                await message.channel.send(embed=embed, delete_after=30)

    async def cog_unload(self):
        """Clean shutdown."""
        if self.db:
            await self.db.close()


async def setup(bot: commands.Bot):
    cog = AFK(bot)
    await cog.init_db()
    await bot.add_cog(cog)