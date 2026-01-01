import discord
from discord.ext import commands, tasks
import asyncio
from typing import Dict, List, Optional, Tuple, Set
import time
import re
import logging
from dataclasses import dataclass
from enum import Enum
from collections import defaultdict

logger = logging.getLogger(__name__)

class LoungeTier(Enum):
    NORMAL = "normal"
    POPULAR = "popular"
    PREMIUM = "premium"
    ELITE = "elite"

@dataclass
class LoungeEntry:
    """Atomic lounge entry with all state"""
    channel_id: int
    guild_id: int
    number: int
    tier: LoungeTier = LoungeTier.NORMAL
    deletion_task: Optional[asyncio.Task] = None
    deletion_scheduled: Optional[float] = None
    created_at: float = 0.0
    last_active: float = 0.0
    peak_users: int = 0
    metadata: Dict = None

class AtomicLoungeManager:
    """Atomic state manager for lounges"""
    
    def __init__(self):
        self._lock = asyncio.Lock()
        self._lounges: Dict[int, LoungeEntry] = {}  # channel_id -> LoungeEntry
        self._guild_lounges: Dict[int, List[int]] = defaultdict(list)  # guild_id -> sorted channel_ids
        self._pending_creations: Dict[int, asyncio.Task] = {}
        self._user_alone_timers: Dict[int, Dict[int, float]] = defaultdict(dict)
        
    async def add_lounge(self, guild_id: int, channel_id: int, number: int) -> LoungeEntry:
        """Atomically add a new lounge"""
        async with self._lock:
            entry = LoungeEntry(
                channel_id=channel_id,
                guild_id=guild_id,
                number=number,
                created_at=time.time(),
                last_active=time.time(),
                metadata={}
            )
            self._lounges[channel_id] = entry
            
            # Insert in sorted position
            if channel_id not in self._guild_lounges[guild_id]:
                self._guild_lounges[guild_id].append(channel_id)
                self._guild_lounges[guild_id].sort(
                    key=lambda cid: self._lounges[cid].number
                )
            
            return entry
    
    async def remove_lounge(self, channel_id: int) -> Optional[LoungeEntry]:
        """Atomically remove a lounge"""
        async with self._lock:
            entry = self._lounges.pop(channel_id, None)
            if entry:
                # Cancel any deletion task
                if entry.deletion_task:
                    entry.deletion_task.cancel()
                
                # Remove from guild list
                if channel_id in self._guild_lounges[entry.guild_id]:
                    self._guild_lounges[entry.guild_id].remove(channel_id)
                
                # Clean up user timers for this channel
                guild_timers = self._user_alone_timers[entry.guild_id]
                users_to_remove = []
                for user_id, (timer_channel_id, _) in guild_timers.items():
                    if timer_channel_id == channel_id:
                        users_to_remove.append(user_id)
                for user_id in users_to_remove:
                    del guild_timers[user_id]
            
            return entry
    
    async def get_lounge(self, channel_id: int) -> Optional[LoungeEntry]:
        """Get lounge entry"""
        async with self._lock:
            return self._lounges.get(channel_id)
    
    async def get_guild_lounges(self, guild_id: int) -> List[LoungeEntry]:
        """Get all lounges for a guild in order"""
        async with self._lock:
            channel_ids = self._guild_lounges.get(guild_id, [])
            return [self._lounges[cid] for cid in channel_ids if cid in self._lounges]
    
    async def update_lounge(self, channel_id: int, **kwargs) -> bool:
        """Atomically update lounge properties"""
        async with self._lock:
            if channel_id not in self._lounges:
                return False
            
            entry = self._lounges[channel_id]
            for key, value in kwargs.items():
                if hasattr(entry, key):
                    setattr(entry, key, value)
            
            return True
    
    async def schedule_deletion(self, channel_id: int, deletion_time: float) -> bool:
        """Atomically schedule a lounge for deletion"""
        async with self._lock:
            if channel_id not in self._lounges:
                return False
            
            entry = self._lounges[channel_id]
            
            # Cancel existing task
            if entry.deletion_task:
                entry.deletion_task.cancel()
            
            entry.deletion_scheduled = time.time() + deletion_time
            return True
    
    async def cancel_deletion(self, channel_id: int) -> bool:
        """Atomically cancel scheduled deletion"""
        async with self._lock:
            if channel_id not in self._lounges:
                return False
            
            entry = self._lounges[channel_id]
            if entry.deletion_task:
                entry.deletion_task.cancel()
                entry.deletion_task = None
            
            entry.deletion_scheduled = None
            return True
    
    async def set_deletion_task(self, channel_id: int, task: asyncio.Task) -> bool:
        """Set the deletion task for a lounge"""
        async with self._lock:
            if channel_id not in self._lounges:
                return False
            
            entry = self._lounges[channel_id]
            entry.deletion_task = task
            return True
    
    async def get_pending_creations(self, guild_id: int) -> List[int]:
        """Get channels with pending creations"""
        async with self._lock:
            return [cid for cid, task in self._pending_creations.items() 
                   if cid in self._lounges and self._lounges[cid].guild_id == guild_id]
    
    async def add_pending_creation(self, channel_id: int, task: asyncio.Task):
        """Add a pending creation task"""
        async with self._lock:
            self._pending_creations[channel_id] = task
    
    async def remove_pending_creation(self, channel_id: int):
        """Remove a pending creation task"""
        async with self._lock:
            task = self._pending_creations.pop(channel_id, None)
            if task:
                task.cancel()
    
    async def set_user_alone_timer(self, guild_id: int, user_id: int, channel_id: int):
        """Set a timer for a user being alone"""
        async with self._lock:
            self._user_alone_timers[guild_id][user_id] = (channel_id, time.time())
    
    async def remove_user_alone_timer(self, guild_id: int, user_id: int):
        """Remove a user's alone timer"""
        async with self._lock:
            self._user_alone_timers[guild_id].pop(user_id, None)
    
    async def get_user_alone_timers(self, guild_id: int) -> Dict[int, Tuple[int, float]]:
        """Get all alone timers for a guild"""
        async with self._lock:
            return dict(self._user_alone_timers[guild_id])

