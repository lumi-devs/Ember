"""Main Staff Analytics cog — no help-event nonsense, clean tracking, Wick-aware mod parsing."""

from collections import defaultdict
import io
import json
import re
import discord
from discord.ext import commands, tasks
from discord import app_commands
from typing import Optional, List, Dict, Set, Tuple
import asyncio
import logging
from datetime import datetime, timedelta

from .database import DatabaseManager
from .metrics import MetricsTracker
from .config import StaffAnalyticsConfig
from .models import (
    StaffMessage, Bump, StaffNote, RankTier, EngagementEvent, ReactionReceived,
    PingResponseEvent,
)
from .utils import (
    format_number, format_seconds, format_relative_time,
    create_progress_bar, create_activity_heatmap_compact,
    create_error_embed, create_success_embed,
    create_info_embed, create_warning_embed,
    get_member_safe, parse_duration, get_rank_badge,
    format_trend_delta, format_trend_arrow, format_kpi_status,
    build_comparison_table,
)
from .constants import (
    EMBED_COLORS, MODERATION_COMMANDS, MODERATION_COMMAND_RE, WICK_RESULT_RE,
    RANK_TIERS, SCORE_WEIGHTS,
    NEW_MEMBER_THRESHOLD_DAYS, KPI_TARGETS, ACHIEVEMENT_THRESHOLDS,
    LEADERBOARD_LIMIT, MEDAL_EMOJIS,
    QUESTION_RE, QUESTION_KEYWORDS, ACHIEVEMENT_DECAY_DAYS,
    PING_RESPONSE_WINDOW, STAFF_ROLE_PING_KEYWORDS,
    DISBOARD_BOT_ID, STAFF_DB_PATH,
    RANKUP_HYSTERESIS, DAILY_CHALLENGES,
)

logger = logging.getLogger(__name__)
_STAFF_CACHE_TTL = 300


