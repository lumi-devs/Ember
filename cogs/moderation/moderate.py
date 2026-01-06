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

# Define your role IDs here
ROLE_IDS = {
    "MODERATOR": 1406324000736542911,  # Replace with actual Moderator role ID
    "HELPER": 1410302930485121106,     # Replace with actual Helper role ID
    "CHAT_MANAGER": 1408709182856630332,  # Replace with actual Chat Manager role ID
}

def has_role_by_id(member: discord.Member, role_id: int) -> bool:
    """Check if member has a specific role by ID."""
    return any(role.id == role_id for role in member.roles)

def can_punish(punisher: discord.Member, target: discord.Member, action: str) -> tuple[bool, str]:
    """
    Check if punisher can punish target based on role IDs.
    Returns: (can_punish, error_message)
    
    Role hierarchy (from highest to lowest authority):
    1. MODERATOR - Can mute, kick, ban
    2. HELPER - Can mute, kick (but not ban)
    3. CHAT_MANAGER - Can mute only
    """
    
    # Bot owners/admins can always punish (optional - remove if you don't want this)
    if punisher.guild_permissions.administrator:
        return True, ""
    
    # Check if punisher has any moderation role
    punisher_roles = {
        "is_moderator": has_role_by_id(punisher, ROLE_IDS["MODERATOR"]),
        "is_helper": has_role_by_id(punisher, ROLE_IDS["HELPER"]),
        "is_chat_manager": has_role_by_id(punisher, ROLE_IDS["CHAT_MANAGER"]),
    }
    
    # Determine punisher's highest role
    punisher_role_level = 0
    punisher_role_name = ""
    
    if punisher_roles["is_moderator"]:
        punisher_role_level = 3
        punisher_role_name = "Moderator"
    elif punisher_roles["is_helper"]:
        punisher_role_level = 2
        punisher_role_name = "Helper"
    elif punisher_roles["is_chat_manager"]:
        punisher_role_level = 1
        punisher_role_name = "Chat Manager"
    else:
        return False, "You don't have any moderation role (Moderator, Helper, or Chat Manager)."
    
    # Check if target has any moderation role
    target_roles = {
        "is_moderator": has_role_by_id(target, ROLE_IDS["MODERATOR"]),
        "is_helper": has_role_by_id(target, ROLE_IDS["HELPER"]),
        "is_chat_manager": has_role_by_id(target, ROLE_IDS["CHAT_MANAGER"]),
    }
    
    # Determine target's highest role
    target_role_level = 0
    target_role_name = ""
    
    if target_roles["is_moderator"]:
        target_role_level = 3
        target_role_name = "Moderator"
    elif target_roles["is_helper"]:
        target_role_level = 2
        target_role_name = "Helper"
    elif target_roles["is_chat_manager"]:
        target_role_level = 1
        target_role_name = "Chat Manager"
    
    # Rule 1: Can't punish yourself
    if punisher.id == target.id:
        return False, "You cannot punish yourself."
    
    # Rule 2: Can't punish someone with higher role level
    if target_role_level > punisher_role_level:
        return False, f"You cannot punish a {target_role_name} as a {punisher_role_name}."
    
    # Rule 3: Can't punish someone with the same role level (FIXED)
    if target_role_level == punisher_role_level and target_role_level > 0:
        return False, f"You cannot punish another {punisher_role_name}."
    
    # Rule 4: Check action permissions based on role
    if action == "mute":
        # All moderation roles can mute
        pass  # Allowed for all
    
    elif action == "kick":
        # Only Moderators and Helpers can kick
        if punisher_role_level < 2:  # Chat Managers (level 1) cannot kick
            return False, f"{punisher_role_name}s cannot kick members."
    
    elif action == "ban":
        # Only Moderators can ban
        if punisher_role_level < 3:  # Only Moderators (level 3) can ban
            return False, f"{punisher_role_name}s cannot ban members."
    
    # Rule 5: Check Discord role hierarchy for non-moderation roles
    if target_role_level == 0:  # Target has no moderation role
        # Get highest non-moderation role positions
        punisher_highest = max(
            [role for role in punisher.roles if role.id not in ROLE_IDS.values()],
            key=lambda r: r.position,
            default=None
        )
        target_highest = max(
            [role for role in target.roles if role.id not in ROLE_IDS.values()],
            key=lambda r: r.position,
            default=None
        )
        
        if punisher_highest and target_highest:
            if target_highest.position >= punisher_highest.position:
                return False, "You cannot punish someone with an equal or higher role position than you."
    
    return True, ""

