"""
SHADOW CHAT - INACTIVITY WATCHER (OPTIMIZED)
Monitors sessions for inactivity and cleans up old sessions using async concurrency.
"""

import asyncio
import discord
import datetime
from typing import Dict, List
from .constants import SESSION_TIMEOUT_MINUTES, GUILD_ID, COLOR_WARNING
from .session_manager import SessionManager
from .models import ShadowSession
from .utils import get_thread_by_id

class InactivityWatcher:
    """Monitors and cleans up inactive sessions."""
    
    def __init__(self, bot, session_manager: SessionManager):
        self.bot = bot
        self.session_manager = session_manager
        self.watcher_task: asyncio.Task = None
        self.is_running = False
        
        # Cache for warning messages to avoid spamming
        self.warnings_sent: Dict[str, datetime.datetime] = {}
    
    async def start(self):
        """Start the inactivity watcher."""
        if self.watcher_task and not self.watcher_task.done():
            return
        
        self.is_running = True
        self.watcher_task = asyncio.create_task(self._watch_loop())
        print("[Shadow Chat] Inactivity watcher started")
    
    async def stop(self):
        """Stop the inactivity watcher."""
        self.is_running = False
        if self.watcher_task:
            self.watcher_task.cancel()
            try:
                await self.watcher_task
            except asyncio.CancelledError:
                pass
        print("[Shadow Chat] Inactivity watcher stopped")
    
    async def _watch_loop(self):
        """Main watch loop."""
        while self.is_running:
            try:
                # Run main checks
                await self._check_sessions()
                
                # Periodically clean up memory
                await self.cleanup_old_warnings()
                
                await asyncio.sleep(30)  # Check every 30 seconds
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[Shadow Chat] Watcher error: {e}")
                await asyncio.sleep(60)  # Wait longer on error
    
    async def _check_sessions(self):
        """Check all active sessions for inactivity in PARALLEL."""
        sessions = await self.session_manager.get_all_active_sessions()
        if not sessions:
            return

        now = discord.utils.utcnow()
        
        # Create tasks for all sessions to run concurrently
        # This prevents one slow session check from blocking others
        tasks = []
        for session in sessions:
            tasks.append(self._process_single_session(session, now))
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _process_single_session(self, session: ShadowSession, now: datetime.datetime):
        """Process checks for a single session (Inactivity + Duration)."""
        try:
            # We run these sequentially per session because ending a session
            # negates the need to check duration
            timed_out = await self._check_session_inactivity(session, now)
            if not timed_out:
                await self._check_session_duration(session, now)
        except Exception as e:
            print(f"[Shadow Chat] Error checking session {session.session_id}: {e}")
    
    async def _check_session_inactivity(self, session: ShadowSession, now: datetime.datetime) -> bool:
        """Check if a session has been inactive too long. Returns True if ended."""
        inactive_seconds = (now - session.last_activity).total_seconds()
        warning_threshold = SESSION_TIMEOUT_MINUTES * 60 - 60  # 1 minute before timeout
        
        # End session if timed out
        if inactive_seconds >= SESSION_TIMEOUT_MINUTES * 60:
            await self.session_manager.end_session(
                session.session_id,
                "Inactivity timeout"
            )
            # Clean up warning cache
            if session.session_id in self.warnings_sent:
                del self.warnings_sent[session.session_id]
            return True

        # Send warning if close to timeout
        if inactive_seconds >= warning_threshold and session.session_id not in self.warnings_sent:
            # We don't await this to block execution, just fire and forget or simple await
            await self._send_inactivity_warning(session)
            self.warnings_sent[session.session_id] = now
            
        return False
    
    async def _check_session_duration(self, session: ShadowSession, now: datetime.datetime):
        """Check if session has been active too long (safety measure)."""
        duration_hours = (now - session.created_at).total_seconds() / 3600
        
        # Max 6 hours per session (safety limit)
        if duration_hours >= 6:
            await self.session_manager.end_session(
                session.session_id,
                "Maximum session duration reached (6 hours)"
            )
    
    async def _send_inactivity_warning(self, session: ShadowSession):
        """Send inactivity warning to both users (Parallelized)."""
        guild = self.bot.get_guild(GUILD_ID)
        if not guild: return

        # Fetch threads in parallel
        t1_task = get_thread_by_id(guild, session.thread1_id)
        t2_task = get_thread_by_id(guild, session.thread2_id)
        thread1, thread2 = await asyncio.gather(t1_task, t2_task)
        
        if not thread1 or not thread2:
            return
        
        # Create warning embed
        embed = discord.Embed(
            title="⏰ Session Inactive",
            description="Session will end in 1 minute due to inactivity.",
            color=COLOR_WARNING
        )
        embed.add_field(name="To Continue", value="Send a message to reset the timer.", inline=False)
        
        # Send warnings in parallel
        await asyncio.gather(
            thread1.send(embed=embed),
            thread2.send(embed=embed),
            return_exceptions=True
        )
    
    async def cleanup_old_warnings(self):
        """Clean up old entries from warnings cache."""
        cutoff = discord.utils.utcnow() - datetime.timedelta(minutes=10)
        # Use dict comprehension for efficiency
        self.warnings_sent = {
            sid: ts for sid, ts in self.warnings_sent.items() 
            if ts > cutoff
        }
    
    async def force_check(self):
        """Force an immediate check of all sessions."""
        await self._check_sessions()