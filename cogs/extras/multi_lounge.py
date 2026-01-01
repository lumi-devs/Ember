import discord
from discord.ext import commands, tasks
import asyncio
from typing import Dict, List, Optional
import time
import re
import logging

logger = logging.getLogger(__name__)

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
        self.inactivity_timer = 300  # 5 minutes for deletion
        self.creation_delay = 60    # 1 minute delay before creating new lounge
        self.max_lounges = 15        # Maximum number of lounges
        self.anti_spam_cooldown = 30  # User cooldown in seconds
        self.min_users_to_create = 2  # Minimum users needed to trigger creation
        
        # Channel capacity rules (lounge number -> capacity)
        self.capacity_rules = {
            3: float('inf'),  # Lounge3 has infinite capacity
            4: 8,             # Lounge4 has capacity 8
            5: 8,             # Lounge5 has capacity 8
            6: 6,             # Lounge6 has capacity 6
            7: 6,             # Lounge7 has capacity 6
            8: 4,             # Lounge8 has capacity 4
            9: 4,             # Lounge9 has capacity 4
            10: 4,            # Lounge10 has capacity 4
        }
        self.default_capacity = 4  # For lounges beyond defined rules
        
        # Tracking
        self.empty_channels: Dict[int, asyncio.Task] = {}
        self.pending_creations: Dict[int, asyncio.Task] = {}
        self.last_creation_check: Dict[int, float] = {}
        
        # Statistics
        self.stats = {
            "channels_created": 0,
            "channels_deleted": 0,
            "renames_performed": 0
        }
        
        # Start cleanup task
        self.cleanup_task.start()
    
    def cog_unload(self):
        self.cleanup_task.cancel()
        for task in self.empty_channels.values():
            task.cancel()
        for task in self.pending_creations.values():
            task.cancel()
    
    def get_capacity_for_lounge(self, lounge_number: int) -> int:
        """Get capacity based on lounge number"""
        return self.capacity_rules.get(lounge_number, self.default_capacity)
    
    def parse_lounge_number(self, channel_name: str) -> Optional[int]:
        """Extract lounge number from fancy name format"""
        # Match patterns like "┊ 🔊﹕₊˚⌇lounge 3  ꜝꜝ‹3"
        match = re.search(r'lounge\s*(\d+)', channel_name, re.IGNORECASE)
        return int(match.group(1)) if match else None
    
    def get_lounge_name(self, number: int) -> str:
        """Generate proper lounge name with format"""
        return f"{self.base_name} {number}{self.name_suffix}"
    
    def is_lounge_channel(self, channel: discord.VoiceChannel) -> bool:
        """Check if a channel is a lounge channel"""
        return self.parse_lounge_number(channel.name) is not None and self.parse_lounge_number(channel.name) >= 3
    
    async def get_initial_channel(self, guild: discord.Guild) -> Optional[discord.VoiceChannel]:
        """Get the initial lounge channel by ID"""
        return guild.get_channel(self.initial_channel_id)
    
    async def get_all_lounges(self, guild: discord.Guild) -> List[discord.VoiceChannel]:
        """Get all lounge channels sorted by position (top to bottom)"""
        lounges = []
        for channel in guild.voice_channels:
            if self.is_lounge_channel(channel):
                lounges.append(channel)
        
        # Sort by position (top to bottom in Discord)
        lounges.sort(key=lambda x: x.position)
        return lounges
    
    async def get_sorted_lounges_by_number(self, guild: discord.Guild) -> List[discord.VoiceChannel]:
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
    
    async def should_create_new_lounge(self, guild: discord.Guild, current_channel: discord.VoiceChannel) -> bool:
        """Check if we should create a new lounge"""
        # Get all lounges sorted by position
        lounges = await self.get_all_lounges(guild)
        
        # Don't create if we're at max
        if len(lounges) >= self.max_lounges:
            return False
        
        # Check if current channel has at least 2 users
        if len(current_channel.members) < self.min_users_to_create:
            return False
        
        # Get current channel's lounge number
        current_number = self.parse_lounge_number(current_channel.name)
        if not current_number:
            return False
        
        # Find the highest lounge number
        highest_number = current_number
        for lounge in lounges:
            num = self.parse_lounge_number(lounge.name)
            if num and num > highest_number:
                highest_number = num
        
        # Check if highest lounge has users
        highest_lounge = None
        for lounge in lounges:
            if self.parse_lounge_number(lounge.name) == highest_number:
                highest_lounge = lounge
                break
        
        # Only create if highest lounge has users
        if highest_lounge and len(highest_lounge.members) > 0:
            # Check if highest lounge is at or near capacity
            capacity = self.get_capacity_for_lounge(highest_number)
            current_members = len(highest_lounge.members)
            
            # If highest lounge has capacity left, don't create
            if current_members < capacity:
                return False
            
            return True
        
        return False
    
    async def rename_lounges_sequentially(self, guild: discord.Guild):
        """Rename all lounges to have sequential numbers without gaps"""
        async with self.renaming_lock:
            lounges = await self.get_sorted_lounges_by_number(guild)
            
            # Skip if no lounges
            if not lounges:
                return
            
            # Start from Lounge3
            target_number = 3
            
            for lounge in lounges:
                expected_name = self.get_lounge_name(target_number)
                
                if lounge.name != expected_name:
                    try:
                        await lounge.edit(name=expected_name)
                        self.stats["renames_performed"] += 1
                        logger.info(f"Renamed {lounge.name} to {expected_name}")
                    except Exception as e:
                        logger.error(f"Failed to rename {lounge.name}: {e}")
                
                target_number += 1
    
    async def create_new_lounge(self, guild: discord.Guild, base_channel: discord.VoiceChannel):
        """Create a new lounge channel below the last one"""
        async with self.creating_lock:
            # Check if we're already at max
            lounges = await self.get_all_lounges(guild)
            if len(lounges) >= self.max_lounges:
                return None
            
            # Find the highest lounge number
            highest_number = 2  # Start from 2 since Lounge3 is number 3
            for lounge in lounges:
                num = self.parse_lounge_number(lounge.name)
                if num and num > highest_number:
                    highest_number = num
            
            new_number = highest_number + 1
            new_name = self.get_lounge_name(new_number)
            
            # Determine capacity for new lounge
            capacity = self.get_capacity_for_lounge(new_number)
            
            try:
                # Get category from base channel
                category = base_channel.category
                
                # Get position - place it below the last lounge
                if lounges:
                    # Find the lounge with highest position
                    last_lounge = max(lounges, key=lambda x: x.position)
                    position = last_lounge.position + 1
                else:
                    position = 0
                
                # Create the new lounge
                new_channel = await guild.create_voice_channel(
                    name=new_name,
                    category=category,
                    position=position,
                    user_limit=capacity if capacity != float('inf') else 0
                )
                
                self.stats["channels_created"] += 1
                logger.info(f"Created new lounge: {new_name} with capacity {capacity}")
                
                # Rename all lounges to ensure sequential numbering
                await self.rename_lounges_sequentially(guild)
                
                return new_channel
                
            except Exception as e:
                logger.error(f"Failed to create lounge: {e}")
                return None
    
    async def schedule_lounge_deletion(self, channel: discord.VoiceChannel):
        """Schedule a lounge for deletion if it stays empty"""
        if channel.id in self.empty_channels:
            self.empty_channels[channel.id].cancel()
        
        async def delete_if_empty():
            try:
                await asyncio.sleep(self.inactivity_timer)
                
                # Check if still empty
                if len(channel.members) == 0:
                    try:
                        await channel.delete()
                        self.stats["channels_deleted"] += 1
                        logger.info(f"Deleted empty lounge: {channel.name}")
                        
                        # Rename remaining lounges
                        await self.rename_lounges_sequentials(channel.guild)
                        
                    except Exception as e:
                        logger.error(f"Failed to delete {channel.name}: {e}")
                
            except asyncio.CancelledError:
                pass
            finally:
                self.empty_channels.pop(channel.id, None)
        
        task = asyncio.create_task(delete_if_empty())
        self.empty_channels[channel.id] = task
    
    async def check_and_create_lounge_delayed(self, guild: discord.Guild, channel_id: int):
        """Check for lounge creation after delay with anti-spam"""
        await asyncio.sleep(self.creation_delay)
        
        # Check if we still need to create
        lounge = guild.get_channel(channel_id)
        if not lounge:
            return
        
        current_time = time.time()
        if current_time - self.last_creation_check.get(channel_id, 0) < 5:
            return
        
        self.last_creation_check[channel_id] = current_time
        
        # Check if we should create a new lounge
        if await self.should_create_new_lounge(guild, lounge):
            await self.create_new_lounge(guild, lounge)
        
        self.pending_creations.pop(channel_id, None)
    
    async def handle_member_join(self, member: discord.Member, channel: discord.VoiceChannel):
        """Handle member joining a lounge"""
        # Cancel deletion if scheduled
        if channel.id in self.empty_channels:
            self.empty_channels[channel.id].cancel()
            self.empty_channels.pop(channel.id, None)
        
        # Check if we should create a new lounge
        if len(channel.members) >= self.min_users_to_create:
            # Get lounge number
            lounge_number = self.parse_lounge_number(channel.name)
            if not lounge_number:
                return
            
            # Check if this is the highest numbered lounge
            lounges = await self.get_sorted_lounges_by_number(channel.guild)
            if not lounges:
                return
            
            highest_lounge = lounges[-1]
            highest_number = self.parse_lounge_number(highest_lounge.name)
            
            # If this is the highest lounge and has enough users, schedule creation
            if lounge_number == highest_number:
                if channel.id not in self.pending_creations:
                    task = asyncio.create_task(
                        self.check_and_create_lounge_delayed(channel.guild, channel.id)
                    )
                    self.pending_creations[channel.id] = task
    
    async def handle_member_leave(self, member: discord.Member, channel: discord.VoiceChannel):
        """Handle member leaving a lounge"""
        # Schedule deletion if empty
        if len(channel.members) == 0:
            await self.schedule_lounge_deletion(channel)
        
        # Cancel any pending creation for this channel
        if channel.id in self.pending_creations:
            self.pending_creations[channel.id].cancel()
            self.pending_creations.pop(channel.id, None)
    
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
                current_channels = [c.id for c in await self.get_all_lounges(guild)]
                
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
    
    @cleanup_task.before_loop
    async def before_cleanup(self):
        await self.bot.wait_until_ready()
    
    @commands.command(name="loungestats", aliases=["ls"])
    @commands.has_permissions(administrator=True)
    async def lounge_stats(self, ctx):
        """Show lounge system statistics"""
        lounges = await self.get_sorted_lounges_by_number(ctx.guild)
        
        embed = discord.Embed(
            title="🎯 Lounge System Statistics",
            color=discord.Color.blue()
        )
        
        # System stats
        embed.add_field(
            name="📊 System",
            value=f"**Total Lounges:** {len(lounges)}\n"
                  f"**Created:** {self.stats['channels_created']}\n"
                  f"**Deleted:** {self.stats['channels_deleted']}\n"
                  f"**Renames:** {self.stats['renames_performed']}",
            inline=True
        )
        
        # Current lounges with fancy names
        lounge_info = []
        for lounge in lounges:
            lounge_number = self.parse_lounge_number(lounge.name)
            if lounge_number:
                capacity = self.get_capacity_for_lounge(lounge_number)
                capacity_str = "∞" if capacity == float('inf') else str(capacity)
                members = len(lounge.members)
                status = "🟢" if members > 0 else "⚫"
                lounge_info.append(
                    f"{status} **{lounge.name}:** {members}/{capacity_str}"
                )
        
        if lounge_info:
            embed.add_field(
                name="🎭 Current Lounges",
                value="\n".join(lounge_info),
                inline=False
            )
        else:
            embed.add_field(
                name="🎭 Current Lounges",
                value="No lounges found",
                inline=False
            )
        
        # Configuration
        config_info = (
            f"**Initial Channel ID:** {self.initial_channel_id}\n"
            f"**Max Lounges:** {self.max_lounges}\n"
            f"**Min Users to Create:** {self.min_users_to_create}\n"
            f"**Inactivity Timer:** {self.inactivity_timer//60}m\n"
            f"**Creation Delay:** {self.creation_delay}s\n"
            f"**Anti-spam:** {self.anti_spam_cooldown}s"
        )
        
        embed.add_field(
            name="⚙️ Configuration",
            value=config_info,
            inline=False
        )
        
        await ctx.send(embed=embed)
    
    @commands.command(name="lr", aliases=["lr"])
    @commands.has_permissions(administrator=True)
    async def lounge_reset(self, ctx):
        """Reset lounge system (delete all except Lounge3)"""
        confirm = await ctx.send(
            "⚠️ This will delete ALL lounge channels except Lounge3. "
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
        lounges = await self.get_all_lounges(ctx.guild)
        
        deleted = 0
        for lounge in lounges:
            if lounge.id != initial_channel.id:
                try:
                    await lounge.delete()
                    deleted += 1
                    await asyncio.sleep(0.5)  # Rate limit protection
                except Exception as e:
                    logger.error(f"Failed to delete {lounge.name}: {e}")
        
        # Reset stats
        self.stats = {
            "channels_created": 0,
            "channels_deleted": deleted,
            "renames_performed": 0
        }
        
        await ctx.send(f"✅ Reset complete. Deleted {deleted} lounges.")

async def setup(bot):
    await bot.add_cog(DynamicLounges(bot))