"""
SHADOW CHAT - QUEUE MANAGER (OPTIMIZED)
Handles user queue operations and matchmaking with proper concurrency control.
"""

import asyncio
import datetime
from typing import List, Optional, Tuple, Dict
import discord

from . import constants
from .models import Database, QueueEntry, ShadowSession
from .utils import generate_session_id, format_thread_name
from .permissions import AccessController

class QueueManager:
    """Manages the user queue and matchmaking with atomic operations."""
    
    def __init__(self, bot, db: Database, access_controller: AccessController):
        self.bot = bot
        self.db = db
        self.access = access_controller
        
        # Processing state
        self.is_processing = False
        
        # Atomic operations control
        self.queue_lock = asyncio.Lock()
        self.processing_lock = asyncio.Lock()
        self.user_locks: Dict[int, asyncio.Lock] = {}
        
        # Queue state tracking
        self.queue_update_callbacks = []
        self.matchmaking_callbacks = []
        
        # Session duration tracking for wait time estimation
        self.session_durations: List[int] = []
        self.avg_session_duration = 15  # Default 15 minutes
    
    def _get_user_lock(self, user_id: int) -> asyncio.Lock:
        """Get or create a user-specific lock to prevent duplicate operations."""
        if user_id not in self.user_locks:
            self.user_locks[user_id] = asyncio.Lock()
        return self.user_locks[user_id]
    
    async def is_user_banned(self, user_id: int) -> bool:
        """Check if user is banned from Shadow Chat."""
        try:
            cursor = await self.db.conn.execute(
                "SELECT is_banned FROM shadow_users WHERE user_id = ?",
                (user_id,)
            )
            row = await cursor.fetchone()
            return row and row[0] == 1
        except Exception as e:
            print(f"[Queue Manager] Error checking ban status: {e}")
            return False
    
    async def is_user_in_queue(self, user_id: int) -> bool:
        """Check if user is already in queue."""
        try:
            cursor = await self.db.conn.execute(
                "SELECT 1 FROM shadow_queue WHERE user_id = ?",
                (user_id,)
            )
            return await cursor.fetchone() is not None
        except Exception as e:
            print(f"[Queue Manager] Error checking if user in queue: {e}")
            return False

    async def add_to_queue(self, user: discord.Member) -> Tuple[bool, str, int, bool]:
        """
        Add user to queue.
        Returns: (success, message, position, should_be_delayed)
        """
        async with self._get_user_lock(user.id):
            try:
                # Check if user is banned
                if await self.is_user_banned(user.id):
                    return False, "You are banned from using Shadow Chat.", 0, False
                
                # Check if already in queue
                if await self.is_user_in_queue(user.id):
                    return False, "You're already in the queue.", 0, False
                
                # Check if in active session
                cursor = await self.db.conn.execute("""
                    SELECT 1 FROM shadow_sessions 
                    WHERE (user1_id = ? OR user2_id = ?) 
                    AND is_active = 1
                    AND NOT (
                        (user1_id = ? AND user1_left = 1) OR 
                        (user2_id = ? AND user2_left = 1)
                    )
                """, (user.id, user.id, user.id, user.id))

                if await cursor.fetchone():
                    return False, "You're already in an active session. Use /end to end your session first.", 0, False
                
                position = await self.db.add_user_to_queue(user.id, user.name)
                
                if position > 0:
                    print(f"[Shadow Chat] Added {user} to queue (position {position})")
                    
                    # Return success - matchmaking will be done in background
                    return True, "Joined queue successfully.", position, False
                else:
                    return False, "Failed to join queue.", 0, False
                    
            except Exception as e:
                print(f"[Shadow Chat] Queue add error: {e}")
                return False, f"Error: {str(e)}", 0, False
    async def _try_matchmaking(self):
        """Try to match users in the queue."""
        try:
            # Only start matchmaking if not already processing
            if not self.is_processing:
                await self.process_queue()
        except Exception as e:
            print(f"[Queue Manager] Matchmaking error: {e}")
    
    async def calculate_estimated_wait(self, position: int) -> float:
        """
        Calculate estimated wait time in minutes.
        Simple version - you can enhance this later.
        """
        # Simple calculation: 2 minutes per person ahead
        wait_minutes = (position // 2) * self.avg_session_duration
        
        # Minimum 30 seconds, maximum 30 minutes
        return max(0.5, min(wait_minutes, 30.0))
    
    async def get_queue_stats(self) -> Dict:
        """Get queue statistics including oldest wait time."""
        async with self.queue_lock:
            try:
                # Get queue entries
                cursor = await self.db.conn.execute("""
                    SELECT user_id, joined_at FROM shadow_queue 
                    ORDER BY joined_at ASC
                """)
                queue_entries = await cursor.fetchall()
                
                if not queue_entries:
                    return {
                        'size': 0,
                        'oldest_wait': None,
                        'average_wait': None,
                        'total_wait_time': 0
                    }
                
                # Parse dates properly
                from datetime import datetime, timezone
                
                parsed_entries = []
                for entry in queue_entries:
                    joined_at_str = entry[1]
                    if '+' in joined_at_str or joined_at_str.endswith('Z'):
                        joined_at = datetime.fromisoformat(joined_at_str.replace('Z', '+00:00'))
                    else:
                        naive_dt = datetime.fromisoformat(joined_at_str)
                        joined_at = naive_dt.replace(tzinfo=timezone.utc)
                    parsed_entries.append(joined_at)
                
                # Calculate oldest wait time
                now = datetime.now(timezone.utc)
                oldest_join_time = parsed_entries[0]
                oldest_wait = now - oldest_join_time
                
                # Calculate total and average wait time
                total_wait_seconds = 0
                for join_time in parsed_entries:
                    wait_time = now - join_time
                    total_wait_seconds += wait_time.total_seconds()
                
                average_wait_seconds = total_wait_seconds / len(parsed_entries)
                
                return {
                    'size': len(queue_entries),
                    'oldest_wait': oldest_join_time,
                    'oldest_wait_seconds': oldest_wait.total_seconds(),
                    'average_wait_seconds': average_wait_seconds,
                    'total_wait_time': total_wait_seconds
                }
                
            except Exception as e:
                print(f"[Queue Manager] Error getting queue stats: {e}")
                import traceback
                traceback.print_exc()
                return {
                    'size': 0,
                    'oldest_wait': None,
                    'average_wait': None,
                    'total_wait_time': 0
                }
    
    async def get_queue_info(self, user_id: int) -> Optional[QueueEntry]:
        """Get user's queue information if they're in the queue."""
        async with self.queue_lock:
            try:
                cursor = await self.db.conn.execute("""
                    SELECT user_id, joined_at, (
                        SELECT COUNT(*) FROM shadow_queue sq2 
                        WHERE sq2.joined_at <= sq.joined_at
                    ) as position
                    FROM shadow_queue sq
                    WHERE user_id = ?
                """, (user_id,))
                
                row = await cursor.fetchone()
                if row:
                    # Parse the ISO format string with timezone info
                    from datetime import datetime, timezone
                    
                    # Check if the string has timezone info
                    joined_at_str = row[1]
                    
                    if '+' in joined_at_str or joined_at_str.endswith('Z'):
                        # Has timezone info, parse directly
                        joined_at = datetime.fromisoformat(joined_at_str.replace('Z', '+00:00'))
                    else:
                        # No timezone info, assume UTC
                        naive_dt = datetime.fromisoformat(joined_at_str)
                        joined_at = naive_dt.replace(tzinfo=timezone.utc)
                    return QueueEntry(
                        user_id=row[0],
                        joined_at=joined_at,
                        position=row[2]
                    )
                return None
                
            except Exception as e:
                print(f"[Queue Manager] Error getting queue info: {e}")
                import traceback
                traceback.print_exc()
                return None
            
    async def remove_from_queue(self, user_id: int) -> bool:
        """Remove user from queue."""
        async with self.queue_lock:
            try:
                cursor = await self.db.conn.execute("""
                    DELETE FROM shadow_queue WHERE user_id = ?
                """, (user_id,))
                
                await self.db.conn.commit()
                removed = cursor.rowcount > 0
                
                if removed:
                    await self._notify_queue_update()
                
                return removed
                
            except Exception as e:
                print(f"[Queue Manager] Error removing from queue: {e}")
                return False
    
    async def process_queue(self):
        """Process queue to create matches (public version)."""
        try:
            await self._process_queue_internal()
        except Exception as e:
            print(f"[Queue] Process queue error: {e}")
    
    async def _process_queue_internal(self):
        """Internal queue processing with proper locking."""
        # Prevent concurrent processing
        async with self.processing_lock:
            if self.is_processing:
                return
            self.is_processing = True
        
        try:
            while True:
                # Check if we can create new sessions
                active_count = await self.db.get_active_session_count()
                if active_count >= constants.MAX_ACTIVE_SESSIONS:
                    print(f"[Queue] Max sessions reached ({active_count}/{constants.MAX_ACTIVE_SESSIONS})")
                    break
                
                # Get next 2 users from queue
                async with self.queue_lock:
                    # Get queue entries in order
                    cursor = await self.db.conn.execute("""
                        SELECT user_id, joined_at FROM shadow_queue 
                        ORDER BY joined_at ASC 
                        LIMIT 2
                    """)
                    rows = await cursor.fetchall()
                    
                    if len(rows) < 2:
                        print(f"[Queue] Not enough users in queue: {len(rows)}/2")
                        break
                    
                    # Extract user IDs and join times
                    user_ids = [row[0] for row in rows]
                    join_times = [row[1] for row in rows]
                
                # Remove users from queue
                success = True
                for user_id in user_ids:
                    if not await self.db.remove_from_queue(user_id):
                        success = False
                        break
                
                if not success:
                    print(f"[Queue] Failed to remove users from queue")
                    break
                
                # Get user objects
                user1 = self.bot.get_user(user_ids[0])
                user2 = self.bot.get_user(user_ids[1])
                
                if not user1 or not user2:
                    print(f"[Queue] Users not found: {user_ids}")
                    # Re-add them to queue
                    for i, user_id in enumerate(user_ids):
                        user = self.bot.get_user(user_id)
                        if user:
                            await self.db.add_user_to_queue(
                                user_id, 
                                user.name, 
                                datetime.datetime.fromisoformat(join_times[i])
                            )
                    continue
                
                print(f"[Queue] Match found: {user1.id} and {user2.id}")
                
                # Trigger matchmaking via callback IN THE BACKGROUND
                for callback in self.matchmaking_callbacks:
                    try:
                        # Run callback in background to avoid blocking
                        asyncio.create_task(callback(user1, user2))
                    except Exception as e:
                        print(f"[Queue] Matchmaking callback error: {e}")
                
                # Check if we should continue processing
                active_count = await self.db.get_active_session_count()
                if active_count >= constants.MAX_ACTIVE_SESSIONS:
                    break
                
                # Small delay to prevent tight loop
                await asyncio.sleep(0.1)  # Reduced delay for faster processing
                    
        except Exception as e:
            print(f"[Queue] Processing error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self.is_processing = False
            await self._notify_queue_update()
            
    async def _notify_queue_update(self):
        """Notify all registered callbacks of queue update."""
        for callback in self.queue_update_callbacks:
            try:
                await callback()
            except Exception as e:
                print(f"[Queue] Queue update callback error: {e}")
    
    def register_queue_callback(self, callback):
        """Register a callback for queue updates."""
        self.queue_update_callbacks.append(callback)
    
    def register_matchmaking_callback(self, callback):
        """Register a callback for matchmaking (when pair is found)."""
        self.matchmaking_callbacks.append(callback)
    
    async def clear_queue(self):
        """Clear the entire queue (for maintenance)."""
        async with self.queue_lock:
            try:
                cursor = await self.db.conn.execute("DELETE FROM shadow_queue")
                await self.db.conn.commit()
                count = cursor.rowcount
                
                await self._notify_queue_update()
                return count
                
            except Exception as e:
                print(f"[Queue Manager] Error clearing queue: {e}")
                return 0
    
    async def get_queue_users(self) -> List[int]:
        """Get all user IDs currently in queue."""
        async with self.queue_lock:
            try:
                cursor = await self.db.conn.execute("""
                    SELECT user_id FROM shadow_queue ORDER BY joined_at ASC
                """)
                rows = await cursor.fetchall()
                return [row[0] for row in rows]
                
            except Exception as e:
                print(f"[Queue Manager] Error getting queue users: {e}")
                return []
    
    async def update_session_duration(self, duration_minutes: int):
        """Update session duration tracking for better wait time estimation."""
        self.session_durations.append(duration_minutes)
        
        # Keep only last 100 durations
        if len(self.session_durations) > 100:
            self.session_durations = self.session_durations[-100:]
        
        # Update average
        self.avg_session_duration = sum(self.session_durations) / len(self.session_durations)