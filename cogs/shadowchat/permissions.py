"""
SHADOW CHAT - PERMISSIONS & SAFETY
User validation, rate limiting, and access control with configurable limits.
"""

import datetime
import asyncio
from typing import Optional, Dict, Tuple
import discord

from . import constants
from .models import Database

# ==================== CONFIGURABLE RATE LIMITS ====================

# Rate limit configuration - easily adjustable here
RATE_LIMIT_CONFIG = {
    'join_queue': {
        'max_actions': 5,  # Max 5 join attempts
        'per_seconds': 60   # per 60 seconds
    },
    'send_messages': {
        'max_actions': 15,  # Max 15 messages
        'per_seconds': 5    # per 5 seconds
    },
    'reveal_identity': {
        'max_actions': 3,   # Max 3 reveal attempts
        'per_seconds': 30   # per 30 seconds
    }
}

# ==================== RATE LIMITING ====================

class RateLimiter:
    """Simple rate limiter for user actions with configurable limits."""
    
    def __init__(self, max_actions: int, per_seconds: int, name: str = "generic"):
        self.max_actions = max_actions
        self.per_seconds = per_seconds
        self.name = name
        self.user_actions: Dict[int, list] = {}
        self.lock = asyncio.Lock()
    
    async def is_rate_limited(self, user_id: int) -> bool:
        """Check if user is rate limited."""
        async with self.lock:
            now = discord.utils.utcnow()
            
            # Clean old entries
            if user_id in self.user_actions:
                self.user_actions[user_id] = [
                    ts for ts in self.user_actions[user_id]
                    if (now - ts).seconds < self.per_seconds
                ]
            
            # Check count
            action_count = len(self.user_actions.get(user_id, []))
            
            if action_count >= self.max_actions:
                return True
            
            # Record this action
            if user_id not in self.user_actions:
                self.user_actions[user_id] = []
            self.user_actions[user_id].append(now)
            
            return False
    
    async def get_time_remaining(self, user_id: int) -> int:
        """Get seconds until rate limit resets."""
        async with self.lock:
            if user_id not in self.user_actions:
                return 0
            
            now = discord.utils.utcnow()
            
            # Clean old entries first
            self.user_actions[user_id] = [
                ts for ts in self.user_actions[user_id]
                if (now - ts).seconds < self.per_seconds
            ]
            
            if not self.user_actions[user_id]:
                return 0
            
            # Find oldest timestamp within window
            oldest = min(self.user_actions[user_id])
            elapsed = (now - oldest).seconds
            
            return max(0, self.per_seconds - elapsed)
    
    def get_limit_info(self) -> str:
        """Get rate limit information for display."""
        return f"{self.max_actions} per {self.per_seconds}s"

# ==================== ACCESS CONTROLLER ====================

