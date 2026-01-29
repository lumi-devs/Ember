import discord
from discord.ext import commands, tasks
from discord.ui import View, Button, UserSelect
import json
import os
from datetime import datetime, timedelta
import pytz

class DynamicMemberList(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.data_file = "data/member_list_data.json"
        self.ist = pytz.timezone('Asia/Kolkata')
        
        # Configuration - KEEP AS INTEGERS
        self.MAX_MEMBERS = 12
        self.MEMBER_EXPIRY_DAYS = 2
        
        # Role IDs for auto-categorization (SET THESE IN YOUR BOT CONFIG)
        self.NEWBIE_ROLE_ID = os.environ.get("NEWBIE_ROLE_ID")  # Replace with actual newbie role ID
        self.MALE_ROLE_ID = os.environ.get("MALE_ROLE_ID")    # Replace with actual male role ID
        self.FEMALE_ROLE_ID = os.environ.get("FEMALE_ROLE_ID")  # Replace with actual female role ID

        # Permission roles (manual list only)
        self.ADD_ROLES = os.environ.get("ADD_NEWBIES_ROLE_ID")
        self.REMOVE_ROLES = os.environ.get("REMOVE_NEWBIES_ROLE_ID")
        [1406324000736542911]
        
        # Load existing data
        self.member_lists = self.load_data()
        
        # Start background tasks
        self.cleanup_expired_members.start()
        self.refresh_auto_lists.start()
        
        # Register persistent views
        self.bot.add_view(self.MembersMainView(cog=self))
        
        print(f"[DynamicMemberList] Loaded {len(self.member_lists)} existing lists")
    
    def cog_unload(self):
        """Cleanup when cog is unloaded"""
        self.cleanup_expired_members.cancel()
        self.refresh_auto_lists.cancel()
        self.save_data()
    
    def load_data(self):
        """Load member list data from file"""
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, 'r') as f:
                    data = json.load(f)
                return data
            except Exception as e:
                print(f"[ERROR] Failed to load data: {e}")
                return {}
        return {}
    
    def save_data(self):
        """Save member list data to file"""
        os.makedirs(os.path.dirname(self.data_file), exist_ok=True)
        
        with open(self.data_file, 'w') as f:
            json.dump(self.member_lists, f, indent=4)
    
    # ------------------------------------------------------------
    # AUTO LIST MANAGEMENT (Newbie Detection + Categorization)
    # ------------------------------------------------------------
    
    def get_auto_newbies(self, guild):
        """Get auto-categorized newbies from guild members"""
        newbie_role = guild.get_role(self.NEWBIE_ROLE_ID)
        male_role = guild.get_role(self.MALE_ROLE_ID)
        female_role = guild.get_role(self.FEMALE_ROLE_ID)
        
        if not newbie_role:
            return [], []
        
        males = []
        females = []
        
        for member in guild.members:
            if newbie_role not in member.roles:
                continue
            
            # Skip bots
            if member.bot:
                continue
            
            if male_role and male_role in member.roles:
                males.append(member)
            elif female_role and female_role in member.roles:
                females.append(member)
            # Members without gender role are ignored (not categorized)
        
        # Sort by join date (oldest first)
        males.sort(key=lambda m: m.joined_at or datetime.now(pytz.UTC))
        females.sort(key=lambda m: m.joined_at or datetime.now(pytz.UTC))
        
        return males, females
    
    @tasks.loop(minutes=5)
    async def refresh_auto_lists(self):
        """Refresh auto-categorized newbie lists periodically"""
        await self.bot.wait_until_ready()
        
        for channel_id, list_data in list(self.member_lists.items()):
            try:
                channel = self.bot.get_channel(int(channel_id))
                if not channel or not isinstance(channel, discord.TextChannel):
                    continue
                
                guild = channel.guild
                males, females = self.get_auto_newbies(guild)
                
                # Update all messages for this list
                await self.update_all_messages(channel, list_data, males, females)
            except Exception as e:
                print(f"[ERROR] Failed to refresh auto list in channel {channel_id}: {e}")
    
    @refresh_auto_lists.before_loop
    async def before_refresh(self):
        await self.bot.wait_until_ready()
    
    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        """Detect role changes to update auto lists immediately"""
        # Check if newbie role or gender roles changed
        newbie_role = after.guild.get_role(self.NEWBIE_ROLE_ID)
        male_role = after.guild.get_role(self.MALE_ROLE_ID)
        female_role = after.guild.get_role(self.FEMALE_ROLE_ID)
        
        if not newbie_role:
            return
        
        # Check if relevant roles changed
        roles_changed = (
            (newbie_role in before.roles) != (newbie_role in after.roles) or
            (male_role and (male_role in before.roles) != (male_role in after.roles)) or
            (female_role and (female_role in before.roles) != (female_role in after.roles))
        )
        
        if not roles_changed:
            return
        
        # Refresh all lists in this guild
        for channel_id, list_data in self.member_lists.items():
            if list_data.get('guild_id') == str(after.guild.id):
                try:
                    channel = self.bot.get_channel(int(channel_id))
                    if channel:
                        males, females = self.get_auto_newbies(after.guild)
                        await self.update_all_messages(channel, list_data, males, females)
                except Exception as e:
                    print(f"[ERROR] Failed to update list after role change: {e}")
    
    @commands.Cog.listener()
    async def on_member_remove(self, member):
        """Remove from manual list when member leaves (auto list updates automatically)"""
        now = datetime.now(pytz.UTC)
        removed_from_lists = 0
        
        for channel_id, list_data in list(self.member_lists.items()):
            if 'members' in list_data and 'guild_id' in list_data:
                if int(list_data['guild_id']) == member.guild.id:
                    member_id_str = str(member.id)
                    
                    if member_id_str in list_data['members']:
                        member_data = list_data['members'].pop(member_id_str)
                        list_data['removed_members'].append({
                            'id': member_id_str,
                            'name': member_data['name'],
                            'removed_at': now.isoformat(),
                            'reason': 'left_server_immediately'
                        })
                        removed_from_lists += 1
                        self.save_data()
                        
                        try:
                            channel = self.bot.get_channel(int(channel_id))
                            if channel:
                                males, females = self.get_auto_newbies(member.guild)
                                await self.update_all_messages(channel, list_data, males, females)
                        except Exception as e:
                            print(f"Error updating list after member left: {e}")
        
        if removed_from_lists > 0:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Removed {member.name} from {removed_from_lists} manual list(s)")
    
    # ------------------------------------------------------------
    # MESSAGE UPDATERS (All 7 Messages)
    # ------------------------------------------------------------
    
    async def update_all_messages(self, channel, list_data, auto_males=None, auto_females=None):
        """Update all 7 messages for a list"""
        if auto_males is None or auto_females is None:
            auto_males, auto_females = self.get_auto_newbies(channel.guild)
        
        # 1. Update auto males embed
        if list_data.get('auto_males_message_id'):
            try:
                msg = await channel.fetch_message(int(list_data['auto_males_message_id']))
                await self.update_auto_embed(msg, auto_males, "blue", "👦 Newbie Males")
            except:
                pass
        
        # 2. Update auto females embed
        if list_data.get('auto_females_message_id'):
            try:
                msg = await channel.fetch_message(int(list_data['auto_females_message_id']))
                await self.update_auto_embed(msg, auto_females, "pink", "👧 Newbie Females")
            except:
                pass
        
        # 3. Update manual embed
        if list_data.get('manual_message_id'):
            try:
                msg = await channel.fetch_message(int(list_data['manual_message_id']))
                await self.update_manual_embed(msg, list_data)
            except:
                pass
        
        # 4. Update plain males
        if list_data.get('plain_males_id'):
            try:
                msg = await channel.fetch_message(int(list_data['plain_males_id']))
                await self.update_plain_mentions(msg, auto_males)
            except:
                pass
        
        # 5. Update plain females
        if list_data.get('plain_females_id'):
            try:
                msg = await channel.fetch_message(int(list_data['plain_females_id']))
                await self.update_plain_mentions(msg, auto_females)
            except:
                pass
        
        # 6. Update plain collected (combined)
        if list_data.get('plain_collected_id'):
            try:
                msg = await channel.fetch_message(int(list_data['plain_collected_id']))
                await self.update_plain_mentions(msg, auto_males + auto_females)
            except:
                pass
        
        # 7. Update plain manual
        if list_data.get('plain_manual_id'):
            try:
                msg = await channel.fetch_message(int(list_data['plain_manual_id']))
                await self.update_plain_manual(msg, list_data)
            except:
                pass
    
    async def update_auto_embed(self, message, members, color_name, title):
        """Update auto-categorized embed (males/females)"""
        color_map = {
            "blue": discord.Color.blue(),
            "pink": discord.Color.magenta()
        }
        color = color_map.get(color_name, discord.Color.blue())
        
        embed = discord.Embed(
            title=title,
            color=color,
            timestamp=datetime.now(pytz.UTC)
        )
        
        if members:
            member_text = ""
            for i, member in enumerate(members[:25], 1):  # Limit to 25 for embed
                member_text += f"{i}. {member.mention} (joined <t:{int(member.joined_at.timestamp())}:R>)\n"
            
            embed.description = f"**Total: {len(members)}**\n{member_text}"
            embed.set_footer(text="Auto-updated every 5 minutes • Members with Newbie + Gender roles")
        else:
            embed.description = "*No members with required roles*"
            embed.set_footer(text="Auto-updated • Requires Newbie + Gender roles")
        
        await message.edit(embed=embed)
    
    async def update_manual_embed(self, message, list_data):
        """Update manual newbie list embed (same as original implementation)"""
        members = list_data['members']
        embed = discord.Embed(
            title="📋 Manual Newbie List",
            description="Members manually added to the newbie list",
            color=discord.Color.green(),
            timestamp=datetime.now(pytz.UTC)
        )
        
        if members:
            sorted_members = sorted(
                members.items(),
                key=lambda x: datetime.fromisoformat(x[1]['added_at'])
            )
            
            member_text = ""
            for i, (member_id, member_data) in enumerate(sorted_members[:25], 1):
                member = message.guild.get_member(int(member_id))
                if member:
                    added_time = datetime.fromisoformat(member_data['added_at'])
                    member_text += f"{i}. {member.mention} (added <t:{int(added_time.timestamp())}:R>)\n"
                else:
                    member_text += f"{i}. *Unknown User* ({member_id})\n"
            
            embed.add_field(
                name=f"👥 Current Members ({len(members)}/{self.MAX_MEMBERS})",
                value=member_text,
                inline=False
            )
        else:
            embed.add_field(
                name=f"👥 Current Members (0/{self.MAX_MEMBERS})",
                value="*No manually added members*\nUse **➕ Add Members** button below",
                inline=False
            )
        
        view = self.MembersMainView(cog=self, channel=message.channel)
        await message.edit(embed=embed, view=view)
    
    async def update_plain_mentions(self, message, members):
        """Update plain text message with @mentions for easy copying"""
        if members:
            content = " ".join([m.mention for m in members])
        else:
            content = "*No members to display*"
        await message.edit(content=content)
    
    async def update_plain_manual(self, message, list_data):
        """Update plain text for manually added members"""
        members = list_data['members']
        if members:
            sorted_members = sorted(
                members.items(),
                key=lambda x: datetime.fromisoformat(x[1]['added_at'])
            )
            content = " ".join([
                message.guild.get_member(int(mid)).mention 
                for mid, _ in sorted_members 
                if message.guild.get_member(int(mid))
            ])
        else:
            content = "*No manually added members*"
        await message.edit(content=content)
    
    # ------------------------------------------------------------
    # CLEANUP TASK (Manual List Only)
    # ------------------------------------------------------------
    
    @tasks.loop(hours=1)
    async def cleanup_expired_members(self):
        """Clean up manually added members after expiry period"""
        now = datetime.now(pytz.UTC)
        expired_count = 0
        
        for channel_id, list_data in list(self.member_lists.items()):
            if 'members' in list_data:
                expired_members = []
                for member_id, member_data in list(list_data['members'].items()):
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
                            males, females = self.get_auto_newbies(channel.guild)
                            await self.update_all_messages(channel, list_data, males, females)
                    except Exception as e:
                        print(f"Error updating after cleanup: {e}")
        
        if expired_count > 0:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Cleaned up {expired_count} expired manual members")
    
    @cleanup_expired_members.before_loop
    async def before_cleanup(self):
        await self.bot.wait_until_ready()
    
    # ------------------------------------------------------------
    # BUTTONS & VIEWS (Manual List Only)
    # ------------------------------------------------------------
    
    class AddMembersButton(Button):
        def __init__(self, cog):
            super().__init__(
                label="➕ Add Newbies",
                style=discord.ButtonStyle.primary,
                custom_id="dml_add_members_button",
                emoji="👦👧",
                row=0
            )
            self.cog = cog
        
        async def callback(self, interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True)
            
            # Permission check (same as original)
            has_permission = False
            if not self.cog.ADD_ROLES:
                has_permission = True
            else:
                for role in interaction.user.roles:
                    if role.id in self.cog.ADD_ROLES:
                        has_permission = True
                        break
                if not has_permission and interaction.user.guild_permissions.administrator:
                    has_permission = True
            
            if not has_permission:
                required_roles = [f"<@&{rid}>" for rid in self.cog.ADD_ROLES] or ["None configured"]
                await interaction.followup.send(
                    f"❌ You don't have permission to add members!\n**Required roles:** {', '.join(required_roles)}",
                    ephemeral=True
                )
                return
            
            channel_id = str(interaction.channel.id)
            if channel_id not in self.cog.member_lists:
                await interaction.followup.send("❌ No list found in this channel!", ephemeral=True)
                return
            
            list_data = self.cog.member_lists[channel_id]
            
            view = discord.ui.View(timeout=300)
            view.add_item(self.cog.UserSelectDropdown(self.cog, "add", list_data))
            
            await interaction.followup.send(
                "**Select newbies to add manually:**\n*(These will stay for 2 days unless removed)*",
                view=view,
                ephemeral=True
            )
    
    class RemoveMembersButton(Button):
        def __init__(self, cog):
            super().__init__(
                label="🗑️ Remove Newbies",
                style=discord.ButtonStyle.danger,
                custom_id="dml_remove_members_button",
                emoji="❌",
                row=0
            )
            self.cog = cog
        
        async def callback(self, interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True)
            
            # Permission check (same as original)
            has_permission = interaction.user.guild_permissions.administrator
            if not has_permission and self.cog.REMOVE_ROLES:
                for role in interaction.user.roles:
                    if role.id in self.cog.REMOVE_ROLES:
                        has_permission = True
                        break
            
            if not has_permission:
                required = "Administrator" if not self.cog.REMOVE_ROLES else f"Administrator or roles: {', '.join([f'<@&{r}>' for r in self.cog.REMOVE_ROLES])}"
                await interaction.followup.send(
                    f"❌ You don't have permission to remove members!\n**Required:** {required}",
                    ephemeral=True
                )
                return
            
            channel_id = str(interaction.channel.id)
            if channel_id not in self.cog.member_lists:
                await interaction.followup.send("❌ No list found in this channel!", ephemeral=True)
                return
            
            list_data = self.cog.member_lists[channel_id]
            
            view = discord.ui.View(timeout=300)
            view.add_item(self.cog.UserSelectDropdown(self.cog, "remove", list_data))
            
            await interaction.followup.send(
                "**Select manually added newbies to remove:**",
                view=view,
                ephemeral=True
            )
    
    class RefreshButton(Button):
        def __init__(self, cog):
            super().__init__(
                label="🔄 Refresh All",
                style=discord.ButtonStyle.secondary,
                custom_id="dml_refresh_button",
                emoji="🔄",
                row=0
            )
            self.cog = cog
        
        async def callback(self, interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True)
            
            channel_id = str(interaction.channel.id)
            if channel_id not in self.cog.member_lists:
                await interaction.followup.send("❌ No list found!", ephemeral=True)
                return
            
            try:
                males, females = self.cog.get_auto_newbies(interaction.guild)
                await self.cog.update_all_messages(interaction.channel, self.cog.member_lists[channel_id], males, females)
                await interaction.followup.send("✅ All lists refreshed!", ephemeral=True)
            except Exception as e:
                await interaction.followup.send(f"❌ Error: {str(e)}", ephemeral=True)
    
    class InfoButton(Button):
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
            
            channel_id = str(interaction.channel.id)
            if channel_id not in self.cog.member_lists:
                await interaction.followup.send("❌ No list found!", ephemeral=True)
                return
            
            list_data = self.cog.member_lists[channel_id]
            auto_males, auto_females = self.cog.get_auto_newbies(interaction.guild)
            manual_count = len(list_data['members'])
            
            embed = discord.Embed(
                title="📋 Newbie List System Info",
                color=discord.Color.blue(),
                timestamp=datetime.now(pytz.UTC)
            )
            
            embed.add_field(
                name="Auto-Categorized Newbies",
                value=f"👦 Males: {len(auto_males)}\n👧 Females: {len(auto_females)}\n📊 Total: {len(auto_males) + len(auto_females)}",
                inline=False
            )
            
            embed.add_field(
                name="Manual Newbies",
                value=f"👥 Current: {manual_count}/{self.cog.MAX_MEMBERS}\n⏳ Expiry: {self.cog.MEMBER_EXPIRY_DAYS} days",
                inline=False
            )
            
            embed.add_field(
                name="How It Works",
                value=(
                    "• **Auto Lists**: Members with `Newbie` + `Male/Female` roles\n"
                    "• **Manual List**: Added via buttons (expires after 2 days)\n"
                    "• **Auto Refresh**: Every 5 minutes + on role changes"
                ),
                inline=False
            )
            
            await interaction.followup.send(embed=embed, ephemeral=True)
    
    class UserSelectDropdown(UserSelect):
        def __init__(self, cog, action_type: str, list_data: dict):
            placeholder = "Select newbies to add..." if action_type == "add" else "Select newbies to remove..."
            super().__init__(
                placeholder=placeholder,
                min_values=1,
                max_values=10,
                custom_id=f"dml_user_select_{action_type}"
            )
            self.cog = cog
            self.action_type = action_type
            self.list_data = list_data
        
        async def callback(self, interaction: discord.Interaction):
            try:
                if not interaction.response.is_done():
                    await interaction.response.defer(ephemeral=True)
            except:
                await interaction.followup.send("❌ Interaction expired.", ephemeral=True)
                return
            
            channel_id = str(interaction.channel.id)
            if channel_id not in self.cog.member_lists:
                await interaction.followup.send("❌ List no longer exists!", ephemeral=True)
                return
            
            list_data = self.cog.member_lists[channel_id]
            
            if self.action_type == "add":
                # Permission check (same as button)
                has_permission = False
                if not self.cog.ADD_ROLES:
                    has_permission = True
                else:
                    for role in interaction.user.roles:
                        if role.id in self.cog.ADD_ROLES:
                            has_permission = True
                            break
                    if not has_permission and interaction.user.guild_permissions.administrator:
                        has_permission = True
                
                if not has_permission:
                    await interaction.followup.send("❌ No permission to add members!", ephemeral=True)
                    return
                
                added = []
                skipped = []
                
                for member in self.values:
                    mid = str(member.id)
                    if mid in list_data['members']:
                        skipped.append(member)
                        continue
                    
                    # Enforce capacity limit
                    if len(list_data['members']) >= self.cog.MAX_MEMBERS:
                        # Remove oldest
                        oldest = min(list_data['members'].items(), key=lambda x: datetime.fromisoformat(x[1]['added_at']))
                        del list_data['members'][oldest[0]]
                        list_data['removed_members'].append({
                            'id': oldest[0],
                            'name': oldest[1]['name'],
                            'removed_at': datetime.now(pytz.UTC).isoformat(),
                            'reason': 'auto_removed_capacity'
                        })
                    
                    list_data['members'][mid] = {
                        'id': mid,
                        'name': member.name,
                        'display_name': member.display_name,
                        'added_at': datetime.now(pytz.UTC).isoformat(),
                        'added_by': str(interaction.user.id),
                        'added_by_name': interaction.user.name
                    }
                    added.append(member)
                
                self.cog.save_data()
                
                try:
                    males, females = self.cog.get_auto_newbies(interaction.guild)
                    await self.cog.update_all_messages(interaction.channel, list_data, males, females)
                    
                    msg = ""
                    if added:
                        msg += f"✅ Added {len(added)} newbie(s):\n" + "\n".join([f"• {m.display_name}" for m in added])
                    if skipped:
                        msg += f"\n⚠️ Skipped {len(skipped)} (already in manual list)"
                    await interaction.followup.send(msg or "No new members added.", ephemeral=True)
                except Exception as e:
                    await interaction.followup.send(f"❌ Update error: {str(e)}", ephemeral=True)
            
            elif self.action_type == "remove":
                # Permission check
                has_permission = interaction.user.guild_permissions.administrator
                if not has_permission and self.cog.REMOVE_ROLES:
                    for role in interaction.user.roles:
                        if role.id in self.cog.REMOVE_ROLES:
                            has_permission = True
                            break
                
                if not has_permission:
                    await interaction.followup.send("❌ No permission to remove members!", ephemeral=True)
                    return
                
                removed = []
                for member in self.values:
                    mid = str(member.id)
                    if mid in list_data['members']:
                        data = list_data['members'].pop(mid)
                        list_data['removed_members'].append({
                            'id': mid,
                            'name': data['name'],
                            'removed_at': datetime.now(pytz.UTC).isoformat(),
                            'removed_by': str(interaction.user.id),
                            'reason': 'admin_removed'
                        })
                        removed.append(member)
                
                if removed:
                    self.cog.save_data()
                    try:
                        males, females = self.cog.get_auto_newbies(interaction.guild)
                        await self.cog.update_all_messages(interaction.channel, list_data, males, females)
                        msg = f"✅ Removed {len(removed)} newbie(s):\n" + "\n".join([f"• {m.display_name}" for m in removed])
                        await interaction.followup.send(msg, ephemeral=True)
                    except Exception as e:
                        await interaction.followup.send(f"❌ Update error: {str(e)}", ephemeral=True)
                else:
                    await interaction.followup.send("❌ No members removed (not in manual list)", ephemeral=True)
    
    class MembersMainView(View):
        def __init__(self, cog, channel=None):
            super().__init__(timeout=None)
            self.cog = cog
            self.add_item(DynamicMemberList.AddMembersButton(cog))
            self.add_item(DynamicMemberList.RemoveMembersButton(cog))
            self.add_item(DynamicMemberList.RefreshButton(cog))
            self.add_item(DynamicMemberList.InfoButton(cog))
    
    # ------------------------------------------------------------
    # COMMANDS
    # ------------------------------------------------------------
    
    @commands.command(name="createlist")
    @commands.has_permissions(administrator=True)
    async def create_list(self, ctx):
        """Create complete newbie list system (7 messages)"""
        channel_id = str(ctx.channel.id)
        if channel_id in self.member_lists:
            await ctx.send("⚠️ List system already exists here! Use `.deletelist` first.")
            return
        
        # Create initial data structure
        list_data = {
            'auto_males_message_id': None,
            'auto_females_message_id': None,
            'manual_message_id': None,
            'plain_males_id': None,
            'plain_females_id': None,
            'plain_collected_id': None,
            'plain_manual_id': None,
            'members': {},
            'removed_members': [],
            'created_at': datetime.now(pytz.UTC).isoformat(),
            'created_by': str(ctx.author.id),
            'guild_id': str(ctx.guild.id)
        }
        
        # Get current auto newbies
        males, females = self.get_auto_newbies(ctx.guild)
        
        # 1. Auto Males Embed
        embed_males = discord.Embed(
            title="👦 Newbie Males",
            color=discord.Color.blue(),
            description="*Auto-updated list of members with Newbie + Male roles*"
        )
        msg1 = await ctx.send(embed=embed_males)
        list_data['auto_males_message_id'] = str(msg1.id)
        
        # 2. Auto Females Embed
        embed_females = discord.Embed(
            title="👧 Newbie Females",
            color=discord.Color.magenta(),
            description="*Auto-updated list of members with Newbie + Female roles*"
        )
        msg2 = await ctx.send(embed=embed_females)
        list_data['auto_females_message_id'] = str(msg2.id)
        
        # 3. Manual List Embed
        embed_manual = discord.Embed(
            title="📋 Manual Newbie List",
            description="*Manually added newbies (expire after 2 days)*",
            color=discord.Color.green()
        )
        embed_manual.add_field(
            name=f"👥 Current Members (0/{self.MAX_MEMBERS})",
            value="*No manually added members*\nUse **➕ Add Newbies** button below",
            inline=False
        )
        view = self.MembersMainView(cog=self, channel=ctx.channel)
        msg3 = await ctx.send(embed=embed_manual, view=view)
        list_data['manual_message_id'] = str(msg3.id)
        
        # 4. Plain Males
        content_males = " ".join([m.mention for m in males]) if males else "*No newbie males*"
        msg4 = await ctx.send(content_males)
        list_data['plain_males_id'] = str(msg4.id)
        
        # 5. Plain Females
        content_females = " ".join([m.mention for m in females]) if females else "*No newbie females*"
        msg5 = await ctx.send(content_females)
        list_data['plain_females_id'] = str(msg5.id)
        
        # 6. Plain Collected (Combined)
        content_collected = " ".join([m.mention for m in males + females]) if (males or females) else "*No newbies*"
        msg6 = await ctx.send(content_collected)
        list_data['plain_collected_id'] = str(msg6.id)
        
        # 7. Plain Manual
        msg7 = await ctx.send("*No manually added newbies*")
        list_data['plain_manual_id'] = str(msg7.id)
        
        # Save data
        self.member_lists[channel_id] = list_data
        self.save_data()
        
        await ctx.send("✅ Newbie list system created with 7 messages!", delete_after=5)
    
    @commands.command(name="deletelist")
    @commands.has_permissions(administrator=True)
    async def delete_list(self, ctx):
        """Delete entire newbie list system from channel"""
        channel_id = str(ctx.channel.id)
        if channel_id not in self.member_lists:
            await ctx.send("❌ No list system exists here!")
            return
        
        list_data = self.member_lists[channel_id]
        message_ids = [
            'auto_males_message_id', 'auto_females_message_id', 'manual_message_id',
            'plain_males_id', 'plain_females_id', 'plain_collected_id', 'plain_manual_id'
        ]
        
        # Attempt to delete all messages
        for key in message_ids:
            if list_data.get(key):
                try:
                    msg = await ctx.channel.fetch_message(int(list_data[key]))
                    await msg.delete()
                except:
                    pass
        
        # Remove from data
        del self.member_lists[channel_id]
        self.save_data()
        
        await ctx.send("✅ Newbie list system deleted!")
    
    @commands.command(name="setnewbieroles")
    @commands.has_permissions(administrator=True)
    async def set_newbie_roles(self, ctx, newbie_role: discord.Role, male_role: discord.Role, female_role: discord.Role):
        """Set the three required roles for auto-categorization"""
        self.NEWBIE_ROLE_ID = newbie_role.id
        self.MALE_ROLE_ID = male_role.id
        self.FEMALE_ROLE_ID = female_role.id
        
        await ctx.send(
            f"✅ Newbie roles configured:\n"
            f"• Newbie Role: {newbie_role.mention}\n"
            f"• Male Role: {male_role.mention}\n"
            f"• Female Role: {female_role.mention}"
        )
    
    @commands.command(name="listconfig")
    @commands.has_permissions(administrator=True)
    async def list_config(self, ctx):
        """Show current configuration"""
        newbie_role = ctx.guild.get_role(self.NEWBIE_ROLE_ID)
        male_role = ctx.guild.get_role(self.MALE_ROLE_ID)
        female_role = ctx.guild.get_role(self.FEMALE_ROLE_ID)
        
        embed = discord.Embed(title="⚙️ Newbie List Configuration", color=discord.Color.green())
        
        embed.add_field(
            name="Auto-Categorization Roles",
            value=(
                f"👦 Newbie Role: {newbie_role.mention if newbie_role else '❌ Not set'}\n"
                f"👦 Male Role: {male_role.mention if male_role else '❌ Not set'}\n"
                f"👧 Female Role: {female_role.mention if female_role else '❌ Not set'}"
            ),
            inline=False
        )
        
        embed.add_field(
            name="Manual List Settings",
            value=(
                f"👥 Max Members: {self.MAX_MEMBERS}\n"
                f"⏳ Expiry: {self.MEMBER_EXPIRY_DAYS} days\n"
                f"🔑 Add Roles: {', '.join([f'<@&{r}>' for r in self.ADD_ROLES]) or 'Everyone'}"
            ),
            inline=False
        )
        
        embed.add_field(
            name="System Status",
            value=(
                f"📊 Active Lists: {len(self.member_lists)}\n"
                f"🔄 Auto Refresh: Every 5 minutes\n"
                f"🧹 Cleanup: Hourly"
            ),
            inline=False
        )
        
        await ctx.send(embed=embed)
    
    @commands.command(name="forceremove")
    @commands.has_permissions(administrator=True)
    async def force_remove(self, ctx, member: discord.Member):
        """Force remove member from manual list"""
        channel_id = str(ctx.channel.id)
        if channel_id not in self.member_lists:
            await ctx.send("❌ No list system here!")
            return
        
        list_data = self.member_lists[channel_id]
        mid = str(member.id)
        
        if mid not in list_data['members']:
            await ctx.send(f"❌ {member.mention} not in manual list!")
            return
        
        data = list_data['members'].pop(mid)
        list_data['removed_members'].append({
            'id': mid,
            'name': data['name'],
            'removed_at': datetime.now(pytz.UTC).isoformat(),
            'removed_by': str(ctx.author.id),
            'reason': 'admin_force_remove'
        })
        self.save_data()
        
        males, females = self.get_auto_newbies(ctx.guild)
        await self.update_all_messages(ctx.channel, list_data, males, females)
        await ctx.send(f"✅ Force removed {member.mention} from manual list")

async def setup(bot):
    await bot.add_cog(DynamicMemberList(bot))