"""Report handler mixin for SessionManager."""

from __future__ import annotations

import asyncio
import io
import json as _json
import logging
from datetime import datetime

import aiohttp
import discord

from .constants import REPORT_CHANNEL_ID

log = logging.getLogger("ember.shadowchat.session")


class ReportHandlerMixin:
    async def handle_report(
        self,
        session_id: str,
        reporter_id: int,
        reason: str,
        reporter_attachment: "discord.Attachment | None" = None,
    ):
        session = await self.db.session_get(session_id)
        if not session:
            return

        reported_id = (
            session["user2_id"] if reporter_id == session["user1_id"] else session["user1_id"]
        )
        await self.db.report_add(session_id, reporter_id, reported_id, reason)

        channel = self.bot.get_channel(session["channel1_id"])
        guild = channel.guild if channel else None
        ch1 = self.bot.get_channel(session["channel1_id"])
        ch2 = self.bot.get_channel(session["channel2_id"])
        from .views import _text_view

        for ch in (ch1, ch2):
            if ch:
                try:
                    await ch.send(
                        view=_text_view(
                            "🚨 **A report has been filed.** Collecting evidence — do not delete any messages.",
                            accent="#ED4245",
                        )
                    )
                except discord.HTTPException:
                    pass

        if guild:
            reported_member = guild.get_member(reported_id)
            reporter_member = guild.get_member(reporter_id)

            async def _revoke(ch: discord.TextChannel, member: discord.Member):
                if ch and member:
                    try:
                        await ch.set_permissions(
                            member,
                            view_channel=False,
                            send_messages=False,
                            reason="ShadowChat report — evidence lock",
                        )
                    except discord.HTTPException:
                        pass

            reporter_ch = ch1 if reporter_id == session["user1_id"] else ch2
            reported_ch = ch2 if reporter_id == session["user1_id"] else ch1
            if reported_member:
                await _revoke(reported_ch, reported_member)

            await asyncio.sleep(5)
            if reporter_member:
                await _revoke(reporter_ch, reporter_member)

        messages = await self.db.messages_get_session(session_id)
        log_text = self._format_chat_log(session, messages)
        reveal_status = "revealed" if session.get("reveal_done") else "not revealed"

        evidence_files: list[discord.File] = []
        async with aiohttp.ClientSession() as http:
            for m in messages:
                if len(evidence_files) >= 9:
                    break
                for url in _json.loads(m["attachments"] or "[]"):
                    if len(evidence_files) >= 9:
                        break
                    try:
                        async with http.get(url) as r:
                            if r.status == 200:
                                data = await r.read()
                                fname = url.split("/")[-1].split("?")[0] or "attachment"
                                evidence_files.append(
                                    discord.File(io.BytesIO(data), filename=fname)
                                )
                    except Exception:
                        log.warning(f"Could not re-download evidence file: {url}")

        if reporter_attachment is not None and len(evidence_files) < 10:
            try:
                data = await reporter_attachment.read()
                evidence_files.append(
                    discord.File(io.BytesIO(data), filename=reporter_attachment.filename)
                )
            except Exception:
                log.warning("Could not read reporter evidence attachment")

        def _member_label(user_id: int) -> str:
            if guild:
                m = guild.get_member(user_id)
                if m:
                    return f"{m.display_name} ({user_id})"
            return f"({user_id})"

        log_channel = self.bot.get_channel(REPORT_CHANNEL_ID)
        if log_channel:
            try:
                from .views import ReportLogView

                short_log = log_text if len(log_text) <= 1800 else None
                view = ReportLogView(
                    reporter_id,
                    reported_id,
                    reason,
                    session_id,
                    short_log,
                    reveal_status=reveal_status,
                    reporter_label=_member_label(reporter_id),
                    reported_label=_member_label(reported_id),
                )
                await log_channel.send(view=view)

                if short_log is None:
                    await log_channel.send(
                        file=discord.File(
                            io.BytesIO(log_text.encode()),
                            filename=f"shadowchat_{session_id}.txt",
                        )
                    )

                if evidence_files:
                    for i in range(0, len(evidence_files), 10):
                        batch = evidence_files[i : i + 10]
                        await log_channel.send(
                            content=f"📎 Evidence attachments ({i + 1}–{i + len(batch)}):",
                            files=batch,
                        )
            except discord.HTTPException as e:
                log.error(f"Failed to post report: {e}")

        await self.end_session(session_id, reason="reported")

    def _format_chat_log(self, session: dict, messages: list) -> str:
        lines = [
            f"ShadowChat Log — Session {session['session_id']}",
            f"Started: {datetime.utcfromtimestamp(session['started_at']).strftime('%Y-%m-%d %H:%M UTC')}",
            f"User A ({session['codename1']}): {session['user1_id']}",
            f"User B ({session['codename2']}): {session['user2_id']}",
            "─" * 50,
        ]
        for m in messages:
            ts = datetime.utcfromtimestamp(m["sent_at"]).strftime("%H:%M")
            codename = (
                session["codename1"]
                if m["sender_id"] == session["user1_id"]
                else session["codename2"]
            )
            content = m["content"] or ""
            atts = _json.loads(m["attachments"] or "[]")
            if atts:
                content += " " + " ".join(f"[attachment: {a}]" for a in atts)
            lines.append(f"[{ts}] {codename}: {content}")
        return "\n".join(lines)
