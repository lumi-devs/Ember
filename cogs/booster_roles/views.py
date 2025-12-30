import asyncio
import discord
from discord import ui, Interaction, ButtonStyle, SelectOption, Color, Embed
from typing import Optional, Dict, Any, Set, List
from datetime import datetime, timezone, timedelta
import re
import aiohttp

# Updated imports for SQLite database
from .database import get_db  # Adjust import path as needed
from .modals import CreateRoleModal, RoleNameModal, RoleColorsModal
from .helpers import extract_emoji, download_emoji_image, delete_role_from_db, get_role_from_db

class RenounceConfirmationView(ui.View):
    """View for confirming role renouncement"""
    def __init__(self, role: discord.Role, owner_id: int):
        super().__init__(timeout=60.0)
        self.role = role
        self.owner_id = owner_id
        self.confirmed = False
    
    @ui.button(label="✅ Renounce Role", style=ButtonStyle.danger, emoji="🚫")
    async def confirm_button(self, inter: Interaction, button: ui.Button):
        await inter.response.defer(ephemeral=True)
        
        self.confirmed = True
        self.stop()
        
        # Disable buttons
        for child in self.children:
            child.disabled = True
        
        try:
            # Remove role from user
            await inter.user.remove_roles(
                self.role,
                reason=f"User renounced bestowed role"
            )
            
            # Update database
            db = await get_db()
            await db.remove_bestowed(self.owner_id, inter.user.id)
            
            # Success embed
            success_embed = Embed(
                title="✅ Role Renounced",
                description=f"You have renounced **{self.role.name}**",
                color=Color.green()
            )
            
            success_embed.add_field(
                name="What changed?",
                value=(
                    "• Role removed from your profile\n"
                    "• Database entry cleared\n"
                    "• Owner has been notified"
                ),
                inline=False
            )
            
            success_embed.add_field(
                name="Want it back?",
                value="The owner can send you a new invitation anytime.",
                inline=False
            )
            
            await inter.edit_original_response(embed=success_embed, view=self)
            
            # Notify the role owner
            try:
                owner = inter.guild.get_member(self.owner_id)
                if owner:
                    notify_embed = Embed(
                        title="🎭 Role Renounced",
                        description=f"{inter.user.mention} has renounced your role **{self.role.name}**",
                        color=Color.orange()
                    )
                    notify_embed.add_field(name="User", value=inter.user.mention, inline=True)
                    notify_embed.add_field(name="Role", value=self.role.name, inline=True)
                    notify_embed.add_field(name="Time", value=f"<t:{int(datetime.now().timestamp())}:R>", inline=True)
                    
                    notify_embed.add_field(
                        name="Note",
                        value="You can send them a new invitation if you want.",
                        inline=False
                    )
                    
                    await owner.send(embed=notify_embed)
            except:
                pass
            
        except discord.Forbidden:
            error_embed = Embed(
                title="❌ Permission Error",
                description="Bot lacks permissions to remove roles.",
                color=Color.red()
            )
            await inter.edit_original_response(embed=error_embed, view=self)
            
        except Exception as e:
            error_embed = Embed(
                title="❌ Failed to Renounce",
                description=f"Error: {str(e)[:100]}",
                color=Color.red()
            )
            await inter.edit_original_response(embed=error_embed, view=self)
    
    @ui.button(label="❌ Keep Role", style=ButtonStyle.secondary)
    async def cancel_button(self, inter: Interaction, button: ui.Button):
        self.confirmed = False
        self.stop()
        
        # Disable buttons
        for child in self.children:
            child.disabled = True
        
        embed = Embed(
            title="✅ Role Kept",
            description=f"You decided to keep **{self.role.name}**",
            color=Color.blue()
        )
        
        await inter.response.edit_message(embed=embed, view=self)
    
    async def on_timeout(self):
        """Handle view timeout"""
        if not self.confirmed:
            for child in self.children:
                child.disabled = True
            
            try:
                embed = Embed(
                    title="⏰ Decision Timed Out",
                    description="Renounce confirmation timed out. Your role is safe.",
                    color=Color.greyple()
                )
                await self.message.edit(embed=embed, view=self)
            except:
                pass


class RoleConflictView(ui.View):
    """View for resolving role conflicts"""
    def __init__(self, new_role: discord.Role, new_owner: discord.Member, 
                 current_role: discord.Role, current_owner: discord.Member):
        super().__init__(timeout=604800.0)  # 7 days
        self.new_role = new_role
        self.new_owner = new_owner
        self.current_role = current_role
        self.current_owner = current_owner
        self.decision = None
        self.bot = None  # We'll set this later
    
    @ui.button(label="🔄 Switch to New Role", style=ButtonStyle.success, emoji="🔄")
    async def switch_button(self, inter: Interaction, button: ui.Button):
        self.decision = "switch"
        self.stop()
        
        # Disable all buttons
        for child in self.children:
            child.disabled = True
        
        await inter.response.edit_message(
            content="🔄 **Switching roles...**",
            view=self
        )
        
        # Remove current role and add new role
        try:
            # Get the guild member object for the user
            guild = inter.guild
            member = guild.get_member(inter.user.id)
            if not member:
                member = await guild.fetch_member(inter.user.id)
            
            # Remove current role
            await member.remove_roles(
                self.current_role,
                reason=f"Switched to {self.new_role.name} from {self.new_owner}"
            )
            
            # Add new role
            await member.add_roles(
                self.new_role,
                reason=f"Switched from {self.current_role.name} by {member}"
            )
            
            # Update database
            db = await get_db()
            
            # Remove old bestowed entry
            await db.remove_bestowed(self.current_owner.id, inter.user.id)
            
            # Add new bestowed entry
            await db.add_bestowed(
                owner_id=self.new_owner.id,
                user_id=inter.user.id,
                role_id=self.new_role.id
            )
            await db.update_bestowed(self.new_owner.id, inter.user.id, accepted=True)
            
            # Notify both owners
            try:
                # Notify new owner
                new_owner_embed = Embed(
                    title="✅ Role Switch Accepted",
                    description=f"{member.mention} has accepted your role and switched from {self.current_role.name}!",
                    color=Color.green()
                )
                await self.new_owner.send(embed=new_owner_embed)
                
                # Notify old owner
                old_owner_embed = Embed(
                    title="🎭 Role Switch Notification",
                    description=f"{member.mention} has switched from your role **{self.current_role.name}** to {self.new_role.name} by {self.new_owner.mention}",
                    color=Color.orange()
                )
                await self.current_owner.send(embed=old_owner_embed)
            except:
                pass
            
            await inter.followup.send("✅ Successfully switched roles!", ephemeral=True)
            
        except Exception as e:
            await inter.followup.send(f"❌ Failed to switch roles: {e}", ephemeral=True)
    
    @ui.button(label="💎 Keep Current Role", style=ButtonStyle.primary, emoji="💎")
    async def keep_button(self, inter: Interaction, button: ui.Button):
        self.decision = "keep"
        self.stop()
        
        # Disable all buttons
        for child in self.children:
            child.disabled = True
        
        await inter.response.edit_message(
            content="💎 **Keeping current role...**",
            view=self
        )
        
        # Update database - remove the new invitation
        db = await get_db()
        await db.remove_bestowed(self.new_owner.id, inter.user.id)
        
        # Notify the new owner
        try:
            notify_embed = Embed(
                title="❌ Invitation Declined",
                description=f"{inter.user.mention} has chosen to keep their current role instead of accepting yours.",
                color=Color.red()
            )
            await self.new_owner.send(embed=notify_embed)
        except:
            pass
        
        await inter.followup.send("✅ You've kept your current role. The new invitation has been declined.", ephemeral=True)
    
    @ui.button(label="❌ Decline New Role", style=ButtonStyle.danger, emoji="🚫")
    async def decline_button(self, inter: Interaction, button: ui.Button):
        self.decision = "decline"
        self.stop()
        
        # Disable all buttons
        for child in self.children:
            child.disabled = True
        
        await inter.response.edit_message(
            content="❌ **Declining new role...**",
            view=self
        )
        
        # Update database - remove the invitation
        db = await get_db()
        await db.remove_bestowed(self.new_owner.id, inter.user.id)
        
        # Notify the new owner
        try:
            notify_embed = Embed(
                title="❌ Invitation Declined",
                description=f"{inter.user.mention} has declined your role invitation.",
                color=Color.red()
            )
            await self.new_owner.send(embed=notify_embed)
        except:
            pass
        
        await inter.followup.send("✅ New role invitation declined.", ephemeral=True)

