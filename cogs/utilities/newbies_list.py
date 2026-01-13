import discord
from discord.ext import commands, tasks
from discord.ui import View, Button, UserSelect
import json
import os
from datetime import datetime
import pytz

class DynamicMemberList(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.data_file = "data/member_list_data.json"
        self.ist = pytz.timezone('Asia/Kolkata')
        
        # Configuration - KEEP AS INTEGERS
        self.MAX_MEMBERS = 12
        self.MEMBER_EXPIRY_DAYS = 2
        self.ADD_ROLES = [1406324000736542911, 1410302930485121106, 1408709182856630332, 1438551732807536690, 1393170995157798983]  # Add role IDs here that can ADD members
        self.REMOVE_ROLES=[1406324000736542911]
        # Load existing data
        self.member_lists = self.load_data()
        
        # Start cleanup tasks
        self.cleanup_expired_members.start()
        
        # Register persistent views
        self.bot.add_view(self.MembersMainView(cog=self))
        
        print(f"[DynamicMemberList] Loaded {len(self.member_lists)} existing lists")
    
    def cog_unload(self):
        """Cleanup when cog is unloaded"""
        self.cleanup_expired_members.cancel()
        self.save_data()
    
    def load_data(self):
        """Load member list data from file"""
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, 'r') as f:
                    data = json.load(f)
                return data
            except:
                return {}
        return {}
    
    def save_data(self):
        """Save member list data to file"""
        os.makedirs(os.path.dirname(self.data_file), exist_ok=True)
        
        with open(self.data_file, 'w') as f:
            json.dump(self.member_lists, f, indent=4)
    
    @tasks.loop(hours=1)
    async def cleanup_expired_members(self):
        """Clean up members who have been on list for 2+ days"""
        now = datetime.now(pytz.UTC)
        expired_count = 0
        
        for channel_id, list_data in list(self.member_lists.items()):
            if 'members' in list_data:
                expired_members = []
                for member_id, member_data in list_data['members'].items():
                    added_at = datetime.fromisoformat(member_data['added_at'])
                    if (now - added_at).days >= self.MEMBER_EXPIRY_DAYS:
                        expired_members.append((member_id, member_data))
                
                for member_id, member_data in expired_members:
                    del list_data['members'][member_id]
                    list_data['removed_members'].append({
                        'id': member_id,
                        'name': member_data['name'],
                        'removed_at': now.isoformat(),
                        'reason': 'expired_2_days'
                    })
                    expired_count += 1
                
                if expired_members:
                    self.save_data()
                    try:
                        channel = self.bot.get_channel(int(channel_id))
                        if channel:
                            # Update both messages
                            await self.update_both_messages(channel, list_data)
                    except:
                        pass
        
        if expired_count > 0:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Cleaned up {expired_count} expired members")
    
    @cleanup_expired_members.before_loop
    async def before_cleanup(self):
        """Wait until bot is ready"""
        await self.bot.wait_until_ready()
    
    @commands.Cog.listener()
    async def on_member_remove(self, member):
        """Immediately remove members when they leave the server"""
        now = datetime.now(pytz.UTC)
        removed_from_lists = 0
        
        for channel_id, list_data in list(self.member_lists.items()):
            if 'members' in list_data and 'guild_id' in list_data:
                # Check if this list belongs to the member's guild
                if int(list_data['guild_id']) == member.guild.id:
                    member_id_str = str(member.id)
                    
                    if member_id_str in list_data['members']:
                        # Remove the member from this list
                        member_data = list_data['members'].pop(member_id_str)
                        list_data['removed_members'].append({
                            'id': member_id_str,
                            'name': member_data['name'],
                            'removed_at': now.isoformat(),
                            'reason': 'left_server_immediately'
                        })
                        removed_from_lists += 1
                        
                        # Save data
                        self.save_data()
                        
                        # Update both messages
                        try:
                            channel = self.bot.get_channel(int(channel_id))
                            if channel:
                                await self.update_both_messages(channel, list_data)
                        except Exception as e:
                            print(f"Error updating list after member left: {e}")
        
        if removed_from_lists > 0:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Removed {member.name} from {removed_from_lists} list(s) (left server)")
    
    # ------------------------------------------------------------
    # Helper Methods for Dual Messages
    # ------------------------------------------------------------
    
    async def update_both_messages(self, channel, list_data):
        """Update both the embed message and plain text message"""
        try:
            # Update embed message
            message_id = list_data.get('message_id')
            if message_id:
                message = await channel.fetch_message(int(message_id))
                if message:
                    await self.update_embed(message, list_data)
            
            # Update plain text message
            plain_message_id = list_data.get('plain_message_id')
            if plain_message_id:
                plain_message = await channel.fetch_message(int(plain_message_id))
                if plain_message:
                    await self.update_plain_text(plain_message, list_data)
        except Exception as e:
            print(f"Error updating messages: {e}")
    
    async def update_plain_text(self, message, list_data):
        """Update the plain text message with member list for copying"""
        members = list_data['members']
        
        if members:
            # Sort members by when they were added (oldest first)
            sorted_members = sorted(
                members.items(),
                key=lambda x: datetime.fromisoformat(x[1]['added_at'])
            )
            
            # Create plain text format for easy copying
            plain_text = ""
            
            for member_id, member_data in sorted_members:
                member = message.guild.get_member(int(member_id))
                
                if member:
                    # Plain @mention format that users can copy
                    plain_text += f"{member.mention} "
            
            if plain_text:
                content = f"{plain_text}"
            else:
                content = "***No members in the list yet*\n*Add members using the buttons above*"
        else:
            content = "***No members in the list yet*\n*Add members using the buttons above*"
        
        # Update message
        await message.edit(content=content)
    
    # ------------------------------------------------------------
    # Button Classes
    # ------------------------------------------------------------
    
    class AddMembersButton(Button):
        """Button to open member selection dropdown"""
        def __init__(self, cog):
            super().__init__(
                label="➕ Add Members",
                style=discord.ButtonStyle.primary,
                custom_id="dml_add_members_button",
                emoji="👥",
                row=0
            )
            self.cog = cog
        
        async def callback(self, interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True)
            
            channel = interaction.channel
            
            # Check permissions - FIXED VERSION
            has_permission = False
            
            # If ADD_ROLES is empty, allow everyone
            if not self.cog.ADD_ROLES:
                has_permission = True
            else:
                # Check each role the user has
                for role in interaction.user.roles:
                    # Compare role.id (int) with ADD_ROLES (int)
                    if role.id in self.cog.ADD_ROLES:
                        has_permission = True
                        break
                
                # Also check if user is admin (optional, but good to have)
                if not has_permission and interaction.user.guild_permissions.administrator:
                    has_permission = True
            
            if not has_permission:
                # Show which roles are required for debugging
                required_roles = []
                for role_id in self.cog.ADD_ROLES:
                    role = interaction.guild.get_role(role_id)
                    if role:
                        required_roles.append(role.mention)
                    else:
                        required_roles.append(f"Unknown Role ({role_id})")
                
                await interaction.followup.send(
                    f"❌ You don't have permission to add members to this list!\n"
                    f"**Required roles:** {', '.join(required_roles) if required_roles else 'None configured'}\n"
                    f"**Your roles:** {', '.join([r.mention for r in interaction.user.roles[1:]])}",
                    ephemeral=True
                )
                return
            
            # Check if list exists
            channel_id = str(channel.id)
            if channel_id not in self.cog.member_lists:
                await interaction.followup.send(
                    "❌ No list found in this channel!",
                    ephemeral=True
                )
                return
            
            list_data = self.cog.member_lists[channel_id]
            
            # Create view with UserSelect
            view = discord.ui.View(timeout=300)
            view.add_item(self.cog.UserSelectDropdown(self.cog, "add", list_data))
            
            await interaction.followup.send(
                "**Select members to add to the list:**\n"
                "Use the dropdown below to choose members.",
                view=view,
                ephemeral=True
            )
    
    class RemoveMembersButton(Button):
        """Button to remove members (Admin/Remove Roles only)"""
        def __init__(self, cog):
            super().__init__(
                label="🗑️ Remove Members",
                style=discord.ButtonStyle.danger,
                custom_id="dml_remove_members_button",
                emoji="❌",
                row=0
            )
            self.cog = cog
        
        async def callback(self, interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True)
            
            # Check permissions - UPDATED VERSION
            has_permission = False
            
            # Check if user is admin
            if interaction.user.guild_permissions.administrator:
                has_permission = True
            
            # Check REMOVE_ROLES if not admin
            if not has_permission and self.cog.REMOVE_ROLES:
                # Check each role the user has
                for role in interaction.user.roles:
                    # Compare role.id (int) with REMOVE_ROLES (int)
                    if role.id in self.cog.REMOVE_ROLES:
                        has_permission = True
                        break
            
            if not has_permission:
                # Show which roles are required for debugging
                required_roles = []
                for role_id in self.cog.REMOVE_ROLES:
                    role = interaction.guild.get_role(role_id)
                    if role:
                        required_roles.append(role.mention)
                    else:
                        required_roles.append(f"Unknown Role ({role_id})")
                
                await interaction.followup.send(
                    f"❌ You don't have permission to remove members from this list!\n"
                    f"**Required:** Administrator permission or one of these roles: {', '.join(required_roles) if required_roles else 'Administrator only'}\n",
                    ephemeral=True
                )
                return
            
            channel = interaction.channel
            
            # Check if list exists
            channel_id = str(channel.id)
            if channel_id not in self.cog.member_lists:
                await interaction.followup.send(
                    "❌ No list found in this channel!",
                    ephemeral=True
                )
                return
            
            list_data = self.cog.member_lists[channel_id]
            
            # Create view with UserSelect
            view = discord.ui.View(timeout=300)
            view.add_item(self.cog.UserSelectDropdown(self.cog, "remove", list_data))
            
            await interaction.followup.send(
                "**Select members to remove from the list:**\n"
                "Use the dropdown below to choose members.",
                view=view,
                ephemeral=True
            )
    
    class RefreshButton(Button):
        """Button to refresh the list"""
        def __init__(self, cog):
            super().__init__(
                label="🔄 Refresh",
                style=discord.ButtonStyle.secondary,
                custom_id="dml_refresh_button",
                emoji="🔄",
                row=0
            )
            self.cog = cog
        
        async def callback(self, interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True)
            
            channel = interaction.channel
            channel_id = str(channel.id)
            
            if channel_id not in self.cog.member_lists:
                await interaction.followup.send(
                    "❌ No list found in this channel!",
                    ephemeral=True
                )
                return
            
            list_data = self.cog.member_lists[channel_id]
            
            try:
                await self.cog.update_both_messages(channel, list_data)
                await interaction.followup.send("✅ List refreshed!", ephemeral=True)
            except Exception as e:
                await interaction.followup.send(f"❌ Error refreshing: {str(e)}", ephemeral=True)
    
    class InfoButton(Button):
        """Button to show list info"""
        def __init__(self, cog):
            super().__init__(
                label="ℹ️ Info",
                style=discord.ButtonStyle.secondary,
                custom_id="dml_info_button",
                emoji="📊",
                row=0
            )
            self.cog = cog
        
        async def callback(self, interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True)
            
            channel = interaction.channel
            channel_id = str(channel.id)
            
            if channel_id not in self.cog.member_lists:
                await interaction.followup.send(
                    "❌ No list found in this channel!",
                    ephemeral=True
                )
                return
            
            list_data = self.cog.member_lists[channel_id]
            members = list_data['members']
            
            embed = discord.Embed(
                title="📋 List Information",
                color=discord.Color.blue(),
                timestamp=datetime.now(pytz.UTC)
            )
            
            # Count members expiring soon
            expiring_soon = 0
            now = datetime.now(pytz.UTC)
            
            for member_id, member_data in members.items():
                added_at = datetime.fromisoformat(member_data['added_at'])
                days_passed = (now - added_at).days
                if days_passed >= 1:  # Expiring in 1 day or less
                    expiring_soon += 1
            
            embed.add_field(
                name="Statistics",
                value=(
                    f"**Total Members:** {len(members)}/{self.cog.MAX_MEMBERS}\n"
                    f"**Expiring Soon:** {expiring_soon} member(s)\n"
                    f"**Duration:** {self.cog.MEMBER_EXPIRY_DAYS} days per member\n"
                    f"**Last Updated:** <t:{int(now.timestamp())}:R>"
                ),
                inline=False
            )
            
            await interaction.followup.send(embed=embed, ephemeral=True)
    
    # ------------------------------------------------------------
    # User Select Dropdown
    # ------------------------------------------------------------
    
    class UserSelectDropdown(UserSelect):
        """Discord's built-in UserSelect for selecting users"""
        def __init__(self, cog, action_type: str, list_data: dict):
            placeholder = "Select members..." if action_type == "add" else "Select members to remove..."
            super().__init__(
                placeholder=placeholder,
                min_values=1,
                max_values=25,  # Discord's max
                custom_id=f"dml_user_select_{action_type}"
            )
            self.cog = cog
            self.action_type = action_type
            self.list_data = list_data
        
        async def callback(self, interaction: discord.Interaction):
            try:
                if not interaction.response.is_done():
                    await interaction.response.defer(ephemeral=True)
            except discord.NotFound:
                await interaction.followup.send("❌ This interaction has expired.", ephemeral=True)
                return
            
            channel_id = str(interaction.channel.id)
            
            if channel_id not in self.cog.member_lists:
                await interaction.followup.send("❌ This list no longer exists!", ephemeral=True)
                return
            
            list_data = self.cog.member_lists[channel_id]
            
            if self.action_type == "add":
                # Check ADD permissions - FIXED VERSION
                has_permission = False
                if not self.cog.ADD_ROLES:
                    has_permission = True
                else:
                    for role in interaction.user.roles:
                        if role.id in self.cog.ADD_ROLES:
                            has_permission = True
                            break
                
                if not has_permission:
                    await interaction.followup.send("❌ You don't have permission to add members!", ephemeral=True)
                    return
                
                added_members = []
                skipped_members = []
                
                for member in self.values:
                    member_id_str = str(member.id)
                    
                    # Skip if already in list
                    if member_id_str in list_data['members']:
                        skipped_members.append(member)
                        continue
                    
                    # Check capacity and remove oldest if needed
                    if len(list_data['members']) >= self.cog.MAX_MEMBERS:
                        oldest_member_id = None
                        oldest_time = None
                        
                        for mid, data in list_data['members'].items():
                            added_time = datetime.fromisoformat(data['added_at'])
                            if oldest_time is None or added_time < oldest_time:
                                oldest_time = added_time
                                oldest_member_id = mid
                        
                        if oldest_member_id:
                            old_data = list_data['members'].pop(oldest_member_id)
                            list_data['removed_members'].append({
                                'id': oldest_member_id,
                                'name': old_data['name'],
                                'removed_at': datetime.now(pytz.UTC).isoformat(),
                                'removed_by': str(interaction.user.id),
                                'reason': 'auto_removed_capacity'
                            })
                    
                    # Add member WITHOUT avatar URL
                    list_data['members'][member_id_str] = {
                        'id': member_id_str,
                        'name': member.name,
                        'display_name': member.display_name,
                        'added_at': datetime.now(pytz.UTC).isoformat(),
                        'added_by': str(interaction.user.id),
                        'added_by_name': interaction.user.name
                    }
                    added_members.append(member)
                
                # Save and update
                self.cog.save_data()
                
                try:
                    await self.cog.update_both_messages(interaction.channel, list_data)
                    
                    response_text = ""
                    if added_members:
                        response_text += f"✅ **Added {len(added_members)} member(s):**\n"
                        for member in added_members:
                            response_text += f"• **{member.display_name or member.name}** (@{member.name})\n"
                    
                    if skipped_members:
                        response_text += f"\n⚠️ Skipped {len(skipped_members)} (already in list)"
                    
                    await interaction.followup.send(response_text or "No members added.", ephemeral=True)
                except Exception as e:
                    await interaction.followup.send(f"❌ Error updating list: {str(e)}", ephemeral=True)
            
            elif self.action_type == "remove":
                # Check permissions - UPDATED VERSION
                has_permission = False
                
                # Check if user is admin
                if interaction.user.guild_permissions.administrator:
                    has_permission = True
                
                # Check REMOVE_ROLES if not admin
                if not has_permission and self.cog.REMOVE_ROLES:
                    for role in interaction.user.roles:
                        if role.id in self.cog.REMOVE_ROLES:
                            has_permission = True
                            break
                
                if not has_permission:
                    await interaction.followup.send("❌ You don't have permission to remove members!", ephemeral=True)
                    return
                
                removed_members = []
                
                for member in self.values:
                    member_id_str = str(member.id)
                    
                    if member_id_str in list_data['members']:
                        member_data = list_data['members'].pop(member_id_str)
                        list_data['removed_members'].append({
                            'id': member_id_str,
                            'name': member_data['name'],
                            'removed_at': datetime.now(pytz.UTC).isoformat(),
                            'removed_by': str(interaction.user.id),
                            'reason': 'admin_removed'
                        })
                        removed_members.append(member)
                
                if removed_members:
                    self.cog.save_data()
                    
                    try:
                        await self.cog.update_both_messages(interaction.channel, list_data)
                        
                        response_text = f"✅ **Removed {len(removed_members)} member(s):**\n"
                        for member in removed_members:
                            response_text += f"• **{member.display_name or member.name}** (@{member.name})\n"
                        
                        await interaction.followup.send(response_text, ephemeral=True)
                    except Exception as e:
                        await interaction.followup.send(f"❌ Error updating list: {str(e)}", ephemeral=True)
                else:
                    await interaction.followup.send("❌ No members were removed!", ephemeral=True)
    
    # ------------------------------------------------------------
    # Main View
    # ------------------------------------------------------------
    
    class MembersMainView(View):
        """Main view with buttons for the member list"""
        def __init__(self, cog, channel: discord.TextChannel = None):
            super().__init__(timeout=None)  # Persistent view
            self.cog = cog
            
            # Add buttons with unique custom_ids
            self.add_item(DynamicMemberList.AddMembersButton(cog))
            self.add_item(DynamicMemberList.RemoveMembersButton(cog))
            self.add_item(DynamicMemberList.RefreshButton(cog))
            self.add_item(DynamicMemberList.InfoButton(cog))
    
    # ------------------------------------------------------------
    # Helper Methods
    # ------------------------------------------------------------
    
    async def update_embed(self, message, list_data):
        """Update the embed with current member list"""
        members = list_data['members']
        
        # Create embed
        embed = discord.Embed(
            title="📋 Dynamic Member List",
            description="Use the buttons below to manage the member list.",
            color=discord.Color.blue(),
            timestamp=datetime.now(pytz.UTC)
        )
        
        # Add member list
        if members:
            # Sort members by when they were added (oldest first)
            sorted_members = sorted(
                members.items(),
                key=lambda x: datetime.fromisoformat(x[1]['added_at'])
            )
            
            # Simple member list format
            member_text = ""
            member_count = 1
            
            for member_id, member_data in sorted_members:
                member = message.guild.get_member(int(member_id))
                
                if member:
                    # Member is in the server
                    member_text += f"{member.mention} \n"
                    member_count += 1
                else:
                    # Member left - skip them
                    continue
            
            if member_text:
                embed.add_field(
                    name=f"👥 Current Members ({len(members)}/{self.MAX_MEMBERS})",
                    value=member_text,
                    inline=False
                )
            else:
                embed.add_field(
                    name=f"👥 Current Members (0/{self.MAX_MEMBERS})",
                    value="*No members in the list yet*\nUse the **➕ Add Members** button to add members.",
                    inline=False
                )
        else:
            embed.add_field(
                name=f"👥 Current Members (0/{self.MAX_MEMBERS})",
                value="*No members in the list yet*\nUse the **➕ Add Members** button to add members.",
                inline=False
            )
        
        # Create main view with buttons
        view = self.MembersMainView(cog=self, channel=message.channel)
        
        # Update message
        await message.edit(embed=embed, view=view)
    
    # ------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------
    
    @commands.command(name="createlist")
    @commands.has_permissions(administrator=True)
    async def create_list(self, ctx):
        """Create a new dynamic member list (Admin only)"""
        channel_id = str(ctx.channel.id)
        
        if channel_id in self.member_lists:
            await ctx.send("⚠️ A member list already exists in this channel! Use `.deletelist` first.")
            return
        
        # Create initial data
        list_data = {
            'message_id': None,
            'plain_message_id': None,  # NEW: Store plain text message ID
            'members': {},
            'removed_members': [],
            'created_at': datetime.now(pytz.UTC).isoformat(),
            'created_by': str(ctx.author.id),
            'guild_id': str(ctx.guild.id)
        }
        
        # Create initial embed (buttons message)
        embed = discord.Embed(
            title="📋 Dynamic Member List",
            description="Use the buttons below to manage the member list.",
            color=discord.Color.blue(),
            timestamp=datetime.now(pytz.UTC)
        )
        
        embed.add_field(
            name=f"👥 Current Members (0/{self.MAX_MEMBERS})",
            value="*No members in the list yet*\nUse the **➕ Add Members** button to add members.",
            inline=False
        )
        
        # Create view with buttons
        view = self.MembersMainView(cog=self, channel=ctx.channel)
        
        # Send first message with buttons
        message = await ctx.send(embed=embed, view=view)
        
        # Send second message for plain text copy
        plain_text_message = await ctx.send("***No members in the list yet*\n*Add members using the buttons above*")
        
        # Store data
        list_data['message_id'] = str(message.id)
        list_data['plain_message_id'] = str(plain_text_message.id)  # NEW: Store plain text message ID
        self.member_lists[channel_id] = list_data
        self.save_data()
        
        await ctx.send("✅ Dynamic member list created with copy-friendly display!", delete_after=5)
    
    @commands.command(name="deletelist")
    @commands.has_permissions(administrator=True)
    async def delete_list(self, ctx):
        """Delete the member list from this channel (Admin only)"""
        channel_id = str(ctx.channel.id)
        
        if channel_id not in self.member_lists:
            await ctx.send("❌ No member list exists in this channel!")
            return
        
        # Try to delete both messages
        list_data = self.member_lists[channel_id]
        message_id = list_data.get('message_id')
        plain_message_id = list_data.get('plain_message_id')
        
        try:
            if message_id:
                message = await ctx.channel.fetch_message(int(message_id))
                await message.delete()
        except:
            pass
        
        try:
            if plain_message_id:
                plain_message = await ctx.channel.fetch_message(int(plain_message_id))
                await plain_message.delete()
        except:
            pass
        
        # Remove from data
        del self.member_lists[channel_id]
        self.save_data()
        
        await ctx.send("✅ Member list deleted!")
    
    @commands.command(name="setaddroles")
    @commands.has_permissions(administrator=True)
    async def set_add_roles(self, ctx, *roles: discord.Role):
        """Set which roles can add members to the list (Admin only)"""
        if not roles:
            await ctx.send("❌ Please mention at least one role!")
            return
        
        self.ADD_ROLES = [role.id for role in roles]
        await ctx.send(f"✅ Set add permissions to: {', '.join([role.mention for role in roles])}")
    
    @commands.command(name="forceremove")
    @commands.has_permissions(administrator=True)
    async def force_remove(self, ctx, member: discord.Member = None):
        """Force remove a member from the list (Admin only)"""
        if not member:
            await ctx.send("❌ Please mention a member to remove!")
            return
        
        channel_id = str(ctx.channel.id)
        
        if channel_id not in self.member_lists:
            await ctx.send("❌ No member list exists in this channel!")
            return
        
        list_data = self.member_lists[channel_id]
        member_id = str(member.id)
        
        if member_id not in list_data['members']:
            await ctx.send(f"❌ **{member.display_name or member.name}** (@{member.name}) is not in the list!")
            return
        
        # Remove member
        member_data = list_data['members'].pop(member_id)
        list_data['removed_members'].append({
            'id': member_id,
            'name': member.name,
            'removed_at': datetime.now(pytz.UTC).isoformat(),
            'removed_by': str(ctx.author.id),
            'reason': 'admin_force_remove'
        })
        
        self.save_data()
        
        # Update both messages
        try:
            await self.update_both_messages(ctx.channel, list_data)
            await ctx.send(f"✅ Force removed **{member.display_name or member.name}** (@{member.name}) from the list.")
        except Exception as e:
            await ctx.send(f"❌ Error updating list: {str(e)}")
    
    @commands.command(name="reloadviews")
    @commands.has_permissions(administrator=True)
    async def reload_views(self, ctx):
        """Reload persistent views for all lists (Admin only)"""
        await ctx.send("🔄 Reloading persistent views...")
        
        # Clear existing views
        self.bot.remove_view(self.MembersMainView(cog=self))
        
        # Re-add view
        self.bot.add_view(self.MembersMainView(cog=self))
        
        await ctx.send("✅ Persistent views reloaded!")
    
    @commands.command(name="checkperms")
    async def check_perms(self, ctx):
        """Check your permissions for the member list"""
        user = ctx.author
        
        # Check which ADD_ROLES user has
        user_role_ids = [role.id for role in user.roles]
        matching_roles = []
        
        for role_id in self.ADD_ROLES:
            if role_id in user_role_ids:
                role = ctx.guild.get_role(role_id)
                matching_roles.append(role.mention if role else f"Role {role_id}")
        
        embed = discord.Embed(
            title="🔍 Permission Check",
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name="Configured ADD_ROLES",
            value="\n".join([f"• <@&{role_id}> ({role_id})" for role_id in self.ADD_ROLES]) or "None",
            inline=False
        )
        
        embed.add_field(
            name="Your Roles",
            value="\n".join([f"• {role.mention} ({role.id})" for role in user.roles[1:]]) or "None",
            inline=False
        )
        
        embed.add_field(
            name="Matching Roles",
            value="\n".join(matching_roles) if matching_roles else "❌ No matching roles",
            inline=False
        )
        
        embed.add_field(
            name="Has Permission?",
            value="✅ YES" if matching_roles or not self.ADD_ROLES else "❌ NO",
            inline=False
        )
        
        await ctx.send(embed=embed)
    
    @commands.command(name="listconfig")
    @commands.has_permissions(administrator=True)
    async def list_config(self, ctx):
        """Show current configuration (Admin only)"""
        embed = discord.Embed(
            title="⚙️ Dynamic Member List Configuration",
            color=discord.Color.green()
        )
        
        embed.add_field(
            name="Max Members",
            value=str(self.MAX_MEMBERS),
            inline=True
        )
        
        embed.add_field(
            name="Expiry Days",
            value=str(self.MEMBER_EXPIRY_DAYS),
            inline=True
        )
        
        embed.add_field(
            name="Add Roles",
            value="\n".join([f"• <@&{role_id}>" for role_id in self.ADD_ROLES]) or "None (everyone can add)",
            inline=False
        )
        
        embed.add_field(
            name="Active Lists",
            value=str(len(self.member_lists)),
            inline=True
        )
        
        await ctx.send(embed=embed)
    
    @commands.command(name="showmembers")
    async def show_members(self, ctx):
        """Show all members in the current list"""
        channel_id = str(ctx.channel.id)
        
        if channel_id not in self.member_lists:
            await ctx.send("❌ No member list exists in this channel!")
            return
        
        list_data = self.member_lists[channel_id]
        members = list_data['members']
        
        if not members:
            await ctx.send("📭 The list is currently empty!")
            return
        
        # Create an embed
        embed = discord.Embed(
            title="👥 Members in List",
            color=discord.Color.blue(),
            timestamp=datetime.now(pytz.UTC)
        )
        
        member_text = ""
        member_count = 1
        
        for member_id, member_data in members.items():
            member = ctx.guild.get_member(int(member_id))
            
            if member:
                member_text += f"**{member_count}.** **{member.display_name or member.name}** (@{member.name})\n"
                member_count += 1
            else:
                # Member left - skip (should have been removed)
                continue
        
        embed.add_field(
            name=f"Total: {len(members)}/{self.MAX_MEMBERS}",
            value=member_text or "No active members in the list.",
            inline=False
        )
        
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(DynamicMemberList(bot))