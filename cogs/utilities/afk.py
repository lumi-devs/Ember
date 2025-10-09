import discord
from discord.ext import commands
from datetime import datetime, timezone
import aiosqlite

AFK_SET_COLOR = discord.Color.orange()        # Orangish
AFK_REMOVED_COLOR = discord.Color.gold()      # Yellowish
AFK_MENTION_COLOR = discord.Color.from_rgb(255, 200, 0)  # Golden tone


def afk_strip(name: str) -> str:
    if name.startswith("[AFK]"):
        return name[len("[AFK]"):].lstrip()
    return name


def human_timedelta(dt: datetime) -> str:
    now = datetime.now(timezone.utc)
    delta = now - dt
    days = delta.days
    seconds = delta.seconds
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
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db_path = "data/afk.db"
        self.bot.loop.create_task(self.init_db())

    async def init_db(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS afk (
                    user_id TEXT PRIMARY KEY,
                    reason TEXT,
                    since TEXT
                )
            """)
            await db.commit()

    async def set_afk(self, user_id: int, reason: str):
        since = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "REPLACE INTO afk (user_id, reason, since) VALUES (?, ?, ?)",
                (str(user_id), reason, since)
            )
            await db.commit()

    async def remove_afk(self, user_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM afk WHERE user_id = ?", (str(user_id),))
            await db.commit()

    async def get_afk(self, user_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT reason, since FROM afk WHERE user_id = ?", (str(user_id),)) as cursor:
                return await cursor.fetchone()

    async def get_all_afk(self):
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT user_id, reason, since FROM afk") as cursor:
                return await cursor.fetchall()

    @commands.command(name="afk")
    async def afk(self, ctx: commands.Context, *, reason: str = "AFK"):
        user = ctx.author
        await ctx.message.delete()
        await self.set_afk(user.id, reason)

        if ctx.guild:
            try:
                if not user.display_name.startswith("[AFK]"):
                    await user.edit(nick=f"[AFK] {user.display_name}")
            except discord.Forbidden:
                pass

        embed = discord.Embed(
            title="✅ AFK Set",
            description=f"{user.mention}, you are now AFK: **{reason}**",
            color=AFK_SET_COLOR
        )
        msg = await ctx.send(embed=embed)
        await msg.delete(delay=20)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        # Check if user was AFK
        afk_entry = await self.get_afk(message.author.id)
        if afk_entry:
            await self.remove_afk(message.author.id)
            if message.guild and message.author.display_name.startswith("[AFK]"):
                new = message.author.display_name[len("[AFK] "):]
                try:
                    await message.author.edit(nick=new)
                except discord.Forbidden:
                    pass
            try:
                since_dt = datetime.fromisoformat(afk_entry[1])
                duration = human_timedelta(since_dt)
                embed = discord.Embed(
                    title="👋 Welcome Back!",
                    description=f"{message.author.mention}, AFK removed.\nYou had been AFK for **{duration}**.",
                    color=AFK_REMOVED_COLOR
                )
                msg = await message.channel.send(embed=embed)
                await msg.delete(delay=20)
            except Exception:
                pass

        # Notify when mentioning AFK users
        if message.mentions:
            for u in message.mentions:
                entry = await self.get_afk(u.id)
                if entry:
                    reason, since = entry
                    since_dt = datetime.fromisoformat(since)
                    duration = human_timedelta(since_dt)
                    embed = discord.Embed(
                        title=f"{afk_strip(u.display_name)} is AFK 💤",
                        description=f"**Reason:** {reason}\n**Since:** {duration} ago",
                        color=AFK_MENTION_COLOR
                    )
                    msg = await message.channel.send(embed=embed)
                    await msg.delete(delay=600)

        await self.bot.process_commands(message)


async def setup(bot: commands.Bot):
    await bot.add_cog(AFK(bot))
