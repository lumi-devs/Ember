import discord
from discord.ext import commands, tasks
from discord.ui import Select, View
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
    
    class UserSelect(Select):
        """Dropdown for selecting users"""
        def __init__(self, users: List[discord.Member], action_type: str, placeholder: str):
            options = []
            
            for user in users[:25]:  # Discord limit of 25 options
                options.append(
                    discord.SelectOption(
                        label=user.display_name[:100],
                        value=str(user.id),
                        description=f"ID: {user.id}"[:100],
                        emoji="👤"
                    )
                )
            
            super().__init__(
                placeholder=placeholder,
                min_values=1,
                max_values=min(len(users), 10),
                options=options
            )
            self.users = users
            self.action_type = action_type
        
        async def callback(self, interaction: discord.Interaction):
            # This will be handled by the parent View
            pass
    
    class UserSelectActionView(View):
        """View with user dropdown for actions"""
        def __init__(self, cog, channel_id: int, owner_id: int, channel: discord.TextChannel, 
                     users: List[discord.Member], action_type: str, placeholder: str):
            super().__init__(timeout=None)
            self.cog = cog
            self.channel_id = channel_id
            self.owner_id = owner_id
            self.channel = channel
            self.action_type = action_type
            
            if users:
                self.add_item(DynamicMemberList.UserSelect(users, action_type, placeholder))
            else:
                # Create disabled select if no users
                select = Select(
                    placeholder="No users available",
                    options=[discord.SelectOption(label="No users", value="none", description="No users to select")],
                    disabled=True
                )
                self.add_item(select)
        
        async def handle_selection(self, interaction: discord.Interaction, selected_values: List[str]):
            # Check if user has ADD permission
            has_add_permission = False
            if not self.cog.ADD_ROLES:  # If empty, allow everyone
                has_add_permission = True
            else:
                for role in interaction.user.roles:
                    if str(role.id) in self.cog.ADD_ROLES:
                        has_add_permission = True
                        break
            
            if not has_add_permission:
                await interaction.response.send_message(
                    "❌ You don't have permission to add members to this list!",
                    ephemeral=True
                )
                return
            
            # Get list data
            channel_id = str(self.channel_id)
            if channel_id not in self.cog.member_lists:
                await interaction.response.send_message(
                    "❌ This list no longer exists!",
                    ephemeral=True
                )
                return
            
            list_data = self.cog.member_lists[channel_id]
            added_members = []
            skipped_members = []
            
            for member_id in selected_values:
                member_id_str = str(member_id)
                
                # Check if already in list
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
                        old_member_data = list_data['members'].pop(oldest_member_id)
                        list_data['removed_members'].append({
                            'id': oldest_member_id,
                            'name': old_member_data['name'],
                            'removed_at': datetime.now(pytz.UTC).isoformat(),
                            'removed_by': str(interaction.user.id),
                            'reason': 'auto_removed_capacity'
                        })
                
                # Add new member
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
            
            # Save data
            self.cog.save_data()
            
            # Update embed
            try:
                message = await self.channel.fetch_message(int(list_data['message_id']))
                await self.cog.update_embed(message, list_data)
                
                # Send response
                response_text = ""
                if added_members:
                    response_text += f"✅ **Added {len(added_members)} member(s) to the list:**\n"
                    for member in added_members:
                        response_text += f"• {member.mention}\n"
                
                if skipped_members:
                    response_text += f"\n⚠️ **Skipped {len(skipped_members)} member(s)** (already in list)"
                
                await interaction.response.send_message(
                    response_text or "No members were added.",
                    ephemeral=True
                )
            except Exception as e:
                await interaction.response.send_message(
                    f"❌ Error updating list: {str(e)}",
                    ephemeral=True
                )
    
    async def update_embed(self, message, list_data):
        """Update the embed with current member list"""
        members = list_data['members']
        
        # Create embed
        embed = discord.Embed(
            title="📋 Dynamic Member List",
            description="Use the dropdown below to add members to the list.",
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
                added_time = datetime.fromisoformat(member_data['added_at'])
                time_ago = self.get_time_ago(added_time)
                expires_in = self.MEMBER_EXPIRY_DAYS - (datetime.now(pytz.UTC) - added_time).days
                
                try:
                    member = message.guild.get_member(int(member_id))
                    if member:
                        members_list += f"• {member.mention} - Added {time_ago} (Expires in {expires_in} day{'s' if expires_in != 1 else ''})\n"
                    else:
                        members_list += f"• **{member_data['name']}** - Added {time_ago} (Expires in {expires_in} day{'s' if expires_in != 1 else ''})\n"
                except:
                    members_list += f"• **{member_data['name']}** - Added {time_ago} (Expires in {expires_in} day{'s' if expires_in != 1 else ''})\n"
            
            embed.add_field(
                name=f"📝 Current Members ({len(members)}/{self.MAX_MEMBERS})",
                value=members_list,
                inline=False
            )
        else:
            embed.add_field(
                name=f"📝 Current Members (0/{self.MAX_MEMBERS})",
                value="*No members in the list yet*",
                inline=False
            )
        
        # Add statistics
        stats_text = f"**Capacity:** {len(members)}/{self.MAX_MEMBERS}\n"
        stats_text += f"**Member Duration:** {self.MEMBER_EXPIRY_DAYS} days\n"
        stats_text += f"**Last updated:** <t:{int(datetime.now(pytz.UTC).timestamp())}:R>"
        
        embed.add_field(name="📊 Statistics", value=stats_text, inline=False)
        
        # Add rules
        embed.add_field(
            name="⚙️ Rules & Info",
            value=(
                "• Members stay on list for 2 days\n"
                "• Max 12 members at a time\n"
                "• Oldest members removed when full\n"
                "• Members who leave server are auto-removed\n"
                f"• Only specific roles can add members"
            ),
            inline=False
        )
        
        # Get available members (not already in list and not bots)
        available_members = [
            member for member in message.guild.members 
            if not member.bot and str(member.id) not in members
        ]
        
        # Create dropdown view
        view = self.UserSelectActionView(
            cog=self,
            channel_id=message.channel.id,
            owner_id=message.guild.owner_id,
            channel=message.channel,
            users=available_members,
            action_type="add_member",
            placeholder="Select members to add..."
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
            description="Use the dropdown below to add members to the list.",
            color=discord.Color.blue(),
            timestamp=datetime.now(pytz.UTC)
        )
        
        embed.add_field(
            name=f"📝 Current Members (0/{self.MAX_MEMBERS})",
            value="*No members in the list yet*",
            inline=False
        )
        
        stats_text = f"**Capacity:** 0/{self.MAX_MEMBERS}\n"
        stats_text += f"**Member Duration:** {self.MEMBER_EXPIRY_DAYS} days\n"
        stats_text += f"**Last updated:** <t:{int(datetime.now(pytz.UTC).timestamp())}:R>"
        
        embed.add_field(name="📊 Statistics", value=stats_text, inline=False)
        
        embed.add_field(
            name="⚙️ Rules & Info",
            value=(
                "• Members stay on list for 2 days\n"
                "• Max 12 members at a time\n"
                "• Oldest members removed when full\n"
                "• Members who leave server are auto-removed\n"
                f"• Only specific roles can add members"
            ),
            inline=False
        )
        
        # Get available members
        available_members = [
            member for member in ctx.guild.members 
            if not member.bot
        ]
        
        # Create view with dropdown
        view = self.UserSelectActionView(
            cog=self,
            channel_id=ctx.channel.id,
            owner_id=ctx.guild.owner_id,
            channel=ctx.channel,
            users=available_members,
            action_type="add_member",
            placeholder="Select members to add..."
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
    
    @commands.command(name="listinfo")
    async def list_info(self, ctx):
        """Get information about the current member list"""
        channel_id = str(ctx.channel.id)
        
        if channel_id not in self.member_lists:
            await ctx.send("❌ No member list exists in this channel!")
            return
        
        list_data = self.member_lists[channel_id]
        members = list_data['members']
        
        embed = discord.Embed(
            title="📊 Member List Information",
            color=discord.Color.green(),
            timestamp=datetime.now(pytz.UTC)
        )
        
        # Basic info
        created_at = datetime.fromisoformat(list_data['created_at'])
        embed.add_field(
            name="General",
            value=(
                f"**Created:** <t:{int(created_at.timestamp())}:R>\n"
                f"**Channel:** {ctx.channel.mention}\n"
                f"**Members:** {len(members)}/{self.MAX_MEMBERS}"
            ),
            inline=False
        )
        
        # Current members
        if members:
            current_text = ""
            for member_id, member_data in members.items():
                added_at = datetime.fromisoformat(member_data['added_at'])
                expires_in = self.MEMBER_EXPIRY_DAYS - (datetime.now(pytz.UTC) - added_at).days
                
                current_text += (
                    f"• **{member_data['display_name']}** "
                    f"(Added <t:{int(added_at.timestamp())}:R>, "
                    f"Expires in {expires_in} day{'s' if expires_in != 1 else ''})\n"
                )
            
            embed.add_field(
                name="Current Members",
                value=current_text,
                inline=False
            )
        
        # Settings
        embed.add_field(
            name="Settings",
            value=(
                f"**Max capacity:** {self.MAX_MEMBERS}\n"
                f"**Expiry days:** {self.MEMBER_EXPIRY_DAYS}\n"
                f"**Add roles required:** {len(self.ADD_ROLES)} configured"
            ),
            inline=False
        )
        
        await ctx.send(embed=embed)
    
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
    
    @commands.command(name="setaddroles")
    @commands.has_permissions(administrator=True)
    async def set_add_roles(self, ctx, *roles: discord.Role):
        """Set which roles can add members to the list (Admin only)"""
        if not roles:
            await ctx.send("❌ Please mention at least one role!")
            return
        
        self.ADD_ROLES = [str(role.id) for role in roles]
        await ctx.send(f"✅ Set add permissions to: {', '.join([role.mention for role in roles])}")

async def setup(bot):
    await bot.add_cog(DynamicMemberList(bot))