class IconSetupView(ui.View):
    """View for setting up role icons"""
    def __init__(self, parent_view):
        super().__init__(timeout=60.0)
        self.parent_view = parent_view
        self.emoji_received = False
        self.emoji = None
    
    @ui.button(label="❌ Cancel", style=ButtonStyle.danger)
    async def cancel_button(self, inter: Interaction, button: ui.Button):
        if inter.user.id != self.parent_view.user.id:
            await inter.response.send_message("❌ Only the role owner can cancel.", ephemeral=True)
            return
        
        self.emoji_received = False
        self.stop()
        await inter.response.edit_message(
            content="❌ Icon setup cancelled.",
            embed=None,
            view=None
        )
    
    async def on_timeout(self):
        # Disable buttons
        for child in self.children:
            child.disabled = True
        
        try:
            await self.message.edit(
                content="⏰ Timeout! No emoji received.",
                embed=None,
                view=self
            )
        except:
            pass


class DeleteConfirmationView(ui.View):
    """View for confirming role deletion"""
    def __init__(self, parent_view, role: discord.Role, is_mod_action: bool = False, reason: Optional[str] = None):
        super().__init__(timeout=30.0)
        self.parent_view = parent_view
        self.role = role
        self.is_mod_action = is_mod_action
        self.reason = reason
        self.confirmed = False
        self.countdown = 5
        self.countdown_task = None
    
    @ui.button(label="✅ Confirm Delete", style=ButtonStyle.danger, emoji="💀")
    async def confirm_button(self, inter: Interaction, button: ui.Button):
        if inter.user.id != self.parent_view.user.id and not self.is_mod_action:
            await inter.response.send_message(
                "❌ Only the role owner can confirm deletion.",
                ephemeral=True
            )
            return
        
        self.confirmed = True
        self.stop()
        
        # Cancel countdown if it's running
        if self.countdown_task:
            self.countdown_task.cancel()
        
        # Disable all buttons
        for child in self.children:
            child.disabled = True
        
        # Update message to show deletion in progress
        await inter.response.edit_message(
            content="🗑️ **Deleting role...**",
            embed=None,
            view=self
        )
        
        # Delete the role with fun progress
        try:
            role_name = self.role.name
            role_color = self.role.color
            
            # Show progress animation
            progress_steps = [
                "💔 Disconnecting role from users...",
                "🔥 Burning role data...",
                "🧹 Cleaning up permissions...",
                "🚮 Sending role to the void...",
                "✅ Role deleted successfully!"
            ]
            
            for i, step in enumerate(progress_steps):
                progress_embed = Embed(
                    title="🗑️ Role Deletion in Progress",
                    description=f"**Step {i+1}/{len(progress_steps)}:** {step}",
                    color=Color.red(),
                    timestamp=datetime.now()
                )
                
                # Add progress bar field
                progress_bar = f"```{'▰' * (i+1)}{'▱' * (len(progress_steps)-i-1)} {(i+1)*20}%```"
                progress_embed.add_field(
                    name="Progress",
                    value=progress_bar,
                    inline=False
                )
                
                # Add role details
                progress_embed.add_field(
                    name="Role Being Deleted",
                    value=f"**Name:** `{role_name}`\n",
                    inline=True
                )
                
                await inter.edit_original_response(embed=progress_embed)
                await asyncio.sleep(0.8)
            
            # Actually delete the role
            delete_reason = f"Deleted by {'moderator' if self.is_mod_action else 'owner'} {inter.user} via /boosterroles"
            if self.reason:
                delete_reason += f" - Reason: {self.reason}"
            
            await self.role.delete(reason=delete_reason)
            
            # Find and delete role data from database
            db = await get_db()
            
            # Find which user owns this role
            role_owner_id = None
            all_roles = await db.get_all_roles()
            for role_data in all_roles:
                if role_data.get('role_id') == self.role.id:
                    role_owner_id = role_data['user_id']
                    break
            
            if role_owner_id:
                # Delete role from database
                await db.delete_role(role_owner_id)
                
                # Also remove all bestowed entries for this role
                conn = await db.get_connection()
                await conn.execute("DELETE FROM bestowed WHERE role_id = ?", (self.role.id,))
                await conn.commit()
            
            # Final success embed
            success_embed = Embed(
                title="✅ Role Successfully Deleted",
                description=f"The role **{role_name}** has been permanently deleted.",
                color=Color.red(),
                timestamp=datetime.now()
            )
            
            success_embed.add_field(
                name="Role Details",
                value=f"**Name:** `{role_name}`\n**Color:** `{role_color}`\n**Deleted:** <t:{int(datetime.now().timestamp())}:R>",
                inline=False
            )
            
            if self.is_mod_action:
                success_embed.add_field(
                    name="Action by",
                    value=f"Moderator: {inter.user.mention}",
                    inline=False
                )
                if self.reason:
                    success_embed.add_field(name="Reason", value=self.reason, inline=False)
            
            success_embed.add_field(
                name="Effects",
                value="• Role removed from Discord\n• Database entry cleared\n• All bestowed users lost the role\n• Role owner can create a new role anytime",
                inline=False
            )
            
            success_embed.add_field(
                name="What's Next?",
                value="The role owner can create a new role by clicking **🎭 Create Role** in the main menu.",
                inline=False
            )
            
            # Add a sad/funny GIF
            delete_gifs = [
                "https://media.giphy.com/media/l0MYt5jPR6QX5pnqM/giphy.gif",
                "https://media.giphy.com/media/3o7aD2vscU9WE8uVUQ/giphy.gif",
                "https://media.giphy.com/media/26ufdMZhKcIBAk8FG/giphy.gif",
                "https://media.giphy.com/media/l0MYNwK00Dx4y5D4c/giphy.gif",
                "https://media.giphy.com/media/3o7abAHdYvZdBNnGZq/giphy.gif"
            ]
            
            success_embed.set_image(url=delete_gifs[datetime.now().second % len(delete_gifs)])
            success_embed.set_footer(text="Role deletion complete")
            
            await inter.edit_original_response(embed=success_embed, view=None)
            
            # Update the parent view to show no role exists if it was the user's own role
            if self.parent_view and self.parent_view.user.id == inter.user.id:
                self.parent_view.role = None
                self.parent_view.role_data = None
                if hasattr(self.parent_view, 'update_button_states'):
                    self.parent_view.update_button_states()
                
                # Show updated menu after 5 seconds
                await asyncio.sleep(5)
                if hasattr(self.parent_view, 'cog'):
                    await self.parent_view.cog.send_booster_roles_embed(inter)
            
        except discord.Forbidden:
            error_embed = Embed(
                title="❌ Permission Error",
                description="Bot lacks permissions to delete roles.",
                color=Color.red()
            )
            await inter.edit_original_response(embed=error_embed, view=None)
            
        except Exception as e:
            error_embed = Embed(
                title="❌ Deletion Failed",
                description=f"Error: {str(e)}",
                color=Color.red()
            )
            await inter.edit_original_response(embed=error_embed, view=None)
    
    @ui.button(label="❌ Cancel", style=ButtonStyle.secondary, emoji="🚫")
    async def cancel_button(self, inter: Interaction, button: ui.Button):
        if inter.user.id != self.parent_view.user.id and not self.is_mod_action:
            await inter.response.send_message(
                "❌ Only the role owner can cancel.",
                ephemeral=True
            )
            return
        
        self.confirmed = False
        self.stop()
        
        # Cancel countdown if it's running
        if self.countdown_task:
            self.countdown_task.cancel()
        
        await inter.response.edit_message(
            content="✅ Deletion cancelled. Your role is safe!",
            embed=None,
            view=None
        )
    
    async def start_countdown(self, message):
        """Start a countdown animation on the confirm button"""
        original_label = self.confirm_button.label
        try:
            for i in range(5, 0, -1):
                self.countdown = i
                self.confirm_button.label = f"✅ Confirm Delete ({i}s)"
                await message.edit(view=self)
                await asyncio.sleep(1)
            
            # Time's up - disable the button
            self.confirm_button.disabled = True
            self.confirm_button.label = "⏰ Time's up!"
            await message.edit(view=self)
            
        except asyncio.CancelledError:
            # Countdown was cancelled
            self.confirm_button.label = original_label
            await message.edit(view=self)
    
    async def on_timeout(self):
        """Handle view timeout"""
        if not self.confirmed:
            # Cancel countdown if it's running
            if self.countdown_task:
                self.countdown_task.cancel()
            
            # Disable all buttons
            for child in self.children:
                child.disabled = True
            
            try:
                await self.message.edit(
                    content="⏰ Deletion timed out. Your role is safe!",
                    view=self
                )
            except:
                pass


