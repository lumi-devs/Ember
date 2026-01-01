import discord
from discord.ext import commands, tasks
import asyncio
import aiosqlite
from typing import Dict, List, Optional, Tuple, Set
import time
import re
import logging
from dataclasses import dataclass
from enum import Enum
from collections import defaultdict
import json

logger = logging.getLogger(__name__)

# Database path
DB_PATH = "lounges.db"

class LoungeTier(Enum):
    NORMAL = "normal"
    POPULAR = "popular"
    PREMIUM = "premium"
    ELITE = "elite"

@dataclass
class LoungeEntry:
    """Lounge entry with database persistence"""
    channel_id: int
    guild_id: int
    number: int
    tier: LoungeTier = LoungeTier.NORMAL
    deletion_scheduled: Optional[float] = None
    created_at: float = 0.0
    last_active: float = 0.0
    peak_users: int = 0
    metadata: str = "{}"  # JSON string
    
    @classmethod
    def from_db(cls, row):
        """Create from database row"""
        try:
            metadata = row.get('metadata', '{}')
            if isinstance(metadata, str):
                metadata_dict = json.loads(metadata)
            else:
                metadata_dict = metadata or {}
        except:
            metadata_dict = {}
        
        return cls(
            channel_id=row['channel_id'],
            guild_id=row['guild_id'],
            number=row['number'],
            tier=LoungeTier(row['tier']),
            deletion_scheduled=row['deletion_scheduled'],
            created_at=row['created_at'],
            last_active=row['last_active'],
            peak_users=row['peak_users'],
            metadata=json.dumps(metadata_dict) if isinstance(metadata_dict, dict) else '{}'
        )
    
    def to_db_dict(self):
        """Convert to dictionary for database"""
        return {
            'channel_id': self.channel_id,
            'guild_id': self.guild_id,
            'number': self.number,
            'tier': self.tier.value,
            'deletion_scheduled': self.deletion_scheduled,
            'created_at': self.created_at,
            'last_active': self.last_active,
            'peak_users': self.peak_users,
            'metadata': self.metadata
        }

