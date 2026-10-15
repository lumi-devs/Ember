"""ShadowChat — anonymous gender-matched relay chat inside Discord."""

import logging
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core.cog import EmberCog
from core.i18n import Translator, cog_i18n
from utils.feature_registry import ConfigField, FieldType, feature

from .constants import (
    ENTRY_CHANNEL_ID,
    GENDER_ROLES,
    RATING_CLEANUP_INTERVAL,
)
from .inactivity_watcher import InactivityWatcher
from .models import ShadowDB
from .queue_manager import QueueManager
from .relay_handler import RelayHandler
from .schema import ShadowChatGuildConfig
from .session_manager import SessionManager
from .views import (
    DmNotificationView,
    EndConfirmLayoutView,
    EntryLayoutView,
    ReportModal,
    RevealChoiceLayoutView,
    _info_view,
)

_ = Translator("shadowchat", __file__)
log = logging.getLogger("ember.shadowchat")


@cog_i18n(_)
@feature(
    display_name="ShadowChat",
    emoji="🌒",
    description="Anonymous gender-matched 1:1 relay chat with ratings and optional reveal system.",
    fields=[
        ConfigField(
            key="entry_channel_id",
            label="Entry Channel",
            type=FieldType.CHANNEL,
            description="Channel where the ShadowChat entry card is posted.",
        ),
        ConfigField(
            key="category_id",
            label="Session Category",
            type=FieldType.CHANNEL,
            description="Category where session channels are created.",
            channel_types=[discord.ChannelType.category],
        ),
        ConfigField(
            key="mod_log_channel_id",
            label="Mod Log Channel",
            type=FieldType.CHANNEL,
            description="Channel for moderation and report logs.",
        ),
        ConfigField(
            key="report_channel_id",
            label="Report Channel",
            type=FieldType.CHANNEL,
            description="Channel where user reports are forwarded.",
        ),
    ],
)
class ShadowChat(EmberCog, name="ShadowChat"):
    """ShadowChat cog — anonymous gender-matched relay chat."""

    default_config = {
        "guild": {
            "entry_channel_id": None,
            "category_id": None,
            "mod_log_channel_id": None,
            "report_channel_id": None,
            "enabled": True,
        }
    }

    def __init__(self, bot: commands.Bot):
        super().__init__(bot)
        self.db.register_schema("guild", ShadowChatGuildConfig)

        self.shadow_db = ShadowDB()
        self.session_mgr = SessionManager(bot, self.shadow_db)
        self.relay = RelayHandler(self.shadow_db)
        self.relay._bot = bot  # lets relay_handler fetch messages for fallback reply embeds
        self.watcher = InactivityWatcher(bot, self.session_mgr)
        self.queue_mgr = QueueManager(self.shadow_db, on_match=self._on_match)

        # Expose on bot so views can find managers via bot reference
        bot.sc_queue = self.queue_mgr
        bot.sc_session = self.session_mgr

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def cog_load(self) -> None:
        await self.shadow_db.init_db()
        await self.relay.start()
        await self.session_mgr.restore()
        await self.queue_mgr.start()
        self.watcher.start()

        # Re-register persistent views
        self.bot.add_view(EntryLayoutView())
        self.bot.add_view(DmNotificationView())

        self._ratings_cleanup.start()

        log.info("ShadowChat cog loaded")

    async def cog_unload(self) -> None:
        self.queue_mgr.stop()
        self.watcher.stop()
        self._ratings_cleanup.cancel()
        if self.session_mgr._cleanup_task and not self.session_mgr._cleanup_task.done():
            self.session_mgr._cleanup_task.cancel()
        await self.relay.stop()

    async def red_delete_data_for_user(
        self,
        *,
        requester: Literal["discord_deleted_user", "owner", "user", "user_strict"],
        user_id: int,
    ) -> None:
        """Delete user's shadowchat data for GDPR compliance."""
        await self.shadow_db.delete_user_data(user_id)
        log.info("Deleted ShadowChat data for user %d (requester=%s)", user_id, requester)

    # ── Ratings cleanup loop ──────────────────────────────────────────────────

    @tasks.loop(seconds=RATING_CLEANUP_INTERVAL)
    async def _ratings_cleanup(self):
        try:
            await self.shadow_db.ratings_cleanup_expired()
        except Exception as e:
            log.error("Ratings cleanup error: %s", e, exc_info=True)

    # ── Matchmaking callback ──────────────────────────────────────────────────

    async def _on_match(self, uid_a: int, gender_a: str, uid_b: int, gender_b: str):
        await self.session_mgr.create_session(uid_a, gender_a, uid_b, gender_b)

    # ── on_message relay ──────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if not message.guild:
            return

        # Touch date channel idle timer if message is in a tracked date channel
        if message.channel.id in self.session_mgr.date_channels:
            self.session_mgr.touch_date_channel(message.channel.id)
            return  # date channels don't relay — just plain chat

        session = self.session_mgr.get_session_by_channel(message.channel.id)
        if not session:
            return

        self.session_mgr.touch(session["session_id"])
        await self.relay.relay(message, session)

    # ── Button handlers ───────────────────────────────────────────────────────

    async def handle_find_match(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        user = interaction.user

        # Check if already in a session (search active sessions by user ID, not current channel)
        already_in_session = any(
            user.id in (s["user1_id"], s["user2_id"]) for s in self.session_mgr.active.values()
        )
        if already_in_session:
            return await interaction.followup.send(
                view=_info_view(
                    "Already in a session", "You're already in an active session!", accent="#ED4245"
                ),
                ephemeral=True,
            )

        # Check if already queued
        if self.queue_mgr.in_queue(user.id):
            return await interaction.followup.send(
                view=_info_view(
                    "Already Queued",
                    "You're already in the queue. Please wait...",
                    accent="#FEE75C",
                ),
                ephemeral=True,
            )

        # Check cooldown
        remaining = await self.shadow_db.cooldown_check(user.id)
        if remaining:
            return await interaction.followup.send(
                view=_info_view(
                    "Cooldown",
                    f"Please wait **{int(remaining)}s** before searching again.",
                    accent="#FEE75C",
                ),
                ephemeral=True,
            )

        # Detect gender from roles
        gender = self._detect_gender(user)
        if not gender:
            role_names = "\n".join(f"• `{g}` → <@&{rid}>" for g, rid in GENDER_ROLES.items())
            return await interaction.followup.send(
                view=_info_view(
                    "Missing Gender Role",
                    f"You need a gender role to use ShadowChat.\n\n**Available roles:**\n{role_names}",
                    accent="#ED4245",
                ),
                ephemeral=True,
            )

        added = await self.queue_mgr.add(user.id, gender)
        if not added:
            return await interaction.followup.send(
                view=_info_view("Already Queued", "You're already in the queue.", accent="#FEE75C"),
                ephemeral=True,
            )

        queue_pos = self.queue_mgr.size()
        stats = await self.shadow_db.global_stats()
        active = stats.get("active_sessions", 0)
        await interaction.followup.send(
            view=_info_view(
                "🔍 Searching...",
                f"**Queue position:** #{queue_pos}\n"
                f"**Active sessions:** {active}\n\n"
                f"You'll get a DM and a private channel when matched.\n"
                f"Click **Leave Queue** to cancel.",
                accent="#5865F2",
            ),
            ephemeral=True,
        )

    async def handle_leave_queue(self, interaction: discord.Interaction):
        removed = await self.queue_mgr.remove(interaction.user.id)
        if removed:
            await interaction.response.send_message(
                view=_info_view(
                    "Left Queue", "You've been removed from the queue.", accent="#57F287"
                ),
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                view=_info_view(
                    "Not in Queue", "You're not currently in the queue.", accent="#4f545c"
                ),
                ephemeral=True,
            )

    async def handle_dm_mute(self, interaction: discord.Interaction):
        await self.shadow_db.dm_mute_set(interaction.user.id, hours=12)
        await interaction.response.send_message(
            view=_info_view(
                "🔕 DMs Muted",
                "Match DMs muted for **12 hours**. You'll still be matched — just no DM ping.",
                accent="#4f545c",
            ),
            ephemeral=True,
        )

    async def handle_my_stats(self, interaction: discord.Interaction):
        from .views import StatsLayoutView

        stats = await self.shadow_db.stats_get(interaction.user.id)
        view = StatsLayoutView(interaction.user, stats)
        try:
            await interaction.response.send_message(view=view, ephemeral=True)
        except discord.InteractionResponded:
            await interaction.followup.send(view=view, ephemeral=True)

    async def handle_end(self, interaction: discord.Interaction, session_id: str, skip: bool):
        session = await self.shadow_db.session_get(session_id)
        if not session or session.get("ended_at"):
            return await interaction.response.send_message(
                view=_info_view(
                    "Already Ended", "This session has already ended.", accent="#4f545c"
                ),
                ephemeral=True,
            )

        if interaction.user.id not in (session["user1_id"], session["user2_id"]):
            return await interaction.response.send_message(
                view=_info_view("Not Your Session", "This isn't your session.", accent="#ED4245"),
                ephemeral=True,
            )

        view = EndConfirmLayoutView(session_id, skip=skip)
        await interaction.response.send_message(view=view, ephemeral=True)

    async def handle_report(
        self,
        interaction: discord.Interaction,
        session_id: str,
        allow_ended: bool = False,
    ):
        session = await self.shadow_db.session_get(session_id)
        if not session:
            return await interaction.response.send_message(
                view=_info_view("Session Not Found", "Session not found.", accent="#4f545c"),
                ephemeral=True,
            )
        # Date-channel reports are allowed on ended sessions; regular reports are not
        if not allow_ended and session.get("ended_at"):
            return await interaction.response.send_message(
                view=_info_view(
                    "Session Already Ended", "This session has already ended.", accent="#4f545c"
                ),
                ephemeral=True,
            )
        if interaction.user.id not in (session["user1_id"], session["user2_id"]):
            return await interaction.response.send_message(
                view=_info_view("Not Your Session", "This isn't your session.", accent="#ED4245"),
                ephemeral=True,
            )

        # Check report rate limit
        if not await self.shadow_db.report_rate_check(interaction.user.id):
            return await interaction.response.send_message(
                view=_info_view(
                    "Report Limit Reached",
                    "You've reached the maximum number of reports allowed per hour. Please wait before reporting again.",
                    accent="#ED4245",
                ),
                ephemeral=True,
            )

        await interaction.response.send_modal(ReportModal(session_id))

    async def handle_report_submit(
        self,
        interaction: discord.Interaction,
        session_id: str,
        reason: str,
        attachment: discord.Attachment | None = None,
    ):
        await interaction.response.defer(ephemeral=True)

        # Record the report for rate limiting
        await self.shadow_db.report_rate_record(interaction.user.id)

        # Extend cooldown for reported user as a penalty
        session = await self.shadow_db.session_get(session_id)
        if session:
            reported_id = (
                session["user2_id"]
                if interaction.user.id == session["user1_id"]
                else session["user1_id"]
            )
            await self.shadow_db.cooldown_extend(reported_id, 300)  # 5 min penalty

        await self.session_mgr.handle_report(
            session_id, interaction.user.id, reason, reporter_attachment=attachment
        )
        await interaction.followup.send(
            view=_info_view(
                "🚨 Report Submitted",
                "Report received. The session has been ended and a log sent to moderators.",
                accent="#57F287",
            ),
            ephemeral=True,
        )

    async def handle_date_channel_delete(self, interaction: discord.Interaction, session_id: str):
        """Called from the End/Delete button on the date channel welcome card."""
        session = await self.shadow_db.session_get(session_id)
        if not session:
            return await interaction.response.send_message(
                view=_info_view(
                    "Not Found", "This channel's session record was not found.", accent="#4f545c"
                ),
                ephemeral=True,
            )
        if interaction.user.id not in (session["user1_id"], session["user2_id"]):
            return await interaction.response.send_message(
                view=_info_view("Not Your Channel", "This isn't your channel.", accent="#ED4245"),
                ephemeral=True,
            )
        channel = interaction.channel
        self.session_mgr.unregister_date_channel(channel.id)
        await interaction.response.send_message(
            view=_info_view(
                "🗑️ Deleting", "Closing and deleting this channel...", accent="#4f545c"
            ),
            ephemeral=True,
        )
        try:
            await channel.delete(reason="Date channel closed by user")
        except discord.HTTPException:
            pass

    async def handle_reveal_request(self, interaction: discord.Interaction, session_id: str):
        await interaction.response.defer(ephemeral=True)
        # Always fetch fresh from DB — cached session can have stale reveal_done
        session = await self.shadow_db.session_get(session_id)
        if not session or session.get("ended_at"):
            return await interaction.followup.send(
                view=_info_view(
                    "Session Not Found", "Session not found or already ended.", accent="#4f545c"
                ),
                ephemeral=True,
            )
        if interaction.user.id not in (session["user1_id"], session["user2_id"]):
            return await interaction.followup.send(
                view=_info_view("Not Your Session", "This isn't your session.", accent="#ED4245"),
                ephemeral=True,
            )
        if session.get("reveal_done"):
            return await interaction.followup.send(
                view=_info_view(
                    "Already Revealed", "Reveal has already been completed.", accent="#4f545c"
                ),
                ephemeral=True,
            )

        ok, msg = await self.session_mgr.request_reveal(session_id, interaction.user.id)
        accent = "#57F287" if ok else "#ED4245"
        await interaction.followup.send(
            view=_info_view("✨ Reveal Request" if ok else "Reveal Failed", msg, accent=accent),
            ephemeral=True,
        )

    async def handle_reveal_accept(self, interaction: discord.Interaction, session_id: str):
        """Partner accepted the reveal — ask them how they want to reveal."""
        session = await self.shadow_db.session_get(session_id)
        if not session or session.get("ended_at"):
            return await interaction.response.send_message(
                view=_info_view(
                    "Session Not Found", "Session not found or already ended.", accent="#4f545c"
                ),
                ephemeral=True,
            )
        req = await self.shadow_db.reveal_get(session_id)
        if not req or req["accepted"] != 0:
            return await interaction.response.send_message(
                view=_info_view(
                    "No Pending Request", "No pending reveal request found.", accent="#4f545c"
                ),
                ephemeral=True,
            )
        # Only the non-requester (the partner) can accept
        if interaction.user.id == req["requester_id"]:
            return await interaction.response.send_message(
                view=_info_view(
                    "Can't Accept Own Request",
                    "You can't accept your own reveal request — wait for your partner to respond.",
                    accent="#ED4245",
                ),
                ephemeral=True,
            )
        # Ensure acceptor is actually in this session
        if interaction.user.id not in (session["user1_id"], session["user2_id"]):
            return await interaction.response.send_message(
                view=_info_view("Not Your Session", "This isn't your session.", accent="#ED4245"),
                ephemeral=True,
            )

        view = RevealChoiceLayoutView(session_id)
        await interaction.response.send_message(view=view, ephemeral=True)

    async def handle_reveal_here(self, interaction: discord.Interaction, session_id: str):
        await interaction.response.defer(ephemeral=True)
        ok, msg = await self.session_mgr.reveal_here(session_id, interaction.user.id)
        accent = "#57F287" if ok else "#ED4245"
        try:
            await interaction.edit_original_response(
                view=_info_view("✨ Revealed" if ok else "Reveal Failed", msg, accent=accent)
            )
        except Exception:
            pass

    async def handle_reveal_book_date(self, interaction: discord.Interaction, session_id: str):
        await interaction.response.defer(ephemeral=True)
        session = await self.shadow_db.session_get(session_id)
        if not session:
            try:
                await interaction.edit_original_response(
                    view=_info_view(
                        "Session Not Found", "Session not found or already ended.", accent="#4f545c"
                    )
                )
            except Exception:
                pass
            return

        if session.get("reveal_done"):
            # Reveal already completed — create date channel directly without
            # needing a pending reveal request.
            ok, msg = await self.session_mgr.create_date_channel(session_id, interaction.user.id)
        else:
            # Still in the reveal flow — check for a pending request.
            ok, msg = await self.session_mgr.reveal_book_date(session_id, interaction.user.id)

        accent = "#57F287" if ok else "#ED4245"
        try:
            await interaction.edit_original_response(
                view=_info_view("📅 Date Created" if ok else "Failed", msg, accent=accent)
            )
        except Exception:
            pass

    async def handle_reveal_decline(self, interaction: discord.Interaction, session_id: str):
        await interaction.response.defer(ephemeral=True)
        await self.session_mgr.decline_reveal(session_id)
        try:
            await interaction.edit_original_response(
                view=_info_view("Declined", "You declined the reveal request.", accent="#4f545c")
            )
        except Exception:
            pass

    # ── Public API called by EndConfirmView ───────────────────────────────────

    async def end_session(
        self,
        session_id: str,
        reason: str,
        initiator_id: int,
        requeue_initiator: bool,
    ):
        requeue_gender = None
        if requeue_initiator:
            session = await self.shadow_db.session_get(session_id)
            if session:
                # Derive guild from any bot guild that has the member (multi-guild safe)
                guild = next(
                    (g for g in self.bot.guilds if g.get_member(initiator_id)),
                    None,
                )
                if guild:
                    member = guild.get_member(initiator_id)
                    if member:
                        requeue_gender = self._detect_gender(member)

        await self.session_mgr.end_session(
            session_id,
            reason=reason,
            initiator_id=initiator_id,
            requeue_initiator=requeue_initiator,
            requeue_gender=requeue_gender,
        )

    # ── User slash commands (/chat ...) ──────────────────────────────────────

    chat = app_commands.Group(
        name="chat",
        description="ShadowChat controls — use these inside your session channel.",
    )

    @chat.command(name="end", description="End your current ShadowChat session.")
    async def chat_end(self, interaction: discord.Interaction):
        session = self.session_mgr.get_session_by_channel(interaction.channel_id)
        if not session:
            return await interaction.response.send_message(
                "No active session in this channel.", ephemeral=True
            )
        await self.handle_end(interaction, session["session_id"], skip=False)

    @chat.command(name="skip", description="Skip and re-queue for a new match.")
    async def chat_skip(self, interaction: discord.Interaction):
        session = self.session_mgr.get_session_by_channel(interaction.channel_id)
        if not session:
            return await interaction.response.send_message(
                "No active session in this channel.", ephemeral=True
            )
        await self.handle_end(interaction, session["session_id"], skip=True)

    @chat.command(name="report", description="Report your partner and end the session.")
    async def chat_report(self, interaction: discord.Interaction):
        session = self.session_mgr.get_session_by_channel(interaction.channel_id)
        if not session:
            return await interaction.response.send_message(
                "No active session in this channel.", ephemeral=True
            )
        await self.handle_report(interaction, session["session_id"])

    @chat.command(name="reveal", description="Request to reveal your identity to your partner.")
    async def chat_reveal(self, interaction: discord.Interaction):
        session = self.session_mgr.get_session_by_channel(interaction.channel_id)
        if not session:
            return await interaction.response.send_message(
                "No active session in this channel.", ephemeral=True
            )
        await self.handle_reveal_request(interaction, session["session_id"])

    @chat.command(name="stats", description="View your ShadowChat stats.")
    async def chat_stats(self, interaction: discord.Interaction):
        await self.handle_my_stats(interaction)

    @chat.command(name="help", description="Show all ShadowChat commands and how to use them.")
    async def chat_help(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            view=_info_view(
                "🌑 ShadowChat — Commands",
                "**In your session channel:**\n"
                "- `/chat end` — End the current session\n"
                "- `/chat skip` — End and immediately re-queue for a new match\n"
                "- `/chat report` — Report your partner and end the session\n"
                "- `/chat reveal` — Request to unmask identities\n\n"
                "**Anywhere:**\n"
                "- `/chat stats` — See your total chats, time, and messages\n"
                "- `/chat help` — Show this message\n\n"
                "-# *You can also use the buttons in the pinned card at the top of your session channel.*",
                accent="#5865F2",
            ),
            ephemeral=True,
        )

    # ── Admin commands ────────────────────────────────────────────────────────

    sc_group = app_commands.Group(
        name="sc",
        description="ShadowChat admin commands",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @sc_group.command(name="setup", description="Post the ShadowChat entry card")
    async def sc_setup(self, interaction: discord.Interaction):
        entry_ch_id = await self.db.guild(interaction.guild.id).get(
            "entry_channel_id", default=ENTRY_CHANNEL_ID
        )
        channel = interaction.guild.get_channel(entry_ch_id) if entry_ch_id else None
        if not channel:
            return await interaction.response.send_message(
                view=_info_view(
                    "Channel Not Found",
                    "Entry channel not configured. Set it via `/config` → ShadowChat → Entry Channel, or set `SC_ENTRY_CHANNEL_ID` env var.",
                    accent="#ED4245",
                ),
                ephemeral=True,
            )
        stats = await self.shadow_db.global_stats()
        view = EntryLayoutView(stats=stats)
        await channel.send(view=view)
        await interaction.response.send_message(
            view=_info_view(
                "✅ Setup Complete", f"Entry card posted in {channel.mention}.", accent="#57F287"
            ),
            ephemeral=True,
        )

    @sc_group.command(name="queue", description="Show current queue state")
    async def sc_queue_info(self, interaction: discord.Interaction):
        rows = await self.shadow_db.queue_get_all()
        if not rows:
            return await interaction.response.send_message(
                view=_info_view("Queue Empty", "No users currently waiting.", accent="#4f545c"),
                ephemeral=True,
            )
        lines = "\n".join(f"• <@{r['user_id']}> — `{r['gender']}`" for r in rows)
        await interaction.response.send_message(
            view=_info_view(f"Queue — {len(rows)} waiting", lines, accent="#5865F2"),
            ephemeral=True,
        )

    @sc_group.command(name="sessions", description="Show active sessions")
    async def sc_sessions(self, interaction: discord.Interaction):
        sessions = await self.shadow_db.session_get_active_all()
        if not sessions:
            return await interaction.response.send_message(
                view=_info_view(
                    "No Active Sessions", "Nothing running right now.", accent="#4f545c"
                ),
                ephemeral=True,
            )
        lines = "\n".join(
            f"• `{s['session_id'][:8]}` — "
            f"<@{s['user1_id']}> ({s['codename1']}) ↔ "
            f"<@{s['user2_id']}> ({s['codename2']})"
            for s in sessions
        )
        await interaction.response.send_message(
            view=_info_view(f"Active Sessions — {len(sessions)}", lines, accent="#5865F2"),
            ephemeral=True,
        )

    @sc_group.command(name="end", description="Force-end a session by ID")
    async def sc_end_session(self, interaction: discord.Interaction, session_id: str):
        sessions = await self.shadow_db.session_get_active_all()
        target = next((s for s in sessions if s["session_id"].startswith(session_id)), None)
        if not target:
            return await interaction.response.send_message(
                view=_info_view(
                    "Not Found",
                    f"No active session starting with `{session_id}`.",
                    accent="#ED4245",
                ),
                ephemeral=True,
            )
        await self.session_mgr.end_session(target["session_id"], reason="admin_ended")
        await interaction.response.send_message(
            view=_info_view(
                "✅ Session Ended",
                f"Session `{target['session_id'][:8]}` has been ended.",
                accent="#57F287",
            ),
            ephemeral=True,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _detect_gender(self, member: discord.Member) -> str | None:
        """Return the gender string for a member based on their roles, or None."""
        member_role_ids = {r.id for r in member.roles}
        for gender, role_id in GENDER_ROLES.items():
            if role_id in member_role_ids:
                return gender
        return None

    async def handle_rating(self, interaction: discord.Interaction, session_id: str, rating: int):
        if not 1 <= rating <= 5:
            return await interaction.response.send_message(
                view=_info_view(
                    "Invalid Rating", "Rating must be between 1 and 5.", accent="#ED4245"
                ),
                ephemeral=True,
            )
        # Determine the partner (the one being rated)
        session = await self.shadow_db.session_get(session_id)
        if session:
            rated_user_id = (
                session["user2_id"]
                if interaction.user.id == session["user1_id"]
                else session["user1_id"]
            )
        else:
            rated_user_id = 0  # session deleted — store anyway for historical record
        await self.shadow_db.rating_add(session_id, interaction.user.id, rated_user_id, rating)
        from .views import RatingDoneLayoutView

        try:
            await interaction.response.edit_message(view=RatingDoneLayoutView(rating))
        except Exception:
            await interaction.response.send_message(
                view=_info_view(
                    "⭐ Thanks!", f"You rated this session **{rating}/5**.", accent="#FAA61A"
                ),
                ephemeral=True,
            )

    async def handle_rating_skip(self, interaction: discord.Interaction, session_id: str):
        await self.shadow_db.rating_skip_set(interaction.user.id, n=10)
        from .views import RatingSkipDoneLayoutView

        try:
            await interaction.response.edit_message(view=RatingSkipDoneLayoutView())
        except Exception:
            await interaction.response.send_message(
                view=_info_view(
                    "🚫 Rating Skipped",
                    "Rating skipped for the next **10 chats**.",
                    accent="#4f545c",
                ),
                ephemeral=True,
            )

    # ── Reaction relay (on_raw_reaction_add) ────────────────────────────────

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        """When a user reacts to any message in a session channel, relay it."""
        if payload.user_id == self.bot.user.id:
            return
        session = self.session_mgr.get_session_by_channel(payload.channel_id)
        if not session:
            return

        # Determine which side the reactor is on and find partner's webhook
        is_ch1 = payload.channel_id == session["channel1_id"]
        if is_ch1:
            webhook_url = session["webhook2_url"]
            codename = session["codename1"]
        else:
            webhook_url = session["webhook1_url"]
            codename = session["codename2"]

        emoji_str = str(payload.emoji)

        # Look up what message was reacted to — get content + build jump URL
        msg_content: str | None = None
        row = await self.shadow_db.msg_map_lookup(
            session["session_id"], payload.channel_id, payload.message_id
        )
        if row and row.get("content"):
            msg_content = row["content"]
        msg_url = f"https://discord.com/channels/{payload.guild_id}/{payload.channel_id}/{payload.message_id}"

        # Send the reaction notification via webhook to partner's channel
        await self.relay.send_reaction(
            webhook_url=webhook_url,
            codename=codename,
            emoji_str=emoji_str,
            msg_url=msg_url,
            msg_content=msg_content,
        )


setup = ShadowChat.make_setup()
