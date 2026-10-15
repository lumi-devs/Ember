"""Background task that warns and auto-ends sessions gone quiet."""

import asyncio
import logging
from datetime import datetime

import discord

from .constants import (
    DATE_CHANNEL_INACTIVITY_TIMEOUT_SECONDS,
    DATE_CHANNEL_INACTIVITY_WARN_SECONDS,
    INACTIVITY_TIMEOUT_SECONDS,
    INACTIVITY_WARN_SECONDS,
)

log = logging.getLogger("ember.shadowchat.inactivity")

CHECK_INTERVAL = 60  # seconds between scans


class InactivityWatcher:
    def __init__(self, bot: discord.Client, session_mgr):
        self.bot = bot
        self.session_mgr = session_mgr
        self._task: asyncio.Task | None = None

    def start(self):
        self._task = asyncio.create_task(self._loop(), name="sc_inactivity")

    def stop(self):
        if self._task and not self._task.done():
            self._task.cancel()

    async def _loop(self):
        while True:
            try:
                await asyncio.sleep(CHECK_INTERVAL)
                await self._scan()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Inactivity watcher error: {e}", exc_info=True)

    async def _scan(self):
        now = datetime.utcnow().timestamp()
        # Iterate over a snapshot so mutations during iteration are safe
        for session_id, last_ts in list(self.session_mgr.last_activity.items()):
            idle = now - last_ts

            if idle >= INACTIVITY_TIMEOUT_SECONDS:
                log.info(f"Session {session_id} timed out after {idle:.0f}s idle")
                await self.session_mgr.end_session(session_id, reason="inactivity")

            elif idle >= INACTIVITY_WARN_SECONDS and session_id not in self.session_mgr.warned:
                self.session_mgr.warned.add(session_id)
                await self._warn_session(session_id)

        # Scan date channels for 12h inactivity auto-close
        for ch_id, last_ts in list(self.session_mgr.date_channels.items()):
            idle = now - last_ts

            if idle >= DATE_CHANNEL_INACTIVITY_TIMEOUT_SECONDS:
                log.info(f"Date channel {ch_id} auto-closing after {idle:.0f}s idle")
                await self._close_date_channel(ch_id)

            elif (
                idle >= DATE_CHANNEL_INACTIVITY_WARN_SECONDS
                and ch_id not in self.session_mgr._date_warned
            ):
                self.session_mgr._date_warned.add(ch_id)
                await self._warn_date_channel(ch_id)

    async def _warn_date_channel(self, ch_id: int):
        ch = self.bot.get_channel(ch_id)
        if not ch:
            self.session_mgr.unregister_date_channel(ch_id)
            return
        from .views import _text_view

        try:
            await ch.send(
                view=_text_view(
                    "⏰ **No messages for 11 hours.** This channel will auto-close in **1 hour** "
                    "unless someone sends a message.",
                    accent="#FEE75C",
                )
            )
        except discord.HTTPException:
            pass

    async def _close_date_channel(self, ch_id: int):
        self.session_mgr.unregister_date_channel(ch_id)
        ch = self.bot.get_channel(ch_id)
        if not ch:
            return
        from .views import _text_view

        try:
            await ch.send(
                view=_text_view(
                    "🌑 **Auto-closed.** No activity for 12 hours — this channel has been closed.",
                    accent="#4f545c",
                )
            )
        except discord.HTTPException:
            pass
        try:
            await ch.delete(reason="Date channel auto-closed after 12h inactivity")
        except discord.HTTPException:
            pass

    async def _warn_session(self, session_id: str):
        session = await self.session_mgr.db.session_get(session_id)
        if not session:
            return

        remaining = int((INACTIVITY_TIMEOUT_SECONDS - INACTIVITY_WARN_SECONDS) / 60)

        # Import here to avoid circular import at module level
        from .views import _info_view

        warn_view = _info_view(
            "⏰ Inactivity Warning",
            f"No messages for **15 minutes**.\n"
            f"The session will automatically end in **~{remaining} more minute(s)** "
            f"unless someone sends a message.",
            accent="#FEE75C",
        )

        for ch_id in (session["channel1_id"], session["channel2_id"]):
            ch = self.bot.get_channel(ch_id)
            if ch:
                try:
                    await ch.send(view=warn_view)
                except discord.HTTPException:
                    pass
