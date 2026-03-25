from __future__ import annotations

import discord
from discord.ext import commands
import time
import psutil
import os
from datetime import datetime


class CutePing(commands.Cog):
    """🌸 A super cute ping cog with adorable embeds! 🐾"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Use bot.start_time if the main bot file sets it; otherwise fall back to
        # cog load time (uptime would reset on cog reload in that case).
        self.start_time = getattr(bot, "start_time", time.time())
        self.process = psutil.Process(os.getpid())

    def get_uptime(self) -> str:
        """Get cute uptime string."""
        uptime_seconds = int(time.time() - self.start_time)

        days = uptime_seconds // 86400
        hours = (uptime_seconds % 86400) // 3600
        minutes = (uptime_seconds % 3600) // 60
        seconds = uptime_seconds % 60

        parts = []
        if days > 0:
            parts.append(f"{days}d")
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0:
            parts.append(f"{minutes}m")
        if seconds > 0 or not parts:
            parts.append(f"{seconds}s")

        return " ".join(parts)

    def get_cute_status(self, ms: float) -> tuple[str, discord.Color]:
        """Get cute status emoji and color based on ping."""
        if ms < 100:
            return "💚", discord.Color.green()  # Excellent
        elif ms < 200:
            return "💛", discord.Color.gold()  # Good
        elif ms < 300:
            return "🧡", discord.Color.orange()  # Okay
        else:
            return "❤️", discord.Color.red()  # Slow

    @commands.command(name="ping", aliases=["pong", "latency", "🏓"])
    @commands.has_permissions(manage_messages=True)
    async def cute_ping(self, ctx: commands.Context):
        """🏓 Check my adorable ping and status! ✨"""

        # Record start time
        start = time.time()

        # Create initial embed while we wait
        embed = discord.Embed(
            title="🌸 **Pinging...** 🏓",
            description="*calculating cuteness level...*",
            color=discord.Color.lighter_gray(),
        )

        thinking_msg = await ctx.send(embed=embed)

        # Calculate latencies
        api_end = time.time()
        api_ms = round((api_end - start) * 1000, 2)

        # Get websocket latency
        ws_ms = round(self.bot.latency * 1000, 2)

        # Get cute status
        status_emoji, status_color = self.get_cute_status(ws_ms)

        # Get system stats
        cpu_usage = self.process.cpu_percent()
        memory_usage = self.process.memory_percent()

        # Create the adorable response embed
        cute_embed = discord.Embed(
            title=f"{status_emoji} **Pong!** 🏓 {status_emoji}",
            description="*Here's my adorable status!* 🐾",
            color=status_color,
            timestamp=datetime.now(),
        )

        # Add cute field names and values
        cute_embed.add_field(
            name="💞 **Heartbeat**", value=f"```\n{ws_ms}ms\n```", inline=True
        )

        cute_embed.add_field(
            name="✨ **Sparkles**", value=f"```\n{api_ms}ms\n```", inline=True
        )

        cute_embed.add_field(
            name="🎀 **Round Trip**",
            value=f"```\n{round(ws_ms + api_ms, 2)}ms\n```",
            inline=True,
        )

        # System stats with cute icons
        cute_embed.add_field(
            name="🧸 **CPU Cuddles**", value=f"```\n{cpu_usage:.1f}%\n```", inline=True
        )

        cute_embed.add_field(
            name="🍪 **Memory Munchies**",
            value=f"```\n{memory_usage:.1f}%\n```",
            inline=True,
        )

        cute_embed.add_field(
            name="⏰ **Uptime Snuggles**",
            value=f"```\n{self.get_uptime()}\n```",
            inline=True,
        )

        # Add some cute flavor text
        if ws_ms < 100:
            cute_embed.add_field(
                name="🌟 **Status**",
                value="*I'm zooming like a happy puppy!* 🐕",
                inline=False,
            )
        elif ws_ms < 200:
            cute_embed.add_field(
                name="🐢 **Status**",
                value="*Just a cozy turtle today!* 🐢",
                inline=False,
            )
        else:
            cute_embed.add_field(
                name="☕ **Status**", value="*Need a coffee break...* ⏳", inline=False
            )

        # Set author and footer
        cute_embed.set_author(
            name=f"{self.bot.user.name} 💖", icon_url=self.bot.user.display_avatar.url
        )

        cute_embed.set_footer(
            text=f"Requested by {ctx.author.display_name} • ✨ Cuteness overload!",
            icon_url=ctx.author.display_avatar.url,
        )

        # Add a cute thumbnail if available
        if self.bot.user.display_avatar:
            cute_embed.set_thumbnail(url=self.bot.user.display_avatar.url)

        # Edit the message with our cute embed
        await thinking_msg.edit(embed=cute_embed)

    @commands.command(name="status", aliases=["stats", "about", "cute"])
    async def cute_status(self, ctx: commands.Context):
        """🌸 Show my adorable detailed status! 🎀"""

        # Get all latencies
        ws_ms = round(self.bot.latency * 1000, 2)
        _, status_color = self.get_cute_status(ws_ms)

        # Get system stats
        cpu_usage = self.process.cpu_percent(interval=0.5)
        memory_mb = self.process.memory_info().rss / 1024 / 1024

        # Count guilds, users, channels
        guild_count = len(self.bot.guilds)
        user_count = sum(g.member_count for g in self.bot.guilds if g.member_count)
        channel_count = sum(len(g.channels) for g in self.bot.guilds)

        # Create super cute embed
        embed = discord.Embed(
            title="🎀 **My Cute Status Board** 🎀",
            description="*Hewwo! Here's how I'm doing!* ✨",
            color=status_color,
            timestamp=datetime.now(),
        )

        # Left column - Bot stats
        embed.add_field(
            name="🤖 **Bot Cuteness**",
            value=f"```\n"
            f"Servers:  {guild_count}\n"
            f"Users:    {user_count}\n"
            f"Channels: {channel_count}\n"
            f"```",
            inline=True,
        )

        # Right column - Performance
        embed.add_field(
            name="⚡ **Speediness**",
            value=f"```\n"
            f"Ping:     {ws_ms}ms\n"
            f"CPU:      {cpu_usage:.1f}%\n"
            f"Memory:   {memory_mb:.1f} MB\n"
            f"```",
            inline=True,
        )

        # Bottom row - Uptime and cute message
        embed.add_field(
            name="⏰ **Snuggle Time**",
            value=f"```\n{self.get_uptime()}\n```",
            inline=True,
        )

        embed.add_field(
            name="💝 **Love Meter**",
            value=f"```\n{'❤️' * int(ws_ms/100 + 1)}```",
            inline=True,
        )

        # Add some personality
        if ws_ms < 100:
            mood = "🌸 I'm feeling fantastic today! ✨"
        elif ws_ms < 200:
            mood = "🐢 Just taking it easy~"
        elif ws_ms < 300:
            mood = "😴 A little sleepy but still working!"
        else:
            mood = "☕ Time for a coffee break..."

        embed.add_field(name="💭 **Current Mood**", value=f"*{mood}*", inline=False)

        # Pretty borders
        embed.set_author(
            name=f"{self.bot.user.name} • ✨ Cute Bot ✨",
            icon_url=self.bot.user.display_avatar.url,
        )

        embed.set_footer(
            text=f"Requested by {ctx.author.display_name} • 💕 Be happy!",
            icon_url=ctx.author.display_avatar.url,
        )

        embed.set_thumbnail(url=self.bot.user.display_avatar.url)

        await ctx.send(embed=embed)

    @commands.command(name="heartbeat", aliases=["💓", "beat"])
    async def cute_heartbeat(self, ctx: commands.Context):
        """💓 Check my little heart beating!"""

        ws_ms = round(self.bot.latency * 1000, 2)

        # Create heartbeat animation
        if ws_ms < 100:
            heart = "💗💓💖💞💕"
            beat = "lub-dub lub-dub 💓"
        elif ws_ms < 200:
            heart = "💓💗💓💗💓"
            beat = "thump-thump thump-thump 💗"
        else:
            heart = "💔💓💔💓💔"
            beat = "puff puff... 😮‍💨"

        embed = discord.Embed(
            title="💓 **Heartbeat Monitor** 💓",
            description=f"```\n{heart}\n```\n**{beat}**",
            color=discord.Color.from_rgb(255, 192, 203),  # Pink!
        )

        embed.add_field(
            name="📊 **BPM (Bot Per Millisecond)**",
            value=f"```\n{ws_ms}ms\n```",
            inline=True,
        )

        embed.add_field(
            name="🩺 **Health Status**",
            value=(
                "```\nHealthy and happy!\n```"
                if ws_ms < 300
                else "```\nNeeds hugs...\n```"
            ),
            inline=True,
        )

        embed.set_footer(text="💝 Beating just for you!")

        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(CutePing(bot))