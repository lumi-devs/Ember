"""Session creation, teardown, channel + webhook management, reveal."""

import asyncio
import logging
import random
import secrets
from datetime import datetime

import discord
from discord import ui

from .constants import (
    ANON_NAMES,
    ASSETS_DIR,
    CATEGORY_ID,
    CHANNEL_ADJECTIVES,
    CHANNEL_CLEANUP_DELAY,
    CHANNEL_CLEANUP_DELAY_FAST,
    CHANNEL_NOUNS,
    ENTRY_CHANNEL_ID,
    # GUILD_ID removed — guild resolved from session context
    MOD_LOG_CHANNEL_ID,
    REPORT_CHANNEL_ID,
    SESSION_COOLDOWN_SECONDS,
)
from .models import ShadowDB
from .reveal_handler import RevealHandlerMixin
from .report_handler import ReportHandlerMixin
from .views import (
    DateChannelWelcomeView,
    DmNotificationView,
    RevealRequestLayoutView,
    SessionWelcomeLayoutView,
)

log = logging.getLogger("ember.shadowchat.session")

# Supported image extensions for avatar assets
_AVATAR_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


class SessionManager(RevealHandlerMixin, ReportHandlerMixin):
    def __init__(self, bot: discord.Client, db: ShadowDB):
        self.bot = bot
        self.db = db
        # channel_id → session dict (both channels of a session map here)
        self.active: dict[int, dict] = {}
        # session_id → last message timestamp (for inactivity watcher)
        self.last_activity: dict[str, float] = {}
        # session_id → set of user_ids who have been warned about inactivity
        self.warned: set[str] = set()
        # Background task for orphaned channel cleanup
        self._cleanup_task = None

        # Avatar assets: list of (filename, bytes) loaded once at startup
        self._avatar_pool: list[bytes] = []
        self._load_avatars()

        # In-flight reveal request guard: session_ids currently being processed
        # Prevents a double-click from firing two concurrent request_reveal calls.
        self._reveal_in_flight: set[str] = set()

        # Date channel inactivity tracking: channel_id → last message timestamp
        self.date_channels: dict[int, float] = {}
        # channel_id → warned flag
        self._date_warned: set[int] = set()

    # ── Avatar loading ────────────────────────────────────────────────────────

    def _load_avatars(self):
        if not ASSETS_DIR.is_dir():
            return
        for p in ASSETS_DIR.iterdir():
            if p.suffix.lower() in _AVATAR_EXTS:
                try:
                    self._avatar_pool.append(p.read_bytes())
                except OSError:
                    pass
        log.info(f"Loaded {len(self._avatar_pool)} avatar asset(s) from {ASSETS_DIR}")

    def _pick_avatar(self) -> bytes | None:
        return random.choice(self._avatar_pool) if self._avatar_pool else None

    # ── Channel naming ────────────────────────────────────────────────────────

    @staticmethod
    def _random_channel_name() -> str:
        """e.g. 'misty-haven', 'amber-crest' — fully opaque, no session ID."""
        adj = secrets.choice(CHANNEL_ADJECTIVES)
        noun = secrets.choice(CHANNEL_NOUNS)
        return f"{adj}-{noun}"

    # ── Startup restore ───────────────────────────────────────────────────────

    async def restore(self):
        """Reload active sessions from DB after a bot restart."""
        sessions = await self.db.session_get_active_all()
        restored = 0
        missing_channels = 0

        for s in sessions:
            ch1 = self.bot.get_channel(s["channel1_id"])
            ch2 = self.bot.get_channel(s["channel2_id"])
            if not ch1 or not ch2:
                await self.db.session_end(s["session_id"], "channels_missing")
                missing_channels += 1
                await self._notify_users_about_restored_end(s)
                continue
            self.active[s["channel1_id"]] = s
            self.active[s["channel2_id"]] = s
            self.last_activity[s["session_id"]] = datetime.utcnow().timestamp()
            self.bot.add_view(SessionWelcomeLayoutView(s["session_id"]))
            self.bot.add_view(RevealRequestLayoutView(s["session_id"]))
            self.bot.add_view(DateChannelWelcomeView(s["session_id"], "", ""))
            restored += 1

        log.info(
            f"Restored {restored} session(s), {missing_channels} ended due to missing channels"
        )

        self._cleanup_task = asyncio.create_task(
            self._orphaned_channel_cleanup_loop(), name="sc_orphan_cleanup"
        )
        log.info("Orphaned channel cleanup task started")

    async def _orphaned_channel_cleanup_loop(self):
        """Periodically check for and clean up orphaned channels."""
        while True:
            try:
                await asyncio.sleep(300)  # Check every 5 minutes
                await self._cleanup_orphaned_channels()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Orphaned channel cleanup error: {e}", exc_info=True)

    async def _cleanup_orphaned_channels(self):
        """Delete session channels whose session has ended but the channel was never cleaned up."""
        protected = {ENTRY_CHANNEL_ID, MOD_LOG_CHANNEL_ID, REPORT_CHANNEL_ID}

        sessions = await self.db.session_get_active_all()
        active_channel_ids = {s["channel1_id"] for s in sessions} | {
            s["channel2_id"] for s in sessions
        }

        if sessions:
            channel = self.bot.get_channel(sessions[0]["channel1_id"])
            guild = channel.guild if channel else None
        else:
            guild = None
        if not guild:
            return

        category = guild.get_channel(CATEGORY_ID)
        if not category:
            return

        for channel in category.channels:
            if channel.id in protected or channel.id in active_channel_ids:
                continue
            session = await self.db.session_get_by_channel(channel.id)
            if session and session.get("ended_at"):
                try:
                    await channel.delete(reason="Orphaned session channel cleanup")
                    log.info(f"Deleted orphaned channel: {channel.name}")
                except discord.HTTPException as e:
                    log.warning(f"Failed to delete orphaned channel {channel.id}: {e}")

    async def _notify_users_about_restored_end(self, session: dict):
        """Notify users when their session was ended due to missing channels on restart."""
        channel = self.bot.get_channel(session["channel1_id"])
        guild = channel.guild if channel else None
        if not guild:
            return

        for user_id in (session["user1_id"], session["user2_id"]):
            member = guild.get_member(user_id)
            if member:
                try:
                    end_view = ui.LayoutView(timeout=None)
                    end_view.add_item(
                        ui.Container(
                            ui.TextDisplay("## 🌑 ShadowChat Session Ended"),
                            ui.Separator(spacing=discord.SeparatorSpacing.small),
                            ui.TextDisplay(
                                "Your previous session was ended because the private channels\n"
                                "were no longer available (likely due to a bot restart or server issue).\n\n"
                                "You can start a new session anytime by using the ShadowChat entry card!"
                            ),
                            accent_color=discord.Color.from_str("#4f545c"),
                        )
                    )
                    await member.send(view=end_view)
                except discord.Forbidden:
                    pass  # User has DMs disabled

    # ── Session creation ──────────────────────────────────────────────────────

    async def create_session(
        self,
        user1_id: int,
        gender1: str,
        user2_id: int,
        gender2: str,
    ):
        if not self.bot.guilds:
            log.error("No guilds available for session creation")
            return
        guild = self.bot.guilds[0]

        category = guild.get_channel(CATEGORY_ID)
        if not category or not isinstance(category, discord.CategoryChannel):
            log.error(f"Category {CATEGORY_ID} not found or is not a category")
            return

        member1 = guild.get_member(user1_id)
        member2 = guild.get_member(user2_id)
        if not member1 or not member2:
            log.error(f"Members not found: {user1_id}, {user2_id}")
            return

        used = {s["codename1"] for s in self.active.values()} | {
            s["codename2"] for s in self.active.values()
        }
        available = [n for n in ANON_NAMES if n not in used]
        if len(available) < 2:
            available = ANON_NAMES
        codename1, codename2 = random.sample(available, 2)

        session_id = secrets.token_hex(8)

        name1 = self._random_channel_name()
        name2 = self._random_channel_name()
        while name2 == name1:
            name2 = self._random_channel_name()

        try:
            ch1 = await self._create_private_channel(guild, category, member1, name1)
            ch2 = await self._create_private_channel(guild, category, member2, name2)

            # wh1 lives in ch1 (Alice's channel) — it's used to relay Bob's messages TO Alice
            # wh2 lives in ch2 (Bob's channel)   — it's used to relay Alice's messages TO Bob
            # So wh1 should wear codename2's avatar, wh2 should wear codename1's avatar.
            wh1 = await ch1.create_webhook(name=codename2)
            wh2 = await ch2.create_webhook(name=codename1)

            av1 = self._pick_avatar()
            av2 = self._pick_avatar()
            if av1:
                try:
                    await wh1.edit(avatar=av1)
                except discord.HTTPException:
                    pass
            if av2:
                try:
                    await wh2.edit(avatar=av2)
                except discord.HTTPException:
                    pass

        except discord.HTTPException as e:
            log.error(f"Channel/webhook creation failed: {e}")
            return

        await self.db.session_create(
            session_id,
            user1_id,
            user2_id,
            ch1.id,
            ch2.id,
            wh1.url,
            wh2.url,
            codename1,
            codename2,
        )

        session = await self.db.session_get(session_id)
        self.active[ch1.id] = session
        self.active[ch2.id] = session
        self.last_activity[session_id] = datetime.utcnow().timestamp()

        self.bot.add_view(SessionWelcomeLayoutView(session_id))
        self.bot.add_view(RevealRequestLayoutView(session_id))

        wm1_id = await self._send_welcome(ch1, codename1, codename2, session_id)
        wm2_id = await self._send_welcome(ch2, codename2, codename1, session_id)
        await self.db.session_set_welcome_msgs(session_id, wm1_id, wm2_id)

        for member, ch in ((member1, ch1), (member2, ch2)):
            try:
                ping = await ch.send(member.mention)
                await ping.delete()
            except discord.HTTPException:
                pass

        for member, ch in ((member1, ch1), (member2, ch2)):
            muted = await self.db.dm_mute_check(member.id)
            if muted:
                continue
            try:
                await member.send(
                    view=DmNotificationView(
                        channel_id=ch.id, guild_id=ch.guild.id if ch.guild else None
                    )
                )
            except discord.Forbidden:
                pass

        log.info(f"Session {session_id} created: {user1_id}↔{user2_id}")

    async def _create_private_channel(
        self,
        guild: discord.Guild,
        category: discord.CategoryChannel,
        member: discord.Member,
        name: str,
    ) -> discord.TextChannel:
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            member: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                manage_messages=True,
                manage_channels=True,
                manage_webhooks=True,
            ),
        }
        return await guild.create_text_channel(name, category=category, overwrites=overwrites)

    async def _send_welcome(
        self,
        channel: discord.TextChannel,
        your_codename: str,
        partner_codename: str,
        session_id: str,
    ) -> int:
        """Send and pin the welcome card. Returns the message ID."""
        view = SessionWelcomeLayoutView(session_id, your_codename, partner_codename)
        msg = await channel.send(view=view)
        try:
            await msg.pin()
        except discord.HTTPException:
            pass
        return msg.id

    async def _update_welcome_cards(self, session_id: str):
        """After a reveal, swap the Reveal button → Book a Date on both welcome cards."""
        session = await self.db.session_get(session_id)
        if not session:
            return
        pairs = [
            (
                session.get("channel1_id"),
                session.get("welcome_msg1_id"),
                session["codename1"],
                session["codename2"],
            ),
            (
                session.get("channel2_id"),
                session.get("welcome_msg2_id"),
                session["codename2"],
                session["codename1"],
            ),
        ]
        for ch_id, msg_id, your_cn, partner_cn in pairs:
            if not ch_id or not msg_id:
                continue
            ch = self.bot.get_channel(ch_id)
            if not ch:
                continue
            try:
                msg = await ch.fetch_message(msg_id)
                updated_view = SessionWelcomeLayoutView(
                    session_id, your_cn, partner_cn, reveal_done=True
                )
                await msg.edit(
                    view=updated_view,
                    flags=discord.MessageFlags(components_v2=True),
                )
            except discord.HTTPException as e:
                log.warning(f"Failed to update welcome card {msg_id} in {ch_id}: {e}")

    # ── Session end ───────────────────────────────────────────────────────────

    async def end_session(
        self,
        session_id: str,
        reason: str = "ended",
        initiator_id: int | None = None,
        requeue_initiator: bool = False,
        requeue_gender: str | None = None,
    ) -> bool:
        session = await self.db.session_get(session_id)
        if not session or session.get("ended_at"):
            return False

        await self.db.session_end(session_id, reason)

        self.active.pop(session["channel1_id"], None)
        self.active.pop(session["channel2_id"], None)
        self.last_activity.pop(session_id, None)
        self.warned.discard(session_id)
        self._cleanup_warned_set()

        duration = int(datetime.utcnow().timestamp() - session["started_at"])
        for uid in (session["user1_id"], session["user2_id"]):
            msg_count = await self.db.messages_count_session(uid, session_id)
            await self.db.stats_end_session(uid, duration, msg_count)
            await self.db.cooldown_set(uid, SESSION_COOLDOWN_SECONDS)

        ch1 = self.bot.get_channel(session["channel1_id"])
        ch2 = self.bot.get_channel(session["channel2_id"])

        from .views import _text_view

        end_messages = {
            "ended": ("🚪 Chat Ended", "Hope you had a good conversation!", "#4f545c"),
            "skipped": ("⏭️ Chat Skipped", "Re-queuing you for a new match…", "#5865F2"),
            "reported": ("🚨 Session Reported", "This session was reported and ended.", "#ED4245"),
            "inactivity": (
                "⏰ Inactivity",
                "Session ended due to 30 minutes of inactivity.",
                "#FEE75C",
            ),
            "channels_missing": ("⚠️ Session Ended", "Channel was deleted externally.", "#4f545c"),
        }
        title, body, accent = end_messages.get(
            reason, ("🚪 Chat Ended", "The session has ended.", "#4f545c")
        )

        from .views import RatingLayoutView

        skip_flags: dict[int, bool] = {}
        if reason not in ("reported", "channels_missing"):
            for uid in (session["user1_id"], session["user2_id"]):
                should_skip = await self.db.rating_skip_check(uid)
                if should_skip:
                    await self.db.rating_skip_decrement(uid)
                skip_flags[uid] = should_skip

        ch_user_map = {
            session["channel1_id"]: session["user1_id"],
            session["channel2_id"]: session["user2_id"],
        }

        for ch in (ch1, ch2):
            if ch:
                try:
                    await ch.send(view=_text_view(f"## {title}\n{body}", accent=accent))
                    if reason not in ("reported", "channels_missing"):
                        uid = ch_user_map.get(ch.id)
                        if uid and not skip_flags.get(uid, False):
                            await ch.send(view=RatingLayoutView(session_id))
                except discord.HTTPException:
                    pass

        if requeue_initiator and initiator_id and requeue_gender:
            queue_mgr = getattr(self.bot, "sc_queue", None)
            if queue_mgr:
                await queue_mgr.add(initiator_id, requeue_gender)

        delay = (
            CHANNEL_CLEANUP_DELAY_FAST
            if reason in ("ended", "skipped", "reported", "channels_missing")
            else CHANNEL_CLEANUP_DELAY
        )
        asyncio.create_task(
            self._delete_channels_after(ch1, ch2, delay, reason),
            name=f"sc_cleanup_{session_id}",
        )
        return True

    async def _delete_channels_after(
        self,
        ch1: discord.TextChannel | None,
        ch2: discord.TextChannel | None,
        delay: float,
        reason: str = "ended",
    ):
        channels = [c for c in (ch1, ch2) if c]
        if not channels:
            return

        from .views import _text_view

        if delay <= 60:
            await asyncio.sleep(delay)
        else:
            half = delay / 2
            two_min_mark = delay - 120

            await asyncio.sleep(half)
            remaining_after_half = max(1, int((delay - half) / 60))
            warn1_view = _text_view(
                f"⏳ This channel will be deleted in **~{remaining_after_half} minute(s)**.",
                accent="#FEE75C",
            )
            for ch in channels:
                try:
                    await ch.send(view=warn1_view)
                except discord.HTTPException:
                    pass

            gap = two_min_mark - half
            if gap > 0:
                await asyncio.sleep(gap)
                warn2_view = _text_view(
                    "⚠️ This channel will be **deleted in 2 minutes**.", accent="#ED4245"
                )
                for ch in channels:
                    try:
                        await ch.send(view=warn2_view)
                    except discord.HTTPException:
                        pass
                await asyncio.sleep(120)
            else:
                await asyncio.sleep(delay - half)

        for ch in channels:
            try:
                await ch.delete(reason=f"ShadowChat session ended ({reason})")
            except discord.HTTPException:
                pass

    # ── Date channel — lock + cleanup session channels ────────────────────────

    async def _lock_and_delete_session_channels(
        self,
        ch1: discord.TextChannel | None,
        ch2: discord.TextChannel | None,
        date_channel: discord.TextChannel,
        member1: discord.Member | None,
        member2: discord.Member | None,
    ):
        """Lock both session channels (no more typing), post a redirect card, delete after 60s."""
        from .views import _text_view

        redirect_text = f"📅 **You're on a date!** Head to {date_channel.mention}.\n-# *This channel deletes in 1 minute.*"

        for ch, member in ((ch1, member1), (ch2, member2)):
            if not ch:
                continue
            if member:
                try:
                    await ch.set_permissions(
                        member,
                        send_messages=False,
                        reason="Date channel created — session channel locked",
                    )
                except discord.HTTPException:
                    pass
            try:
                await ch.send(view=_text_view(redirect_text, accent="#FEE75C"))
            except discord.HTTPException:
                pass

        await asyncio.sleep(60)
        for ch in (ch1, ch2):
            if ch:
                try:
                    await ch.delete(reason="Date channel created — session channel cleanup")
                except discord.HTTPException:
                    pass

    # ── Lookup helpers ────────────────────────────────────────────────────────

    def get_session_by_channel(self, channel_id: int) -> dict | None:
        return self.active.get(channel_id)

    def touch(self, session_id: str):
        self.last_activity[session_id] = datetime.utcnow().timestamp()

    # ── Cleanup helpers ─────────────────────────────────────────────────────

    def _cleanup_warned_set(self):
        """Remove warned entries for sessions no longer in the active cache."""
        active_session_ids = {s["session_id"] for s in self.active.values()}
        self.warned = {sid for sid in self.warned if sid in active_session_ids}