class DynamicLounges(commands.Cog):
    """Atomic and robust lounge system"""
    
    def __init__(self, bot):
        self.bot = bot
        self.state = AtomicLoungeManager()
        
        # Configuration
        self.initial_channel_id = 1455864869030068285  # Lounge 3 generator
        self.base_name = "┊ 🔊﹕₊˚⌇lounge"
        self.name_suffix = "  ꜝꜝ‹3"
        
        # Atomic operation locks (for Discord API calls)
        self._discord_ops_lock = asyncio.Lock()  # For Discord API calls
        self._creation_queue = asyncio.Queue()   # Serialize creations
        
        # Settings
        self.inactivity_timer = 300      # 5 minutes for deletion
        self.creation_delay = 30         # 30 seconds delay
        self.max_lounges = 15
        self.min_users_per_lounge = 2
        
        # Capacity rules
        self.base_capacity_rules = {
            3: float('inf'),
            4: 8, 5: 8,
            6: 6, 7: 6,
            8: 4,
        }
        self.default_capacity = 4
        
        # Promotion system
        self.promotion_rules = {
            LoungeTier.NORMAL: {"min_users": 2, "capacity_boost": 0, "emoji": ""},
            LoungeTier.POPULAR: {"min_users": 5, "duration_minutes": 30, "capacity_boost": 2, "emoji": "⭐"},
            LoungeTier.PREMIUM: {"min_users": 8, "duration_minutes": 60, "capacity_boost": 4, "emoji": "💎"},
            LoungeTier.ELITE: {"min_users": 10, "duration_minutes": 120, "capacity_boost": 6, "emoji": "👑"},
        }
        
        # Anti-hogging
        self.anti_hogging_rules = {
            "alone_timeout": 600,
            "low_activity_timeout": 1200,
            "move_to_next": True,
            "notify_user": True,
        }
        
        # Statistics (protected by atomic operations)
        self.stats = {
            "channels_created": 0,
            "channels_deleted": 0,
            "renames_performed": 0,
            "promotions_given": 0,
            "users_moved": 0,
            "hogging_prevented": 0,
        }
        
        # Start tasks
        self._start_tasks()
    
    def _start_tasks(self):
        """Start all background tasks"""
        self.creation_worker_task = asyncio.create_task(self._creation_worker())
        self.cleanup_task = self._create_cleanup_task()
        self.promotion_check_task = self._create_promotion_check_task()
        self.anti_hogging_task = self._create_anti_hogging_task()
        
        self.cleanup_task.start()
        self.promotion_check_task.start()
        self.anti_hogging_task.start()
    
    def cog_unload(self):
        """Cleanup on cog unload"""
        self.cleanup_task.cancel()
        self.promotion_check_task.cancel()
        self.anti_hogging_task.cancel()
        self.creation_worker_task.cancel()
    
    # ========== ATOMIC WORKERS ==========
    
    async def _creation_worker(self):
        """Single worker for lounge creation (prevents race conditions)"""
        while True:
            try:
                (guild_id, trigger_channel_id) = await self._creation_queue.get()
                await self._process_creation_request(guild_id, trigger_channel_id)
                await asyncio.sleep(1)  # Rate limiting
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Creation worker error: {e}")
    
    def _create_cleanup_task(self):
        """Create cleanup task"""
        @tasks.loop(minutes=5)
        async def task():
            for guild in self.bot.guilds:
                try:
                    await self._atomic_cleanup(guild)
                except Exception as e:
                    logger.error(f"Cleanup error in {guild.name}: {e}")
        return task
    
    def _create_promotion_check_task(self):
        """Create promotion check task"""
        @tasks.loop(minutes=2)
        async def task():
            for guild in self.bot.guilds:
                try:
                    await self._atomic_promotion_check(guild)
                except Exception as e:
                    logger.error(f"Promotion check error in {guild.name}: {e}")
        return task
    
    def _create_anti_hogging_task(self):
        """Create anti-hogging task"""
        @tasks.loop(minutes=1)
        async def task():
            for guild in self.bot.guilds:
                try:
                    await self._atomic_anti_hogging(guild)
                except Exception as e:
                    logger.error(f"Anti-hogging error in {guild.name}: {e}")
        return task
    
    # ========== ATOMIC OPERATIONS ==========
    
    async def _process_creation_request(self, guild_id: int, trigger_channel_id: int):
        """Process a creation request atomically"""
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        
        # Get fresh Discord state
        trigger_channel = guild.get_channel(trigger_channel_id)
        if not trigger_channel:
            return
        
        # Check conditions with fresh state
        if not await self._check_creation_conditions(guild, trigger_channel):
            return
        
        # Create the lounge
        await self._atomic_create_lounge(guild)
    
    async def _atomic_create_lounge(self, guild: discord.Guild) -> Optional[discord.VoiceChannel]:
        """Atomically create a new lounge"""
        async with self._discord_ops_lock:  # Lock Discord API calls
            try:
                # Get current state
                lounges = await self.state.get_guild_lounges(guild.id)
                
                # Check max lounges
                if len(lounges) >= self.max_lounges - 1:  # -1 for Lounge 3
                    logger.info(f"Max lounges reached for {guild.name}")
                    return None
                
                # Calculate next number
                existing_numbers = {l.number for l in lounges}
                next_number = 4  # Start from 4
                while next_number in existing_numbers:
                    next_number += 1
                
                # Get Lounge 3 for positioning
                lounge3 = guild.get_channel(self.initial_channel_id)
                if not lounge3:
                    logger.error("Lounge 3 not found!")
                    return None
                
                # Calculate position (after the last lounge)
                last_lounge = None
                if lounges:
                    # Get the actual Discord channel for the highest number
                    highest_num = max(l.number for l in lounges)
                    for l in lounges:
                        if l.number == highest_num:
                            last_channel = guild.get_channel(l.channel_id)
                            if last_channel:
                                last_lounge = last_channel
                                break
                
                position = lounge3.position + 1
                if last_lounge:
                    position = last_lounge.position + 1
                
                # Create the channel
                name = self._get_lounge_name(next_number, LoungeTier.NORMAL)
                capacity = self.base_capacity_rules.get(next_number, self.default_capacity)
                
                new_channel = await guild.create_voice_channel(
                    name=name,
                    category=lounge3.category,
                    position=position,
                    user_limit=capacity if capacity != float('inf') else 0
                )
                
                # Update atomic state
                await self.state.add_lounge(guild.id, new_channel.id, next_number)
                
                # Update stats
                self.stats["channels_created"] += 1
                
                logger.info(f"✅ Created {name} at position {position}")
                
                # Rename all lounges to ensure sequential numbering
                await self._atomic_renumber_lounges(guild)
                
                return new_channel
                
            except Exception as e:
                logger.error(f"❌ Failed to create lounge: {e}")
                return None
    
    async def _atomic_renumber_lounges(self, guild: discord.Guild):
        """Atomically renumber all lounges"""
        async with self._discord_ops_lock:
            try:
                lounges = await self.state.get_guild_lounges(guild.id)
                
                # Sort by current number
                lounges.sort(key=lambda x: x.number)
                
                # Renumber starting from 3
                target_number = 3
                for lounge_entry in lounges:
                    channel = guild.get_channel(lounge_entry.channel_id)
                    if not channel:
                        continue
                    
                    # Update entry number
                    await self.state.update_lounge(
                        lounge_entry.channel_id,
                        number=target_number
                    )
                    
                    # Rename if needed
                    expected_name = self._get_lounge_name(target_number, lounge_entry.tier)
                    if channel.name != expected_name:
                        try:
                            await channel.edit(name=expected_name)
                            self.stats["renames_performed"] += 1
                        except Exception as e:
                            logger.error(f"Failed to rename {channel.name}: {e}")
                    
                    target_number += 1
                    
            except Exception as e:
                logger.error(f"Renumbering error: {e}")
    
    async def _atomic_schedule_deletion(self, channel: discord.VoiceChannel):
        """Atomically schedule a lounge for deletion"""
        # Never delete Lounge 3
        if channel.id == self.initial_channel_id:
            return
        
        # Check if already has users
        if len(channel.members) > 0:
            return
        
        # Schedule deletion
        await self.state.schedule_deletion(channel.id, self.inactivity_timer)
        
        # Create deletion task
        async def deletion_task():
            try:
                await asyncio.sleep(self.inactivity_timer)
                
                # Re-check conditions
                channel_refreshed = channel.guild.get_channel(channel.id)
                if not channel_refreshed or len(channel_refreshed.members) > 0:
                    await self.state.cancel_deletion(channel.id)
                    return
                
                # Delete the channel
                async with self._discord_ops_lock:
                    await channel_refreshed.delete()
                    self.stats["channels_deleted"] += 1
                    logger.info(f"🧹 Deleted {channel_refreshed.name}")
                
                # Remove from state
                await self.state.remove_lounge(channel.id)
                
                # Renumber remaining lounges
                await self._atomic_renumber_lounges(channel.guild)
                
            except asyncio.CancelledError:
                logger.info(f"Deletion cancelled for {channel.name}")
            except Exception as e:
                logger.error(f"Deletion task error: {e}")
        
        task = asyncio.create_task(deletion_task())
        await self.state.set_deletion_task(channel.id, task)
    
    async def _atomic_cancel_deletion(self, channel: discord.VoiceChannel):
        """Atomically cancel scheduled deletion"""
        await self.state.cancel_deletion(channel.id)
    
    async def _atomic_cleanup(self, guild: discord.Guild):
        """Atomic cleanup operation"""
        # Clean up orphaned state entries
        lounges = await self.state.get_guild_lounges(guild.id)
        
        for lounge_entry in list(lounges):
            channel = guild.get_channel(lounge_entry.channel_id)
            if not channel:
                # Channel doesn't exist, remove from state
                await self.state.remove_lounge(lounge_entry.channel_id)
                logger.info(f"Cleaned up orphaned lounge entry: {lounge_entry.number}")
    
    async def _atomic_promotion_check(self, guild: discord.Guild):
        """Atomic promotion check"""
        lounges = await self.state.get_guild_lounges(guild.id)
        
        for lounge_entry in lounges:
            channel = guild.get_channel(lounge_entry.channel_id)
            if not channel or len(channel.members) == 0:
                continue
            
            # Check for promotion
            await self._check_and_promote_lounge(channel, lounge_entry)
    
    async def _atomic_anti_hogging(self, guild: discord.Guild):
        """Atomic anti-hogging check"""
        if not self.anti_hogging_rules["move_to_next"]:
            return
        
        timers = await self.state.get_user_alone_timers(guild.id)
        current_time = time.time()
        moves_to_make = []
        
        # Check each timer
        for user_id, (channel_id, start_time) in list(timers.items()):
            alone_duration = current_time - start_time
            
            user = guild.get_member(user_id)
            if not user or not user.voice or not user.voice.channel:
                await self.state.remove_user_alone_timer(guild.id, user_id)
                continue
            
            channel = user.voice.channel
            if channel.id != channel_id:
                await self.state.remove_user_alone_timer(guild.id, user_id)
                continue
            
            # Check if user is alone
            if len(channel.members) == 1:
                if alone_duration >= self.anti_hogging_rules["alone_timeout"]:
                    moves_to_make.append((user, channel))
        
        # Move users (outside atomic lock for Discord API)
        for user, current_channel in moves_to_make:
            await self._move_user_to_next_lounge(guild, user, current_channel)
    
    # ========== HELPER METHODS ==========
    
    def _get_lounge_name(self, number: int, tier: LoungeTier) -> str:
        """Generate lounge name"""
        tier_emoji = self.promotion_rules[tier]["emoji"]
        base_name = f"{self.base_name} {number}{self.name_suffix}"
        return f"{base_name} {tier_emoji}".strip()
    
    def _parse_lounge_number(self, name: str) -> Optional[int]:
        """Parse lounge number from name"""
        clean_name = name
        
        # Remove promotion emojis
        for tier in [LoungeTier.POPULAR, LoungeTier.PREMIUM, LoungeTier.ELITE]:
            emoji = self.promotion_rules[tier]["emoji"]
            if clean_name.endswith(emoji):
                clean_name = clean_name[:-len(emoji)].strip()
        
        # Extract number
        match = re.search(r'lounge\s*(\d+)', clean_name, re.IGNORECASE)
        return int(match.group(1)) if match else None
    
    async def _check_creation_conditions(self, guild: discord.Guild, trigger_channel: discord.VoiceChannel) -> bool:
        """Check if we should create a new lounge"""
        lounges = await self.state.get_guild_lounges(guild.id)
        
        # Filter dynamic lounges (4+)
        dynamic_lounges = [l for l in lounges if l.number >= 4]
        
        # Check max lounges
        if len(lounges) >= self.max_lounges - 1:  # -1 for Lounge 3
            return False
        
        # If no dynamic lounges, check if someone is in Lounge 3
        if len(dynamic_lounges) == 0:
            lounge3 = guild.get_channel(self.initial_channel_id)
            return lounge3 and len(lounge3.members) > 0
        
        # Check if all dynamic lounges have enough users
        for lounge_entry in dynamic_lounges:
            channel = guild.get_channel(lounge_entry.channel_id)
            if not channel or len(channel.members) < self.min_users_per_lounge:
                return False
        
        return True
    
    async def _check_and_promote_lounge(self, channel: discord.VoiceChannel, lounge_entry: LoungeEntry):
        """Check and promote a lounge"""
        current_users = len(channel.members)
        
        # Update peak users
        if current_users > lounge_entry.peak_users:
            await self.state.update_lounge(
                channel.id,
                peak_users=current_users,
                last_active=time.time()
            )
        
        # Check promotion rules
        current_tier = lounge_entry.tier
        next_tier = None
        
        tiers = [LoungeTier.POPULAR, LoungeTier.PREMIUM, LoungeTier.ELITE]
        for tier in tiers:
            rules = self.promotion_rules[tier]
            if (current_users >= rules["min_users"] and 
                tier.value > current_tier.value):
                
                if "duration_minutes" in rules:
                    active_duration = (time.time() - lounge_entry.last_active) / 60
                    if active_duration >= rules["duration_minutes"]:
                        next_tier = tier
                        break
                else:
                    next_tier = tier
                    break
        
        # Apply promotion
        if next_tier and next_tier != current_tier:
            await self.state.update_lounge(
                channel.id,
                tier=next_tier
            )
            
            self.stats["promotions_given"] += 1
            
            # Update capacity
            base_capacity = self.base_capacity_rules.get(lounge_entry.number, self.default_capacity)
            if base_capacity != float('inf'):
                boost = self.promotion_rules[next_tier]["capacity_boost"]
                new_capacity = base_capacity + boost
                
                try:
                    await channel.edit(user_limit=new_capacity)
                except Exception as e:
                    logger.error(f"Failed to update capacity: {e}")
            
            # Rename with new emoji
            await self._atomic_renumber_lounges(channel.guild)
    
    async def _move_user_to_next_lounge(self, guild: discord.Guild, user: discord.Member, current_channel: discord.VoiceChannel):
        """Move a user to the next available lounge"""
        lounges = await self.state.get_guild_lounges(guild.id)
        
        # Find current lounge index
        current_index = -1
        for i, entry in enumerate(lounges):
            if entry.channel_id == current_channel.id:
                current_index = i
                break
        
        if current_index >= 0 and current_index + 1 < len(lounges):
            next_entry = lounges[current_index + 1]
            next_channel = guild.get_channel(next_entry.channel_id)
            
            if next_channel:
                # Check capacity
                capacity = self.base_capacity_rules.get(next_entry.number, self.default_capacity)
                if next_entry.tier != LoungeTier.NORMAL:
                    capacity += self.promotion_rules[next_entry.tier]["capacity_boost"]
                
                if capacity == float('inf') or len(next_channel.members) < capacity:
                    try:
                        # Notify user
                        if self.anti_hogging_rules["notify_user"]:
                            try:
                                await user.send(
                                    f"👋 You're being moved from {current_channel.name} to {next_channel.name} "
                                    f"to keep lounges active and social!"
                                )
                            except:
                                pass
                        
                        # Move user
                        await user.move_to(next_channel)
                        self.stats["hogging_prevented"] += 1
                        self.stats["users_moved"] += 1
                        
                        logger.info(f"Moved {user.name} to {next_channel.name}")
                        
                        # Remove timer
                        await self.state.remove_user_alone_timer(guild.id, user.id)
                        
                    except Exception as e:
                        logger.error(f"Failed to move user: {e}")
    
    # ========== EVENT HANDLERS ==========
    
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        """Handle voice state updates atomically"""
        if member.bot:
            return
        
        # Handle joins
        if after.channel:
            # Check if joined Lounge 3 (generator)
            if after.channel.id == self.initial_channel_id:
                # Queue creation request
                await self._creation_queue.put((after.channel.guild.id, after.channel.id))
            
            # Check if joined a dynamic lounge
            elif self._parse_lounge_number(after.channel.name) is not None:
                # Cancel any scheduled deletion
                await self._atomic_cancel_deletion(after.channel)
                
                # Update activity
                lounge_entry = await self.state.get_lounge(after.channel.id)
                if lounge_entry:
                    await self.state.update_lounge(
                        after.channel.id,
                        last_active=time.time()
                    )
                
                # Set alone timer if user is alone
                if len(after.channel.members) == 1:
                    await self.state.set_user_alone_timer(
                        after.channel.guild.id,
                        member.id,
                        after.channel.id
                    )
                else:
                    # Clear timer if multiple users
                    await self.state.remove_user_alone_timer(after.channel.guild.id, member.id)
                
                # Queue creation check if this is the highest lounge
                lounges = await self.state.get_guild_lounges(after.channel.guild.id)
                if lounges:
                    highest_lounge = lounges[-1]
                    if highest_lounge.channel_id == after.channel.id:
                        await self._creation_queue.put((after.channel.guild.id, after.channel.id))
        
        # Handle leaves
        if before.channel and before.channel.id != self.initial_channel_id:
            lounge_number = self._parse_lounge_number(before.channel.name)
            if lounge_number is not None and lounge_number >= 4:
                # Remove alone timer
                await self.state.remove_user_alone_timer(before.channel.guild.id, member.id)
                
                # If lounge is now empty, schedule deletion
                if len(before.channel.members) == 0:
                    await self._atomic_schedule_deletion(before.channel)
    
    # ========== COMMANDS ==========
    
    @commands.command(name="lounge_stats", aliases=["ls"])
    @commands.has_permissions(administrator=True)
    async def lounge_stats(self, ctx):
        """Show lounge statistics"""
        lounges = await self.state.get_guild_lounges(ctx.guild.id)
        
        embed = discord.Embed(
            title="🎯 Atomic Lounge System Statistics",
            color=discord.Color.blue()
        )
        
        # Stats
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
        
        # Current lounges
        lounge_info = []
        current_time = time.time()
        
        for entry in lounges:
            channel = ctx.guild.get_channel(entry.channel_id)
            if not channel:
                continue
            
            members = len(channel.members)
            capacity = self.base_capacity_rules.get(entry.number, self.default_capacity)
            if entry.tier != LoungeTier.NORMAL:
                capacity += self.promotion_rules[entry.tier]["capacity_boost"]
            
            capacity_str = "∞" if capacity == float('inf') else str(capacity)
            
            # Check deletion status
            deletion_info = ""
            if entry.deletion_scheduled and entry.deletion_scheduled > current_time:
                time_left = entry.deletion_scheduled - current_time
                minutes = int(time_left // 60)
                seconds = int(time_left % 60)
                deletion_info = f" 🧹 Deletes in {minutes}:{seconds:02d}"
            
            # Generator or dynamic lounge
            if entry.number == 3:
                status = "🔧"
                lounge_info.append(
                    f"{status} **{channel.name}:** {members}/{capacity_str} users (Generator){deletion_info}"
                )
            else:
                meets_requirement = members >= self.min_users_per_lounge
                status = "✅" if meets_requirement else "❌"
                
                lounge_info.append(
                    f"{status} **{channel.name}:** {members}/{capacity_str} users{deletion_info}"
                )
        
        if lounge_info:
            dynamic_lounges = [l for l in lounges if l.number >= 4]
            all_have_users = all(
                len(ctx.guild.get_channel(l.channel_id).members) >= self.min_users_per_lounge
                for l in dynamic_lounges
                if ctx.guild.get_channel(l.channel_id)
            )
            
            status_text = "\n".join(lounge_info)
            if dynamic_lounges and not all_have_users:
                status_text += f"\n\n**⚠️ Creation blocked:** All dynamic lounges need {self.min_users_per_lounge}+ users"
            elif dynamic_lounges:
                status_text += f"\n\n**✅ Creation ready:** All dynamic lounges have {self.min_users_per_lounge}+ users"
            else:
                status_text += f"\n\n**⏳ Waiting:** Need users in Lounge 3 to create first dynamic lounge"
            
            embed.add_field(
                name="🎭 Current Lounges",
                value=status_text,
                inline=False
            )
        
        # Configuration
        config_info = (
            f"**Generator Channel:** Lounge 3\n"
            f"**Max Lounges:** {self.max_lounges}\n"
            f"**Min Users Per Lounge:** {self.min_users_per_lounge}\n"
            f"**Inactivity Timer:** 5 minutes\n"
            f"**Creation Delay:** {self.creation_delay} seconds\n"
            f"**Atomic Operations:** ✅ Enabled"
        )
        
        embed.add_field(
            name="⚙️ Configuration",
            value=config_info,
            inline=False
        )
        
        await ctx.send(embed=embed)
    
    @commands.command(name="lounge_reset", aliases=["lr"])
    @commands.has_permissions(administrator=True)
    async def lounge_reset(self, ctx):
        """Reset the lounge system"""
        confirm = await ctx.send(
            "⚠️ This will delete ALL dynamic lounges (4+) and reset the system.\n"
            "Type `CONFIRM` to proceed."
        )
        
        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel and m.content == "CONFIRM"
        
        try:
            await self.bot.wait_for('message', timeout=30.0, check=check)
        except asyncio.TimeoutError:
            await confirm.edit(content="Reset cancelled.")
            return
        
        # Delete all dynamic lounges
        lounges = await self.state.get_guild_lounges(ctx.guild.id)
        deleted = 0
        
        async with self._discord_ops_lock:
            for entry in lounges:
                if entry.number >= 4:  # Only delete dynamic lounges
                    channel = ctx.guild.get_channel(entry.channel_id)
                    if channel:
                        try:
                            await channel.delete()
                            deleted += 1
                            await asyncio.sleep(0.5)
                        except Exception as e:
                            logger.error(f"Failed to delete {channel.name}: {e}")
        
        # Reset state
        for entry in lounges:
            if entry.number >= 4:
                await self.state.remove_lounge(entry.channel_id)
        
        # Reset stats
        self.stats["channels_deleted"] += deleted
        
        await ctx.send(f"✅ Reset complete. Deleted {deleted} dynamic lounges.")

async def setup(bot):
    await bot.add_cog(DynamicLounges(bot))