import discord
from discord import Embed, Color
from datetime import datetime, timezone, timedelta
from typing import Optional

# Import your Database class or get_db function
from .database import get_db  # Adjust import path as needed

async def create_progress_embed(title: str, description: str, 
                               color: Color = Color.blue(), 
                               status: str = "Processing") -> Embed:
    """Create a progress embed"""
    IST = timezone(timedelta(hours=5, minutes=30))
    now_ist = datetime.now(IST)
    
    embed = Embed(
        title=title,
        description=description,
        color=color,
        timestamp=now_ist
    )
    embed.add_field(name="🔄 Status", value=f"```{status}```", inline=False)
    embed.set_footer(text="Role Creation System")
    
    return embed

async def create_role_creation_success_embed(interaction: discord.Interaction, role: discord.Role, fixed_role_id: int) -> Embed:
    """Create success embed for role creation"""
    IST = timezone(timedelta(hours=5, minutes=30))
    now_ist = datetime.now(IST)
    
    embed = Embed(
        title="✅ Custom Role Created Successfully!",
        color=Color.green(),
        timestamp=now_ist
    )
    
    embed.set_author(
        name=f"{interaction.user.display_name}'s New Role",
        icon_url=interaction.user.display_avatar.url
    )
    
    # Add fancy role display
    embed.add_field(
        name="🎭 Role Details",
        value=f"**Name:** `{role.name}`\n**ID:** `{role.id}`\n**Color:** `{str(role.color)}`",
        inline=False
    )
    
    embed.add_field(
        name="👤 Created For",
        value=interaction.user.mention,
        inline=True
    )
    
    embed.add_field(
        name="📅 Created",
        value=f"<t:{int(now_ist.timestamp())}:R>",
        inline=True
    )
    
    embed.add_field(
        name="🎨 Customization Options",
        value="Use `/boosterroles` to:\n• Change name\n• Edit colors\n• Add icon\n• Bestow to friends",
        inline=False
    )
    
    embed.set_footer(
        text="Thanks for boosting! ✨ | Roles remain as long as you continue to boost.",
        icon_url=interaction.guild.icon.url if interaction.guild.icon else None
    )
    
    # Add fun animated GIF
    success_gifs = [
        "https://media.giphy.com/media/26tknCqiJrBQG6DrC/giphy.gif",
        "https://media.giphy.com/media/l0MYt5jPR6QX5pnqM/giphy.gif",
        "https://media.giphy.com/media/3o7abAHdYvZdBNnGZq/giphy.gif",
        "https://media.giphy.com/media/xT5LMHxhOfscxPfIfm/giphy.gif",
        "https://media.giphy.com/media/3o7TKsQ8gTp3WqXqjq/giphy.gif"
    ]
    
    embed.set_image(url=success_gifs[now_ist.second % len(success_gifs)])
    embed.description = f"🎉 **Your custom booster role is ready!**"
    
    return embed

async def create_booster_roles_embed(interaction: discord.Interaction, role: Optional[discord.Role], 
                                    role_data: Optional[dict]) -> Embed:
    """Create the main booster roles management embed"""
    db = await get_db()  # Get database instance
    
    if role:
        # User has a role
        embed = Embed(
            title="🎭 Your Booster Role",
            description=f"Manage your custom booster role: {role.mention}",
            color=role.color
        )
        
        embed.add_field(name="Role Name", value=f"`{role.name}`", inline=True)
        embed.add_field(name="Color", value=f"`{str(role.color)}`", inline=True)
        
        if role_data:
            created_at = datetime.fromisoformat(role_data.get('created_at', datetime.now().isoformat()))
            embed.add_field(name="Created", value=f"<t:{int(created_at.timestamp())}:R>", inline=True)
            
            if 'updated_at' in role_data:
                updated_at = datetime.fromisoformat(role_data['updated_at'])
                embed.add_field(name="Last Updated", value=f"<t:{int(updated_at.timestamp())}:R>", inline=True)
        
        # Check for icon
        has_icon = bool(role.display_icon or role.unicode_emoji)
        embed.add_field(name="Has Icon", value="✅ Yes" if has_icon else "❌ No", inline=True)
        
        # Check for gradient
        has_gradient = role_data.get('color_type') == 'gradient' if role_data else False
        if has_gradient and 'secondary_colour' in role_data:
            embed.add_field(name="Color Type", value="🌈 Gradient", inline=True)
            embed.add_field(name="Secondary Color", value=f"`{role_data['secondary_colour']}`", inline=True)
        else:
            embed.add_field(name="Color Type", value="🎨 Solid", inline=True)
        
        # Add bestowed statistics (Updated for SQLite)
        bestowed_users = await db.get_bestowed_by_owner(interaction.user.id)
        if bestowed_users:
            total_bestowed = len(bestowed_users)
            accepted_bestowed = sum(1 for data in bestowed_users if data.get('accepted', False))
            pending_bestowed = total_bestowed - accepted_bestowed
            
            embed.add_field(
                name="🎁 Bestowed Statistics",
                value=(
                    f"**Total Invited:** {total_bestowed} users\n"
                    f"**✅ Accepted:** {accepted_bestowed} users\n"
                    f"**📨 Pending:** {pending_bestowed} users"
                ),
                inline=False
            )
            
            # Show recent bestowed users (max 3)
            recent_users = []
            for bestowed_data in bestowed_users[:3]:
                user_id = bestowed_data['user_id']
                user = interaction.guild.get_member(user_id)
                if user:
                    status = "✅" if bestowed_data.get('accepted', False) else "📨"
                    recent_users.append(f"{status} {user.mention}")
            
            if recent_users:
                embed.add_field(
                    name="👥 Recent Bestowed",
                    value="\n".join(recent_users),
                    inline=True
                )
        
        # Update available actions based on whether user has a role
        embed.add_field(
            name="Available Actions",
            value=(
                "**🎭 Edit Role** - Rename, recolor, or change icon\n"
                "**🎁 Bestow Role** - Share your role with others\n"
                "**🔄 Refresh** - Update this menu"
            ),
            inline=False
        )
        
        # Add bestow feature explanation
        embed.add_field(
            name="🎁 What is 'Bestow Role'?",
            value=(
                "Share your custom role with friends! They'll receive a DM invitation "
                "and can choose to accept or decline. You can manage who has your role "
                "and remove it anytime."
            ),
            inline=False
        )
        
    else:
        # User doesn't have a role
        embed = Embed(
            title="🎭 Booster Role Manager",
            description="Create and manage your custom booster role!",
            color=Color.gold()
        )
        
        embed.add_field(
            name="How It Works",
            value=(
                "1. Click **🎭 Create Role** to make your custom role\n"
                "2. Use the buttons below to customize it\n"
                "3. Share with friends using **🎁 Bestow Role**\n"
                "4. Your role will appear above other members!"
            ),
            inline=False
        )
        
        embed.add_field(
            name="Available Actions",
            value=(
                "**🎭 Create Role** - Make your custom booster role\n"
                "**🔄 Refresh** - Update this menu"
            ),
            inline=False
        )
    
    # Check if user is blacklisted
    is_banned = await db.is_user_banned(interaction.user.id)
    if is_banned:
        embed.add_field(
            name="⚠️ Account Status",
            value="❌ **Your account has been blacklisted.**\nYou cannot create or manage booster roles.",
            inline=False
        )
        embed.color = Color.red()
    
    return embed