class ModApprovalView(discord.ui.View):
    """Persistent view attached to a pending mod-action review embed.

    Approve  → inserts the moderation action into the DB and credits the mod.
    Reject   → marks the action rejected; no DB credit.
    Only staff with manage_guild (or higher) may click.
    """

    def __init__(self, cog: "StaffAnalytics", pending_id: int, moderator_id: int, action_type: str, target_id: int):
        super().__init__(timeout=None)   # persistent across restarts
        self.cog          = cog
        self.pending_id   = pending_id
        self.moderator_id = moderator_id
        self.action_type  = action_type
        self.target_id    = target_id

    async def _is_authorized(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild:
            return False
        member = interaction.guild.get_member(interaction.user.id)
        return member is not None and member.guild_permissions.manage_guild

    @discord.ui.button(label="✅ Approve", style=discord.ButtonStyle.success, custom_id="modapprove")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._is_authorized(interaction):
            await interaction.response.send_message("❌ Only senior staff can approve mod actions.", ephemeral=True)
            return
        row = await self.cog.db.approve_pending_mod_action(self.pending_id)
        if not row:
            await interaction.response.send_message("This action was already reviewed.", ephemeral=True)
            return

        # Now credit the mod
        from .constants import MODERATION_SEVERITY
        action = self.cog.metrics.track_moderation(
            moderator_id=row["moderator_id"],
            target_id=row["target_id"],
            command=row["action_type"],
            timestamp=datetime.fromisoformat(row["detected_at"]),
            reason=row.get("reason"),
        )
        if action:
            await self.cog.db.insert_moderation_action(action)

        # Update the embed to show approved
        embed = interaction.message.embeds[0] if interaction.message.embeds else discord.Embed()
        embed.color = discord.Color.green()
        embed.set_footer(text=f"✅ Approved by {interaction.user.display_name} · {discord.utils.utcnow().strftime('%H:%M UTC')}")
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(embed=embed, view=self)
        logger.info("Mod action #%d approved by %d", self.pending_id, interaction.user.id)

    @discord.ui.button(label="❌ Reject", style=discord.ButtonStyle.danger, custom_id="modreject")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._is_authorized(interaction):
            await interaction.response.send_message("❌ Only senior staff can reject mod actions.", ephemeral=True)
            return
        await self.cog.db.reject_pending_mod_action(self.pending_id)

        embed = interaction.message.embeds[0] if interaction.message.embeds else discord.Embed()
        embed.color = discord.Color.red()
        embed.set_footer(text=f"❌ Rejected by {interaction.user.display_name} · {discord.utils.utcnow().strftime('%H:%M UTC')}")
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(embed=embed, view=self)
        logger.info("Mod action #%d rejected by %d", self.pending_id, interaction.user.id)


class StaffAnalytics(commands.Cog):
    """Tracks staff engagement, moderation, community interaction, and more."""

    def __init__(self, bot: commands.Bot):
        self.bot    = bot
        self.db     = DatabaseManager(db_path=STAFF_DB_PATH)
        self.config = StaffAnalyticsConfig()

        self._staff_cache: Dict[int, tuple] = {}
        self.metrics = MetricsTracker(staff_id_set_getter=self._get_cached_staff_ids)
        self.last_message_cache: Dict[int, Dict[int, datetime]] = defaultdict(dict)
        self.active_vc_session_ids: Dict[tuple, int] = {}

        # Track VC occupancy per channel for people-bonus
        self.vc_channel_members: Dict[int, Set[int]] = defaultdict(set)

        self.initialized = False
        self._last_weekly_report: Optional[datetime] = None
        self._last_known_rank: Dict[int, str] = {}
        self._last_known_streak: Dict[int, int] = {}
        self._dashboard_message_ids: Dict[int, int] = {}
        self._streak_warned_today: Dict[str, Set[int]] = defaultdict(set)
        self._recent_prefix_commands: Dict[Tuple[int, int, str], datetime] = {}
        # Ping-to-action tracking: channel_id -> {role_id: (pinger_id, timestamp)}
        self._pending_staff_pings: Dict[int, Dict[int, tuple]] = defaultdict(dict)
        # Rank-up guard: in-memory set of "staff_id:from:to:date" already DM'd this session.
        # DB has the durable guard (has_notified_rankup); this avoids redundant DB hits.
        self._rankup_sent_session: Set[str] = set()

    async def cog_load(self):
        await self.db.initialize()
        self.initialized = True
        self.bot.analytics_db = self.db
        logger.info("StaffAnalytics cog loaded")
        asyncio.create_task(self._weekly_report_loop())
        asyncio.create_task(self._inactivity_check_loop())
        asyncio.create_task(self._metrics_cleanup_loop())
        asyncio.create_task(self._notification_loop())
        asyncio.create_task(self._dashboard_loop())
        # Pre-seed rank cache from DB so we don't DM everyone on first loop
        asyncio.create_task(self._seed_rank_cache())

    async def _seed_rank_cache(self):
        """Load current ranks from DB at startup so the notification loop
        doesn't treat every staff member as a rank-up on first tick."""
        await asyncio.sleep(30)  # let everything settle
        for guild in self.bot.guilds:
            staff = [m for m in guild.members if self.is_staff(m)]
            if not staff:
                continue
            weights     = await self.db.get_score_weights(guild.id)
            cached_rows = await self.db.get_cached_staff_stats(
                guild.id, [m.id for m in staff], 30, weights
            )
            for sid, s in cached_rows:
                try:
                    self._last_known_rank[sid]   = s.rank_tier.value
                    self._last_known_streak[sid] = (
                        s.consistency.current_streak if s.consistency else 0
                    )
                except Exception:
                    pass
        logger.info("Rank cache seeded for %d staff members", len(self._last_known_rank))

    async def cog_unload(self):
        await self.db.close()
        logger.info("StaffAnalytics cog unloaded")

    # ─── Staff role check ─────────────────────────────────────────────────────

    def is_staff(self, member: discord.Member) -> bool:
        if member.bot:
            return False
        now = discord.utils.utcnow()
        cached = self._staff_cache.get(member.id)
        if cached:
            result, cached_at = cached
            if (now - cached_at).total_seconds() < _STAFF_CACHE_TTL:
                return result
        staff_roles = self.config.staff_roles
        result = any(r.id in staff_roles for r in member.roles)
        self._staff_cache[member.id] = (result, now)
        return result

    def _get_cached_staff_ids(self) -> Set[int]:
        now = discord.utils.utcnow()
        return {
            uid for uid, (is_s, ts) in self._staff_cache.items()
            if is_s and (now - ts).total_seconds() < _STAFF_CACHE_TTL
        }

    def _member_age_days(self, member: discord.Member) -> float:
        if not member.joined_at:
            return 999.0
        return (discord.utils.utcnow() - member.joined_at).total_seconds() / 86400

    # ─── Event listeners ──────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.roles != after.roles:
            self._staff_cache.pop(after.id, None)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild:
            return

        # ── Bot messages ─────────────────────────────────────────────────────────
        if message.author.bot:
            await self._try_parse_mod_bot_embed(message)
            await self._try_track_disboard_bump(message)
            return

        is_staff_member = self.is_staff(message.author)
        now = discord.utils.utcnow()

        # ── Non-staff messages ─────────────────────────────────────────────────
        if not is_staff_member:
            content = message.content or ""
            if self._is_question_message(content):
                self.metrics.record_question(message.channel.id, message.author.id, now)

            # Confirm a pending dead-chat revival
            revival = self.metrics.confirm_revival(message.channel.id, message.author.id, now)
            if revival:
                await self.db.insert_engagement_event(revival)

            # Track role pings for ping-to-action latency
            if message.role_mentions:
                for role in message.role_mentions:
                    role_lower = role.name.lower()
                    if any(kw in role_lower for kw in STAFF_ROLE_PING_KEYWORDS):
                        self._pending_staff_pings[message.channel.id][role.id] = (
                            message.author.id, now
                        )

            # Track prefix moderation commands from non-staff? Usually staff issue these
            # But if non-staff users are using mod commands, they might be testing or abusing
            # We'll still track them but with a flag
            if message.content.startswith(('!', 'w!', '.', '-')):
                await self._try_parse_prefix_command(message, is_staff=False)

            self.metrics.record_any_message(message.channel.id, now)
            return

        # ── Staff messages ─────────────────────────────────────────────────────
        word_count = self.metrics.count_words(message.content)
        staff_msg = StaffMessage(
            staff_id=message.author.id,
            channel_id=message.channel.id,
            message_id=message.id,
            timestamp=now,
            content=message.content,
            word_count=word_count,
        )

        await self.db.insert_staff_message(staff_msg)
        await self.db.update_consistency_stats(message.author.id, now)
        self.metrics.record_message_time(message.author.id, now)
        self.last_message_cache[message.channel.id][message.author.id] = now

        # Check if this is a prefix moderation command from a staff member
        # Track it immediately - don't wait for bot embed
        if message.content.startswith(('!', 'w!', '.', '-')):
            await self._try_parse_prefix_command(message, is_staff=True, staff_msg=staff_msg)

        # Response time
        rt = self.metrics.check_response_time(staff_msg, message.channel.id)
        if rt:
            await self.db.insert_response_time(rt)

        # First response
        has_pending = bool(self.metrics.pending_questions.get(message.channel.id))
        fr = self.metrics.check_first_response(staff_msg, message.channel.id, has_pending)
        if fr:
            await self.db.insert_first_response(fr)

        # Ping-to-action latency: check if staff is responding to a @staff ping
        await self._check_ping_response(message, staff_msg, now)

        # Message quality scoring
        has_attachment = bool(message.attachments)
        quality_event = self.metrics.score_message_quality(staff_msg, has_attachment)
        if quality_event:
            await self.db.insert_quality_event(quality_event)

        # Interactions (reply/mention to non-staff)
        await self._process_interactions(message, staff_msg)

        # Role pings (staff pinging a role)
        if message.role_mentions:
            counts = {role.id: len(role.members) for role in message.role_mentions}
            for ping in self.metrics.check_role_ping(
                staff_msg, [{"id": r.id} for r in message.role_mentions], counts
            ):
                await self.db.insert_role_ping(ping)

        # Dead-chat revival detection
        self.metrics.check_dead_chat_revival(staff_msg, now)
        self.metrics.record_any_message(message.channel.id, now)

        # Conversation starter in general channels
        if not message.reference and self.config.is_general_channel(message.channel.id):
            starter = self.metrics.check_conversation_starter(staff_msg, now)
            if starter:
                await self.db.insert_engagement_event(starter)

        # New member greeting (reply to new member)
        if message.reference and message.reference.resolved:
            resolved = message.reference.resolved
            if isinstance(resolved, discord.Message) and not resolved.author.bot:
                target = message.guild.get_member(resolved.author.id)
                if target and self._member_age_days(target) <= NEW_MEMBER_THRESHOLD_DAYS:
                    greeting = self.metrics.check_new_member_greeting(
                        staff_msg, target.id, now
                    )
                    if greeting:
                        await self.db.insert_engagement_event(greeting)

    async def _check_ping_response(
        self, message: discord.Message, staff_msg: StaffMessage, now: datetime
    ):
        """Credit staff who quickly responds to a pending @staff/@mod ping in the channel."""
        pending = self._pending_staff_pings.get(message.channel.id, {})
        if not pending:
            return
        answered = []
        for role_id, (pinger_id, ping_time) in list(pending.items()):
            elapsed = (now - ping_time).total_seconds()
            if elapsed <= PING_RESPONSE_WINDOW:
                event = EngagementEvent(
                    staff_id=staff_msg.staff_id,
                    channel_id=staff_msg.channel_id,
                    message_id=staff_msg.message_id,
                    event_type="ping_response",
                    user_id=pinger_id,
                    timestamp=now,
                )
                await self.db.insert_engagement_event(event)
                answered.append(role_id)
            else:
                answered.append(role_id)  # expired
        for role_id in answered:
            pending.pop(role_id, None)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ):
        if member.bot:
            return

        afk_channel_id = member.guild.afk_channel.id if member.guild.afk_channel else None

        def _is_countable(channel) -> bool:
            if channel is None: return False
            if afk_channel_id and channel.id == afk_channel_id: return False
            return True

        now = discord.utils.utcnow()
        was_countable = _is_countable(before.channel)
        is_countable  = _is_countable(after.channel)

        joined        = not was_countable and is_countable
        left          = was_countable and not is_countable
        moved         = was_countable and is_countable and before.channel != after.channel
        moved_to_afk  = (_is_countable(before.channel) and not _is_countable(after.channel)
                         and after.channel is not None)

        # Maintain occupancy tracking
        if before.channel and _is_countable(before.channel):
            self.vc_channel_members[before.channel.id].discard(member.id)
        if after.channel and _is_countable(after.channel):
            self.vc_channel_members[after.channel.id].add(member.id)

        if not self.is_staff(member):
            return

        # Count non-bot people currently in the channel (for people-bonus)
        def _occupancy(channel) -> int:
            if channel is None: return 0
            return max(0, len([m for m in channel.members if not m.bot]) - 1)

        if joined:
            occupancy = _occupancy(after.channel)
            session = self.metrics.start_vc_session(member.id, after.channel.id, now)
            session.peak_occupancy = max(1, occupancy)
            session_id = await self.db.start_vc_session(session)
            self.active_vc_session_ids[(member.id, after.channel.id)] = session_id

        elif left or moved_to_afk:
            key = (member.id, before.channel.id)
            if key in self.active_vc_session_ids:
                # People-bonus only credited if session was long enough
                join_time = self.metrics.active_vc_sessions.get(
                    (member.id, before.channel.id), None
                )
                duration_mins = (now - join_time.join_time).total_seconds() / 60 if join_time else 0
                from .constants import VC_MIN_DURATION_FOR_PEOPLE_BONUS
                occ = _occupancy(before.channel) if duration_mins >= VC_MIN_DURATION_FOR_PEOPLE_BONUS else 0
                await self.db.end_vc_session(
                    self.active_vc_session_ids.pop(key), now,
                    peak_occupancy=max(1, occ + 1)
                )

        elif moved:
            prev_key = (member.id, before.channel.id)
            if prev_key in self.active_vc_session_ids:
                join_time = self.metrics.active_vc_sessions.get(
                    (member.id, before.channel.id), None
                )
                duration_mins = (now - join_time.join_time).total_seconds() / 60 if join_time else 0
                from .constants import VC_MIN_DURATION_FOR_PEOPLE_BONUS
                occ = _occupancy(before.channel) if duration_mins >= VC_MIN_DURATION_FOR_PEOPLE_BONUS else 0
                await self.db.end_vc_session(
                    self.active_vc_session_ids.pop(prev_key), now,
                    peak_occupancy=max(1, occ + 1)
                )
            occupancy = _occupancy(after.channel)
            session = self.metrics.start_vc_session(member.id, after.channel.id, now)
            session.peak_occupancy = max(1, occupancy)
            session_id = await self.db.start_vc_session(session)
            self.active_vc_session_ids[(member.id, after.channel.id)] = session_id

    @commands.Cog.listener()
    async def on_app_command_completion(
        self, interaction: discord.Interaction, command: app_commands.Command
    ):
        if not interaction.guild or not self.is_staff(interaction.user):
            return
        cmd = command.name.lower()

        if cmd in MODERATION_COMMANDS:
            target_id = None
            reason    = None
            for option in interaction.data.get("options", []):
                if option.get("type") == 6:
                    target_id = int(option["value"])
                if option.get("name") == "reason":
                    reason = option.get("value")
            if target_id:
                await self._queue_mod_for_review(
                    guild=interaction.guild,
                    moderator_id=interaction.user.id,
                    target_id=target_id,
                    action_type=cmd,
                    reason=reason,
                    channel_id=interaction.channel_id or 0,
                    message_id=interaction.id,
                    timestamp=discord.utils.utcnow(),
                )

    async def _try_parse_mod_bot_embed(self, message: discord.Message) -> None:
        """
        Parse moderation embeds from Wick and similar bots.
        Fixed to properly detect Warns, Bans, Kicks, Mutes, Timeouts.
        """
        if not message.embeds:
            return

        # Map of action names
        ACTION_MAP = {
            "banned": "ban", "ban": "ban", "kicked": "kick", "kick": "kick",
            "warned": "warn", "warn": "warn", "warning": "warn",
            "muted": "mute", "mute": "mute", "timed out": "timeout", "timeout": "timeout",
            "unmuted": "unmute", "unmute": "unmute", "unbanned": "unban", "unban": "unban",
            "strike": "strike", "softban": "softban", "purged": "purge", "purge": "purge",
            "cleared": "purge", "locked": "lock", "lock": "lock",
            "unlocked": "unlock", "unlock": "unlock", "slowmode": "slowmode",
        }

        for embed in message.embeds:
            # Build raw text from all embed components
            all_text_parts = []
            if embed.title:
                all_text_parts.append(embed.title)
            if embed.description:
                all_text_parts.append(embed.description)
            for field in embed.fields:
                all_text_parts.append(field.name)
                all_text_parts.append(field.value)
            raw_text = " ".join(all_text_parts)
            embed_lower = raw_text.lower()

            # Check if this is a moderation result embed
            has_mod = "moderator" in embed_lower or "mod" in embed_lower
            has_result = "result:" in embed_lower or "success" in embed_lower
            
            # Wick-specific: check title for "Warn result:" etc.
            wick_match = WICK_RESULT_RE.search(embed_lower) if embed.title else None
            
            if not (has_mod and has_result) and not wick_match:
                continue

            # Detect command type
            cmd = None
            
            # Method 1: Check title for Wick-style "Warn result:"
            if embed.title and embed.title.lower() == "warn result:":
                cmd = "warn"
            elif embed.title and embed.title.lower() == "ban result:":
                cmd = "ban"
            elif embed.title and embed.title.lower() == "kick result:":
                cmd = "kick"
            elif embed.title and embed.title.lower() == "mute result:":
                cmd = "mute"
            elif embed.title and embed.title.lower() == "timeout result:":
                cmd = "timeout"
            
            # Method 2: Check description for "Successful X"
            if not cmd and embed.description:
                desc_lower = embed.description.lower()
                if "successful ban" in desc_lower:
                    cmd = "ban"
                elif "successful kick" in desc_lower:
                    cmd = "kick"
                elif "successful warn" in desc_lower:
                    cmd = "warn"
                elif "successful mute" in desc_lower:
                    cmd = "mute"
                elif "successful timeout" in desc_lower:
                    cmd = "timeout"
            
            # Method 3: Check field names
            if not cmd:
                for field in embed.fields:
                    field_lower = field.name.lower()
                    if "warned" in field_lower:
                        cmd = "warn"
                    elif "banned" in field_lower:
                        cmd = "ban"
                    elif "kicked" in field_lower:
                        cmd = "kick"
                    elif "muted" in field_lower:
                        cmd = "mute"
                    elif "timed out" in field_lower:
                        cmd = "timeout"
                    if cmd:
                        break
            
            if not cmd:
                continue

            # Extract target ID
            target_id = None
            
            # Wick format: "Warned: Name [ID]" or "Banned: @User [ID]"
            bracket_match = re.search(r"\[(\d{17,20})\]", raw_text)
            if bracket_match:
                target_id = int(bracket_match.group(1))
            
            # Discord mention
            if not target_id:
                mention_match = re.search(r"<@!?(\d{17,20})>", raw_text)
                if mention_match:
                    target_id = int(mention_match.group(1))
            
            # Plain ID in text
            if not target_id:
                id_match = re.search(r"\b(\d{17,20})\b", raw_text)
                if id_match:
                    target_id = int(id_match.group(1))
            
            if not target_id:
                continue

            # Extract reason
            reason = None
            reason_match = re.search(r"[Rr]eason[:\s]+(.+?)(?:\n|$|\[)", raw_text)
            if reason_match:
                reason = reason_match.group(1).strip()
                # Remove emoji IDs and clean up
                reason = re.sub(r'<:[a-zA-Z_]+:\d+>', '', reason)
                reason = re.sub(r':[a-zA-Z_]+:', '', reason).strip()
                reason = re.sub(r'\*\*', '', reason)
                if reason.lower() in ["no reason.", "none", "no reason", "", "(none)"]:
                    reason = None

            # Extract moderator ID
            moderator_id = None
            
            # Wick puts moderator in fields or description
            mod_match = re.search(r"[Mm]oderator[:\s]+<@!?(\d{17,20})>", raw_text)
            if mod_match:
                moderator_id = int(mod_match.group(1))
            
            # Fallback: search channel history for the command
            if not moderator_id:
                try:
                    async for prev in message.channel.history(limit=15, before=message):
                        if prev.author.bot:
                            continue
                        if not self.is_staff(prev.author):
                            continue
                        # Check if this message contains the command
                        cmd_in_content = any(
                            re.search(rf"\b{action}\b", prev.content.lower())
                            for action in MODERATION_COMMANDS
                        )
                        mentions_target = (
                            str(target_id) in prev.content
                            or f"<@{target_id}>" in prev.content
                            or f"<@!{target_id}>" in prev.content
                        )
                        if mentions_target and cmd_in_content:
                            moderator_id = prev.author.id
                            # Also try to extract reason from command message
                            if not reason:
                                reason_match = re.search(
                                    rf"(?:<@!?{target_id}>|{target_id})\s+(.+)",
                                    prev.content
                                )
                                if reason_match:
                                    reason = reason_match.group(1).strip()[:200] or None
                            break
                except (discord.Forbidden, discord.HTTPException):
                    pass

            if not moderator_id:
                logger.debug("Mod action embed: could not find moderator for %s on %s", cmd, target_id)
                continue

            # Queue for review instead of inserting directly
            action = self.metrics.track_moderation(
                moderator_id=moderator_id,
                target_id=target_id,
                command=cmd,
                timestamp=message.created_at.replace(tzinfo=None),
                reason=reason,
            )
            if action:
                await self._queue_mod_for_review(
                    guild=message.guild,
                    moderator_id=moderator_id,
                    target_id=target_id,
                    action_type=cmd,
                    reason=reason,
                    channel_id=message.channel.id,
                    message_id=message.id,
                    timestamp=message.created_at.replace(tzinfo=None),
                )
                logger.info("Queued %s action for review: mod=%s target=%s", cmd, moderator_id, target_id)
            break

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.Member):
        if not hasattr(user, "guild") or user.bot:
            return
        if not self.is_staff(user):
            return
        if reaction.message.author.bot or reaction.message.author.id == user.id:
            return

        event = self.metrics.track_reaction(
            staff_id           = user.id,
            message_author_id  = reaction.message.author.id,
            channel_id         = reaction.message.channel.id,
            message_id         = reaction.message.id,
            emoji              = str(reaction.emoji),
            timestamp          = discord.utils.utcnow(),
        )
        if event:
            await self.db.insert_reaction(event)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if not payload.guild_id or payload.member is None or payload.member.bot:
            return
        channel = self.bot.get_channel(payload.channel_id)
        if not channel:
            return
        try:
            msg = await channel.fetch_message(payload.message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return
        if msg.author.bot or not self.is_staff(msg.author):
            return
        now = discord.utils.utcnow()
        rr = self.metrics.track_reaction_received(
            staff_message_id = payload.message_id,
            staff_id         = msg.author.id,
            reactor_id       = payload.user_id,
            channel_id       = payload.channel_id,
            emoji            = str(payload.emoji),
            now              = now,
        )
        if rr:
            await self.db.insert_reaction_received(rr)

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread):
        if not thread.owner or thread.owner.bot:
            return
        member = thread.guild.get_member(thread.owner_id)
        if not member or not self.is_staff(member):
            return
        event = self.metrics.track_thread(
            staff_id=thread.owner_id, thread_id=thread.id,
            channel_id=thread.parent_id or 0, event_type="created",
            timestamp=discord.utils.utcnow(),
        )
        if event:
            await self.db.insert_thread_event(event)

    # ─── Internal helpers ─────────────────────────────────────────────────────

    async def _process_interactions(self, message: discord.Message, staff_msg: StaffMessage):
        referenced = None
        if message.reference and message.reference.resolved:
            if isinstance(message.reference.resolved, discord.Message):
                referenced = {
                    "author":  message.reference.resolved.author.id,
                    "content": message.reference.resolved.content,
                }
        mentions = [u.id for u in message.mentions if u.id != message.author.id]
        for interaction in self.metrics.check_interaction(staff_msg, referenced, mentions):
            await self.db.insert_interaction(interaction)
            # New member chat bonus
            target = message.guild.get_member(interaction.user_id)
            if target and self._member_age_days(target) <= NEW_MEMBER_THRESHOLD_DAYS:
                ev = EngagementEvent(
                    staff_id=staff_msg.staff_id, channel_id=staff_msg.channel_id,
                    message_id=staff_msg.message_id, event_type="new_member_chat",
                    user_id=interaction.user_id, timestamp=discord.utils.utcnow(),
                )
                await self.db.insert_engagement_event(ev)

    async def _fetch_all_staff(self, guild: discord.Guild) -> List[discord.Member]:
        return [m for m in guild.members if self.is_staff(m)]

    def _next_tier(self, score: float):
        order = ["Bronze", "Silver", "Gold", "Platinum", "Diamond"]
        current = RankTier.from_score(score).value
        idx = order.index(current)
        if idx + 1 < len(order):
            name = order[idx + 1]
            return name, RANK_TIERS[name]["min"]
        return None, None

    async def _try_track_disboard_bump(self, message: discord.Message) -> None:
        """Credit the staff member who triggered a successful Disboard bump and announce it."""
        if message.author.id != DISBOARD_BOT_ID:
            return

        is_bump = False
        for embed in message.embeds:
            text = " ".join(filter(None, [embed.title, embed.description])).lower()
            if "bump done" in text:
                is_bump = True
                break

        if not is_bump:
            return

        bumper = None
        if message.interaction_metadata:
            bumper = message.interaction_metadata.user

        if bumper is None and message.reference:
            try:
                ref = await message.channel.fetch_message(message.reference.message_id)
                if ref.interaction_metadata:
                    bumper = ref.interaction_metadata.user
            except discord.HTTPException:
                pass

        if bumper is None:
            logger.warning("BumpTracker: bump detected but could not identify bumper.")
            return

        await self.db.insert_bump(Bump(
            staff_id=bumper.id,
            timestamp=message.created_at,
            bump_type="disboard",
        ))

        # ── Confirmation message ───────────────────────────────────────────────
        try:
            confirm = await message.channel.send(
                f"✅ Bump tracked for {bumper.mention} — points added! 🚀",
                delete_after=15,
            )
        except (discord.Forbidden, discord.HTTPException):
            pass

    def _is_question_message(self, content: str) -> bool:
        if len(content) < 5:
            return False
        lower = content.lower()
        has_kw = any(kw in lower for kw in QUESTION_KEYWORDS)
        has_q  = bool(QUESTION_RE.search(content))
        return has_kw or has_q

    async def _queue_mod_for_review(
        self,
        guild: discord.Guild,
        moderator_id: int,
        target_id: int,
        action_type: str,
        reason: Optional[str],
        channel_id: int,
        message_id: int,
        timestamp,
    ) -> None:
        """Post a detected mod action to the mod_log channel for senior staff approval.

        Until a senior mod clicks ✅ Approve the action is NOT credited to the moderator's
        score, preventing gaming and ensuring quality control.
        """
        from .constants import MODERATION_SEVERITY
        severity = MODERATION_SEVERITY.get(action_type, 1.0)

        pending_id = await self.db.insert_pending_mod_action(
            moderator_id=moderator_id,
            target_id=target_id,
            action_type=action_type,
            reason=reason,
            severity=severity,
            guild_id=guild.id,
            channel_id=channel_id,
            message_id=message_id,
        )

        review_channel_id = self.config.get_channel_id("mod_log")
        if not review_channel_id:
            # No review channel configured — auto-approve immediately
            row = await self.db.approve_pending_mod_action(pending_id)
            if row:
                from .constants import MODERATION_SEVERITY as _MS
                action = self.metrics.track_moderation(
                    moderator_id=moderator_id, target_id=target_id,
                    command=action_type, timestamp=timestamp, reason=reason,
                )
                if action:
                    await self.db.insert_moderation_action(action)
            return

        review_channel = guild.get_channel(review_channel_id)
        if not review_channel:
            return

        moderator = await get_member_safe(guild, moderator_id)
        mod_name  = moderator.mention if moderator else f"<@{moderator_id}>"
        target    = await get_member_safe(guild, target_id)
        tgt_name  = target.mention if target else f"<@{target_id}>"

        action_emojis = {
            "ban": "🔨", "kick": "👢", "warn": "⚠️", "mute": "🔇",
            "timeout": "⏱️", "softban": "🧲", "strike": "📛",
            "purge": "🗑️", "lock": "🔒", "unlock": "🔓", "slowmode": "🐌",
        }
        emoji = action_emojis.get(action_type, "🛡️")

        embed = discord.Embed(
            title=f"{emoji} Mod Action — Pending Review",
            description=(
                f"**Action:** `{action_type}`  ·  Severity **×{severity:.1f}**\n"
                f"**Moderator:** {mod_name}\n"
                f"**Target:** {tgt_name}\n"
                f"**Reason:** {reason or '*No reason given*'}\n"
                f"**Channel:** <#{channel_id}>"
            ),
            color=discord.Color.orange(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text=f"Pending ID #{pending_id}  ·  Senior staff: click below to review")

        view = ModApprovalView(
            cog=self,
            pending_id=pending_id,
            moderator_id=moderator_id,
            action_type=action_type,
            target_id=target_id,
        )

        try:
            msg = await review_channel.send(embed=embed, view=view)
            await self.db.set_pending_mod_review_msg(pending_id, msg.id)
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.warning("Could not post mod review to mod_log: %s", e)

    # ─── /staffstats ─────────────────────────────────────────────────────────

    @app_commands.command(name="staffstats", description="Detailed performance profile for a staff member")
    @app_commands.describe(member="Staff member to inspect", days="Analysis window in days (default 30)")
    async def staffstats(
        self, interaction: discord.Interaction,
        member: discord.Member, days: int = 30,
    ):
        await interaction.response.defer(ephemeral=True)

        if not self.is_staff(member):
            await interaction.followup.send(embed=create_error_embed(
                "Not a Staff Member", f"{member.mention} does not have a staff role."
            ))
            return

        guild_weights = await self.db.get_score_weights(interaction.guild.id)
        stats = await self.db.get_staff_stats(member.id, days, weights=guild_weights)
        consistency = await self.db.get_consistency_stats(member.id)
        if consistency:
            stats.consistency = consistency
        trends = await self.db.get_trend_comparison(member.id)

        all_staff = await self._fetch_all_staff(interaction.guild)
        rank_pos, total_staff = await self.db.get_staff_rank_position(
            member.id, [m.id for m in all_staff], days
        )
        stats.rank_position = rank_pos
        stats.total_staff   = total_staff

        tier      = stats.rank_tier
        tier_info = RANK_TIERS[tier.value]

        rank_line = f"Rank **#{rank_pos}** of **{total_staff}** staff"
        if total_staff > 0:
            percentile = (1 - (rank_pos - 1) / total_staff) * 100
            rank_line += f"  ·  Top **{percentile:.0f}%**"

        embed = discord.Embed(
            title=f"{tier_info['emoji']} {member.display_name}",
            description=(
                f"> **{tier.display}** · {tier_info['label']} · Score: `{stats.engagement_score:.0f} pts`\n"
                f"> {rank_line}"
            ),
            color=tier_info["colour"],
            timestamp=discord.utils.utcnow(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)

        # Rank progress bar
        next_tier_name, next_threshold = self._next_tier(stats.engagement_score)
        if next_tier_name:
            current_min = tier_info["min"]
            progress    = stats.engagement_score - current_min
            needed      = next_threshold - current_min
            bar = create_progress_bar(progress, needed, length=16)
            embed.add_field(
                name=f"▸ Progress to {get_rank_badge(next_tier_name)} {next_tier_name}",
                value=f"`{bar}` **{stats.engagement_score:.0f}** / **{next_threshold}**",
                inline=False,
            )
        else:
            bar = create_progress_bar(1, 1, length=16)
            embed.add_field(name="▸ Max Rank Achieved", value=f"`{bar}` **{stats.engagement_score:.0f} pts** 💎", inline=False)

        # Community field
        msg_trend   = format_trend_arrow(trends["messages"]["current"],   trends["messages"]["previous"])
        rev_trend   = format_trend_arrow(trends["dead_chat_revivals"]["current"], trends["dead_chat_revivals"]["previous"])
        greet_trend = format_trend_arrow(trends["new_member_greetings"]["current"], trends["new_member_greetings"]["previous"])
        comm_lines = (
            f"`Messages         ` **{format_number(stats.total_messages)}**  {msg_trend}\n"
            f"`Unique talked to ` **{stats.unique_talked_with}**  "
            f"{format_kpi_status(stats.unique_talked_with, KPI_TARGETS['unique_talked_with'])}\n"
            f"`Newbies chatted  ` **{stats.unique_newbies_chatted}** 🌱\n"
            f"`Reactions given  ` **{stats.reactions_given}**\n"
            f"`Threads created  ` **{stats.threads_created}**\n"
            f"`Ping responses   ` **{stats.ping_responses}** 📟  "
            f"{format_kpi_status(stats.ping_responses, KPI_TARGETS['ping_responses'])}"
        )
        embed.add_field(name="💬 Community", value=comm_lines, inline=True)

        # Engagement field
        engage_lines = (
            f"`Chat starters 💡 ` **{stats.conversation_starters}**  "
            f"{format_kpi_status(stats.conversation_starters, KPI_TARGETS['conversation_starters'])}\n"
            f"`Greetings 👋     ` **{stats.new_member_greetings}**  {greet_trend}  "
            f"{format_kpi_status(stats.new_member_greetings, KPI_TARGETS['new_member_greetings'])}\n"
            f"`Dead revivals 🔋 ` **{stats.dead_chat_revivals}**  {rev_trend}  "
            f"{format_kpi_status(stats.dead_chat_revivals, KPI_TARGETS['dead_chat_revivals'])}\n"
            f"`Reactions recv ⭐` **{stats.reactions_received}**  "
            f"{format_kpi_status(stats.reactions_received, KPI_TARGETS['reactions_received'])}"
        )
        embed.add_field(name="🌐 Engagement", value=engage_lines, inline=True)

        # Moderation field
        mod_trend = format_trend_arrow(trends["moderation"]["current"], trends["moderation"]["previous"])
        vc_trend  = format_trend_arrow(trends["vc_hours"]["current"], trends["vc_hours"]["previous"])
        # Build compact breakdown: warn:5 mute:2 ban:1
        breakdown = stats.mod_action_breakdown
        if breakdown:
            sev_order = ["ban", "softban", "kick", "timeout", "mute", "warn", "strike", "purge", "clear", "lock", "slowmode", "unban", "unmute", "unlock"]
            bd_parts  = [f"{k}:`{breakdown[k]}`" for k in sev_order if k in breakdown]
            bd_str    = "  ".join(bd_parts[:6]) or "—"
        else:
            bd_str = "—"
        mod_lines = (
            f"`Mod actions      ` **{stats.moderation_actions}**  {mod_trend}\n"
            f"`Breakdown        ` {bd_str}\n"
            f"`Weighted severity` **{stats.moderation_severity_sum:.1f}**\n"
            f"`Reports filed    ` **{stats.reports_filed}**\n"
            f"`VC hours         ` **{stats.vc_hours:.1f}h**  {vc_trend}  "
            f"{format_kpi_status(stats.vc_hours, KPI_TARGETS['vc_hours'])}\n"
            f"`VC people bonus  ` **+{stats.vc_people_bonus:.1f}** pts 👥\n"
            f"`Server bumps     ` **{stats.bumps_performed}**"
        )
        embed.add_field(name="🛡️ Moderation & Presence", value=mod_lines, inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=True)

        # Quality & Speed field
        fr_trend = format_trend_arrow(trends["first_response"]["current"], trends["first_response"]["previous"])
        ql_trend = format_trend_arrow(trends["quality"]["current"], trends["quality"]["previous"])
        quality_lines = (
            f"`Avg response     ` **{stats.response_rate_display}**\n"
            f"`Fast responses   ` **{stats.fast_responses}**  "
            f"{format_kpi_status(stats.fast_responses, KPI_TARGETS['fast_responses'])}\n"
            f"`First to respond ` **{stats.first_responses}**  {fr_trend}  "
            f"{format_kpi_status(stats.first_responses, KPI_TARGETS['first_responses'])}\n"
            f"`Quality messages ` **{stats.quality_events}**  {ql_trend}\n"
            f"`Quality score    ` **{stats.quality_score_display}**\n"
            f"`Avg msg length   ` **{stats.avg_message_words:.1f} words**"
        )
        embed.add_field(name="⚡ Quality & Speed", value=quality_lines, inline=True)

        # Consistency field
        if consistency:
            most_active = f"{int(consistency.most_active_hour):02d}:00" if consistency.most_active_hour is not None else "N/A"
            busiest_day = consistency.busiest_day_name or "N/A"
            weekly_pct  = consistency.weekly_consistency_score
            cons_lines = (
                f"`Current streak   ` **{consistency.current_streak}d** 🔥\n"
                f"`Longest streak   ` **{consistency.longest_streak}d**\n"
                f"`Active days      ` **{consistency.active_days}** / {days}  "
                f"{format_kpi_status(stats.active_days, KPI_TARGETS['active_days'])}\n"
                f"`Weekly consist.  ` **{weekly_pct:.0f}%** (28d)\n"
                f"`Hour coverage    ` **{consistency.active_hours_count}**/24 hrs\n"
                f"`Off-peak msgs    ` **{consistency.off_peak_activity}** 🦉\n"
                f"`Peak hour        ` **{most_active}**\n"
                f"`Busiest day      ` **{busiest_day}**"
            )
            embed.add_field(name="🔄 Consistency", value=cons_lines, inline=True)
            embed.add_field(name="\u200b", value="\u200b", inline=True)

            heatmap = create_activity_heatmap_compact(dict(consistency.active_hours))
            embed.add_field(
                name="📊 Activity Heatmap  (00–11 AM / 12–23 PM)",
                value=f"```\n{heatmap}\n```",
                inline=False,
            )

        # Achievements with decay indicators
        achievements = stats.earned_achievements
        now_dt = discord.utils.utcnow()
        if achievements:
            ach_with_decay = await self._format_achievements_with_decay(
                member.id, achievements, now_dt
            )
            embed.add_field(
                name=f"🏅 Achievements  ({len(achievements)})",
                value=ach_with_decay[:1024],
                inline=False,
            )

        embed.set_footer(text=(
            f"Last {days} days  ·  🟢 ≥80% KPI  🟡 ≥50%  🟠 ≥30%  🔴 <30%  "
            f"·  Trends vs prev {days//7 or 1}w  ·  ✨ = fresh achievement  💤 = decayed"
        ))
        await interaction.followup.send(embed=embed)

    async def _format_achievements_with_decay(
        self, staff_id: int, achievements: List[str], now: datetime
    ) -> str:
        """Add decay indicators: ✨ for recently earned, 💤 for old ones."""
        lines = []
        for ach_str in achievements[:8]:
            emoji = ach_str.split(" ")[0]
            # Find matching achievement key
            key = None
            for k, info in ACHIEVEMENT_THRESHOLDS.items():
                if info["emoji"] == emoji:
                    key = k
                    break
            if key:
                notified_at = await self.db.get_achievement_notified_at(staff_id, key)
                decay_days  = ACHIEVEMENT_THRESHOLDS[key].get("decay_days")
                if notified_at and decay_days:
                    age_days = (now - notified_at).days
                    if age_days > decay_days:
                        lines.append(f"💤 ~~{ach_str}~~")
                        continue
                    elif age_days < 7:
                        lines.append(f"✨ {ach_str}")
                        continue
            lines.append(ach_str)
        extra = len(achievements) - 8
        result = "  ".join(lines)
        if extra > 0:
            result += f"  (+{extra} more)"
        return result

    # ─── /mystats ─────────────────────────────────────────────────────────────

    @app_commands.command(name="mystats", description="View your own staff performance profile")
    @app_commands.describe(days="Analysis window in days (default 30)")
    async def mystats(self, interaction: discord.Interaction, days: int = 30):
        if not self.is_staff(interaction.user):
            await interaction.response.send_message(
                embed=create_error_embed("Not Staff", "You don't have a staff role."), ephemeral=True
            )
            return
        await self.staffstats.callback(self, interaction, interaction.user, days)

    # ─── /staffleaderboard ───────────────────────────────────────────────────

    @app_commands.command(name="staffleaderboard", description="Staff performance leaderboards")
    @app_commands.describe(metric="Metric to rank by", days="Analysis window (default 30)")
    @app_commands.choices(metric=[
        app_commands.Choice(name="⭐ Engagement Score",        value="score"),
        app_commands.Choice(name="🎙️ VC Hours",                value="vc_hours"),
        app_commands.Choice(name="🛡️ Moderation (weighted)",   value="moderation"),
        app_commands.Choice(name="📋 Reports Filed",           value="reports"),
        app_commands.Choice(name="💬 Total Messages",          value="messages"),
        app_commands.Choice(name="😀 Reactions Given",         value="reactions"),
        app_commands.Choice(name="🧵 Threads Created",         value="threads"),
        app_commands.Choice(name="🚀 First Responses",         value="first_response"),
        app_commands.Choice(name="✨ Quality Messages",        value="quality"),
        app_commands.Choice(name="💡 Conversation Starters",   value="conversation_starters"),
        app_commands.Choice(name="👋 New Member Greetings",    value="new_member_greetings"),
        app_commands.Choice(name="🔋 Dead Chat Revivals",      value="dead_chat_revivals"),
        app_commands.Choice(name="⭐ Reactions Received",      value="reactions_received"),
        app_commands.Choice(name="🫂 Unique People Talked To", value="unique_talked_with"),
        app_commands.Choice(name="📟 Ping Responses",          value="ping_responses"),
        app_commands.Choice(name="🔥 Streak",                  value="streak"),
        app_commands.Choice(name="📅 Weekly Consistency",      value="weekly_consistency"),
        app_commands.Choice(name="🚀 Server Bumps",             value="bumps"),
    ])
    async def staffleaderboard(
        self, interaction: discord.Interaction,
        metric: app_commands.Choice[str], days: int = 30,
    ):
        await interaction.response.defer(ephemeral=True)

        if metric.value in ("score", "streak", "weekly_consistency"):
            staff       = await self._fetch_all_staff(interaction.guild)
            weights     = await self.db.get_score_weights(interaction.guild.id)
            cached_rows = await self.db.get_cached_staff_stats(
                interaction.guild.id, [m.id for m in staff], days, weights
            )
            if metric.value == "score":
                rows = [(sid, s.engagement_score) for sid, s in cached_rows]
            elif metric.value == "streak":
                rows = [(sid, s.consistency.current_streak if s.consistency else 0)
                        for sid, s in cached_rows]
            else:  # weekly_consistency
                rows = [(sid, s.consistency.weekly_consistency_score if s.consistency else 0.0)
                        for sid, s in cached_rows]
            rows.sort(key=lambda x: x[1], reverse=True)
        elif metric.value == "bumps":
            rows = await self.db.get_leaderboard("bumps", days, limit=LEADERBOARD_LIMIT)
        else:
            rows = await self.db.get_leaderboard(metric.value, days, limit=LEADERBOARD_LIMIT)

        if not rows:
            await interaction.followup.send(
                embed=create_info_embed("No Data", f"No data recorded in the last {days} days.")
            )
            return

        metric_labels = {
            "score":                 "⭐ Engagement Score",
            "vc_hours":              "🎙️ VC Hours",
            "moderation":            "🛡️ Weighted Moderation",
            "reports":               "📋 Reports Filed",
            "messages":              "💬 Total Messages",
            "reactions":             "😀 Reactions Given",
            "threads":               "🧵 Threads Created",
            "first_response":        "🚀 First Responses",
            "quality":               "✨ Avg Quality Score",
            "conversation_starters": "💡 Conversation Starters",
            "new_member_greetings":  "👋 New Member Greetings",
            "dead_chat_revivals":    "🔋 Dead Chat Revivals",
            "reactions_received":    "⭐ Reactions Received",
            "unique_talked_with":    "🫂 Unique People Talked To",
            "ping_responses":        "📟 Ping Responses",
            "streak":                "🔥 Current Streak",
            "weekly_consistency":    "📅 Weekly Consistency %",
            "bumps":                 "🚀 Server Bumps",
        }

        embed = discord.Embed(
            title=f"🏆 Leaderboard — {metric_labels.get(metric.value, metric.value)}",
            color=EMBED_COLORS["primary"],
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text=f"Last {days} days")

        top_val = rows[0][1] if rows else 1
        lines = []
        for idx, (staff_id, value) in enumerate(rows[:LEADERBOARD_LIMIT], 1):
            member = await get_member_safe(interaction.guild, staff_id)
            name   = member.display_name if member else f"Unknown ({staff_id})"
            prefix = MEDAL_EMOJIS[idx - 1] if idx <= 3 else f"`{idx:>2}.`"

            if metric.value == "vc_hours":
                val_str = f"**{value:.1f}h**"
            elif metric.value == "quality":
                val_str = f"**{value:.2f}** avg"
            elif metric.value == "score":
                tier    = RankTier.from_score(value)
                val_str = f"{tier.display.split()[0]} **{value:.0f}**"
            elif metric.value == "weekly_consistency":
                val_str = f"**{value:.0f}%**"
            elif metric.value == "streak":
                val_str = f"**{int(value)}d** 🔥"
            else:
                val_str = f"**{format_number(value)}**"

            bar_len = max(1, int((value / top_val) * 8)) if top_val else 0
            bar     = "█" * bar_len + "░" * (8 - bar_len)
            lines.append(f"{prefix} {name}  `{bar}` {val_str}")

        embed.description = "\n".join(lines)
        await interaction.followup.send(embed=embed)

    # ─── /staffcompare ───────────────────────────────────────────────────────

    @app_commands.command(name="staffcompare", description="Compare two staff members side-by-side")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(member1="First staff member", member2="Second staff member", days="Days (default 30)")
    async def staffcompare(
        self, interaction: discord.Interaction,
        member1: discord.Member, member2: discord.Member, days: int = 30,
    ):
        await interaction.response.defer(ephemeral=True)
        for m in (member1, member2):
            if not self.is_staff(m):
                await interaction.followup.send(
                    embed=create_error_embed("Not Staff", f"{m.mention} is not a staff member.")
                )
                return

        weights     = await self.db.get_score_weights(interaction.guild.id)
        cached_rows = await self.db.get_cached_staff_stats(
            interaction.guild.id, [member1.id, member2.id], days, weights
        )
        stats_map = {sid: s for sid, s in cached_rows}
        s1 = stats_map.get(member1.id) or await self.db.get_staff_stats(member1.id, days, weights)
        s2 = stats_map.get(member2.id) or await self.db.get_staff_stats(member2.id, days, weights)

        streak1 = s1.consistency.current_streak if s1.consistency else 0
        streak2 = s2.consistency.current_streak if s2.consistency else 0
        wcons1  = s1.consistency.weekly_consistency_score if s1.consistency else 0.0
        wcons2  = s2.consistency.weekly_consistency_score if s2.consistency else 0.0

        rows = [
            ("⭐ Score",            s1.engagement_score,       s2.engagement_score),
            ("💬 Messages",         s1.total_messages,          s2.total_messages),
            ("🫂 Unique talked to", s1.unique_talked_with,      s2.unique_talked_with),
            ("🌱 Newbies chatted",  s1.unique_newbies_chatted,  s2.unique_newbies_chatted),
            ("🛡️ Mod actions",      s1.moderation_actions,      s2.moderation_actions),
            ("⚖️ Mod severity",     s1.moderation_severity_sum, s2.moderation_severity_sum),
            ("🎙️ VC hours",         s1.vc_hours,                s2.vc_hours),
            ("👥 VC people bonus",  s1.vc_people_bonus,         s2.vc_people_bonus),
            ("📋 Reports",          s1.reports_filed,           s2.reports_filed),
            ("😀 Reactions",        s1.reactions_given,         s2.reactions_given),
            ("🧵 Threads",          s1.threads_created,         s2.threads_created),
            ("🚀 First resp.",      s1.first_responses,         s2.first_responses),
            ("📟 Ping resp.",       s1.ping_responses,          s2.ping_responses),
            ("✨ Quality msgs",     s1.quality_events,          s2.quality_events),
            ("🔥 Streak",           streak1,                    streak2),
            ("📅 Weekly %",         wcons1,                     wcons2),
        ]

        wins1 = sum(1 for _, v1, v2 in rows if v1 > v2)
        wins2 = sum(1 for _, v1, v2 in rows if v2 > v1)

        embed = discord.Embed(
            title=f"⚖️ {member1.display_name}  vs  {member2.display_name}",
            description=(
                f"**{member1.display_name}** wins **{wins1}** categories  ·  "
                f"**{member2.display_name}** wins **{wins2}** categories"
            ),
            color=EMBED_COLORS["info"],
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text=f"Last {days} days")

        col_label, col1, col2 = [], [], []
        for label, v1, v2 in rows:
            lbl, c1, c2 = build_comparison_table(label, v1, v2)
            col_label.append(lbl)
            col1.append(c1)
            col2.append(c2)

        embed.add_field(name="Metric",                  value="\n".join(col_label), inline=True)
        embed.add_field(name=member1.display_name[:20], value="\n".join(col1),      inline=True)
        embed.add_field(name=member2.display_name[:20], value="\n".join(col2),      inline=True)
        await interaction.followup.send(embed=embed)

    # ─── /staffranks ─────────────────────────────────────────────────────────

    @app_commands.command(name="staffranks", description="Staff rank tier distribution")
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.describe(days="Analysis window (default 30)")
    async def staffranks(self, interaction: discord.Interaction, days: int = 30):
        await interaction.response.defer(ephemeral=True)
        staff = await self._fetch_all_staff(interaction.guild)
        if not staff:
            await interaction.followup.send(embed=create_info_embed("No Staff", "No staff members found."))
            return

        try:
            weights     = await self.db.get_score_weights(interaction.guild.id)
            cached_rows = await self.db.get_cached_staff_stats(
                interaction.guild.id, [m.id for m in staff], days, weights
            )
            stats_map = {sid: s for sid, s in cached_rows}
            buckets: Dict[str, List[str]] = {name: [] for name in RANK_TIERS}
            scores_map: Dict[str, float] = {}
            for member in staff:
                s = stats_map.get(member.id)
                if not s:
                    continue
                buckets[s.rank_tier.value].append(member.display_name)
                scores_map[member.display_name] = s.engagement_score

            total = len(staff)
            embed = discord.Embed(title="🏅 Staff Rank Distribution", color=EMBED_COLORS["primary"], timestamp=discord.utils.utcnow())
            embed.set_footer(text=f"Last {days} days  ·  {total} staff total")

            for tier_name in ("Diamond", "Platinum", "Gold", "Silver", "Bronze"):
                info  = RANK_TIERS[tier_name]
                names = buckets[tier_name]
                if not names:
                    continue
                count      = len(names)
                bar_filled = max(1, int((count / total) * 12)) if total else 0
                bar        = "█" * bar_filled + "░" * (12 - bar_filled)
                sorted_names = sorted(names, key=lambda n: scores_map.get(n, 0), reverse=True)
                name_list    = ", ".join(sorted_names[:12]) + (f" +{count - 12}" if count > 12 else "")
                embed.add_field(
                    name=f"{info['emoji']} {tier_name}  ·  {count} staff  `{bar}`",
                    value=f"≥ **{info['min']} pts** · {name_list}",
                    inline=False,
                )
            await interaction.followup.send(embed=embed)
        except Exception as e:
            logger.exception("staffranks command failed: %s", e)
            try:
                await interaction.followup.send(embed=create_error_embed(
                    "Command Failed", "An unexpected error occurred. Please try again."
                ), ephemeral=True)
            except Exception:
                pass

    # ─── /staffactivity ──────────────────────────────────────────────────────

    @app_commands.command(name="staffactivity", description="Recent staff activity feed")
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.describe(hours="Hours to look back (default 24)")
    async def staffactivity(self, interaction: discord.Interaction, hours: int = 24):
        await interaction.response.defer(ephemeral=True)
        staff = await self._fetch_all_staff(interaction.guild)
        if not staff:
            await interaction.followup.send(embed=create_info_embed("No Staff", "No staff members found."))
            return

        activity = await self.db.get_recent_activity([m.id for m in staff], hours)
        embed = discord.Embed(
            title=f"📋 Staff Activity — Last {hours}h",
            color=EMBED_COLORS["info"],
            timestamp=discord.utils.utcnow(),
        )

        type_icons = {
            "message":        "💬",
            "moderation":     "🛡️",
            "voice":          "🎙️",
            "first_response": "🚀",
            "ping_response":  "📟",
            "bump":           "🚀",
        }

        shown = 0
        for member in sorted(staff, key=lambda m: m.display_name):
            acts = activity.get(member.id, [])[:4]
            if not acts:
                continue
            lines = []
            for act in acts:
                try:
                    ts = datetime.fromisoformat(act["timestamp"]).strftime("%H:%M")
                except Exception:
                    ts = "??"
                icon = type_icons.get(act["type"], "•")
                if act["type"] == "message":
                    lines.append(f"{icon} `{ts}` <#{act['channel_id']}>")
                elif act["type"] == "moderation":
                    sev = act.get("severity", 1.0)
                    lines.append(f"{icon} `{ts}` **{act.get('details','mod')}** (×{sev:.1f})")
                elif act["type"] == "voice":
                    lines.append(f"{icon} `{ts}` <#{act['channel_id']}>")
                elif act["type"] == "first_response":
                    lines.append(f"{icon} `{ts}` First responder")
                elif act["type"] == "ping_response":
                    lines.append(f"{icon} `{ts}` Answered @staff ping")
            if lines:
                embed.add_field(name=member.display_name, value="\n".join(lines), inline=True)
                shown += 1
            if shown >= 12:
                break

        if shown == 0:
            embed.description = "No activity recorded in this period."
        await interaction.followup.send(embed=embed)

    # ─── /staffinactive ──────────────────────────────────────────────────────

    @app_commands.command(name="staffinactive", description="Show inactive staff members")
    @app_commands.default_permissions(ban_members=True)
    @app_commands.describe(days="Days of inactivity threshold (default 7)")
    async def staffinactive(self, interaction: discord.Interaction, days: int = 7):
        await interaction.response.defer(ephemeral=True)
        staff = await self._fetch_all_staff(interaction.guild)
        if not staff:
            await interaction.followup.send(embed=create_info_embed("No Staff", "No staff members found."))
            return

        try:
            inactive_ids = await self.db.get_inactive_staff([m.id for m in staff], days)
            if not inactive_ids:
                await interaction.followup.send(embed=create_success_embed(
                    "All Staff Active", f"All staff have been active within the last **{days}** days. ✅"
                ))
                return

            inactive = [m for m in staff if m.id in inactive_ids]
            embed = create_warning_embed(
                f"Inactive Staff — {len(inactive)} member{'s' if len(inactive) != 1 else ''}",
                f"No activity recorded in the last **{days}** days:\n"
                + "\n".join(f"• {m.mention}" for m in inactive[:20]),
            )
            if len(inactive) > 20:
                embed.set_footer(text=f"And {len(inactive) - 20} more not shown")
            await interaction.followup.send(embed=embed)
        except Exception as e:
            logger.exception("staffinactive command failed: %s", e)
            try:
                await interaction.followup.send(embed=create_error_embed(
                    "Command Failed", "An unexpected error occurred. Please try again."
                ), ephemeral=True)
            except Exception:
                pass

    # ─── /staffconsistency ───────────────────────────────────────────────────

    @app_commands.command(name="staffconsistency", description="Consistency & streak leaderboard")
    @app_commands.default_permissions(manage_guild=True)
    async def staffconsistency(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        staff = await self._fetch_all_staff(interaction.guild)
        if not staff:
            await interaction.followup.send(embed=create_info_embed("No Staff", "No staff members found."))
            return

        try:
            weights     = await self.db.get_score_weights(interaction.guild.id)
            cached_rows = await self.db.get_cached_staff_stats(
                interaction.guild.id, [m.id for m in staff], 30, weights
            )
            scores = []
            for sid, s in cached_rows:
                member = interaction.guild.get_member(sid)
                if not member:
                    continue
                # get_cached_staff_stats doesn't attach consistency — fetch separately
                c = s.consistency or await self.db.get_consistency_stats(sid)
                if not c:
                    # Member has no consistency record yet (never sent a message)
                    continue
                scores.append((member, c.consistency_score, c.current_streak,
                               c.longest_streak, c.off_peak_activity, c.weekly_consistency_score))
            scores.sort(key=lambda x: x[1], reverse=True)

            embed = discord.Embed(
                title="🔄 Consistency Leaderboard",
                color=EMBED_COLORS["primary"],
                timestamp=discord.utils.utcnow(),
            )
            embed.set_footer(text="🔥=streak  📅=weekly%  🦉=off-peak msgs")

            lines = []
            for idx, (member, score, streak, longest, off_peak, weekly_pct) in enumerate(scores[:12], 1):
                prefix = MEDAL_EMOJIS[idx - 1] if idx <= 3 else f"`{idx:>2}.`"
                streak_badge = f"🔥{streak}d" if streak >= 3 else f"{streak}d"
                off_peak_badge = f"🦉{off_peak}" if off_peak > 0 else ""
                lines.append(
                    f"{prefix} **{member.display_name}** — `{score:.0f} pts`  "
                    f"{streak_badge}  📅{weekly_pct:.0f}%  best:{longest}d  {off_peak_badge}"
                )
            embed.description = "\n".join(lines) or "No consistency data yet."
            await interaction.followup.send(embed=embed)
        except Exception as e:
            logger.exception("staffconsistency command failed: %s", e)
            try:
                await interaction.followup.send(embed=create_error_embed(
                    "Command Failed", "An unexpected error occurred. Please try again."
                ), ephemeral=True)
            except Exception:
                pass

    # ─── /staffachievements ──────────────────────────────────────────────────

    @app_commands.command(name="staffachievements", description="View achievements earned by a staff member")
    @app_commands.describe(member="Staff member (defaults to yourself)")
    async def staffachievements(
        self, interaction: discord.Interaction, member: Optional[discord.Member] = None
    ):
        await interaction.response.defer(ephemeral=True)
        target = member or interaction.user
        if not self.is_staff(target):
            await interaction.followup.send(
                embed=create_error_embed("Not Staff", f"{target.mention} is not a staff member.")
            )
            return

        guild_weights = await self.db.get_score_weights(interaction.guild.id)
        stats = await self.db.get_staff_stats(target.id, 9999, weights=guild_weights)
        consistency = await self.db.get_consistency_stats(target.id)
        if consistency:
            stats.consistency = consistency

        streak = consistency.current_streak if consistency else 0
        metric_map = {
            "moderation_actions":    stats.moderation_actions,
            "vc_hours":              stats.vc_hours,
            "new_members_helped":    stats.new_members_helped,
            "fast_responses":        stats.fast_responses,
            "first_responses":       stats.first_responses,
            "threads_created":       stats.threads_created,
            "reactions_given":       stats.reactions_given,
            "current_streak":        streak,
            "off_peak_messages":     consistency.off_peak_activity if consistency else 0,
            "conversation_starters": stats.conversation_starters,
            "new_member_greetings":  stats.new_member_greetings,
            "dead_chat_revivals":    stats.dead_chat_revivals,
            "reactions_received":    stats.reactions_received,
            "unique_talked_with":    stats.unique_talked_with,
            "ping_responses":        stats.ping_responses,
            "vc_people_sessions":    stats.vc_people_sessions,
            "unique_newbies_chatted":stats.unique_newbies_chatted,
        }

        now_dt = discord.utils.utcnow()
        earned_lines = []
        locked_lines = []

        for key, info in ACHIEVEMENT_THRESHOLDS.items():
            val        = metric_map.get(info["metric"], 0)
            target_val = info["value"]
            if val >= target_val:
                notified_at = await self.db.get_achievement_notified_at(target.id, key)
                decay_days  = info.get("decay_days")
                if notified_at and decay_days:
                    age_days = (now_dt - notified_at).days
                    if age_days > decay_days:
                        earned_lines.append(f"💤 ~~{info['emoji']} **{info['desc']}**~~ *(faded)*")
                        continue
                    elif age_days < 7:
                        earned_lines.append(f"✨ {info['emoji']} **{info['desc']}** *(new!)*")
                        continue
                earned_lines.append(f"{info['emoji']} **{info['desc']}**")
            else:
                progress = min(val / target_val, 1.0) * 100
                bar = create_progress_bar(val, target_val, length=6)
                locked_lines.append(
                    f"🔒 {info['desc']}  `{bar}` {progress:.0f}%  ({format_number(val)}/{format_number(target_val)})"
                )

        tier_info = RANK_TIERS[stats.rank_tier.value]
        embed = discord.Embed(
            title=f"🏅 {target.display_name}'s Achievements",
            description=f"**{stats.rank_tier.display}** · {len(earned_lines)} / {len(ACHIEVEMENT_THRESHOLDS)} unlocked",
            color=tier_info["colour"],
            timestamp=discord.utils.utcnow(),
        )
        embed.set_thumbnail(url=target.display_avatar.url)

        if earned_lines:
            embed.add_field(name=f"✅ Earned ({len(earned_lines)})", value="\n".join(earned_lines), inline=False)
        if locked_lines:
            embed.add_field(name=f"🔒 In Progress ({len(locked_lines)})", value="\n".join(locked_lines[:10]), inline=False)

        embed.set_footer(text="✨ = earned recently  💤 = achievement decayed (inactive)  🔒 = not yet earned")
        await interaction.followup.send(embed=embed)

    # ─── /dailychallenges ─────────────────────────────────────────────────────

    @app_commands.command(name="dailychallenges", description="View and track your daily staff challenges")
    async def dailychallenges(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if not self.is_staff(interaction.user):
            await interaction.followup.send(
                embed=create_error_embed("Not Staff", "You don't have a staff role."), ephemeral=True
            )
            return

        today    = discord.utils.utcnow().date().isoformat()
        staff_id = interaction.user.id

        # Fetch today's stats (1-day window) to measure progress
        weights  = await self.db.get_score_weights(interaction.guild.id)
        stats    = await self.db.get_staff_stats(staff_id, days=1, weights=weights)
        cons     = await self.db.get_consistency_stats(staff_id)
        active_today = await self.db.was_active_today(staff_id)

        # Build a metric map keyed the same way DAILY_CHALLENGES uses
        metric_map = {
            "total_messages":      stats.total_messages,
            "moderation_actions":  stats.moderation_actions,
            "vc_hours":            stats.vc_hours,
            "unique_talked_with":  stats.unique_talked_with,
            "reactions_given":     stats.reactions_given,
            "first_responses":     stats.first_responses,
            "new_member_greetings":stats.new_member_greetings,
            "quality_events":      stats.quality_events,
            "_active_today":       1 if active_today else 0,
            "ping_responses":      stats.ping_responses,
        }

        completed_count = 0
        total_pts_today = 0
        challenge_lines = []

        for ch in DAILY_CHALLENGES:
            current = metric_map.get(ch["metric"], 0)
            target  = ch["target"]
            done    = current >= target
            if done:
                completed_count += 1
                total_pts_today += ch["pts"]

            # Persist progress
            await self.db.upsert_daily_challenge_progress(
                staff_id, today, ch["key"], float(current), done
            )

            bar_len = 8
            filled  = min(int((current / target) * bar_len), bar_len) if target > 0 else bar_len
            bar     = "█" * filled + "░" * (bar_len - filled)

            if isinstance(target, float):
                val_str = f"{current:.1f}/{target:.1f}"
            else:
                val_str = f"{int(current)}/{int(target)}"

            status = "✅" if done else "🔲"
            pts_tag = f"  `+{ch['pts']}pts`" if done else f"  `{ch['pts']}pts`"
            challenge_lines.append(
                f"{status} {ch['emoji']} **{ch['label']}**{pts_tag}\n"
                f"  `{bar}` {val_str}"
            )

        tier_info = RANK_TIERS[stats.rank_tier.value]
        embed = discord.Embed(
            title=f"🎯 Daily Challenges — {interaction.user.display_name}",
            description=(
                f"**{completed_count}/{len(DAILY_CHALLENGES)}** completed today  ·  "
                f"**+{total_pts_today} pts** earned\n"
                f"Resets at midnight UTC 🕛"
            ),
            color=tier_info["colour"],
            timestamp=discord.utils.utcnow(),
        )
        embed.set_thumbnail(url=interaction.user.display_avatar.url)

        # Split into two columns of 5
        half = len(challenge_lines) // 2
        embed.add_field(name="Challenges", value="\n".join(challenge_lines[:half]), inline=True)
        embed.add_field(name="\u200b",     value="\n".join(challenge_lines[half:]), inline=True)
        embed.set_footer(text="Progress measured from midnight UTC today")
        await interaction.followup.send(embed=embed)

    # ─── /modqueue ────────────────────────────────────────────────────────────

    @app_commands.command(name="modqueue", description="View pending mod actions awaiting review")
    @app_commands.default_permissions(manage_guild=True)
    async def modqueue(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        pending = await self.db.get_pending_mod_actions(interaction.guild.id)
        if not pending:
            await interaction.followup.send(
                embed=create_success_embed("Queue Empty", "No mod actions are pending review. ✅")
            )
            return

        embed = discord.Embed(
            title=f"🛡️ Mod Review Queue — {len(pending)} pending",
            description="Use the buttons in the mod-log channel to approve or reject each action.",
            color=EMBED_COLORS["warning"],
            timestamp=discord.utils.utcnow(),
        )

        for row in pending[:10]:
            mod    = await get_member_safe(interaction.guild, row["moderator_id"])
            target = await get_member_safe(interaction.guild, row["target_id"])
            mod_n  = mod.display_name    if mod    else f"<@{row['moderator_id']}>"
            tgt_n  = target.display_name if target else f"<@{row['target_id']}>"
            age    = discord.utils.utcnow() - datetime.fromisoformat(row["detected_at"])
            ago    = f"{int(age.total_seconds() // 60)}m ago"
            embed.add_field(
                name=f"#{row['id']} · {row['action_type'].upper()} by {mod_n}",
                value=f"Target: **{tgt_n}**  ·  Reason: {row['reason'] or '*none*'}  ·  {ago}",
                inline=False,
            )

        if len(pending) > 10:
            embed.set_footer(text=f"Showing 10 of {len(pending)} — check mod-log channel for full list")
        await interaction.followup.send(embed=embed)

    # ─── /staffoverview ──────────────────────────────────────────────────────

    @app_commands.command(name="staffoverview", description="Server-wide staff health & KPI dashboard (Admin only)")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(days="Analysis window (default 7)")
    async def staffoverview(self, interaction: discord.Interaction, days: int = 7):
        await interaction.response.defer(ephemeral=True)
        staff = await self._fetch_all_staff(interaction.guild)
        if not staff:
            await interaction.followup.send(embed=create_info_embed("No Staff", "No staff members found."))
            return

        try:
            health       = await self.db.get_server_health([m.id for m in staff], days)
            inactive_ids = await self.db.get_inactive_staff([m.id for m in staff], days)
            active_pct   = (health["active_staff"] / health["total_staff"] * 100) if health["total_staff"] else 0
            avg_rt_str   = format_seconds(health["avg_response_secs"]) if health["avg_response_secs"] else "N/A"

            embed = discord.Embed(
                title=f"📊 Staff Health Dashboard — {interaction.guild.name}",
                description=f"**Last {days} days** · {health['total_staff']} total staff",
                color=EMBED_COLORS["primary"],
                timestamp=discord.utils.utcnow(),
            )

            coverage_bar = create_progress_bar(health["active_staff"], health["total_staff"], length=12)
            embed.add_field(
                name="👥 Staff Coverage",
                value=(
                    f"`{coverage_bar}` **{health['active_staff']}/{health['total_staff']}** active  "
                    f"({active_pct:.0f}%)\n"
                    f"Inactive: **{len(inactive_ids)}** members flagged"
                ),
                inline=False,
            )

            embed.add_field(
                name="📈 Community KPIs",
                value=(
                    f"`Total messages      ` **{format_number(health['total_messages'])}**\n"
                    f"`Unique talked to    ` **{health.get('unique_talked_with', 0)}** 🫂\n"
                    f"`Ping responses      ` **{health.get('ping_responses', 0)}** 📟\n"
                    f"`First responses     ` **{health['first_responses']}** 🚀"
                ),
                inline=True,
            )

            embed.add_field(
                name="🛡️ Operations",
                value=(
                    f"`Mod actions         ` **{health['total_moderation']}**\n"
                    f"`Avg response time   ` **{avg_rt_str}**\n"
                    f"`Total VC hours      ` **{health['vc_hours']:.1f}h**\n"
                    f"`Server bumps        ` **{health.get('total_bumps', 0)}**"
                ),
                inline=True,
            )

            # Cache already includes consistency — no secondary fetch needed
            weights     = await self.db.get_score_weights(interaction.guild.id)
            staff_ids   = [m.id for m in staff]
            cached_rows = await self.db.get_cached_staff_stats(interaction.guild.id, staff_ids, days, weights)
            stats_map   = {sid: s for sid, s in cached_rows}
            stats_list  = [(m, stats_map[m.id]) for m in staff if m.id in stats_map]
            stats_list.sort(key=lambda x: x[1].engagement_score, reverse=True)

            top_lines = []
            for idx, (m, s) in enumerate(stats_list[:5], 1):
                streak = s.consistency.current_streak if s.consistency else 0
                wcons  = s.consistency.weekly_consistency_score if s.consistency else 0.0
                top_lines.append(
                    f"`{idx}.` **{m.display_name}** {s.rank_tier.display.split()[0]} "
                    f"· **{s.engagement_score:.0f}** pts  "
                    f"💬{s.total_messages} 🛡️{s.moderation_actions} 🔥{streak}d 📅{wcons:.0f}%"
                )
            if top_lines:
                embed.add_field(name=f"🏆 Top Performers ({days}d)", value="\n".join(top_lines), inline=False)

            if inactive_ids:
                inactive_members = [m for m in staff if m.id in inactive_ids]
                embed.add_field(
                    name=f"⚠️ Inactive {days}+ Days ({len(inactive_ids)})",
                    value="\n".join(f"• {m.mention}" for m in inactive_members[:8])
                    + (f"\n… and {len(inactive_ids) - 8} more" if len(inactive_ids) > 8 else ""),
                    inline=False,
                )

            embed.set_footer(text="Admin only · Use /staffstats for individual member details")
            await interaction.followup.send(embed=embed)
        except Exception as e:
            logger.exception("staffoverview command failed: %s", e)
            try:
                await interaction.followup.send(embed=create_error_embed(
                    "Command Failed", "An unexpected error occurred. Please try again."
                ), ephemeral=True)
            except Exception:
                pass

    # ─── Notes, weights, config, export, dashboard ─────────────────────────────

    notes_group = app_commands.Group(name="staffnote", description="Manage staff performance notes")

    @notes_group.command(name="add", description="Add a note to a staff member")
    @app_commands.default_permissions(manage_guild=True)
    async def staffnote_add(self, interaction: discord.Interaction, member: discord.Member, note: str, positive: bool = True):
        if not self.is_staff(member):
            await interaction.response.send_message(embed=create_error_embed("Not Staff", f"{member.mention} is not a staff member."), ephemeral=True)
            return
        sn = StaffNote(id=None, staff_id=member.id, author_id=interaction.user.id, note=note, created_at=discord.utils.utcnow(), is_positive=positive)
        note_id = await self.db.add_staff_note(sn)
        emoji = "✅" if positive else "📌"
        await interaction.response.send_message(embed=create_success_embed("Note Added", f"{emoji} Note **#{note_id}** added for **{member.display_name}**.\n> {note}"), ephemeral=True)

    @notes_group.command(name="view", description="View notes for a staff member")
    async def staffnote_view(self, interaction: discord.Interaction, member: discord.Member):
        notes = await self.db.get_staff_notes(member.id, limit=10)
        if not notes:
            await interaction.response.send_message(embed=create_info_embed("No Notes", f"No notes recorded for **{member.display_name}**."), ephemeral=True)
            return
        embed = discord.Embed(title=f"📝 Notes — {member.display_name}", color=EMBED_COLORS["info"], timestamp=discord.utils.utcnow())
        embed.set_thumbnail(url=member.display_avatar.url)
        for n in notes:
            author = self.bot.get_user(n.author_id)
            author_name = author.display_name if author else f"ID:{n.author_id}"
            emoji = "✅" if n.is_positive else "📌"
            embed.add_field(name=f"{emoji} Note #{n.id}  ·  {author_name}  ·  {format_relative_time(n.created_at)}", value=n.note[:512], inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @notes_group.command(name="delete", description="Delete a staff note by ID")
    @app_commands.default_permissions(manage_guild=True)
    async def staffnote_delete(self, interaction: discord.Interaction, note_id: int):
        deleted = await self.db.delete_staff_note(note_id, interaction.user.id)
        if deleted:
            await interaction.response.send_message(embed=create_success_embed("Note Deleted", f"Note **#{note_id}** has been removed."), ephemeral=True)
        else:
            await interaction.response.send_message(embed=create_error_embed("Not Found", f"Note **#{note_id}** does not exist."), ephemeral=True)

    weights_group = app_commands.Group(name="staffweights", description="Manage per-guild scoring weights")

    @weights_group.command(name="view", description="View current scoring weights")
    async def staffweights_view(self, interaction: discord.Interaction):
        weights = await self.db.get_score_weights(interaction.guild.id)
        lines = []
        for k, v in sorted(weights.items()):
            default = SCORE_WEIGHTS.get(k, v)
            custom_tag = "  *(custom)*" if v != default else ""
            lines.append(f"`{k:<28}` **{v}**{custom_tag}")
        embed = discord.Embed(title="⚙️ Scoring Weights", description="\n".join(lines), color=EMBED_COLORS["primary"])
        embed.set_footer(text="Use /staffweights set to override  ·  /staffweights reset to restore defaults")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @weights_group.command(name="set", description="Override a scoring weight")
    @app_commands.default_permissions(administrator=True)
    async def staffweights_set(self, interaction: discord.Interaction, key: str, value: float):
        if key not in SCORE_WEIGHTS:
            valid = ", ".join(f"`{k}`" for k in sorted(SCORE_WEIGHTS))
            await interaction.response.send_message(embed=create_error_embed("Invalid Key", f"Valid keys:\n{valid}"), ephemeral=True)
            return
        if value < 0:
            await interaction.response.send_message(embed=create_error_embed("Invalid Value", "Weight value must be ≥ 0."), ephemeral=True)
            return
        await self.db.set_score_weight(interaction.guild.id, key, value, interaction.user.id)
        self.db.invalidate_dashboard_cache(interaction.guild.id)
        await interaction.response.send_message(embed=create_success_embed("Weight Updated", f"`{key}` → **{value}**"), ephemeral=True)

    @weights_group.command(name="reset", description="Reset all weights to defaults")
    @app_commands.default_permissions(administrator=True)
    async def staffweights_reset(self, interaction: discord.Interaction):
        await self.db.reset_score_weights(interaction.guild.id)
        self.db.invalidate_dashboard_cache(interaction.guild.id)
        await interaction.response.send_message(embed=create_success_embed("Weights Reset", "All scoring weights have been reset to defaults."), ephemeral=True)

    @app_commands.command(name="staffconfig", description="View staff analytics configuration")
    @app_commands.default_permissions(administrator=True)
    async def staffconfig(self, interaction: discord.Interaction):
        embed = discord.Embed(title="⚙️ Staff Analytics Config", color=EMBED_COLORS["primary"])
        role_mentions = []
        for role_id in self.config.staff_roles:
            role = interaction.guild.get_role(role_id)
            if role:
                role_mentions.append(role.mention)
        embed.add_field(name="👥 Staff Roles", value="\n".join(role_mentions) or "None configured  *(set MOD_ROLES env var)*", inline=False)
        channel_names = ["trial_chat", "mod_log", "staff_chat", "alert_channel", "weekly_report_channel", "dashboard_channel"]
        ch_lines = []
        for name in channel_names:
            ch_id = self.config.get_channel_id(name)
            if ch_id:
                ch_obj = interaction.guild.get_channel(ch_id)
                ch_lines.append(f"• **{name}**: {ch_obj.mention if ch_obj else ch_id}")
        embed.add_field(name="📺 Tracked Channels", value="\n".join(ch_lines) or "None configured", inline=False)
        settings_lines = [
            f"• Inactivity threshold: **{self.config.get_setting('inactivity_days')}d**",
            f"• Min VC session: **{self.config.get_setting('min_vc_session')}s**",
            f"• Report cooldown: **{self.config.get_setting('report_cooldown')}s**",
            f"• Data retention: **{self.config.get_setting('db_retention_days')}d**",
            f"• Response time window: **{self.config.get_setting('response_time_window')}s**",
            f"• Ping response window: **{PING_RESPONSE_WINDOW}s**",
            f"• Achievement decay days: **{ACHIEVEMENT_DECAY_DAYS}d**",
        ]
        embed.add_field(name="⚙️ Settings", value="\n".join(settings_lines), inline=False)
        embed.set_footer(text="Override settings via environment variables  ·  See docs for full list")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="staffdashboard", description="Post or refresh the live staff dashboard (Admin only)")
    @app_commands.default_permissions(manage_guild=True)
    async def staffdashboard(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        channel_id = self.config.get_channel_id("dashboard_channel")
        if not channel_id:
            await interaction.followup.send(embed=create_error_embed("Not Configured", "Set the `DASHBOARD_CHANNEL_ID` environment variable to enable the dashboard."))
            return
        try:
            await self._update_dashboard()
            channel = interaction.guild.get_channel(channel_id)
            ch_mention = channel.mention if channel else f"<#{channel_id}>"
            await interaction.followup.send(embed=create_success_embed("Dashboard Updated", f"Dashboard refreshed in {ch_mention}."))
        except Exception as e:
            await interaction.followup.send(embed=create_error_embed("Update Failed", str(e)))

    @app_commands.command(name="staffexport", description="Export staff data as JSON (Admin only)")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(member="Staff member to export", days="Days to include (default 30)")
    async def staffexport(self, interaction: discord.Interaction, member: discord.Member, days: int = 30):
        await interaction.response.defer(ephemeral=True)
        if not self.is_staff(member):
            await interaction.followup.send(embed=create_error_embed("Not Staff", f"{member.mention} is not a staff member."))
            return
        try:
            data = await self.db.export_staff_data(member.id, days)
            filename = f"staff_export_{member.id}_{discord.utils.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
            buf = io.BytesIO(json.dumps(data, indent=2, default=str).encode("utf-8"))
            buf.seek(0)
            await interaction.followup.send(file=discord.File(buf, filename=filename), content=f"📊 Export for **{member.display_name}** (last {days} days)")
        except Exception as e:
            logger.exception("staffexport command failed: %s", e)
            try:
                await interaction.followup.send(embed=create_error_embed(
                    "Command Failed", "An unexpected error occurred. Please try again."
                ), ephemeral=True)
            except Exception:
                pass

    # ─── /stafftimeline ──────────────────────────────────────────────────────

    @app_commands.command(name="stafftimeline", description="30-day ASCII activity chart for a staff member")
    @app_commands.describe(member="Staff member (defaults to yourself)", days="Days to chart (default 30, max 60)")
    async def stafftimeline(
        self, interaction: discord.Interaction,
        member: Optional[discord.Member] = None,
        days: int = 30,
    ):
        await interaction.response.defer(ephemeral=True)
        target = member or interaction.user
        if not self.is_staff(target):
            await interaction.followup.send(
                embed=create_error_embed("Not Staff", f"{target.mention} is not a staff member.")
            )
            return

        days = max(7, min(days, 60))
        daily_counts = await self.db.get_daily_message_counts(target.id, days)
        mod_counts   = await self.db.get_daily_mod_counts(target.id, days)

        if not any(daily_counts.values()):
            await interaction.followup.send(
                embed=create_info_embed("No Data", f"No messages recorded for **{target.display_name}** in the last {days} days.")
            )
            return

        # Build ASCII bar chart
        from datetime import date, timedelta as td
        today   = discord.utils.utcnow().date()
        dates   = [today - td(days=i) for i in range(days - 1, -1, -1)]
        msg_vals = [daily_counts.get(str(d), 0) for d in dates]
        mod_vals = [mod_counts.get(str(d), 0) for d in dates]

        max_val  = max(msg_vals) if msg_vals and max(msg_vals) > 0 else 1
        bar_height = 8   # rows in the chart

        # Build rows top-down
        rows = []
        for row_i in range(bar_height, 0, -1):
            threshold = (row_i / bar_height) * max_val
            row_chars = []
            for v in msg_vals:
                if v >= threshold:
                    row_chars.append("█")
                elif v >= threshold * 0.5:
                    row_chars.append("▄")
                else:
                    row_chars.append(" ")
            rows.append("".join(row_chars))

        # Bottom axis: marks for every 7 days
        axis = ""
        for i, d in enumerate(dates):
            if i % 7 == 0:
                axis += "┬"
            else:
                axis += "─"
        axis_labels = ""
        for i, d in enumerate(dates):
            if i % 7 == 0:
                label = d.strftime("%d/%m")
                axis_labels += label
                # pad to next 7-day mark
                remaining = 7 - len(label)
                axis_labels += " " * max(0, remaining)

        # Peak/avg stats
        avg_val   = sum(msg_vals) / max(len([v for v in msg_vals if v > 0]), 1)
        peak_date = dates[msg_vals.index(max(msg_vals))] if max(msg_vals) > 0 else today
        total_mod = sum(mod_vals)

        # 4-week comparison
        mid = len(msg_vals) // 2
        first_half  = sum(msg_vals[:mid])
        second_half = sum(msg_vals[mid:])
        if first_half > 0:
            trend_pct = round((second_half - first_half) / first_half * 100)
            trend_str = f"{'⬆️' if trend_pct >= 0 else '⬇️'} {abs(trend_pct)}% vs prior {days//2}d"
        else:
            trend_str = "🆕 First period"

        # Drop-off detection
        dropoff_warning = ""
        if second_half < first_half * 0.5 and first_half > 5:
            dropoff_warning = "\n⚠️ **Significant drop-off detected** in recent days"

        chart_lines = "\n".join(rows)
        chart = f"```\n{chart_lines}\n{axis}\n{axis_labels.rstrip()}\n```"

        tier = RankTier.from_score((await self.db.get_staff_stats(target.id, days)).engagement_score)
        tier_info = RANK_TIERS[tier.value]

        embed = discord.Embed(
            title=f"📈 Activity Timeline — {target.display_name}",
            description=(
                f"{tier_info['emoji']} **{tier.value}** · Last **{days}** days{dropoff_warning}"
            ),
            color=tier_info["colour"],
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name=f"Daily Messages (peak={max(msg_vals)})", value=chart, inline=False)

        # Mod activity as compact dot row
        if total_mod > 0:
            mod_dots = ""
            for v in mod_vals:
                if v == 0:   mod_dots += "·"
                elif v < 3:  mod_dots += "▪"
                elif v < 7:  mod_dots += "◾"
                else:        mod_dots += "⬛"
            embed.add_field(
                name=f"🛡️ Mod Actions  ({total_mod} total)",
                value=f"`{mod_dots}`\n`·=none  ▪=1-2  ◾=3-6  ⬛=7+`",
                inline=False,
            )

        embed.add_field(
            name="📊 Stats",
            value=(
                f"`Avg/active day  ` **{avg_val:.1f}** msgs\n"
                f"`Peak day        ` **{peak_date.strftime('%d %b')}** ({max(msg_vals)} msgs)\n"
                f"`Active days     ` **{sum(1 for v in msg_vals if v > 0)}** / {days}\n"
                f"`Trend           ` {trend_str}"
            ),
            inline=False,
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.set_footer(text="█=full  ▄=half  ·=silent  |  each column = 1 day")
        await interaction.followup.send(embed=embed)

    # ─── Background tasks ─────────────────────────────────────────────────────

    async def _weekly_report_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            now = discord.utils.utcnow()
            if now.weekday() == 0 and now.hour == 9:
                already_sent = (self._last_weekly_report is not None and self._last_weekly_report.date() == now.date())
                if not already_sent:
                    try:
                        await self._send_weekly_report()
                        self._last_weekly_report = now
                    except Exception:
                        logger.exception("Weekly report failed")
                await asyncio.sleep(3600)
            else:
                await asyncio.sleep(3600)

    async def _send_weekly_report(self):
        channel_id = self.config.get_alert_setting("weekly_report_channel")
        if not channel_id:
            return
        channel = self.bot.get_channel(channel_id)
        if not channel:
            return

        all_staff = [m for guild in self.bot.guilds for m in guild.members if self.is_staff(m)]
        if not all_staff:
            return

        guild   = self.bot.guilds[0]
        weights = await self.db.get_score_weights(guild.id)
        health  = await self.db.get_server_health([m.id for m in all_staff], 7)
        inactive_ids = await self.db.get_inactive_staff([m.id for m in all_staff], 7)

        embed = discord.Embed(
            title="📊 Weekly Staff Report",
            description=f"Week ending **{discord.utils.utcnow().strftime('%B %d, %Y')}**",
            color=EMBED_COLORS["primary"],
            timestamp=discord.utils.utcnow(),
        )

        member_map  = {m.id: m for m in all_staff[:25]}
        cached_rows = await self.db.get_cached_staff_stats(
            guild.id, list(member_map.keys()), 7, weights
        )
        stats_list = [(member_map[sid], s) for sid, s in cached_rows if sid in member_map]
        stats_list.sort(key=lambda x: x[1].engagement_score, reverse=True)

        top_lines = []
        for idx, (member, s) in enumerate(stats_list[:5], 1):
            prefix = MEDAL_EMOJIS[idx - 1] if idx <= 3 else f"{idx}."
            streak = s.consistency.current_streak if s.consistency else 0
            wcons  = s.consistency.weekly_consistency_score if s.consistency else 0.0
            top_lines.append(
                f"{prefix} **{member.display_name}** {s.rank_tier.display.split()[0]} "
                f"— **{s.engagement_score:.0f}** pts  "
                f"💬{s.total_messages} 🛡️{s.moderation_actions} 🔥{streak}d 📅{wcons:.0f}%"
            )
        if top_lines:
            embed.add_field(name="🏆 Top Performers", value="\n".join(top_lines), inline=False)

        active_pct = (health["active_staff"] / health["total_staff"] * 100) if health["total_staff"] else 0
        embed.add_field(
            name="📈 7-Day Server Summary",
            value=(
                f"`Staff active         ` **{health['active_staff']}/{health['total_staff']}** ({active_pct:.0f}%)\n"
                f"`Mod actions          ` **{health['total_moderation']}**\n"
                f"`VC hours             ` **{health['vc_hours']:.1f}h**\n"
                f"`First responses      ` **{health['first_responses']}** 🚀\n"
                f"`Ping responses       ` **{health.get('ping_responses', 0)}** 📟\n"
                f"`Avg response time    ` **{format_seconds(health['avg_response_secs'])}**"
            ),
            inline=False,
        )

        if inactive_ids:
            inactive = [m for m in all_staff if m.id in inactive_ids]
            embed.add_field(
                name=f"⚠️ Inactive 7+ Days ({len(inactive)})",
                value=", ".join(m.display_name for m in inactive[:10]) + (f" +{len(inactive) - 10}" if len(inactive) > 10 else ""),
                inline=False,
            )

        # Drop-off detection — flag active staff with a sudden dip
        all_staff_ids = [m.id for m in all_staff]
        dropoffs = await self.db.get_dropoff_staff(all_staff_ids, lookback_weeks=4)
        if dropoffs:
            dip_lines = []
            for d in dropoffs[:6]:
                m = next((x for x in all_staff if x.id == d["staff_id"]), None)
                name = m.display_name if m else f"<@{d['staff_id']}>"
                dip_lines.append(
                    f"• **{name}** — ⬇️ **{d['drop_pct']}%** drop  "
                    f"(this week: {d['this_week']} msgs · avg: {d['avg_prev']:.0f})"
                )
            embed.add_field(
                name=f"📉 Sudden Drop-offs ({len(dropoffs)})",
                value="".join(dip_lines),
                inline=False,
            )

        await channel.send(embed=embed)

    async def _inactivity_check_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            await self._check_inactivity()
            await asyncio.sleep(86400)

    async def _check_inactivity(self):
        alert_channel_id = self.config.get_alert_setting("alert_channel")
        if not alert_channel_id:
            return
        alert_channel = self.bot.get_channel(alert_channel_id)
        if not alert_channel:
            return
        threshold = self.config.get_setting("inactivity_threshold_days", 7)
        for guild in self.bot.guilds:
            staff = [m for m in guild.members if self.is_staff(m)]
            if not staff:
                continue
            inactive_ids = await self.db.get_inactive_staff([m.id for m in staff], threshold)
            if inactive_ids:
                inactive = [m for m in staff if m.id in inactive_ids]
                embed = create_warning_embed(
                    f"Staff Inactivity — {guild.name}",
                    f"The following staff have had no recorded activity in **{threshold}+** days:\n"
                    + "\n".join(f"• {m.mention}" for m in inactive[:10]),
                )
                if len(inactive) > 10:
                    embed.set_footer(text=f"And {len(inactive) - 10} more not shown")
                await alert_channel.send(embed=embed)

    async def _metrics_cleanup_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            self.metrics.cleanup_old_data()
            now = discord.utils.utcnow()
            self._staff_cache = {uid: (res, ts) for uid, (res, ts) in self._staff_cache.items() if (now - ts).total_seconds() < _STAFF_CACHE_TTL * 4}
            # Prune expired staff pings
            for ch_id in list(self._pending_staff_pings):
                for role_id in list(self._pending_staff_pings[ch_id]):
                    _, ping_time = self._pending_staff_pings[ch_id][role_id]
                    if (now - ping_time).total_seconds() > PING_RESPONSE_WINDOW * 2:
                        del self._pending_staff_pings[ch_id][role_id]
            await asyncio.sleep(3600)

    # ─── Notification loop ────────────────────────────────────────────────────

    async def _notification_loop(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(120)
        while not self.bot.is_closed():
            try:
                await self._run_notifications()
            except Exception:
                logger.exception("Notification loop error")
            await asyncio.sleep(1800)

    async def _run_notifications(self):
        for guild in self.bot.guilds:
            staff = [m for m in guild.members if self.is_staff(m)]
            if not staff:
                continue
            weights = await self.db.get_score_weights(guild.id)
            for member in staff:
                try:
                    await self._check_member_notifications(member, weights)
                except Exception:
                    logger.debug("Notification check failed for %d", member.id, exc_info=True)

    async def _check_member_notifications(self, member: discord.Member, weights: Dict):
        # Use the 30-day dashboard cache when available — avoids 2 fresh DB
        # queries per member per notification tick (saves ~1600 queries/day at 17 staff)
        cached = self.db._dashboard_cache.get((member.guild.id, 30))
        if cached:
            _, rows = cached
            stats_found = next((s for sid, s in rows if sid == member.id), None)
        else:
            stats_found = None

        if stats_found is not None:
            stats = stats_found
            cons  = stats.consistency
        else:
            stats = await self.db.get_staff_stats(member.id, 30, weights)
            cons  = await self.db.get_consistency_stats(member.id)
            if cons:
                stats.consistency = cons

        now   = discord.utils.utcnow()
        today = now.date().isoformat()
        tier  = stats.rank_tier.value
        streak = cons.current_streak if cons else 0

        prev_tier = self._last_known_rank.get(member.id)
        if prev_tier is not None and prev_tier != tier:
            tier_order = ["Bronze", "Silver", "Gold", "Platinum", "Diamond"]
            is_upgrade = tier_order.index(tier) > tier_order.index(prev_tier)
            if is_upgrade:
                # Hysteresis guard: only fire if score is meaningfully above the new threshold.
                # Prevents oscillation when score sits right on the boundary.
                tier_min  = RANK_TIERS[tier]["min"]
                above_min = stats.engagement_score - tier_min
                session_key = f"{member.id}:{prev_tier}:{tier}:{today}"
                if above_min >= RANKUP_HYSTERESIS and session_key not in self._rankup_sent_session:
                    already_notified = await self.db.has_notified_rankup(member.id, prev_tier, tier)
                    if not already_notified:
                        self._rankup_sent_session.add(session_key)
                        await self.db.mark_rankup_notified(member.id, prev_tier, tier)
                        await self._dm_rank_up(member, prev_tier, tier, stats)
        self._last_known_rank[member.id] = tier

        if streak >= 3 and member.id not in self._streak_warned_today.get(today, set()):
            active_today = await self.db.was_active_today(member.id)
            if not active_today and now.hour >= 20:
                self._streak_warned_today.setdefault(today, set()).add(member.id)
                await self._dm_streak_warning(member, streak)

        prev_streak = self._last_known_streak.get(member.id, 0)
        self._last_known_streak[member.id] = streak
        await self._check_achievement_unlocks(member, stats, prev_streak)

    async def _dm_rank_up(self, member: discord.Member, from_tier: str, to_tier: str, stats):
        if not self.config.dm_notifications_enabled:
            return
        from_info = RANK_TIERS[from_tier]
        to_info   = RANK_TIERS[to_tier]
        embed = discord.Embed(
            title=f"🎉 Rank Up! {to_info['emoji']} {to_tier}",
            description=(
                f"You've ranked up from **{from_info['emoji']} {from_tier}** "
                f"to **{to_info['emoji']} {to_tier}** — {to_info['label']}!\n\n"
                f"**Score:** `{stats.engagement_score:.0f} pts`"
            ),
            color=to_info["colour"], timestamp=discord.utils.utcnow(),
        )
        embed.add_field(
            name="📊 This month",
            value=(
                f"💬 **{stats.total_messages}** messages\n"
                f"🛡️ **{stats.moderation_actions}** mod actions\n"
                f"🔥 **{stats.consistency.current_streak if stats.consistency else 0}d** streak"
            ), inline=True,
        )
        next_tier_name, next_threshold = self._next_tier(stats.engagement_score)
        if next_tier_name:
            next_info = RANK_TIERS[next_tier_name]
            needed = next_threshold - stats.engagement_score
            embed.add_field(name=f"🎯 Next: {next_info['emoji']} {next_tier_name}", value=f"`{needed:.0f} pts` to go", inline=True)
        embed.set_footer(text="Keep it up! Use /mystats to track your progress.")
        try:
            await member.send(embed=embed)
        except discord.Forbidden:
            pass

    async def _dm_streak_warning(self, member: discord.Member, streak: int):
        if not self.config.dm_notifications_enabled:
            return
        embed = discord.Embed(
            title=f"⚠️ Streak at risk — {streak} days",
            description=(
                f"Your **{streak}-day activity streak** will reset at midnight "
                f"if you don't send a message in the server today.\n\nEven a single message keeps it alive. 🔥"
            ),
            color=EMBED_COLORS["warning"], timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text="This reminder only fires after 8 PM when you haven't been active.")
        try:
            await member.send(embed=embed)
        except discord.Forbidden:
            pass

    async def _check_achievement_unlocks(self, member: discord.Member, stats, prev_streak: int):
        if not self.config.dm_notifications_enabled:
            return
        streak = stats.consistency.current_streak if stats.consistency else 0
        metric_map = {
            "moderation_actions":    stats.moderation_actions,
            "vc_hours":              stats.vc_hours,
            "new_members_helped":    stats.new_members_helped,
            "fast_responses":        stats.fast_responses,
            "first_responses":       stats.first_responses,
            "threads_created":       stats.threads_created,
            "reactions_given":       stats.reactions_given,
            "current_streak":        streak,
            "off_peak_messages":     stats.consistency.off_peak_activity if stats.consistency else 0,
            "conversation_starters": stats.conversation_starters,
            "new_member_greetings":  stats.new_member_greetings,
            "dead_chat_revivals":    stats.dead_chat_revivals,
            "reactions_received":    stats.reactions_received,
            "unique_talked_with":    stats.unique_talked_with,
            "ping_responses":        stats.ping_responses,
            "vc_people_sessions":    stats.vc_people_sessions,
            "unique_newbies_chatted":stats.unique_newbies_chatted,
        }
        for key, info in ACHIEVEMENT_THRESHOLDS.items():
            val = metric_map.get(info["metric"], 0)
            if val >= info["value"]:
                if not await self.db.has_notified_achievement(member.id, key):
                    await self.db.mark_achievement_notified(member.id, key)
                    await self._dm_achievement(member, info)

    async def _dm_achievement(self, member: discord.Member, info: dict):
        embed = discord.Embed(
            title=f"{info['emoji']} Achievement Unlocked!",
            description=f"**{info['desc']}**",
            color=EMBED_COLORS["success"], timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text="Use /staffachievements to see all your badges.")
        try:
            await member.send(embed=embed)
        except discord.Forbidden:
            pass

    # ─── Dashboard ────────────────────────────────────────────────────────────

    async def _dashboard_loop(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(180)
        while not self.bot.is_closed():
            try:
                await self._update_dashboard()
            except Exception:
                logger.exception("Dashboard update error")
            interval = self.config.get_setting("dashboard_update_interval", 3600)
            await asyncio.sleep(interval)

    async def _update_dashboard(self):
        channel_id = self.config.get_channel_id("dashboard_channel")
        if not channel_id:
            return
        for guild in self.bot.guilds:
            channel = guild.get_channel(channel_id)
            if not channel:
                continue
            embed = await self._build_dashboard_embed(guild)
            if not embed:
                continue
            msg_id = self._dashboard_message_ids.get(guild.id)
            if msg_id:
                try:
                    msg = await channel.fetch_message(msg_id)
                    await msg.edit(embed=embed)
                    continue
                except discord.NotFound:
                    self._dashboard_message_ids.pop(guild.id, None)
                except (discord.Forbidden, discord.HTTPException):
                    continue
            try:
                async for message in channel.history(limit=20):
                    if message.author == self.bot.user and message.pinned and message.embeds:
                        if "Staff Dashboard" in (message.embeds[0].title or ""):
                            await message.edit(embed=embed)
                            self._dashboard_message_ids[guild.id] = message.id
                            return
            except Exception:
                pass
            try:
                msg = await channel.send(embed=embed)
                await msg.pin()
                self._dashboard_message_ids[guild.id] = msg.id
            except (discord.Forbidden, discord.HTTPException):
                pass

    async def _build_dashboard_embed(self, guild: discord.Guild) -> Optional[discord.Embed]:
        staff = [m for m in guild.members if self.is_staff(m)]
        if not staff:
            return None

        weights  = await self.db.get_score_weights(guild.id)
        health   = await self.db.get_server_health([m.id for m in staff], 7)
        inactive_ids = await self.db.get_inactive_staff([m.id for m in staff], 7)

        # Cache already includes consistency — no secondary fetch needed
        staff_ids   = [m.id for m in staff]
        cached_rows = await self.db.get_cached_staff_stats(guild.id, staff_ids, 30, weights)
        stats_map   = {sid: s for sid, s in cached_rows}
        stats_list  = [(m, stats_map[m.id]) for m in staff if m.id in stats_map]
        stats_list.sort(key=lambda x: x[1].engagement_score, reverse=True)

        now        = discord.utils.utcnow()
        active_pct = (health["active_staff"] / health["total_staff"] * 100) if health["total_staff"] else 0

        embed = discord.Embed(
            title=f"📊 Staff Dashboard — {guild.name}",
            description=(
                f"**Live leaderboard** · Last updated <t:{int(now.timestamp())}:R>\n"
                f"Scores reflect the last **30 days**  ·  Health stats reflect last **7 days**"
            ),
            color=EMBED_COLORS["primary"],
            timestamp=now,
        )

        # Leaderboard
        podium_lines = []
        for idx, (member, s) in enumerate(stats_list[:10], 1):
            streak     = s.consistency.current_streak if s.consistency else 0
            wcons      = s.consistency.weekly_consistency_score if s.consistency else 0.0
            tier_emoji = RANK_TIERS[s.rank_tier.value]["emoji"]
            prefix     = MEDAL_EMOJIS[idx - 1] if idx <= 3 else f"`{idx:>2}.`"
            streak_str = f" 🔥{streak}d" if streak >= 3 else ""
            podium_lines.append(
                f"{prefix} {tier_emoji} **{member.display_name}**"
                f"  `{s.engagement_score:.0f}`"
                f"  💬{s.total_messages} 🛡️{s.moderation_actions}"
                f"  📅{wcons:.0f}%{streak_str}"
            )
        embed.add_field(name="🏆 Leaderboard (30d score)", value="\n".join(podium_lines) or "No data yet.", inline=False)

        # Health
        coverage_bar = create_progress_bar(health["active_staff"], health["total_staff"], length=10)
        avg_rt_str   = format_seconds(health["avg_response_secs"]) if health["avg_response_secs"] else "N/A"
        embed.add_field(
            name="📈 7-Day Health",
            value=(
                f"`Coverage  ` `{coverage_bar}` **{health['active_staff']}/{health['total_staff']}** ({active_pct:.0f}%)\n"
                f"`Mod       ` **{health['total_moderation']}** actions  ·  "
                f"`Response  ` avg **{avg_rt_str}**\n"
                f"`VC        ` **{health['vc_hours']:.1f}h**  ·  "
                f"`Pings     ` **{health.get('ping_responses', 0)}** answered 📟\n"
                f"`Bumps     ` **{health.get('total_bumps', 0)}** 🚀"
            ),
            inline=False,
        )

        # Tier distribution
        tier_counts: Dict[str, int] = {t: 0 for t in RANK_TIERS}
        for _, s in stats_list:
            tier_counts[s.rank_tier.value] += 1
        tier_parts = []
        for tier_name in ("Diamond", "Platinum", "Gold", "Silver", "Bronze"):
            count = tier_counts[tier_name]
            if count:
                info = RANK_TIERS[tier_name]
                tier_parts.append(f"{info['emoji']} {count}")
        embed.add_field(name="🎖️ Tier Distribution", value="  ·  ".join(tier_parts) or "No ranked staff", inline=True)

        # Streak leaders
        streak_leaders = sorted(
            [(m, s) for m, s in stats_list if s.consistency and s.consistency.current_streak >= 3],
            key=lambda x: x[1].consistency.current_streak, reverse=True,
        )[:3]
        if streak_leaders:
            streak_lines = [f"🔥 **{m.display_name}** — {s.consistency.current_streak}d" for m, s in streak_leaders]
            embed.add_field(name="🔥 Streak Leaders", value="\n".join(streak_lines), inline=True)

        # Inactive
        if inactive_ids:
            inactive = [m for m in staff if m.id in inactive_ids]
            embed.add_field(
                name=f"⚠️ Inactive 7d+ ({len(inactive_ids)})",
                value="  ".join(m.mention for m in inactive[:6]) + (f"\n…+{len(inactive_ids) - 6} more" if len(inactive_ids) > 6 else ""),
                inline=False,
            )

        embed.set_footer(text=f"Auto-refreshes every {self.config.get_setting('dashboard_update_interval', 3600)//60} min  ·  /staffstats for individual profiles")
        return embed

    async def _try_parse_prefix_command(self, message: discord.Message, is_staff: bool = False, staff_msg: Optional[StaffMessage] = None) -> None:
        """
        Parse prefix commands like !warn @user or w!warn @user
        Also handles Wick-style w!command @user
        """
        if not message.guild:
            return
        
        content = message.content.strip()
        now = discord.utils.utcnow()
        
        # Dedup key for prefix commands to avoid double-tracking
        dedup_key = None
        
        # Pattern 1: Standard prefix with mention
        # Matches: !warn @user reason, w!warn @user reason, .warn @user, -warn @user
        patterns = [
            # w!warn @user reason
            re.compile(r"^(?:w!|!|\.|-)(\w+)\s+<@!?(\d{17,20})>\s*(.*)$", re.IGNORECASE),
            # w!warn 123456789 reason (ID without mention)
            re.compile(r"^(?:w!|!|\.|-)(\w+)\s+(\d{17,20})\s*(.*)$", re.IGNORECASE),
            # Wick-specific: w!warn <@user> reason
            re.compile(r"^w!(\w+)\s+<@!?(\d{17,20})>\s*(.*)$", re.IGNORECASE),
            # Some bots use w!command user (no @)
            re.compile(r"^w!(\w+)\s+([a-zA-Z0-9_]{2,32})\s*(.*)$", re.IGNORECASE),
        ]
        
        for pattern in patterns:
            match = pattern.match(content)
            if match:
                cmd = match.group(1).lower()
                
                # Check if this is a moderation command
                if cmd not in MODERATION_COMMANDS:
                    continue
                
                # Extract target ID or username
                target_id = None
                target_name = None
                
                if len(match.groups()) >= 2:
                    second_group = match.group(2)
                    # Check if it's a numeric ID
                    if second_group.isdigit():
                        target_id = int(second_group)
                    else:
                        target_name = second_group
                        # Try to find member by name
                        member_obj = discord.utils.find(
                            lambda m: m.name.lower() == target_name.lower() or 
                                    m.display_name.lower() == target_name.lower(),
                            message.guild.members
                        )
                        if member_obj:
                            target_id = member_obj.id
                
                # Extract reason
                reason = match.group(3).strip() if len(match.groups()) >= 3 else None
                if reason == "":
                    reason = None
                
                if not target_id:
                    logger.debug(f"Could not find target for {cmd} command from {message.author.id}")
                    return
                
                # Create dedup key
                dedup_key = (message.author.id, target_id, cmd)
                
                # Check if we've tracked this recently (within last 5 seconds)
                if hasattr(self, '_recent_prefix_commands'):
                    if dedup_key in self._recent_prefix_commands:
                        age = (now - self._recent_prefix_commands[dedup_key]).total_seconds()
                        if age < 5.0:
                            logger.debug(f"Prefix command deduped: {cmd} by {message.author.id} on {target_id}")
                            return
                
                # Store in recent commands cache
                if not hasattr(self, '_recent_prefix_commands'):
                    self._recent_prefix_commands = {}
                self._recent_prefix_commands[dedup_key] = now
                
                # Clean old entries
                if len(self._recent_prefix_commands) > 1000:
                    cutoff = now - timedelta(seconds=10)
                    self._recent_prefix_commands = {
                        k: v for k, v in self._recent_prefix_commands.items() if v > cutoff
                    }
                
                # If this is a staff member, track immediately
                # If not staff, still track but flag it (could be a test or abuse)
                if is_staff:
                    # Staff member issuing command — send to mod review channel
                    await self._queue_mod_for_review(
                        guild=message.guild,
                        moderator_id=message.author.id,
                        target_id=target_id,
                        action_type=cmd,
                        reason=reason,
                        channel_id=message.channel.id,
                        message_id=message.id,
                        timestamp=message.created_at.replace(tzinfo=None),
                    )
                else:
                    # Non-staff issuing mod command - suspicious, track in a separate log
                    logger.warning(f"Non-staff user {message.author.id} attempted moderation command {cmd} on {target_id}")
                    
                    # Optionally alert staff channel
                    alert_channel_id = self.config.get_channel_id("alert_channel")
                    if alert_channel_id:
                        alert_channel = message.guild.get_channel(alert_channel_id)
                        if alert_channel:
                            embed = create_warning_embed(
                                "⚠️ Unauthorized Mod Command Attempt",
                                f"**User:** {message.author.mention}\n"
                                f"**Command:** `{cmd}`\n"
                                f"**Target:** <@{target_id}>\n"
                                f"**Reason:** {reason or 'No reason'}\n"
                                f"**Channel:** {message.channel.mention}\n"
                                f"[Jump to message]({message.jump_url})"
                            )
                            await alert_channel.send(embed=embed)
                
                break  # Only process first matching pattern
async def setup(bot: commands.Bot):
    await bot.add_cog(StaffAnalytics(bot))