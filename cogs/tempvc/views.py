import discord
from discord.ui import Button, View, Select, UserSelect
from .modals import RenameModal, TransferModal  # removed LimitModal
from .config import JOIN_CHANNELS, DB_PATH
import aiosqlite

class ControlPanel(View):
    """Interactive control panel for voice channels"""
    
    def __init__(self, cog, channel_id, owner_id):
        super().__init__(timeout=None)
        self.cog = cog
        self.channel_id = channel_id
        self.owner_id = owner_id
    
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Only allow VC owner to use the controls"""
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "❌ Only the VC owner can use these controls!", 
                ephemeral=True
            )
            return False
        return True

    async def update_database(self, **kwargs):
        """Update database with new settings"""
        async with aiosqlite.connect(DB_PATH) as db:
            set_clause = ", ".join([f"{key} = ?" for key in kwargs.keys()])
            values = list(kwargs.values()) + [self.owner_id]
            await db.execute(f"UPDATE voiceChannel SET {set_clause} WHERE userID = ?", values)
            await db.commit()

class BasicControlsView(ControlPanel):
    """Refined VC controls with proper logic"""
    
    def __init__(self, cog, channel_id, owner_id):
        super().__init__(cog, channel_id, owner_id)
        
        # Row 1: Basic Settings
        self.add_item(NameButton())
        self.add_item(PrivacyButton())  # removed LimitButton()
        
        # Row 2: Member Management
        self.add_item(MembersButton())
        self.add_item(RegionButton())
        
        # Row 3: Advanced & Actions
        self.add_item(TransferButton())
        self.add_item(ClaimButton())
        self.add_item(DeleteButton())

class NameButton(Button):
    def __init__(self):
        super().__init__(label="👤 Name", style=discord.ButtonStyle.secondary, custom_id="name", row=0)
    
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        channel = interaction.guild.get_channel(view.channel_id)
        if not channel:
            await interaction.response.send_message("❌ Channel not found!", ephemeral=True)
            return
        
        modal = RenameModal(view.cog, channel)
        await interaction.response.send_modal(modal)

# completely removed LimitButton class

class PrivacyButton(Button):
    def __init__(self):
        super().__init__(label="🔐 Privacy", style=discord.ButtonStyle.secondary, custom_id="privacy", row=0)
    
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        channel = interaction.guild.get_channel(view.channel_id)
        if not channel:
            await interaction.response.send_message("❌ Channel not found!", ephemeral=True)
            return
        
        privacy_view = PrivacyView(view.cog, view.channel_id, view.owner_id, channel)
        await interaction.response.send_message(view=privacy_view, ephemeral=True)

class PrivacyView(ControlPanel):
    """Privacy controls - only basic channel settings"""
    
    def __init__(self, cog, channel_id, owner_id, channel):
        super().__init__(cog, channel_id, owner_id)
        self.channel = channel
        
        current_overwrites = channel.overwrites_for(channel.guild.default_role)
        is_locked = current_overwrites.connect is False
        is_hidden = current_overwrites.view_channel is False
        
        # Row 0: Lock/Unlock
        self.add_item(LockButtonRow(is_locked))
        self.add_item(UnlockButtonRow(is_locked))
        
        # Row 1: Hide/Unhide
        self.add_item(HideButtonRow(is_hidden))
        self.add_item(UnhideButtonRow(is_hidden))

class LockButtonRow(Button):
    def __init__(self, is_locked):
        super().__init__(label="🔒 Lock", style=discord.ButtonStyle.secondary, custom_id="lock_row", row=0, disabled=is_locked)
    
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        current_overwrites = view.channel.overwrites_for(interaction.guild.default_role)
        await view.channel.set_permissions(
            interaction.guild.default_role, 
            connect=False,
            view_channel=current_overwrites.view_channel
        )
        await view.update_database(locked=True)
        
        new_view = PrivacyView(view.cog, view.channel_id, view.owner_id, view.channel)
        await interaction.response.edit_message(view=new_view)
        await interaction.followup.send("🔒 Channel locked!", ephemeral=True)

class UnlockButtonRow(Button):
    def __init__(self, is_locked):
        super().__init__(label="🔓 Unlock", style=discord.ButtonStyle.secondary, custom_id="unlock_row", row=0, disabled=not is_locked)
    
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        current_overwrites = view.channel.overwrites_for(interaction.guild.default_role)
        await view.channel.set_permissions(
            interaction.guild.default_role, 
            connect=True,
            view_channel=current_overwrites.view_channel
        )
        await view.update_database(locked=False)
        
        new_view = PrivacyView(view.cog, view.channel_id, view.owner_id, view.channel)
        await interaction.response.edit_message(view=new_view)
        await interaction.followup.send("🔓 Channel unlocked!", ephemeral=True)

class HideButtonRow(Button):
    def __init__(self, is_hidden):
        super().__init__(label="🕵️ Hide", style=discord.ButtonStyle.secondary, custom_id="hide_row", row=1, disabled=is_hidden)
    
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        current_overwrites = view.channel.overwrites_for(interaction.guild.default_role)
        await view.channel.set_permissions(
            interaction.guild.default_role, 
            view_channel=False,
            connect=current_overwrites.connect
        )
        await view.update_database(hidden=True)
        
        new_view = PrivacyView(view.cog, view.channel_id, view.owner_id, view.channel)
        await interaction.response.edit_message(view=new_view)
        await interaction.followup.send("🕵️ Channel hidden!", ephemeral=True)

class UnhideButtonRow(Button):
    def __init__(self, is_hidden):
        super().__init__(label="👀 Unhide", style=discord.ButtonStyle.secondary, custom_id="unhide_row", row=1, disabled=not is_hidden)
    
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        current_overwrites = view.channel.overwrites_for(interaction.guild.default_role)
        await view.channel.set_permissions(
            interaction.guild.default_role, 
            view_channel=True,
            connect=current_overwrites.connect
        )
        await view.update_database(hidden=False)
        
        new_view = PrivacyView(view.cog, view.channel_id, view.owner_id, view.channel)
        await interaction.response.edit_message(view=new_view)
        await interaction.followup.send("👀 Channel now visible!", ephemeral=True)

class MembersButton(Button):
    def __init__(self):
        super().__init__(label="👥 Members", style=discord.ButtonStyle.secondary, custom_id="members", row=1)
    
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        channel = interaction.guild.get_channel(view.channel_id)
        if not channel:
            await interaction.response.send_message("❌ Channel not found!", ephemeral=True)
            return
        
        members_view = MembersMainView(view.cog, view.channel_id, view.owner_id, channel)
        await interaction.response.send_message(view=members_view, ephemeral=True)

class MembersMainView(ControlPanel):
    """Main member management menu"""
    
    def __init__(self, cog, channel_id, owner_id, channel):
        super().__init__(cog, channel_id, owner_id)
        self.channel = channel
        
        # Row 0: Grant Permissions
        self.add_item(GrantAccessButton())
        self.add_item(RevokeAccessButton())
        
        # Row 1: Moderate
        self.add_item(KickButton())
        self.add_item(BlockButton())
        self.add_item(UnblockButton())  # NEW: Add Unblock button
        
        # Row 2: View Status
        self.add_item(ViewAccessListButton())
        self.add_item(ViewBlockedButton())

class GrantAccessButton(Button):
    def __init__(self):
        super().__init__(label="✅ Grant Access", style=discord.ButtonStyle.secondary, custom_id="grant_access", row=0)
    
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        # Show users without explicit ALLOW permissions (they might have deny or no permissions)
        users_without_explicit_access = []
        for member in view.channel.guild.members:
            if member.id != view.owner_id and not member.bot:
                overwrite = view.channel.overwrites_for(member)
                # Check if user has NO explicit ALLOW permissions
                has_no_explicit_access = not any([
                    overwrite.connect is True, 
                    overwrite.view_channel is True,
                    overwrite.speak is True, 
                    overwrite.stream is True
                ])
                if has_no_explicit_access:
                    users_without_explicit_access.append(member)
        
        if users_without_explicit_access:
            access_view = UserAccessView(
                view.cog, view.channel_id, view.owner_id, view.channel, 
                users_without_explicit_access, "grant", "Select users to grant access..."
            )
            await interaction.response.send_message(view=access_view, ephemeral=True)
        else:
            await interaction.response.send_message("✅ All users already have explicit access permissions!", ephemeral=True)

class RevokeAccessButton(Button):
    def __init__(self):
        super().__init__(label="❌ Revoke Access", style=discord.ButtonStyle.secondary, custom_id="revoke_access", row=0)
    
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        # Get users with explicit ALLOW permissions
        users_with_access = []
        for member in view.channel.guild.members:
            if member.id != view.owner_id and not member.bot:
                overwrite = view.channel.overwrites_for(member)
                if any([overwrite.connect is True, overwrite.view_channel is True]):
                    users_with_access.append(member)
        
        if users_with_access:
            # Use CUSTOM DROPDOWN only for revoke access
            custom_view = CustomUserDropdownView(
                view.cog, view.channel_id, view.owner_id, view.channel,
                users_with_access, "revoke", "Select users to revoke access..."
            )
            await interaction.response.send_message(view=custom_view, ephemeral=True)
        else:
            await interaction.response.send_message("❌ No users have special access permissions!", ephemeral=True)

# NEW: Unblock Button
class UnblockButton(Button):
    def __init__(self):
        super().__init__(label="✅ Unblock", style=discord.ButtonStyle.secondary, custom_id="unblock", row=1)
    
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        # Get users with explicit DENY permissions (blocked users)
        blocked_users = []
        for member in view.channel.guild.members:
            if member.id != view.owner_id and not member.bot:
                overwrite = view.channel.overwrites_for(member)
                # Check if user has explicit DENY permissions
                if any([overwrite.connect is False, overwrite.view_channel is False]):
                    blocked_users.append(member)
        
        if blocked_users:
            # Use CUSTOM DROPDOWN for unblock (similar to revoke access)
            unblock_view = CustomUserDropdownView(
                view.cog, view.channel_id, view.owner_id, view.channel,
                blocked_users, "unblock", "Select users to unblock..."
            )
            await interaction.response.send_message(view=unblock_view, ephemeral=True)
        else:
            await interaction.response.send_message("✅ No users are currently blocked!", ephemeral=True)

# ========== CUSTOM DROPDOWN (FOR REVOKE ACCESS AND UNBLOCK) ==========

class CustomUserDropdownView(ControlPanel):
    """Custom dropdown view for revoke access and unblock"""
    
    def __init__(self, cog, channel_id, owner_id, channel, users, action_type, placeholder):
        super().__init__(cog, channel_id, owner_id)
        self.channel = channel
        self.users = users
        self.action_type = action_type
        
        if users:
            # Create custom options for the dropdown
            options = []
            for member in users:
                # Get their specific permissions
                overwrite = channel.overwrites_for(member)
                
                if action_type == "revoke":
                    permissions = []
                    if overwrite.connect is True:
                        permissions.append("Join")
                    if overwrite.view_channel is True:
                        permissions.append("View")
                    if overwrite.speak is True:
                        permissions.append("Speak")
                    if overwrite.stream is True:
                        permissions.append("Stream")
                    
                    permission_text = f"({', '.join(permissions)})" if permissions else "(No specific perms)"
                    
                elif action_type == "unblock":
                    deny_permissions = []
                    if overwrite.connect is False:
                        deny_permissions.append("Join")
                    if overwrite.view_channel is False:
                        deny_permissions.append("View")
                    if overwrite.speak is False:
                        deny_permissions.append("Speak")
                    if overwrite.stream is False:
                        deny_permissions.append("Stream")
                    
                    permission_text = f"(Blocked: {', '.join(deny_permissions)})" if deny_permissions else "(Blocked)"
                
                options.append(
                    discord.SelectOption(
                        label=member.display_name,
                        value=str(member.id),
                        description=permission_text
                    )
                )
            
            self.add_item(CustomUserDropdown(options, action_type, placeholder))
        else:
            # Add disabled select if no users
            select = discord.ui.Select(
                placeholder="No users available",
                options=[discord.SelectOption(label="No users available", value="none")],
                disabled=True,
                row=0
            )
            self.add_item(select)

class CustomUserDropdown(discord.ui.Select):
    def __init__(self, options, action_type, placeholder):
        super().__init__(
            placeholder=placeholder,
            options=options,
            min_values=1,
            max_values=len(options),  # Allow selecting all
            row=0
        )
        self.action_type = action_type
    
    async def callback(self, interaction: discord.Interaction):
        successful = []
        for user_id in self.values:
            try:
                member = interaction.guild.get_member(int(user_id))
                if member:
                    if self.action_type == "revoke":
                        # Remove ALL explicit permissions
                        await self.view.channel.set_permissions(member, overwrite=None)
                        successful.append(member.display_name)
                    
                    elif self.action_type == "unblock":
                        # Remove ALL explicit permissions (including deny)
                        await self.view.channel.set_permissions(member, overwrite=None)
                        successful.append(member.display_name)
                        
            except Exception as e:
                print(f"Error in {self.action_type} action: {e}")
        
        if successful:
            action_text = {
                "revoke": "❌ Revoked access from", 
                "unblock": "✅ Unblocked"
            }
            await interaction.response.send_message(
                f"{action_text[self.action_type]}: {', '.join(successful)}", 
                ephemeral=True
            )
        else:
            await interaction.response.send_message(f"❌ Failed to {self.action_type}!", ephemeral=True)

# Keep the other views using regular UserSelect
class UserAccessView(ControlPanel):
    """Regular UserSelect view for other actions"""
    
    def __init__(self, cog, channel_id, owner_id, channel, users, action_type, placeholder):
        super().__init__(cog, channel_id, owner_id)
        self.channel = channel
        self.action_type = action_type
        
        if users:
            self.add_item(UserActionSelect(users, action_type, placeholder))
        else:
            select = UserSelect(placeholder="No users available", disabled=True, row=0)
            self.add_item(select)

class UserActionSelect(UserSelect):
    def __init__(self, users, action_type, placeholder):
        super().__init__(
            placeholder=placeholder,
            min_values=1,
            max_values=min(25, len(users)),
            row=0
        )
        self.action_type = action_type
    
    async def callback(self, interaction: discord.Interaction):
        successful = []
        for member in self.values:
            try:
                if self.action_type == "grant":
                    # Grant access permissions
                    await self.view.channel.set_permissions(
                        member, 
                        connect=True, 
                        view_channel=True
                    )
                    successful.append(member.display_name)
                
                elif self.action_type == "kick":
                    # Kick from VC
                    await member.move_to(None)
                    successful.append(member.display_name)
                
                elif self.action_type == "block":
                    # Kick if in VC, then block
                    if member in self.view.channel.members:
                        await member.move_to(None)
                    # Set explicit deny permissions
                    await self.view.channel.set_permissions(
                        member, 
                        connect=False, 
                        view_channel=False
                    )
                    successful.append(member.display_name)
                    
            except Exception as e:
                print(f"Error in user action: {e}")
        
        if successful:
            action_text = {
                "grant": "granted access to", 
                "kick": "kicked", 
                "block": "blocked"
            }
            await interaction.response.send_message(
                f"✅ {action_text[self.action_type]}: {', '.join(successful)}", 
                ephemeral=True
            )
        else:
            await interaction.response.send_message("❌ Failed to perform action!", ephemeral=True)

class KickButton(Button):
    def __init__(self):
        super().__init__(label="👢 Kick", style=discord.ButtonStyle.secondary, custom_id="kick", row=1)
    
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        # Show users currently in VC (except owner)
        vc_users = [member for member in view.channel.members if member.id != view.owner_id]
        
        if vc_users:
            kick_view = UserActionView(
                view.cog, view.channel_id, view.owner_id, view.channel,
                vc_users, "kick", "Select users to kick from VC..."
            )
            await interaction.response.send_message(view=kick_view, ephemeral=True)
        else:
            await interaction.response.send_message("❌ No users in VC to kick!", ephemeral=True)

class BlockButton(Button):
    def __init__(self):
        super().__init__(label="🚫 Block", style=discord.ButtonStyle.secondary, custom_id="block", row=1)
    
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        # Show users that are NOT blocked (no explicit deny)
        users_to_block = []
        for member in view.channel.guild.members:
            if member.id != view.owner_id and not member.bot:
                overwrite = view.channel.overwrites_for(member)
                # User is not blocked if they don't have explicit deny
                if overwrite.connect is not False:
                    users_to_block.append(member)
        
        if users_to_block:
            block_view = UserActionView(
                view.cog, view.channel_id, view.owner_id, view.channel,
                users_to_block, "block", "Select users to block..."
            )
            await interaction.response.send_message(view=block_view, ephemeral=True)
        else:
            await interaction.response.send_message("✅ All non-owner users are already blocked!", ephemeral=True)

class ViewAccessListButton(Button):
    def __init__(self):
        super().__init__(label="📋 Access List", style=discord.ButtonStyle.secondary, custom_id="view_access", row=2)
    
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        users_with_access = []
        users_with_deny = []
        
        for member in view.channel.guild.members:
            if member.id != view.owner_id and not member.bot:
                overwrite = view.channel.overwrites_for(member)
                
                # Check for explicit ALLOW permissions
                if any([overwrite.connect is True, overwrite.view_channel is True, 
                       overwrite.speak is True, overwrite.stream is True]):
                    permissions = []
                    if overwrite.connect is True:
                        permissions.append("Join")
                    if overwrite.view_channel is True:
                        permissions.append("View")
                    if overwrite.speak is True:
                        permissions.append("Speak")
                    if overwrite.stream is True:
                        permissions.append("Stream")
                    
                    users_with_access.append(f"✅ {member.display_name} - {', '.join(permissions)}")
                
                # Check for explicit DENY permissions
                elif any([overwrite.connect is False, overwrite.view_channel is False]):
                    deny_permissions = []
                    if overwrite.connect is False:
                        deny_permissions.append("Join")
                    if overwrite.view_channel is False:
                        deny_permissions.append("View")
                    
                    users_with_deny.append(f"❌ {member.display_name} - {', '.join(deny_permissions)}")
        
        embed = discord.Embed(title="📋 Channel Permissions", color=discord.Color.blue())
        
        if users_with_access:
            embed.add_field(
                name="✅ Users with Explicit Access",
                value="\n".join(users_with_access),
                inline=False
            )
        
        if users_with_deny:
            embed.add_field(
                name="❌ Users with Explicit Deny",
                value="\n".join(users_with_deny),
                inline=False
            )
        
        if not users_with_access and not users_with_deny:
            embed.description = "No users have explicit permissions"
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

class ViewBlockedButton(Button):
    def __init__(self):
        super().__init__(label="🚫 Blocked Users", style=discord.ButtonStyle.secondary, custom_id="view_blocked", row=2)
    
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        blocked_users = []
        
        for member in view.channel.guild.members:
            if member.id != view.owner_id and not member.bot:
                overwrite = view.channel.overwrites_for(member)
                if overwrite.connect is False:  # Explicitly blocked
                    blocked_users.append(member.display_name)
        
        if blocked_users:
            embed = discord.Embed(
                title="🚫 Blocked Users",
                description="\n".join(blocked_users),
                color=discord.Color.red()
            )
        else:
            embed = discord.Embed(
                title="🚫 Blocked Users",
                description="No users are blocked",
                color=discord.Color.red()
            )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

class UserAccessView(ControlPanel):
    """View for granting/revoking access permissions"""
    
    def __init__(self, cog, channel_id, owner_id, channel, users, action_type, placeholder):
        super().__init__(cog, channel_id, owner_id)
        self.channel = channel
        self.action_type = action_type
        
        if users:
            self.add_item(UserAccessSelect(users, action_type, placeholder))
        else:
            select = UserSelect(placeholder="No users available", disabled=True, row=0)
            self.add_item(select)

class UserAccessSelect(UserSelect):
    def __init__(self, users, action_type, placeholder):
        super().__init__(
            placeholder=placeholder,
            min_values=1,
            max_values=min(25, len(users)),
            row=0
        )
        self.action_type = action_type
    
    async def callback(self, interaction: discord.Interaction):
        successful = []
        for member in self.values:
            try:
                if self.action_type == "grant":
                    # Grant full access permissions
                    await self.view.channel.set_permissions(
                        member, 
                        connect=True, 
                        view_channel=True,
                        speak=True,
                        stream=True
                    )
                    successful.append(member.display_name)
                
                elif self.action_type == "revoke":
                    # Remove ALL explicit permissions (back to default)
                    await self.view.channel.set_permissions(member, overwrite=None)
                    successful.append(member.display_name)
                    
            except Exception as e:
                print(f"Error in access action: {e}")
        
        if successful:
            action_text = {"grant": "granted access to", "revoke": "revoked access from"}
            await interaction.response.send_message(
                f"✅ {action_text[self.action_type]}: {', '.join(successful)}", 
                ephemeral=True
            )
        else:
            await interaction.response.send_message("❌ Failed to perform action!", ephemeral=True)

class UserActionView(ControlPanel):
    """View for kick/block actions"""
    
    def __init__(self, cog, channel_id, owner_id, channel, users, action_type, placeholder):
        super().__init__(cog, channel_id, owner_id)
        self.channel = channel
        self.action_type = action_type
        
        if users:
            self.add_item(UserActionSelect(users, action_type, placeholder))
        else:
            select = UserSelect(placeholder="No users available", disabled=True, row=0)
            self.add_item(select)

class UserActionSelect(UserSelect):
    def __init__(self, users, action_type, placeholder):
        super().__init__(
            placeholder=placeholder,
            min_values=1,
            max_values=min(25, len(users)),
            row=0
        )
        self.action_type = action_type
    
    async def callback(self, interaction: discord.Interaction):
        successful = []
        for member in self.values:
            try:
                if self.action_type == "kick":
                    await member.move_to(None)
                    successful.append(member.display_name)
                
                elif self.action_type == "block":
                    # Kick if in VC, then block
                    if member in self.view.channel.members:
                        await member.move_to(None)
                    # Set explicit deny permissions
                    await self.view.channel.set_permissions(
                        member, 
                        connect=False, 
                        view_channel=False,
                        speak=False,
                        stream=False
                    )
                    successful.append(member.display_name)
                    
            except Exception as e:
                print(f"Error in user action: {e}")
        
        if successful:
            action_text = {"kick": "kicked", "block": "blockned"}
            await interaction.response.send_message(
                f"✅ {action_text[self.action_type].capitalize()}: {', '.join(successful)}", 
                ephemeral=True
            )
        else:
            await interaction.response.send_message("❌ Failed to perform action!", ephemeral=True)

# Region, Transfer, Claim, Delete buttons remain the same as before
class RegionButton(Button):
    def __init__(self):
        super().__init__(label="🌐 Region", style=discord.ButtonStyle.secondary, custom_id="region", row=1)
    
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        channel = interaction.guild.get_channel(view.channel_id)
        if not channel:
            await interaction.response.send_message("❌ Channel not found!", ephemeral=True)
            return
        
        region_view = RegionView(view.cog, view.channel_id, view.owner_id, channel)
        await interaction.response.send_message(view=region_view, ephemeral=True)

class RegionView(ControlPanel):
    """Voice region selection"""
    
    def __init__(self, cog, channel_id, owner_id, channel):
        super().__init__(cog, channel_id, owner_id)
        self.channel = channel
        
        regions = [
            ("🇺🇸 US West", "us-west"),
            ("🇺🇸 US East", "us-east"), 
            ("🇺🇸 US Central", "us-central"),
            ("🇸🇬 Singapore", "singapore"),
            ("🇮🇳 India", "india"),
            ("🇧🇷 Brazil", "brazil"),
            ("🇦🇺 Australia", "australia"),
            ("🇪🇺 Europe", "europe"),
            ("🇬🇧 London", "london"),
            ("🇩🇪 Germany", "germany"),
            ("🇯🇵 Japan", "japan"),
            ("🇷🇺 Russia", "russia"),
            ("🇿🇦 South Africa", "southafrica"),
            ("🇰🇷 South Korea", "south-korea"),
            ("🇭🇰 Hong Kong", "hongkong")
        ]
        
        for i, (label, region) in enumerate(regions):
            self.add_item(RegionSelectButton(label, region, i // 3))

class RegionSelectButton(Button):
    def __init__(self, label, region, row):
        super().__init__(label=label, style=discord.ButtonStyle.secondary, custom_id=f"region_{region}", row=row)
        self.region = region
    
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        try:
            await view.channel.edit(rtc_region=self.region)
            await interaction.response.send_message(f"🌐 Voice region set to {self.label}!", ephemeral=True)
        except Exception as e:
            print(f"Region change error: {e}")
            await interaction.response.send_message("❌ Failed to change voice region! This region might not be available.", ephemeral=True)

class TransferButton(Button):
    def __init__(self):
        super().__init__(label="🔄 Transfer", style=discord.ButtonStyle.secondary, custom_id="transfer", row=2)
    
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        modal = TransferModal(view.cog, view.channel_id)
        await interaction.response.send_modal(modal)

class ClaimButton(Button):
    def __init__(self):
        super().__init__(label="🎯 Claim", style=discord.ButtonStyle.secondary, custom_id="claim", row=2)
    
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("❌ You must be in a voice channel to claim it!", ephemeral=True)
            return

        channel = interaction.user.voice.channel
        
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT userID FROM voiceChannel WHERE voiceID = ?", (channel.id,)) as cursor:
                data = await cursor.fetchone()
            
            if data:
                owner_id = data[0]
                owner = interaction.guild.get_member(owner_id)
                if owner and owner in channel.members:
                    await interaction.response.send_message("❌ This channel already has an active owner!", ephemeral=True)
                    return
            
            await db.execute("UPDATE voiceChannel SET userID = ? WHERE voiceID = ?", (interaction.user.id, channel.id))
            if db.total_changes == 0:
                base_name = channel.name.split(' ')[0]
                limit = channel.user_limit
                await db.execute(
                    "INSERT INTO voiceChannel (userID, voiceID, channelName, channelLimit) VALUES (?, ?, ?, ?)",
                    (interaction.user.id, channel.id, channel.name, limit)
                )
            await db.commit()
        
        await interaction.response.send_message(f"✅ Successfully claimed {channel.mention}!", ephemeral=True)

class DeleteButton(Button):
    def __init__(self):
        super().__init__(label="🗑️ Delete", style=discord.ButtonStyle.danger, custom_id="delete", row=2)
    
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        channel = interaction.guild.get_channel(view.channel_id)
        if not channel:
            await interaction.response.send_message("❌ Channel not found!", ephemeral=True)
            return
        
        confirm_view = ConfirmDeleteView(view.cog, view.channel_id, view.owner_id, channel)
        await interaction.response.send_message(
            "⚠️ Are you sure you want to delete this voice channel? This action cannot be undone!",
            view=confirm_view,
            ephemeral=True
        )

class ConfirmDeleteView(View):
    def __init__(self, cog, channel_id, owner_id, channel):
        super().__init__(timeout=30)
        self.cog = cog
        self.channel_id = channel_id
        self.owner_id = owner_id
        self.channel = channel
    
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.owner_id
    
    @discord.ui.button(label="✅ Confirm Delete", style=discord.ButtonStyle.danger)
    async def confirm_delete(self, interaction: discord.Interaction, button: Button):
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("DELETE FROM voiceChannel WHERE voiceID = ?", (self.channel.id,))
                await db.commit()
            
            await self.channel.delete(reason="VC owner requested deletion")
            await interaction.response.send_message("✅ Voice channel deleted!", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message("❌ Failed to delete channel!", ephemeral=True)
    
    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_delete(self, interaction: discord.Interaction, button: Button):
        await interaction.response.edit_message(content="Deletion cancelled.", view=None)