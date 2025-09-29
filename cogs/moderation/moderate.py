import discord
from discord.ext import commands
from datetime import datetime, timedelta, timezone
import re

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
            embed = discord.Embed(title="❌ Missing Permissions", description="You need the **Moderate Members** permission to mute someone.", color=discord.Color.red())
            return await ctx.send(embed=embed)

        if member == ctx.author:
            embed = discord.Embed(title="⚠️ Action Denied", description="You cannot mute yourself.", color=discord.Color.red())
            return await ctx.send(embed=embed)

        if member.top_role >= ctx.author.top_role:
            embed = discord.Embed(title="⚠️ Action Denied", description="You cannot mute someone with an equal or higher role than you.", color=discord.Color.red())
            return await ctx.send(embed=embed)

        seconds = parse_duration(duration)
        until = datetime.now(timezone.utc) + timedelta(seconds=seconds) if seconds else None

        try:
            await member.timeout(until, reason=f"By - {ctx.author} | {reason}")
        except discord.Forbidden:
            embed = discord.Embed(title="❌ Cannot Mute", description="I don’t have permission to mute this user.", color=discord.Color.red())
            return await ctx.send(embed=embed)

        embed = discord.Embed(title="🔇 Member Muted", color=discord.Color.orange())
        embed.add_field(name="User", value=member.mention, inline=True)
        embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
        embed.add_field(name="Duration", value=duration if duration else "Indefinite", inline=True)
        embed.add_field(name="Reason", value=reason, inline=True)
        await ctx.send(embed=embed)

    # --- UNMUTE ---
    @commands.command(name="unmute")
    async def unmute(self, ctx, member: discord.Member):
        if not ctx.author.guild_permissions.moderate_members:
            embed = discord.Embed(title="❌ Missing Permissions", description="You need the **Moderate Members** permission to unmute someone.", color=discord.Color.red())
            return await ctx.send(embed=embed)

        if member.top_role >= ctx.author.top_role:
            embed = discord.Embed(title="⚠️ Action Denied", description="You cannot unmute someone with an equal or higher role than you.", color=discord.Color.red())
            return await ctx.send(embed=embed)

        try:
            await member.timeout(None, reason=f"Unmuted by {ctx.author}")
        except discord.Forbidden:
            embed = discord.Embed(title="❌ Cannot Unmute", description="I don’t have permission to unmute this user.", color=discord.Color.red())
            return await ctx.send(embed=embed)

        embed = discord.Embed(title="🔊 Member Unmuted", color=discord.Color.green())
        embed.add_field(name="User", value=member.mention, inline=True)
        embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
        await ctx.send(embed=embed)

    # --- KICK ---
    @commands.command(name="kick")
    async def kick(self, ctx, member: discord.Member, *, reason: str = "No reason provided"):
        if not ctx.author.guild_permissions.kick_members:
            embed = discord.Embed(title="❌ Missing Permissions", description="You need the **Kick Members** permission to kick someone.", color=discord.Color.red())
            return await ctx.send(embed=embed)

        if member == ctx.author:
            embed = discord.Embed(title="⚠️ Action Denied", description="You cannot kick yourself.", color=discord.Color.red())
            return await ctx.send(embed=embed)

        if member.top_role >= ctx.author.top_role:
            embed = discord.Embed(title="⚠️ Action Denied", description="You cannot kick someone with an equal or higher role than you.", color=discord.Color.red())
            return await ctx.send(embed=embed)

        try:
            await member.kick(reason=f"By - {ctx.author} | {reason}")
        except discord.Forbidden:
            embed = discord.Embed(title="❌ Cannot Kick", description="I don’t have permission to kick this user.", color=discord.Color.red())
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
            embed = discord.Embed(title="❌ Missing Permissions", description="You need the **Ban Members** permission to ban someone.", color=discord.Color.red())
            return await ctx.send(embed=embed)

        if member == ctx.author:
            embed = discord.Embed(title="⚠️ Action Denied", description="You cannot ban yourself.", color=discord.Color.red())
            return await ctx.send(embed=embed)

        if member.top_role >= ctx.author.top_role:
            embed = discord.Embed(title="⚠️ Action Denied", description="You cannot ban someone with an equal or higher role than you.", color=discord.Color.red())
            return await ctx.send(embed=embed)

        try:
            await member.ban(reason=f"By - {ctx.author} | {reason}")
        except discord.Forbidden:
            embed = discord.Embed(title="❌ Cannot Ban", description="I don’t have permission to ban this user.", color=discord.Color.red())
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
            embed = discord.Embed(title="❌ Missing Permissions", description="You need the **Ban Members** permission to unban someone.", color=discord.Color.red())
            return await ctx.send(embed=embed)

        try:
            user = await self.bot.fetch_user(user_id)
            await ctx.guild.unban(user, reason=f"Unbanned by {ctx.author}")
        except discord.NotFound:
            embed = discord.Embed(title="❌ Not Found", description="That user is not banned.", color=discord.Color.red())
            return await ctx.send(embed=embed)
        except discord.Forbidden:
            embed = discord.Embed(title="❌ Cannot Unban", description="I don’t have permission to unban this user.", color=discord.Color.red())
            return await ctx.send(embed=embed)

        embed = discord.Embed(title="⚖️ Member Unbanned", color=discord.Color.green())
        embed.add_field(name="User", value=user.mention, inline=True)
        embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Moderation(bot))
