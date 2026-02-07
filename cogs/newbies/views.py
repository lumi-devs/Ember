# UI buttons and views
import discord
from discord.ui import View, Button, UserSelect
from datetime import datetime
import pytz

class AddMembersButton(Button):
    def __init__(self, cog):
        super().__init__(
            label="➕ Add Newbies",
            style=discord.ButtonStyle.primary,
            custom_id="dml_add_members_button",
            emoji="👥",
            row=0
        )
        self.cog = cog
    
    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
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
        view.add_item(UserSelectDropdown(self.cog, "add", list_data))
        
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
        view.add_item(UserSelectDropdown(self.cog, "remove", list_data))
        
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
                
                if len(list_data['members']) >= self.cog.MAX_MEMBERS:
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
            
            self.cog.data_manager.save_member_data(self.cog.member_lists)
            
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
                self.cog.data_manager.save_member_data(self.cog.member_lists)
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
        self.add_item(AddMembersButton(cog))
        self.add_item(RemoveMembersButton(cog))
        self.add_item(RefreshButton(cog))
        self.add_item(InfoButton(cog))