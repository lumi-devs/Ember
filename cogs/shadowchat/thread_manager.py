"""
SHADOW CHAT - THREAD MANAGER
Handles Discord thread creation, management, and cleanup.
"""

import discord
from typing import Optional, Set, List
import asyncio
from . import constants
from .utils import get_thread_by_id, format_thread_name

class ThreadManager:
    """Manages Discord threads for Shadow Chat sessions."""
    
    def __init__(self, bot):
        self.bot = bot
        self.parent_channel: Optional[discord.TextChannel] = None
        self.guild: Optional[discord.Guild] = None  # Cache guild to reduce lookups
        self.initialized = False
    
    async def initialize(self, max_retries: int = 5):
        """Initialize thread manager with parent channel."""
        # Fast exit if already initialized and valid
        if self.initialized and self.parent_channel and self.guild:
            return True

        for attempt in range(max_retries):
            try:
                # 1. Cache the Guild Object
                if not self.guild:
                    self.guild = self.bot.get_guild(constants.GUILD_ID)
                
                if not self.guild:
                    print(f"[Shadow Chat] Attempt {attempt + 1}: Guild {constants.GUILD_ID} not found yet")
                    await asyncio.sleep(2)
                    continue
                
                # 2. Find Parent Channel
                self.parent_channel = self.guild.get_channel(constants.PARENT_CHANNEL_ID)
                
                if not self.parent_channel:
                    # Try to find it via category (Fallback)
                    if hasattr(constants, 'SHADOW_CATEGORY_ID'):
                        category = self.guild.get_channel(constants.SHADOW_CATEGORY_ID)
                        if category and isinstance(category, discord.CategoryChannel):
                            # Fast iteration looking for first TextChannel
                            for channel in category.channels:
                                if isinstance(channel, discord.TextChannel):
                                    self.parent_channel = channel
                                    break
                    
                    if not self.parent_channel:
                        print(f"[Shadow Chat] Parent channel {constants.PARENT_CHANNEL_ID} not found")
                        return False
                
                self.initialized = True
                print(f"[Shadow Chat] Thread manager initialized with parent channel: {self.parent_channel.name}")
                return True
                
            except Exception as e:
                print(f"[Shadow Chat] Initialization attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2)
        
        print("[Shadow Chat] Thread manager failed to initialize")
        return False
    
    async def create_thread(self, session_id: str, user: discord.Member, partner: discord.Member, is_user1: bool = True) -> Optional[discord.Thread]:
        """
        Create a private thread for a user in a session.
        """
        # Optimized: Check directly before awaiting the initialize wrapper
        if not self.parent_channel:
            if not await self.initialize():
                return None
        
        try:
            # Generate thread name based on position
            # Note: Assuming format_thread_name returns a tuple/list.
            # We calculate this before API calls to fail fast if utils break.
            thread_names = format_thread_name(session_id, user.id, partner.id)
            thread_name = thread_names[0] if is_user1 else thread_names[1]
            
            thread = None
            
            try:
                # Method 1: Try to create a private thread directly
                thread = await self.parent_channel.create_thread(
                    name=thread_name,
                    type=discord.ChannelType.private_thread,
                    invitable=False,  # Only moderators can add users
                    auto_archive_duration=constants.THREAD_AUTO_ARCHIVE_DURATION,
                    reason=f"Shadow Chat session {session_id}"
                )
            except (discord.HTTPException, AttributeError) as e:
                print(f"[Shadow Chat] Private thread creation failed: {e}")
                
                # Method 2: Create a public thread (will be made effectively private)
                try:
                    thread = await self.parent_channel.create_thread(
                        name=thread_name,
                        type=discord.ChannelType.public_thread,
                        auto_archive_duration=constants.THREAD_AUTO_ARCHIVE_DURATION,
                        reason=f"Shadow Chat session {session_id}"
                    )
                    
                    print(f"[Shadow Chat] Created public thread for {user}, will manage permissions")
                    
                except Exception as e2:
                    print(f"[Shadow Chat] Public thread creation also failed: {e2}")
                    return None
            
            if not thread:
                return None
            
            # Add the user to the thread
            await thread.add_user(user)
            
            # Set thread permissions to be private if it fell back to Public
            if thread.type == discord.ChannelType.public_thread:
                try:
                    everyone_role = thread.guild.default_role
                    
                    overwrite = discord.PermissionOverwrite()
                    overwrite.view_channel = False
                    overwrite.send_messages = False
                    overwrite.read_message_history = False
                    
                    await thread.set_permissions(everyone_role, overwrite=overwrite, reason="Make thread private for Shadow Chat")
                    
                except Exception as e:
                    print(f"[Shadow Chat] Warning: Could not set thread permissions: {e}")
                    # Continue anyway - the thread exists and user is added
            
            print(f"[Shadow Chat] Created thread {thread.id} ({thread.name}) for {user}")
            return thread
            
        except discord.Forbidden:
            print(f"[Shadow Chat] Missing permissions to create thread for {user}")
            return None
        except discord.HTTPException as e:
            print(f"[Shadow Chat] Failed to create thread: {e}")
            return None
        except Exception as e:
            print(f"[Shadow Chat] Unexpected thread creation error: {e}")
            return None
    
    async def delete_thread(self, thread_id: int, reason: str = "Session ended") -> bool:
        """Delete a thread by ID."""
        # Use cached guild if available, otherwise fetch
        guild = self.guild or self.bot.get_guild(constants.GUILD_ID)
        if not guild:
            return False
        
        thread = await get_thread_by_id(guild, thread_id)
        if not thread:
            return False
        
        try:
            await thread.delete(reason=reason)
            print(f"[Shadow Chat] Deleted thread {thread_id}")
            return True
        except discord.Forbidden:
            print(f"[Shadow Chat] No permission to delete thread {thread_id}")
            return False
        except discord.HTTPException as e:
            print(f"[Shadow Chat] Failed to delete thread {thread_id}: {e}")
            return False
    
    async def _get_thread_by_id(self, thread_id: int) -> Optional[discord.Thread]:
        """Internal method to get thread by ID."""
        guild = self.guild or self.bot.get_guild(constants.GUILD_ID)
        if not guild:
            return None
        
        return await get_thread_by_id(guild, thread_id)
    
    async def cleanup_orphaned_threads(self, active_thread_ids: Set[int]):
        """
        Clean up threads that don't have active sessions.
        OPTIMIZED: Uses asyncio.gather for concurrent deletion.
        """
        if not self.parent_channel:
            return
        
        deletion_tasks = []

        try:
            # 1. Collect Active Threads to delete
            for thread in self.parent_channel.threads:
                if thread.id not in active_thread_ids:
                    print(f"[Shadow Chat] Queuing deletion for orphaned thread {thread.id}")
                    deletion_tasks.append(
                        self._safe_delete(thread, "Orphaned Shadow Chat thread")
                    )
            
            # 2. Collect Archived Threads to delete
            # Note: We must await the generator, but we queue the deletions
            async for thread in self.parent_channel.archived_threads():
                if thread.id not in active_thread_ids:
                    print(f"[Shadow Chat] Queuing deletion for orphaned archived thread {thread.id}")
                    deletion_tasks.append(
                        self._safe_delete(thread, "Orphaned archived Shadow Chat thread")
                    )
            
            # 3. Execute all deletions concurrently
            if deletion_tasks:
                await asyncio.gather(*deletion_tasks, return_exceptions=True)
                        
        except Exception as e:
            print(f"[Shadow Chat] Error cleaning orphaned threads: {e}")

    async def _safe_delete(self, thread: discord.Thread, reason: str):
        """Helper for concurrent cleanup to swallow errors per thread."""
        try:
            await thread.delete(reason=reason)
            print(f"[Shadow Chat] Successfully deleted {thread.id}")
        except Exception as e:
            # Silently fail for individual threads so others continue
            pass

    async def update_thread_name(self, thread_id: int, new_name: str) -> bool:
        """Update a thread's name."""
        thread = await self._get_thread_by_id(thread_id)
        if not thread:
            return False
        
        try:
            await thread.edit(name=new_name)
            return True
        except discord.HTTPException:
            return False
    
    async def get_all_threads(self) -> list:
        """Get all threads in the parent channel."""
        if not self.parent_channel:
            return []
        
        threads = list(self.parent_channel.threads)
        
        # Also add archived threads
        try:
            async for thread in self.parent_channel.archived_threads():
                threads.append(thread)
        except:
            pass
        
        return threads