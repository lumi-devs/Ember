"""Reveal handler mixin for SessionManager."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import aiohttp
import discord

from .constants import CATEGORY_ID, REVEAL_TIMEOUT_SECONDS

log = logging.getLogger("ember.shadowchat.session")


class RevealHandlerMixin:
    async def request_reveal(self, session_id: str, requester_id: int):
        if session_id in self._reveal_in_flight:
            return False, "A reveal request is already being processed."
        self._reveal_in_flight.add(session_id)
        try:
            session = await self.db.session_get(session_id)
            if not session or session.get("reveal_done"):
                return False, "Reveal already completed or session not found."

            existing = await self.db.reveal_get(session_id)
            if existing and existing["accepted"] == 0:
                return False, "A reveal request is already pending."

            await self.db.reveal_set(session_id, requester_id)

            is_user1 = requester_id == session["user1_id"]
            requester_channel_id = session["channel1_id"] if is_user1 else session["channel2_id"]
            partner_channel_id = session["channel2_id"] if is_user1 else session["channel1_id"]

            partner_channel = self.bot.get_channel(partner_channel_id)
            requester_channel = self.bot.get_channel(requester_channel_id)

            if partner_channel:
                from .views import RevealRequestLayoutView

                view = RevealRequestLayoutView(session_id, timeout_secs=REVEAL_TIMEOUT_SECONDS)
                await partner_channel.send(view=view)
                asyncio.create_task(
                    self._expire_reveal(session_id, requester_channel),
                    name=f"sc_reveal_expire_{session_id}",
                )

            return True, "✨ Reveal request sent to your partner."
        finally:
            self._reveal_in_flight.discard(session_id)

    async def _expire_reveal(self, session_id: str, requester_channel: discord.TextChannel):
        """After timeout, clear the request and notify the *requester* that no response came."""
        await asyncio.sleep(REVEAL_TIMEOUT_SECONDS)
        req = await self.db.reveal_get(session_id)
        if req and req["accepted"] == 0:
            await self.db.reveal_clear(session_id)
            if requester_channel:
                from .views import _text_view

                try:
                    await requester_channel.send(
                        view=_text_view(
                            "⏰ Your reveal request expired — partner didn't respond.",
                            accent="#FEE75C",
                        )
                    )
                except discord.HTTPException:
                    pass

    async def _mark_reveal_done(self, session_id: str):
        """Shared bookkeeping: accept + mark done + clear pending request."""
        await self.db.reveal_accept(session_id)
        await self.db.session_set_reveal_done(session_id)
        await self.db.reveal_clear(session_id)

    async def reveal_here(self, session_id: str, acceptor_id: int):
        """Reveal in-place: webhook names + avatars become the real users."""
        session = await self.db.session_get(session_id)
        if not session:
            return False, "Session not found."

        req = await self.db.reveal_get(session_id)
        if not req or req["accepted"] != 0:
            return False, "No pending reveal request."

        await self._mark_reveal_done(session_id)

        channel = self.bot.get_channel(session["channel1_id"])
        guild = channel.guild if channel else None
        if not guild:
            return False, "Guild not found."

        member1 = guild.get_member(session["user1_id"])
        member2 = guild.get_member(session["user2_id"])
        if not member1 or not member2:
            return False, "Could not find both members."

        async with aiohttp.ClientSession() as http:

            async def _fetch_avatar(member: discord.Member) -> bytes | None:
                try:
                    async with http.get(str(member.display_avatar.with_size(128).url)) as r:
                        if r.status == 200:
                            return await r.read()
                except Exception:
                    pass
                return None

            av1_bytes, av2_bytes = await asyncio.gather(
                _fetch_avatar(member1), _fetch_avatar(member2)
            )

            try:
                wh1 = discord.Webhook.from_url(session["webhook1_url"], session=http)
                wh2 = discord.Webhook.from_url(session["webhook2_url"], session=http)
                wh1_obj = await wh1.fetch()
                wh2_obj = await wh2.fetch()
                await wh1_obj.edit(name=member2.display_name, avatar=av2_bytes)
                await wh2_obj.edit(name=member1.display_name, avatar=av1_bytes)
            except discord.HTTPException as e:
                log.error(f"Webhook edit for reveal_here failed: {e}")

        real_name1 = member1.display_name
        real_name2 = member2.display_name
        old_codename1 = session["codename1"]
        old_codename2 = session["codename2"]
        for cached in self.active.values():
            if cached.get("session_id") == session_id:
                cached["codename1"] = real_name1
                cached["codename2"] = real_name2
        await self.db.session_update_codenames(session_id, real_name1, real_name2)

        av1_url = str(member1.display_avatar.with_size(512).url)
        av2_url = str(member2.display_avatar.with_size(512).url)

        from .views import RevealAnnouncementView

        announce_view = RevealAnnouncementView(
            codename1=old_codename1,
            codename2=old_codename2,
            name1=real_name1,
            name2=real_name2,
            avatar1_url=av1_url,
            avatar2_url=av2_url,
        )

        ch1 = self.bot.get_channel(session["channel1_id"])
        ch2 = self.bot.get_channel(session["channel2_id"])
        for ch in (ch1, ch2):
            if ch:
                try:
                    await ch.send(view=announce_view)
                except discord.HTTPException:
                    pass

        await self._update_welcome_cards(session_id)

        return True, "✨ Reveal complete — identities unmasked in your channels."

    async def reveal_book_date(self, session_id: str, acceptor_id: int):
        """Reveal by creating a shared channel for both users."""
        session = await self.db.session_get(session_id)
        if not session:
            return False, "Session not found."

        req = await self.db.reveal_get(session_id)
        if not req or req["accepted"] != 0:
            return False, "No pending reveal request."

        await self._mark_reveal_done(session_id)

        channel = self.bot.get_channel(session["channel1_id"])
        guild = channel.guild if channel else None
        if not guild:
            return False, "Guild not found."

        member1 = guild.get_member(session["user1_id"])
        member2 = guild.get_member(session["user2_id"])

        reveal_channel = None
        if member1 and member2:
            try:
                category = guild.get_channel(CATEGORY_ID)
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(view_channel=False),
                    member1: discord.PermissionOverwrite(
                        view_channel=True, send_messages=True, read_message_history=True
                    ),
                    member2: discord.PermissionOverwrite(
                        view_channel=True, send_messages=True, read_message_history=True
                    ),
                    guild.me: discord.PermissionOverwrite(
                        view_channel=True, send_messages=True, manage_channels=True
                    ),
                }
                reveal_channel = await guild.create_text_channel(
                    f"date-{session_id[:6]}",
                    category=category if isinstance(category, discord.CategoryChannel) else None,
                    overwrites=overwrites,
                )
                av1_url = str(member1.display_avatar.with_size(512).url)
                av2_url = str(member2.display_avatar.with_size(512).url)
                from .views import DateChannelWelcomeView

                welcome_view = DateChannelWelcomeView(
                    session_id,
                    member1.display_name,
                    member2.display_name,
                    avatar1_url=av1_url,
                    avatar2_url=av2_url,
                )
                self.bot.add_view(welcome_view)
                msg = await reveal_channel.send(view=welcome_view)
                try:
                    await msg.pin()
                except discord.HTTPException:
                    pass
                self.register_date_channel(reveal_channel.id)
            except discord.HTTPException as e:
                log.error(f"Reveal channel creation failed: {e}")

        ch1 = self.bot.get_channel(session["channel1_id"])
        ch2 = self.bot.get_channel(session["channel2_id"])
        reveal_mention = reveal_channel.mention if reveal_channel else "a new channel"

        if member1 and member2:
            av1_url = str(member1.display_avatar.with_size(512).url)
            av2_url = str(member2.display_avatar.with_size(512).url)
            from .views import RevealAnnouncementView

            announce_view = RevealAnnouncementView(
                codename1=session["codename1"],
                codename2=session["codename2"],
                name1=member1.display_name,
                name2=member2.display_name,
                avatar1_url=av1_url,
                avatar2_url=av2_url,
            )
            for ch in (ch1, ch2, reveal_channel):
                if ch:
                    try:
                        await ch.send(view=announce_view)
                        from .views import _text_view

                        await ch.send(
                            view=_text_view(
                                f"📅 You've been added to {reveal_mention}.", accent="#FEE75C"
                            )
                        )
                    except discord.HTTPException:
                        pass
        else:
            from .views import _text_view

            for ch in (ch1, ch2):
                if ch:
                    try:
                        await ch.send(
                            view=_text_view(
                                f"✅ **Reveal accepted!** Both of you have been added to {reveal_mention}.",
                                accent="#57F287",
                            )
                        )
                    except discord.HTTPException:
                        pass

        if reveal_channel and member1 and member2:
            try:
                ping = await reveal_channel.send(f"{member1.mention} {member2.mention}")
                await ping.delete()
            except discord.HTTPException:
                pass

        if reveal_channel:
            asyncio.create_task(
                self._lock_and_delete_session_channels(ch1, ch2, reveal_channel, member1, member2),
                name=f"sc_date_cleanup_{session_id}",
            )

        return True, "✨ Reveal complete — shared channel created."

    async def create_date_channel(self, session_id: str, requester_id: int):
        """Create a shared date channel for an already-revealed session.

        Called when a user clicks 'Book a Date' on the welcome card *after* a
        reveal has already been completed (i.e. no pending reveal request
        exists).  Skips the reveal-request check entirely.
        """
        session = await self.db.session_get(session_id)
        if not session:
            return False, "Session not found."
        if not session.get("reveal_done"):
            return False, "Reveal hasn't been completed yet."

        channel = self.bot.get_channel(session["channel1_id"])
        guild = channel.guild if channel else None
        if not guild:
            return False, "Guild not found."

        member1 = guild.get_member(session["user1_id"])
        member2 = guild.get_member(session["user2_id"])

        reveal_channel = None
        if member1 and member2:
            try:
                category = guild.get_channel(CATEGORY_ID)
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(view_channel=False),
                    member1: discord.PermissionOverwrite(
                        view_channel=True, send_messages=True, read_message_history=True
                    ),
                    member2: discord.PermissionOverwrite(
                        view_channel=True, send_messages=True, read_message_history=True
                    ),
                    guild.me: discord.PermissionOverwrite(
                        view_channel=True, send_messages=True, manage_channels=True
                    ),
                }
                reveal_channel = await guild.create_text_channel(
                    f"date-{session_id[:6]}",
                    category=category if isinstance(category, discord.CategoryChannel) else None,
                    overwrites=overwrites,
                )
                av1_url = str(member1.display_avatar.with_size(512).url)
                av2_url = str(member2.display_avatar.with_size(512).url)
                from .views import DateChannelWelcomeView

                welcome_view = DateChannelWelcomeView(
                    session_id,
                    member1.display_name,
                    member2.display_name,
                    avatar1_url=av1_url,
                    avatar2_url=av2_url,
                )
                self.bot.add_view(welcome_view)
                msg = await reveal_channel.send(view=welcome_view)
                try:
                    await msg.pin()
                except discord.HTTPException:
                    pass
                self.register_date_channel(reveal_channel.id)
            except discord.HTTPException as e:
                log.error(f"Date channel creation failed: {e}")
                return False, "Failed to create date channel."

        reveal_mention = reveal_channel.mention if reveal_channel else "a new channel"
        ch1 = self.bot.get_channel(session["channel1_id"])
        ch2 = self.bot.get_channel(session["channel2_id"])
        from .views import RevealAnnouncementView

        if member1 and member2 and reveal_channel:
            av1_url = str(member1.display_avatar.with_size(512).url)
            av2_url = str(member2.display_avatar.with_size(512).url)
            announce_view = RevealAnnouncementView(
                codename1=session["codename1"],
                codename2=session["codename2"],
                name1=member1.display_name,
                name2=member2.display_name,
                avatar1_url=av1_url,
                avatar2_url=av2_url,
            )
            for ch in (ch1, ch2, reveal_channel):
                if ch:
                    try:
                        await ch.send(view=announce_view)
                    except discord.HTTPException:
                        pass

        if reveal_channel and member1 and member2:
            try:
                ping = await reveal_channel.send(f"{member1.mention} {member2.mention}")
                await ping.delete()
            except discord.HTTPException:
                pass

        if reveal_channel:
            asyncio.create_task(
                self._lock_and_delete_session_channels(ch1, ch2, reveal_channel, member1, member2),
                name=f"sc_date_cleanup_{session_id}",
            )

        return True, f"✨ Date channel created — head to {reveal_mention}."

    def register_date_channel(self, channel_id: int):
        """Start tracking a date channel for inactivity auto-close."""
        self.date_channels[channel_id] = datetime.utcnow().timestamp()

    def touch_date_channel(self, channel_id: int):
        """Record activity in a date channel."""
        if channel_id in self.date_channels:
            self.date_channels[channel_id] = datetime.utcnow().timestamp()
            self._date_warned.discard(channel_id)

    def unregister_date_channel(self, channel_id: int):
        """Remove a date channel from inactivity tracking."""
        self.date_channels.pop(channel_id, None)
        self._date_warned.discard(channel_id)

    async def accept_reveal(self, session_id: str, acceptor_id: int):
        return await self.reveal_book_date(session_id, acceptor_id)

    async def decline_reveal(self, session_id: str):
        req = await self.db.reveal_get(session_id)
        await self.db.reveal_clear(session_id)
        session = await self.db.session_get(session_id)
        if not session:
            return

        requester_id = req["requester_id"] if req else None
        if requester_id:
            requester_channel_id = (
                session["channel1_id"]
                if requester_id == session["user1_id"]
                else session["channel2_id"]
            )
            ch = self.bot.get_channel(requester_channel_id)
            if ch:
                from .views import _text_view

                try:
                    await ch.send(
                        view=_text_view(
                            "❌ Your partner declined the reveal request.", accent="#ED4245"
                        )
                    )
                except discord.HTTPException:
                    pass
