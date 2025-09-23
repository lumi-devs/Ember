import discord
from discord.ext import commands
from datetime import datetime, timezone
from pathlib import Path
import json
import asyncio
from cogs.utilities.github_sync import GitHubSync

# Colors for embeds
AFK_SET_COLOR = discord.Color.orange()
AFK_REMOVED_COLOR = discord.Color.gold()
AFK_MENTION_COLOR = discord.Color.from_rgb(255, 200, 0)

# AFK storage file
AFK_FILE = Path("data/afk.json")
AFK_FILE.parent.mkdir(exist_ok=True)

# --- Helper functions --- #
def load_afk() -> dict:
    if AFK_FILE.exists():
        try:
            return json.loads(AFK_FILE.read_text())
        except json.JSONDecodeError:
            return {}
    return {}

def save_afk(data: dict):
    AFK_FILE.write_text(json.dumps(data, indent=2))

def afk_strip(name: str) -> str:
    if name.startswith("[AFK]"):
        return name[len("[AFK] "):].lstrip()
    return name

def human_timedelta(dt: datetime) -> str:
    now = datetime.now(timezone.utc)
    delta = now - dt
    days = delta.days
    seconds = delta.seconds
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    parts = []
    if days: parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours: parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes: parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    if seconds and not parts: parts.append(f"{seconds} second{'s' if seconds != 1 else ''}")
    return " ".join(parts) if parts else "just now"

# --- AFK Cog --- #
class AFK(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.afk = {}  # will load after GitHub pull

    async def load_from_github(self):
        """Pull afk.json once from GitHub on startup, then load locally."""
        github_cog = self.bot.get_cog("GitHubSync")
        if github_cog:
            await github_cog.pull_from_github()
        self.afk = load_afk()

    # --- Commands --- #
    @commands.command(name="afk")
    async def set_afk(self, ctx: commands.Context, *, reason: str = "AFK"):
        user = ctx.author
        await ctx.message.delete()
        since = datetime.now(timezone.utc).isoformat()

        self.afk[str(user.id)] = {"reason": reason, "since": since}
        save_afk(self.afk)

        if ctx.guild:
            try:
                if not user.display_name.startswith("[AFK]"):
                    await user.edit(nick=f"[AFK] {user.display_name}")
            except discord.Forbidden:
                pass

        embed = discord.Embed(
            description=f"✅ AFK Set! {user.mention}, you are now AFK: **{reason}**",
            color=AFK_SET_COLOR
        )
        msg = await ctx.send(embed=embed)
        await msg.delete(delay=10)

    @commands.command(name="back")
    async def remove_afk(self, ctx: commands.Context):
        user_id = str(ctx.author.id)
        if user_id in self.afk:
            self.afk.pop(user_id)
            save_afk(self.afk)

            if ctx.guild and ctx.author.display_name.startswith("[AFK]"):
                try:
                    await ctx.author.edit(nick=afk_strip(ctx.author.display_name))
                except discord.Forbidden:
                    pass

            await ctx.send(f"👋 Welcome back, {ctx.author.mention}!", delete_after=10)
        else:
            await ctx.send("❌ You weren't AFK.", delete_after=10)

    @commands.command(name="afklist")
    @commands.is_owner()
    async def afklist(self, ctx: commands.Context):
        if not self.afk:
            return await ctx.send("📭 No one is AFK right now.")

        embed = discord.Embed(
            title="AFK List",
            description="Users currently marked as AFK:",
            color=discord.Color.blurple()
        )

        for uid, entry in self.afk.items():
            user = self.bot.get_user(int(uid))
            name = user.name if user else f"Unknown ({uid})"
            reason = entry.get("reason", "AFK")
            since_dt = datetime.fromisoformat(entry.get("since"))
            duration = human_timedelta(since_dt)
            embed.add_field(
                name=name,
                value=f"Reason: {reason}\nSince: {duration} ago",
                inline=False
            )

        await ctx.send(embed=embed)

    @commands.command(name="afkload")
    @commands.is_owner()
    async def reload_afk(self, ctx: commands.Context):
        """Reload AFK data from file into memory"""
        self.afk = load_afk()
        await ctx.send("✅ AFK data reloaded from file.", delete_after=10)

    # --- Event listeners --- #
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        user_id = str(message.author.id)
        if user_id in self.afk:
            afk_entry = self.afk.pop(user_id)
            save_afk(self.afk)

            if message.guild and message.author.display_name.startswith("[AFK]"):
                try:
                    await message.author.edit(nick=afk_strip(message.author.display_name))
                except discord.Forbidden:
                    pass

            try:
                since_dt = datetime.fromisoformat(afk_entry["since"])
                duration = human_timedelta(since_dt)
                embed = discord.Embed(
                    description=f"👋 Welcome Back! {message.author.mention}, AFK removed.\nYou were AFK for **{duration}**.",
                    color=AFK_REMOVED_COLOR
                )
                msg = await message.channel.send(embed=embed)
                await msg.delete(delay=10)
            except Exception:
                pass

        # Notify mentions
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
                await msg.delete(delay=180)

        await self.bot.process_commands(message)

# --- Cog setup --- #
async def setup(bot: commands.Bot):
    afk_cog = AFK(bot)
    await asyncio.sleep(5)  # optional delay
    await afk_cog.load_from_github()  # pull once and load
    await bot.add_cog(afk_cog)