class PersistentLoungeManager:
    """Database-backed atomic lounge manager"""
    
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._lock = asyncio.Lock()
        self._cache: Dict[int, LoungeEntry] = {}  # channel_id -> LoungeEntry
        self._guild_cache: Dict[int, List[int]] = defaultdict(list)  # guild_id -> sorted channel_ids
        self._user_alone_timers: Dict[int, Dict[int, Tuple[int, float]]] = defaultdict(dict)
    
    async def initialize(self):
        """Initialize database"""
        async with aiosqlite.connect(self.db_path) as db:
            # Create main lounges table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS lounges (
                    channel_id INTEGER PRIMARY KEY,
                    guild_id INTEGER NOT NULL,
                    number INTEGER NOT NULL,
                    tier TEXT NOT NULL,
                    deletion_scheduled REAL,
                    created_at REAL NOT NULL,
                    last_active REAL NOT NULL,
                    peak_users INTEGER DEFAULT 0,
                    metadata TEXT DEFAULT '{}',
                    UNIQUE(guild_id, number)
                )
            """)
            
            # Create indexes for fast queries
            await db.execute("CREATE INDEX IF NOT EXISTS idx_guild ON lounges(guild_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_guild_number ON lounges(guild_id, number)")
            
            # Create statistics table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS lounge_stats (
                    guild_id INTEGER,
                    stat_name TEXT,
                    stat_value INTEGER DEFAULT 0,
                    PRIMARY KEY (guild_id, stat_name)
                )
            """)
            
            # Create deletion queue table (for restart recovery)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS deletion_queue (
                    channel_id INTEGER PRIMARY KEY,
                    guild_id INTEGER NOT NULL,
                    scheduled_time REAL NOT NULL,
                    created_at REAL NOT NULL
                )
            """)
            
            await db.commit()
            
            # Load all lounges into cache
            await self._load_all_lounges(db)
    
    async def _load_all_lounges(self, db):
        """Load all lounges from database into cache"""
        async with db.execute("SELECT * FROM lounges") as cursor:
            rows = await cursor.fetchall()
            columns = [description[0] for description in cursor.description]
            
            for row_values in rows:
                row = dict(zip(columns, row_values))
                entry = LoungeEntry.from_db(row)
                self._cache[entry.channel_id] = entry
                
                # Add to guild cache
                if entry.channel_id not in self._guild_cache[entry.guild_id]:
                    self._guild_cache[entry.guild_id].append(entry.channel_id)
            
            # Sort each guild's lounges by number
            for guild_id in self._guild_cache:
                self._guild_cache[guild_id].sort(
                    key=lambda cid: self._cache[cid].number
                )
    
    async def add_lounge(self, guild_id: int, channel_id: int, number: int) -> LoungeEntry:
        """Atomically add a new lounge with database persistence"""
        async with self._lock:
            # Check if already exists
            if channel_id in self._cache:
                return self._cache[channel_id]
            
            entry = LoungeEntry(
                channel_id=channel_id,
                guild_id=guild_id,
                number=number,
                created_at=time.time(),
                last_active=time.time(),
                metadata="{}"
            )
            
            # Save to database
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("""
                    INSERT OR REPLACE INTO lounges 
                    (channel_id, guild_id, number, tier, deletion_scheduled, created_at, last_active, peak_users, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    entry.channel_id,
                    entry.guild_id,
                    entry.number,
                    entry.tier.value,
                    entry.deletion_scheduled,
                    entry.created_at,
                    entry.last_active,
                    entry.peak_users,
                    entry.metadata
                ))
                await db.commit()
            
            # Update cache
            self._cache[channel_id] = entry
            
            # Insert in sorted position in guild cache
            if channel_id not in self._guild_cache[guild_id]:
                self._guild_cache[guild_id].append(channel_id)
                self._guild_cache[guild_id].sort(
                    key=lambda cid: self._cache[cid].number
                )
            
            return entry
    
    async def remove_lounge(self, channel_id: int) -> Optional[LoungeEntry]:
        """Atomically remove a lounge with database persistence"""
        async with self._lock:
            entry = self._cache.get(channel_id)
            if not entry:
                return None
            
            # Remove from database
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("DELETE FROM lounges WHERE channel_id = ?", (channel_id,))
                
                # Also remove from deletion queue
                await db.execute("DELETE FROM deletion_queue WHERE channel_id = ?", (channel_id,))
                
                await db.commit()
            
            # Remove from caches
            self._cache.pop(channel_id, None)
            
            if channel_id in self._guild_cache[entry.guild_id]:
                self._guild_cache[entry.guild_id].remove(channel_id)
            
            # Clean up user timers
            guild_timers = self._user_alone_timers[entry.guild_id]
            users_to_remove = []
            for user_id, (timer_channel_id, _) in guild_timers.items():
                if timer_channel_id == channel_id:
                    users_to_remove.append(user_id)
            for user_id in users_to_remove:
                del guild_timers[user_id]
            
            return entry
    
    async def update_lounge(self, channel_id: int, **kwargs) -> bool:
        """Atomically update lounge properties with database persistence"""
        async with self._lock:
            if channel_id not in self._cache:
                return False
            
            entry = self._cache[channel_id]
            
            # Update fields
            for key, value in kwargs.items():
                if hasattr(entry, key):
                    setattr(entry, key, value)
            
            # Save to database
            async with aiosqlite.connect(self.db_path) as db:
                db_dict = entry.to_db_dict()
                await db.execute("""
                    UPDATE lounges SET
                    tier = ?,
                    deletion_scheduled = ?,
                    last_active = ?,
                    peak_users = ?,
                    metadata = ?
                    WHERE channel_id = ?
                """, (
                    db_dict['tier'],
                    db_dict['deletion_scheduled'],
                    db_dict['last_active'],
                    db_dict['peak_users'],
                    db_dict['metadata'],
                    channel_id
                ))
                await db.commit()
            
            return True
    
    async def schedule_deletion(self, channel_id: int, deletion_time: float) -> bool:
        """Schedule deletion with database persistence"""
        async with self._lock:
            if channel_id not in self._cache:
                return False
            
            entry = self._cache[channel_id]
            scheduled_time = time.time() + deletion_time
            
            # Update entry
            entry.deletion_scheduled = scheduled_time
            
            # Save to database and deletion queue
            async with aiosqlite.connect(self.db_path) as db:
                # Update lounge entry
                await db.execute(
                    "UPDATE lounges SET deletion_scheduled = ? WHERE channel_id = ?",
                    (scheduled_time, channel_id)
                )
                
                # Add to deletion queue for restart recovery
                await db.execute("""
                    INSERT OR REPLACE INTO deletion_queue 
                    (channel_id, guild_id, scheduled_time, created_at)
                    VALUES (?, ?, ?, ?)
                """, (
                    channel_id,
                    entry.guild_id,
                    scheduled_time,
                    time.time()
                ))
                
                await db.commit()
            
            return True
    
    async def cancel_deletion(self, channel_id: int) -> bool:
        """Cancel scheduled deletion with database persistence"""
        async with self._lock:
            if channel_id not in self._cache:
                return False
            
            entry = self._cache[channel_id]
            entry.deletion_scheduled = None
            
            # Update database and deletion queue
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "UPDATE lounges SET deletion_scheduled = NULL WHERE channel_id = ?",
                    (channel_id,)
                )
                
                await db.execute(
                    "DELETE FROM deletion_queue WHERE channel_id = ?",
                    (channel_id,)
                )
                
                await db.commit()
            
            return True
    
    async def get_lounge(self, channel_id: int) -> Optional[LoungeEntry]:
        """Get lounge entry from cache"""
        async with self._lock:
            return self._cache.get(channel_id)
    
    async def get_guild_lounges(self, guild_id: int) -> List[LoungeEntry]:
        """Get all lounges for a guild in order"""
        async with self._lock:
            channel_ids = self._guild_cache.get(guild_id, [])
            return [self._cache[cid] for cid in channel_ids if cid in self._cache]
    
    async def get_pending_deletions(self, guild_id: Optional[int] = None) -> List[Tuple[int, float]]:
        """Get all pending deletions (for restart recovery)"""
        async with self._lock:
            pending = []
            current_time = time.time()
            
            for channel_id, entry in self._cache.items():
                if (entry.deletion_scheduled and 
                    entry.deletion_scheduled > current_time and
                    (guild_id is None or entry.guild_id == guild_id)):
                    pending.append((channel_id, entry.deletion_scheduled))
            
            return sorted(pending, key=lambda x: x[1])  # Sort by deletion time
    
    async def update_stat(self, guild_id: int, stat_name: str, delta: int = 1):
        """Update a statistic with database persistence"""
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                # Get current value
                async with db.execute(
                    "SELECT stat_value FROM lounge_stats WHERE guild_id = ? AND stat_name = ?",
                    (guild_id, stat_name)
                ) as cursor:
                    row = await cursor.fetchone()
                    current_value = row[0] if row else 0
                
                # Update value
                new_value = current_value + delta
                await db.execute("""
                    INSERT OR REPLACE INTO lounge_stats (guild_id, stat_name, stat_value)
                    VALUES (?, ?, ?)
                """, (guild_id, stat_name, new_value))
                
                await db.commit()
    
    async def get_stats(self, guild_id: int) -> Dict[str, int]:
        """Get all statistics for a guild"""
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute(
                    "SELECT stat_name, stat_value FROM lounge_stats WHERE guild_id = ?",
                    (guild_id,)
                ) as cursor:
                    rows = await cursor.fetchall()
                    return {row[0]: row[1] for row in rows}
    
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
    
    async def cleanup_orphaned_entries(self, guild: discord.Guild):
        """Clean up database entries for channels that no longer exist"""
        async with self._lock:
            current_channel_ids = {c.id for c in guild.voice_channels}
            
            # Get all lounge entries for this guild
            guild_entries = await self.get_guild_lounges(guild.id)
            
            for entry in list(guild_entries):
                if entry.channel_id not in current_channel_ids:
                    # Channel doesn't exist, remove from database
                    logger.info(f"Cleaning up orphaned lounge entry: {entry.number}")
                    await self.remove_lounge(entry.channel_id)

