"""
SHADOW CHAT - MAIN COG
Updated with skip command, shadow command group, and leave queue button
Database reports removed - reports go directly to mod channel
"""

import discord
from discord.ext import commands
from discord import app_commands
import datetime
import asyncio
import aiohttp
import io
from collections import OrderedDict
from typing import Optional

from . import constants
from .models import Database
from .queue_manager import QueueManager
from .thread_manager import ThreadManager
from .webhook_manager import WebhookManager
from .session_manager import SessionManager
from .relay_handler import RelayHandler
from .identity_manager import IdentityManager
from .inactivity_watcher import InactivityWatcher
from .permissions import AccessController, is_moderator
from .utils import get_thread_by_id


# ==================== LRU CACHE ====================

class LRUCache:
    """Thread-safe LRU cache with async lock support."""
    
    def __init__(self, max_size: int = 1000):
        self.max_size = max_size
        self.cache = OrderedDict()
        self._lock = asyncio.Lock()
    
    async def get(self, key):
        """Get a value from cache, updating its position as most recently used."""
        async with self._lock:
            if key not in self.cache:
                return None
            # Move to end (most recently used)
            self.cache.move_to_end(key)
            return self.cache[key]
    
    async def set(self, key, value):
        """Set a value in cache, removing oldest if at capacity."""
        async with self._lock:
            if key in self.cache:
                # Update existing key
                self.cache.move_to_end(key)
                self.cache[key] = value
            else:
                # Add new key
                self.cache[key] = value
                # Remove oldest if over capacity
                if len(self.cache) > self.max_size:
                    self.cache.popitem(last=False)
    
    async def delete(self, key):
        """Remove a key from cache."""
        async with self._lock:
            if key in self.cache:
                del self.cache[key]
    
    async def clear(self):
        """Clear all items from cache."""
        async with self._lock:
            self.cache.clear()
    
    async def size(self):
        """Get current cache size."""
        async with self._lock:
            return len(self.cache)
    
    async def keys(self):
        """Get all cache keys."""
        async with self._lock:
            return list(self.cache.keys())

# ==================== MAIN COG ====================