class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    async def send_log(self, action: str, moderator: discord.Member, target: discord.Member, **kwargs):
        """Send moderation action to log channel (optional)."""
        # You can implement logging to a specific channel here
        # For now, we'll just print to console
        print(f"[MODLOG] {action.upper()}: {moderator} -> {target} | {kwargs}")
    
    # --- MUTE ---
    @commands.command(name="mute")
    async def mute(self, ctx, member: discord.Member, duration: str = None, *, reason: str = "No reason provided"):
        # Check role-based permissions
        can_punish_check, error_msg = can_punish(ctx.author, member, "mute")
        if not can_punish_check:
            embed = discord.Embed(title="⚠️ Action Denied", description=error_msg, color=discord.Color.red())
            return await ctx.send(embed=embed)
        
        seconds = parse_duration(duration)
        until = datetime.now(timezone.utc) + timedelta(seconds=seconds) if seconds else None
        
        try:
            await member.timeout(until, reason=f"Muted by {ctx.author} | {reason}")
        except discord.Forbidden:
            embed = discord.Embed(title="❌ Cannot Mute", description="I don't have permission to mute this user.", color=discord.Color.red())
            return await ctx.send(embed=embed)
        except discord.HTTPException as e:
            embed = discord.Embed(title="❌ Error", description=f"Failed to mute user: {e}", color=discord.Color.red())
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
        await self.send_log("mute", ctx.author, member, duration=duration, reason=reason)
    
    # --- UNMUTE ---
    @commands.command(name="unmute")
    async def unmute(self, ctx, member: discord.Member):
        # For unmute, we check if user can mute (same permission)
        can_punish_check, error_msg = can_punish(ctx.author, member, "mute")
        if not can_punish_check:
            embed = discord.Embed(title="⚠️ Action Denied", description=error_msg, color=discord.Color.red())
            return await ctx.send(embed=embed)
        
        try:
            await member.timeout(None, reason=f"Unmuted by {ctx.author}")
        except discord.Forbidden:
            embed = discord.Embed(title="❌ Cannot Unmute", description="I don't have permission to unmute this user.", color=discord.Color.red())
            return await ctx.send(embed=embed)
        except discord.HTTPException as e:
            embed = discord.Embed(title="❌ Error", description=f"Failed to unmute user: {e}", color=discord.Color.red())
            return await ctx.send(embed=embed)
        
        embed = discord.Embed(title="🔊 Member Unmuted", color=discord.Color.green())
        embed.add_field(name="User", value=member.mention, inline=True)
        embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
        embed.set_footer(text=f"User ID: {member.id}")
        
        await ctx.send(embed=embed)
        await self.send_log("unmute", ctx.author, member)
    
    # --- KICK ---
    @commands.command(name="kick")
    async def kick(self, ctx, member: discord.Member, *, reason: str = "No reason provided"):
        # Check role-based permissions
        can_punish_check, error_msg = can_punish(ctx.author, member, "kick")
        if not can_punish_check:
            embed = discord.Embed(title="⚠️ Action Denied", description=error_msg, color=discord.Color.red())
            return await ctx.send(embed=embed)
        
        try:
            await member.kick(reason=f"Kicked by {ctx.author} | {reason}")
        except discord.Forbidden:
            embed = discord.Embed(title="❌ Cannot Kick", description="I don't have permission to kick this user.", color=discord.Color.red())
            return await ctx.send(embed=embed)
        except discord.HTTPException as e:
            embed = discord.Embed(title="❌ Error", description=f"Failed to kick user: {e}", color=discord.Color.red())
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
    async def ban(self, ctx, member: discord.Member, *, reason: str = "No reason provided"):
        # Check role-based permissions
        can_punish_check, error_msg = can_punish(ctx.author, member, "ban")
        if not can_punish_check:
            embed = discord.Embed(title="⚠️ Action Denied", description=error_msg, color=discord.Color.red())
            return await ctx.send(embed=embed)
        
        try:
            await member.ban(reason=f"Banned by {ctx.author} | {reason}")
        except discord.Forbidden:
            embed = discord.Embed(title="❌ Cannot Ban", description="I don't have permission to ban this user.", color=discord.Color.red())
            return await ctx.send(embed=embed)
        except discord.HTTPException as e:
            embed = discord.Embed(title="❌ Error", description=f"Failed to ban user: {e}", color=discord.Color.red())
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
        # For unban, we check if user can ban (same permission)
        # Since we need a member object for can_punish, we'll check role directly
        author_roles = {
            "is_moderator": has_role_by_id(ctx.author, ROLE_IDS["MODERATOR"]),
            "is_helper": has_role_by_id(ctx.author, ROLE_IDS["HELPER"]),
            "is_chat_manager": has_role_by_id(ctx.author, ROLE_IDS["CHAT_MANAGER"]),
        }
        
        # Only Moderators can unban
        if not author_roles["is_moderator"]:
            highest_role = "Helper" if author_roles["is_helper"] else "Chat Manager" if author_roles["is_chat_manager"] else "No Mod Role"
            embed = discord.Embed(title="⚠️ Action Denied", 
                                description=f"{highest_role}s cannot unban members. Only Moderators can unban.", 
                                color=discord.Color.red())
            return await ctx.send(embed=embed)
        
        try:
            user = await self.bot.fetch_user(user_id)
            await ctx.guild.unban(user, reason=f"Unbanned by {ctx.author}")
        except discord.NotFound:
            embed = discord.Embed(title="❌ Not Found", description="That user is not banned.", color=discord.Color.red())
            return await ctx.send(embed=embed)
        except discord.Forbidden:
            embed = discord.Embed(title="❌ Cannot Unban", description="I don't have permission to unban this user.", color=discord.Color.red())
            return await ctx.send(embed=embed)
        except discord.HTTPException as e:
            embed = discord.Embed(title="❌ Error", description=f"Failed to unban user: {e}", color=discord.Color.red())
            return await ctx.send(embed=embed)
        
        embed = discord.Embed(title="⚖️ Member Unbanned", color=discord.Color.green())
        embed.add_field(name="User", value=user.mention, inline=True)
        embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
        embed.set_footer(text=f"User ID: {user_id}")
        
        await ctx.send(embed=embed)
        await self.send_log("unban", ctx.author, user)

async def setup(bot):
    await bot.add_cog(Moderation(bot))