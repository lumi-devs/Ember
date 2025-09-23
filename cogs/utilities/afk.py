import discord
from discord.ext import commands
from datetime import datetime, timezone
from pathlib import Path
import json
import asyncio


# Colors for embeds
AFK_SET_COLOR = discord.Color.orange()
AFK_REMOVED_COLOR = discord.Color.gold()
AFK_MENTION_COLOR = discord.Color.from_rgb(255, 200, 0)

# AFK storage file
AFK_FILE = Path("data/afk.json")
AFK_FILE.parent.mkdir(exist_ok=True)  # Ensure 'data' folder exists

# --- Helper functions --- #

def load_afk() -> dict:
    """Load the AFK JSON safely."""
    if AFK_FILE.exists():
        try:
            return json.loads(AFK_FILE.read_text())
        except json.JSONDecodeError:
            return {}
    return {}

def save_afk_entry(user_id: int, reason: str, since: str):
    """Save a single AFK entry without overwriting others."""
    data = load_afk()  # Load fresh
    data[str(user_id)] = {"reason": reason, "since": since}
    AFK_FILE.write_text(json.dumps(data, indent=2))

def remove_afk_entry(user_id: int):
    """Remove a single AFK entry."""
    data = load_afk()
    data.pop(str(user_id), None)
    AFK_FILE.write_text(json.dumps(data, indent=2))

def afk_strip(name: str) -> str:
    """Remove [AFK] prefix from a name."""
    if name.startswith("[AFK]"):
        return name[len("[AFK] "):].lstrip()
    return name

def human_timedelta(dt: datetime) -> str:
    """Convert datetime to a human-readable difference."""
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

# --- AFK Cog --- #

class AFK(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.afk = load_afk()  # load from file once on startup



    @commands.command()
    @commands.is_owner()
    async def afklist(self, ctx):
        """Bot owner only: shows all AFK users and reasons"""
        if not self.afk_list:
            return await ctx.send("📭 No one is AFK right now.")

        embed = discord.Embed(
            title="AFK List",
            description="Here are the users currently marked as AFK:",
            color=discord.Color.blurple()
        )

        for uid, reason in self.afk_list.items():
            user = self.bot.get_user(int(uid))
            name = user.name if user else f"Unknown ({uid})"
            embed.add_field(name=name, value=reason, inline=False)

    @commands.is_owner()
    @commands.command(name="afkload")
    async def reload_afk(self, ctx: commands.Context):
        """Reload AFK data from file into memory (owner only)."""
        self.afk = load_afk()
        await ctx.send("✅ AFK data reloaded from file.", delete_after=10)

    @commands.command(name="afk")
    async def set_afk(self, ctx: commands.Context, *, reason: str = "AFK"):
        user = ctx.author
        await ctx.message.delete()
        since = datetime.now(timezone.utc).isoformat()

        # Save to memory + file
        self.afk[str(user.id)] = {"reason": reason, "since": since}
        AFK_FILE.write_text(json.dumps(self.afk, indent=2))

        # Update nickname
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

        # --- Remove AFK if author is AFK ---
        if str(message.author.id) in self.afk:
            afk_entry = self.afk.pop(str(message.author.id))
            AFK_FILE.write_text(json.dumps(self.afk, indent=2))

            if message.guild and message.author.display_name.startswith("[AFK]"):
                new_name = afk_strip(message.author.display_name)
                try:
                    await message.author.edit(nick=new_name)
                except discord.Forbidden:
                    pass

            try:
                since_dt = datetime.fromisoformat(afk_entry["since"])
                duration = human_timedelta(since_dt)
                embed = discord.Embed(
                    title="👋 Welcome Back!",
                    description=f"{message.author.mention}, AFK removed.\nYou were AFK for **{duration}**.",
                    color=AFK_REMOVED_COLOR
                )
                msg = await message.channel.send(embed=embed)
                await msg.delete(delay=20)
            except Exception:
                pass

        # --- Notify if mentions someone AFK ---
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
                    await msg.delete(delay=600)

        await self.bot.process_commands(message)


async def setup(bot: commands.Bot):
    await asyncio.sleep(5)
    await bot.add_cog(AFK(bot))