class RemoveIconView(ui.View):
    """View for removing role icons"""
    def __init__(self, parent_view):
        super().__init__(timeout=30.0)
        self.parent_view = parent_view
    
    @ui.button(label="✅ Remove Icon", style=ButtonStyle.danger)
    async def confirm_button(self, inter: Interaction, button: ui.Button):
        if inter.user.id != self.parent_view.user.id:
            await inter.response.send_message("❌ Only the role owner can confirm.", ephemeral=True)
            return
        
        await inter.response.defer(ephemeral=True)
        
        try:
            # Remove icon from role
            await self.parent_view.role.edit(display_icon=None)
            
            # Update database
            db = await get_db()
            role_data = await db.get_role(inter.user.id)
            
            if role_data:
                role_data.pop('icon_type', None)
                role_data.pop('icon_set_at', None)
                role_data['updated_at'] = datetime.now(timezone(timedelta(hours=5, minutes=30))).isoformat()
                
                await db.save_role(inter.user.id, role_data)
            
            await inter.followup.send(
                "✅ Icon removed from your role!",
                ephemeral=True
            )
            
        except discord.Forbidden:
            await inter.followup.send(
                "❌ Bot lacks permissions to edit role icons.",
                ephemeral=True
            )
        except Exception as e:
            await inter.followup.send(
                f"❌ Error removing icon: {str(e)}",
                ephemeral=True
            )
    
    @ui.button(label="❌ Cancel", style=ButtonStyle.secondary)
    async def cancel_button(self, inter: Interaction, button: ui.Button):
        if inter.user.id != self.parent_view.user.id:
            await inter.response.send_message("❌ Only the role owner can cancel.", ephemeral=True)
            return
        
        await inter.response.edit_message(
            content="✅ Icon removal cancelled.",
            embed=None,
            view=None
        )


class RoleInvitationView(ui.View):
    """View for role invitation DMs"""
    def __init__(self, owner: discord.Member, recipient: discord.Member, role: discord.Role):
        super().__init__(timeout=604800.0)  # 7 day timeout
        self.owner = owner
        self.recipient = recipient
        self.role = role
        self.decision_made = False
    
    @ui.button(label="✅ Accept Role", style=ButtonStyle.success, emoji="🎭")
    async def accept_button(self, inter: Interaction, button: ui.Button):
        if inter.user.id != self.recipient.id:
            await inter.response.send_message(
                "❌ This invitation is not for you!",
                ephemeral=True
            )
            return
        
        self.decision_made = True
        self.stop()
        
        # Disable buttons
        for child in self.children:
            child.disabled = True
        
        # Update message
        await inter.response.edit_message(
            content="🎭 **Processing your acceptance...**",
            embed=None,
            view=self
        )
        
        # Add role to recipient
        try:
            await self.recipient.add_roles(
                self.role, 
                reason=f"Accepted invitation from {self.owner}"
            )
            
            # Update database (SQLite version)
            db = await get_db()
            await db.update_bestowed(self.owner.id, self.recipient.id, accepted=True)
            
            # Send confirmation to recipient
            success_embed = Embed(
                title="✅ Role Accepted!",
                description=f"You have accepted **{self.role.name}** from {self.owner.mention}",
                color=Color.green()
            )
            success_embed.add_field(name="Role", value=self.role.name, inline=True)
            success_embed.add_field(name="Bestowed By", value=self.owner.mention, inline=True)
            success_embed.add_field(name="Accepted", value=f"<t:{int(datetime.now().timestamp())}:R>", inline=True)
            
            await inter.followup.send(embed=success_embed)
            
            # Notify the owner
            try:
                owner_embed = Embed(
                    title="✅ Role Accepted",
                    description=f"{self.recipient.mention} has accepted your role invitation!",
                    color=Color.green()
                )
                owner_embed.add_field(name="Role", value=self.role.mention, inline=True)
                owner_embed.add_field(name="Accepted By", value=self.recipient.mention, inline=True)
                await self.owner.send(embed=owner_embed)
            except:
                pass
            
        except discord.Forbidden:
            await inter.followup.send(
                "❌ Bot lacks permissions to add roles.",
                ephemeral=True
            )
        except Exception as e:
            await inter.followup.send(
                f"❌ Failed to add role: {e}",
                ephemeral=True
            )
    
    @ui.button(label="❌ Decline Role", style=ButtonStyle.danger, emoji="🚫")
    async def decline_button(self, inter: Interaction, button: ui.Button):
        if inter.user.id != self.recipient.id:
            await inter.response.send_message(
                "❌ This invitation is not for you!",
                ephemeral=True
            )
            return
        
        self.decision_made = True
        self.stop()
        
        # Disable buttons
        for child in self.children:
            child.disabled = True
        
        # Update message
        await inter.response.edit_message(
            content="**Invitation Declined**",
            embed=None,
            view=self
        )
        
        # Update database (SQLite version)
        db = await get_db()
        await db.remove_bestowed(self.owner.id, self.recipient.id)
        
        # Notify the owner
        try:
            owner_embed = Embed(
                title="❌ Role Declined",
                description=f"{self.recipient.mention} has declined your role invitation.",
                color=Color.red()
            )
            await self.owner.send(embed=owner_embed)
        except:
            pass
    
    async def on_timeout(self):
        """Handle invitation timeout"""
        if not self.decision_made:
            # Update database to remove expired invitation
            db = await get_db()
            await db.remove_bestowed(self.owner.id, self.recipient.id)


