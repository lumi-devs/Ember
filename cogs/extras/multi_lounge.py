import discord
from discord.ext import commands, tasks
import asyncio
from typing import Dict, List, Optional, Tuple
import time
import re
import logging
from datetime import datetime, timedelta
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

class LoungeTier(Enum):
    NORMAL = "normal"
    POPULAR = "popular"
    PREMIUM = "premium"
    ELITE = "elite"

@dataclass
class LoungeData:
    channel_id: int
    created_at: float
    peak_users: int = 0
    total_hours: float = 0.0
    promotion_score: int = 0
    last_active: float = 0.0
    tier: LoungeTier = LoungeTier.NORMAL

class DynamicLounges(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.creating_lock = asyncio.Lock()
        self.renaming_lock = asyncio.Lock()
        self.cooldowns: Dict[int, float] = {}  # user_id: timestamp
        
        # Configuration - SET YOUR INITIAL CHANNEL ID HERE
        self.initial_channel_id = 1455864869030068285  # REPLACE THIS WITH YOUR LOUNGE3 CHANNEL ID
        
        # Lounge name format
        self.base_name = "┊ 🔊﹕₊˚⌇lounge"
        self.name_suffix = "  ꜝꜝ‹3"
        
        # System settings
        self.inactivity_timer = 300      # Base: 5 minutes for deletion
        self.creation_delay = 60        # 1 minutes delay before creating new lounge
        self.max_lounges = 15            # Maximum number of lounges
        self.anti_spam_cooldown = 30     # User cooldown in seconds
        self.min_users_per_lounge = 2    # Minimum users needed PER LOUNGE to allow next creation
        
        # Channel capacity rules
        self.base_capacity_rules = {
            3: float('inf'),  # Lounge3 has infinite capacity
            4: 8,             # Lounge4 has capacity 8
            5: 8,             # Lounge5 has capacity 8
            6: 6,             # Lounge6 has capacity 6
            7: 6,             # Lounge7 has capacity 6
            8: 4,             # Lounge8 has capacity 4
        }
        self.default_capacity = 4  # For lounges beyond defined rules
        
        # **ENHANCEMENT 1: PROMOTION SYSTEM**
        self.promotion_rules = {
            LoungeTier.NORMAL: {"min_users": 2, "capacity_boost": 0, "emoji": "🟢"},
            LoungeTier.POPULAR: {"min_users": 5, "duration_minutes": 30, "capacity_boost": 2, "emoji": "⭐"},
            LoungeTier.PREMIUM: {"min_users": 8, "duration_minutes": 60, "capacity_boost": 4, "emoji": "💎"},
            LoungeTier.ELITE: {"min_users": 10, "duration_minutes": 120, "capacity_boost": 6, "emoji": "👑"},
        }
        
        # **ENHANCEMENT 2: SMART CLEANUP**
        self.smart_cleanup_levels = {
            0: 600,   # First empty lounge: 10 minutes
            1: 300,   # Second empty lounge: 5 minutes
            2: 120,   # Third empty lounge: 2 minutes
            3: 60,    # Fourth+ empty lounge: 1 minute
        }
        
        # **ENHANCEMENT 3: ANTI-HOGGING SYSTEM**
        self.anti_hogging_rules = {
            "alone_timeout": 600,      # 10 minutes alone in lounge
            "low_activity_timeout": 1200,  # 20 minutes with 1 user
            "move_to_next": True,      # Move user to next available lounge
            "notify_user": True,       # Notify user before moving
        }
        
        # Tracking
        self.empty_channels: Dict[int, asyncio.Task] = {}
        self.pending_creations: Dict[int, asyncio.Task] = {}
        self.last_check: Dict[int, float] = {}
        self.lounge_data: Dict[int, LoungeData] = {}  # channel_id -> LoungeData
        self.user_alone_timers: Dict[int, Dict[int, float]] = {}  # guild_id -> {user_id: start_time}
        self.empty_lounge_count: Dict[int, int] = {}  # guild_id -> count of consecutive empty lounges
        
        # Statistics
        self.stats = {
            "channels_created": 0,
            "channels_deleted": 0,
            "renames_performed": 0,
            "promotions_given": 0,
            "users_moved": 0,
            "hogging_prevented": 0,
        }
        
        # Start tasks
        self.cleanup_task.start()
        self.promotion_check_task.start()
        self.anti_hogging_task.start()
    
    def cog_unload(self):
        self.cleanup_task.cancel()
        self.promotion_check_task.cancel()
        self.anti_hogging_task.cancel()
        for task in self.empty_channels.values():
            task.cancel()
        for task in self.pending_creations.values():
            task.cancel()
    
    def get_base_capacity(self, lounge_number: int) -> int:
        """Get base capacity based on lounge number"""
        return self.base_capacity_rules.get(lounge_number, self.default_capacity)
    
    def get_actual_capacity(self, lounge: discord.VoiceChannel) -> int:
        """Get actual capacity with promotion boosts"""
        lounge_number = self.parse_lounge_number(lounge.name)
        if not lounge_number:
            return 0
        
        base_capacity = self.get_base_capacity(lounge_number)
        
        # Apply promotion boost if any
        lounge_info = self.lounge_data.get(lounge.id)
        if lounge_info and lounge_info.tier != LoungeTier.NORMAL:
            boost = self.promotion_rules[lounge_info.tier]["capacity_boost"]
            if base_capacity != float('inf'):
                return base_capacity + boost
        
        return base_capacity
    
    def parse_lounge_number(self, channel_name: str) -> Optional[int]:
        """Extract lounge number from fancy name format"""
        match = re.search(r'lounge\s*(\d+)', channel_name, re.IGNORECASE)
        return int(match.group(1)) if match else None
    
    def get_lounge_name(self, number: int, tier: LoungeTier = LoungeTier.NORMAL) -> str:
        """Generate proper lounge name with format and tier emoji"""
        emoji = self.promotion_rules[tier]["emoji"]
        return f"{emoji} {self.base_name} {number}{self.name_suffix}"
    
    def is_lounge_channel(self, channel: discord.VoiceChannel) -> bool:
        """Check if a channel is a lounge channel"""
        return self.parse_lounge_number(channel.name) is not None and self.parse_lounge_number(channel.name) >= 3
    
    async def get_initial_channel(self, guild: discord.Guild) -> Optional[discord.VoiceChannel]:
        """Get the initial lounge channel by ID"""
        return guild.get_channel(self.initial_channel_id)
    
    async def get_all_lounges_sorted(self, guild: discord.Guild) -> List[discord.VoiceChannel]:
        """Get all lounge channels sorted by lounge number"""
        lounges = []
        for channel in guild.voice_channels:
            if self.is_lounge_channel(channel):
                num = self.parse_lounge_number(channel.name)
                if num is not None:
                    lounges.append((num, channel))
        
        # Sort by lounge number
        lounges.sort(key=lambda x: x[0])
        return [channel for _, channel in lounges]
    
    async def should_create_new_lounge(self, guild: discord.Guild) -> bool:
        """Check if we should create a new lounge - ALL previous must have 2+ users"""
        lounges = await self.get_all_lounges_sorted(guild)
        
        # Don't create if at max
        if len(lounges) >= self.max_lounges:
            return False
        
        # If no lounges or only Lounge3 exists
        if len(lounges) == 0:
            return False
        
        # Check if ALL lounges have at least 2 users
        for lounge in lounges:
            if len(lounge.members) < self.min_users_per_lounge:
                logger.info(f"Not creating new lounge: {lounge.name} has only {len(lounge.members)} users")
                return False
        
        # All existing lounges have at least 2 users
        return True
    
    async def rename_lounges_sequentially(self, guild: discord.Guild):
        """Rename all lounges to have sequential numbers without gaps"""
        async with self.renaming_lock:
            lounges = await self.get_all_lounges_sorted(guild)
            
            # Skip if no lounges
            if not lounges:
                return
            
            # Start from Lounge3
            target_number = 3
            
            for lounge in lounges:
                # Get current tier
                tier = LoungeTier.NORMAL
                lounge_info = self.lounge_data.get(lounge.id)
                if lounge_info:
                    tier = lounge_info.tier
                
                expected_name = self.get_lounge_name(target_number, tier)
                
                if lounge.name != expected_name:
                    try:
                        await lounge.edit(name=expected_name)
                        self.stats["renames_performed"] += 1
                        logger.info(f"Renamed {lounge.name} to {expected_name}")
                    except Exception as e:
                        logger.error(f"Failed to rename {lounge.name}: {e}")
                
                target_number += 1
    
    async def check_and_promote_lounge(self, lounge: discord.VoiceChannel):
        """Check if a lounge should be promoted to higher tier"""
        lounge_info = self.lounge_data.get(lounge.id)
        if not lounge_info:
            # Initialize lounge data
            lounge_info = LoungeData(
                channel_id=lounge.id,
                created_at=time.time(),
                last_active=time.time(),
            )
            self.lounge_data[lounge.id] = lounge_info
        
        current_users = len(lounge.members)
        
        # Update peak users
        if current_users > lounge_info.peak_users:
            lounge_info.peak_users = current_users
        
        # Update activity
        if current_users > 0:
            lounge_info.last_active = time.time()
        
        # Check for promotion
        current_tier = lounge_info.tier
        next_tier = None
        
        # Check each tier in order
        tiers = [LoungeTier.POPULAR, LoungeTier.PREMIUM, LoungeTier.ELITE]
        for tier in tiers:
            rules = self.promotion_rules[tier]
            if (current_users >= rules["min_users"] and 
                tier.value > current_tier.value):
                
                # Check duration requirement
                if "duration_minutes" in rules:
                    active_duration = (time.time() - lounge_info.last_active) / 60
                    if active_duration >= rules["duration_minutes"]:
                        next_tier = tier
                        break
                else:
                    next_tier = tier
                    break
        
        # Apply promotion if eligible
        if next_tier and next_tier != current_tier:
            lounge_info.tier = next_tier
            self.stats["promotions_given"] += 1
            
            # Update capacity based on promotion
            base_capacity = self.get_base_capacity(self.parse_lounge_number(lounge.name) or 3)
            if base_capacity != float('inf'):
                boost = self.promotion_rules[next_tier]["capacity_boost"]
                new_capacity = base_capacity + boost
                
                try:
                    await lounge.edit(user_limit=new_capacity)
                    logger.info(f"Promoted {lounge.name} to {next_tier.value} with capacity {new_capacity}")
                except Exception as e:
                    logger.error(f"Failed to update capacity for {lounge.name}: {e}")
            
            # Rename with new tier emoji
            await self.rename_lounges_sequentially(lounge.guild)
            
            # Send promotion announcement
            try:
                # Find a text channel to announce in
                for channel in lounge.guild.text_channels:
                    if "general" in channel.name.lower() or "chat" in channel.name.lower():
                        await channel.send(
                            f"🎉 **Lounge Promotion!** {lounge.mention} has been promoted to "
                            f"**{next_tier.value.upper()}** status for being consistently active!"
                        )
                        break
            except Exception as e:
                logger.error(f"Failed to send promotion announcement: {e}")
    
    async def create_new_lounge(self, guild: discord.Guild):
        """Create a new lounge channel below the last one"""
        async with self.creating_lock:
            # Check if we're already at max
            lounges = await self.get_all_lounges_sorted(guild)
            if len(lounges) >= self.max_lounges:
                return None
            
            if not lounges:
                logger.error("No lounges found to base new lounge on")
                return None
            
            # Find the highest lounge number
            highest_number = 2  # Start from 2 since Lounge3 is number 3
            for lounge in lounges:
                num = self.parse_lounge_number(lounge.name)
                if num and num > highest_number:
                    highest_number = num
            
            new_number = highest_number + 1
            new_name = self.get_lounge_name(new_number, LoungeTier.NORMAL)
            
            # Determine base capacity for new lounge
            base_capacity = self.get_base_capacity(new_number)
            
            try:
                # Get category from existing lounge
                category = lounges[0].category if lounges[0].category else None
                
                # Get position - place it below the last lounge by position
                lounges_by_pos = sorted(
                    [c for c in guild.voice_channels if self.is_lounge_channel(c)],
                    key=lambda x: x.position
                )
                if lounges_by_pos:
                    last_lounge = lounges_by_pos[-1]
                    position = last_lounge.position + 1
                else:
                    position = 0
                
                # Create the new lounge
                new_channel = await guild.create_voice_channel(
                    name=new_name,
                    category=category,
                    position=position,
                    user_limit=base_capacity if base_capacity != float('inf') else 0
                )
                
                # Initialize lounge data
                self.lounge_data[new_channel.id] = LoungeData(
                    channel_id=new_channel.id,
                    created_at=time.time(),
                    last_active=time.time(),
                )
                
                self.stats["channels_created"] += 1
                logger.info(f"Created new lounge: {new_name} with capacity {base_capacity}")
                
                # Rename all lounges to ensure sequential numbering
                await self.rename_lounges_sequentially(guild)
                
                return new_channel
                
            except Exception as e:
                logger.error(f"Failed to create lounge: {e}")
                return None
    
    async def schedule_lounge_deletion(self, channel: discord.VoiceChannel):
        """Schedule a lounge for deletion with SMART CLEANUP timing"""
        if channel.id in self.empty_channels:
            self.empty_channels[channel.id].cancel()
        
        # **ENHANCEMENT 2: Smart cleanup timing**
        guild_id = channel.guild.id
        empty_count = self.empty_lounge_count.get(guild_id, 0)
        
        # Get deletion time based on how many consecutive empty lounges
        deletion_time = self.smart_cleanup_levels.get(
            min(empty_count, len(self.smart_cleanup_levels) - 1),
            self.inactivity_timer
        )
        
        logger.info(f"Scheduling deletion for {channel.name} in {deletion_time}s (empty count: {empty_count})")
        
        async def delete_if_empty():
            try:
                await asyncio.sleep(deletion_time)
                
                # Check if still empty
                if len(channel.members) == 0:
                    try:
                        await channel.delete()
                        self.stats["channels_deleted"] += 1
                        logger.info(f"Deleted empty lounge: {channel.name}")
                        
                        # Remove from lounge data
                        self.lounge_data.pop(channel.id, None)
                        
                        # Update empty count
                        self.empty_lounge_count[guild_id] = max(
                            0, self.empty_lounge_count.get(guild_id, 0) - 1
                        )
                        
                        # Rename remaining lounges
                        await self.rename_lounges_sequentially(channel.guild)
                        
                    except Exception as e:
                        logger.error(f"Failed to delete {channel.name}: {e}")
                
            except asyncio.CancelledError:
                pass
            finally:
                self.empty_channels.pop(channel.id, None)
        
        task = asyncio.create_task(delete_if_empty())
        self.empty_channels[channel.id] = task
        
        # Update empty lounge count
        self.empty_lounge_count[guild_id] = empty_count + 1
    
    async def check_and_create_lounge_delayed(self, guild: discord.Guild, channel_id: int):
        """Check for lounge creation after delay with anti-spam"""
        await asyncio.sleep(self.creation_delay)
        
        # Check if we still need to create
        current_time = time.time()
        if current_time - self.last_check.get(channel_id, 0) < 5:
            return
        
        self.last_check[channel_id] = current_time
        
        # Check if ALL lounges have 2+ users
        if await self.should_create_new_lounge(guild):
            await self.create_new_lounge(guild)
        
        self.pending_creations.pop(channel_id, None)
    
    async def handle_member_join(self, member: discord.Member, channel: discord.VoiceChannel):
        """Handle member joining a lounge"""
        # Cancel deletion if scheduled
        if channel.id in self.empty_channels:
            self.empty_channels[channel.id].cancel()
            self.empty_channels.pop(channel.id, None)
            
            # Reset empty count for this guild
            guild_id = channel.guild.id
            if self.empty_lounge_count.get(guild_id, 0) > 0:
                self.empty_lounge_count[guild_id] = 0
        
        # Check for anti-hogging - reset alone timer
        if len(channel.members) == 1:
            # This user is now alone, start timer
            guild_id = channel.guild.id
            if guild_id not in self.user_alone_timers:
                self.user_alone_timers[guild_id] = {}
            self.user_alone_timers[guild_id][member.id] = time.time()
        else:
            # Multiple users, clear alone timers for this channel
            guild_id = channel.guild.id
            if guild_id in self.user_alone_timers:
                # Remove timers for users who are no longer alone
                for user_id in list(self.user_alone_timers[guild_id].keys()):
                    user = channel.guild.get_member(user_id)
                    if user and user.voice and user.voice.channel and user.voice.channel.id == channel.id:
                        if len(user.voice.channel.members) > 1:
                            self.user_alone_timers[guild_id].pop(user_id, None)
        
        # Check for promotion
        await self.check_and_promote_lounge(channel)
        
        # Check if we should create a new lounge
        lounges = await self.get_all_lounges_sorted(channel.guild)
        if not lounges:
            return
        
        # Get current lounge number
        current_number = self.parse_lounge_number(channel.name)
        if not current_number:
            return
        
        # Check if this is the highest lounge
        highest_number = 0
        for lounge in lounges:
            num = self.parse_lounge_number(lounge.name)
            if num and num > highest_number:
                highest_number = num
        
        # Only trigger from highest lounge
        if current_number != highest_number:
            return
        
        # Check if ALL lounges now have 2+ users
        all_have_min_users = True
        for lounge in lounges:
            if len(lounge.members) < self.min_users_per_lounge:
                all_have_min_users = False
                break
        
        if all_have_min_users:
            if channel.id not in self.pending_creations:
                task = asyncio.create_task(
                    self.check_and_create_lounge_delayed(channel.guild, channel.id)
                )
                self.pending_creations[channel.id] = task
    
    async def handle_member_leave(self, member: discord.Member, channel: discord.VoiceChannel):
        """Handle member leaving a lounge"""
        # Update anti-hogging timers
        guild_id = channel.guild.id
        if guild_id in self.user_alone_timers:
            self.user_alone_timers[guild_id].pop(member.id, None)
        
        # Schedule deletion if empty (with smart timing)
        if len(channel.members) == 0:
            await self.schedule_lounge_deletion(channel)
        
        # Cancel any pending creation for this channel
        if channel.id in self.pending_creations:
            self.pending_creations[channel.id].cancel()
            self.pending_creations.pop(channel.id, None)
        
        # Also cancel pending creations for any lounge
        # Because if one lounge drops below 2 users, we can't create new one
        lounges = await self.get_all_lounges_sorted(channel.guild)
        for lounge in lounges:
            if len(lounge.members) < self.min_users_per_lounge:
                # Cancel all pending creations if any lounge has <2 users
                for task_id in list(self.pending_creations.keys()):
                    self.pending_creations[task_id].cancel()
                    self.pending_creations.pop(task_id, None)
                break
    
    async def handle_anti_hogging(self, guild: discord.Guild):
        """Handle anti-hogging: Move users who are alone for too long"""
        if not self.anti_hogging_rules["move_to_next"]:
            return
        
        guild_id = guild.id
        if guild_id not in self.user_alone_timers:
            return
        
        current_time = time.time()
        users_to_move = []
        
        # Check each user's alone time
        for user_id, start_time in list(self.user_alone_timers[guild_id].items()):
            alone_duration = current_time - start_time
            
            user = guild.get_member(user_id)
            if not user or not user.voice or not user.voice.channel:
                self.user_alone_timers[guild_id].pop(user_id, None)
                continue
            
            channel = user.voice.channel
            if not self.is_lounge_channel(channel):
                continue
            
            # Check if user is alone
            if len(channel.members) == 1:
                if alone_duration >= self.anti_hogging_rules["alone_timeout"]:
                    users_to_move.append(user)
            # Check if lounge has low activity (1 user for too long)
            elif len(channel.members) == 1 and alone_duration >= self.anti_hogging_rules["low_activity_timeout"]:
                users_to_move.append(user)
        
        # Move users
        for user in users_to_move:
            try:
                current_channel = user.voice.channel
                
                # Find next available lounge
                lounges = await self.get_all_lounges_sorted(guild)
                current_index = -1
                
                for i, lounge in enumerate(lounges):
                    if lounge.id == current_channel.id:
                        current_index = i
                        break
                
                if current_index >= 0 and current_index + 1 < len(lounges):
                    next_lounge = lounges[current_index + 1]
                    
                    # Check if next lounge has space
                    capacity = self.get_actual_capacity(next_lounge)
                    if capacity == float('inf') or len(next_lounge.members) < capacity:
                        
                        # Notify user if enabled
                        if self.anti_hogging_rules["notify_user"]:
                            try:
                                await user.send(
                                    f"👋 You're being moved from {current_channel.name} to {next_lounge.name} "
                                    f"to keep lounges active and social!"
                                    f"Try out <#1431227839050682579> if you want to have a duo lounge!"
                                )
                            except:
                                pass  # Can't DM user, that's okay
                        
                        # Move user
                        await user.move_to(next_lounge)
                        self.stats["hogging_prevented"] += 1
                        self.stats["users_moved"] += 1
                        
                        logger.info(f"Moved user {user.name} from {current_channel.name} to {next_lounge.name}")
                        
                        # Reset their alone timer
                        if guild_id in self.user_alone_timers:
                            self.user_alone_timers[guild_id].pop(user.id, None)
                
            except Exception as e:
                logger.error(f"Failed to move user {user.name} for anti-hogging: {e}")
    
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        """Monitor voice state changes"""
        
        # Ignore bot activity
        if member.bot:
            return
        
        # Anti-spam cooldown
        current_time = time.time()
        if current_time - self.cooldowns.get(member.id, 0) < self.anti_spam_cooldown:
            return
        self.cooldowns[member.id] = current_time
        
        # Member joined a lounge channel
        if after.channel and self.is_lounge_channel(after.channel):
            await self.handle_member_join(member, after.channel)
        
        # Member left a lounge channel
        if before.channel and self.is_lounge_channel(before.channel):
            await self.handle_member_leave(member, before.channel)
        
        # Member moved between lounge channels
        if (before.channel and after.channel and 
            self.is_lounge_channel(before.channel) and
            self.is_lounge_channel(after.channel) and
            before.channel != after.channel):
            
            await self.handle_member_leave(member, before.channel)
            await self.handle_member_join(member, after.channel)
    
    @tasks.loop(minutes=5)
    async def cleanup_task(self):
        """Periodic cleanup and optimization"""
        for guild in self.bot.guilds:
            try:
                # Clean up any orphaned tasks
                current_channels = [c.id for c in await self.get_all_lounges_sorted(guild)]
                
                # Cancel deletion tasks for non-existent channels
                for channel_id in list(self.empty_channels.keys()):
                    if channel_id not in current_channels:
                        task = self.empty_channels.pop(channel_id, None)
                        if task:
                            task.cancel()
                
                # Ensure sequential naming
                await self.rename_lounges_sequentially(guild)
                
            except Exception as e:
                logger.error(f"Cleanup error in {guild.name}: {e}")
    
    @tasks.loop(minutes=2)
    async def promotion_check_task(self):
        """Check lounges for promotion eligibility"""
        for guild in self.bot.guilds:
            try:
                lounges = await self.get_all_lounges_sorted(guild)
                for lounge in lounges:
                    if len(lounge.members) > 0:  # Only check active lounges
                        await self.check_and_promote_lounge(lounge)
            except Exception as e:
                logger.error(f"Promotion check error in {guild.name}: {e}")
    
    @tasks.loop(minutes=1)
    async def anti_hogging_task(self):
        """Check for and handle users hogging lounges"""
        for guild in self.bot.guilds:
            try:
                await self.handle_anti_hogging(guild)
            except Exception as e:
                logger.error(f"Anti-hogging error in {guild.name}: {e}")
    
    @cleanup_task.before_loop
    @promotion_check_task.before_loop
    @anti_hogging_task.before_loop
    async def before_tasks(self):
        await self.bot.wait_until_ready()
    
    @commands.command(name="lounge_stats", aliases=["ls"])
    @commands.has_permissions(administrator=True)
    async def lounge_stats(self, ctx):
        """Show enhanced lounge system statistics"""
        lounges = await self.get_all_lounges_sorted(ctx.guild)
        
        embed = discord.Embed(
            title="🎯 Enhanced Lounge System Statistics",
            color=discord.Color.blue()
        )
        
        # System stats
        embed.add_field(
            name="📊 System Stats",
            value=f"**Total Lounges:** {len(lounges)}\n"
                  f"**Created:** {self.stats['channels_created']}\n"
                  f"**Deleted:** {self.stats['channels_deleted']}\n"
                  f"**Promotions:** {self.stats['promotions_given']}\n"
                  f"**Users Moved:** {self.stats['users_moved']}\n"
                  f"**Hogging Preventions:** {self.stats['hogging_prevented']}",
            inline=False
        )
        
        # Current lounges with tier status
        lounge_info = []
        creation_blocked = False
        
        for lounge in lounges:
            lounge_number = self.parse_lounge_number(lounge.name)
            if lounge_number:
                actual_capacity = self.get_actual_capacity(lounge)
                capacity_str = "∞" if actual_capacity == float('inf') else str(actual_capacity)
                members = len(lounge.members)
                
                # Get tier info
                tier = LoungeTier.NORMAL
                tier_emoji = self.promotion_rules[LoungeTier.NORMAL]["emoji"]
                lounge_data = self.lounge_data.get(lounge.id)
                if lounge_data:
                    tier = lounge_data.tier
                    tier_emoji = self.promotion_rules[tier]["emoji"]
                
                # Check if this lounge meets requirements
                meets_requirement = members >= self.min_users_per_lounge
                status = "✅" if meets_requirement else "❌"
                
                # First lounge that doesn't meet requirement blocks creation
                if not meets_requirement and not creation_blocked:
                    creation_blocked = True
                    status = "🚫"
                
                lounge_info.append(
                    f"{status} {tier_emoji} **{lounge.name}:** {members}/{capacity_str} users"
                )
        
        if lounge_info:
            status_text = "\n".join(lounge_info)
            if creation_blocked:
                status_text += "\n\n**⚠️ Creation blocked:** All lounges need 2+ users"
            else:
                status_text += "\n\n**✅ Creation ready:** All lounges have 2+ users"
            
            embed.add_field(
                name="🎭 Current Lounges",
                value=status_text,
                inline=False
            )
        
        # Enhanced Features Status
        features = (
            f"**🎖️ Promotion System:** Active\n"
            f"   - Popular (⭐): 5+ users for 30min (+2 capacity)\n"
            f"   - Premium (💎): 8+ users for 60min (+4 capacity)\n"
            f"   - Elite (👑): 10+ users for 120min (+6 capacity)\n\n"
            
            f"**🧹 Smart Cleanup:** Active\n"
            f"   - 1st empty: 10 minutes\n"
            f"   - 2nd empty: 5 minutes\n"
            f"   - 3rd empty: 2 minutes\n"
            f"   - 4th+ empty: 1 minute\n\n"
            
            f"**🚫 Anti-Hogging:** Active\n"
            f"   - Alone for 10min → Move to next lounge\n"
            f"   - Low activity for 15min → Move users\n"
        )
        
        embed.add_field(
            name="⚡ Enhanced Features",
            value=features,
            inline=False
        )
        
        # Basic Configuration
        config_info = (
            f"**Initial Channel ID:** {self.initial_channel_id}\n"
            f"**Max Lounges:** {self.max_lounges}\n"
            f"**Min Users Per Lounge:** {self.min_users_per_lounge}\n"
            f"**Rule:** ALL lounges must have {self.min_users_per_lounge}+ users to create next"
        )
        
        embed.add_field(
            name="⚙️ Basic Configuration",
            value=config_info,
            inline=False
        )
        
        await ctx.send(embed=embed)
    
    @commands.command(name="lounge_promote", aliases=["lp"])
    @commands.has_permissions(administrator=True)
    async def lounge_promote(self, ctx, lounge_number: int):
        """Manually promote a lounge to next tier"""
        lounges = await self.get_all_lounges_sorted(ctx.guild)
        
        target_lounge = None
        for lounge in lounges:
            num = self.parse_lounge_number(lounge.name)
            if num == lounge_number:
                target_lounge = lounge
                break
        
        if not target_lounge:
            await ctx.send(f"❌ Lounge {lounge_number} not found!")
            return
        
        # Get current tier
        lounge_data = self.lounge_data.get(target_lounge.id)
        current_tier = lounge_data.tier if lounge_data else LoungeTier.NORMAL
        
        # Find next tier
        tiers = [LoungeTier.NORMAL, LoungeTier.POPULAR, LoungeTier.PREMIUM, LoungeTier.ELITE]
        current_index = tiers.index(current_tier)
        
        if current_index >= len(tiers) - 1:
            await ctx.send(f"❌ {target_lounge.name} is already at the highest tier!")
            return
        
        next_tier = tiers[current_index + 1]
        
        # Manually promote
        if not lounge_data:
            lounge_data = LoungeData(
                channel_id=target_lounge.id,
                created_at=time.time(),
                last_active=time.time(),
                tier=next_tier
            )
            self.lounge_data[target_lounge.id] = lounge_data
        else:
            lounge_data.tier = next_tier
        
        # Update capacity
        base_capacity = self.get_base_capacity(lounge_number)
        if base_capacity != float('inf'):
            boost = self.promotion_rules[next_tier]["capacity_boost"]
            new_capacity = base_capacity + boost
            
            try:
                await target_lounge.edit(user_limit=new_capacity)
            except Exception as e:
                logger.error(f"Failed to update capacity: {e}")
        
        # Rename with new tier
        await self.rename_lounges_sequentially(ctx.guild)
        
        await ctx.send(f"✅ {target_lounge.name} manually promoted to **{next_tier.value.upper()}** tier!")
    
    @commands.command(name="lounge_reset", aliases=["lr"])
    @commands.has_permissions(administrator=True)
    async def lounge_reset(self, ctx):
        """Reset lounge system (delete all except Lounge3)"""
        confirm = await ctx.send(
            "⚠️ This will delete ALL lounge channels except Lounge3 and reset all data. "
            "Type `CONFIRM` to proceed."
        )
        
        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel and m.content == "CONFIRM"
        
        try:
            await self.bot.wait_for('message', timeout=30.0, check=check)
        except asyncio.TimeoutError:
            await confirm.edit(content="Reset cancelled.")
            return
        
        # Get initial channel
        initial_channel = await self.get_initial_channel(ctx.guild)
        if not initial_channel:
            await ctx.send("❌ Initial channel not found!")
            return
        
        # Delete all lounges except Lounge3
        lounges = await self.get_all_lounges_sorted(ctx.guild)
        
        deleted = 0
        for lounge in lounges:
            if lounge.id != initial_channel.id:
                try:
                    await lounge.delete()
                    deleted += 1
                    await asyncio.sleep(0.5)  # Rate limit protection
                except Exception as e:
                    logger.error(f"Failed to delete {lounge.name}: {e}")
        
        # Reset all data
        self.lounge_data = {}
        self.user_alone_timers = {}
        self.empty_lounge_count = {}
        
        # Reset stats
        self.stats = {
            "channels_created": 0,
            "channels_deleted": deleted,
            "renames_performed": 0,
            "promotions_given": 0,
            "users_moved": 0,
            "hogging_prevented": 0,
        }
        
        await ctx.send(f"✅ Reset complete. Deleted {deleted} lounges and cleared all data.")

async def setup(bot):
    await bot.add_cog(DynamicLounges(bot))