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


# Define your role IDs here - REPLACE THESE WITH YOUR ACTUAL ROLE IDS
ROLE_IDS = {
    "MODERATOR": 1406324000736542911,  # Level 4 - Full permissions
    "HELPER": 1410302930485121106,  # Level 3 - Can mute, kick, warn
    "CHAT_MANAGER": 1408709182856630332,  # Level 2 - Can mute only
    "TRIAL_STAFF": 1421211760727429311,  # Level 1 - Can mute (limited)
}


def has_role_by_id(member: discord.Member, role_id: int) -> bool:
    """Check if member has a specific role by ID."""
    return any(role.id == role_id for role in member.roles)


def get_staff_level(member: discord.Member) -> tuple[int, str]:
    """
    Get the staff level of a member.
    Returns: (level, role_name)
    Level 4: Moderator
    Level 3: Helper
    Level 2: Chat Manager
    Level 1: Trial Staff
    Level 0: No staff role
    """
    if has_role_by_id(member, ROLE_IDS["MODERATOR"]):
        return (4, "Moderator")
    elif has_role_by_id(member, ROLE_IDS["HELPER"]):
        return (3, "Helper")
    elif has_role_by_id(member, ROLE_IDS["CHAT_MANAGER"]):
        return (2, "Chat Manager")
    elif has_role_by_id(member, ROLE_IDS["TRIAL_STAFF"]):
        return (1, "Trial Staff")
    else:
        return (0, "No Staff Role")


def can_punish(
    punisher: discord.Member, target: discord.Member, action: str
) -> tuple[bool, str]:
    """
    Check if punisher can punish target based on staff level.
    Returns: (can_punish, error_message)
    """

    # Bot owners/admins can always punish
    if punisher.guild_permissions.administrator:
        return True, ""

    # Get staff levels
    punisher_level, punisher_role = get_staff_level(punisher)
    target_level, target_role = get_staff_level(target)

    # Check if punisher has any staff role
    if punisher_level == 0:
        return (
            False,
            "You don't have any staff role (Trial Staff, Chat Manager, Helper, or Moderator).",
        )

    # Rule 1: Can't punish yourself
    if punisher.id == target.id:
        return False, "You cannot punish yourself."

    # Rule 2: Can't punish someone with higher staff level
    if target_level > punisher_level:
        return False, f"You cannot punish a {target_role} as a {punisher_role}."

    # Rule 3: Can't punish someone with the same staff level
    if target_level == punisher_level and target_level > 0:
        return False, f"You cannot punish another {punisher_role}."

    # Rule 4: Check action permissions based on staff level
    if action == "mute":
        # All staff can mute, but Trial Staff have duration limits
        if punisher_level == 1:  # Trial Staff
            # Will check duration limit in the command itself
            pass

    elif action == "warn":
        # Only Helper and Moderator can warn
        if punisher_level < 3:  # Trial Staff (1) and Chat Manager (2) cannot warn
            return False, f"{punisher_role}s cannot warn members."

    elif action == "kick":
        # Only Helper and Moderator can kick
        if punisher_level < 3:  # Trial Staff (1) and Chat Manager (2) cannot kick
            return False, f"{punisher_role}s cannot kick members."

    elif action == "ban":
        # Only Moderator can ban
        if punisher_level < 4:  # Only Moderator (4) can ban
            return (
                False,
                f"{punisher_role}s cannot ban members. Only Moderators can ban.",
            )

    # Rule 5: Check Discord role hierarchy for non-staff roles
    if target_level == 0:  # Target has no staff role
        # Get highest non-staff role positions
        staff_role_ids = list(ROLE_IDS.values())

        punisher_roles = [
            role for role in punisher.roles if role.id not in staff_role_ids
        ]
        target_roles = [role for role in target.roles if role.id not in staff_role_ids]

        punisher_highest = max(punisher_roles, key=lambda r: r.position, default=None)
        target_highest = max(target_roles, key=lambda r: r.position, default=None)

        if punisher_highest and target_highest:
            if target_highest.position >= punisher_highest.position:
                return (
                    False,
                    "You cannot punish someone with an equal or higher role position than you.",
                )

    return True, ""