class ManageRoleView(ui.View):
    """Sub-view for managing role properties"""
    def __init__(self, parent_view):
        super().__init__(timeout=300.0)
        self.parent_view = parent_view
        self.update_button_states()
    
    def update_button_states(self):
        """Update button states based on role existence"""
        has_role = self.parent_view.role is not None
        
        self.name_button.disabled = not has_role
        self.colors_button.disabled = not has_role
        self.icon_button.disabled = not has_role
        self.remove_icon_button.disabled = not has_role or not (self.parent_view.role.display_icon)
        self.delete_button.disabled = not has_role
    
    @ui.button(label="✏️ Change Name", style=ButtonStyle.secondary, emoji="📝", row=0)
    async def name_button(self, interaction: Interaction, button: ui.Button):
        if interaction.user.id != self.parent_view.user.id:
            await interaction.response.send_message("❌ This menu is not for you!", ephemeral=True)
            return
        if not self.parent_view.role:
            await interaction.response.send_message("❌ You don't have a role to rename!", ephemeral=True)
            return
        modal = RoleNameModal(self.parent_view.role)
        await interaction.response.send_modal(modal)
    
    @ui.button(label="🎨 Edit Colors", style=ButtonStyle.secondary, emoji="🌈", row=0)
    async def colors_button(self, interaction: Interaction, button: ui.Button):
        if interaction.user.id != self.parent_view.user.id:
            await interaction.response.send_message("❌ This menu is not for you!", ephemeral=True)
            return
        if not self.parent_view.role or not self.parent_view.role_data:
            await interaction.response.send_message("❌ You don't have a role to edit!", ephemeral=True)
            return
        modal = RoleColorsModal(self.parent_view.role, self.parent_view.role_data)
        await interaction.response.send_modal(modal)
    
    @ui.button(label="🖼️ Set Icon", style=ButtonStyle.secondary, emoji="🎨", row=0)
    async def icon_button(self, interaction: Interaction, button: ui.Button):
        if interaction.user.id != self.parent_view.user.id:
            await interaction.response.send_message("❌ This menu is not for you!", ephemeral=True)
            return
        if not self.parent_view.role:
            await interaction.response.send_message("❌ You don't have a role to set an icon for!", ephemeral=True)
            return
        
        # Ask user to send an emoji
        await interaction.response.send_message(
            "🎨 **Please send an emoji in chat to use as the role icon!**\n"
            "You can send any emoji (Unicode or custom Discord emoji).",
            ephemeral=True
        )
        
        # Wait for user's emoji response
        def check(m):
            return m.author.id == interaction.user.id and m.channel.id == interaction.channel_id
        
        try:
            emoji_msg = await interaction.client.wait_for('message', timeout=60.0, check=check)
            
            # Extract emoji from the message
            emoji_content = emoji_msg.content.strip()
            
            if not emoji_content:
                await interaction.followup.send("❌ No emoji found in your message!", ephemeral=True)
                return
            
            # Process and set the emoji as role icon
            await self.process_emoji_for_role(interaction, emoji_content, emoji_msg)
            
        except asyncio.TimeoutError:
            await interaction.followup.send("⏰ Timeout! You didn't send an emoji in time.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {str(e)}", ephemeral=True)

    async def process_emoji_for_role(self, interaction: Interaction, emoji_content: str, emoji_msg):
        """Process any type of emoji and set it as role icon"""
        
        try:
            # Delete the user's emoji message
            await emoji_msg.delete()
            
            # If it's a custom Discord emoji (<:name:id> or <a:name:id>)
            if emoji_content.startswith('<') and emoji_content.endswith('>'):
                # Extract emoji ID from the format
                emoji_id = emoji_content.split(':')[-1][:-1]
                
                # Download the emoji
                emoji_url = f"https://cdn.discordapp.com/emojis/{emoji_id}.png"
                async with aiohttp.ClientSession() as session:
                    async with session.get(emoji_url) as resp:
                        if resp.status == 200:
                            emoji_data = await resp.read()
                        else:
                            # Try with gif extension for animated emojis
                            emoji_url = f"https://cdn.discordapp.com/emojis/{emoji_id}.gif"
                            async with session.get(emoji_url) as gif_resp:
                                if gif_resp.status == 200:
                                    emoji_data = await gif_resp.read()
                                else:
                                    raise ValueError("Could not download emoji from Discord")
            
            else:
                # It's a Unicode emoji
                await interaction.followup.send(
                    "⚠️ Unicode emoji support requires additional setup. "
                    "Please use a custom Discord emoji for now!",
                    ephemeral=True
                )
                return
            
            # Set the role icon
            await self.parent_view.role.edit(display_icon=emoji_data)
            await interaction.followup.send(
                f"✅ Successfully set role icon to {emoji_content}!",
                ephemeral=True
            )
            
        except Exception as e:
            await interaction.followup.send(
                f"❌ Failed to set role icon: {str(e)}\n"
                f"Make sure you're using a valid custom Discord emoji!",
                ephemeral=True
            )
    
    @ui.button(label="🗑️ Remove Icon", style=ButtonStyle.secondary, emoji="❌", row=1)
    async def remove_icon_button(self, interaction: Interaction, button: ui.Button):
        if interaction.user.id != self.parent_view.user.id:
            await interaction.response.send_message("❌ This menu is not for you!", ephemeral=True)
            return
        if not self.parent_view.role:
            await interaction.response.send_message("❌ You don't have a role!", ephemeral=True)
            return
        
        # Check if role has an icon
        if not (self.parent_view.role.display_icon):
            await interaction.response.send_message(
                "ℹ️ Your role doesn't have an icon to remove.",
                ephemeral=True
            )
            return
        
        # Create confirmation
        view = RemoveIconView(self.parent_view)
        
        embed = Embed(
            title="🗑️ Remove Role Icon",
            description=f"Are you sure you want to remove the icon from {self.parent_view.role.mention}?",
            color=Color.orange()
        )
        
        if self.parent_view.role.display_icon:
            embed.add_field(name="Current Icon", value="Custom Emoji", inline=True)
        
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    
    @ui.button(label="🗑️ Delete Role", style=ButtonStyle.danger, emoji="⚠️", row=1)
    async def delete_button(self, interaction: Interaction, button: ui.Button):
        if interaction.user.id != self.parent_view.user.id:
            await interaction.response.send_message("❌ This menu is not for you!", ephemeral=True)
            return
        if not self.parent_view.role:
            await interaction.response.send_message("❌ You don't have a role to delete!", ephemeral=True)
            return
        
        # Create confirmation embed
        confirm_embed = Embed(
            title="⚠️ **CRITICAL WARNING** ⚠️",
            color=Color.red(),
            timestamp=datetime.now()
        )
        
        confirm_embed.add_field(
            name="Role to Delete",
            value=f"**Name:** `{self.parent_view.role.name}`\n**Created:** <t:{int(self.parent_view.role.created_at.timestamp()) if hasattr(self.parent_view.role, 'created_at') else int(datetime.now().timestamp())}:R>",
            inline=False
        )
        
        # Add statistics
        db = await get_db()
        bestowed_entries = await db.get_bestowed_by_owner(self.parent_view.user.id)
        bestowed_count = len(bestowed_entries) if bestowed_entries else 0
        
        confirm_embed.add_field(
            name="📊 Statistics",
            value=(
                f"• **Bestowed to:** {bestowed_count} user{'s' if bestowed_count != 1 else ''}\n"
                f"• **Color Type:** {'Gradient' if self.parent_view.role_data.get('color_type') == 'gradient' else 'Solid'}"
            ),
            inline=False
        )
        
        confirm_embed.add_field(
            name="🚨 **IRREVERSIBLE CONSEQUENCES**",
            value=(
                "```diff\n"
                "- Role will be PERMANENTLY deleted\n"
                "- All bestowed users will LOSE the role\n"
                "- Role history will be ERASED\n"
                "- No recovery possible\n"
                "```"
            ),
            inline=False
        )
        
        confirm_embed.add_field(
            name="💡 Alternatives",
            value=(
                "Consider:\n"
                "• **✏️ Renaming** your role instead\n"
                "• **🎨 Changing colors** for a fresh look\n"
                "• **👥 Removing bestowed users** if needed\n"
                "• **🔄 Starting fresh** with a new role"
            ),
            inline=False
        )
        
        confirm_embed.set_footer(
            text="Think carefully! This action cannot be undone.",
            icon_url=self.parent_view.user.display_avatar.url
        )
        
        # Add warning emoji image
        confirm_embed.set_image(url="https://media.giphy.com/media/3o7aD2vscU9WE8uVUQ/giphy.gif")
        
        # Create view and send
        confirm_view = DeleteConfirmationView(self.parent_view, self.parent_view.role)
        await interaction.response.send_message(
            embed=confirm_embed,
            view=confirm_view,
            ephemeral=True
        )
        confirm_view.message = await interaction.original_response()
        
        # Start countdown animation
        confirm_view.countdown_task = asyncio.create_task(
            confirm_view.start_countdown(confirm_view.message)
        )
    
    @ui.button(label="Back to Main", style=ButtonStyle.secondary, emoji="⬅️", row=1)
    async def back_button(self, interaction: Interaction, button: ui.Button):
        if interaction.user.id != self.parent_view.user.id:
            await interaction.response.send_message("❌ This menu is not for you!", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True)
        # Resend the embed by calling the cog's method
        await self.parent_view.cog.send_booster_roles_embed(interaction)


