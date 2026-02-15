import asyncio
from typing import Optional
import discord
from discord.ext import commands, tasks
import os
import ast
from datetime import datetime, timedelta
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
        self.AUTO_PAIR_CHANNEL_ID = int(os.environ.get("AUTO_PAIR_CHANNEL_ID", 0))
        
        # Permission roles
        self.ADD_ROLES = ast.literal_eval(os.environ.get("ADD_NEWBIES_ROLE_ID"))
        self.REMOVE_ROLES = ast.literal_eval(os.environ.get("REMOVE_NEWBIES_ROLE_ID"))
        
        # Initialize managers
        self.data_manager = DataManager()
        self.auto_pair_system = AutoPairSystem(self)
        
        # Load data
        self.member_lists = self.data_manager.load_member_data()
        
        # Track last message updates
        self.last_message_update = {}
        
        print(f"[DynamicMemberList] Loaded {len(self.member_lists)} existing lists")
    
    async def cog_load(self):
        """Setup after cog is loaded (async version of __init__ for async setup)"""
        # Start background tasks
        self.cleanup_expired_members.start()
        self.refresh_auto_lists.start()
        self.update_sticky_message.start()
        # Initialize auto-pair system after bot is ready
        await self.auto_pair_system.initialize_after_ready()

        # Add event listeners
        self.bot.add_listener(self.auto_pair_system.on_message, 'on_message')
        self.bot.add_listener(self.on_member_update, 'on_member_update')
        self.bot.add_listener(self.on_member_remove, 'on_member_remove')
        self.bot.add_listener(self.auto_pair_system.on_member_join, 'on_member_join')
        self.bot.add_listener(self.auto_pair_system.on_member_update, 'on_member_update')
        
        # Register persistent views
        self.bot.add_view(MembersMainView(cog=self))
        
        print(f"[DynamicMemberList] Initialization complete")
    
    async def cog_unload(self):
        """Cleanup when cog is unloaded"""
        self.cleanup_expired_members.cancel()
        self.refresh_auto_lists.cancel()
        self.auto_pair_system.stop_tasks()
        self.data_manager.save_member_data(self.member_lists)
        self.update_sticky_message.cancel() 
    
    def get_staff_channel(self, guild):
        """Get the staff channel for announcements"""
        if hasattr(self, 'AUTO_PAIR_CHANNEL_ID') and self.AUTO_PAIR_CHANNEL_ID:
            return guild.get_channel(self.AUTO_PAIR_CHANNEL_ID)
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
        

    @commands.command(name="createlist")
    @commands.has_permissions(administrator=True)
    async def create_list(self, ctx):
        """Create complete newbie list system"""
        channel_id = str(ctx.channel.id)
        if channel_id in self.member_lists:
            await ctx.send("⚠️ List system already exists here! Use `.deletelist` first.")
            return
        
        list_data = {
            'auto_males_message_id': None,
            'auto_females_message_id': None,
            'manual_message_id': None,
            'plain_manual_id': None,
            'members': {},
            'removed_members': [],
            'created_at': datetime.now(pytz.UTC).isoformat(),
            'created_by': str(ctx.author.id),
            'guild_id': str(ctx.guild.id)
        }
        
        males, females = self.get_auto_newbies(ctx.guild)

        embed_males = discord.Embed(
            title="👦 Newbie Males",
            color=discord.Color.blue(),
            description="*Auto-updated list of members with Newbie + Male roles*"
        )
        msg1 = await ctx.send(embed=embed_males)
        list_data['auto_males_message_id'] = str(msg1.id)

        embed_females = discord.Embed(
            title="👧 Newbie Females",
            color=discord.Color.magenta(),
            description="*Auto-updated list of members with Newbie + Female roles*"
        )
        msg2 = await ctx.send(embed=embed_females)
        list_data['auto_females_message_id'] = str(msg2.id)

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
        from .views import MembersMainView
        view = MembersMainView(cog=self)
        msg3 = await ctx.send(embed=embed_manual, view=view)
        list_data['manual_message_id'] = str(msg3.id)

        msg4 = await ctx.send("MANUAL NEWBIES\n*No manually added newbies*")
        list_data['plain_manual_id'] = str(msg4.id)
        
        self.member_lists[channel_id] = list_data
        self.data_manager.save_member_data(self.member_lists)
        
        await ctx.send("✅ Newbie list system created!", delete_after=5)
    
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
            'auto_males_message_id',
            'auto_females_message_id',
            'manual_message_id',
            'plain_manual_id' 
        ]
        
        for key in message_ids:
            if list_data.get(key):
                try:
                    msg = await ctx.channel.fetch_message(int(list_data[key]))
                    await msg.delete()
                except:
                    pass
        
        del self.member_lists[channel_id]
        self.data_manager.save_member_data(self.member_lists)
        
        await ctx.send("✅ Newbie list system deleted!")
    
    # Auto-pair system commands
    @commands.command(name="paircomplete")
    @commands.has_permissions(administrator=True)
    async def pair_complete(self, ctx, member: discord.Member):
        """Mark a pairing as completed"""
        member_id = str(member.id)
        pairing_data = self.auto_pair_system.system_data.get("pairings", {}).get(member_id)
        
        if not pairing_data or not pairing_data.get("active", False):
            await ctx.send(f"❌ No active pairing found for {member.mention}")
            return
        
        pairing_data["completed_at"] = datetime.now(pytz.UTC).isoformat()
        pairing_data["completed_by"] = str(ctx.author.id)
        pairing_data["active"] = False
        self.auto_pair_system.data_manager.save_system_data(self.auto_pair_system.system_data)
        
        await ctx.send(f"✅ Marked pairing for {member.mention} as completed!")
    
    @commands.command(name="checkstatus")
    @commands.has_permissions(administrator=True)
    async def check_status(self, ctx, member: Optional[discord.Member] = None):
        """Check newbie status (activity and pairings)"""
        target = member or ctx.author
        
        embed = discord.Embed(
            title=f"📊 Status for {target.display_name}",
            color=discord.Color.blue(),
            timestamp=datetime.now(pytz.UTC)
        )
        
        # Activity status - get message count from activity tracking
        activity_data = self.auto_pair_system.member_activity.get(str(target.id), {})
        message_count = activity_data.get("message_count", 0)
        
        activity_status = "✅ Active" if message_count >= self.auto_pair_system.MIN_MESSAGES_FOR_ACTIVE else "⚠️ Inactive"
        embed.add_field(
            name="Activity (Last Hour)",
            value=f"{activity_status}\nMessages: {message_count}/{self.auto_pair_system.MIN_MESSAGES_FOR_ACTIVE} needed",
            inline=False
        )
        
        # Last message time
        last_message = activity_data.get("last_message")
        if last_message:
            try:
                last_msg_time = datetime.fromisoformat(last_message) if isinstance(last_message, str) else last_message
                embed.add_field(
                    name="Last Message",
                    value=f"<t:{int(last_msg_time.timestamp())}:R>",
                    inline=True
                )
            except:
                pass
        
        # Pairing status
        pairing_data = self.auto_pair_system.system_data.get("pairings", {}).get(str(target.id))
        if pairing_data:
            mod_id = pairing_data.get("mod_id")
            mod = ctx.guild.get_member(int(mod_id)) if mod_id else None
            paired_at = datetime.fromisoformat(pairing_data["paired_at"]) if "paired_at" in pairing_data else None
            
            pairing_status = "✅ Active" if pairing_data.get("active", False) else "✅ Completed"
            mod_mention = mod.mention if mod else f"<@{mod_id}>" if mod_id else "Unknown"
            
            pairing_info = f"Status: {pairing_status}\nMod: {mod_mention}"
            if paired_at:
                pairing_info += f"\nPaired: <t:{int(paired_at.timestamp())}:R>"
            
            embed.add_field(
                name="Current Pairing",
                value=pairing_info,
                inline=False
            )
        else:
            embed.add_field(
                name="Pairing Status",
                value="❌ Not currently paired",
                inline=True
            )
        
        await ctx.send(embed=embed)
    
    @commands.command(name="forcepair")
    @commands.has_permissions(administrator=True)
    async def force_pair(self, ctx, mod: discord.Member, newbie: discord.Member):
        """Force pair a mod with a newbie"""
        newbie_role = ctx.guild.get_role(self.NEWBIE_ROLE_ID)
        if not newbie_role or newbie_role not in newbie.roles:
            await ctx.send(f"❌ {newbie.mention} doesn't have the newbie role!")
            return
        
        is_mod = False
        for role_id in self.auto_pair_system.MOD_ROLES:
            role = ctx.guild.get_role(role_id)
            if role and role in mod.roles:
                is_mod = True
                break
        
        if not is_mod:
            await ctx.send(f"❌ {mod.mention} doesn't have mod permissions!")
            return
        
        now = datetime.now(pytz.UTC)
        self.auto_pair_system.system_data.setdefault("pairings", {})[str(newbie.id)] = {
            "mod_id": str(mod.id),
            "mod_name": mod.display_name,
            "newbie_id": str(newbie.id),
            "newbie_name": newbie.display_name,
            "paired_at": now.isoformat(),
            "gender_match": "manual",
            "active": True,
            "guild_id": str(ctx.guild.id),
            "forced_by": str(ctx.author.id)
        }
        self.auto_pair_system.data_manager.save_system_data(self.auto_pair_system.system_data)
        
        try:
            await mod.send(f"🤝 **Manual Pairing Assignment**\nYou've been manually paired with **{newbie.display_name}** by {ctx.author.mention}!")
        except discord.Forbidden:
            pass
        
        try:
            await newbie.send(f"👋 **You've been paired with a mentor!**\n**{mod.display_name}** will be reaching out to help you get settled in!")
        except discord.Forbidden:
            pass
        
        await ctx.send(f"✅ Successfully paired {mod.mention} with {newbie.mention}!")
    
    @commands.command(name="listpairings")
    @commands.has_permissions(administrator=True)
    async def list_pairings(self, ctx):
        """List all active pairings"""
        active_pairings = []
        
        for newbie_id, data in self.auto_pair_system.system_data.get("pairings", {}).items():
            if data.get("active", False) and data.get("guild_id") == str(ctx.guild.id):
                paired_at_str = data.get("paired_at")
                if paired_at_str:
                    try:
                        paired_at = datetime.fromisoformat(paired_at_str)
                        hours_since = (datetime.now(pytz.UTC) - paired_at).total_seconds() / 3600
                        if hours_since < 24:
                            active_pairings.append(data)
                    except:
                        active_pairings.append(data)
        
        if not active_pairings:
            await ctx.send("📭 No active pairings right now!")
            return
        
        embed = discord.Embed(
            title="🤝 Active Mentor Pairings",
            description=f"Total: {len(active_pairings)}",
            color=discord.Color.green(),
            timestamp=datetime.now(pytz.UTC)
        )
        
        for i, pairing in enumerate(active_pairings[:10], 1):
            mod_mention = f"<@{pairing['mod_id']}>"
            newbie_mention = f"<@{pairing['newbie_id']}>"
            paired_at = datetime.fromisoformat(pairing["paired_at"])
            
            embed.add_field(
                name=f"{i}. {pairing.get('newbie_name', 'Unknown')}",
                value=(
                    f"**Mod:** {mod_mention}\n"
                    f"**Newbie:** {newbie_mention}\n"
                    f"**Paired:** <t:{int(paired_at.timestamp())}:R>\n"
                    f"**Match:** {pairing.get('gender_match', 'unknown')}"
                ),
                inline=False
            )
        
        if len(active_pairings) > 10:
            embed.set_footer(text=f"And {len(active_pairings) - 10} more pairings...")
        
        await ctx.send(embed=embed)

    @commands.command(name="myassigned", aliases=["assigned"])
    @commands.has_permissions(administrator=True)
    async def my_assigned(self, ctx):
        """Show newbies assigned to you and their activity status."""
        # Check if user has any mod role
        if not any(role.id in self.auto_pair_system.MOD_ROLES for role in ctx.author.roles):
            await ctx.send("❌ You don't have permission to use this command.")
            return

        mod_id = str(ctx.author.id)
        guild_id = str(ctx.guild.id)
        pairings = self.auto_pair_system.system_data.get("pairings", {})
        active = []
        for newbie_id, data in pairings.items():
            if data.get("mod_id") == mod_id and data.get("guild_id") == guild_id and data.get("active", False):
                active.append(data)

        if not active:
            await ctx.send("📭 You have no active assignments right now.")
            return

        embed = discord.Embed(
            title=f"📋 Your Assigned Newbies ({len(active)})",
            color=discord.Color.blue(),
            timestamp=datetime.now(pytz.UTC)
        )

        for data in active:
            newbie_id = data["newbie_id"]
            newbie = ctx.guild.get_member(int(newbie_id))
            if not newbie:
                continue

            activity = self.auto_pair_system.member_activity.get(newbie_id, {})
            msg_count = activity.get("message_count", 0)
            last_msg = activity.get("last_message")
            if isinstance(last_msg, str):
                last_msg = datetime.fromisoformat(last_msg)
            last_msg_str = f"<t:{int(last_msg.timestamp())}:R>" if last_msg else "Never"

            active_threshold = self.auto_pair_system.MIN_MESSAGES_FOR_ACTIVE
            is_active = msg_count >= active_threshold
            status = "✅ Active" if is_active else "⚠️ Inactive"
            reminder = " Consider a checkup!" if not is_active else ""

            value = (f"**Status:** {status}\n"
                    f"**Messages:** {msg_count}/{active_threshold}\n"
                    f"**Last message:** {last_msg_str}{reminder}")
            embed.add_field(
                name=f"{newbie.display_name} ({newbie.id})",
                value=value,
                inline=False
            )

        await ctx.send(embed=embed)


    @commands.command(name="ignore")
    @commands.has_permissions(administrator=True)
    async def ignore_newbie(self, ctx, member: discord.Member):
        """Ignore a newbie for the next 2 cycles (12 hours) – they won't be assigned to you."""
        if not any(role.id in self.auto_pair_system.MOD_ROLES for role in ctx.author.roles):
            await ctx.send("❌ You don't have permission to use this command.")
            return

        self.auto_pair_system.add_ignore(str(ctx.author.id), str(member.id), str(ctx.guild.id), hours=12)
        await ctx.send(f"✅ You will ignore {member.mention} for the next 12 hours (2 cycles).")


    @commands.command(name="unignore")
    @commands.has_permissions(administrator=True)
    async def unignore_newbie(self, ctx, member: discord.Member):
        """Remove ignore for a newbie."""
        if not any(role.id in self.auto_pair_system.MOD_ROLES for role in ctx.author.roles):
            await ctx.send("❌ You don't have permission to use this command.")
            return

        self.auto_pair_system.remove_ignore(str(ctx.author.id), str(member.id), str(ctx.guild.id))
        await ctx.send(f"✅ Removed ignore for {member.mention}.")


    @commands.command(name="ignored")
    @commands.has_permissions(administrator=True)
    async def list_ignored(self, ctx):
        """List newbies you are currently ignoring."""
        if not any(role.id in self.auto_pair_system.MOD_ROLES for role in ctx.author.roles):
            await ctx.send("❌ You don't have permission to use this command.")
            return

        mod_id = str(ctx.author.id)
        guild_id = str(ctx.guild.id)
        ignored_list = self.auto_pair_system.system_data.get("ignored", [])
        now = datetime.now(pytz.UTC)
        active_ignores = []
        for rec in ignored_list:
            if rec["guild_id"] == guild_id and rec["mod_id"] == mod_id:
                expires = datetime.fromisoformat(rec["expires_at"])
                if expires > now:
                    newbie = ctx.guild.get_member(int(rec["newbie_id"]))
                    name = newbie.mention if newbie else f"<@{rec['newbie_id']}>"
                    active_ignores.append((name, expires))

        if not active_ignores:
            await ctx.send("You are not ignoring any newbies.")
            return

        lines = [f"{name} until <t:{int(exp.timestamp())}:R>" for name, exp in active_ignores]
        await ctx.send("**Ignored newbies:**\n" + "\n".join(lines))

    @tasks.loop(seconds=60)
    async def update_sticky_message(self):
        """Update the sticky dashboard message in each guild's staff channel."""
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            staff_channel = self.get_staff_channel(guild)
            if not staff_channel:
                continue

            guild_id = str(guild.id)
            sticky_data = self.auto_pair_system.system_data.setdefault("sticky_messages", {})
            message_id = sticky_data.get(guild_id)

            embed = self.build_sticky_embed(guild)

            try:
                if message_id:
                    try:
                        msg = await staff_channel.fetch_message(int(message_id))
                        await msg.edit(embed=embed)
                    except discord.NotFound:
                        # Message was deleted, send a new one
                        msg = await staff_channel.send(embed=embed)
                        sticky_data[guild_id] = str(msg.id)
                        self.auto_pair_system.data_manager.save_system_data(self.auto_pair_system.system_data)
                    except Exception as e:
                        print(f"Error updating sticky message in {guild.name}: {e}")
                else:
                    # First time – send a new message
                    msg = await staff_channel.send(embed=embed)
                    sticky_data[guild_id] = str(msg.id)
                    self.auto_pair_system.data_manager.save_system_data(self.auto_pair_system.system_data)
            except Exception as e:
                print(f"Error with sticky message in {guild.name}: {e}")

    def build_sticky_embed(self, guild):
        """Build the enhanced dashboard embed with 7 key sections."""
        pairings = self.auto_pair_system.system_data.get("pairings", {})
        guild_id = str(guild.id)
        now = datetime.now(pytz.UTC)
        
        # Separate active and completed pairings for this guild
        active_pairings = []
        completed_pairings = []
        for newbie_id, data in pairings.items():
            if data.get("guild_id") == guild_id:
                if data.get("active", False):
                    active_pairings.append(data)
                else:
                    completed_pairings.append(data)
        
        total_pairings = len(active_pairings) + len(completed_pairings)
        success_rate = (len(completed_pairings) / total_pairings * 100) if total_pairings > 0 else 0
        
        # Active ignore records (not expired)
        ignores = self.auto_pair_system.system_data.get("ignored", [])
        active_ignores = []
        for ign in ignores:
            if ign.get("guild_id") == guild_id:
                expires = datetime.fromisoformat(ign["expires_at"])
                if expires > now:
                    active_ignores.append(ign)
        
        # Count active assignments per mod
        mod_assignments = {}
        for pair in active_pairings:
            mod_id = pair["mod_id"]
            mod_assignments[mod_id] = mod_assignments.get(mod_id, 0) + 1
        top_mods_active = sorted(mod_assignments.items(), key=lambda x: x[1], reverse=True)[:3]
        
        # Count completed assignments per mod (lifetime)
        mod_completions = {}
        mod_response_times = {}  # For efficiency stats
        for pair in completed_pairings:
            mod_id = pair["mod_id"]
            mod_completions[mod_id] = mod_completions.get(mod_id, 0) + 1
            
            # Track response time if available (simplified)
            if "paired_at" in pair and "completed_at" in pair:
                try:
                    paired = datetime.fromisoformat(pair["paired_at"])
                    completed = datetime.fromisoformat(pair["completed_at"])
                    hours = (completed - paired).total_seconds() / 3600
                    if mod_id not in mod_response_times:
                        mod_response_times[mod_id] = []
                    mod_response_times[mod_id].append(hours)
                except:
                    pass
        
        top_mods_completed = sorted(mod_completions.items(), key=lambda x: x[1], reverse=True)[:3]
        
        # Calculate average response times for top mods
        mod_avg_times = {}
        for mod_id, times in mod_response_times.items():
            if times:
                avg_hours = sum(times) / len(times)
                mod_avg_times[mod_id] = avg_hours
        
        # Count active ignores per mod
        mod_ignores = {}
        for ign in active_ignores:
            mod_id = ign["mod_id"]
            mod_ignores[mod_id] = mod_ignores.get(mod_id, 0) + 1
        top_ignore_mods = sorted(mod_ignores.items(), key=lambda x: x[1], reverse=True)[:3]
        
        # --- NEW SECTION 1: 📈 Trend Indicators ---
        # Compare today vs yesterday (simplified using last 24h vs previous 24h)
        yesterday = now - timedelta(days=1)
        day_before = now - timedelta(days=2)
        
        new_today = 0
        new_yesterday = 0
        completed_today = 0
        completed_yesterday = 0
        
        for pair in active_pairings + completed_pairings:
            if "paired_at" in pair:
                paired = datetime.fromisoformat(pair["paired_at"])
                if paired > yesterday:
                    new_today += 1
                elif paired > day_before:
                    new_yesterday += 1
            
            if "completed_at" in pair and pair.get("completed_at"):
                completed = datetime.fromisoformat(pair["completed_at"])
                if completed > yesterday:
                    completed_today += 1
                elif completed > day_before:
                    completed_yesterday += 1
        
        new_trend = "↑" if new_today > new_yesterday else "↓" if new_today < new_yesterday else "→"
        complete_trend = "↑" if completed_today > completed_yesterday else "↓" if completed_today < completed_yesterday else "→"
        
        # --- NEW SECTION 2: ⚠️ At-Risk Newbies ---
        at_risk = []
        for pair in active_pairings:
            newbie_id = pair["newbie_id"]
            activity = self.auto_pair_system.member_activity.get(newbie_id, {})
            msg_count = activity.get("message_count", 0)
            last_msg = activity.get("last_message")
            
            if isinstance(last_msg, str):
                last_msg = datetime.fromisoformat(last_msg)
            
            days_since_join = 0
            newbie = guild.get_member(int(newbie_id))
            if newbie and newbie.joined_at:
                days_since_join = (now - newbie.joined_at.replace(tzinfo=pytz.UTC)).days
            
            # Risk factors: low messages, old join, no recent activity
            if msg_count < 5 or (last_msg and (now - last_msg).days > 1) or days_since_join > 3:
                at_risk.append((newbie_id, msg_count, days_since_join))
        
        at_risk = at_risk[:3]  # Show top 3
        
        # --- NEW SECTION 3: 🔄 Queue Status ---
        queue = self.auto_pair_system.priority_queue
        queue_count = len(queue)
        oldest_wait = "N/A"
        if queue:
            # Queue items are (score, member_id, guild_id)
            oldest_score = queue[-1][0] if queue else 0
            # Rough estimate: higher score = older/longer wait
            oldest_wait = f"{oldest_score:.1f} priority"
        
        # --- NEW SECTION 4: 📋 Recent Activity ---
        recent_events = []
        all_events = []
        
        # Collect completed pairings
        for pair in completed_pairings[-5:]:  # Last 5 completed
            if "completed_at" in pair:
                all_events.append({
                    "type": "complete",
                    "time": datetime.fromisoformat(pair["completed_at"]),
                    "mod_id": pair["mod_id"],
                    "newbie_id": pair["newbie_id"]
                })
        
        # Collect new pairings
        for pair in active_pairings[-5:]:
            if "paired_at" in pair:
                all_events.append({
                    "type": "assign",
                    "time": datetime.fromisoformat(pair["paired_at"]),
                    "mod_id": pair["mod_id"],
                    "newbie_id": pair["newbie_id"]
                })
        
        # Sort by time, newest first
        all_events.sort(key=lambda x: x["time"], reverse=True)
        recent_events = all_events[:3]  # Top 3 most recent
        
        # --- NEW SECTION 5: 🏆 Achievement Showcase ---
        # Calculate various achievements
        achievement_texts = []
        
        # Most completions this week
        week_ago = now - timedelta(days=7)
        weekly_completions = {}
        for pair in completed_pairings:
            if "completed_at" in pair:
                completed = datetime.fromisoformat(pair["completed_at"])
                if completed > week_ago:
                    mod_id = pair["mod_id"]
                    weekly_completions[mod_id] = weekly_completions.get(mod_id, 0) + 1
        
        if weekly_completions:
            top_weekly = max(weekly_completions.items(), key=lambda x: x[1])
            mod = guild.get_member(int(top_weekly[0]))
            name = mod.mention if mod else f"<@{top_weekly[0]}>"
            achievement_texts.append(f"🔥 Most Completions (Week): {name} ({top_weekly[1]})")
        
        # Fastest response (if we have data)
        if mod_avg_times:
            fastest = min(mod_avg_times.items(), key=lambda x: x[1])
            mod = guild.get_member(int(fastest[0]))
            name = mod.mention if mod else f"<@{fastest[0]}>"
            hours = fastest[1]
            achievement_texts.append(f"⚡ Fastest Response: {name} (avg {hours:.1f}h)")
        
        # Completion rate leader (among mods with at least 5 assignments)
        mod_completion_rate = {}
        for mod_id in set([p["mod_id"] for p in active_pairings + completed_pairings]):
            mod_total = mod_completions.get(mod_id, 0) + mod_assignments.get(mod_id, 0)
            if mod_total >= 5:
                rate = (mod_completions.get(mod_id, 0) / mod_total) * 100
                mod_completion_rate[mod_id] = rate
        
        if mod_completion_rate:
            top_rate = max(mod_completion_rate.items(), key=lambda x: x[1])
            mod = guild.get_member(int(top_rate[0]))
            name = mod.mention if mod else f"<@{top_rate[0]}>"
            achievement_texts.append(f"🎯 Best Completion Rate: {name} ({top_rate[1]:.0f}%)")
        
        # --- NEW SECTION 6: ⚡ Mod Efficiency Stats ---
        efficiency_texts = []
        for mod_id, count in top_mods_completed:
            mod = guild.get_member(int(mod_id))
            name = mod.mention if mod else f"<@{mod_id}>"
            
            # Completion rate
            total = mod_completions.get(mod_id, 0) + mod_assignments.get(mod_id, 0)
            rate = (mod_completions.get(mod_id, 0) / total * 100) if total > 0 else 0
            
            # Avg response time
            avg_time = mod_avg_times.get(mod_id)
            time_str = f", {avg_time:.1f}h avg" if avg_time else ""
            
            efficiency_texts.append(f"{name}: {rate:.0f}%{time_str}")
        
        # --- NEW SECTION 7: 🚨 Expiring Soon ---
        expiring_soon = []
        for pair in active_pairings:
            if "paired_at" in pair:
                paired = datetime.fromisoformat(pair["paired_at"])
                hours_old = (now - paired).total_seconds() / 3600
                if hours_old > 20:  # Expiring in next 4 hours (assuming 24h expiry)
                    expiring_soon.append((pair, 24 - hours_old))
        
        expiring_soon.sort(key=lambda x: x[1])  # Sort by hours remaining
        expiring_soon = expiring_soon[:3]  # Top 3 most urgent
        
        # --- BUILD THE EMBED ---
        embed = discord.Embed(
            title="📊 Newbie Pairing Dashboard",
            color=discord.Color.gold(),
            timestamp=now
        )
        
        # Row 1: Key stats
        embed.add_field(name="Active Pairings", value=str(len(active_pairings)), inline=True)
        embed.add_field(name="Completed Pairings", value=f"{len(completed_pairings)} ({success_rate:.1f}%)", inline=True)
        embed.add_field(name="Active Ignores", value=str(len(active_ignores)), inline=True)
        
        # Section 1: 📈 Trend Indicators
        trend_text = f"New: {new_today} ({new_trend} vs yesterday)\nCompletions: {completed_today} ({complete_trend})"
        embed.add_field(name="📈 24h Trends", value=trend_text, inline=False)
        
        # Section 2: ⚠️ At-Risk Newbies
        if at_risk:
            risk_text = ""
            for newbie_id, msg_count, days in at_risk:
                newbie = guild.get_member(int(newbie_id))
                name = newbie.mention if newbie else f"<@{newbie_id}>"
                risk_text += f"• {name}: {msg_count} msgs, {days}d old\n"
        else:
            risk_text = "No at-risk newbies! 🎉"
        embed.add_field(name="⚠️ At-Risk Newbies", value=risk_text, inline=False)
        
        # Section 3: 🔄 Queue Status
        queue_text = f"Waiting: {queue_count}\nOldest wait: {oldest_wait}"
        embed.add_field(name="🔄 Queue Status", value=queue_text, inline=False)
        
        # Section 4: 📋 Recent Activity
        if recent_events:
            activity_text = ""
            for event in recent_events:
                mod = guild.get_member(int(event["mod_id"]))
                mod_name = mod.mention if mod else f"<@{event['mod_id']}>"
                newbie = guild.get_member(int(event["newbie_id"]))
                newbie_name = newbie.mention if newbie else f"<@{event['newbie_id']}>"
                
                if event["type"] == "complete":
                    activity_text += f"• ✅ {mod_name} completed with {newbie_name}\n"
                else:
                    activity_text += f"• 🔄 {mod_name} assigned to {newbie_name}\n"
        else:
            activity_text = "No recent activity"
        embed.add_field(name="📋 Recent Activity", value=activity_text, inline=False)
        
        # Section 5: 🏆 Achievement Showcase
        achievement_value = "\n".join(achievement_texts[:3]) if achievement_texts else "No achievements yet"
        embed.add_field(name="🏆 Achievements", value=achievement_value, inline=False)
        
        # Section 6: ⚡ Mod Efficiency Stats
        efficiency_value = "\n".join(efficiency_texts[:3]) if efficiency_texts else "No efficiency data yet"
        embed.add_field(name="⚡ Top Mod Efficiency", value=efficiency_value, inline=False)
        
        # Section 7: 🚨 Expiring Soon
        if expiring_soon:
            expiring_text = ""
            for pair, hours_left in expiring_soon:
                mod = guild.get_member(int(pair["mod_id"]))
                mod_name = mod.mention if mod else f"<@{pair['mod_id']}>"
                newbie = guild.get_member(int(pair["newbie_id"]))
                newbie_name = newbie.mention if newbie else f"<@{pair['newbie_id']}>"
                expiring_text += f"• {newbie_name} with {mod_name} ({hours_left:.1f}h left)\n"
        else:
            expiring_text = "No pairings expiring soon"
        embed.add_field(name="🚨 Expiring in <4h", value=expiring_text, inline=False)
        
        # Quick command reference
        embed.add_field(
            name="📝 Quick Commands",
            value=(
                "`.myassigned` – View your assigned newbies\n"
                "`.ignore @user` – Ignore for 12h\n"
                "`.unignore @user` – Remove ignore\n"
                "`.ignored` – List your ignores\n"
                "`.paircomplete @user` – Mark pairing done\n"
                "`.checkstatus @user` – Check activity"
            ),
            inline=False
        )
        
        embed.set_footer(text="Updates every 60 seconds • Dashboard shows last 7 sections")
        return embed

    @cleanup_expired_members.before_loop
    async def before_cleanup(self):
        await self.bot.wait_until_ready()

async def setup(bot: commands.Bot):
    await bot.add_cog(DynamicMemberList(bot))