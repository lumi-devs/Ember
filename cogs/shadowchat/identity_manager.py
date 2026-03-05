"""
SHADOW CHAT - IDENTITY MANAGER (OPTIMIZED)
Handles the identity reveal system with mutual consent using async concurrency.
"""

import discord
import asyncio
from typing import Optional, Tuple
from .models import Database, ShadowSession
from .session_manager import SessionManager
from .webhook_manager import WebhookManager
from .utils import get_thread_by_id, create_reveal_embed, create_confirmation_buttons
from . import constants


class IdentityManager:
    """Manages identity reveal requests and confirmations."""

    def __init__(
        self,
        bot,
        db: Database,
        session_manager: SessionManager,
        webhook_manager: WebhookManager,
    ):
        self.bot = bot
        self.db = db
        self.session_manager = session_manager
        self.webhook_manager = webhook_manager
        self.session_lock = asyncio.Lock()

    async def request_reveal(
        self, user_id: int, session_id: str
    ) -> Tuple[bool, Optional[str]]:
        """
        User requests to reveal their identity. Handles both initial request and confirmation.
        Returns: (success, message)
        """
        session = await self.session_manager.get_session(session_id)
        if not session:
            return False, "Session not found."
        async with self.session_lock:
            if session.identities_revealed:
                return False, "Identities are already revealed."

            # Determine current consent status
            is_user1 = user_id == session.user1_id
            current_consent = (
                session.user1_consent if is_user1 else session.user2_consent
            )

            if current_consent:
                return False, "You've already requested/confirmed reveal."

            # Update Database and Cache
            await self.db.update_reveal_consent(session_id, user_id, True)
            if is_user1:
                session.user1_consent = True
            else:
                session.user2_consent = True

            # Check Mutual Consent
            if session.user1_consent and session.user2_consent:
                return await self._perform_reveal(session)
            else:
                # Send request to partner asynchronously (fire and forget logic for speed, or await if critical)
                asyncio.create_task(self._send_reveal_request(session, user_id))
                return True, "Reveal request sent. Waiting for partner consent."

    # Alias confirm_reveal to request_reveal as logic is identical (idempotent)
    async def confirm_reveal(
        self, user_id: int, session_id: str
    ) -> Tuple[bool, Optional[str]]:
        return await self.request_reveal(user_id, session_id)

    async def cancel_reveal(
        self, user_id: int, session_id: str
    ) -> Tuple[bool, Optional[str]]:
        """
        User cancels their reveal request or declines partner's request.
        """
        session = await self.session_manager.get_session(session_id)
        if not session:
            return False, "Session not found."

        if session.identities_revealed:
            return False, "Identities are already revealed."

        # Update DB and Cache
        await self.db.update_reveal_consent(session_id, user_id, False)

        if user_id == session.user1_id:
            session.user1_consent = False
        else:
            session.user2_consent = False

        # Notify partner in background
        asyncio.create_task(self._notify_reveal_cancelled(session, user_id))

        return True, "Reveal request cancelled."

    async def _perform_reveal(
        self, session: ShadowSession
    ) -> Tuple[bool, Optional[str]]:
        """
        Actually reveal identities in a session.
        OPTIMIZATION: Updates webhooks and sends notifications in parallel.
        """
        user1 = self.bot.get_user(session.user1_id)
        user2 = self.bot.get_user(session.user2_id)

        if not user1 or not user2:
            return False, "Could not find users."

        # 1. Update Webhooks in Parallel
        # We create two tasks to hit the Discord API simultaneously
        task1 = self.webhook_manager.update_webhook_identity(
            webhook_url=session.webhook1_url, user=user2, use_default_avatar=False
        )
        task2 = self.webhook_manager.update_webhook_identity(
            webhook_url=session.webhook2_url, user=user1, use_default_avatar=False
        )

        results = await asyncio.gather(task1, task2, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception) or result is False:
                # Log the specific error here so you know WHY it failed
                print(f"Webhook update failed: {result}")

        if not all(results):
            return False, "Failed to update webhooks."

        # 2. Update DB and Local State
        await self.db.set_identities_revealed(session.session_id)
        session.identities_revealed = True

        # 3. Send Notifications & Logs in Parallel
        # We don't need to wait for logging to finish to return success to the user
        asyncio.create_task(self._send_reveal_notifications(session, user1, user2))
        asyncio.create_task(self._log_reveal(session, user1, user2))

        return True, "Identities revealed!"

    async def _send_reveal_request(
        self, session: ShadowSession, requesting_user_id: int
    ):
        """Send reveal request to partner."""
        partner_id = session.get_other_user(requesting_user_id)
        partner_thread_id = session.get_user_thread(partner_id)

        guild = self.bot.get_guild(constants.GUILD_ID)
        if not guild:
            return

        thread = await get_thread_by_id(guild, partner_thread_id)
        if not thread:
            return

        embed = discord.Embed(
            title="👁️ Identity Reveal Request",
            description="Your partner wants to reveal identities.",
            color=0xFEE75C,
        )
        embed.add_field(
            name="Action Required",
            value="Click below to confirm or decline.",
            inline=False,
        )
        embed.add_field(name="Note", value="Both users must consent.", inline=False)

        view = create_confirmation_buttons(session.session_id)
        await thread.send(embed=embed, view=view)

    async def _send_reveal_notifications(
        self, session: ShadowSession, user1: discord.User, user2: discord.User
    ):
        """Send notifications that identities have been revealed (Parallel)."""
        guild = self.bot.get_guild(constants.GUILD_ID)
        if not guild:
            return

        # Fetch threads in parallel? Usually get_thread_by_id is fast if cached,
        # but we can grab objects first.
        t1_task = get_thread_by_id(guild, session.thread1_id)
        t2_task = get_thread_by_id(guild, session.thread2_id)
        thread1, thread2 = await asyncio.gather(t1_task, t2_task)

        if not thread1 or not thread2:
            return

        # Prepare embeds
        embed1 = create_reveal_embed(
            user2.display_name, str(user2.avatar.url) if user2.avatar else None
        )
        embed2 = create_reveal_embed(
            user1.display_name, str(user1.avatar.url) if user1.avatar else None
        )

        # Send in parallel
        await asyncio.gather(
            thread1.send(embed=embed1),
            thread2.send(embed=embed2),
            return_exceptions=True,
        )

    async def _notify_reveal_cancelled(
        self, session: ShadowSession, cancelling_user_id: int
    ):
        """Notify partner that reveal was cancelled."""
        partner_id = session.get_other_user(cancelling_user_id)
        partner_thread_id = session.get_user_thread(partner_id)

        # We need the guild object to get the thread
        # Assuming the thread is in the main guild
        guild = self.bot.get_guild(constants.GUILD_ID)
        if not guild:
            return

        thread = await get_thread_by_id(guild, partner_thread_id)
        if not thread:
            return

        embed = discord.Embed(
            title="❌ Reveal Request Cancelled",
            description="Your partner has cancelled the request.",
            color=0xED4245,
        )
        await thread.send(embed=embed)

    async def _log_reveal(
        self, session: ShadowSession, user1: discord.User, user2: discord.User
    ):
        """Log identity reveal to mod channel."""
        channel = self.bot.get_channel(constants.MOD_LOG_CHANNEL_ID)
        if not channel:
            return

        embed = discord.Embed(
            title="👁️ Identity Reveal",
            description="Users have mutually revealed identities.",
            color=0x57F287,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(
            name="Session ID", value=f"`{session.session_id}`", inline=False
        )
        embed.add_field(
            name="User 1", value=f"{user1.mention} ({user1.id})", inline=True
        )
        embed.add_field(
            name="User 2", value=f"{user2.mention} ({user2.id})", inline=True
        )

        await channel.send(embed=embed)

    async def get_reveal_status(self, session_id: str) -> dict:
        """Get reveal status for a session (Optimized)."""
        session = await self.session_manager.get_session(session_id)
        if not session:
            return {"error": "Session not found"}

        user1 = self.bot.get_user(session.user1_id)
        user2 = self.bot.get_user(session.user2_id)

        return {
            "session_id": session_id,
            "revealed": session.identities_revealed,
            "user1_consent": session.user1_consent,
            "user2_consent": session.user2_consent,
            "user1": {
                "id": session.user1_id,
                "name": user1.name if user1 else "Unknown",
                "avatar": str(user1.avatar.url) if user1 and user1.avatar else None,
            },
            "user2": {
                "id": session.user2_id,
                "name": user2.name if user2 else "Unknown",
                "avatar": str(user2.avatar.url) if user2 and user2.avatar else None,
            },
        }