class ShareRoleView(ui.View):
    """Sub-view for sharing role with others"""
    def __init__(self, parent_view):
        super().__init__(timeout=300.0)
        self.parent_view = parent_view  # This is BoosterRoleView
        self.update_button_states()
    
    def update_button_states(self):
        """Update button states based on role existence"""
        has_role = self.parent_view.role is not None
        self.bestow_button.disabled = not has_role
        self.manage_bestowed_button.disabled = not has_role
    
    async def check_capacity(self, interaction: Interaction) -> tuple[bool, int, int]:
        """Check if user can send more invitations"""
        db = await get_db()
        bestowed_entries = await db.get_bestowed_by_owner(self.parent_view.user.id)
        total = len(bestowed_entries) if bestowed_entries else 0
        accepted = sum(1 for data in bestowed_entries if data.get('accepted', False)) if bestowed_entries else 0
        pending = total - accepted
        
        # Max 10 total invitations
        can_send = total < 10
        remaining = 10 - total
        
        return can_send, remaining, total

    @ui.button(label="🎁 Bestow Role", style=ButtonStyle.success, emoji="🤝", row=0)
    async def bestow_button(self, interaction: Interaction, button: ui.Button):
        if interaction.user.id != self.parent_view.user.id:
            await interaction.response.send_message(
                "❌ This menu is not for you!",
                ephemeral=True
            )
            return
        
        if not self.parent_view.role:
            await interaction.response.send_message(
                "❌ You don't have a role to bestow!",
                ephemeral=True
            )
            return
        
        # Check capacity
        can_send, remaining, current_total = await self.check_capacity(interaction)
        
        if not can_send:
            await interaction.response.send_message(
                f"❌ **Capacity Reached!**\n"
                f"You have already bestowed your role to 10 users.\n"
                f"To send more invitations, you must remove some existing bestowed users.",
                ephemeral=True
            )
            return
        
        # Create user selector with capacity info
        class UserSelectView(ui.View):
            def __init__(self, share_view, remaining):
                super().__init__(timeout=120.0)
                self.share_view = share_view
                self.remaining = remaining
                
                # Create the user select dropdown - limited to remaining capacity
                max_users = min(10, remaining)  # Don't allow more than capacity
                self.user_select = ui.UserSelect(
                    placeholder=f"Select users ({remaining} slots remaining)...",
                    min_values=1,
                    max_values=max_users,
                    custom_id="user_select"
                )
                self.add_item(self.user_select)
            
            async def on_timeout(self):
                for child in self.children:
                    child.disabled = True
                try:
                    await self.message.edit(view=self)
                except:
                    pass
        
        select_view = UserSelectView(self, remaining)
        await interaction.response.send_message(
            f"**👥 Select Users**\n"
            f"You have **{remaining} invitation slot(s)** remaining.\n"
            f"Choose up to **{remaining} user(s)** to offer your role to:",
            view=select_view,
            ephemeral=True
        )
        select_view.message = await interaction.original_response()
        
        try:
            await self.wait_for_user_selection(interaction, select_view)
        except asyncio.TimeoutError:
            await interaction.followup.send(
                "⏰ Selection timed out.",
                ephemeral=True
            )
    
    async def wait_for_user_selection(self, interaction: Interaction, select_view: ui.View):
        """Wait for user selection and process invitations"""
        try:
            # Access bot through parent_view.cog.bot
            select_interaction = await self.parent_view.cog.bot.wait_for(
                'interaction',
                timeout=120.0,
                check=lambda i: (
                    i.data.get('custom_id') == 'user_select' and
                    i.user.id == interaction.user.id
                )
            )
            
            # Get selected users
            selected_user_ids = select_interaction.data.get('values', [])
            selected_users = []
            
            for user_id in selected_user_ids:
                user = interaction.guild.get_member(int(user_id))
                if user and user.id != self.parent_view.user.id:  # Can't bestow to self
                    selected_users.append(user)
            
            if not selected_users:
                await select_interaction.response.edit_message(
                    content="❌ No valid users selected.",
                    embed=None,
                    view=None
                )
                return
            
            # Show selected users and confirmation
            user_list = "\n".join([user.mention for user in selected_users])
            embed = Embed(
                title="👥 Selected Users",
                description=f"**{len(selected_users)} user(s) selected:**\n{user_list}",
                color=Color.blue()
            )
            
            # Check for existing bestowed roles and conflicts
            db = await get_db()
            warnings = []
            can_invite_users = []
            
            for user in selected_users:
                # Check if user is blacklisted
                if await db.is_user_banned(user.id):
                    warnings.append(f"🚫 {user.mention} - Blacklisted (skipped)")
                    continue
                
                # Check if user already has this role
                if self.parent_view.role in user.roles:
                    warnings.append(f"⚠️ {user.mention} - Already has role (skipped)")
                    continue
                
                # Check if user already has a pending invitation
                existing_invitation = await db.get_bestowed(self.parent_view.user.id, user.id)
                if existing_invitation:
                    warnings.append(f"📨 {user.mention} - Already invited (skipped)")
                    continue
                
                # Check if user already has a bestowed role (accepted one)
                user_bestowed = await db.get_bestowed_by_user(user.id)
                accepted_roles = [b for b in user_bestowed if b.get('accepted')]
                
                if accepted_roles:
                    # User has existing role - they'll get conflict resolution
                    warnings.append(f"⚡ {user.mention} - Has existing role (conflict DM will be sent)")
                    can_invite_users.append(user)
                else:
                    # User can receive invitation normally
                    can_invite_users.append(user)
            
            # Add warnings to embed if any
            if warnings:
                embed.add_field(
                    name="⚠️ Notes",
                    value="\n".join(warnings),
                    inline=False
                )
            
            if not can_invite_users:
                embed.add_field(
                    name="❌ No valid users",
                    value="All selected users have issues. No invitations sent.",
                    inline=False
                )
                await select_interaction.response.edit_message(embed=embed, view=None)
                return
            
            embed.add_field(
                name="Next Step",
                value=f"Click 'Send Invitations' to send requests to {len(can_invite_users)} user(s).",
                inline=False
            )
            
            # Add send button
            class SendInvitationsView(ui.View):
                def __init__(self, share_view, selected_users, valid_users):
                    super().__init__(timeout=120.0)
                    self.share_view = share_view  # ShareRoleView instance
                    self.selected_users = selected_users  # All selected
                    self.valid_users = valid_users  # Users who can receive invites
                
                @ui.button(label="📨 Send Invitations", style=ButtonStyle.success)
                async def send_button(self, inter: Interaction, button: ui.Button):
                    if inter.user.id != interaction.user.id:
                        await inter.response.send_message(
                            "❌ Only the role owner can send invitations.",
                            ephemeral=True
                        )
                        return
                    
                    await inter.response.defer(ephemeral=True)
                    
                    # Track invitation results
                    results = {
                        "success": [],  # Normal invitations sent
                        "conflict": [],  # Conflict invitations sent
                        "failed": [],    # DMs disabled
                        "already_has": [],  # Already has role
                        "blacklisted": []   # Blacklisted users
                    }
                    
                    db = await get_db()
                    
                    for user in self.selected_users:
                        # Check if user is blacklisted
                        if await db.is_user_banned(user.id):
                            results["blacklisted"].append(user.mention)
                            continue
                        
                        # Check if user already has this role
                        if self.share_view.parent_view.role in user.roles:
                            results["already_has"].append(user.mention)
                            continue
                        
                        # Check if user already has a pending invitation
                        existing_invitation = await db.get_bestowed(self.share_view.parent_view.user.id, user.id)
                        if existing_invitation:
                            continue  # Skip, already invited
                        
                        # Check if user has existing bestowed roles
                        user_bestowed = await db.get_bestowed_by_user(user.id)
                        accepted_roles = [b for b in user_bestowed if b.get('accepted')]
                        
                        if user in self.valid_users:
                            # Check which type of invitation to send
                            if accepted_roles:
                                # Send conflict invitation
                                success = await self.share_view.parent_view.send_conflict_invitation(inter, user, accepted_roles)
                                if success:
                                    results["conflict"].append(user.mention)
                                else:
                                    results["failed"].append(user.mention)
                            else:
                                # Send normal invitation
                                success = await self.share_view.parent_view.send_normal_invitation(inter, user)
                                if success:
                                    results["success"].append(user.mention)
                                else:
                                    results["failed"].append(user.mention)
                    
                    # Create result embed
                    result_embed = Embed(
                        title="📨 Invitation Results",
                        color=Color.green() if results["success"] or results["conflict"] else Color.orange(),
                        timestamp=datetime.now()
                    )
                    
                    total_invited = len(results["success"]) + len(results["conflict"])
                    
                    if total_invited > 0:
                        result_embed.description = f"**Successfully sent {total_invited} invitation(s)**"
                        
                        if results["success"]:
                            result_embed.add_field(
                                name="✅ Normal Invitations",
                                value="\n".join(results["success"]),
                                inline=False
                            )
                        
                        if results["conflict"]:
                            result_embed.add_field(
                                name="⚡ Conflict Invitations",
                                value="\n".join(results["conflict"]) + "\n\n*These users already have roles. They'll get a comparison DM.*",
                                inline=False
                            )
                    
                    if results["failed"]:
                        result_embed.add_field(
                            name="❌ Failed (DMs disabled)",
                            value="\n".join(results["failed"]),
                            inline=False
                        )
                    
                    if results["already_has"]:
                        result_embed.add_field(
                            name="⚠️ Already Have Role",
                            value="\n".join(results["already_has"]),
                            inline=False
                        )
                    
                    if results["blacklisted"]:
                        result_embed.add_field(
                            name="🚫 Blacklisted",
                            value="\n".join(results["blacklisted"]),
                            inline=False
                        )
                    
                    result_embed.set_footer(text=f"Invitations sent by {self.share_view.parent_view.user.display_name}")
                    
                    await inter.followup.send(embed=result_embed, ephemeral=True)
                    
                    # Close the selection view
                    await select_interaction.edit_original_response(
                        content="✅ Invitations sent successfully!",
                        embed=None,
                        view=None
                    )
                
                @ui.button(label="❌ Cancel", style=ButtonStyle.secondary)
                async def cancel_button(self, inter: Interaction, button: ui.Button):
                    if inter.user.id != interaction.user.id:
                        await inter.response.send_message(
                            "❌ Only the role owner can cancel.",
                            ephemeral=True
                        )
                        return
                    
                    await inter.response.edit_message(
                        content="❌ Invitation sending cancelled.",
                        embed=None,
                        view=None
                    )
            
            send_view = SendInvitationsView(self, selected_users, can_invite_users)
            await select_interaction.response.edit_message(embed=embed, view=send_view)
            
        except asyncio.TimeoutError:
            await interaction.followup.send("⏰ Selection timed out.", ephemeral=True)
                
    @ui.button(label="👥 Manage Bestowed", style=ButtonStyle.secondary, emoji="⚙️", row=0)
    async def manage_bestowed_button(self, interaction: Interaction, button: ui.Button):
        if interaction.user.id != self.parent_view.user.id:
            await interaction.response.send_message(
                "❌ This menu is not for you!",
                ephemeral=True
            )
            return
        
        if not self.parent_view.role:
            await interaction.response.send_message(
                "❌ You don't have a role to manage!",
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=True)
        await self.parent_view.show_bestowed_management(interaction)
    
    @ui.button(label="🔙 Back to Main", style=ButtonStyle.secondary, emoji="⬅️", row=1)
    async def back_button(self, interaction: Interaction, button: ui.Button):
        if interaction.user.id != self.parent_view.user.id:
            await interaction.response.send_message("❌ This menu is not for you!", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True)
        # Resend the embed by calling the cog's method
        await self.parent_view.cog.send_booster_roles_embed(interaction)


