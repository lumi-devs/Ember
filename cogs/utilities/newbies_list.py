import discord
from discord.ext import commands, tasks
from discord.ui import Select, View, Button
import json
import os
from datetime import datetime, timedelta
import pytz
import asyncio
from typing import List

class DynamicMemberList(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.data_file = "data/member_list_data.json"
        self.ist = pytz.timezone('Asia/Kolkata')
        
        # Configuration
        self.MAX_MEMBERS = 12
        self.MEMBER_EXPIRY_DAYS = 2
        self.ADD_ROLES = []  # Add role IDs here that can ADD members
        
        # Load existing data
        self.member_lists = self.load_data()
        
        # Start cleanup tasks
        self.cleanup_expired_members.start()
        self.cleanup_left_members.start()
    
    def cog_unload(self):
        """Cleanup when cog is unloaded"""
        self.cleanup_expired_members.cancel()
        self.cleanup_left_members.cancel()
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
                            message_id = list_data.get('message_id')
                            if message_id:
                                message = await channel.fetch_message(int(message_id))
                                if message:
                                    await self.update_embed(message, list_data)
                    except:
                        pass
        
        if expired_count > 0:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Cleaned up {expired_count} expired members")
    
    @tasks.loop(hours=1)
    async def cleanup_left_members(self):
        """Clean up members who have left the server"""
        now = datetime.now(pytz.UTC)
        left_count = 0
        
        for channel_id, list_data in list(self.member_lists.items()):
            if 'members' in list_data and 'guild_id' in list_data:
                try:
                    guild = self.bot.get_guild(int(list_data['guild_id']))
                    if not guild:
                        continue
                    
                    members_to_remove = []
                    for member_id, member_data in list_data['members'].items():
                        member_in_guild = guild.get_member(int(member_id))
                        if not member_in_guild:
                            members_to_remove.append((member_id, member_data))
                    
                    for member_id, member_data in members_to_remove:
                        del list_data['members'][member_id]
                        list_data['removed_members'].append({
                            'id': member_id,
                            'name': member_data['name'],
                            'removed_at': now.isoformat(),
                            'reason': 'left_server'
                        })
                        left_count += 1
                    
                    if members_to_remove:
                        self.save_data()
                        try:
                            channel = self.bot.get_channel(int(channel_id))
                            if channel:
                                message_id = list_data.get('message_id')
                                if message_id:
                                    message = await channel.fetch_message(int(message_id))
                                    if message:
                                        await self.update_embed(message, list_data)
                        except:
                            pass
                except Exception as e:
                    print(f"Error in cleanup_left_members: {e}")
        
        if left_count > 0:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Cleaned up {left_count} members who left the server")
    
    @cleanup_expired_members.before_loop
    @cleanup_left_members.before_loop
    async def before_cleanup(self):
        """Wait until bot is ready"""
        await self.bot.wait_until_ready()
    
    class AddMembersButton(Button):
        """Button to open member selection dropdown"""
        def __init__(self):
            super().__init__(
                label="➕ Add Members",
                style=discord.ButtonStyle.primary,
                custom_id="add_members_button",
                emoji="👥",
                row=0
            )
        
        async def callback(self, interaction: discord.Interaction):
            # Defer the response
            await interaction.response.defer(ephemeral=True)
            
            # Get the view instance
            view: MembersMainView = self.view
            
            # Check permissions
            has_permission = False
            if not view.cog.ADD_ROLES:  # If empty, allow everyone
                has_permission = True
            else:
                for role in interaction.user.roles:
                    if str(role.id) in view.cog.ADD_ROLES:
                        has_permission = True
                        break
            
            if not has_permission:
                await interaction.followup.send(
                    "❌ You don't have permission to add members to this list!",
                    ephemeral=True
                )
                return
            
            # Get available members (not in list, not bots, not self)
            users = []
            for member in view.channel.guild.members:
                if member.bot:
                    continue
                
                # Check if member is already in the list
                channel_id = str(view.channel.id)
                if channel_id in view.cog.member_lists:
                    if str(member.id) in view.cog.member_lists[channel_id]['members']:
                        continue
                
                users.append(member)
            
            if users:
                # Send dropdown view
                await interaction.followup.send(
                    view=DynamicMemberList.UserSelectActionView(
                        view.cog, 
                        view.channel_id, 
                        view.owner_id, 
                        view.channel, 
                        users, 
                        "add", 
                        "Select members to add to the list..."
                    ),
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    "✅ All available members are already in the list or no members available!",
                    ephemeral=True
                )
    
    class RemoveMembersButton(Button):
        """Button to remove members (Admin only)"""
        def __init__(self):
            super().__init__(
                label="🗑️ Remove Members",
                style=discord.ButtonStyle.danger,
                custom_id="remove_members_button",
                emoji="❌",
                row=0
            )
        
        async def callback(self, interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True)
            
            # Check if user is admin
            if not interaction.user.guild_permissions.administrator:
                await interaction.followup.send(
                    "❌ Only administrators can remove members from the list!",
                    ephemeral=True
                )
                return
            
            view: MembersMainView = self.view
            
            # Get members currently in the list
            channel_id = str(view.channel.id)
            if channel_id not in view.cog.member_lists:
                await interaction.followup.send(
                    "❌ No list found in this channel!",
                    ephemeral=True
                )
                return
            
            list_data = view.cog.member_lists[channel_id]
            users_in_list = []
            
            for member_id, member_data in list_data['members'].items():
                member = view.channel.guild.get_member(int(member_id))
                if member:
                    users_in_list.append(member)
            
            if users_in_list:
                await interaction.followup.send(
                    view=DynamicMemberList.UserSelectActionView(
                        view.cog,
                        view.channel_id,
                        view.owner_id,
                        view.channel,
                        users_in_list,
                        "remove",
                        "Select members to remove from the list..."
                    ),
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    "❌ No members in the list to remove!",
                    ephemeral=True
                )
    
    class RefreshButton(Button):
        """Button to refresh the list"""
        def __init__(self):
            super().__init__(
                label="🔄 Refresh",
                style=discord.ButtonStyle.secondary,
                custom_id="refresh_button",
                emoji="🔄",
                row=0
            )
        
        async def callback(self, interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True)
            
            view: MembersMainView = self.view
            channel_id = str(view.channel.id)
            
            if channel_id not in view.cog.member_lists:
                await interaction.followup.send(
                    "❌ No list found in this channel!",
                    ephemeral=True
                )
                return
            
            list_data = view.cog.member_lists[channel_id]
            
            try:
                message = await view.channel.fetch_message(int(list_data['message_id']))
                await view.cog.update_embed(message, list_data)
                await interaction.followup.send("✅ List refreshed!", ephemeral=True)
            except Exception as e:
                await interaction.followup.send(f"❌ Error refreshing: {str(e)}", ephemeral=True)
    
    class InfoButton(Button):
        """Button to show list info"""
        def __init__(self):
            super().__init__(
                label="ℹ️ Info",
                style=discord.ButtonStyle.secondary,
                custom_id="info_button",
                emoji="📊",
                row=0
            )
        
        async def callback(self, interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True)
            
            view: MembersMainView = self.view
            channel_id = str(view.channel.id)
            
            if channel_id not in view.cog.member_lists:
                await interaction.followup.send(
                    "❌ No list found in this channel!",
                    ephemeral=True
                )
                return
            
            list_data = view.cog.member_lists[channel_id]
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
                    f"**Total Members:** {len(members)}/{view.cog.MAX_MEMBERS}\n"
                    f"**Expiring Soon:** {expiring_soon} member(s)\n"
                    f"**Duration:** {view.cog.MEMBER_EXPIRY_DAYS} days per member\n"
                    f"**Last Updated:** <t:{int(now.timestamp())}:R>"
                ),
                inline=False
            )
            
            # Show members expiring soon
            if expiring_soon > 0:
                expiring_text = ""
                for member_id, member_data in list(members.items())[:5]:  # Show first 5
                    added_at = datetime.fromisoformat(member_data['added_at'])
                    days_left = view.cog.MEMBER_EXPIRY_DAYS - (now - added_at).days
                    
                    if days_left <= 1:
                        member = view.channel.guild.get_member(int(member_id))
                        name = member.mention if member else member_data['name']
                        expiring_text += f"• {name} (expires in {days_left} day{'s' if days_left != 1 else ''})\n"
                
                if expiring_text:
                    embed.add_field(
                        name="Expiring Soon",
                        value=expiring_text,
                        inline=False
                    )
            
            await interaction.followup.send(embed=embed, ephemeral=True)
    
    class UserSelect(Select):
        """Dropdown for selecting users"""
        def __init__(self, users: List[discord.Member], action_type: str, placeholder: str):
            options = []
            
            for user in users[:25]:  # Discord limit
                options.append(
                    discord.SelectOption(
                        label=user.display_name[:100],
                        value=str(user.id),
                        description=f"Click to {action_type} {user.name[:50]}",
                        emoji="👤"
                    )
                )
            
            super().__init__(
                placeholder=placeholder,
                min_values=1,
                max_values=min(len(users), 10),
                options=options
            )
            self.action_type = action_type
        
        async def callback(self, interaction: discord.Interaction):
            # Get the parent view
            view: UserSelectActionView = self.view
            await view.handle_selection(interaction, self.values)
    
    class UserSelectActionView(View):
        """View with user dropdown for actions"""
        def __init__(self, cog, channel_id: int, owner_id: int, channel: discord.TextChannel, 
                     users: List[discord.Member], action_type: str, placeholder: str):
            super().__init__(timeout=300)  # 5 minute timeout
            self.cog = cog
            self.channel_id = channel_id
            self.owner_id = owner_id
            self.channel = channel
            self.action_type = action_type
            
            if users:
                self.add_item(DynamicMemberList.UserSelect(users, action_type, placeholder))
            else:
                # Create disabled select
                select = Select(
                    placeholder="No users available",
                    options=[discord.SelectOption(label="No users", value="none", description="No users to select")],
                    disabled=True
                )
                self.add_item(select)
        
        async def handle_selection(self, interaction: discord.Interaction, selected_values: List[str]):
            channel_id = str(self.channel_id)
            
            if channel_id not in self.cog.member_lists:
                await interaction.response.send_message(
                    "❌ This list no longer exists!",
                    ephemeral=True
                )
                return
            
            list_data = self.cog.member_lists[channel_id]
            
            if self.action_type == "add":
                # Check ADD permissions again
                has_permission = False
                if not self.cog.ADD_ROLES:
                    has_permission = True
                else:
                    for role in interaction.user.roles:
                        if str(role.id) in self.cog.ADD_ROLES:
                            has_permission = True
                            break
                
                if not has_permission:
                    await interaction.response.send_message(
                        "❌ You don't have permission to add members!",
                        ephemeral=True
                    )
                    return
                
                added_members = []
                skipped_members = []
                
                for member_id in selected_values:
                    member_id_str = str(member_id)
                    
                    if member_id_str in list_data['members']:
                        skipped_members.append(member_id_str)
                        continue
                    
                    # Check capacity
                    if len(list_data['members']) >= self.cog.MAX_MEMBERS:
                        # Remove oldest
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
                    
                    # Add member
                    try:
                        member = await self.channel.guild.fetch_member(int(member_id))
                        list_data['members'][member_id_str] = {
                            'id': member_id_str,
                            'name': member.name,
                            'display_name': member.display_name,
                            'added_at': datetime.now(pytz.UTC).isoformat(),
                            'added_by': str(interaction.user.id),
                            'added_by_name': interaction.user.name
                        }
                        added_members.append(member)
                    except:
                        continue
                
                # Save and update
                self.cog.save_data()
                
                try:
                    message = await self.channel.fetch_message(int(list_data['message_id']))
                    await self.cog.update_embed(message, list_data)
                    
                    response_text = ""
                    if added_members:
                        response_text += f"✅ **Added {len(added_members)} member(s):**\n"
                        for member in added_members:
                            response_text += f"• {member.mention}\n"
                    
                    if skipped_members:
                        response_text += f"\n⚠️ Skipped {len(skipped_members)} (already in list)"
                    
                    await interaction.response.send_message(
                        response_text or "No members added.",
                        ephemeral=True
                    )
                except Exception as e:
                    await interaction.response.send_message(
                        f"❌ Error: {str(e)}",
                        ephemeral=True
                    )
            
            elif self.action_type == "remove":
                # Check if user is admin
                if not interaction.user.guild_permissions.administrator:
                    await interaction.response.send_message(
                        "❌ Only admins can remove members!",
                        ephemeral=True
                    )
                    return
                
                removed_members = []
                
                for member_id in selected_values:
                    member_id_str = str(member_id)
                    
                    if member_id_str in list_data['members']:
                        member_data = list_data['members'].pop(member_id_str)
                        list_data['removed_members'].append({
                            'id': member_id_str,
                            'name': member_data['name'],
                            'removed_at': datetime.now(pytz.UTC).isoformat(),
                            'removed_by': str(interaction.user.id),
                            'reason': 'admin_removed'
                        })
                        
                        member = self.channel.guild.get_member(int(member_id))
                        if member:
                            removed_members.append(member)
                        else:
                            removed_members.append(member_data['name'])
                
                if removed_members:
                    self.cog.save_data()
                    
                    try:
                        message = await self.channel.fetch_message(int(list_data['message_id']))
                        await self.cog.update_embed(message, list_data)
                        
                        response_text = f"✅ **Removed {len(removed_members)} member(s):**\n"
                        for member in removed_members:
                            if isinstance(member, discord.Member):
                                response_text += f"• {member.mention}\n"
                            else:
                                response_text += f"• {member}\n"
                        
                        await interaction.response.send_message(
                            response_text,
                            ephemeral=True
                        )
                    except Exception as e:
                        await interaction.response.send_message(
                            f"❌ Error: {str(e)}",
                            ephemeral=True
                        )
                else:
                    await interaction.response.send_message(
                        "❌ No members were removed!",
                        ephemeral=True
                    )
    
    class MembersMainView(View):
        """Main view with buttons for the member list"""
        def __init__(self, cog, channel_id: int, owner_id: int, channel: discord.TextChannel):
            super().__init__(timeout=None)  # Persistent view
            self.cog = cog
            self.channel_id = channel_id
            self.owner_id = owner_id
            self.channel = channel
            
            # Add buttons
            self.add_item(DynamicMemberList.AddMembersButton())
            self.add_item(DynamicMemberList.RemoveMembersButton())
            self.add_item(DynamicMemberList.RefreshButton())
            self.add_item(DynamicMemberList.InfoButton())
    
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
            members_list = ""
            sorted_members = sorted(
                members.items(),
                key=lambda x: datetime.fromisoformat(x[1]['added_at'])
            )
            
            for member_id, member_data in sorted_members:
                added_at = datetime.fromisoformat(member_data['added_at'])
                time_ago = self.get_time_ago(added_at)
                expires_in = self.MEMBER_EXPIRY_DAYS - (datetime.now(pytz.UTC) - added_at).days
                
                member = message.guild.get_member(int(member_id))
                if member:
                    members_list += f"• {member.mention} - Added {time_ago} (Expires in {expires_in} day{'s' if expires_in != 1 else ''})\n"
                else:
                    members_list += f"• **{member_data['name']}** - Added {time_ago} (Expires in {expires_in} day{'s' if expires_in != 1 else ''})\n"
            
            embed.add_field(
                name=f"📝 Current Members ({len(members)}/{self.MAX_MEMBERS})",
                value=members_list,
                inline=False
            )
        else:
            embed.add_field(
                name=f"📝 Current Members (0/{self.MAX_MEMBERS})",
                value="*No members in the list yet*\nUse the **➕ Add Members** button to add members.",
                inline=False
            )
        
        # Add rules
        embed.add_field(
            name="⚙️ Rules",
            value=(
                f"• **Max {self.MAX_MEMBERS} members** at a time\n"
                f"• Members stay for **{self.MEMBER_EXPIRY_DAYS} days**\n"
                f"• Oldest members removed when full\n"
                f"• Auto-removed if member leaves server\n"
                f"• Only specific roles can add members"
            ),
            inline=False
        )
        
        # Create main view with buttons
        view = self.MembersMainView(
            cog=self,
            channel_id=message.channel.id,
            owner_id=message.guild.owner_id,
            channel=message.channel
        )
        
        # Update message
        await message.edit(embed=embed, view=view)
    
    def get_time_ago(self, dt):
        """Convert datetime to human-readable time ago string"""
        now = datetime.now(pytz.UTC)
        diff = now - dt
        
        if diff.days > 0:
            return f"{diff.days} day{'s' if diff.days != 1 else ''} ago"
        elif diff.seconds >= 3600:
            hours = diff.seconds // 3600
            return f"{hours} hour{'s' if hours != 1 else ''} ago"
        elif diff.seconds >= 60:
            minutes = diff.seconds // 60
            return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
        else:
            return "just now"
    
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
            'members': {},
            'removed_members': [],
            'created_at': datetime.now(pytz.UTC).isoformat(),
            'created_by': str(ctx.author.id),
            'guild_id': str(ctx.guild.id)
        }
        
        # Create initial embed
        embed = discord.Embed(
            title="📋 Dynamic Member List",
            description="Use the buttons below to manage the member list.",
            color=discord.Color.blue(),
            timestamp=datetime.now(pytz.UTC)
        )
        
        embed.add_field(
            name=f"📝 Current Members (0/{self.MAX_MEMBERS})",
            value="*No members in the list yet*\nUse the **➕ Add Members** button to add members.",
            inline=False
        )
        
        embed.add_field(
            name="⚙️ Rules",
            value=(
                f"• **Max {self.MAX_MEMBERS} members** at a time\n"
                f"• Members stay for **{self.MEMBER_EXPIRY_DAYS} days**\n"
                f"• Oldest members removed when full\n"
                f"• Auto-removed if member leaves server\n"
                f"• Only specific roles can add members"
            ),
            inline=False
        )
        
        # Create view with buttons
        view = self.MembersMainView(
            cog=self,
            channel_id=ctx.channel.id,
            owner_id=ctx.guild.owner_id,
            channel=ctx.channel
        )
        
        # Send message
        message = await ctx.send(embed=embed, view=view)
        
        # Store data
        list_data['message_id'] = str(message.id)
        self.member_lists[channel_id] = list_data
        self.save_data()
        
        await ctx.send("✅ Dynamic member list created!", delete_after=5)
    
    @commands.command(name="deletelist")
    @commands.has_permissions(administrator=True)
    async def delete_list(self, ctx):
        """Delete the member list from this channel (Admin only)"""
        channel_id = str(ctx.channel.id)
        
        if channel_id not in self.member_lists:
            await ctx.send("❌ No member list exists in this channel!")
            return
        
        # Try to delete the message
        list_data = self.member_lists[channel_id]
        message_id = list_data.get('message_id')
        
        try:
            if message_id:
                message = await ctx.channel.fetch_message(int(message_id))
                await message.delete()
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
        
        self.ADD_ROLES = [str(role.id) for role in roles]
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
            await ctx.send(f"❌ {member.mention} is not in the list!")
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
        
        # Update embed
        try:
            message = await ctx.channel.fetch_message(int(list_data['message_id']))
            await self.update_embed(message, list_data)
            await ctx.send(f"✅ Force removed {member.mention} from the list.")
        except Exception as e:
            await ctx.send(f"❌ Error updating list: {str(e)}")

async def setup(bot):
    await bot.add_cog(DynamicMemberList(bot))