class AccessController:
    """Centralized access control for Shadow Chat with configurable limits."""
    
    def __init__(self, db: Database):
        self.db = db
        
        # Initialize rate limiters with configurable values
        self.join_limiter = RateLimiter(
            max_actions=RATE_LIMIT_CONFIG['join_queue']['max_actions'],
            per_seconds=RATE_LIMIT_CONFIG['join_queue']['per_seconds'],
            name="join_queue"
        )
        
        self.message_limiter = RateLimiter(
            max_actions=RATE_LIMIT_CONFIG['send_messages']['max_actions'],
            per_seconds=RATE_LIMIT_CONFIG['send_messages']['per_seconds'],
            name="send_messages"
        )
        
        self.reveal_limiter = RateLimiter(
            max_actions=RATE_LIMIT_CONFIG['reveal_identity']['max_actions'],
            per_seconds=RATE_LIMIT_CONFIG['reveal_identity']['per_seconds'],
            name="reveal_identity"
        )
        
        # Track recently banned users (in-memory cache)
        self.recent_bans: Dict[int, datetime.datetime] = {}
    
    async def get_rate_limit_info(self) -> Dict[str, str]:
        """Get current rate limit configuration."""
        return {
            'join_queue': self.join_limiter.get_limit_info(),
            'send_messages': self.message_limiter.get_limit_info(),
            'reveal_identity': self.reveal_limiter.get_limit_info()
        }
    
    async def is_user_banned(self, user_id: int) -> bool:
        """Check if a user is banned from Shadow Chat."""
        return await self.db.is_user_banned(user_id)
    
    async def can_join_queue(self, user: discord.Member) -> Tuple[bool, Optional[str]]:
        """
        Comprehensive check if user can join queue.
        Returns: (allowed, reason_if_not)
        """
        # 1. Check if user is in the correct guild
        if user.guild.id != constants.GUILD_ID:
            return False, "This server is not configured for Shadow Chat."
        
        # 2. Check rate limit
        if await self.join_limiter.is_rate_limited(user.id):
            remaining = await self.join_limiter.get_time_remaining(user.id)
            return False, f"Please wait {remaining}s before joining again. (Limit: {self.join_limiter.get_limit_info()})"
        
        # 3. Check if user is banned
        if await self.db.is_user_banned(user.id):
            return False, "You are banned from using Shadow Chat."
        
        # 4. Check if user is already in queue
        position = await self.db.get_queue_position(user.id)
        if position is not None:
            return False, "You're already in the queue!"
        
        # 5. Check if user is already in an active session
        existing_session = await self.db.get_session_by_user(user.id)
        if existing_session:
            return False, "You're already in an active session!"
        
        # 6. Basic permission checks (no bot, etc.)
        if user.bot:
            return False, "Bots cannot use Shadow Chat."
        
        # 7. Check if max sessions reached
        active_sessions = await self.db.get_active_session_count()
        if active_sessions >= constants.MAX_ACTIVE_SESSIONS:
            return False, "Maximum active sessions reached. Please try again later."
        
        return True, None
    
    async def can_send_message(self, user_id: int, session_id: str) -> Tuple[bool, Optional[str]]:
        """
        Check if user can send message in session.
        """
        # 1. Rate limit check
        if await self.message_limiter.is_rate_limited(user_id):
            remaining = await self.message_limiter.get_time_remaining(user_id)
            return False, f"Please wait {remaining}s before sending another message. (Limit: {self.message_limiter.get_limit_info()})"
        
        # 2. Get session
        session = await self.db.get_session(session_id)
        if not session:
            return False, "Session not found."
        
        # 3. Check if user is in this session
        if user_id not in (session.user1_id, session.user2_id):
            return False, "You are not part of this session."
        
        # 4. Check if session is active
        if not session.is_active:
            return False, "This session has ended."
        
        return True, None
    
    async def can_reveal_identity(self, user_id: int, session_id: str) -> Tuple[bool, Optional[str]]:
        """
        Check if user can request identity reveal.
        """
        # 1. Rate limit check
        if await self.reveal_limiter.is_rate_limited(user_id):
            remaining = await self.reveal_limiter.get_time_remaining(user_id)
            return False, f"Please wait {remaining}s before trying again. (Limit: {self.reveal_limiter.get_limit_info()})"
        
        # 2. Get session
        session = await self.db.get_session(session_id)
        if not session:
            return False, "Session not found."
        
        # 3. Check if user is in this session
        if user_id not in (session.user1_id, session.user2_id):
            return False, "You are not part of this session."
        
        # 4. Check if already revealed
        if session.identities_revealed:
            return False, "Identities are already revealed."
        
        # 5. Check if user already gave consent
        user_consent = session.user1_consent if user_id == session.user1_id else session.user2_consent
        if user_consent:
            return False, "You've already requested reveal."
        
        return True, None
    
    async def reset_rate_limit(self, user_id: int, limit_type: str = "all") -> bool:
        """
        Reset rate limit for a specific user.
        Types: 'join_queue', 'send_messages', 'reveal_identity', 'all'
        """
        try:
            if limit_type == "all" or limit_type == "join_queue":
                if user_id in self.join_limiter.user_actions:
                    self.join_limiter.user_actions[user_id].clear()
            
            if limit_type == "all" or limit_type == "send_messages":
                if user_id in self.message_limiter.user_actions:
                    self.message_limiter.user_actions[user_id].clear()
            
            if limit_type == "all" or limit_type == "reveal_identity":
                if user_id in self.reveal_limiter.user_actions:
                    self.reveal_limiter.user_actions[user_id].clear()
            
            return True
        except Exception as e:
            print(f"[Shadow Chat] Rate limit reset failed: {e}")
            return False
    
    async def ban_user(self, user_id: int, reason: str, moderator: discord.Member) -> bool:
        """
        Ban a user from Shadow Chat.
        Returns success status.
        """
        try:
            await self.db.ban_user(user_id, reason)
            self.recent_bans[user_id] = discord.utils.utcnow()
            
            # Clean old entries from cache
            cutoff = discord.utils.utcnow() - datetime.timedelta(hours=1)
            self.recent_bans = {
                uid: ts for uid, ts in self.recent_bans.items()
                if ts > cutoff
            }
            
            return True
        except Exception as e:
            print(f"[Shadow Chat] Ban failed: {e}")
            return False
    
    async def unban_user(self, user_id: int, moderator: discord.Member) -> bool:
        """
        Unban a user.
        """
        try:
            await self.db.unban_user(user_id)
            if user_id in self.recent_bans:
                del self.recent_bans[user_id]
            return True
        except Exception as e:
            print(f"[Shadow Chat] Unban failed: {e}")
            return False
    
    def is_recently_banned(self, user_id: int) -> bool:
        """Check if user was banned recently (cached)."""
        if user_id not in self.recent_bans:
            return False
        
        cutoff = discord.utils.utcnow() - datetime.timedelta(minutes=5)
        return self.recent_bans[user_id] > cutoff

# ==================== MODERATION PERMISSIONS ====================

def is_moderator(member: discord.Member) -> bool:
    """
    Check if member has moderation permissions.
    Customize this based on your server's mod role structure.
    """
    # Example: Check for specific role
    MOD_ROLE_IDS = [123456789012345683]  # Add your mod role IDs
    
    if any(role.id in MOD_ROLE_IDS for role in member.roles):
        return True
    
    # Fallback: Check Discord permissions
    required_permissions = discord.Permissions()
    required_permissions.manage_messages = True
    
    return member.guild_permissions >= required_permissions

async def log_mod_action(
    bot,
    action: str,
    moderator: discord.Member,
    target_user: Optional[discord.User] = None,
    session_id: Optional[str] = None,
    reason: Optional[str] = None
):
    """Log moderation action to mod channel."""
    channel = bot.get_channel(constants.MOD_LOG_CHANNEL_ID)
    if not channel:
        return
    
    embed = discord.Embed(
        title=f"🛡️ Shadow Chat Mod Action: {action}",
        color=constants.COLOR_WARNING,
        timestamp=discord.utils.utcnow()
    )
    
    embed.add_field(
        name="Moderator",
        value=f"{moderator.mention} (`{moderator.id}`)",
        inline=False
    )
    
    if target_user:
        embed.add_field(
            name="Target User",
            value=f"{target_user.mention} (`{target_user.id}`)",
            inline=True
        )
    
    if session_id:
        embed.add_field(
            name="Session ID",
            value=f"`{session_id}`",
            inline=True
        )
    
    if reason:
        embed.add_field(
            name="Reason",
            value=reason,
            inline=False
        )
    
    await channel.send(embed=embed)