class BoosterRoleView(ui.View):
    """Main view for booster role management"""
    def __init__(self, cog, user: discord.Member, role: Optional[discord.Role], role_data: Optional[Dict[str, Any]]):
        super().__init__(timeout=300.0)  # 5 minute timeout
        self.cog = cog
        self.user = user
        self.role = role
        self.role_data = role_data
        self.selected_users: Set[discord.Member] = set()
        self.update_button_states()
    
    def update_button_states(self):
        """Update button states based on role existence"""
        has_role = self.role is not None
        
        # Disable buttons if no role exists
        self.create_button.disabled = has_role
        self.manage_role_button.disabled = not has_role
        self.share_role_button.disabled = not has_role
        self.refresh_button.disabled = False
    
    @ui.button(label="🎭 Create Role", style=ButtonStyle.success, emoji="✨", row=0)
    async def create_button(self, interaction: Interaction, button: ui.Button):
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("❌ This menu is not for you!", ephemeral=True)
            return
        modal = CreateRoleModal(self.cog)
        await interaction.response.send_modal(modal)
    
    @ui.button(label="⚙️ Manage Role", style=ButtonStyle.primary, emoji="🔧", row=0)
    async def manage_role_button(self, interaction: Interaction, button: ui.Button):
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("❌ This menu is not for you!", ephemeral=True)
            return
        
        # Create management sub-view
        manage_view = ManageRoleView(self)
        
        # Create embed for management options
        embed = Embed(
            title="⚙️ Role Management",
            description=f"Manage properties of your role: {self.role.mention if self.role else 'No role found'}",
            color=Color.blue()
        )
        
        if self.role:
            embed.add_field(
                name="Current Role Details",
                value=(
                    f"**Name:** `{self.role.name}`\n"
                    f"**Has Icon:** {'✅ Yes' if self.role.display_icon else '❌ No'}"
                ),
                inline=False
            )
        
        embed.add_field(
            name="Available Actions",
            value=(
                "**✏️ Change Name** - Rename your role\n"
                "**🎨 Edit Colors** - Change role color\n"
                "**🖼️ Set Icon** - Add an emoji icon\n"
                "**🗑️ Remove Icon** - Remove current icon\n"
                "**⚠️ Delete Role** - Permanently delete role"
            ),
            inline=False
        )
        
        await interaction.response.send_message(embed=embed, view=manage_view, ephemeral=True)
    
    @ui.button(label="🤝 Share Role", style=ButtonStyle.primary, emoji="🎁", row=0)
    async def share_role_button(self, interaction: Interaction, button: ui.Button):
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("❌ This menu is not for you!", ephemeral=True)
            return
        
        # Create share sub-view
        share_view = ShareRoleView(self)
        
        # Create embed for sharing options
        embed = Embed(
            title="🤝 Share Your Role",
            description=f"Share your role {self.role.mention if self.role else 'No role found'} with others",
            color=Color.green()
        )
        
        if self.role:
            # Load database to get statistics
            db = await get_db()
            bestowed_entries = await db.get_bestowed_by_owner(self.user.id)
            total_bestowed = len(bestowed_entries) if bestowed_entries else 0
            accepted_count = sum(1 for data in bestowed_entries if data.get('accepted', False)) if bestowed_entries else 0
            
            embed.add_field(
                name="Current Statistics",
                value=(
                    f"**Total Bestowed:** {total_bestowed} user{'s' if total_bestowed != 1 else ''}\n"
                    f"**Accepted Invites:** {accepted_count} user{'s' if accepted_count != 1 else ''}\n"
                    f"**Pending Invites:** {total_bestowed - accepted_count} user{'s' if (total_bestowed - accepted_count) != 1 else ''}"
                ),
                inline=False
            )
        
        embed.add_field(
            name="Available Actions",
            value=(
                "**🎁 Bestow Role** - Send role invitations to users\n"
                "**👥 Manage Bestowed** - View and manage users with your role\n"
                "**🔙 Back** - Return to main menu"
            ),
            inline=False
        )
        
        await interaction.response.send_message(embed=embed, view=share_view, ephemeral=True)
    
    @ui.button(label="🔄 Refresh", style=ButtonStyle.secondary, emoji="🔄", row=0)
    async def refresh_button(self, interaction: Interaction, button: ui.Button):
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("❌ This menu is not for you!", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        # Resend the embed by calling the cog's method
        await self.cog.send_booster_roles_embed(interaction)
    
    async def send_normal_invitation(self, interaction: Interaction, recipient: discord.Member) -> bool:
        """Send normal invitation DM (when user has no existing role)"""
        db = await get_db()
        success = await db.add_bestowed(
            owner_id=self.user.id,
            user_id=recipient.id,
            role_id=self.role.id
        )
        
        if not success:
            return False
        
        # Try to send DM
        embed = Embed(
            title="🎁 Role Invitation",
            description=f"{self.user.mention} wants to bestow their custom role to you!",
            color=self.role.color
        )
        
        embed.add_field(name="Role Name", value=f"**{self.role.name}**", inline=True)
        embed.add_field(name="Color", value=f"`{str(self.role.color)}`", inline=True)
        embed.add_field(name="Bestowed By", value=self.user.mention, inline=True)
        
        if self.role.display_icon:
            embed.add_field(name="Role Icon", value="Custom Icon", inline=True)
        
        embed.add_field(
            name="What does this mean?",
            value="You'll receive the role and it will appear in your profile.\nYou can remove it anytime via `/renounce` command.",
            inline=False
        )
        
        embed.add_field(
            name="Invitation expires in",
            value="7 days",
            inline=True
        )
        
        embed.set_footer(text="Click a button below to accept or decline")
        
        view = RoleInvitationView(self.user, recipient, self.role)
        
        try:
            await recipient.send(embed=embed, view=view)
            # DM successful - mark as sent
            return True
            
        except discord.Forbidden:
            # User has DMs disabled - remove from database
            await db.remove_bestowed(self.user.id, recipient.id)
            return False
            
        except Exception as e:
            print(f"[ERROR] Failed to send normal invitation: {e}")
            return False
    
    async def send_conflict_invitation(self, interaction: Interaction, recipient: discord.Member, 
                                    current_roles: list) -> bool:
        """Send conflict invitation DM (when user already has a role)"""
        try:
            # Get current role info
            current_role_data = current_roles[0]  # User can only have one
            current_role = interaction.guild.get_role(current_role_data.get('role_id'))
            current_owner = interaction.guild.get_member(current_role_data.get('owner_id'))
            
            if not current_role:
                # Fallback to normal invitation
                return await self.send_normal_invitation(interaction, recipient)
            
            # Save to database first
            db = await get_db()
            success = await db.add_bestowed(
                owner_id=self.user.id,
                user_id=recipient.id,
                role_id=self.role.id
            )
            
            if not success:
                return False
            
            # Send conflict DM
            embed = Embed(
                title="⚠️ Role Conflict Detected",
                description=f"You already have a bestowed role from {current_owner.mention if current_owner else 'someone'}!",
                color=Color.orange()
            )
            
            # Comparison table
            embed.add_field(
                name="🆚 **Role Comparison**",
                value=(
                    "```diff\n"
                    f"+ NEW: {self.role.name} (from {self.user.display_name})\n"
                    f"- CURRENT: {current_role.name} (from {current_owner.display_name if current_owner else 'Unknown'})\n"
                    "```"
                ),
                inline=False
            )
            
            # Color comparison
            embed.add_field(
                name="🎨 **Colors**",
                value=(
                    f"**New:** `{self.role.color}`\n"
                    f"**Current:** `{current_role.color}`"
                ),
                inline=True
            )
            
            # Important notice
            embed.add_field(
                name="⚠️ **Important Notice**",
                value=(
                    "**If you choose to switch:**\n"
                    "• Your current role will be **automatically removed**\n"
                    "• The previous owner will be notified\n"
                    "• You'll only have the new role"
                ),
                inline=False
            )
            
            embed.set_footer(text="Choose carefully! You can only have one bestowed role at a time.")
            
            view = RoleConflictView(self.role, self.user, current_role, current_owner)
            
            await recipient.send(embed=embed, view=view)
            return True
            
        except discord.Forbidden:
            # User has DMs disabled
            return False
        except Exception as e:
            print(f"[ERROR] Failed to send conflict invitation: {e}")
            return False
    
    async def show_bestowed_management(self, interaction: Interaction):
        """Show bestowed users management interface"""
        db = await get_db()
        bestowed_entries = await db.get_bestowed_by_owner(self.user.id)
        
        # Filter to only show successful invitations (those in database)
        # Users with DMs disabled won't be in the database after failed send attempts
        
        embed = Embed(
            title="👥 Bestowed Users Management",
            description=f"**Role:** {self.role.mention}\n**Owner:** {self.user.mention}\n**Note:** Only users who could receive DMs are shown",
            color=self.role.color
        )
        
        if bestowed_entries:
            # Count statistics
            total = len(bestowed_entries)
            accepted = sum(1 for data in bestowed_entries if data.get('accepted', False))
            pending = total - accepted
            
            embed.add_field(
                name="📊 Statistics",
                value=(
                    f"**Total:** {total} users\n"
                    f"**Accepted:** {accepted} users\n"
                    f"**Pending:** {pending} users\n"
                    f"**Capacity:** {10 - total} users remaining"
                ),
                inline=False
            )
            
            # Show recent successful invitations (max 5)
            recent_users = []
            for entry in bestowed_entries[:5]:  # Show first 5
                user = interaction.guild.get_member(entry['user_id'])
                if user:
                    status = "✅ Accepted" if entry.get('accepted', False) else "📨 Pending"
                    invited_at = datetime.fromisoformat(entry.get('invited_at', datetime.now().isoformat()))
                    recent_users.append(f"• {user.mention} - {status} (<t:{int(invited_at.timestamp())}:R>)")
            
            if recent_users:
                embed.add_field(
                    name="👥 Recent Invitations",
                    value="\n".join(recent_users),
                    inline=False
                )
                
                if total > 5:
                    embed.set_footer(text=f"...and {total - 5} more users")
            
            # Add capacity warning if near limit
            if total >= 8:
                embed.add_field(
                    name="⚠️ Capacity Warning",
                    value=f"You have bestowed to {total}/10 users. You can only bestow to 10 users total.",
                    inline=False
                )
        else:
            embed.add_field(
                name="📭 No Active Invitations",
                value="You haven't successfully sent any invitations yet. Users with DMs disabled won't appear here.",
                inline=False
            )
        
        # Add management buttons (ALWAYS show these, even if no users)
        class ManagementView(ui.View):
            def __init__(self, parent_view):
                super().__init__(timeout=300.0)
                self.parent_view = parent_view
            
            @ui.button(label="📋 View All", style=ButtonStyle.secondary, emoji="👥")
            async def view_all_button(self, inter: Interaction, button: ui.Button):
                if inter.user.id != self.parent_view.user.id:
                    await inter.response.send_message(
                        "❌ Only the role owner can manage bestowed users.",
                        ephemeral=True
                    )
                    return
                
                await inter.response.defer(ephemeral=True)
                await self.parent_view.show_all_bestowed(inter)
            
            @ui.button(label="🗑️ Remove User", style=ButtonStyle.danger, emoji="➖")
            async def remove_user_button(self, inter: Interaction, button: ui.Button):
                if inter.user.id != self.parent_view.user.id:
                    await inter.response.send_message(
                        "❌ Only the role owner can manage bestowed users.",
                        ephemeral=True
                    )
                    return
                
                # Get current bestowed users
                db = await get_db()
                bestowed_entries = await db.get_bestowed_by_owner(self.parent_view.user.id)
                
                if not bestowed_entries:
                    await inter.response.send_message(
                        "❌ You haven't bestowed your role to anyone yet.",
                        ephemeral=True
                    )
                    return
                
                # Create user selection
                options = []
                for entry in bestowed_entries:
                    user = inter.guild.get_member(entry['user_id'])
                    if user:
                        status = "✅ Accepted" if entry.get('accepted', False) else "📨 Pending"
                        options.append(SelectOption(
                            label=f"{user.display_name}",
                            description=f"{status} | ID: {entry['user_id']}",
                            value=str(entry['user_id']),
                            emoji="👤"
                        ))
                
                if not options:
                    await inter.response.send_message(
                        "❌ No valid users to remove.",
                        ephemeral=True
                    )
                    return
                
                class RemoveSelectView(ui.View):
                    def __init__(self, parent_view, options):
                        super().__init__(timeout=120.0)
                        self.parent_view = parent_view
                        self.select = ui.Select(
                            placeholder="Select user to remove...",
                            options=options[:25],
                            custom_id="remove_select"
                        )
                        self.add_item(self.select)
                    
                    async def on_timeout(self):
                        for child in self.children:
                            child.disabled = True
                        try:
                            await self.message.edit(view=self)
                        except:
                            pass
                
                select_view = RemoveSelectView(self, options)
                await inter.response.send_message(
                    "**🗑️ Remove User**\nSelect a user to remove from your bestowed list:",
                    view=select_view,
                    ephemeral=True
                )
                select_view.message = await inter.original_response()
                
                # Wait for selection
                try:
                    # Wait for the select interaction
                    select_interaction = await self.parent_view.cog.bot.wait_for(
                        'interaction',
                        timeout=120.0,
                        check=lambda i: (
                            i.data.get('custom_id') == 'remove_select' and
                            i.user.id == inter.user.id
                        )
                    )
                    
                    user_id = int(select_interaction.data.get('values', [])[0])
                    user = inter.guild.get_member(user_id)
                    
                    if not user:
                        await select_interaction.response.edit_message(
                            content="❌ User not found in this server.",
                            embed=None,
                            view=None
                        )
                        return
                    
                    # Create confirmation view
                    confirm_view = RemoveConfirmationView(
                        user_to_remove=user,
                        parent_view=self,
                        role=self.parent_view.role
                    )
                    
                    confirm_embed = Embed(
                        title="⚠️ Confirm Removal",
                        description=f"Are you sure you want to remove {user.mention} from your bestowed list?",
                        color=Color.orange()
                    )
                    confirm_embed.add_field(name="User", value=user.mention, inline=True)
                    confirm_embed.add_field(name="Role", value=self.parent_view.role.mention, inline=True)
                    confirm_embed.add_field(
                        name="Effects",
                        value="• Role will be removed from user\n• User will be notified\n• User will be removed from your bestowed list",
                        inline=False
                    )
                    
                    await select_interaction.response.edit_message(embed=confirm_embed, view=confirm_view)
                    
                except asyncio.TimeoutError:
                    await inter.followup.send("⏰ Selection timed out.", ephemeral=True)
            
            @ui.button(label="🔄 Refresh", style=ButtonStyle.secondary, emoji="🔄")
            async def refresh_button(self, inter: Interaction, button: ui.Button):
                if inter.user.id != self.parent_view.user.id:
                    await inter.response.send_message(
                        "❌ Only the role owner can refresh.",
                        ephemeral=True
                    )
                    return
                
                await inter.response.defer(ephemeral=True)
                await self.parent_view.show_bestowed_management(inter)
        
        view = ManagementView(self)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    
    async def show_all_bestowed(self, interaction: Interaction):
        """Show detailed list of all bestowed users"""
        db = await get_db()
        bestowed_entries = await db.get_bestowed_by_owner(self.user.id)
        
        if not bestowed_entries:
            embed = Embed(
                title="📭 No Bestowed Users",
                description=f"{self.user.mention} hasn't bestowed their role to anyone yet.",
                color=Color.blue()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        
        # Create paginated embeds
        users_list = []
        for entry in bestowed_entries:
            user = interaction.guild.get_member(entry['user_id'])
            if user:
                status = "✅ Accepted" if entry.get('accepted', False) else "📨 Pending"
                invited_at = datetime.fromisoformat(entry.get('invited_at', datetime.now().isoformat()))
                
                if entry.get('accepted_at'):
                    accepted_at = datetime.fromisoformat(entry['accepted_at'])
                    accepted_text = f"Accepted: <t:{int(accepted_at.timestamp())}:R>"
                else:
                    accepted_text = "Not accepted yet"
                
                users_list.append({
                    "user": user,
                    "status": status,
                    "invited_at": invited_at,
                    "accepted_at": entry.get('accepted_at')
                })
        
        # Sort by invited date (newest first)
        users_list.sort(key=lambda x: x["invited_at"], reverse=True)
        
        # Create pages (10 users per page)
        pages = []
        for i in range(0, len(users_list), 10):
            page_users = users_list[i:i+10]
            
            embed = Embed(
                title=f"👥 Bestowed Users for {self.user.display_name}",
                description=f"**Role:** {self.role.mention}\n**Total Users:** {len(bestowed_entries)}",
                color=self.role.color,
                timestamp=datetime.now()
            )
            
            for user_data in page_users:
                user = user_data["user"]
                status = user_data["status"]
                invited_at = user_data["invited_at"]
                
                value = f"**Status:** {status}\n"
                value += f"**Invited:** <t:{int(invited_at.timestamp())}:R>\n"
                
                if user_data["accepted_at"]:
                    accepted_at = datetime.fromisoformat(user_data["accepted_at"])
                    value += f"**Accepted:** <t:{int(accepted_at.timestamp())}:R>"
                
                embed.add_field(
                    name=f"👤 {user.display_name}",
                    value=value,
                    inline=True
                )
            
            embed.set_footer(text=f"Page {i//10 + 1}/{(len(users_list)+9)//10}")
            pages.append(embed)
        
        if len(pages) == 1:
            await interaction.followup.send(embed=pages[0], ephemeral=True)
        else:
            # Simple pagination
            current_page = 0
            
            class PaginationView(ui.View):
                def __init__(self, pages):
                    super().__init__(timeout=300.0)
                    self.pages = pages
                    self.current_page = 0
                
                @ui.button(label="◀️ Previous", style=ButtonStyle.secondary, disabled=True)
                async def prev_button(self, inter: Interaction, button: ui.Button):
                    if inter.user.id != interaction.user.id:
                        await inter.response.send_message(
                            "❌ Only the command user can navigate.",
                            ephemeral=True
                        )
                        return
                    
                    self.current_page = max(0, self.current_page - 1)
                    await self.update_message(inter)
                
                @ui.button(label="Next ▶️", style=ButtonStyle.secondary)
                async def next_button(self, inter: Interaction, button: ui.Button):
                    if inter.user.id != interaction.user.id:
                        await inter.response.send_message(
                            "❌ Only the command user can navigate.",
                            ephemeral=True
                        )
                        return
                    
                    self.current_page = min(len(self.pages) - 1, self.current_page + 1)
                    await self.update_message(inter)
                
                @ui.button(label="❌ Close", style=ButtonStyle.danger)
                async def close_button(self, inter: Interaction, button: ui.Button):
                    if inter.user.id != interaction.user.id:
                        await inter.response.send_message(
                            "❌ Only the command user can close.",
                            ephemeral=True
                        )
                        return
                    
                    await inter.message.delete()
                
                async def update_message(self, interaction: Interaction):
                    # Update button states
                    self.prev_button.disabled = self.current_page == 0
                    self.next_button.disabled = self.current_page == len(self.pages) - 1
                    
                    # Update embed
                    await interaction.response.edit_message(
                        embed=self.pages[self.current_page],
                        view=self
                    )
            
            view = PaginationView(pages)
            await interaction.followup.send(embed=pages[0], view=view, ephemeral=True)


class RemoveConfirmationView(ui.View):
    """View for confirming user removal from bestowed list"""
    def __init__(self, user_to_remove: discord.Member, parent_view, role: discord.Role):
        super().__init__(timeout=60.0)
        self.user_to_remove = user_to_remove
        self.parent_view = parent_view
        self.role = role
        self.confirmed = False
    
    @ui.button(label="✅ Confirm Remove", style=ButtonStyle.danger)
    async def confirm_button(self, inter: Interaction, button: ui.Button):
        if inter.user.id != self.parent_view.parent_view.user.id:
            await inter.response.send_message(
                "❌ Only the role owner can confirm.",
                ephemeral=True
            )
            return
        
        self.confirmed = True
        self.stop()
        
        # Remove the role
        try:
            await self.user_to_remove.remove_roles(
                self.role,
                reason=f"Removed by role owner {self.parent_view.parent_view.user}"
            )
            
            # Update database (SQLite version)
            db = await get_db()
            await db.remove_bestowed(self.parent_view.parent_view.user.id, self.user_to_remove.id)
            
            # Notify the removed user
            try:
                notify_embed = Embed(
                    title="🎭 Role Removed",
                    description=f"Your bestowed role **{self.role.name}** has been removed by {self.parent_view.parent_view.user.mention}",
                    color=Color.red()
                )
                notify_embed.add_field(name="Removed At", value=f"<t:{int(datetime.now().timestamp())}:R>", inline=True)
                await self.user_to_remove.send(embed=notify_embed)
            except:
                pass
            
            await inter.response.edit_message(
                embed=Embed(
                    title="✅ User Removed",
                    description=f"Removed {self.user_to_remove.mention} from your bestowed list and removed the role.",
                    color=Color.green()
                ),
                view=None
            )
            
        except Exception as e:
            await inter.response.edit_message(
                embed=Embed(
                    title="❌ Removal Failed",
                    description=f"Failed to remove {self.user_to_remove.mention}: {e}",
                    color=Color.red()
                ),
                view=None
            )
    
    @ui.button(label="❌ Cancel", style=ButtonStyle.secondary)
    async def cancel_button(self, inter: Interaction, button: ui.Button):
        if inter.user.id != self.parent_view.parent_view.user.id:
            await inter.response.send_message(
                "❌ Only the role owner can cancel.",
                ephemeral=True
            )
            return
        
        self.confirmed = False
        self.stop()
        await inter.response.edit_message(
            embed=Embed(
                title="Removal Cancelled",
                description="No changes were made.",
                color=Color.blue()
            ),
            view=None
        )
    
    async def on_timeout(self):
        """Handle view timeout"""
        for child in self.children:
            child.disabled = True
        try:
            await self.message.edit(
                embed=Embed(
                    title="⏰ Confirmation Timed Out",
                    description="The removal confirmation timed out.",
                    color=Color.greyple()
                ),
                view=self
            )
        except:
            pass