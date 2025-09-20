import discord
from discord.ext import commands
from helpers.github_data import load_db, save_db
from datetime import datetime, timezone

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
        self.afk, self.afk_file = load_db("afk.json")
        if not isinstance(self.afk, dict):
            self.afk = {}

    @commands.command(name="afk")
    async def afk(self, ctx: commands.Context, *, reason: str = "AFK"):
        user = ctx.author
        await ctx.message.delete()
        since = datetime.now(timezone.utc).isoformat()
        self.afk[str(user.id)] = {"reason": reason, "since": since}
        save_db(self.afk, self.afk_file, "afk.json")
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
        await msg.delete(delay=10)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        # Remove AFK if user sends a message
        if str(message.author.id) in self.afk:
            afk_entry = self.afk.pop(str(message.author.id))
            save_db(self.afk, self.afk_file, "afk.json")
            if message.guild and message.author.display_name.startswith("[AFK]"):
                new = message.author.display_name[len("[AFK] "):]
                try:
                    await message.author.edit(nick=new)
                except discord.Forbidden:
                    pass
            try:
                since_dt = datetime.fromisoformat(afk_entry["since"])
                duration = human_timedelta(since_dt)
                embed = discord.Embed(
                    title="👋 Welcome Back!",
                    description=f"{message.author.mention}, AFK removed.\nYou had been AFK for **{duration}**.",
                    color=AFK_REMOVED_COLOR
                )
                msg = await message.channel.send(embed=embed)
                await msg.delete(delay=10)
            except Exception:
                pass

        # Notify if mentioned someone who is AFK
        if message.mentions:
            for u in message.mentions:
                if str(u.id) in self.afk:
                    afk_entry = self.afk[str(u.id)]
                    since_dt = datetime.fromisoformat(afk_entry["since"])
                    duration = human_timedelta(since_dt)
                    embed = discord.Embed(
                        title=f"{afk_strip(u.display_name)} is AFK 💤",
                        description=f"**Reason:** {afk_entry['reason']}\n**Since:** {duration} ago",
                        color=AFK_MENTION_COLOR
                    )
                    msg = await message.channel.send(embed=embed)
                    await msg.delete(delay=120)

        await self.bot.process_commands(message)

async def setup(bot: commands.Bot):
    await bot.add_cog(AFK(bot))