class ShadowChat(commands.Cog):
    """Anonymous chat system with private threads and webhook relaying."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db: Optional[Database] = None
        self.queue_manager: Optional[QueueManager] = None
        self.thread_manager: Optional[ThreadManager] = None
        self.webhook_manager: Optional[WebhookManager] = None
        self.session_manager: Optional[SessionManager] = None
        self.relay_handler: Optional[RelayHandler] = None
        self.identity_manager: Optional[IdentityManager] = None
        self.inactivity_watcher: Optional[InactivityWatcher] = None
        self.access_controller: Optional[AccessController] = None
        self.report_log_limit = 10
        
        # For tracking setup state
        self.is_setup = False
        self.entry_message: Optional[discord.Message] = None
        
        # Defer initialization until bot is ready
        self.initialized = False
        
        self.button_cooldowns = LRUCache(max_size=2000)  # user_id -> timestamp
        self.button_locks = {}  # custom_id -> asyncio.Lock (kept as dict for simplicity)
        self.user_locks = LRUCache(max_size=2000)  # user_id -> asyncio.Lock
        
        # For tracking interaction responses to prevent duplicates
        self.processed_interactions = LRUCache(max_size=5000)  # interaction_id -> timestamp
        self.processing_users = set()  # Users currently being processed
        
        # Cleanup task
        self.cleanup_task: Optional[asyncio.Task] = None
    
    async def initialize(self):
        """Initialize the cog after bot is ready."""
        print("[Shadow Chat] Initializing cog...")
        
        try:
            # Initialize database
            self.db = Database(constants.DB_PATH)
            await self.db.initialize()
            
            # Initialize managers
            self.access_controller = AccessController(self.db)
            self.queue_manager = QueueManager(self.bot, self.db, self.access_controller)
            
            self.thread_manager = ThreadManager(self.bot)
            await self.thread_manager.initialize()
            
            self.webhook_manager = WebhookManager(self.bot, self.db)
            await self.webhook_manager.initialize()
            
            self.session_manager = SessionManager(
                self.bot, self.db, self.thread_manager, self.webhook_manager
            )
            await self.session_manager.initialize()
            # Check that all components are initialized
            if not all([
                self.db,
                self.queue_manager,
                self.thread_manager,
                self.webhook_manager,
                self.session_manager,
                self.access_controller
            ]):
                raise RuntimeError("Failed to initialize all components")
            
            self.relay_handler = RelayHandler(
                self.bot, self.db, self.session_manager, self.webhook_manager
            )
            
            self.identity_manager = IdentityManager(
                self.bot, self.db, self.session_manager, self.webhook_manager
            )
            
            self.inactivity_watcher = InactivityWatcher(self.bot, self.session_manager)
            
            # Register callbacks
            self.queue_manager.register_queue_callback(self._on_queue_update)
            self.queue_manager.register_matchmaking_callback(self.process_queue_matchmaking)
            
            # Start inactivity watcher
            await self.inactivity_watcher.start()
            
            # RE-REGISTER PERSISTENT VIEWS AFTER RESTART
            await self._register_persistent_views()
            
            self.initialized = True
            print("[Shadow Chat] ✅ Cog initialized successfully")
            
        except Exception as e:
            print(f"[Shadow Chat] ❌ Failed to initialize cog: {e}")
            import traceback
            traceback.print_exc()
            # Don't set initialized to True if any component failed
            self.initialized = False
    
    async def _register_persistent_views(self):
        """Register persistent views after bot restart."""
        print("[Shadow Chat] Registering persistent views...")
        
        # Register the main entry view (updated with leave queue button)
        class EntryView(discord.ui.View):
            def __init__(self, cog):
                super().__init__(timeout=None)
                self.cog = cog
                self.processing_users = set()  # Track users currently being processed

            @discord.ui.button(label="Join Queue", style=discord.ButtonStyle.secondary, custom_id="shadow_join_queue", emoji="🕶️", row=0)
            async def join_queue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                # Check if user is already being processed
                if interaction.user.id in self.processing_users:
                    # Silently ignore duplicate clicks
                    try:
                        await interaction.response.defer(ephemeral=True, thinking=False)
                    except:
                        pass
                    return
                
            # Mark as processing
                self.processing_users.add(interaction.user.id)
                
                try:
                    # Acquire lock for this user
                    lock = await self.cog._get_user_lock(interaction.user.id)
                    async with lock:
                        # Check cooldown
                        if not await self.cog._check_cooldown(interaction.user.id):
                            await interaction.response.send_message(
                                "⏳ Please wait a moment before clicking again.",
                                ephemeral=True,
                                delete_after=2
                            )
                            return
                        
                        # Process the queue join
                        await self.cog.handle_join_queue(interaction)
                finally:
                    # Always remove from processing set
                    if interaction.user.id in self.processing_users:
                        self.processing_users.remove(interaction.user.id)
            
            @discord.ui.button(label="Queue Status", style=discord.ButtonStyle.secondary, custom_id="shadow_queue_status", emoji="📊", row=1)
            async def queue_status_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                # Check if user is already being processed
                if interaction.user.id in self.processing_users:
                    # Silently ignore duplicate clicks
                    try:
                        await interaction.response.defer(ephemeral=True, thinking=False)
                    except:
                        pass
                    return
                
                # Mark as processing
                self.processing_users.add(interaction.user.id)
                
                try:
                    # Acquire lock for this user
                    lock = await self.cog._get_user_lock(interaction.user.id)
                    async with lock:
                        # Check cooldown
                        if not await self.cog._check_cooldown(interaction.user.id):
                            await interaction.response.send_message(
                                "⏳ Please wait a moment before clicking again.",
                                ephemeral=True,
                                delete_after=2
                            )
                            return
                        
                        # Show queue status
                        await self.cog.handle_queue_status(interaction)
                finally:
                    # Always remove from processing set
                    if interaction.user.id in self.processing_users:
                        self.processing_users.remove(interaction.user.id)
            
            @discord.ui.button(label="Leave Queue", style=discord.ButtonStyle.secondary, custom_id="shadow_leave_queue", emoji="🚪", row=1)
            async def leave_queue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                # Check if user is already being processed
                if interaction.user.id in self.processing_users:
                    # Silently ignore duplicate clicks
                    try:
                        await interaction.response.defer(ephemeral=True, thinking=False)
                    except:
                        pass
                    return
                
                # Mark as processing
                self.processing_users.add(interaction.user.id)
                
                try:
                    # Acquire lock for this user
                    lock = await self.cog._get_user_lock(interaction.user.id)
                    async with lock:
                        # Check cooldown
                        if not await self.cog._check_cooldown(interaction.user.id):
                            await interaction.response.send_message(
                                "⏳ Please wait a moment before clicking again.",
                                ephemeral=True,
                                delete_after=2
                            )
                            return
                        
                        # Process the queue leave
                        await self.cog.handle_leave_queue_button(interaction)
                finally:
                    # Always remove from processing set
                    if interaction.user.id in self.processing_users:
                        self.processing_users.remove(interaction.user.id)
        
        # Add the view to the bot
        self.bot.add_view(EntryView(self))
        
        # Register session header views
        class SessionHeaderView(discord.ui.View):
            def __init__(self, cog):
                super().__init__(timeout=None)
                self.cog = cog
            
            @discord.ui.button(label="Reveal Identity", style=discord.ButtonStyle.primary, custom_id="shadow_reveal_", emoji="👁️", row=0)
            async def reveal_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                # This will be handled dynamically by on_interaction
                pass
            
            @discord.ui.button(label="End Chat", style=discord.ButtonStyle.danger, custom_id="shadow_end_", emoji="⏹️", row=0)
            async def end_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                # This will be handled dynamically by on_interaction
                pass
        
        self.bot.add_view(SessionHeaderView(self))
        
        # Register confirmation view for reveals
        class ConfirmRevealView(discord.ui.View):
            def __init__(self, cog):
                super().__init__(timeout=None)
                self.cog = cog
            
            @discord.ui.button(label="Confirm Reveal", style=discord.ButtonStyle.success, custom_id="shadow_confirm_reveal_", emoji="✅")
            async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                # This will be handled dynamically by on_interaction
                pass
            
            @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, custom_id="shadow_cancel_reveal_", emoji="❌")
            async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                # This will be handled dynamically by on_interaction
                pass
        
        self.bot.add_view(ConfirmRevealView(self))
        
        print("[Shadow Chat] Persistent views registered")
    
    def _get_lock(self, key):
        """Get or create a lock for the given key."""
        if key not in self.button_locks:
            self.button_locks[key] = asyncio.Lock()
        return self.button_locks[key]
    
    async def _get_user_lock(self, user_id: int):
        """Get or create a lock for a specific user."""
        lock = await self.user_locks.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            await self.user_locks.set(user_id, lock)
        return lock
    
    async def _check_cooldown(self, user_id: int, cooldown_seconds: int = 2):
        """Check if user is on cooldown for button clicks."""
        now = datetime.datetime.now().timestamp()
        
        last_click = await self.button_cooldowns.get(user_id)
        if last_click is not None:
            if now - last_click < cooldown_seconds:
                return False
        
        await self.button_cooldowns.set(user_id, now)
        return True
    
    async def _on_queue_update(self):
        """Handle queue updates."""
        print("[Shadow Chat] Queue updated")
    
    @commands.Cog.listener()
    async def on_ready(self):
        """Initialize when bot is ready."""
        if not self.initialized:
            await self.initialize()
    
    async def cog_load(self):
        """Cog load handler."""
        print("[Shadow Chat] Cog loaded")
        # Start cleanup task for processed interactions
        self.cleanup_task = asyncio.create_task(self._cleanup_old_interactions())
    
    async def _cleanup_old_interactions(self):
        """Clean up old processed interactions."""
        while True:
            await asyncio.sleep(300)  # Clean up every 5 minutes
            try:
                now = discord.utils.utcnow()
                # Get all keys to check (this is safe with LRU cache)
                keys = await self.processed_interactions.keys()
                
                for key in keys:
                    timestamp = await self.processed_interactions.get(key)
                    if timestamp and (now - timestamp).total_seconds() > 600:
                        await self.processed_interactions.delete(key)
                
                # Also clean up old cooldowns
                current_time = datetime.datetime.now().timestamp()
                cooldown_keys = await self.button_cooldowns.keys()
                
                for user_id in cooldown_keys:
                    timestamp = await self.button_cooldowns.get(user_id)
                    if timestamp and current_time - timestamp > 30:
                        await self.button_cooldowns.delete(user_id)
                        
                # No need to clear user_locks - LRU handles it automatically
                # Check and log cache sizes
                user_locks_size = await self.user_locks.size()
                if user_locks_size > 1000:
                    print(f"[Shadow Chat] User lock cache size: {user_locks_size}")
                    
            except Exception as e:
                print(f"[Shadow Chat] Cleanup error: {e}")
    
    async def cog_unload(self):
        """Clean up on cog unload."""
        print("[Shadow Chat] Unloading cog...")
        
        if self.cleanup_task:
            self.cleanup_task.cancel()
            try:
                await self.cleanup_task
            except asyncio.CancelledError:
                pass
        
        # Clear all caches
        await self.processed_interactions.clear()
        await self.button_cooldowns.clear()
        await self.user_locks.clear()
        
        if self.inactivity_watcher:
            await self.inactivity_watcher.stop()
        
        if self.webhook_manager:
            await self.webhook_manager.close()
        
        if self.db:
            await self.db.close()
        
        print("[Shadow Chat] Cog unloaded")
    
    async def setup_entry_channel(self):
        """Set up the entry channel with join button."""
        channel = self.bot.get_channel(constants.ENTRY_CHANNEL_ID)
        if not channel:
            print(f"[Shadow Chat] Entry channel {constants.ENTRY_CHANNEL_ID} not found!")
            return
        
        # Clear existing messages (optional)
        try:
            await channel.purge(limit=10)
        except:
            pass
        
        # Create welcome embed
        embed = discord.Embed(
            title="🐈‍⬛ Shadow Cat's Domain",
            description="*\"Psst. Over here, in the dark... <:bhondubilla:1442209308153614458>\"*\n"
                        "Welcome, traveler. I am **Shadow Cat**, your mysterious guide through the unseen pathways of this server. <:cutepepe:1400771649728548904>\n"
                        "I specialize in connecting lost souls for anonymous conversation. Trust my whiskers, and I'll help you find new friends from behind the veil.\n\n"
                        "**Ready to prowl?** Use the buttons below to step into the shadows. <:02ic:1392121893477290085>",
            color=constants.COLOR_ANONYMOUS
        )



        embed.add_field(
            name="🐾 Commands",
            value="`/reveal` • Request to lift the veil (mutual identity reveal).\n"
                "`/end` • Vanish back into the night (end session).\n"
                "`/skip` • Slink away to find another (skip partner).\n"
                "`/complain` • Hiss at bad behavior (report user).\n"
                "`/leave` • Step out of the shadows completely.",
            inline=False
        )

        embed.set_thumbnail(url=constants.MAIN_MESSAGE_THUMBNAIL)
        embed.set_image(url=constants.MAIN_MESSAGE_IMAGE)
        embed.colour=constants.MAIN_MESSAGE_COLOR

        # Add queue and rooms status
        if self.initialized:
            queue_size = await self.db.get_queue_size()
            active_sessions = await self.session_manager.get_all_active_sessions()
            available_rooms = constants.MAX_ACTIVE_SESSIONS - len(active_sessions)
            
            # Show if rooms are full but queue is still open
            room_status = ""
            if available_rooms <= 0:
                room_status = " (Rooms full - you'll be queued)"
            
        
        embed.set_footer(text="Use the buttons below to control your queue status")
        
        # Create button view with all three buttons
        class EntryView(discord.ui.View):
            def __init__(self, cog):
                super().__init__(timeout=None)
                self.cog = cog
                self.processing_users = set()  # Track users currently being processed
            
            @discord.ui.button(label="Join Shadow Chat", style=discord.ButtonStyle.primary, custom_id="shadow_join_queue", emoji="🕶️", row=0)
            async def join_queue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                # Check if user is already being processed
                if interaction.user.id in self.processing_users:
                    # Silently ignore duplicate clicks
                    try:
                        await interaction.response.defer(ephemeral=True, thinking=False)
                    except:
                        pass
                    return
                
                # Mark as processing
                self.processing_users.add(interaction.user.id)
                
                try:
                    # Acquire lock for this user
                    lock = await self.cog._get_user_lock(interaction.user.id)
                    async with lock:
                        # Check cooldown
                        if not await self.cog._check_cooldown(interaction.user.id):
                            await interaction.response.send_message(
                                "⏳ Please wait a moment before clicking again.",
                                ephemeral=True,
                                delete_after=2
                            )
                            return
                        
                        # Process the queue join
                        await self.cog.handle_join_queue(interaction)
                finally:
                    # Always remove from processing set
                    if interaction.user.id in self.processing_users:
                        self.processing_users.remove(interaction.user.id)
            
            @discord.ui.button(label="Queue Status", style=discord.ButtonStyle.secondary, custom_id="shadow_queue_status", emoji="📊", row=0)
            async def queue_status_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                # Check if user is already being processed
                if interaction.user.id in self.processing_users:
                    # Silently ignore duplicate clicks
                    try:
                        await interaction.response.defer(ephemeral=True, thinking=False)
                    except:
                        pass
                    return
                
                # Mark as processing
                self.processing_users.add(interaction.user.id)
                
                try:
                    # Acquire lock for this user
                    lock = await self.cog._get_user_lock(interaction.user.id)
                    async with lock:
                        # Check cooldown
                        if not await self.cog._check_cooldown(interaction.user.id):
                            await interaction.response.send_message(
                                "⏳ Please wait a moment before clicking again.",
                                ephemeral=True,
                                delete_after=2
                            )
                            return
                        
                        # Show queue status
                        await self.cog.handle_queue_status(interaction)
                finally:
                    # Always remove from processing set
                    if interaction.user.id in self.processing_users:
                        self.processing_users.remove(interaction.user.id)
            
            @discord.ui.button(label="Leave Queue", style=discord.ButtonStyle.danger, custom_id="shadow_leave_queue", emoji="🚪", row=0)
            async def leave_queue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                # Check if user is already being processed
                if interaction.user.id in self.processing_users:
                    # Silently ignore duplicate clicks
                    try:
                        await interaction.response.defer(ephemeral=True, thinking=False)
                    except:
                        pass
                    return
                
                # Mark as processing
                self.processing_users.add(interaction.user.id)
                
                try:
                    # Acquire lock for this user
                    lock = await self.cog._get_user_lock(interaction.user.id)
                    async with lock:
                        # Check cooldown
                        if not await self.cog._check_cooldown(interaction.user.id):
                            await interaction.response.send_message(
                                "⏳ Please wait a moment before clicking again.",
                                ephemeral=True,
                                delete_after=2
                            )
                            return
                        
                        # Process the queue leave
                        await self.cog.handle_leave_queue_button(interaction)
                finally:
                    # Always remove from processing set
                    if interaction.user.id in self.processing_users:
                        self.processing_users.remove(interaction.user.id)
        
        # Send message
        self.entry_message = await channel.send(embed=embed, view=EntryView(self))
        print(f"[Shadow Chat] Entry channel setup complete")
    
    def _calculate_estimated_wait(self, position: int) -> str:
        """Calculate estimated wait time based on position."""
        if position == 1:
            return "Next in line"
        
        # Base estimate: ~15 minutes per session, divided by 2 (since 2 users per session)
        # Position 2-3: ~7-10 minutes (waiting for current sessions to end)
        # Position 4+: ~(position * 5-7) minutes
        
        if position <= 3:
            estimated_minutes = (position - 1) * 5
        else:
            estimated_minutes = (position - 1) * 6
        
        if estimated_minutes < 1:
            return "Less than a minute"
        elif estimated_minutes == 1:
            return "~1 minute"
        elif estimated_minutes < 60:
            return f"~{estimated_minutes} minutes"
        else:
            hours = estimated_minutes // 60
            minutes = estimated_minutes % 60
            if minutes > 0:
                return f"~{hours}h {minutes}m"
            return f"~{hours}h"
    
    # ==================== USER COMMANDS ====================
    
    @app_commands.command(name="reveal", description="Request identity reveal with mutual consent")
    async def reveal(self, interaction: discord.Interaction):
        """Request to reveal identities in current session."""
        await interaction.response.defer(ephemeral=True)
        
        # Check if user is in an active session
        session = await self.session_manager.get_session_by_user(interaction.user.id)
        if not session:
            await interaction.followup.send(
                "❌ You're not in an active Shadow Chat session.",
                ephemeral=True
            )
            return
        
        # Check if identities are already revealed
        if session.identities_revealed:
            # Get the other user's info
            other_user_id = session.user2_id if session.user1_id == interaction.user.id else session.user1_id
            other_user = self.bot.get_user(other_user_id)
            
            if other_user:
                embed = discord.Embed(
                    title="👥 Identities Already Revealed",
                    description=f"You already know each other!",
                    color=constants.COLOR_SUCCESS
                )
                embed.add_field(
                    name="👤 The other person is:",
                    value=f"**{other_user.name}**#{other_user.discriminator}",
                    inline=False
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.followup.send(
                    "✅ Identities are already revealed in this session.",
                    ephemeral=True
                )
            return
        
        # Use identity manager to request reveal
        lock = await self._get_user_lock(interaction.user.id)
        async with lock:
            success, message = await self.identity_manager.request_reveal(
                interaction.user.id,
                session.session_id
            )
        
        if success:
            embed = discord.Embed(
                title="🔄 Reveal Request Sent",
                description=message,
                color=constants.COLOR_SUCCESS
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            embed = discord.Embed(
                title="❌ Reveal Failed",
                description=message,
                color=constants.COLOR_ERROR
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
    
    @app_commands.command(name="end", description="End your current Shadow Chat session")
    async def end(self, interaction: discord.Interaction):
        """End your current Shadow Chat session."""
        await interaction.response.defer(ephemeral=True)
        
        # Check if user is in an active session
        session = await self.session_manager.get_session_by_user(interaction.user.id)
        if not session:
            await interaction.followup.send(
                "❌ You're not in an active Shadow Chat session.",
                ephemeral=True
            )
            return
        
        # Get the other user info before ending
        other_user_id = session.user2_id if session.user1_id == interaction.user.id else session.user1_id
        
        # Confirm with the user
        embed = discord.Embed(
            title="⚠️ End Session Confirmation",
            description="Are you sure you want to end this Shadow Chat session?",
            color=constants.COLOR_WARNING
        )
        
        # Show if identities were revealed
        if session.identities_revealed:
            other_user = self.bot.get_user(other_user_id)
            if other_user:
                embed.add_field(
                    name="👤 Chatting with:",
                    value=f"**{other_user.name}**#{other_user.discriminator}",
                    inline=False
                )
        else:
            embed.add_field(
                name="👤 Chatting with:",
                value="Anonymous User",
                inline=False
            )
        
        embed.set_footer(text="This will end the session for both users")
        
        # Create confirmation buttons
        class ConfirmView(discord.ui.View):
            def __init__(self, cog, session_id):
                super().__init__(timeout=30)
                self.cog = cog
                self.session_id = session_id
                self.confirmed = False
            
            @discord.ui.button(label="Yes, End Session", style=discord.ButtonStyle.danger)
            async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                if interaction.user.id != session.user1_id and interaction.user.id != session.user2_id:
                    await interaction.response.send_message(
                        "❌ You're not part of this session.",
                        ephemeral=True
                    )
                    return
                
                self.confirmed = True
                button.disabled = True
                button.label = "Ending..."
                self.children[1].disabled = True
                
                await interaction.response.edit_message(view=self)
                
                # End the session
                await self.cog.session_manager.end_session(
                    self.session_id,
                    f"Session ended by user via /end command"
                )
                
                embed = discord.Embed(
                    title="✅ Session Ended",
                    description="Your Shadow Chat session has been ended.",
                    color=constants.COLOR_SUCCESS
                )
                try:
                    await interaction.followup.send(embed=embed, ephemeral=True)
                except:
                    pass    
            
            @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
            async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                if interaction.user.id != session.user1_id and interaction.user.id != session.user2_id:
                    await interaction.response.send_message(
                        "❌ You're not part of this session.",
                        ephemeral=True
                    )
                    return
                
                await interaction.response.edit_message(
                    content="❌ Session end cancelled.",
                    embed=None,
                    view=None
                )
            
            async def on_timeout(self):
                if not self.confirmed:
                    # Nothing to do here as the view will stop working
                    pass
        
        view = ConfirmView(self, session.session_id)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    
    @app_commands.command(name="skip", description="Skip your current partner and re-queue")
    async def skip(self, interaction: discord.Interaction):
        """Skip current partner and get re-queued for a new match."""
        await interaction.response.defer(ephemeral=True)
        
        # Check if user is in an active session
        session = await self.session_manager.get_session_by_user(interaction.user.id)
        if not session:
            await interaction.followup.send(
                "❌ You're not in an active Shadow Chat session.",
                ephemeral=True
            )
            return
        
        # Check if session is already ending
        if session.ending:
            await interaction.followup.send(
                "❌ This session is already ending.",
                ephemeral=True
            )
            return
        
        # Confirm skip with user
        embed = discord.Embed(
            title="⏭️ Skip Partner",
            description="Are you sure you want to skip your current partner?",
            color=constants.COLOR_WARNING
        )
        
        embed.add_field(
            name="What happens:",
            value="• You'll be re-added to the queue\n"
                  "• Current session ends for both users\n"
                  "• You'll be matched with someone new",
            inline=False
        )
        
        embed.add_field(
            name="Note:",
            value="Your current partner will also be removed from the session.",
            inline=False
        )
        
        class SkipConfirmView(discord.ui.View):
            def __init__(self, cog, session_id):
                super().__init__(timeout=30)
                self.cog = cog
                self.session_id = session_id
                self.confirmed = False
            
            @discord.ui.button(label="Skip Partner", style=discord.ButtonStyle.primary, emoji="⏭️")
            async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                if interaction.user.id != session.user1_id and interaction.user.id != session.user2_id:
                    await interaction.response.send_message(
                        "❌ You're not part of this session.",
                        ephemeral=True
                    )
                    return
                
                self.confirmed = True
                button.disabled = True
                button.label = "Skipping..."
                self.children[1].disabled = True
                
                await interaction.response.edit_message(view=self)
                
                # Skip the session (this will handle re-queuing)
                success, message = await self.cog.session_manager.skip_session(
                    self.session_id,
                    interaction.user.id
                )
                
                if success:
                    embed = discord.Embed(
                        title="✅ Partner Skipped",
                        description=message,
                        color=constants.COLOR_SUCCESS
                    )
                else:
                    embed = discord.Embed(
                        title="❌ Skip Failed",
                        description=message,
                        color=constants.COLOR_ERROR
                    )
                
                await interaction.followup.send(embed=embed, ephemeral=True)
            
            @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
            async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                if interaction.user.id != session.user1_id and interaction.user.id != session.user2_id:
                    await interaction.response.send_message(
                        "❌ You're not part of this session.",
                        ephemeral=True
                    )
                    return
                
                await interaction.response.edit_message(
                    content="❌ Skip cancelled.",
                    embed=None,
                    view=None
                )
            
            async def on_timeout(self):
                if not self.confirmed:
                    pass
        
        view = SkipConfirmView(self, session.session_id)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


    @app_commands.command(name="complain", description="Report your current session to moderators")
    @app_commands.describe(reason="The reason for reporting this user")
    async def report(self, interaction: discord.Interaction, reason: str):
            """Report current partner and log recent chat history with permanent evidence."""
            await interaction.response.defer(ephemeral=True)

            # 1. Validate Session
            session = await self.session_manager.get_session_by_user(interaction.user.id)
            if not session:
                await interaction.followup.send("❌ You are not in an active Shadow Chat session.", ephemeral=True)
                return

            # 2. Get Mod Channel
            mod_channel = self.bot.get_channel(constants.MOD_CHANNEL_ID)
            if not mod_channel:
                await interaction.followup.send("❌ Configuration error: Mod channel not found.", ephemeral=True)
                return

            # 3. Identify Participants
            reporter = interaction.user
            other_user_id = session.user2_id if session.user1_id == reporter.id else session.user1_id
            reported_user = self.bot.get_user(other_user_id) 

            # 4. Fetch & Process Chat History
            log_lines = []
            files_to_upload = [] # List to store discord.File objects
            evidence_count = 0 

            # Fetch last N messages
            history = [msg async for msg in interaction.channel.history(limit=self.report_log_limit)]
            history.reverse() 

            # We need a web session for stickers (attachments have a built-in read method)
            async with aiohttp.ClientSession() as web_session:
                for msg in history:
                    # A. Determine Author
                    timestamp = msg.created_at.strftime("%H:%M:%S")
                    if msg.author.id == reporter.id:
                        author_display = "🟦 You"
                    elif msg.webhook_id: 
                        author_display = "🟥 Partner"
                    else:
                        author_display = "⬜ System"

                    content = msg.clean_content if msg.clean_content else ""
                    extras = []

                    # --- B. PROCESS ATTACHMENTS (Download & Prepare for Re-upload) ---
                    if msg.attachments:
                        for att in msg.attachments:
                            # Limit evidence to prevent bot timeout/errors (Max 10 per report)
                            if evidence_count < 10:
                                try:
                                    # Download bytes into memory
                                    image_data = await att.read()
                                    file_obj = discord.File(io.BytesIO(image_data), filename=f"evidence_{att.filename}")
                                    files_to_upload.append(file_obj)
                                    extras.append(f"[IMAGE: {att.filename}]")
                                    evidence_count += 1
                                except Exception as e:
                                    extras.append(f"[FAILED DOWNLOAD: {att.filename}]")
                            else:
                                extras.append(f"[IMAGE: {att.filename} (Limit Reached)]")

                    # --- C. PROCESS STICKERS (Download & Prepare for Re-upload) ---
                    if msg.stickers:
                        for sticker in msg.stickers:
                            if evidence_count < 10:
                                try:
                                    # Stickers don't have .read(), need standard HTTP get
                                    async with web_session.get(sticker.url) as resp:
                                        if resp.status == 200:
                                            sticker_data = await resp.read()
                                            # Convert to file
                                            ext = "png" if sticker.format == discord.StickerFormatType.png else "json"
                                            file_obj = discord.File(io.BytesIO(sticker_data), filename=f"evidence_sticker_{sticker.name}.{ext}")
                                            files_to_upload.append(file_obj)
                                            extras.append(f"[STICKER: {sticker.name}]")
                                            evidence_count += 1
                                except Exception:
                                    extras.append("[STICKER (Failed)]")
                            else:
                                extras.append("[STICKER (Limit Reached)]")

                    # Check Embeds
                    if msg.embeds:
                        extras.append("[EMBED/GIF]")

                    # Format Line
                    extra_str = " " + " ".join(extras) if extras else ""
                    log_lines.append(f"`[{timestamp}]` **{author_display}:** {content}{extra_str}")

            # 5. Build Report Embed
            duration = discord.utils.utcnow() - session.created_at
            duration_str = f"{int(duration.total_seconds() // 60)}m {int(duration.total_seconds() % 60)}s"

            embed = discord.Embed(
                title="🚨 Shadow Chat Report (Evidence Secured)",
                description="**Media files have been re-uploaded to this channel.**",
                color=discord.Color.red(),
                timestamp=discord.utils.utcnow()
            )

            embed.add_field(name="👤 Reporter", value=f"{reporter.mention} (`{reporter.id}`)", inline=True)
            embed.add_field(name="⚠️ Reported User", value=f"{reported_user.mention if reported_user else 'Unknown'} (`{other_user_id}`)", inline=True)
            embed.add_field(name="⏱️ Duration", value=duration_str, inline=True)
            embed.add_field(name="🆔 Session ID", value=f"`{session.session_id}`", inline=False)
            embed.add_field(name="📝 Reason", value=f"```{reason}```", inline=False)

            # Log Logic
            full_log = "\n".join(log_lines) if log_lines else "*No recent messages found.*"
            if len(full_log) > 1024:
                full_log = full_log[:1020] + "..."
            
            embed.add_field(name=f"📜 Last {len(history)} Messages", value=full_log, inline=False)

            # 6. Send to Mods with Files
            # Note: We send files + embed in one go. 
            # Discord limits: 10 files per message. The loop above caps at 10.
            try:
                await mod_channel.send(content=f"{session.session_id}", embed=embed, files=files_to_upload)
            except discord.HTTPException as e:
                # Fallback if files are too massive
                await mod_channel.send(embed=embed)
                await mod_channel.send(f"⚠️ **Error uploading evidence files:** {e}")

            # 7. Confirm to User
            await interaction.followup.send(
                "✅ **Report Submitted.** Moderators have been notified!\nEnding this session.",
                ephemeral=True
            )
            await self.session_manager.end_session(
                session.session_id,
                f"Session ended due to report by {interaction.user.id}"
            )

    @app_commands.command(name="leave", description="Leave the Shadow Chat queue")
    async def leave(self, interaction: discord.Interaction):
        """Leave the queue if you're in it."""
        await interaction.response.defer(ephemeral=True)
        
        # Check if in queue
        queue_info = await self.queue_manager.get_queue_info(interaction.user.id)
        if queue_info:
            await self.queue_manager.remove_from_queue(interaction.user.id)
            
            embed = discord.Embed(
                title="✅ Left Queue",
                description="You've been removed from the Shadow Chat queue.",
                color=constants.COLOR_SUCCESS
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        
        # Check if in active session
        session = await self.session_manager.get_session_by_user(interaction.user.id)
        if session:
            await interaction.followup.send(
                "❌ You're in an active session. Use `/end` to end your session instead.",
                ephemeral=True
            )
            return
        
        await interaction.followup.send(
            "❌ You're not in the queue or an active session.",
            ephemeral=True
        )
    
    # ==================== SHADOW MODERATION COMMANDS ====================
    
    shadow_group = app_commands.Group(
            name="shadow", 
            description="Shadow Chat moderation commands"
    )
    
    @shadow_group.command(name="stats", description="View Shadow Chat statistics (Mod only)")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def shadow_stats(self, interaction: discord.Interaction):
        """View system statistics."""
        if not is_moderator(interaction.user):
            await interaction.response.send_message(
                "Moderator only.",
                ephemeral=True
            )
            return
        
        await interaction.response.defer(thinking=True, ephemeral=True)
        
        # Gather stats
        queue_size = await self.db.get_queue_size()
        active_sessions = await self.session_manager.get_all_active_sessions()
        queue_stats = await self.queue_manager.get_queue_stats()
        
        embed = discord.Embed(
            title="📊 Shadow Chat Statistics",
            color=constants.COLOR_ANONYMOUS,
            timestamp=discord.utils.utcnow()
        )
        
        embed.add_field(
            name="Queue",
            value=f"**Size:** {queue_size}\n"
                  f"**Oldest wait:** {queue_stats['oldest_wait'].strftime('%H:%M:%S') if queue_stats['oldest_wait'] else 'N/A'}",
            inline=True
        )
        
        embed.add_field(
            name="Sessions",
            value=f"**Active:** {len(active_sessions)}\n"
                  f"**Max:** {constants.MAX_ACTIVE_SESSIONS}",
            inline=True
        )
        
        # User stats
        total_users_cursor = await self.db.conn.execute(
            "SELECT COUNT(*) FROM shadow_users"
        )
        total_users = (await total_users_cursor.fetchone())[0]
        
        banned_users_cursor = await self.db.conn.execute(
            "SELECT COUNT(*) FROM shadow_users WHERE is_banned = 1"
        )
        banned_users = (await banned_users_cursor.fetchone())[0]
        
        embed.add_field(
            name="Users",
            value=f"**Total:** {total_users}\n"
                  f"**Banned:** {banned_users}",
            inline=True
        )
        
        # Recent sessions
        recent_sessions_cursor = await self.db.conn.execute("""
            SELECT COUNT(*) FROM shadow_sessions 
            WHERE created_at > datetime('now', '-1 hour')
        """)
        recent_sessions = (await recent_sessions_cursor.fetchone())[0]
        
        embed.add_field(
            name="Recent Activity",
            value=f"**Last hour:** {recent_sessions} sessions",
            inline=False
        )
        
        await interaction.followup.send(embed=embed, ephemeral=True)
    
    @shadow_group.command(name="setup", description="Setup Shadow Chat system (Mod only)")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def shadow_setup(self, interaction: discord.Interaction):
        """Setup command for moderators."""
        if not is_moderator(interaction.user):
            await interaction.response.send_message(
                "You need moderator permissions to use this command.",
                ephemeral=True
            )
            return
        
        await interaction.response.defer(thinking=True, ephemeral=True)
        
        try:
            await self.setup_entry_channel()
            await interaction.followup.send(
                "✅ Shadow Chat setup complete!",
                ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(
                f"❌ Setup failed: {e}",
                ephemeral=True
            )
    
    @shadow_group.command(name="ban", description="Ban a user from Shadow Chat (Mod only)")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def shadow_ban(self, interaction: discord.Interaction, user: discord.User, reason: str = "No reason provided"):
        """Ban a user from using Shadow Chat."""
        if not is_moderator(interaction.user):
            await interaction.response.send_message(
                "Moderator only.",
                ephemeral=True
            )
            return
        
        await interaction.response.defer(thinking=True, ephemeral=True)
        
        # Check if user is already banned
        is_banned = await self.access_controller.is_user_banned(user.id)
        if is_banned:
            await interaction.followup.send(
                f"❌ {user.mention} is already banned from Shadow Chat.",
                ephemeral=True
            )
            return
        
        # Ban the user
        success = await self.access_controller.ban_user(
            user.id,
            interaction.user.id,
            reason
        )
        
        if success:
            # Remove from queue if they're in it
            await self.queue_manager.remove_from_queue(user.id)
            
            # End any active session they're in
            session = await self.session_manager.get_session_by_user(user.id)
            if session:
                await self.session_manager.end_session(
                    session.session_id,
                    f"User banned by moderator"
                )
            
            embed = discord.Embed(
                title="✅ User Banned",
                description=f"{user.mention} has been banned from Shadow Chat.",
                color=constants.COLOR_SUCCESS
            )
            embed.add_field(
                name="Reason",
                value=reason,
                inline=False
            )
            embed.add_field(
                name="Moderator",
                value=interaction.user.mention,
                inline=False
            )
            
            await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send(
                f"❌ Failed to ban {user.mention}.",
                ephemeral=True
            )
    
    @shadow_group.command(name="unban", description="Unban a user from Shadow Chat (Mod only)")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def shadow_unban(self, interaction: discord.Interaction, user: discord.User):
        """Unban a user from Shadow Chat."""
        await interaction.response.defer(ephemeral=True)
        
        # Pass the moderator (the person who issued the command)
        success = await self.access_controller.unban_user(user.id, interaction.user)
        
        if success:
            await interaction.followup.send(
                f"✅ {user.mention} has been unbanned from Shadow Chat.",
                ephemeral=True
            )
        else:
            await interaction.followup.send(
                "❌ Failed to unban user. They might not be banned.",
                ephemeral=True
            )
    
    @shadow_group.command(name="view_queue", description="View current queue (Mod only)")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def shadow_view_queue(self, interaction: discord.Interaction):
        """View current queue."""
        if not is_moderator(interaction.user):
            await interaction.response.send_message(
                "Moderator only.",
                ephemeral=True
            )
            return
        
        await interaction.response.defer(thinking=True, ephemeral=True)
        
        # Get queue
        queue_users = await self.queue_manager.get_queue_users()
        
        embed = discord.Embed(
            title="📋 Current Queue",
            color=constants.COLOR_ANONYMOUS,
            timestamp=discord.utils.utcnow()
        )
        
        if not queue_users:
            embed.description = "✅ Queue is empty."
        else:
            embed.description = f"**Total in queue:** {len(queue_users)}"
            
            queue_list = []
            for i, user_id in enumerate(queue_users, 1):  # user_id is int, not QueueEntry
                user = self.bot.get_user(user_id)
                # We can't get wait_time without querying the database
                # For now, just show the user
                queue_list.append(
                    f"{i}. {user.mention if user else f'Unknown ({user_id})'}"
                )
            
            # Split into chunks to avoid field value limits
            chunks = [queue_list[i:i+10] for i in range(0, len(queue_list), 10)]
            
            for i, chunk in enumerate(chunks):
                embed.add_field(
                    name=f"Users {i*10+1}-{i*10+len(chunk)}",
                    value="\n".join(chunk),
                    inline=False
                )
        
        await interaction.followup.send(embed=embed, ephemeral=True)
    
    @shadow_group.command(name="active_sessions", description="View active sessions (Mod only)")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def shadow_active_sessions(self, interaction: discord.Interaction):
        """View active sessions."""
        if not is_moderator(interaction.user):
            await interaction.response.send_message(
                "Moderator only.",
                ephemeral=True
            )
            return
        
        await interaction.response.defer(thinking=True, ephemeral=True)
        
        # Get active sessions
        sessions = await self.session_manager.get_all_active_sessions()
        
        embed = discord.Embed(
            title="🎭 Active Sessions",
            color=constants.COLOR_ANONYMOUS,
            timestamp=discord.utils.utcnow()
        )
        
        if not sessions:
            embed.description = "✅ No active sessions."
        else:
            embed.description = f"**Active sessions:** {len(sessions)}/{constants.MAX_ACTIVE_SESSIONS}"
            
            for i, session in enumerate(sessions[:5], 1):  # Show first 5
                user1 = self.bot.get_user(session.user1_id)
                user2 = self.bot.get_user(session.user2_id)
                session_time = (discord.utils.utcnow() - session.created_at).seconds
                
                embed.add_field(
                    name=f"Session #{i}",
                    value=f"**ID:** `{session.session_id}`\n"
                          f"**Users:** {user1.mention if user1 else 'Unknown'} & "
                          f"{user2.mention if user2 else 'Unknown'}\n"
                          f"**Duration:** {session_time // 60}m {session_time % 60}s\n"
                          f"**Threads:** <#{session.thread1_id}> & <#{session.thread2_id}>\n"
                          f"**Revealed:** {'✅ Yes' if session.identities_revealed else '❌ No'}",
                    inline=False
                )
        
        if len(sessions) > 5:
            embed.set_footer(text=f"And {len(sessions) - 5} more sessions...")
        
        await interaction.followup.send(embed=embed, ephemeral=True)
    
    # ==================== BUTTON HANDLERS ====================
    
    async def handle_queue_status(self, interaction: discord.Interaction):
        """Handle queue status button click."""
        # Generate a unique key for this interaction to prevent duplicates
        interaction_key = f"{interaction.id}_{interaction.user.id}"
        
        # Check if this interaction was already processed
        if await self.processed_interactions.get(interaction_key):
            # Already processing, ignore duplicate
            try:
                await interaction.response.defer(ephemeral=True, thinking=False)
            except:
                pass
            return
        
        # Mark as processing
        await self.processed_interactions.set(interaction_key, discord.utils.utcnow())
        
        try:
            await interaction.response.defer(ephemeral=True)
            
            # Get current queue info
            queue_size = await self.db.get_queue_size()
            active_sessions = await self.session_manager.get_all_active_sessions()
            queue_stats = await self.queue_manager.get_queue_stats()
            
            # Create status embed
            embed = discord.Embed(
                title="📊 Shadow Chat Status",
                color=constants.COLOR_ANONYMOUS,
                timestamp=discord.utils.utcnow()
            )
            
            # Available rooms
            available_rooms = max(0, constants.MAX_ACTIVE_SESSIONS - len(active_sessions))
            
            embed.add_field(
                name="Room Availability",
                value=f"**Available:** {available_rooms} / {constants.MAX_ACTIVE_SESSIONS}\n"
                      f"**In Use:** {len(active_sessions)} rooms\n"
                      f"**Status:** {'✅ Available' if available_rooms > 0 else '⏳ Full - Queueing enabled'}",
                inline=False
            )
            
            # Queue information
            if queue_size > 0:
                wait_time = self._calculate_estimated_wait(queue_size)
                
                embed.add_field(
                    name="Queue Status",
                    value=f"**People waiting:** {queue_size}\n"
                          f"**Estimated wait:** {wait_time}\n"
                          f"**Oldest in queue:** {queue_stats['oldest_wait'].strftime('%H:%M:%S') if queue_stats['oldest_wait'] else 'N/A'}",
                    inline=False
                )
                
                # User's position in queue if they're in it
                queue_info = await self.queue_manager.get_queue_info(interaction.user.id)
                if queue_info:
                    embed.add_field(
                        name="Your Position",
                        value=f"**Position:** #{queue_info.position}\n"
                              f"**Joined:** <t:{int(queue_info.joined_at.timestamp())}:R>",
                        inline=False
                    )
            else:
                embed.add_field(
                    name="Queue Status",
                    value="✅ No one in queue - instant match available!",
                    inline=False
                )
            
            # Current sessions info
            if active_sessions:
                embed.add_field(
                    name="Active Sessions",
                    value=f"**Currently active:** {len(active_sessions)}\n"
                          f"**Session duration limit:** 2 hours",
                    inline=False
                )
            
            # Add refresh button
            class RefreshView(discord.ui.View):
                def __init__(self, cog, interaction_key):
                    super().__init__(timeout=120)
                    self.cog = cog
                    self.interaction_key = interaction_key
                
                @discord.ui.button(label="🔄 Refresh", style=discord.ButtonStyle.secondary, custom_id="refresh_status")
                async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                    # Check if already processing
                    refresh_key = f"{interaction.id}_{interaction.user.id}_refresh"
                    if refresh_key in self.cog.processed_interactions:
                        try:
                            await interaction.response.defer(ephemeral=True, thinking=False)
                        except:
                            pass
                        return
                    
                    await self.cog.processed_interactions.set(refresh_key, discord.utils.utcnow())
                    
                    try:
                        lock = await self.cog._get_user_lock(interaction.user.id)
                        async with lock:
                            if not await self.cog._check_cooldown(interaction.user.id, 5):
                                await interaction.response.send_message(
                                    "⏳ Please wait a moment before refreshing again.",
                                    ephemeral=True,
                                    delete_after=2
                                )
                                return
                            
                            await interaction.response.defer(ephemeral=True)
                            
                            # Get updated stats
                            queue_size = await self.cog.db.get_queue_size()
                            active_sessions = await self.cog.session_manager.get_all_active_sessions()
                            queue_stats = await self.cog.queue_manager.get_queue_stats()
                            
                            # Update embed
                            embed = discord.Embed(
                                title="📊 Shadow Chat Status",
                                color=constants.COLOR_ANONYMOUS,
                                timestamp=discord.utils.utcnow()
                            )
                            
                            available_rooms = max(0, constants.MAX_ACTIVE_SESSIONS - len(active_sessions))
                            
                            embed.add_field(
                                name="Room Availability",
                                value=f"**Available:** {available_rooms} / {constants.MAX_ACTIVE_SESSIONS}\n"
                                      f"**In Use:** {len(active_sessions)} rooms",
                                inline=False
                            )
                            
                            if queue_size > 0:
                                wait_time = self.cog._calculate_estimated_wait(queue_size)
                                
                                embed.add_field(
                                    name="Queue Status",
                                    value=f"**People waiting:** {queue_size}\n"
                                          f"**Estimated wait:** {wait_time}\n"
                                          f"**Oldest in queue:** {queue_stats['oldest_wait'].strftime('%H:%M:%S') if queue_stats['oldest_wait'] else 'N/A'}",
                                    inline=False
                                )
                                
                                queue_info = await self.cog.queue_manager.get_queue_info(interaction.user.id)
                                if queue_info:
                                    embed.add_field(
                                        name="Your Position",
                                        value=f"**Position:** #{queue_info.position}\n"
                                              f"**Joined:** <t:{int(queue_info.joined_at.timestamp())}:R>",
                                        inline=False
                                    )
                            else:
                                embed.add_field(
                                    name="Queue Status",
                                    value="✅ No one in queue - instant match available!",
                                    inline=False
                                )
                            
                            button.label = f"🔄 Refreshed"
                            button.disabled = True
                            
                            await interaction.edit_original_response(embed=embed, view=self)
                    finally:
                        # Clean up
                         await self.cog.processed_interactions.delete(refresh_key)
                
                async def on_timeout(self):
                    for child in self.children:
                        child.disabled = True
                    try:
                        await self.message.edit(view=self)
                    except:
                        pass
            
            view = RefreshView(self, interaction_key)
            view.message = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
            
        except Exception as e:
            print(f"[Shadow Chat] Error in handle_queue_status: {e}")
            import traceback
            traceback.print_exc()
            
            try:
                await interaction.followup.send(
                    "❌ An error occurred while processing your request.",
                    ephemeral=True
                )
            except:
                pass
        finally:
            # Always clean up, even on error
            if await self.processed_interactions.get(interaction_key):
                await self.processed_interactions.delete(interaction_key)
    
    async def handle_leave_queue_button(self, interaction: discord.Interaction):
        """Handle leave queue button click."""
        interaction_key = f"{interaction.id}_{interaction.user.id}_leave"
        
        if await self.processed_interactions.get(interaction_key):
            try:
                await interaction.response.defer(ephemeral=True, thinking=False)
            except:
                pass
            return
        
        await self.processed_interactions.set(interaction_key, discord.utils.utcnow())
        
        try:
            await interaction.response.defer(ephemeral=True)
            
            # Check if in queue
            queue_info = await self.queue_manager.get_queue_info(interaction.user.id)
            if queue_info:
                await self.queue_manager.remove_from_queue(interaction.user.id)
                
                embed = discord.Embed(
                    title="✅ Left Queue",
                    description="You've been removed from the Shadow Chat queue.",
                    color=constants.COLOR_SUCCESS
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                # Check if in active session
                session = await self.session_manager.get_session_by_user(interaction.user.id)
                if session:
                    await interaction.followup.send(
                        "❌ You're in an active session. Use `/end` or the end button in your thread.",
                        ephemeral=True
                    )
                else:
                    await interaction.followup.send(
                        "❌ You're not in the queue.",
                        ephemeral=True
                    )
                    
        except Exception as e:
            print(f"[Shadow Chat] Leave queue button error: {e}")
            await interaction.followup.send(
                "❌ An error occurred while processing your request.",
                ephemeral=True
            )
        finally:
            await self.processed_interactions.delete(interaction_key)
    
    async def handle_join_queue(self, interaction: discord.Interaction):
        """Handle join queue button click with race condition protection."""
        # Generate a unique key for this interaction to prevent duplicates
        interaction_key = f"{interaction.id}_{interaction.user.id}"
        
        # Check if this interaction was already processed
        if await self.processed_interactions.get(interaction_key):
            # Already processing, ignore duplicate
            try:
                await interaction.response.defer(ephemeral=True, thinking=False)
            except:
                pass
            return
        
        # Mark as processing
        await self.processed_interactions.set(interaction_key, discord.utils.utcnow())
        
        try:
            # IMMEDIATE RESPONSE - don't defer, just respond right away
            await interaction.response.defer(thinking=True, ephemeral=True)
            
            # Check if already in queue
            queue_info = await self.queue_manager.get_queue_info(interaction.user.id)
            if queue_info:
                # Show current position
                queue_size = await self.db.get_queue_size()
                wait_time = self._calculate_estimated_wait(queue_info.position)
                
                embed = discord.Embed(
                    title="🕶️ Already in Queue",
                    description="You're already in the Shadow Chat queue.",
                    color=constants.COLOR_ANONYMOUS
                )
                embed.add_field(
                    name="Your Position",
                    value=f"**Position:** #{queue_info.position} of {queue_size}\n"
                          f"**Estimated wait:** {wait_time}\n"
                          f"**Joined:** <t:{int(queue_info.joined_at.timestamp())}:R>",
                    inline=False
                )
                
                await interaction.followup.send(embed=embed, ephemeral=True)
                
                # Clean up after processing
                await self.processed_interactions.delete(interaction_key)
                return
            
            # Check if in active session
            session = await self.session_manager.get_session_by_user(interaction.user.id)
            if session:
                embed = discord.Embed(
                    title="🎭 Active Session",
                    description="You're already in a Shadow Chat session.",
                    color=constants.COLOR_WARNING
                )
                embed.add_field(
                    name="Current Session",
                    value=f"• Use `/end` to end your current session\n"
                          f"• Use `/reveal` to request identity reveal",
                    inline=False
                )
                
                await interaction.followup.send(embed=embed, ephemeral=True)
                
                # Clean up after processing
                await self.processed_interactions.delete(interaction_key)
                return
            
            # QUICK ADD TO QUEUE - minimal processing for immediate response
            try:
                # Simple check for bans
                if await self.queue_manager.is_user_banned(interaction.user.id):
                    embed = discord.Embed(
                        title="❌ Cannot Join Queue",
                        description="You are banned from using Shadow Chat.",
                        color=constants.COLOR_ERROR
                    )
                    await interaction.followup.send(embed=embed, ephemeral=True)
                    
                    await self.processed_interactions.delete(interaction_key)
                    return
                
                # Add to queue quickly
                position = await self.db.add_user_to_queue(interaction.user.id, interaction.user.name)
                
                if position > 0:
                    # Get info for immediate response
                    queue_size = await self.db.get_queue_size()
                    wait_time = self._calculate_estimated_wait(position)
                    
                    # Get active sessions count for information
                    active_sessions = await self.session_manager.get_all_active_sessions()
                    available_rooms = constants.MAX_ACTIVE_SESSIONS - len(active_sessions)
                    
                    # IMMEDIATE RESPONSE
                    embed = discord.Embed(
                        title="✅ Joined Shadow Chat Queue",
                        description="You've been added to the matchmaking queue.",
                        color=constants.COLOR_SUCCESS
                    )
                    
                    # If rooms are full, inform user
                    if available_rooms <= 0:
                        embed.add_field(
                            name="⚠️ Notice",
                            value=f"All {constants.MAX_ACTIVE_SESSIONS} rooms are currently in use.\n"
                                  f"You're queued and will be matched when a room becomes available.",
                            inline=False
                        )
                    
                    embed.add_field(
                        name="Queue Position",
                        value=f"**Position:** #{position} of {queue_size}\n"
                              f"**Estimated wait:** {wait_time}",
                        inline=False
                    )
                    
                    embed.add_field(
                        name="Room Status",
                        value=f"**Active sessions:** {len(active_sessions)}/{constants.MAX_ACTIVE_SESSIONS}\n"
                              f"**Available rooms:** {available_rooms}",
                        inline=True
                    )
                    
                    embed.add_field(
                        name="What's Next?",
                        value="• You'll be matched with another user\n"
                              "• A private thread will be created for you\n"
                              "• You'll receive a DM when matched",
                        inline=False
                    )
                    embed.set_image(url=constants.SHADOW_CHAT_QUEUE_IMAGE)
                    embed.color = constants.SHADOW_CHAT_QUEUE_COLOR
                    embed.set_footer(text="Use /leave or the leave queue button to exit")
                    
                    await interaction.followup.send(embed=embed, ephemeral=True)
                    
                    # NOW DO THE BACKGROUND PROCESSING
                    # Start matchmaking in the background
                    asyncio.create_task(self._background_matchmaking(interaction.user))
                    
                else:
                    # Failed to add to queue
                    embed = discord.Embed(
                        title="❌ Cannot Join Queue",
                        description="Failed to join queue. Please try again.",
                        color=constants.COLOR_ERROR
                    )
                    await interaction.followup.send(embed=embed, ephemeral=True)
                    
            except Exception as e:
                print(f"[Shadow Chat] Queue add error: {e}")
                embed = discord.Embed(
                    title="❌ Error",
                    description=f"An error occurred: {str(e)[:100]}",
                    color=constants.COLOR_ERROR
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            print(f"[Shadow Chat] Error in handle_join_queue: {e}")
            import traceback
            traceback.print_exc()
        
            # Try to send a generic error message
            try:
                await interaction.followup.send(
                    "❌ An error occurred while processing your request.",
                    ephemeral=True
                )
            except:
                pass
        finally:
            # Always clean up, even on error
            await self.processed_interactions.delete(interaction_key)
    
    async def _background_matchmaking(self, user: discord.Member):
        """Background task to handle matchmaking after user joins queue."""
        try:
            print(f"[Background Matchmaking] Starting for user {user.id}")
            
            # Wait a moment to ensure queue is updated
            await asyncio.sleep(0.5)
            
            # Check if user is still in queue
            queue_info = await self.queue_manager.get_queue_info(user.id)
            if not queue_info:
                #print(f"[Background Matchmaking] User {user.id} not in queue anymore")
                return
            
            # Try to process queue (this will check for matches)
            # Note: process_queue is already async and handles its own locks
            await self.queue_manager.process_queue()
            
            #print(f"[Background Matchmaking] Completed for user {user.id}")
            
        except Exception as e:
            print(f"[Background Matchmaking] Error: {e}")
            import traceback
            traceback.print_exc()
    
    # ==================== EVENT HANDLERS ====================
    
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        """Handle button interactions with race condition protection."""
        if not interaction.data or 'custom_id' not in interaction.data:
            return
        
        custom_id = interaction.data['custom_id']
        
        #print(f"[Shadow Chat] Interaction received: {custom_id}")
        
        # Handle the main join and status buttons
        if custom_id == "shadow_join_queue":
            return
        elif custom_id == "shadow_queue_status":
            return
        elif custom_id == "shadow_leave_queue":
            return
        
        # Handle other buttons with proper locking
        async with self._get_lock(custom_id):
            if not await self._check_cooldown(interaction.user.id):
                try:
                    await interaction.response.send_message(
                        "⏳ Please wait a moment before clicking again.",
                        ephemeral=True,
                        delete_after=2
                    )
                except discord.InteractionResponded:
                    pass
                return
            
            #print(f"[Shadow Chat] Button clicked: '{custom_id}' by {interaction.user.id}")
            
            # Handle dynamic session buttons
            if custom_id.startswith("shadow_end_"):
                session_id = custom_id[11:]
                await self.handle_end_chat(interaction, session_id)
            
            elif custom_id.startswith("shadow_skip_"):
                session_id = custom_id[12:]
                await self.handle_skip_chat(interaction, session_id)
            
            elif custom_id.startswith("shadow_reveal_"):
                session_id = custom_id[14:]
                await self.handle_reveal_request(interaction, session_id)
            
            elif custom_id.startswith("shadow_confirm_reveal_"):
                session_id = custom_id[22:]
                await self.handle_confirm_reveal(interaction, session_id)
            
            elif custom_id.startswith("shadow_cancel_reveal_"):
                session_id = custom_id[21:]
                await self.handle_cancel_reveal(interaction, session_id)
    
    async def handle_skip_chat(self, interaction: discord.Interaction, session_id: str):
        """Handle skip button click."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        
        #print(f"[Shadow Chat] Skip button clicked by {interaction.user.id} for session {session_id}")
        
        # Verify user is in this session
        session = await self.session_manager.get_session(session_id)
        if not session:
            await interaction.followup.send("Session not found.", ephemeral=True)
            return
        
        if interaction.user.id not in (session.user1_id, session.user2_id):
            await interaction.followup.send("You're not in this session.", ephemeral=True)
            return
        
        # Show confirmation
        embed = discord.Embed(
            title="⏭️ Skip Partner",
            description="Are you sure you want to skip your current partner?\n\n**What happens:**\n• You'll be re-added to the queue\n• Session ends for both users in 10 seconds\n• You'll be matched with someone new",
            color=constants.COLOR_WARNING
        )
        
        class SkipConfirmView(discord.ui.View):
            def __init__(self, cog, session_id, user_id):
                super().__init__(timeout=30)
                self.cog = cog
                self.session_id = session_id
                self.user_id = user_id
            
            @discord.ui.button(label="Yes, Skip Partner", style=discord.ButtonStyle.primary)
            async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                if interaction.user.id != self.user_id:
                    await interaction.response.send_message(
                        "❌ This confirmation is not for you.",
                        ephemeral=True
                    )
                    return
                
                await interaction.response.defer(ephemeral=True)
                
                # Send skip notification to both threads BEFORE processing skip
                guild = self.cog.bot.get_guild(constants.GUILD_ID)
                if guild:
                    # Get threads
                    thread1 = await get_thread_by_id(guild, session.thread1_id)
                    thread2 = await get_thread_by_id(guild, session.thread2_id)
                    
                    skip_embed = discord.Embed(
                        title="⏭️ Session Skipped",
                        description=f"This session has been skipped.\n\nThis thread will be deleted in 10 seconds.",
                        color=constants.COLOR_WARNING
                    )
                    
                    if thread1:
                        try:
                            await thread1.send(embed=skip_embed)
                        except Exception as e:
                            print(f"[Shadow Chat] Error sending skip notification to thread1: {e}")
                    
                    if thread2:
                        try:
                            await thread2.send(embed=skip_embed)
                        except Exception as e:
                            print(f"[Shadow Chat] Error sending skip notification to thread2: {e}")
                
                # Process skip
                success, message = await self.cog.session_manager.skip_session(self.session_id, self.user_id)
                try:
                    if success:
                        embed = discord.Embed(
                            title="✅ Partner Skipped",
                            description=message,
                            color=constants.COLOR_SUCCESS
                        )
                        await interaction.followup.send(embed=embed, ephemeral=True)
                    else:
                        embed = discord.Embed(
                            title="❌ Failed to Skip",
                            description=message,
                            color=constants.COLOR_ERROR
                        )
                        await interaction.followup.send(embed=embed, ephemeral=True)
                except Exception as e:
                    print(f"[Shadow Chat] Skip followup error: {e}")
                # Disable buttons
                for child in self.children:
                    child.disabled = True
                await interaction.edit_original_response(view=self)
            
            @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
            async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                if interaction.user.id != self.user_id:
                    await interaction.response.send_message(
                        "❌ This confirmation is not for you.",
                        ephemeral=True
                    )
                    return
                
                await interaction.response.edit_message(
                    content="❌ Skip cancelled.",
                    embed=None,
                    view=None
                )
        
        view = SkipConfirmView(self, session_id, interaction.user.id)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


    async def handle_end_chat(self, interaction: discord.Interaction, session_id: str):
        """Handle end button click - ends only for the clicking user."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        
        #print(f"[Shadow Chat] End button clicked by {interaction.user.id} for session {session_id}")
        
        # Verify user is in this session
        session = await self.session_manager.get_session(session_id)
        if not session:
            await interaction.followup.send("Session not found.", ephemeral=True)
            return
        
        if interaction.user.id not in (session.user1_id, session.user2_id):
            await interaction.followup.send("You're not in this session.", ephemeral=True)
            return
        
        # Show confirmation
        embed = discord.Embed(
            title="⏹️ End Chat",
            description="Are you sure you want to end this chat?\n\n**What happens:**\n• Both users will leave\n• Session ends completely\n• Threads will be deleted",
            color=constants.COLOR_WARNING
        )
        
        class EndConfirmView(discord.ui.View):
            def __init__(self, cog, session_id, user_id):
                super().__init__(timeout=30)
                self.cog = cog
                self.session_id = session_id
                self.user_id = user_id
            
            @discord.ui.button(label="Yes, End Chat", style=discord.ButtonStyle.danger)
            async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                if interaction.user.id != self.user_id:
                    await interaction.response.send_message(
                        "❌ This confirmation is not for you.",
                        ephemeral=True
                    )
                    return
                
                await interaction.response.defer(ephemeral=True)
                
                # Process end for BOTH users (session ends completely)
                success = await self.cog.session_manager.end_session(
                    self.session_id,
                    f"Session ended by user via end button"
                )
                
                if success:
                    embed = discord.Embed(
                        title="✅ Chat Ended",
                        description="The chat session has been ended for both users.",
                        color=constants.COLOR_SUCCESS
                    )
                    try:
                        await interaction.followup.send(embed=embed, ephemeral=True)
                    except:
                        pass
                else:
                    embed = discord.Embed(
                        title="❌ Failed to End Chat",
                        description="Could not end the chat session.",
                        color=constants.COLOR_ERROR
                    )
                    await interaction.followup.send(embed=embed, ephemeral=True)
                
                # Disable buttons
                for child in self.children:
                    child.disabled = True
                try:
                    await interaction.edit_original_response(view=self)
                except:
                    pass    
                
            @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
            async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                if interaction.user.id != self.user_id:
                    await interaction.response.send_message(
                        "❌ This confirmation is not for you.",
                        ephemeral=True
                    )
                    return
                
                await interaction.response.edit_message(
                    content="❌ End cancelled.",
                    embed=None,
                    view=None
                )
        
        view = EndConfirmView(self, session_id, interaction.user.id)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    
    async def process_queue_matchmaking(self, user1: discord.Member = None, user2: discord.Member = None):
        """Process queue to create matches - called by queue manager."""
        if not self.initialized or not self.session_manager:
            print(f"[Shadow Chat] Cannot process matchmaking - not initialized")
            return
        
        if not user1 or not user2:
            print(f"[Shadow Chat] Matchmaking called without users")
            return
        
        try:
            print(f"[Shadow Chat] Creating session for {user1.id} and {user2.id}")
            
            # Create session - this will automatically send DMs via _send_match_dms
            session = await self.session_manager.create_session(user1, user2)
            if session:
                pass
                #print(f"[Shadow Chat] ✓ Created session {session.session_id} "
                    #f"for {user1} and {user2}")
                # DMs are already sent by _send_match_dms in create_session method
            else:
                print(f"[Shadow Chat] ✗ Failed to create session")
                
        except Exception as e:
            print(f"[Shadow Chat] Matchmaking error: {e}")
            import traceback
            traceback.print_exc()
    
    async def handle_reveal_request(self, interaction: discord.Interaction, session_id: str):
        """Handle reveal request button click."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        
        session = await self.session_manager.get_session(session_id)
        if not session:
            await interaction.followup.send("Session not found.", ephemeral=True)
            return
        
        if interaction.user.id not in (session.user1_id, session.user2_id):
            await interaction.followup.send("You're not in this session.", ephemeral=True)
            return
        
        success, message = await self.session_manager.request_reveal(session_id, interaction.user.id)
        await interaction.followup.send(f"✅ {message}" if success else f"❌ {message}", ephemeral=True)
    
    async def handle_confirm_reveal(self, interaction: discord.Interaction, session_id: str):
        """Handle confirm reveal button click."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        
        session = await self.session_manager.get_session(session_id)
        if not session:
            await interaction.followup.send("Session not found.", ephemeral=True)
            return
        
        if interaction.user.id not in (session.user1_id, session.user2_id):
            await interaction.followup.send("You're not in this session.", ephemeral=True)
            return
        
        success, message = await self.session_manager.request_reveal(session_id, interaction.user.id)
        await interaction.followup.send(f"✅ {message}" if success else f"❌ {message}", ephemeral=True)
    
    async def handle_cancel_reveal(self, interaction: discord.Interaction, session_id: str):
        """Handle cancel reveal button click."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        
        session = await self.session_manager.get_session(session_id)
        if not session:
            await interaction.followup.send("Session not found.", ephemeral=True)
            return
        
        if interaction.user.id not in (session.user1_id, session.user2_id):
            await interaction.followup.send("You're not in this session.", ephemeral=True)
            return
        
        success, message = await self.identity_manager.cancel_reveal(
            interaction.user.id,
            session_id
        )
        await interaction.followup.send(f"✅ {message}" if success else f"❌ {message}", ephemeral=True)
    
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Handle messages in session threads."""
        if not self.initialized:
            return
        
        if not self.session_manager or not self.relay_handler:
            return
        
        if not isinstance(message.channel, discord.Thread):
            return
        
        if message.author.bot:
            return
        
        session = await self.session_manager.get_session_by_thread(message.channel.id)
        if not session:
            return
        
        handled, error = await self.relay_handler.handle_message(message)
        
        if error and "Failed to send" not in error:
            try:
                await message.reply(f"❌ {error}", delete_after=5)
            except:
                pass
    
    # ==================== ERROR HANDLING ====================
    
    @reveal.error
    @end.error
    @skip.error
    @leave.error
    async def user_command_error(self, interaction: discord.Interaction, error):
        """Handle user command errors."""
        if isinstance(error, app_commands.errors.MissingPermissions):
            await interaction.response.send_message(
                "You don't have permission to use this command.",
                ephemeral=True
            )
        else:
            print(f"[Shadow Chat] Command error: {error}")
            await interaction.response.send_message(
                f"❌ An error occurred: {str(error)[:200]}",
                ephemeral=True
            )
    
    @shadow_stats.error
    @shadow_setup.error
    @shadow_ban.error
    @shadow_unban.error
    @shadow_view_queue.error
    @shadow_active_sessions.error
    async def mod_command_error(self, interaction: discord.Interaction, error):
        """Handle mod command errors."""
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need moderator permissions to use this command.",
                ephemeral=True
            )
        else:
            print(f"[Shadow Chat] Mod command error: {error}")
            await interaction.response.send_message(
                f"❌ An error occurred: {str(error)[:200]}",
                ephemeral=True
            )

async def setup(bot: commands.Bot):
    """Setup function for discord.py cog loading."""
    await bot.add_cog(ShadowChat(bot))