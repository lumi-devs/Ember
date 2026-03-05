import discord
from discord.ext import commands
import re
import os
import time
import json
from collections import defaultdict


class GameCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        # Parse GAME_ROLES from environment variable (JSON format)
        game_roles_str = os.environ.get("GAME_ROLES", "{}")
        try:
            self.GAME_ROLES = json.loads(game_roles_str)
        except json.JSONDecodeError:
            print("Error parsing GAME_ROLES JSON. Using empty dict.")
            self.GAME_ROLES = {}

        # Parse GAME_PING_ALLOWED_ROLES from environment variable (JSON array)
        allowed_roles_str = os.environ.get("GAME_PING_ALLOWED_ROLES", "[]")
        try:
            self.GAME_PING_ALLOWED_ROLES = json.loads(allowed_roles_str)
        except json.JSONDecodeError:
            print("Error parsing GAME_PING_ALLOWED_ROLES JSON. Using empty list.")
            self.GAME_PING_ALLOWED_ROLES = []

        # Spam protection
        self.user_cooldowns = defaultdict(list)  # Track user command timestamps
        self.channel_cooldowns = defaultdict(list)  # Track channel command timestamps
        self.role_cooldowns = defaultdict(list)  # Track role ping timestamps

        # Spam protection settings
        self.COOLDOWN_SECONDS = 60 * 5  # 5 minutes
        self.CHANNEL_COOLDOWN = 60  # 1 minute
        self.ROLE_COOLDOWN = (
            600 * 10
        )  # 100 minutes (10 minutes between pings for same role)
        self.MAX_PINGS_PER_DAY = 5  # 5 pings per day per user

        # Track daily usage
        self.daily_usage = defaultdict(list)  # Track user pings per day

        # Rate limit tracking
        self.rate_limit_alerts = defaultdict(int)  # Track rate limit alerts

    def normalize_game_name(self, game_name):
        """Normalize game name to match dictionary keys"""
        game_name = game_name.lower().strip()
        # Remove special characters and spaces
        game_name = re.sub(r"[^a-z0-9:]", "", game_name)
        return game_name

    def is_rate_limited(self, user_id, channel_id, role_id):
        """Check if user/channel/role is rate limited"""
        current_time = time.time()

        # Check user cooldown
        user_timestamps = self.user_cooldowns[user_id]
        user_timestamps = [
            t for t in user_timestamps if current_time - t < self.COOLDOWN_SECONDS
        ]
        self.user_cooldowns[user_id] = user_timestamps

        if user_timestamps:  # User has used command recently
            return (
                True,
                f"Please wait {int(self.COOLDOWN_SECONDS - (current_time - user_timestamps[-1]))} seconds before using this command again.",
            )

        # Check channel cooldown
        channel_timestamps = self.channel_cooldowns[channel_id]
        channel_timestamps = [
            t for t in channel_timestamps if current_time - t < self.CHANNEL_COOLDOWN
        ]
        self.channel_cooldowns[channel_id] = channel_timestamps

        if channel_timestamps:
            return (
                True,
                f"This channel is on cooldown. Please wait {int(self.CHANNEL_COOLDOWN - (current_time - channel_timestamps[-1]))} seconds.",
            )

        # Check role cooldown
        role_timestamps = self.role_cooldowns[role_id]
        role_timestamps = [
            t for t in role_timestamps if current_time - t < self.ROLE_COOLDOWN
        ]
        self.role_cooldowns[role_id] = role_timestamps

        if role_timestamps:
            return (
                True,
                f"That game role was recently pinged. Please wait {int(self.ROLE_COOLDOWN - (current_time - role_timestamps[-1]))} seconds.",
            )

        # Check daily limit
        today = time.strftime("%Y-%m-%d")
        user_daily = [
            t
            for t in self.daily_usage[user_id]
            if time.strftime("%Y-%m-%d", time.localtime(t)) == today
        ]

        if len(user_daily) >= self.MAX_PINGS_PER_DAY:
            return (
                True,
                f"You've reached your daily limit of {self.MAX_PINGS_PER_DAY} pings. Try again tomorrow!",
            )

        return False, None

    def update_cooldowns(self, user_id, channel_id, role_id):
        """Update cooldown timestamps after successful command"""
        current_time = time.time()
        self.user_cooldowns[user_id].append(current_time)
        self.channel_cooldowns[channel_id].append(current_time)
        self.role_cooldowns[role_id].append(current_time)
        self.daily_usage[user_id].append(current_time)

        # Clean up old entries
        self.cleanup_old_entries()

    def cleanup_old_entries(self):
        """Clean up old cooldown entries"""
        current_time = time.time()
        cutoff_time = current_time - 86400  # 24 hours ago

        for user_id in list(self.daily_usage.keys()):
            self.daily_usage[user_id] = [
                t for t in self.daily_usage[user_id] if t > cutoff_time
            ]
            if not self.daily_usage[user_id]:
                del self.daily_usage[user_id]

    async def send_rate_limit_alert(self, ctx, reason):
        """Send rate limit alert with proper formatting"""
        user_id = ctx.author.id

        # Check if we should send an alert (limit alerts to prevent spam)
        if self.rate_limit_alerts[user_id] < 3:  # Max 3 alerts per session
            self.rate_limit_alerts[user_id] += 1

            embed = discord.Embed(
                title="⏰ Rate Limited",
                description=reason,
                color=discord.Color.orange(),
            )
            embed.set_footer(text="Please respect cooldowns to prevent spam!")
            await ctx.send(embed=embed, delete_after=10)
        else:
            # Silent rate limit after too many attempts
            try:
                await ctx.message.delete()
            except:
                pass

    @commands.command(name="game", aliases=["g"])
    async def game_ping(self, ctx, *, game_name=None):
        """
        Ping a game role to call people to play!
        Usage: .game <game name>
        Must be in a voice channel to use this command.
        """

        # Check if user has required roles
        if not any(
            role.id in self.GAME_PING_ALLOWED_ROLES for role in ctx.author.roles
        ):
            embed = discord.Embed(
                title="❌ Permission Denied",
                description="You don't have permission to use this command!",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed, delete_after=5)
            return

        # Check if game name was provided
        if not game_name:
            embed = discord.Embed(
                title="❌ Missing Game Name",
                description="Please specify a game! Example: `.game valorant`",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed, delete_after=5)
            return

        # Check if user is in a voice channel
        if not ctx.author.voice or not ctx.author.voice.channel:
            embed = discord.Embed(
                title="❌ Not in Voice Channel",
                description="You must be in a voice channel to use this command!",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed, delete_after=5)
            return

        voice_channel = ctx.author.voice.channel

        # Normalize the game name input
        normalized_game = self.normalize_game_name(game_name)

        # Find matching game role
        role_id = None
        used_key = None

        # Handle case-insensitive matching
        game_roles_lower = {k.lower(): (k, v) for k, v in self.GAME_ROLES.items()}

        if normalized_game in game_roles_lower:
            used_key, role_id = game_roles_lower[normalized_game]
        else:
            # Try partial match
            for key_lower, (original_key, rid) in game_roles_lower.items():
                if normalized_game in key_lower or key_lower in normalized_game:
                    role_id = rid
                    used_key = original_key
                    break

        if not role_id:
            available_games = ", ".join(sorted(self.GAME_ROLES.keys()))
            embed = discord.Embed(
                title="❌ Game Not Found",
                description=f"Available games: {available_games}",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed, delete_after=10)
            return

        # Get the role object
        game_role = ctx.guild.get_role(role_id)
        if not game_role:
            embed = discord.Embed(
                title="❌ Role Error",
                description="Game role not found in server!",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed, delete_after=5)
            return

        # Check rate limits
        is_limited, limit_reason = self.is_rate_limited(
            ctx.author.id, ctx.channel.id, role_id
        )
        if is_limited:
            await self.send_rate_limit_alert(ctx, limit_reason)
            return

        # Delete the original command message
        try:
            await ctx.message.delete()
        except:
            pass  # Ignore if we can't delete

        # Create voice channel invite
        try:
            invite = await voice_channel.create_invite(
                max_age=3600,  # 1 hour
                max_uses=0,  # Unlimited uses
                reason=f"Game ping for {used_key}",
            )
        except discord.Forbidden:
            embed = discord.Embed(
                title="❌ Permission Error",
                description="I don't have permission to create invites!",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed, delete_after=5)
            return
        except Exception as e:
            embed = discord.Embed(
                title="❌ Invite Error",
                description=f"Failed to create invite: {str(e)}",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed, delete_after=5)
            return

        # Update cooldowns after successful command
        self.update_cooldowns(ctx.author.id, ctx.channel.id, role_id)

        # Create and send the game ping message (REGULAR MESSAGE, not embed)
        message = f"{game_role.mention}\n{ctx.author.mention} is calling you to play **{used_key}**!!\n{invite.url}"
        await ctx.send(message)

        # Send a small DM to user confirming usage
        try:
            dm_embed = discord.Embed(
                title="✅ Game Ping Sent",
                description=f"You pinged {game_role.name} in #{ctx.channel.name}",
                color=discord.Color.green(),
            )
            today = time.strftime("%Y-%m-%d")
            daily_used = len(
                [
                    t
                    for t in self.daily_usage[ctx.author.id]
                    if time.strftime("%Y-%m-%d", time.localtime(t)) == today
                ]
            )
            dm_embed.add_field(
                name="Remaining Daily Pings",
                value=f"{self.MAX_PINGS_PER_DAY - daily_used}",
            )
            await ctx.send(embed=dm_embed, delete_after=10)
        except:
            pass  # User might have DMs disabled

    @commands.command(name="listgames", aliases=["games"])
    async def list_games(self, ctx):
        """List all available games"""
        games_list = []
        for game, role_id in sorted(self.GAME_ROLES.items()):
            role = ctx.guild.get_role(role_id)
            role_name = role.name if role else "Unknown Role"
            games_list.append(f"• **{game}** - {role_name}")

        embed = discord.Embed(
            title="🎮 Available Games",
            description=(
                "\n".join(games_list) if games_list else "No games configured yet!"
            ),
            color=discord.Color.blue(),
        )
        embed.set_footer(text="Use .game <game> to ping players!")
        await ctx.send(embed=embed)

    @commands.command(name="cooldowns", aliases=["cd"])
    async def check_cooldowns(self, ctx):
        """Check your current cooldown status"""
        user_id = ctx.author.id

        # Check user cooldown
        current_time = time.time()
        user_timestamps = [
            t
            for t in self.user_cooldowns[user_id]
            if current_time - t < self.COOLDOWN_SECONDS
        ]

        if user_timestamps:
            user_cd = int(self.COOLDOWN_SECONDS - (current_time - user_timestamps[-1]))
        else:
            user_cd = 0

        # Check daily usage
        today = time.strftime("%Y-%m-%d")
        daily_used = len(
            [
                t
                for t in self.daily_usage[user_id]
                if time.strftime("%Y-%m-%d", time.localtime(t)) == today
            ]
        )

        embed = discord.Embed(title="⏰ Cooldown Status", color=discord.Color.blue())
        embed.add_field(
            name="Command Cooldown",
            value=f"{user_cd}s / {self.COOLDOWN_SECONDS}s",
            inline=True,
        )
        embed.add_field(
            name="Daily Pings Used",
            value=f"{daily_used} / {self.MAX_PINGS_PER_DAY}",
            inline=True,
        )

        await ctx.send(embed=embed, delete_after=10)

    @commands.command(name="resetcooldowns", aliases=["resetcd"])
    @commands.has_permissions(administrator=True)
    async def reset_cooldowns(self, ctx, user: discord.Member = None):
        """Reset cooldowns for a user or all users (Admin only)"""
        if user:
            # Reset for specific user
            self.user_cooldowns[user.id] = []
            self.daily_usage[user.id] = []
            self.rate_limit_alerts[user.id] = 0
            await ctx.send(f"✅ Reset cooldowns for {user.mention}", delete_after=5)
        else:
            # Reset for all users
            self.user_cooldowns.clear()
            self.daily_usage.clear()
            self.rate_limit_alerts.clear()
            await ctx.send("✅ Reset all cooldowns!", delete_after=5)


async def setup(bot):
    await bot.add_cog(GameCog(bot))
