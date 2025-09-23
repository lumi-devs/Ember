import discord
from discord.ext import commands
from datetime import datetime, timedelta, timezone
import re
import asyncio

def parse_duration(duration: str):
    """Parse strings like '10m', '3h', '1d', '1d2h10m' into seconds."""
    if not duration:
        return None
    pattern = re.compile(r"(\d+)([smhd])")
    matches = pattern.findall(duration.lower())
    if not matches:
        return None
    seconds = 0
    for amount, unit in matches:
        amount = int(amount)
        if unit == "s":
            seconds += amount
        elif unit == "m":
            seconds += amount * 60
        elif unit == "h":
            seconds += amount * 3600
        elif unit == "d":
            seconds += amount * 86400
    return seconds

class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # --- MUTE ---
    @commands.command(name="mute")
    async def mute(self, ctx, member: discord.Member, duration: str = None, *, reason: str = "No reason provided"):
        if not ctx.author.guild_permissions.moderate_members:
            embed = discord.Embed(title="❌ Missing Permissions", color=discord.Color.red())
            embed.description = "You need the **Moderate Members** permission to mute someone."
            return await ctx.send(embed=embed)
        
        if member == ctx.author:
            return await ctx.send("❌ You cannot mute yourself.")

        seconds = parse_duration(duration)
        until = datetime.now(timezone.utc) + timedelta(seconds=seconds) if seconds else None

        try:
            await member.timeout(until, reason=f"By - {ctx.author} | {reason}")        
        except discord.Forbidden:
            embed = discord.Embed(title="❌ Cannot Mute", color=discord.Color.red())
            embed.description = "I don’t have permission to mute this user."
            return await ctx.send(embed=embed)

        embed = discord.Embed(title="🔇 Member Muted", color=discord.Color.orange())
        embed.add_field(name="User", value=member.mention, inline=True)
        embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
        embed.add_field(name="Duration", value=duration if duration else "Indefinite", inline=True)
        embed.add_field(name="Reason", value=reason, inline=True)
        await ctx.send(embed=embed)

        # Optional auto-unmute message

    # --- UNMUTE ---
    @commands.command(name="unmute")
    async def unmute(self, ctx, member: discord.Member):
        if not ctx.author.guild_permissions.moderate_members:
            embed = discord.Embed(title="❌ Missing Permissions", color=discord.Color.red())
            embed.description = "You need the **Moderate Members** permission to unmute someone."
            return await ctx.send(embed=embed)

        try:
            await member.timeout(until=None, reason=f"Unmuted by {ctx.author}")
        except discord.Forbidden:
            embed = discord.Embed(title="❌ Cannot Unmute", color=discord.Color.red())
            embed.description = "I don’t have permission to unmute this user."
            return await ctx.send(embed=embed)

        embed = discord.Embed(title="🔊 Member Unmuted", color=discord.Color.green())
        embed.add_field(name="User", value=member.mention, inline=True)
        embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
        await ctx.send(embed=embed)

    # --- KICK ---
    @commands.command(name="kick")
    async def kick(self, ctx, member: discord.Member, *, reason: str = "No reason provided"):
        if not ctx.author.guild_permissions.kick_members:
            embed = discord.Embed(title="❌ Missing Permissions", color=discord.Color.red())
            embed.description = "You need the **Kick Members** permission to kick someone."
            return await ctx.send(embed=embed)

        if member == ctx.author:
            return await ctx.send("❌ You cannot kick yourself.")

        try:
            await member.kick(reason=f"By - {ctx.author} | {reason}")
        except discord.Forbidden:
            embed = discord.Embed(title="❌ Cannot Kick", color=discord.Color.red())
            embed.description = "I don’t have permission to kick this user."
            return await ctx.send(embed=embed)

        embed = discord.Embed(title="👢 Member Kicked", color=discord.Color.red())
        embed.add_field(name="User", value=member.mention, inline=True)
        embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
        embed.add_field(name="Reason", value=reason, inline=True)
        await ctx.send(embed=embed)

    # --- BAN ---
    @commands.command(name="ban")
    async def ban(self, ctx, member: discord.Member, *, reason: str = "No reason provided"):
        if not ctx.author.guild_permissions.ban_members:
            embed = discord.Embed(title="❌ Missing Permissions", color=discord.Color.red())
            embed.description = "You need the **Ban Members** permission to ban someone."
            return await ctx.send(embed=embed)

        if member == ctx.author:
            return await ctx.send("❌ You cannot ban yourself.")

        try:
            await member.ban(reason=f"By - {ctx.author} | {reason}")
        except discord.Forbidden:
            embed = discord.Embed(title="❌ Cannot Ban", color=discord.Color.red())
            embed.description = "I don’t have permission to ban this user."
            return await ctx.send(embed=embed)

        embed = discord.Embed(title="🔨 Member Banned", color=discord.Color.dark_red())
        embed.add_field(name="User", value=member.mention, inline=True)
        embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
        embed.add_field(name="Reason", value=reason, inline=True)
        await ctx.send(embed=embed)

    # --- UNBAN ---
    @commands.command(name="unban")
    async def unban(self, ctx, user_id: int):
        if not ctx.author.guild_permissions.ban_members:
            embed = discord.Embed(title="❌ Missing Permissions", color=discord.Color.red())
            embed.description = "You need the **Ban Members** permission to unban someone."
            return await ctx.send(embed=embed)

        try:
            user = await self.bot.fetch_user(user_id)
            await ctx.guild.unban(user, reason=f"Unbanned by {ctx.author}")
        except discord.NotFound:
            return await ctx.send("❌ That user is not banned.")
        except discord.Forbidden:
            embed = discord.Embed(title="❌ Cannot Unban", color=discord.Color.red())
            embed.description = "I don’t have permission to unban this user."
            return await ctx.send(embed=embed)

        embed = discord.Embed(title="⚖️ Member Unbanned", color=discord.Color.green())
        embed.add_field(name="User", value=user.mention)
        embed.add_field(name="Moderator", value=ctx.author.mention)
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Moderation(bot))