class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Store warnings (in production, use a database)
        self.warnings = {}  # Format: {guild_id: {user_id: [warnings]}}

    async def send_log(
        self, action: str, moderator: discord.Member, target: discord.Member, **kwargs
    ):
        """Send moderation action to log channel (optional)."""
        print(f"[MODLOG] {action.upper()}: {moderator} -> {target} | {kwargs}")

    # --- WARN COMMAND ---
    @commands.command(name="warn")
    async def warn(
        self, ctx, member: discord.Member, *, reason: str = "No reason provided"
    ):
        """Warn a member (Helper+ only)"""
        # Check role-based permissions
        can_punish_check, error_msg = can_punish(ctx.author, member, "warn")
        if not can_punish_check:
            embed = discord.Embed(
                title="⚠️ Action Denied",
                description=error_msg,
                color=discord.Color.red(),
            )
            return await ctx.send(embed=embed)

        # Initialize warnings for this guild if not exists
        if ctx.guild.id not in self.warnings:
            self.warnings[ctx.guild.id] = {}

        if member.id not in self.warnings[ctx.guild.id]:
            self.warnings[ctx.guild.id][member.id] = []

        # Add warning
        warning = {
            "id": len(self.warnings[ctx.guild.id][member.id]) + 1,
            "moderator": ctx.author.id,
            "moderator_name": str(ctx.author),
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.warnings[ctx.guild.id][member.id].append(warning)

        # Try to DM the user
        try:
            dm_embed = discord.Embed(
                title=f"⚠️ You have been warned in {ctx.guild.name}",
                description=f"**Reason:** {reason}",
                color=discord.Color.orange(),
                timestamp=datetime.now(timezone.utc),
            )
            dm_embed.set_footer(
                text=f"Warning #{warning['id']} | Moderator: {ctx.author}"
            )
            await member.send(embed=dm_embed)
        except:
            pass  # Can't DM the user, ignore

        # Create response embed
        embed = discord.Embed(title="⚠️ Member Warned", color=discord.Color.orange())
        embed.add_field(name="User", value=member.mention, inline=True)
        embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
        embed.add_field(name="Warning #", value=warning["id"], inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.set_footer(
            text=f"User ID: {member.id} | Total warnings: {len(self.warnings[ctx.guild.id][member.id])}"
        )

        await ctx.send(embed=embed)
        await self.send_log(
            "warn", ctx.author, member, reason=reason, warning_id=warning["id"]
        )

    # --- WARNS COMMAND (Check warnings) ---
    @commands.command(name="warns", aliases=["warnings"])
    async def check_warns(self, ctx, member: discord.Member = None):
        """Check warnings for a member"""
        if member is None:
            member = ctx.author

        # For checking others, require staff role
        if member != ctx.author:
            staff_level, _ = get_staff_level(ctx.author)
            if staff_level == 0 and not ctx.author.guild_permissions.administrator:
                embed = discord.Embed(
                    title="⚠️ Access Denied",
                    description="You need a staff role to check other users' warnings.",
                    color=discord.Color.red(),
                )
                return await ctx.send(embed=embed)

        # Check if user has warnings
        if (
            ctx.guild.id not in self.warnings
            or member.id not in self.warnings[ctx.guild.id]
        ):
            embed = discord.Embed(
                title=f"📋 Warnings for {member.display_name}",
                description="This user has no warnings.",
                color=discord.Color.green(),
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            return await ctx.send(embed=embed)

        warnings = self.warnings[ctx.guild.id][member.id]

        if not warnings:
            embed = discord.Embed(
                title=f"📋 Warnings for {member.display_name}",
                description="This user has no warnings.",
                color=discord.Color.green(),
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            return await ctx.send(embed=embed)

        # Create embed with warnings
        embed = discord.Embed(
            title=f"📋 Warnings for {member.display_name}",
            description=f"**Total Warnings:** {len(warnings)}",
            color=discord.Color.orange(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_thumbnail(url=member.display_avatar.url)

        # Add last 5 warnings
        for warning in warnings[-5:]:
            mod = ctx.guild.get_member(warning["moderator"])
            mod_mention = (
                mod.mention if mod else f"Unknown ({warning['moderator_name']})"
            )
            timestamp = datetime.fromisoformat(warning["timestamp"]).strftime(
                "%Y-%m-%d %H:%M UTC"
            )

            embed.add_field(
                name=f"Warning #{warning['id']} - {timestamp}",
                value=f"**Moderator:** {mod_mention}\n**Reason:** {warning['reason']}",
                inline=False,
            )

        if len(warnings) > 5:
            embed.set_footer(text=f"Showing last 5 of {len(warnings)} warnings")

        await ctx.send(embed=embed)

    # --- CLEARWARN COMMAND ---
    @commands.command(name="clearwarn", aliases=["delwarn", "removewarn"])
    async def clear_warn(self, ctx, member: discord.Member, warning_id: int):
        """Remove a specific warning from a member (Helper+ only)"""
        # Check permissions (need Helper or higher to remove warnings)
        staff_level, staff_role = get_staff_level(ctx.author)
        if (
            staff_level < 3 and not ctx.author.guild_permissions.administrator
        ):  # Need Helper (3) or higher
            embed = discord.Embed(
                title="⚠️ Action Denied",
                description=f"{staff_role}s cannot remove warnings. Only Helpers and Moderators can.",
                color=discord.Color.red(),
            )
            return await ctx.send(embed=embed)

        # Check if user has warnings
        if (
            ctx.guild.id not in self.warnings
            or member.id not in self.warnings[ctx.guild.id]
        ):
            embed = discord.Embed(
                title="❌ Error",
                description="This user has no warnings.",
                color=discord.Color.red(),
            )
            return await ctx.send(embed=embed)

        warnings = self.warnings[ctx.guild.id][member.id]

        # Find the warning
        warning_to_remove = None
        for warning in warnings:
            if warning["id"] == warning_id:
                warning_to_remove = warning
                break

        if not warning_to_remove:
            embed = discord.Embed(
                title="❌ Error",
                description=f"Warning #{warning_id} not found for this user.",
                color=discord.Color.red(),
            )
            return await ctx.send(embed=embed)

        # Remove the warning
        self.warnings[ctx.guild.id][member.id].remove(warning_to_remove)

        # Re-number remaining warnings
        for i, warning in enumerate(self.warnings[ctx.guild.id][member.id], 1):
            warning["id"] = i

        embed = discord.Embed(title="✅ Warning Removed", color=discord.Color.green())
        embed.add_field(name="User", value=member.mention, inline=True)
        embed.add_field(name="Warning #", value=warning_id, inline=True)
        embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
        embed.add_field(
            name="Original Reason", value=warning_to_remove["reason"], inline=False
        )

        await ctx.send(embed=embed)
        await self.send_log("clearwarn", ctx.author, member, warning_id=warning_id)

    # --- MUTE ---
    @commands.command(name="mute")
    async def mute(
        self,
        ctx,
        member: discord.Member,
        duration: str = None,
        *,
        reason: str = "No reason provided",
    ):
        """Mute a member (all staff can mute)"""
        # Check role-based permissions
        can_punish_check, error_msg = can_punish(ctx.author, member, "mute")
        if not can_punish_check:
            embed = discord.Embed(
                title="⚠️ Action Denied",
                description=error_msg,
                color=discord.Color.red(),
            )
            return await ctx.send(embed=embed)

        seconds = parse_duration(duration)
        until = (
            datetime.now(timezone.utc) + timedelta(seconds=seconds) if seconds else None
        )

        # Check duration limits for Trial Staff
        staff_level, staff_role = get_staff_level(ctx.author)
        if staff_level == 1:  # Trial Staff
            if seconds and seconds > 3600:  # 1 hour limit for trial staff
                embed = discord.Embed(
                    title="⚠️ Duration Limit",
                    description="Trial Staff can only mute for up to 1 hour.",
                    color=discord.Color.red(),
                )
                return await ctx.send(embed=embed)
        elif staff_level == 2:  # Chat Manager
            if seconds and seconds > 43200:  # 12 hour limit for chat managers
                embed = discord.Embed(
                    title="⚠️ Duration Limit",
                    description="Chat Managers can only mute for up to 12 hours.",
                    color=discord.Color.red(),
                )
                return await ctx.send(embed=embed)

        try:
            await member.timeout(until, reason=f"Muted by {ctx.author} | {reason}")
        except discord.Forbidden:
            embed = discord.Embed(
                title="❌ Cannot Mute",
                description="I don't have permission to mute this user.",
                color=discord.Color.red(),
            )
            return await ctx.send(embed=embed)
        except discord.HTTPException as e:
            embed = discord.Embed(
                title="❌ Error",
                description=f"Failed to mute user: {e}",
                color=discord.Color.red(),
            )
            return await ctx.send(embed=embed)

        # Create embed response
        embed = discord.Embed(title="🔇 Member Muted", color=discord.Color.orange())
        embed.add_field(name="User", value=member.mention, inline=True)
        embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
        if duration:
            embed.add_field(name="Duration", value=duration, inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.set_footer(text=f"User ID: {member.id}")

        await ctx.send(embed=embed)
        await self.send_log(
            "mute", ctx.author, member, duration=duration, reason=reason
        )

    # --- UNMUTE ---
    @commands.command(name="unmute")
    async def unmute(self, ctx, member: discord.Member):
        """Unmute a member (all staff can unmute)"""
        # Check role-based permissions
        can_punish_check, error_msg = can_punish(ctx.author, member, "mute")
        if not can_punish_check:
            embed = discord.Embed(
                title="⚠️ Action Denied",
                description=error_msg,
                color=discord.Color.red(),
            )
            return await ctx.send(embed=embed)

        try:
            await member.timeout(None, reason=f"Unmuted by {ctx.author}")
        except discord.Forbidden:
            embed = discord.Embed(
                title="❌ Cannot Unmute",
                description="I don't have permission to unmute this user.",
                color=discord.Color.red(),
            )
            return await ctx.send(embed=embed)
        except discord.HTTPException as e:
            embed = discord.Embed(
                title="❌ Error",
                description=f"Failed to unmute user: {e}",
                color=discord.Color.red(),
            )
            return await ctx.send(embed=embed)

        embed = discord.Embed(title="🔊 Member Unmuted", color=discord.Color.green())
        embed.add_field(name="User", value=member.mention, inline=True)
        embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
        embed.set_footer(text=f"User ID: {member.id}")

        await ctx.send(embed=embed)
        await self.send_log("unmute", ctx.author, member)

    # --- KICK ---
    @commands.command(name="kick")
    async def kick(
        self, ctx, member: discord.Member, *, reason: str = "No reason provided"
    ):
        """Kick a member (Helper+ only)"""
        # Check role-based permissions
        can_punish_check, error_msg = can_punish(ctx.author, member, "kick")
        if not can_punish_check:
            embed = discord.Embed(
                title="⚠️ Action Denied",
                description=error_msg,
                color=discord.Color.red(),
            )
            return await ctx.send(embed=embed)

        try:
            await member.kick(reason=f"Kicked by {ctx.author} | {reason}")
        except discord.Forbidden:
            embed = discord.Embed(
                title="❌ Cannot Kick",
                description="I don't have permission to kick this user.",
                color=discord.Color.red(),
            )
            return await ctx.send(embed=embed)
        except discord.HTTPException as e:
            embed = discord.Embed(
                title="❌ Error",
                description=f"Failed to kick user: {e}",
                color=discord.Color.red(),
            )
            return await ctx.send(embed=embed)

        embed = discord.Embed(title="👢 Member Kicked", color=discord.Color.red())
        embed.add_field(name="User", value=member.mention, inline=True)
        embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.set_footer(text=f"User ID: {member.id}")

        await ctx.send(embed=embed)
        await self.send_log("kick", ctx.author, member, reason=reason)

    # --- BAN ---
    @commands.command(name="ban")
    async def ban(
        self, ctx, member: discord.Member, *, reason: str = "No reason provided"
    ):
        """Ban a member (Moderator only)"""
        # Check role-based permissions
        can_punish_check, error_msg = can_punish(ctx.author, member, "ban")
        if not can_punish_check:
            embed = discord.Embed(
                title="⚠️ Action Denied",
                description=error_msg,
                color=discord.Color.red(),
            )
            return await ctx.send(embed=embed)

        try:
            await member.ban(reason=f"Banned by {ctx.author} | {reason}")
        except discord.Forbidden:
            embed = discord.Embed(
                title="❌ Cannot Ban",
                description="I don't have permission to ban this user.",
                color=discord.Color.red(),
            )
            return await ctx.send(embed=embed)
        except discord.HTTPException as e:
            embed = discord.Embed(
                title="❌ Error",
                description=f"Failed to ban user: {e}",
                color=discord.Color.red(),
            )
            return await ctx.send(embed=embed)

        embed = discord.Embed(title="🔨 Member Banned", color=discord.Color.dark_red())
        embed.add_field(name="User", value=member.mention, inline=True)
        embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.set_footer(text=f"User ID: {member.id}")

        await ctx.send(embed=embed)
        await self.send_log("ban", ctx.author, member, reason=reason)

    # --- UNBAN ---
    @commands.command(name="unban")
    async def unban(self, ctx, user_id: int):
        """Unban a user (Moderator only)"""
        # Check permissions (only Moderator can unban)
        staff_level, staff_role = get_staff_level(ctx.author)

        if staff_level < 4 and not ctx.author.guild_permissions.administrator:
            embed = discord.Embed(
                title="⚠️ Action Denied",
                description=f"{staff_role}s cannot unban members. Only Moderators can unban.",
                color=discord.Color.red(),
            )
            return await ctx.send(embed=embed)

        try:
            user = await self.bot.fetch_user(user_id)
            await ctx.guild.unban(user, reason=f"Unbanned by {ctx.author}")
        except discord.NotFound:
            embed = discord.Embed(
                title="❌ Not Found",
                description="That user is not banned.",
                color=discord.Color.red(),
            )
            return await ctx.send(embed=embed)
        except discord.Forbidden:
            embed = discord.Embed(
                title="❌ Cannot Unban",
                description="I don't have permission to unban this user.",
                color=discord.Color.red(),
            )
            return await ctx.send(embed=embed)
        except discord.HTTPException as e:
            embed = discord.Embed(
                title="❌ Error",
                description=f"Failed to unban user: {e}",
                color=discord.Color.red(),
            )
            return await ctx.send(embed=embed)

        embed = discord.Embed(title="⚖️ Member Unbanned", color=discord.Color.green())
        embed.add_field(name="User", value=user.mention, inline=True)
        embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
        embed.set_footer(text=f"User ID: {user_id}")

        await ctx.send(embed=embed)
        await self.send_log("unban", ctx.author, user)

    # --- STAFF HELP COMMAND (Show permissions) ---
    @commands.command(name="staffhelp", aliases=["shelp", "permissions"])
    async def staff_help(self, ctx):
        """Show what each staff role can do"""
        embed = discord.Embed(
            title="🛡️ Staff Role Permissions",
            description="Here's what each staff role can do:",
            color=discord.Color.blue(),
        )

        embed.add_field(
            name="👑 **Moderator** (Level 4)",
            value="✅ Mute (no limit)\n✅ Kick\n✅ Ban\n✅ Warn\n✅ Unban\n✅ Clear warnings\n✅ Full access",
            inline=True,
        )

        embed.add_field(
            name="🤝 **Helper** (Level 3)",
            value="✅ Mute (no limit)\n✅ Kick\n✅ Warn\n✅ Clear warnings\n❌ Cannot ban",
            inline=True,
        )

        embed.add_field(
            name="💬 **Chat Manager** (Level 2)",
            value="✅ Mute (up to 12h)\n❌ Cannot kick\n❌ Cannot ban\n❌ Cannot warn",
            inline=True,
        )

        embed.add_field(
            name="🔰 **Trial Staff** (Level 1)",
            value="✅ Mute (up to 1h)\n❌ Cannot kick\n❌ Cannot ban\n❌ Cannot warn",
            inline=True,
        )

        embed.set_footer(text="Use .warn, .mute, .kick, .ban commands accordingly")

        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Moderation(bot))
