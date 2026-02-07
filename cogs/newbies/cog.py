# Main cog that ties everything together
import asyncio
import discord
from discord.ext import commands, tasks
import os
import ast
from datetime import datetime
import pytz

from .data_manager import DataManager
from .auto_pair import AutoPairSystem
from .views import MembersMainView

class DynamicMemberList(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        
        # Configuration
        self.MAX_MEMBERS = 12
        self.MEMBER_EXPIRY_DAYS = 2
        self.MIN_UPDATE_INTERVAL = 30
        
        # Role IDs from environment
        self.NEWBIE_ROLE_ID = int(os.environ.get("NEWBIE_ROLE_ID"))
        self.MALE_ROLE_ID = int(os.environ.get("MALE_ROLE_ID"))
        self.FEMALE_ROLE_ID = int(os.environ.get("FEMALE_ROLE_ID"))
        
        # Channel IDs from environment
        self.STAFF_CHANNEL_ID = int(os.environ.get("STAFF_CHANNEL_ID", 0))
        
        # Permission roles
        self.ADD_ROLES = ast.literal_eval(os.environ.get("ADD_NEWBIES_ROLE_ID"))
        self.REMOVE_ROLES = ast.literal_eval(os.environ.get("REMOVE_NEWBIES_ROLE_ID"))
        
        # Initialize managers
        self.data_manager = DataManager()
        self.auto_pair_system = AutoPairSystem(self)
        
        # Load data
        self.member_lists = self.data_manager.load_member_data()
        
        # Start background tasks
        self.cleanup_expired_members.start()
        self.refresh_auto_lists.start()
        self.bot.add_listener(self.auto_pair_system.on_message, 'on_message')
        self.bot.add_listener(self.on_member_update, 'on_member_update')
        self.bot.add_listener(self.on_member_remove, 'on_member_remove')
        self.bot.add_listener(self.auto_pair_system.on_member_join, 'on_member_join')
        self.bot.add_listener(self.auto_pair_system.on_member_update, 'on_member_update')
        
        # Register persistent views
        self.bot.add_view(MembersMainView(cog=self))
        
        print(f"[DynamicMemberList] Loaded {len(self.member_lists)} existing lists")
    
    def cog_unload(self):
        """Cleanup when cog is unloaded"""
        self.cleanup_expired_members.cancel()
        self.refresh_auto_lists.cancel()
        self.auto_pair_system.stop_tasks()
        self.data_manager.save_member_data(self.member_lists)
    
    def get_staff_channel(self, guild):
        """Get the staff channel for announcements"""
        if hasattr(self, 'STAFF_CHANNEL_ID') and self.STAFF_CHANNEL_ID:
            return guild.get_channel(self.STAFF_CHANNEL_ID)
        return None
    
    # ------------------------------------------------------------
    # AUTO LIST MANAGEMENT
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
            
            if member.bot:
                continue
            
            if male_role and male_role in member.roles:
                males.append(member)
            elif female_role and female_role in member.roles:
                females.append(member)
        
        males.sort(key=lambda m: m.joined_at or datetime.now(pytz.UTC))
        females.sort(key=lambda m: m.joined_at or datetime.now(pytz.UTC))
        
        return males, females
    
    async def needs_update(self, channel, list_data, new_males, new_females):
        """Check if messages actually need updating"""
        try:
            # Get current male embed
            if list_data.get('auto_males_message_id'):
                msg = await channel.fetch_message(int(list_data['auto_males_message_id']))
                if msg.embeds:
                    embed = msg.embeds[0]
                    # Check if count matches
                    lines = embed.description.split('\n')
                    if lines and 'Total:' in lines[0]:
                        current_count = int(lines[0].split('Total: ')[1].split(')')[0])
                        if current_count == len(new_males):
                            return False
            
            # Get current female embed  
            if list_data.get('auto_females_message_id'):
                msg = await channel.fetch_message(int(list_data['auto_females_message_id']))
                if msg.embeds:
                    embed = msg.embeds[0]
                    lines = embed.description.split('\n')
                    if lines and 'Total:' in lines[0]:
                        current_count = int(lines[0].split('Total: ')[1].split(')')[0])
                        if current_count == len(new_females):
                            return False
        except:
            pass
        
        return True

    @tasks.loop(minutes=5)
    async def refresh_auto_lists(self):
        """Refresh auto-categorized newbie lists periodically"""
        await self.bot.wait_until_ready()
        
        for channel_id, list_data in list(self.member_lists.items()):
            try:
                # Check rate limit
                now = datetime.now(pytz.UTC).timestamp()
                last_update = self.last_message_update.get(channel_id, 0)
                if now - last_update < self.MIN_UPDATE_INTERVAL:
                    continue  # Skip if updated recently
                
                channel = self.bot.get_channel(int(channel_id))
                if not channel or not isinstance(channel, discord.TextChannel):
                    continue
                
                guild = channel.guild
                males, females = self.get_auto_newbies(guild)
                
                # Check if actually needs update
                if not await self.needs_update(channel, list_data, males, females):
                    continue
                
                await self.update_all_messages(channel, list_data, males, females)
                self.last_message_update[channel_id] = now
                
            except Exception as e:
                print(f"[ERROR] Failed to refresh auto list in channel {channel_id}: {e}")
    
    @refresh_auto_lists.before_loop
    async def before_refresh(self):
        await self.bot.wait_until_ready()
    
    # ------------------------------------------------------------
    # MESSAGE UPDATERS
    # ------------------------------------------------------------
    
    async def update_all_messages(self, channel, list_data, auto_males=None, auto_females=None):
        """Update all messages for a list"""
        if auto_males is None or auto_females is None:
            auto_males, auto_females = self.get_auto_newbies(channel.guild)
        
        # Update with error handling for rate limits
        messages_to_update = []
        
        # Auto males
        if list_data.get('auto_males_message_id'):
            try:
                msg = await channel.fetch_message(int(list_data['auto_males_message_id']))
                messages_to_update.append((msg, auto_males, "blue", "👦 Newbie Males"))
            except discord.errors.HTTPException as e:
                if e.status == 429:
                    print(f"[RATE LIMIT] Skipping auto males update in {channel.id}")
        
        # Auto females
        if list_data.get('auto_females_message_id'):
            try:
                msg = await channel.fetch_message(int(list_data['auto_females_message_id']))
                messages_to_update.append((msg, auto_females, "pink", "👧 Newbie Females"))
            except discord.errors.HTTPException as e:
                if e.status == 429:
                    print(f"[RATE LIMIT] Skipping auto females update in {channel.id}")
        
        # Manual embed
        if list_data.get('manual_message_id'):
            try:
                msg = await channel.fetch_message(int(list_data['manual_message_id']))
                await self.update_manual_embed(msg, list_data)
            except discord.errors.HTTPException as e:
                if e.status == 429:
                    print(f"[RATE LIMIT] Skipping manual embed update in {channel.id}")
        
        # Plain manual
        if list_data.get('plain_manual_id'):
            try:
                msg = await channel.fetch_message(int(list_data['plain_manual_id']))
                await self.update_plain_manual(msg, list_data)
            except discord.errors.HTTPException as e:
                if e.status == 429:
                    print(f"[RATE LIMIT] Skipping plain manual update in {channel.id}")
        
        # Update auto embeds with delay
        for msg, members, color_name, title in messages_to_update:
            try:
                await self.update_auto_embed(msg, members, color_name, title)
                await asyncio.sleep(1)  # 1 second delay between updates
            except discord.errors.HTTPException as e:
                if e.status == 429:
                    print(f"[RATE LIMIT] Could not update {title} in {channel.id}")
                    retry_after = e.retry_after if hasattr(e, 'retry_after') else 5
                    await asyncio.sleep(retry_after)
    
    async def update_auto_embed(self, message, members, color_name, title):
        """Update auto-categorized embed"""
        try:
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
                for i, member in enumerate(members[:25], 1):
                    member_text += f"{i}. {member.mention}\n"
                
                embed.description = f"**Total: {len(members)}**\n{member_text}"
                embed.set_footer(text="Auto-updated every 5 minutes")
            else:
                embed.description = "*No members with required roles*"
                embed.set_footer(text="Auto-updated • Requires Newbie + Gender roles")
            
            await message.edit(embed=embed)
        except discord.errors.NotFound:
            print(f"[WARNING] Message for {title} not found, may have been deleted")
        except discord.errors.HTTPException as e:
            if e.status == 429:
                print(f"[RATE LIMIT] Could not update {title} embed")
                raise
    
    async def update_manual_embed(self, message, list_data):
        """Update manual newbie list embed"""
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
        
        view = MembersMainView(cog=self, channel=message.channel)
        await message.edit(embed=embed, view=view)
    
    async def update_plain_manual(self, message, list_data):
        """Update plain text for manually added members"""
        members = list_data['members']
        if members:
            sorted_members = sorted(
                members.items(),
                key=lambda x: datetime.fromisoformat(x[1]['added_at'])
            )
            mentions = " ".join([
                message.guild.get_member(int(mid)).mention 
                for mid, _ in sorted_members 
                if message.guild.get_member(int(mid))
            ])
            content = f"MANUAL NEWBIES\n{mentions}"
        else:
            content = "MANUAL NEWBIES\n*No manually added members*"
        await message.edit(content=content)
    
    # ------------------------------------------------------------
    # CLEANUP TASK
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
                    self.data_manager.save_member_data(self.member_lists)
                    try:
                        channel = self.bot.get_channel(int(channel_id))
                        if channel:
                            males, females = self.get_auto_newbies(channel.guild)
                            await self.update_all_messages(channel, list_data, males, females)
                    except Exception as e:
                        print(f"Error updating after cleanup: {e}")
        
        if expired_count > 0:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Cleaned up {expired_count} expired manual members")


    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        """Detect role changes to update auto lists immediately"""
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
        """Remove from manual list when member leaves"""
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
                        self.data_manager.save_member_data(self.member_lists)
                        
                        try:
                            channel = self.bot.get_channel(int(channel_id))
                            if channel:
                                males, females = self.get_auto_newbies(member.guild)
                                await self.update_all_messages(channel, list_data, males, females)
                        except Exception as e:
                            print(f"Error updating list after member left: {e}")
        
        if removed_from_lists > 0:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Removed {member.name} from {removed_from_lists} manual list(s)")
        
    @cleanup_expired_members.before_loop
    async def before_cleanup(self):
        await self.bot.wait_until_ready()