class DynamicLounges(commands.Cog):
    """Persistent atomic lounge system"""
    
    def __init__(self, bot):
        self.bot = bot
        self.state = PersistentLoungeManager()
        
        # Configuration
        self.initial_channel_id = 1455864869030068285
        self.base_name = "┊ 🔊﹕₊˚⌇lounge"
        self.name_suffix = "  ꜝꜝ‹3"
        
        # Atomic operation locks
        self._discord_ops_lock = asyncio.Lock()
        self._creation_queue = asyncio.Queue()
        self._creation_tasks: Dict[int, asyncio.Task] = {}  # channel_id -> creation task
        
        # Settings
        self.inactivity_timer = 300      # 5 minutes
        self.creation_delay = 30         # 30 seconds
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
        
        # Task references
        self.creation_worker_task = None
        self.cleanup_task = None
        self.promotion_check_task = None
        self.anti_hogging_task = None
        
        # Deletion tasks tracking
        self._deletion_tasks: Dict[int, asyncio.Task] = {}
    
    async def cog_load(self):
        """Initialize on cog load"""
        await self.state.initialize()
        await self._recover_pending_deletions()
        self._start_tasks()
        logger.info("✅ Lounge system loaded with database persistence")
    
    def _start_tasks(self):
        """Start all background tasks"""
        self.creation_worker_task = asyncio.create_task(self._creation_worker())
        self.cleanup_task = self._create_cleanup_task()
        self.promotion_check_task = self._create_promotion_check_task()
        self.anti_hogging_task = self._create_anti_hogging_task()
        
        self.cleanup_task.start()
        self.promotion_check_task.start()
        self.anti_hogging_task.start()
    
    async def _recover_pending_deletions(self):
        """Recover pending deletions after restart"""
        logger.info("Recovering pending deletions from database...")
        
        for guild in self.bot.guilds:
            try:
                # Get all pending deletions for this guild
                pending = await self.state.get_pending_deletions(guild.id)
                current_time = time.time()
                
                for channel_id, scheduled_time in pending:
                    channel = guild.get_channel(channel_id)
                    if not channel:
                        # Channel doesn't exist, clean up
                        await self.state.remove_lounge(channel_id)
                        continue
                    
                    # Check if deletion time has passed
                    if scheduled_time <= current_time:
                        # Should have been deleted, clean up
                        if len(channel.members) == 0:
                            try:
                                async with self._discord_ops_lock:
                                    await channel.delete()
                                    await self.state.update_stat(guild.id, "channels_deleted")
                                    logger.info(f"Recovered: Deleted {channel.name}")
                            except:
                                pass
                        await self.state.remove_lounge(channel_id)
                    else:
                        # Reschedule deletion
                        time_left = scheduled_time - current_time
                        await self._schedule_deletion_task(channel, time_left)
                        logger.info(f"Rescheduled deletion for {channel.name} in {time_left:.1f}s")
                        
            except Exception as e:
                logger.error(f"Error recovering deletions for {guild.name}: {e}")
    
    async def _schedule_deletion_task(self, channel: discord.VoiceChannel, delay: float):
        """Schedule a deletion task"""
        if channel.id in self._deletion_tasks:
            self._deletion_tasks[channel.id].cancel()
        
        async def deletion_task():
            try:
                await asyncio.sleep(delay)
                
                # Re-check conditions
                channel_refreshed = channel.guild.get_channel(channel.id)
                if not channel_refreshed or len(channel_refreshed.members) > 0:
                    await self.state.cancel_deletion(channel.id)
                    self._deletion_tasks.pop(channel.id, None)
                    return
                
                # Delete the channel
                async with self._discord_ops_lock:
                    await channel_refreshed.delete()
                    await self.state.update_stat(channel.guild.id, "channels_deleted")
                    logger.info(f"🧹 Deleted {channel_refreshed.name}")
                
                # Remove from state
                await self.state.remove_lounge(channel.id)
                
                # Renumber remaining lounges
                await self._atomic_renumber_lounges(channel.guild)
                
                # Remove task reference
                self._deletion_tasks.pop(channel.id, None)
                
            except asyncio.CancelledError:
                logger.info(f"Deletion cancelled for {channel.name}")
                self._deletion_tasks.pop(channel.id, None)
            except Exception as e:
                logger.error(f"Deletion task error for {channel.name}: {e}")
                self._deletion_tasks.pop(channel.id, None)
                # Don't remove from database - let cleanup handle it
        
        task = asyncio.create_task(deletion_task())
        self._deletion_tasks[channel.id] = task
    
    def cog_unload(self):
        """Cleanup on cog unload"""
        if self.cleanup_task:
            self.cleanup_task.cancel()
        if self.promotion_check_task:
            self.promotion_check_task.cancel()
        if self.anti_hogging_task:
            self.anti_hogging_task.cancel()
        if self.creation_worker_task:
            self.creation_worker_task.cancel()
        
        # Cancel all deletion tasks
        for task in self._deletion_tasks.values():
            task.cancel()
        
        # Cancel all creation tasks
        for task in self._creation_tasks.values():
            task.cancel()
    
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
        
        @task.before_loop
        async def before_task():
            await self.bot.wait_until_ready()
        
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
        
        @task.before_loop
        async def before_task():
            await self.bot.wait_until_ready()
        
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
        
        @task.before_loop
        async def before_task():
            await self.bot.wait_until_ready()
        
        return task
    
    # ========== ATOMIC OPERATIONS ==========
    
    async def _process_creation_request(self, guild_id: int, trigger_channel_id: int):
        """Process a creation request atomically"""
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        
        # Check if already processing for this channel
        if trigger_channel_id in self._creation_tasks:
            return
        
        # Create and track the creation task
        async def creation_task():
            try:
                await asyncio.sleep(self.creation_delay)
                
                # Get fresh Discord state
                trigger_channel = guild.get_channel(trigger_channel_id)
                if not trigger_channel:
                    return
                
                # Check conditions with fresh state
                if not await self._check_creation_conditions(guild, trigger_channel):
                    return
                
                # Create the lounge
                await self._atomic_create_lounge(guild)
                
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"Creation task error: {e}")
            finally:
                self._creation_tasks.pop(trigger_channel_id, None)
        
        task = asyncio.create_task(creation_task())
        self._creation_tasks[trigger_channel_id] = task
    
    async def _atomic_create_lounge(self, guild: discord.Guild) -> Optional[discord.VoiceChannel]:
        """Atomically create a new lounge"""
        async with self._discord_ops_lock:  # Lock Discord API calls
            try:
                # Get current state
                lounges = await self.state.get_guild_lounges(guild.id)
                
                # Check max lounges (including Lounge 3)
                if len(lounges) >= self.max_lounges:
                    logger.info(f"Max lounges reached for {guild.name}")
                    return None
                
                # Calculate next number
                existing_numbers = {l.number for l in lounges}
                next_number = 3  # Start from 3
                while next_number in existing_numbers:
                    next_number += 1
                
                # Get Lounge 3 for positioning
                lounge3 = guild.get_channel(self.initial_channel_id)
                if not lounge3:
                    logger.error("Lounge 3 not found!")
                    return None
                
                # Calculate position (after the last lounge)
                position = lounge3.position + 1
                last_lounge_channel = None
                
                if lounges:
                    # Find the actual Discord channel with highest position
                    voice_channels = [c for c in guild.voice_channels if self._parse_lounge_number(c.name) is not None]
                    if voice_channels:
                        last_lounge_channel = max(voice_channels, key=lambda c: c.position)
                        position = last_lounge_channel.position + 1
                
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
                await self.state.update_stat(guild.id, "channels_created")
                
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
                renamed_count = 0
                
                for lounge_entry in lounges:
                    channel = guild.get_channel(lounge_entry.channel_id)
                    if not channel:
                        continue
                    
                    # Update entry number if changed
                    if lounge_entry.number != target_number:
                        await self.state.update_lounge(
                            lounge_entry.channel_id,
                            number=target_number
                        )
                    
                    # Rename if needed
                    expected_name = self._get_lounge_name(target_number, lounge_entry.tier)
                    if channel.name != expected_name:
                        try:
                            await channel.edit(name=expected_name)
                            renamed_count += 1
                        except Exception as e:
                            logger.error(f"Failed to rename {channel.name}: {e}")
                    
                    target_number += 1
                
                # Update rename stats if any renames happened
                if renamed_count > 0:
                    await self.state.update_stat(guild.id, "renames_performed", renamed_count)
                    
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
        await self._schedule_deletion_task(channel, self.inactivity_timer)
    
    async def _atomic_cancel_deletion(self, channel: discord.VoiceChannel):
        """Atomically cancel scheduled deletion"""
        await self.state.cancel_deletion(channel.id)
        
        # Cancel deletion task if exists
        if channel.id in self._deletion_tasks:
            self._deletion_tasks[channel.id].cancel()
            self._deletion_tasks.pop(channel.id, None)
    
    async def _atomic_cleanup(self, guild: discord.Guild):
        """Atomic cleanup operation"""
        # Clean up orphaned state entries
        await self.state.cleanup_orphaned_entries(guild)
        
        # Clean up finished deletion tasks
        finished_tasks = []
        for channel_id, task in list(self._deletion_tasks.items()):
            if task.done():
                finished_tasks.append(channel_id)
        
        for channel_id in finished_tasks:
            self._deletion_tasks.pop(channel_id, None)
    
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
        if tier_emoji:
            return f"{base_name} {tier_emoji}"
        return base_name
    
    def _parse_lounge_number(self, name: str) -> Optional[int]:
        """Parse lounge number from name"""
        if not name:
            return None
        
        clean_name = name
        
        # Remove promotion emojis
        for tier in [LoungeTier.POPULAR, LoungeTier.PREMIUM, LoungeTier.ELITE]:
            emoji = self.promotion_rules[tier]["emoji"]
            if emoji and clean_name.endswith(emoji):
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
        if len(lounges) >= self.max_lounges:
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
        
        # Update peak users and last active
        updates = {'last_active': time.time()}
        if current_users > lounge_entry.peak_users:
            updates['peak_users'] = current_users
        
        await self.state.update_lounge(channel.id, **updates)
        
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
            await self.state.update_lounge(channel.id, tier=next_tier)
            await self.state.update_stat(channel.guild.id, "promotions_given")
            
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
                        await self.state.update_stat(guild.id, "hogging_prevented")
                        await self.state.update_stat(guild.id, "users_moved")
                        
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
        
        # Anti-spam cooldown (simple in-memory)
        current_time = time.time()
        # Simple cooldown - can be enhanced if needed
        
        # Handle joins
        if after.channel:
            # Check if joined Lounge 3 (generator)
            if after.channel.id == self.initial_channel_id:
                # Queue creation request
                await self._creation_queue.put((after.channel.guild.id, after.channel.id))
            
            # Check if joined a lounge
            lounge_number = self._parse_lounge_number(after.channel.name)
            if lounge_number is not None:
                # Cancel any scheduled deletion
                await self._atomic_cancel_deletion(after.channel)
                
                # Update activity
                lounge_entry = await self.state.get_lounge(after.channel.id)
                if lounge_entry:
                    await self.state.update_lounge(
                        after.channel.id,
                        last_active=time.time()
                    )
                else:
                    # Add to state if not exists (should only happen for Lounge 3)
                    if lounge_number >= 3:
                        await self.state.add_lounge(after.channel.guild.id, after.channel.id, lounge_number)
                
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
                
                # Queue creation check if this is a dynamic lounge (4+)
                if lounge_number >= 4:
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
    
    @commands.command(name="lounge_stats", aliases=["ls", "lounges"])
    @commands.has_permissions(administrator=True)
    async def lounge_stats_cmd(self, ctx):
        """Show lounge statistics"""
        try:
            logger.info(f"Command .ls called by {ctx.author}")
            
            lounges = await self.state.get_guild_lounges(ctx.guild.id)
            stats = await self.state.get_stats(ctx.guild.id)
            
            embed = discord.Embed(
                title="🎯 Persistent Lounge System Statistics",
                color=discord.Color.blue(),
                timestamp=discord.utils.utcnow()
            )
            
            # Stats from database
            embed.add_field(
                name="📊 System Stats",
                value=f"**Total Lounges:** {len(lounges)}\n"
                      f"**Created:** {stats.get('channels_created', 0)}\n"
                      f"**Deleted:** {stats.get('channels_deleted', 0)}\n"
                      f"**Promotions:** {stats.get('promotions_given', 0)}\n"
                      f"**Users Moved:** {stats.get('users_moved', 0)}\n"
                      f"**Hogging Preventions:** {stats.get('hogging_prevented', 0)}\n"
                      f"**Renames:** {stats.get('renames_performed', 0)}",
                inline=False
            )
            
            # Current lounges with tier status
            lounge_info = []
            current_time = time.time()
            creation_ready = True
            
            for entry in sorted(lounges, key=lambda x: x.number):
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
                    deletion_info = f" ⏰ {minutes}:{seconds:02d}"
                
                # Generator or dynamic lounge
                if entry.number == 3:
                    status = "🔧"
                    lounge_info.append(
                        f"{status} **{channel.name}:** {members}/{capacity_str} users (Generator){deletion_info}"
                    )
                else:
                    meets_requirement = members >= self.min_users_per_lounge
                    status = "✅" if meets_requirement else "❌"
                    
                    if not meets_requirement:
                        creation_ready = False
                    
                    # Add promotion emoji if promoted
                    tier_emoji = self.promotion_rules[entry.tier]["emoji"]
                    promotion_display = f" {tier_emoji}" if tier_emoji else ""
                    
                    lounge_info.append(
                        f"{status} **{channel.name}:** {members}/{capacity_str} users{promotion_display}{deletion_info}"
                    )
            
            if lounge_info:
                status_text = "\n".join(lounge_info)
                dynamic_lounges = [l for l in lounges if l.number >= 4]
                
                if dynamic_lounges:
                    if creation_ready:
                        status_text += f"\n\n**✅ Creation ready:** All dynamic lounges have {self.min_users_per_lounge}+ users"
                    else:
                        status_text += f"\n\n**⚠️ Creation blocked:** All dynamic lounges need {self.min_users_per_lounge}+ users"
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
                f"**Inactivity Timer:** {self.inactivity_timer // 60} minutes\n"
                f"**Creation Delay:** {self.creation_delay} seconds\n"
                f"**Database Persistence:** ✅ Enabled\n"
                f"**Atomic Operations:** ✅ Enabled"
            )
            
            embed.add_field(
                name="⚙️ Configuration",
                value=config_info,
                inline=False
            )
            
            # Pending deletions
            pending = await self.state.get_pending_deletions(ctx.guild.id)
            if pending:
                pending_info = []
                for channel_id, scheduled_time in pending:
                    channel = ctx.guild.get_channel(channel_id)
                    if channel:
                        time_left = scheduled_time - current_time
                        minutes = int(time_left // 60)
                        seconds = int(time_left % 60)
                        pending_info.append(f"• {channel.name}: {minutes}:{seconds:02d}")
                
                if pending_info:
                    embed.add_field(
                        name="⏰ Pending Deletions",
                        value="\n".join(pending_info),
                        inline=False
                    )
            
            embed.set_footer(text=f"Guild ID: {ctx.guild.id}")
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Error in lounge_stats: {e}", exc_info=True)
            await ctx.send(f"❌ Error showing lounge stats: {str(e)}")
    
    @commands.command(name="lounge_promote", aliases=["lp", "promotelounge"])
    @commands.has_permissions(administrator=True)
    async def lounge_promote_cmd(self, ctx, lounge_number: int):
        """Manually promote a lounge to next tier"""
        try:
            lounges = await self.state.get_guild_lounges(ctx.guild.id)
            
            target_lounge = None
            for entry in lounges:
                if entry.number == lounge_number:
                    target_lounge = entry
                    break
            
            if not target_lounge:
                await ctx.send(f"❌ Lounge {lounge_number} not found!")
                return
            
            channel = ctx.guild.get_channel(target_lounge.channel_id)
            if not channel:
                await ctx.send(f"❌ Channel for Lounge {lounge_number} not found!")
                return
            
            # Get current tier
            current_tier = target_lounge.tier
            
            # Find next tier
            tiers = [LoungeTier.NORMAL, LoungeTier.POPULAR, LoungeTier.PREMIUM, LoungeTier.ELITE]
            current_index = tiers.index(current_tier) if current_tier in tiers else 0
            
            if current_index >= len(tiers) - 1:
                await ctx.send(f"❌ {channel.name} is already at the highest tier!")
                return
            
            next_tier = tiers[current_index + 1]
            
            # Manually promote
            await self.state.update_lounge(channel.id, tier=next_tier)
            await self.state.update_stat(ctx.guild.id, "promotions_given")
            
            # Update capacity
            base_capacity = self.base_capacity_rules.get(lounge_number, self.default_capacity)
            if base_capacity != float('inf'):
                boost = self.promotion_rules[next_tier]["capacity_boost"]
                new_capacity = base_capacity + boost
                
                try:
                    await channel.edit(user_limit=new_capacity)
                except Exception as e:
                    logger.error(f"Failed to update capacity: {e}")
            
            # Rename with new emoji
            await self._atomic_renumber_lounges(ctx.guild)
            
            await ctx.send(f"✅ {channel.name} manually promoted to **{next_tier.value.upper()}** tier!")
            
        except Exception as e:
            logger.error(f"Error in lounge_promote: {e}", exc_info=True)
            await ctx.send(f"❌ Error promoting lounge: {str(e)}")
    
    @commands.command(name="lounge_reset", aliases=["lr", "resetlounges"])
    @commands.has_permissions(administrator=True)
    async def lounge_reset_cmd(self, ctx):
        """Reset the lounge system"""
        try:
            confirm = await ctx.send(
                "⚠️ **DANGER: This will delete ALL dynamic lounges (4+) and reset the system.**\n"
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
                                await asyncio.sleep(0.5)  # Rate limit protection
                            except Exception as e:
                                logger.error(f"Failed to delete {channel.name}: {e}")
            
            # Reset state for dynamic lounges
            for entry in lounges:
                if entry.number >= 4:
                    await self.state.remove_lounge(entry.channel_id)
            
            # Reset stats for this guild
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "DELETE FROM lounge_stats WHERE guild_id = ?",
                    (ctx.guild.id,)
                )
                await db.commit()
            
            # Cancel any pending tasks
            for task in list(self._deletion_tasks.values()):
                task.cancel()
            self._deletion_tasks.clear()
            
            for task in list(self._creation_tasks.values()):
                task.cancel()
            self._creation_tasks.clear()
            
            await ctx.send(f"✅ Reset complete. Deleted {deleted} dynamic lounges and cleared all data.")
            
        except Exception as e:
            logger.error(f"Error in lounge_reset: {e}", exc_info=True)
            await ctx.send(f"❌ Error resetting lounges: {str(e)}")
    
    @commands.command(name="lounge_debug", aliases=["ld", "loungedebug"])
    @commands.has_permissions(administrator=True)
    async def lounge_debug_cmd(self, ctx):
        """Debug information for lounge system"""
        try:
            lounges = await self.state.get_guild_lounges(ctx.guild.id)
            
            embed = discord.Embed(
                title="🐛 Lounge System Debug",
                color=discord.Color.orange(),
                timestamp=discord.utils.utcnow()
            )
            
            # Database info
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute("SELECT COUNT(*) FROM lounges WHERE guild_id = ?", (ctx.guild.id,)) as cursor:
                    db_count = (await cursor.fetchone())[0]
                
                async with db.execute("SELECT COUNT(*) FROM deletion_queue WHERE guild_id = ?", (ctx.guild.id,)) as cursor:
                    queue_count = (await cursor.fetchone())[0]
            
            embed.add_field(
                name="Database",
                value=f"**Entries:** {db_count}\n"
                      f"**Cache:** {len(lounges)}\n"
                      f"**Deletion Queue:** {queue_count}\n"
                      f"**Active Tasks:** {len(self._deletion_tasks) + len(self._creation_tasks)}",
                inline=False
            )
            
            # Channel states
            channel_states = []
            for entry in sorted(lounges, key=lambda x: x.number):
                channel = ctx.guild.get_channel(entry.channel_id)
                if channel:
                    channel_states.append(
                        f"{entry.number}: {'✅' if channel else '❌'} ({len(channel.members)} users)"
                    )
                else:
                    channel_states.append(f"{entry.number}: ❌ (missing)")
            
            if channel_states:
                embed.add_field(
                    name="Channel States",
                    value="\n".join(channel_states),
                    inline=False
                )
            
            # Queue info
            embed.add_field(
                name="Queues",
                value=f"**Creation Queue:** {self._creation_queue.qsize()}\n"
                      f"**Pending Creations:** {len(self._creation_tasks)}\n"
                      f"**Pending Deletions:** {len(self._deletion_tasks)}",
                inline=False
            )
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Error in lounge_debug: {e}", exc_info=True)
            await ctx.send(f"❌ Error showing debug info: {str(e)}")


async def setup(bot):
    cog = DynamicLounges(bot)
    await bot.add_cog(cog)
    logger.info("✅ Lounge system loaded with restart persistence")