async def create_help_embed() -> Embed:
    """Create help embed for booster role system"""
    embed = Embed(
        title="🎭 Booster Role System Help",
        description="Manage your custom booster role with these commands:",
        color=Color.blue()
    )
    
    embed.add_field(
        name="🎭 `/boosterroles`",
        value="**Button-based manager** - Full management with buttons and modals\n• Create, rename, recolor, icon, delete\n• Best for new users",
        inline=False
    )
    
    embed.add_field(
        name="Traditional Commands",
        value=(
            "**`/tascreate <name>`** - Create a custom role\n"
            "**`/tasdelete [@user] [reason]`** - Delete your role (mods can delete others)\n"
            "**`/tasblacklist <action> [user] [reason]`** - Manage blacklist (mods only)\n"
            "*Note: Color and name commands are now in `/boosterroles`*"
        ),
        inline=False
    )
    
    embed.add_field(
        name="Features",
        value=(
            "• **Gradient colors** - Set primary and secondary colors\n"
            "• **Role icons** - Use custom or unicode emojis\n"
            "• **Auto-positioning** - Roles placed above designated role\n"
            "• **Blacklist system** - Prevent abuse\n"
            "• **Database backup** - Automatic backups\n"
            "• **SQLite database** - Reliable data storage"
        ),
        inline=False
    )
    
    embed.add_field(
        name="Requirements",
        value="• Must be a server booster\n• Must not be blacklisted\n• Use designated channel (if configured)",
        inline=False
    )
    
    embed.set_footer(text="Thanks for boosting! ✨")
    return embed

async def create_bestowed_management_embed(interaction: discord.Interaction, role: discord.Role) -> Embed:
    """Create embed for managing bestowed roles"""
    db = await get_db()
    
    # Get all bestowed entries for this owner
    bestowed_entries = await db.get_bestowed_by_owner(interaction.user.id)
    
    embed = Embed(
        title="👥 Manage Bestowed Roles",
        description=f"Manage who has your role: {role.mention}",
        color=role.color
    )
    
    if not bestowed_entries:
        embed.add_field(
            name="No Bestowed Roles",
            value="You haven't bestowed your role to anyone yet.\nUse the **🎁 Bestow Role** button to share it!",
            inline=False
        )
    else:
        # Separate accepted and pending
        accepted = []
        pending = []
        
        for entry in bestowed_entries:
            user = interaction.guild.get_member(entry['user_id'])
            if user:
                if entry.get('accepted'):
                    accepted.append(user.mention)
                else:
                    pending.append(user.mention)
        
        if accepted:
            embed.add_field(
                name="✅ Accepted Roles",
                value="\n".join(accepted[:10]) + (f"\n...and {len(accepted) - 10} more" if len(accepted) > 10 else ""),
                inline=False
            )
        
        if pending:
            embed.add_field(
                name="📨 Pending Invitations",
                value="\n".join(pending[:10]) + (f"\n...and {len(pending) - 10} more" if len(pending) > 10 else ""),
                inline=False
            )
        
        # Add statistics
        embed.add_field(
            name="📊 Statistics",
            value=f"**Total:** {len(bestowed_entries)} users\n**Accepted:** {len(accepted)}\n**Pending:** {len(pending)}",
            inline=True
        )
    
    embed.add_field(
        name="How to Remove",
        value="Click a user's name below to remove your role from them.",
        inline=False
    )
    
    embed.set_footer(text=f"Use the buttons below to manage bestowed roles")
    return embed