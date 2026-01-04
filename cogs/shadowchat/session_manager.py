"""
SHADOW CHAT - SESSION MANAGER
Handles session creation, tracking, and termination.
"""

import asyncio
import datetime
from typing import Optional, Tuple, List
import discord

from . import constants
from .models import Database, ShadowSession
from .utils import generate_session_id, format_thread_name, get_thread_by_id, generate_user_code
from .thread_manager import ThreadManager
from .webhook_manager import WebhookManager


class SessionManager:
    """Manages active chat sessions."""
    
    def __init__(self, bot, db: Database, thread_manager: ThreadManager, webhook_manager: WebhookManager):
        self.bot = bot
        self.db = db
        self.thread_manager = thread_manager
        self.webhook_manager = webhook_manager
        
        # In-memory cache for quick session lookups
        self.session_cache: dict[str, ShadowSession] = {}
        self.user_to_session: dict[int, str] = {}
        
        # Task for periodic session cleanup
        self.cleanup_task: Optional[asyncio.Task] = None
    
    async def initialize(self):
        """Load active sessions from database and start cleanup task."""
        # Load active sessions from DB
        active_sessions = await self.db.get_active_sessions()
        for session in active_sessions:
            self.session_cache[session.session_id] = session
            self.user_to_session[session.user1_id] = session.session_id
            self.user_to_session[session.user2_id] = session.session_id
        
        print(f"[Shadow Chat] Loaded {len(active_sessions)} active sessions")
        
        # Start cleanup task
        self.cleanup_task = asyncio.create_task(self._periodic_cleanup())
    
    async def _periodic_cleanup(self):
        """Periodically check for inactive sessions."""
        while True:
            try:
                await asyncio.sleep(60)  # Check every minute
                
                active_sessions = await self.get_all_active_sessions()
                now = discord.utils.utcnow()
                
                for session in active_sessions:
                    # Check for inactivity
                    if (now - session.last_activity).total_seconds() > constants.SESSION_TIMEOUT_MINUTES * 60:
                        await self.end_session(session.session_id, "Inactivity timeout")
                    # Check for very old sessions (safety measure)
                    elif (now - session.created_at).total_seconds() > 3600:  # 1 hour max
                        await self.end_session(session.session_id, "Maximum session duration reached")
                
                # Clean old sessions from database
                await self.db.cleanup_old_sessions(cutoff_minutes=120)
                
            except Exception as e:
                print(f"[Shadow Chat] Cleanup task error: {e}")
    
    # ========== SESSION RETRIEVAL METHODS ==========
    
    async def get_session(self, session_id: str) -> Optional[ShadowSession]:
        """Retrieve a session by ID (uses cache first)."""
        # Check cache first
        if session_id in self.session_cache:
            return self.session_cache[session_id]
        
        # Fall back to database
        session = await self.db.get_session(session_id)
        if session:
            self.session_cache[session_id] = session
            self.user_to_session[session.user1_id] = session_id
            self.user_to_session[session.user2_id] = session_id
        
        return session
    
    async def get_session_by_thread(self, thread_id: int) -> Optional[ShadowSession]:
        """Find session containing a specific thread."""
        # Check cache first
        for session in self.session_cache.values():
            if session.thread1_id == thread_id or session.thread2_id == thread_id:
                return session
        
        # Fall back to database
        session = await self.db.get_session_by_thread(thread_id)
        if session:
            self.session_cache[session.session_id] = session
            self.user_to_session[session.user1_id] = session.session_id
            self.user_to_session[session.user2_id] = session.session_id
        
        return session
    
    async def get_session_by_user(self, user_id: int) -> Optional[ShadowSession]:
        """Find active session for a user."""
        # Check cache first
        if user_id in self.user_to_session:
            session_id = self.user_to_session[user_id]
            if session_id in self.session_cache:
                session = self.session_cache[session_id]
                if session.is_active:
                    return session
        
        # Fall back to database
        session = await self.db.get_session_by_user(user_id)
        if session:
            self.session_cache[session.session_id] = session
            self.user_to_session[session.user1_id] = session.session_id
            self.user_to_session[session.user2_id] = session.session_id
        
        return session
    
    async def create_session(self, user1: discord.Member, user2: discord.Member) -> Optional[ShadowSession]:
        """Create a new session between two users."""
        try:
            # Generate unique session ID
            session_id = generate_session_id()
            
            # Create threads
            thread1 = await self.thread_manager.create_thread(
                session_id, user1, user2, is_user1=True
            )
            thread2 = await self.thread_manager.create_thread(
                session_id, user2, user1, is_user1=False
            )
            
            if not thread1 or not thread2:
                print(f"[Shadow Chat] Failed to create threads for session {session_id}")
                return None
            
            # Create webhooks
            webhook1, webhook1_name = await self.webhook_manager.create_webhook(thread1, user2)
            webhook2, webhook2_name = await self.webhook_manager.create_webhook(thread2, user1)
            
            if not webhook1 or not webhook2:
                print(f"[Shadow Chat] Failed to create webhooks for session {session_id}")
                return None
            
            # Create session object
            session = ShadowSession(
                session_id=session_id,
                user1_id=user1.id,
                user2_id=user2.id,
                thread1_id=thread1.id,
                thread2_id=thread2.id,
                webhook1_url=webhook1.url,
                webhook2_url=webhook2.url,
                webhook1_name=webhook1_name,
                webhook2_name=webhook2_name,
                created_at=discord.utils.utcnow(),
                last_activity=discord.utils.utcnow(),
                is_active=True
            )
            
            # Store in database
            await self.db.create_session(session)
            
            # Update cache
            self.session_cache[session_id] = session
            self.user_to_session[user1.id] = session_id
            self.user_to_session[user2.id] = session_id
            
            # Send welcome messages
            await self._send_welcome_messages(session, user1, user2)
            
            # Send DMs to users
            await self._send_match_dms(session, user1, user2)
            
            # Log session creation
            await self._log_session_creation(session, user1, user2)
            
            print(f"[Shadow Chat] ✓ Created session {session_id} for {user1.display_name} and {user2.display_name}")
            return session
            
        except Exception as e:
            print(f"[Shadow Chat] Error creating session: {e}")
            return None
    
    async def _send_match_dms(self, session: ShadowSession, user1: discord.Member, user2: discord.Member):
        """Send match found DMs to both users."""
        def create_match_found_embed(thread_id: int, session_id: str) -> discord.Embed:
            """Create a match found embed with session details."""
            embed = discord.Embed(
                title="🎭 Shadow Found!",
                description="Your cloaked chat session is ready.",
                color=discord.Color.green()
            )
            embed.add_field(
                name="🕯️Session Code",
                value=f"`{session_id}`",
                inline=False
            )
            embed.add_field(
                name="⚠️Important",
                value="Please save this session code for reporting any issues, disturbances, suspicious behavior, or rule violations to the moderators.\n\nPlease save screenshots as messages are not tracked.",
                inline=False
            )
            embed.add_field(
                name="🐾Your Thread",
                value=f"<#{thread_id}>",
                inline=False
            )
            embed.set_footer(text="🐈‍⬛ Shadow Cat, making BFFs made easier.")
            embed.color=constants.SHADOW_CHAT_QUEUE_COLOR
            embed.set_thumbnail(url=constants.MATCH_FOUND_THUMBNAIL)
            return embed
        
        # Send to user1
        try:
            embed = create_match_found_embed(session.thread1_id, session.session_id)
            await user1.send(embed=embed)
            print(f"[Shadow Chat] ✓ DM sent to {user1}")
        except discord.Forbidden:
            print(f"[Shadow Chat] Cannot DM {user1}")
        
        # Send to user2
        try:
            embed = create_match_found_embed(session.thread2_id, session.session_id)
            await user2.send(embed=embed)
            print(f"[Shadow Chat] ✓ DM sent to {user2}")
        except discord.Forbidden:
            print(f"[Shadow Chat] Cannot DM {user2}")
    
    async def _send_welcome_messages(self, session: ShadowSession, user1: discord.Member, user2: discord.Member):
        """Send welcome messages to both threads."""
        from .utils import create_session_header, create_session_header_buttons
        
        # Get threads
        thread1 = await get_thread_by_id(self.bot.get_guild(constants.GUILD_ID), session.thread1_id)
        thread2 = await get_thread_by_id(self.bot.get_guild(constants.GUILD_ID), session.thread2_id)
        
        if not thread1 or not thread2:
            return
        
        # Create header embeds
        embed1 = create_session_header(session.session_id, generate_user_code())
        embed2 = create_session_header(session.session_id, generate_user_code())
        
        # Create header buttons
        view = create_session_header_buttons(session.session_id)
        
        # Send headers (try to pin them)
        try:
            header1 = await thread1.send(embed=embed1, view=view)
            header2 = await thread2.send(embed=embed2, view=view)
            
            # Try to pin headers
            try:
                await header1.pin()
                await header2.pin()
            except:
                pass  # Can't pin, that's okay
            
            # Send initial greeting message
            embed=discord.Embed(description="💬 Start chatting! Make sure not to share any personal information.",
                                color=constants.SHADOW_CHAT_QUEUE_COLOR)
            await thread1.send(embed=embed)
            await thread2.send(embed=embed)
            
        except Exception as e:
            print(f"[Shadow Chat] Welcome message error: {e}")
    

    async def _log_session_skip(self, session: ShadowSession, skipping_user_id: int):
        """Log session skip to mod channel."""
        channel = self.bot.get_channel(constants.MOD_LOG_CHANNEL_ID)
        if not channel:
            return
        
        # Get users
        user1 = self.bot.get_user(session.user1_id)
        user2 = self.bot.get_user(session.user2_id)
        
        embed = discord.Embed(
            title="⏭️ Shadow Chat Session Skipped",
            color=constants.COLOR_WARNING,
            timestamp=discord.utils.utcnow()
        )
        
        embed.add_field(
            name="Session ID",
            value=f"`{session.session_id}`",
            inline=False
        )
        
        if user1:
            embed.add_field(
                name="User 1",
                value=f"{user1.mention} (`{user1.id}`)" + 
                    (" ⬅️ Skipper" if user1.id == skipping_user_id else ""),
                inline=True
            )
        
        if user2:
            embed.add_field(
                name="User 2",
                value=f"{user2.mention} (`{user2.id}`)" + 
                    (" ⬅️ Skipper" if user2.id == skipping_user_id else ""),
                inline=True
            )
        
        embed.add_field(
            name="Duration",
            value=f"{(discord.utils.utcnow() - session.created_at).seconds // 60} minutes",
            inline=True
        )
        
        embed.add_field(
            name="Action",
            value=f"User {skipping_user_id} skipped and re-queued",
            inline=False
        )
        
        await channel.send(embed=embed)


    async def _log_session_creation(self, session: ShadowSession, user1: discord.Member, user2: discord.Member):
        """Log session creation to mod channel."""
        channel = self.bot.get_channel(constants.MOD_LOG_CHANNEL_ID)
        if not channel:
            return
        
        embed = discord.Embed(
            title="🎭 New Shadow Chat Session",
            color=constants.COLOR_SUCCESS,
            timestamp=discord.utils.utcnow()
        )
        
        embed.add_field(
            name="Session ID",
            value=f"`{session.session_id}`",
            inline=False
        )
        
        embed.add_field(
            name="User 1",
            value=f"{user1.mention} (`{user1.id}`)",
            inline=True
        )
        
        embed.add_field(
            name="User 2",
            value=f"{user2.mention} (`{user2.id}`)",
            inline=True
        )
        
        embed.add_field(
            name="Threads",
            value=f"`{session.thread1_id}` / `{session.thread2_id}`",
            inline=False
        )
        
        await channel.send(embed=embed)

    async def skip_session(self, session_id: str, user_id: int) -> Tuple[bool, str]:
        """
        Skip current partner - re-add clicking user to queue, end session for both.
        """
        session = await self.get_session(session_id)
        if not session:
            return False, "Session not found."
        
        if user_id not in [session.user1_id, session.user2_id]:
            return False, "You are not in this session."
        
        print(f"[Shadow Chat] User {user_id} skipping session {session_id}")
        
        # Get user objects
        skipping_user = self.bot.get_user(user_id)
        other_user_id = session.user2_id if user_id == session.user1_id else session.user1_id
        
        # Send final skip notification to both threads (already sent in handle_skip_chat)
        
        # Mark session as inactive in database
        await self.db.end_session(session_id)
        
        # Update cache
        session.is_active = False
        
        # Remove from user-to-session mapping for BOTH users
        if session.user1_id in self.user_to_session:
            del self.user_to_session[session.user1_id]
        if session.user2_id in self.user_to_session:
            del self.user_to_session[session.user2_id]
        
        # Remove from session cache
        if session_id in self.session_cache:
            del self.session_cache[session_id]
        
        # Add the skipping user back to queue
        if skipping_user:
            await self.db.add_user_to_queue(user_id, skipping_user.name)
            print(f"[Shadow Chat] Re-added user {user_id} to queue")
        
        # Schedule thread deletion after 10 seconds
        asyncio.create_task(self._delete_skipped_threads(session, 10))
        
        # Log session skip
        await self._log_session_skip(session, user_id)
        
        return True, "Skipped partner. You've been re-added to queue."

    async def _delete_skipped_threads(self, session: ShadowSession, delay_seconds: int):
        """Delete both threads after delay for skipped session."""
        # Wait for the specified delay
        await asyncio.sleep(delay_seconds)
        
        guild = self.bot.get_guild(constants.GUILD_ID)
        if not guild:
            return
        
        # Delete thread 1
        thread1 = await get_thread_by_id(guild, session.thread1_id)
        if thread1:
            try:
                await thread1.delete(reason="Session skipped")
                print(f"[Shadow Chat] Deleted thread {session.thread1_id} after skip")
            except Exception as e:
                print(f"[Shadow Chat] Error deleting thread 1: {e}")
        
        # Delete thread 2
        thread2 = await get_thread_by_id(guild, session.thread2_id)
        if thread2:
            try:
                await thread2.delete(reason="Session skipped")
                print(f"[Shadow Chat] Deleted thread {session.thread2_id} after skip")
            except Exception as e:
                print(f"[Shadow Chat] Error deleting thread 2: {e}")
        
        # Delete webhooks
        await self.webhook_manager.delete_webhook(session.webhook1_url)
        await self.webhook_manager.delete_webhook(session.webhook2_url)
        
        # Delete message mappings
        await self.db.delete_session_mappings(session.session_id)
        
        print(f"[Shadow Chat] Fully cleaned up skipped session {session.session_id}")

    # ========== SESSION MANAGEMENT METHODS ==========
    
    async def update_session_activity(self, session_id: str):
        """Update session's last activity timestamp."""
        await self.db.update_session_activity(session_id)
        
        # Update cache
        if session_id in self.session_cache:
            self.session_cache[session_id].last_activity = discord.utils.utcnow()
    
    async def request_reveal(self, session_id: str, user_id: int) -> Tuple[bool, Optional[str]]:
        """
        Request identity reveal.
        Returns: (success, message)
        """
        session = await self.get_session(session_id)
        if not session:
            return False, "Session not found."
        
        if session.identities_revealed:
            return False, "Identities are already revealed."
        
        # Update consent
        await self.db.update_reveal_consent(session_id, user_id, True)
        
        # Update cache
        if user_id == session.user1_id:
            session.user1_consent = True
        else:
            session.user2_consent = True
        
        # Check if both consented
        if session.user1_consent and session.user2_consent:
            await self._reveal_identities(session)
            return True, constants.MESSAGE_REVEAL_CONFIRMED
        else:
            # Notify partner
            await self._notify_reveal_request(session, user_id)
            return True, constants.MESSAGE_REVEAL_PENDING
    
    async def _reveal_identities(self, session: ShadowSession):
        """Reveal identities in a session - SWAP THEM!"""
        # Get user objects
        user1 = self.bot.get_user(session.user1_id)
        user2 = self.bot.get_user(session.user2_id)
        
        if not user1 or not user2:
            return
        
        print(f"[Shadow Chat] Revealing identities:")
        print(f"  User 1: {user1.display_name} ({user1.id})")
        print(f"  User 2: {user2.display_name} ({user2.id})")
        
        # SWAP THE IDENTITIES:
        # Webhook 1 (in User 1's thread) should show User 2's identity
        # Webhook 2 (in User 2's thread) should show User 1's identity
        
        success1 = await self.webhook_manager.update_webhook_identity(
            session.webhook1_url,  # Webhook URL for user1's thread
            user2  # Show user2's identity in user1's thread
        )
        
        success2 = await self.webhook_manager.update_webhook_identity(
            session.webhook2_url,  # Webhook URL for user2's thread
            user1  # Show user1's identity in user2's thread
        )
        
        if success1 and success2:
            # Mark as revealed in database
            await self.db.set_identities_revealed(session.session_id)
            session.identities_revealed = True
            
            # Send notification to both threads
            await self._send_reveal_notification(session, user1, user2)
            
            print(f"[Shadow Chat] Identities swapped successfully!")
            print(f"  Webhook 1 now shows: {user2.display_name}")
            print(f"  Webhook 2 now shows: {user1.display_name}")
        else:
            print(f"[Shadow Chat] Failed to update webhooks")
    
    async def _notify_reveal_request(self, session: ShadowSession, requesting_user_id: int):
        """Notify partner about reveal request."""
        from .utils import create_confirmation_buttons
        
        # Get partner's thread
        partner_id = session.get_other_user(requesting_user_id)
        partner_thread_id = session.get_user_thread(partner_id)
        
        guild = self.bot.get_guild(constants.GUILD_ID)
        if not guild:
            return
        
        thread = await get_thread_by_id(guild, partner_thread_id)
        if not thread:
            print(f"[Shadow Chat] Could not find partner thread {partner_thread_id}")
            return
        
        # Send notification with confirmation buttons
        embed = discord.Embed(
            title="👁️ Identity Reveal Request",
            description=f"Your partner wants to reveal identities. Do you agree?",
            color=constants.COLOR_WARNING
        )
        
        embed.add_field(
            name="Note",
            value="Both users must consent for identities to be revealed.",
            inline=False
        )
        
        view = create_confirmation_buttons(session.session_id)
        
        await thread.send(embed=embed, view=view)
    
    async def _send_reveal_notification(self, session: ShadowSession, user1: discord.User, user2: discord.User):
        """Send notification that identities have been revealed."""
        from .utils import create_reveal_embed
        
        # Get threads
        thread1 = await get_thread_by_id(self.bot.get_guild(constants.GUILD_ID), session.thread1_id)
        thread2 = await get_thread_by_id(self.bot.get_guild(constants.GUILD_ID), session.thread2_id)
        
        if not thread1 or not thread2:
            return
        
        # Create embeds
        embed1 = create_reveal_embed(user2.name, str(user2.avatar.url) if user2.avatar else None)
        embed2 = create_reveal_embed(user1.name, str(user1.avatar.url) if user1.avatar else None)
        
        # Send notifications
        try:
            await thread1.send(embed=embed1)
            await thread2.send(embed=embed2)
        except Exception as e:
            print(f"[Shadow Chat] Reveal notification error: {e}")
    
    async def end_session(self, session_id: str, reason: str = "Session ended"):
        """
        End a session and clean up resources.
        """
        session = await self.get_session(session_id)
        if not session:
            return False
        
        # Mark as inactive in database
        await self.db.end_session(session_id)
        
        # Update cache
        session.is_active = False
        
        # Remove from user-to-session mapping
        if session.user1_id in self.user_to_session:
            del self.user_to_session[session.user1_id]
        if session.user2_id in self.user_to_session:
            del self.user_to_session[session.user2_id]
        
        # Delete threads
        thread1 = await get_thread_by_id(self.bot.get_guild(constants.GUILD_ID), session.thread1_id)
        thread2 = await get_thread_by_id(self.bot.get_guild(constants.GUILD_ID), session.thread2_id)
        
        # Send ending message to both threads
        ending_embed = discord.Embed(
            title="⏹️ Session Ended",
            description=f"Reason: {reason}\n\nThread will be deleted shortly.",
            color=constants.COLOR_ERROR
        )
        ending_embed.set_thumbnail(url=constants.END_SESSION_THUMBNAIL)
        
        if thread1:
            try:
                await thread1.send(embed=ending_embed)
            except:
                pass
        
        if thread2:
            try:
                await thread2.send(embed=ending_embed)
            except:
                pass
        
        # Wait a moment, then delete threads
        await asyncio.sleep(10)
        
        if thread1:
            try:
                await thread1.delete(reason=reason)
            except Exception as e:
                print(f"[Shadow Chat] Thread1 deletion error: {e}")
        
        if thread2:
            try:
                await thread2.delete(reason=reason)
            except Exception as e:
                print(f"[Shadow Chat] Thread2 deletion error: {e}")
        
        # Delete webhooks
        await self.webhook_manager.delete_webhook(session.webhook1_url)
        await self.webhook_manager.delete_webhook(session.webhook2_url)
        
        # Delete message mappings
        await self.db.delete_session_mappings(session_id)
        
        # Remove from cache
        if session_id in self.session_cache:
            del self.session_cache[session_id]
        
        # Log session end
        await self._log_session_end(session, reason)
        
        return True
    
    async def _log_session_end(self, session: ShadowSession, reason: str):
        """Log session end to mod channel."""
        channel = self.bot.get_channel(constants.MOD_LOG_CHANNEL_ID)
        if not channel:
            return
        
        # Get users
        user1 = self.bot.get_user(session.user1_id)
        user2 = self.bot.get_user(session.user2_id)
        
        embed = discord.Embed(
            title="⏹️ Shadow Chat Session Ended",
            color=constants.COLOR_ERROR,
            timestamp=discord.utils.utcnow()
        )
        
        embed.add_field(
            name="Session ID",
            value=f"`{session.session_id}`",
            inline=False
        )
        
        if user1:
            embed.add_field(
                name="User 1",
                value=f"{user1.mention} (`{user1.id}`)",
                inline=True
            )
        
        if user2:
            embed.add_field(
                name="User 2",
                value=f"{user2.mention} (`{user2.id}`)",
                inline=True
            )
        
        embed.add_field(
            name="Duration",
            value=f"{(discord.utils.utcnow() - session.created_at).seconds // 60} minutes",
            inline=True
        )
        
        embed.add_field(
            name="Reason",
            value=reason,
            inline=False
        )
        
        await channel.send(embed=embed)
    
    async def get_all_active_sessions(self) -> List[ShadowSession]:
        """Get all active sessions."""
        sessions = list(self.session_cache.values())
        return [s for s in sessions if s.is_active]
    
    async def close(self):
        """Clean up resources."""
        if self.cleanup_task:
            self.cleanup_task.cancel()
            try:
                await self.cleanup_task
            except asyncio.CancelledError:
                pass
        
        # End all active sessions gracefully
        active_sessions = await self.get_all_active_sessions()
        for session in active_sessions:
            await self.end_session(session.session_id, "Bot shutting down")