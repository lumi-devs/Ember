"""Main Staff Analytics cog — industry-grade metrics, rich ephemeral UI, full tracking."""

from collections import defaultdict
import io
import json
import re
import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional, List, Dict, Set
import asyncio
import logging
from datetime import datetime

from .database import DatabaseManager
from .metrics import MetricsTracker
from .config import StaffAnalyticsConfig
from .models import StaffMessage, Bump, StaffNote, RankTier, EngagementEvent, ReactionReceived
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
    EMBED_COLORS, MODERATION_COMMANDS, MODERATION_COMMAND_RE,
    RANK_TIERS, SCORE_WEIGHTS,
    NEW_MEMBER_THRESHOLD_DAYS, KPI_TARGETS, ACHIEVEMENT_THRESHOLDS,
    LEADERBOARD_LIMIT, MEDAL_EMOJIS,
    QUESTION_RE, QUESTION_KEYWORDS,
)


logger = logging.getLogger(__name__)

# TTL for the staff-role cache (seconds).
# BUG FIX: previously cached forever; a role-config change without a Discord
# role-update event (e.g. env var reload) would leave the cache stale.
_STAFF_CACHE_TTL = 300


class StaffAnalytics(commands.Cog):
    """Tracks staff engagement, moderation, community interaction, and more."""

    def __init__(self, bot: commands.Bot):
        self.bot  = bot
        self.db   = DatabaseManager()
        self.config = StaffAnalyticsConfig()

        # staff_id -> (is_staff: bool, cached_at: datetime)
        self._staff_cache: Dict[int, tuple] = {}

        # Inject a live staff-ID getter into MetricsTracker so it can filter
        # staff-to-staff interactions without a circular import.
        self.metrics = MetricsTracker(staff_id_set_getter=self._get_cached_staff_ids)

        self.last_message_cache: Dict[int, Dict[int, datetime]] = defaultdict(dict)
        self.active_vc_session_ids: Dict[tuple, int] = {}
        self.initialized = False

        # BUG FIX: track last weekly-report date so restarts at 09:00 Monday
        # don't fire the report a second time.
        self._last_weekly_report: Optional[datetime] = None

        # ── Notification state ────────────────────────────────────────────────
        # staff_id -> RankTier.value last time we checked — detect rank-ups
        self._last_known_rank: Dict[int, str] = {}
        # staff_id -> streak last time we checked — detect streak-break warnings
        self._last_known_streak: Dict[int, int] = {}
        # dashboard: message ID of the pinned dashboard embed (per guild)
        self._dashboard_message_ids: Dict[int, int] = {}   # guild_id -> message_id
        # gate: date -> set of staff_ids already warned about streak today
        self._streak_warned_today: Dict[str, Set[int]] = defaultdict(set)


    async def cog_load(self):
        await self.db.initialize()
        self.initialized = True
        # Expose the initialised DatabaseManager so sibling cogs
        # (e.g. StaffReviewCog) can access it via bot.analytics_db.
        self.bot.analytics_db = self.db
        logger.info("StaffAnalytics cog loaded")
        asyncio.create_task(self._weekly_report_loop())
        asyncio.create_task(self._inactivity_check_loop())
        asyncio.create_task(self._metrics_cleanup_loop())
        asyncio.create_task(self._notification_loop())
        asyncio.create_task(self._dashboard_loop())

    async def cog_unload(self):
        await self.db.close()
        logger.info("StaffAnalytics cog unloaded")

    # ─── Staff role check ─────────────────────────────────────────────────────

    def is_staff(self, member: discord.Member) -> bool:
        if member.bot:
            return False
        now = datetime.now()
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
        """Return a snapshot of all currently-cached staff IDs."""
        now = datetime.now()
        return {
            uid for uid, (is_s, ts) in self._staff_cache.items()
            if is_s and (now - ts).total_seconds() < _STAFF_CACHE_TTL
        }

    def _member_age_days(self, member: discord.Member) -> float:
        if not member.joined_at:
            return 999.0
        return (datetime.now(member.joined_at.tzinfo) - member.joined_at).total_seconds() / 86400

    # ─── Event listeners ──────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.roles != after.roles:
            # Invalidate cache entry; it will be rebuilt on next is_staff() call
            self._staff_cache.pop(after.id, None)


    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild:
            return
        # Let bot messages through only for mod-bot embed detection
        if message.author.bot:
            await self._try_parse_mod_bot_embed(message)
            await self._try_track_disboard_bump(message)
            return

        is_staff_member = self.is_staff(message.author)
        now = datetime.now()

        # ── Non-staff messages: record pending questions ──────────────────────
        if not is_staff_member:
            content = message.content or ""
            if self._is_question_message(content):
                self.metrics.record_question(message.channel.id, message.author.id, now)
            # Confirm a pending dead-chat revival if one is registered for this channel
            revival = self.metrics.confirm_revival(message.channel.id, message.author.id, now)
            if revival:
                await self.db.insert_engagement_event(revival)
            # Always update the any-message timestamp (used for dead-chat detection)
            self.metrics.record_any_message(message.channel.id, now)
            return

        word_count = self.metrics.count_words(message.content)
        staff_msg  = StaffMessage(
            staff_id   = message.author.id,
            channel_id = message.channel.id,
            message_id = message.id,
            timestamp  = now,
            content    = message.content,
            word_count = word_count,
        )

        await self.db.insert_staff_message(staff_msg)
        await self.db.update_consistency_stats(message.author.id, now)
        self.metrics.record_message_time(message.author.id, now)
        self.last_message_cache[message.channel.id][message.author.id] = now

        # Response time
        rt = self.metrics.check_response_time(staff_msg, message.channel.id)
        if rt:
            await self.db.insert_response_time(rt)

        # First response
        has_pending = bool(self.metrics.pending_questions.get(message.channel.id))
        fr = self.metrics.check_first_response(staff_msg, message.channel.id, has_pending)
        if fr:
            await self.db.insert_first_response(fr)

        # Message quality scoring
        has_attachment = bool(message.attachments)
        quality_event = self.metrics.score_message_quality(staff_msg, has_attachment)
        if quality_event:
            await self.db.insert_quality_event(quality_event)

        # Interactions
        await self._process_interactions(message, staff_msg)

        # Conversation session
        session = self.metrics.update_conversation(staff_msg, [u.id for u in message.mentions])
        if session:
            await self.db.insert_conversation_session(session)

        # Help event — detect new-member status
        is_new = False
        if message.reference and message.reference.resolved:
            resolved = message.reference.resolved
            if isinstance(resolved, discord.Message) and not resolved.author.bot:
                try:
                    target_member = message.guild.get_member(resolved.author.id)
                    if target_member:
                        is_new = self._member_age_days(target_member) <= NEW_MEMBER_THRESHOLD_DAYS
                except Exception:
                    pass

        # BUG FIX: only fetch channel history when the message passes a keyword
        # pre-filter.  Original fetched history on EVERY staff message — at 24k
        # msg/day with ~20% being staff that's ~4 800 Discord API calls/day just
        # for this check alone.
        await self._process_help_event(message, staff_msg, is_new)

        # Reports in trial-chat
        trial_chat_id = self.config.get_channel_id("trial_chat")
        if trial_chat_id and message.channel.id == trial_chat_id:
            await self._process_report(message, staff_msg)

        # Role pings
        if message.role_mentions:
            counts = {role.id: len(role.members) for role in message.role_mentions}
            for ping in self.metrics.check_role_ping(
                staff_msg, [{"id": r.id} for r in message.role_mentions], counts
            ):
                await self.db.insert_role_ping(ping)

        # ── Engagement tracking ───────────────────────────────────────────────

        # Always record timestamp for dead-chat detection, THEN check for revival.
        # Order matters: record_any_message must run AFTER check_dead_chat_revival
        # so the silence gap is measured against the previous message, not this one.
        self.metrics.check_dead_chat_revival(staff_msg, now)
        self.metrics.record_any_message(message.channel.id, now)

        # Conversation starter — non-reply messages in general channels only
        if not message.reference and self.config.is_general_channel(message.channel.id):
            starter = self.metrics.check_conversation_starter(staff_msg, now)
            if starter:
                await self.db.insert_engagement_event(starter)

        # New member greeting — reply to a new member that didn't produce a help event
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


    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ):
        if member.bot or not self.is_staff(member):
            return

        # BUG FIX: exclude AFK channel — staff parking in AFK shouldn't earn VC hours.
        afk_channel_id = member.guild.afk_channel.id if member.guild.afk_channel else None

        def _is_countable_channel(channel: Optional[discord.VoiceChannel]) -> bool:
            if channel is None:
                return False
            if afk_channel_id and channel.id == afk_channel_id:
                return False
            return True

        now = datetime.now()

        was_countable = _is_countable_channel(before.channel)
        is_countable = _is_countable_channel(after.channel)

        joined = not was_countable and is_countable
        left   = was_countable and not is_countable
        moved  = was_countable and is_countable and before.channel != after.channel
        # Staff moved into AFK — treat as a leave
        moved_to_afk = (
            _is_countable_channel(before.channel)
            and not _is_countable_channel(after.channel)
            and after.channel is not None
        )

        if joined:
            session = self.metrics.start_vc_session(member.id, after.channel.id, now)
            session_id = await self.db.start_vc_session(session)
            self.active_vc_session_ids[(member.id, after.channel.id)] = session_id

        elif left or moved_to_afk:
            key = (member.id, before.channel.id)
            if key in self.active_vc_session_ids:
                await self.db.end_vc_session(self.active_vc_session_ids.pop(key), now)

        elif moved:
            prev_key = (member.id, before.channel.id)
            if prev_key in self.active_vc_session_ids:
                await self.db.end_vc_session(self.active_vc_session_ids.pop(prev_key), now)
            session = self.metrics.start_vc_session(member.id, after.channel.id, now)
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
                # track_moderation now returns None on a dedup hit
                action = self.metrics.track_moderation(
                    moderator_id=interaction.user.id, target_id=target_id,
                    command=cmd, timestamp=datetime.now(), reason=reason,
                )
                if action:
                    await self.db.insert_moderation_action(action)

        elif cmd == "report":
            await self._process_report_command(interaction)



    async def _try_parse_mod_bot_embed(self, message: discord.Message) -> None:
        """
        Parses success embeds sent by external mod bots (Wick, Carl-bot, etc.)
        and records the underlying moderation action.

        Changes vs original:
          • Uses MODERATION_COMMAND_RE (word-boundary regex) instead of plain
            substring matching, which previously matched "ban" in "abandon",
            "lock" in "blocking", "warn" in "I warned you" etc.
          • track_moderation() now has a dedup guard — if on_app_command_completion
            already recorded this action within _MOD_DEDUP_TTL seconds, it returns
            None and we skip the DB write.
        """
        if not message.embeds:
            return

        SUCCESS_KEYWORDS = {
            "successfully", "success", "has been", "was banned",
            "was kicked", "was warned", "was muted", "action taken",
        }

        ACTION_MAP = {
            "banned": "ban", "ban": "ban",
            "kicked": "kick", "kick": "kick",
            "warned": "warn", "warn": "warn", "warning": "warn",
            "muted": "mute", "mute": "mute",
            "timed out": "timeout", "timeout": "timeout",
            "unmuted": "unmute", "unmute": "unmute",
            "unbanned": "unban", "unban": "unban",
            "strike": "strike", "softban": "softban",
            "purged": "purge", "purge": "purge", "cleared": "purge",
            "locked": "lock", "lock": "lock",
            "unlocked": "unlock", "unlock": "unlock",
            "slowmode": "slowmode",
        }

        for embed in message.embeds:
            embed_text = " ".join(filter(None, [
                embed.title or "",
                embed.description or "",
                *(f.value for f in embed.fields),
                embed.footer.text if embed.footer else "",
            ])).lower()

            if not any(kw in embed_text for kw in SUCCESS_KEYWORDS):
                continue

            # BUG FIX: use word-boundary regex so "warn" doesn't match "I warned",
            # "ban" doesn't match "abandon", "lock" doesn't match "blocking" etc.
            cmd_match = MODERATION_COMMAND_RE.search(embed_text)
            if not cmd_match:
                continue
            raw_cmd = cmd_match.group(0).lower().rstrip("dings").rstrip("e")  # rough stem
            cmd = ACTION_MAP.get(cmd_match.group(0).lower()) or ACTION_MAP.get(raw_cmd)
            if not cmd:
                continue

            raw_text = " ".join(filter(None, [
                embed.title or "", embed.description or "",
                *(f.value for f in embed.fields),
            ]))
            target_id = None
            mention_match = re.search(r"<@!?(\d{17,20})>", raw_text)
            if mention_match:
                target_id = int(mention_match.group(1))
            else:
                id_match = re.search(r"\b(\d{17,20})\b", raw_text)
                if id_match:
                    target_id = int(id_match.group(1))
            if not target_id:
                continue

            moderator_id = None
            reason       = None
            try:
                async for prev in message.channel.history(limit=8, before=message):
                    if prev.author.bot:
                        continue
                    if not self.is_staff(prev.author):
                        continue
                    prev_lower = prev.content.lower()
                    mentions_target = (
                        str(target_id) in prev.content
                        or f"<@{target_id}>" in prev.content
                        or f"<@!{target_id}>" in prev.content
                    )
                    # BUG FIX: use word-boundary check via compiled regex rather
                    # than plain `any(kw in prev_lower for kw in MODERATION_COMMANDS)`
                    has_mod_keyword = bool(MODERATION_COMMAND_RE.search(prev_lower))
                    if mentions_target and has_mod_keyword:
                        moderator_id = prev.author.id
                        reason_match = re.search(
                            rf"(?:<@!?{target_id}>|{target_id})\s+(.+)", prev.content
                        )
                        if reason_match:
                            reason = reason_match.group(1).strip()[:200] or None
                        break
            except discord.Forbidden:
                pass

            if not moderator_id:
                continue

            # track_moderation returns None if duplicate — no DB write needed
            action = self.metrics.track_moderation(
                moderator_id=moderator_id,
                target_id=target_id,
                command=cmd,
                timestamp=message.created_at.replace(tzinfo=None),
                reason=reason,
            )
            if action:
                await self.db.insert_moderation_action(action)
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
            timestamp          = datetime.now(),
        )
        if event:
            await self.db.insert_reaction(event)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        """Track reactions received on staff messages — credits the staff author."""
        if not payload.guild_id or payload.member is None or payload.member.bot:
            return
        channel = self.bot.get_channel(payload.channel_id)
        if not channel:
            return
        try:
            msg = await channel.fetch_message(payload.message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return
        # Only credit if the message author is a staff member
        if msg.author.bot or not self.is_staff(msg.author):
            return
        now = datetime.now()
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
            staff_id   = thread.owner_id,
            thread_id  = thread.id,
            channel_id = thread.parent_id or 0,
            event_type = "created",
            timestamp  = datetime.now(),
        )
        if event:
            await self.db.insert_thread_event(event)

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        pass  # Hook for future audit-log integration

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
            # ── New member chat boost ─────────────────────────────────────────
            target = message.guild.get_member(interaction.user_id)
            if target and self._member_age_days(target) <= NEW_MEMBER_THRESHOLD_DAYS:
                ev = EngagementEvent(
                    staff_id=staff_msg.staff_id, channel_id=staff_msg.channel_id,
                    message_id=staff_msg.message_id, event_type="new_member_chat",
                    user_id=interaction.user_id, timestamp=datetime.now(),
                )
                await self.db.insert_engagement_event(ev)

    async def _process_help_event(
        self, message: discord.Message, staff_msg: StaffMessage, is_new_member: bool = False
    ):
        from .constants import HELP_STRONG_RE, HELP_WEAK_RE

        content_lower = (message.content or "").lower()
        strong_hits = len(HELP_STRONG_RE.findall(content_lower))
        weak_hits   = len(HELP_WEAK_RE.findall(content_lower))

        # Pre-filter: skip channel-history fetch entirely if there's no keyword signal.
        # This is the fix for the ~4 800 unnecessary Discord API calls per day.
        has_any_signal = (
            strong_hits >= 1
            or weak_hits >= 2
            or bool(message.reference)  # replies always warrant a check
        )
        if not has_any_signal:
            return

        replied_to = None
        if message.reference and message.reference.resolved:
            if isinstance(message.reference.resolved, discord.Message):
                replied_to = {
                    "author":  message.reference.resolved.author.id,
                    "content": message.reference.resolved.content,
                }

        history: List[Dict] = []
        if strong_hits >= 1:  # only proactive branch needs history
            try:
                async for msg in message.channel.history(limit=5, before=message):
                    if not msg.author.bot:
                        history.append({
                            "author":    msg.author.id,
                            "content":   msg.content,
                            "timestamp": msg.created_at.replace(tzinfo=None),
                        })
            except Exception:
                pass

        ev = self.metrics.check_help_event(staff_msg, replied_to, history, is_new_member)
        if ev:
            await self.db.insert_help_event(ev)

    async def _process_report(self, message: discord.Message, staff_msg: StaffMessage):
        report = self.metrics.check_report(staff_msg, message.channel.id)
        if report:
            report.message_link = message.jump_url
            await self.db.insert_report(report)

    async def _process_report_command(self, interaction: discord.Interaction):
        staff_msg = StaffMessage(
            staff_id   = interaction.user.id,
            channel_id = interaction.channel.id if interaction.channel else 0,
            message_id = interaction.id,
            timestamp  = datetime.now(),
        )
        report = self.metrics.check_report(
            staff_msg,
            interaction.channel.id if interaction.channel else None,
            is_command=True,
        )
        if report:
            await self.db.insert_report(report)

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

        # Rank position among all staff
        all_staff = await self._fetch_all_staff(interaction.guild)
        rank_pos, total_staff = await self.db.get_staff_rank_position(
            member.id, [m.id for m in all_staff], days
        )
        stats.rank_position = rank_pos
        stats.total_staff   = total_staff

        tier      = stats.rank_tier
        tier_info = RANK_TIERS[tier.value]

        # ── Header
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
            timestamp=datetime.now(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)

        # ── Rank progress bar
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
            embed.add_field(
                name="▸ Max Rank Achieved",
                value=f"`{bar}` **{stats.engagement_score:.0f} pts** 💎",
                inline=False,
            )

        # ── Community field
        msg_trend  = format_trend_arrow(trends["messages"]["current"],  trends["messages"]["previous"])
        help_trend = format_trend_arrow(trends["help"]["current"],      trends["help"]["previous"])
        rev_trend  = format_trend_arrow(trends["dead_chat_revivals"]["current"],    trends["dead_chat_revivals"]["previous"])
        greet_trend= format_trend_arrow(trends["new_member_greetings"]["current"],  trends["new_member_greetings"]["previous"])
        comm_lines = (
            f"`Messages         ` **{format_number(stats.total_messages)}**  {msg_trend}\n"
            f"`Unique helped    ` **{stats.unique_members_helped}**  "
            f"  {format_kpi_status(stats.unique_members_helped, KPI_TARGETS['unique_members'])}\n"
            f"`Help events      ` **{stats.help_events}**  {help_trend}  "
            f"{format_kpi_status(stats.help_events, KPI_TARGETS['help_events'])}\n"
            f"`→ Proactive      ` **{stats.proactive_help}**\n"
            f"`→ New members 🌱 ` **{stats.new_members_helped}**  "
            f"{format_kpi_status(stats.new_members_helped, KPI_TARGETS['new_members_helped'])}\n"
            f"`Reactions given  ` **{stats.reactions_given}**\n"
            f"`Threads created  ` **{stats.threads_created}**"
        )
        embed.add_field(name="💬 Community", value=comm_lines, inline=True)

        # ── Engagement field (new)
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

        # ── Moderation field
        mod_trend = format_trend_arrow(trends["moderation"]["current"], trends["moderation"]["previous"])
        mod_lines = (
            f"`Mod actions      ` **{stats.moderation_actions}**  {mod_trend}\n"
            f"`Weighted severity` **{stats.moderation_severity_sum:.1f}**\n"
            f"`Reports filed    ` **{stats.reports_filed}**\n"
            f"`VC hours         ` **{stats.vc_hours:.1f}h**  "
            f"{format_kpi_status(stats.vc_hours, KPI_TARGETS['vc_hours'])}\n"
            f"`Conversations    ` **{stats.conversation_sessions}**\n"
            f"`Channels covered ` **{stats.channels_participated}**\n"
            f"`Server bumps     ` **{stats.bumps_performed}**"
        )
        embed.add_field(name="🛡️ Moderation & Presence", value=mod_lines, inline=True)

        embed.add_field(name="\u200b", value="\u200b", inline=True)   # spacer

        # ── Quality & Speed field
        fr_trend = format_trend_arrow(trends["first_response"]["current"], trends["first_response"]["previous"])
        ql_trend = format_trend_arrow(trends["quality"]["current"],        trends["quality"]["previous"])
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

        # ── Consistency field
        if consistency:
            active_h_count = consistency.active_hours_count
            most_active    = f"{consistency.most_active_hour:02d}:00" if consistency.most_active_hour is not None else "N/A"
            busiest_day    = consistency.busiest_day_name or "N/A"
            cons_lines = (
                f"`Current streak   ` **{consistency.current_streak}d** 🔥\n"
                f"`Longest streak   ` **{consistency.longest_streak}d**\n"
                f"`Active days      ` **{consistency.active_days}** / {days}  "
                f"{format_kpi_status(stats.active_days, KPI_TARGETS['active_days'])}\n"
                f"`Hour coverage    ` **{active_h_count}**/24 hrs\n"
                f"`Off-peak msgs    ` **{consistency.off_peak_activity}** 🦉\n"
                f"`Peak hour        ` **{most_active}**\n"
                f"`Busiest day      ` **{busiest_day}**"
            )
            embed.add_field(name="🔄 Consistency", value=cons_lines, inline=True)
            embed.add_field(name="\u200b", value="\u200b", inline=True)

            # ── Activity heatmap
            heatmap = create_activity_heatmap_compact(dict(consistency.active_hours))
            embed.add_field(
                name="📊 Activity Heatmap  (00–11 AM / 12–23 PM)",
                value=f"```\n{heatmap}\n```",
                inline=False,
            )

        # ── Achievements
        achievements = stats.earned_achievements
        if achievements:
            embed.add_field(
                name=f"🏅 Achievements  ({len(achievements)})",
                value="  ".join(achievements[:8]) + ("…" if len(achievements) > 8 else ""),
                inline=False,
            )

        embed.set_footer(text=f"Last {days} days  ·  🟢 ≥80% KPI  🟡 ≥50%  🟠 ≥30%  🔴 <30%  ·  Trends vs prev {days//7 or 1}w")
        await interaction.followup.send(embed=embed)

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
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.choices(metric=[
        app_commands.Choice(name="⭐ Engagement Score",      value="score"),
        app_commands.Choice(name="🎙️ VC Hours",              value="vc_hours"),
        app_commands.Choice(name="🛡️ Moderation (weighted)", value="moderation"),
        app_commands.Choice(name="🤝 Help Events",           value="help"),
        app_commands.Choice(name="📋 Reports Filed",         value="reports"),
        app_commands.Choice(name="💬 Total Messages",        value="messages"),
        app_commands.Choice(name="😀 Reactions Given",       value="reactions"),
        app_commands.Choice(name="🧵 Threads Created",       value="threads"),
        app_commands.Choice(name="🚀 First Responses",       value="first_response"),
        app_commands.Choice(name="✨ Quality Messages",      value="quality"),
        app_commands.Choice(name="🌱 New Members Helped",    value="new_members"),
        app_commands.Choice(name="💡 Conversation Starters", value="conversation_starters"),
        app_commands.Choice(name="👋 New Member Greetings",  value="new_member_greetings"),
        app_commands.Choice(name="🔋 Dead Chat Revivals",    value="dead_chat_revivals"),
        app_commands.Choice(name="⭐ Reactions Received",    value="reactions_received"),
    ])
    async def staffleaderboard(
        self, interaction: discord.Interaction,
        metric: app_commands.Choice[str], days: int = 30,
    ):
        await interaction.response.defer(ephemeral=True)

        if metric.value == "score":
            staff = await self._fetch_all_staff(interaction.guild)
            weights = await self.db.get_score_weights(interaction.guild.id)
            rows = []
            for m in staff[:30]:
                s = await self.db.get_staff_stats(m.id, days, weights)
                c = await self.db.get_consistency_stats(m.id)
                if c:
                    s.consistency = c
                rows.append((m.id, s.engagement_score))
            rows.sort(key=lambda x: x[1], reverse=True)
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
            "help":                  "🤝 Help Events",
            "reports":               "📋 Reports Filed",
            "messages":              "💬 Total Messages",
            "reactions":             "😀 Reactions Given",
            "threads":               "🧵 Threads Created",
            "first_response":        "🚀 First Responses",
            "quality":               "✨ Avg Quality Score",
            "new_members":           "🌱 New Members Helped",
            "conversation_starters": "💡 Conversation Starters",
            "new_member_greetings":  "👋 New Member Greetings",
            "dead_chat_revivals":    "🔋 Dead Chat Revivals",
            "reactions_received":    "⭐ Reactions Received",
        }

        embed = discord.Embed(
            title=f"🏆 Leaderboard — {metric_labels.get(metric.value, metric.value)}",
            color=EMBED_COLORS["primary"],
            timestamp=datetime.now(),
        )
        embed.set_footer(text=f"Last {days} days")

        top_val = rows[0][1] if rows else 1
        lines = []
        for idx, (staff_id, value) in enumerate(rows[:LEADERBOARD_LIMIT], 1):
            member = await get_member_safe(interaction.guild, staff_id)
            name   = member.display_name if member else f"Unknown ({staff_id})"

            # Medal / position prefix
            if idx <= 3:
                prefix = MEDAL_EMOJIS[idx - 1]
            else:
                prefix = f"`{idx:>2}.`"

            # Value formatting
            if metric.value == "vc_hours":
                val_str = f"**{value:.1f}h**"
            elif metric.value == "quality":
                val_str = f"**{value:.2f}** avg"
            elif metric.value == "score":
                tier    = RankTier.from_score(value)
                val_str = f"{tier.display.split()[0]} **{value:.0f}**"
            else:
                val_str = f"**{format_number(value)}**"

            # Bar showing proportion of top
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

        weights = await self.db.get_score_weights(interaction.guild.id)
        s1 = await self.db.get_staff_stats(member1.id, days, weights)
        s2 = await self.db.get_staff_stats(member2.id, days, weights)
        for s, m in ((s1, member1), (s2, member2)):
            c = await self.db.get_consistency_stats(m.id)
            if c:
                s.consistency = c

        streak1 = s1.consistency.current_streak if s1.consistency else 0
        streak2 = s2.consistency.current_streak if s2.consistency else 0

        rows = [
            ("⭐ Score",         s1.engagement_score,      s2.engagement_score),
            ("💬 Messages",      s1.total_messages,         s2.total_messages),
            ("👥 Unique helped", s1.unique_members_helped,  s2.unique_members_helped),
            ("🤝 Help events",   s1.help_events,            s2.help_events),
            ("🌱 New members",   s1.new_members_helped,     s2.new_members_helped),
            ("🛡️ Mod actions",   s1.moderation_actions,     s2.moderation_actions),
            ("⚖️ Mod severity",  s1.moderation_severity_sum,s2.moderation_severity_sum),
            ("🎙️ VC hours",      s1.vc_hours,               s2.vc_hours),
            ("📋 Reports",       s1.reports_filed,          s2.reports_filed),
            ("😀 Reactions",     s1.reactions_given,        s2.reactions_given),
            ("🧵 Threads",       s1.threads_created,        s2.threads_created),
            ("🚀 First resp.",   s1.first_responses,        s2.first_responses),
            ("✨ Quality msgs",  s1.quality_events,         s2.quality_events),
            ("🔥 Streak",        streak1,                   streak2),
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
            timestamp=datetime.now(),
        )
        embed.set_footer(text=f"Last {days} days")

        col_label, col1, col2 = [], [], []
        for label, v1, v2 in rows:
            lbl, c1, c2 = build_comparison_table(label, v1, v2)
            col_label.append(lbl)
            col1.append(c1)
            col2.append(c2)

        embed.add_field(name="Metric",                         value="\n".join(col_label), inline=True)
        embed.add_field(name=member1.display_name[:20],        value="\n".join(col1),      inline=True)
        embed.add_field(name=member2.display_name[:20],        value="\n".join(col2),      inline=True)

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

        weights = await self.db.get_score_weights(interaction.guild.id)
        buckets: Dict[str, List[str]] = {name: [] for name in RANK_TIERS}
        scores_map: Dict[str, float] = {}
        for member in staff:
            s = await self.db.get_staff_stats(member.id, days, weights)
            c = await self.db.get_consistency_stats(member.id)
            if c:
                s.consistency = c
            buckets[s.rank_tier.value].append(member.display_name)
            scores_map[member.display_name] = s.engagement_score

        total = len(staff)
        embed = discord.Embed(
            title="🏅 Staff Rank Distribution",
            color=EMBED_COLORS["primary"],
            timestamp=datetime.now(),
        )
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
            timestamp=datetime.now(),
        )

        type_icons = {
            "message":        "💬",
            "moderation":     "🛡️",
            "voice":          "🎙️",
            "first_response": "🚀",
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

    # ─── /staffconsistency ───────────────────────────────────────────────────

    @app_commands.command(name="staffconsistency", description="Consistency & streak leaderboard")
    @app_commands.default_permissions(manage_guild=True)
    async def staffconsistency(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        staff = await self._fetch_all_staff(interaction.guild)
        if not staff:
            await interaction.followup.send(embed=create_info_embed("No Staff", "No staff members found."))
            return

        scores = []
        for member in staff:
            c = await self.db.get_consistency_stats(member.id)
            if c:
                scores.append((member, c.consistency_score, c.current_streak, c.longest_streak, c.off_peak_activity))
        scores.sort(key=lambda x: x[1], reverse=True)

        embed = discord.Embed(
            title="🔄 Consistency Leaderboard",
            color=EMBED_COLORS["primary"],
            timestamp=datetime.now(),
        )
        embed.set_footer(text="Rewards daily activity, time-spread, off-peak coverage, and streaks")

        lines = []
        for idx, (member, score, streak, longest, off_peak) in enumerate(scores[:12], 1):
            prefix = MEDAL_EMOJIS[idx - 1] if idx <= 3 else f"`{idx:>2}.`"
            streak_badge = f"🔥{streak}d" if streak >= 3 else f"{streak}d"
            off_peak_badge = f"🦉{off_peak}" if off_peak > 0 else ""
            lines.append(
                f"{prefix} **{member.display_name}** — `{score:.0f} pts`  "
                f"{streak_badge}  best:{longest}d  {off_peak_badge}"
            )
        embed.description = "\n".join(lines) or "No consistency data yet."
        await interaction.followup.send(embed=embed)

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
        stats = await self.db.get_staff_stats(target.id, 9999, weights=guild_weights)  # All-time
        consistency = await self.db.get_consistency_stats(target.id)
        if consistency:
            stats.consistency = consistency

        streak = consistency.current_streak if consistency else 0
        metric_map = {
            "help_events":         stats.help_events,
            "moderation_actions":  stats.moderation_actions,
            "vc_hours":            stats.vc_hours,
            "new_members_helped":  stats.new_members_helped,
            "fast_responses":      stats.fast_responses,
            "first_responses":     stats.first_responses,
            "threads_created":     stats.threads_created,
            "reactions_given":     stats.reactions_given,
            "current_streak":      streak,
            "off_peak_messages":   consistency.off_peak_activity if consistency else 0,
        }

        earned_lines = []
        locked_lines = []
        for key, info in ACHIEVEMENT_THRESHOLDS.items():
            val    = metric_map.get(info["metric"], 0)
            target_val = info["value"]
            if val >= target_val:
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
            timestamp=datetime.now(),
        )
        embed.set_thumbnail(url=target.display_avatar.url)

        if earned_lines:
            embed.add_field(
                name=f"✅ Earned ({len(earned_lines)})",
                value="\n".join(earned_lines),
                inline=False,
            )
        if locked_lines:
            embed.add_field(
                name=f"🔒 In Progress ({len(locked_lines)})",
                value="\n".join(locked_lines[:10]),
                inline=False,
            )

        embed.set_footer(text="Achievements are based on all-time statistics")
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

        health = await self.db.get_server_health([m.id for m in staff], days)
        inactive_ids = await self.db.get_inactive_staff([m.id for m in staff], days)

        active_pct = (health["active_staff"] / health["total_staff"] * 100) if health["total_staff"] else 0
        avg_rt_str = format_seconds(health["avg_response_secs"]) if health["avg_response_secs"] else "N/A"

        embed = discord.Embed(
            title=f"📊 Staff Health Dashboard — {interaction.guild.name}",
            description=f"**Last {days} days** · {health['total_staff']} total staff",
            color=EMBED_COLORS["primary"],
            timestamp=datetime.now(),
        )

        # Coverage
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

        # Core KPIs
        embed.add_field(
            name="📈 Community KPIs",
            value=(
                f"`Total messages   ` **{format_number(health['total_messages'])}**\n"
                f"`Help events      ` **{format_number(health['total_help'])}**\n"
                f"`New members helped` **{health['new_members_helped']}** 🌱\n"
                f"`First responses  ` **{health['first_responses']}** 🚀"
            ),
            inline=True,
        )

        embed.add_field(
            name="🛡️ Operations",
            value=(
                f"`Mod actions      ` **{health['total_moderation']}**\n"
                f"`Avg response time` **{avg_rt_str}**\n"
                f"`Total VC hours   ` **{health['vc_hours']:.1f}h**"
            ),
            inline=True,
        )

        # Top performers this period
        weights = await self.db.get_score_weights(interaction.guild.id)
        stats_list = []
        for m in staff:
            s = await self.db.get_staff_stats(m.id, days, weights)
            c = await self.db.get_consistency_stats(m.id)
            if c:
                s.consistency = c
            stats_list.append((m, s))
        stats_list.sort(key=lambda x: x[1].engagement_score, reverse=True)

        top_lines = []
        for idx, (m, s) in enumerate(stats_list[:5], 1):
            streak = s.consistency.current_streak if s.consistency else 0
            top_lines.append(
                f"`{idx}.` **{m.display_name}** {s.rank_tier.display.split()[0]} "
                f"· **{s.engagement_score:.0f}** pts  "
                f"💬{s.total_messages} 🤝{s.help_events} 🔥{streak}d"
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

    # ─── /staffnotes group ────────────────────────────────────────────────────

    notes_group = app_commands.Group(name="staffnote", description="Manage staff performance notes")

    @notes_group.command(name="add", description="Add a note to a staff member")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(member="Staff member", note="Note content", positive="Positive note?")
    async def staffnote_add(
        self, interaction: discord.Interaction,
        member: discord.Member, note: str, positive: bool = True,
    ):
        if not self.is_staff(member):
            await interaction.response.send_message(
                embed=create_error_embed("Not Staff", f"{member.mention} is not a staff member."), ephemeral=True
            )
            return
        sn = StaffNote(
            id=None, staff_id=member.id, author_id=interaction.user.id,
            note=note, created_at=datetime.now(), is_positive=positive,
        )
        note_id = await self.db.add_staff_note(sn)
        emoji = "✅" if positive else "📌"
        await interaction.response.send_message(
            embed=create_success_embed(
                "Note Added",
                f"{emoji} Note **#{note_id}** added for **{member.display_name}**.\n> {note}"
            ),
            ephemeral=True,
        )

    @notes_group.command(name="view", description="View notes for a staff member")
    async def staffnote_view(self, interaction: discord.Interaction, member: discord.Member):
        notes = await self.db.get_staff_notes(member.id, limit=10)
        if not notes:
            await interaction.response.send_message(
                embed=create_info_embed("No Notes", f"No notes recorded for **{member.display_name}**."),
                ephemeral=True,
            )
            return
        embed = discord.Embed(
            title=f"📝 Notes — {member.display_name}",
            color=EMBED_COLORS["info"],
            timestamp=datetime.now(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        for n in notes:
            author = self.bot.get_user(n.author_id)
            author_name = author.display_name if author else f"ID:{n.author_id}"
            emoji = "✅" if n.is_positive else "📌"
            embed.add_field(
                name=f"{emoji} Note #{n.id}  ·  {author_name}  ·  {format_relative_time(n.created_at)}",
                value=n.note[:512],
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @notes_group.command(name="delete", description="Delete a staff note by ID")
    @app_commands.default_permissions(manage_guild=True)
    async def staffnote_delete(self, interaction: discord.Interaction, note_id: int):
        deleted = await self.db.delete_staff_note(note_id, interaction.user.id)
        if deleted:
            await interaction.response.send_message(
                embed=create_success_embed("Note Deleted", f"Note **#{note_id}** has been removed."), ephemeral=True
            )
        else:
            await interaction.response.send_message(
                embed=create_error_embed("Not Found", f"Note **#{note_id}** does not exist."), ephemeral=True
            )

    # ─── /staffweights group ──────────────────────────────────────────────────

    weights_group = app_commands.Group(name="staffweights", description="Manage per-guild scoring weights")

    @weights_group.command(name="view", description="View current scoring weights")
    async def staffweights_view(self, interaction: discord.Interaction):
        weights = await self.db.get_score_weights(interaction.guild.id)
        lines = []
        for k, v in sorted(weights.items()):
            default = SCORE_WEIGHTS.get(k, v)
            custom_tag = "  *(custom)*" if v != default else ""
            lines.append(f"`{k:<25}` **{v}**{custom_tag}")
        embed = discord.Embed(
            title="⚙️ Scoring Weights",
            description="\n".join(lines),
            color=EMBED_COLORS["primary"],
        )
        embed.set_footer(text="Use /staffweights set to override  ·  /staffweights reset to restore defaults")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @weights_group.command(name="set", description="Override a scoring weight")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(key="Weight key (see /staffweights view)", value="New weight value (≥ 0)")
    async def staffweights_set(
        self, interaction: discord.Interaction, key: str, value: float
    ):
        if key not in SCORE_WEIGHTS:
            valid = ", ".join(f"`{k}`" for k in sorted(SCORE_WEIGHTS))
            await interaction.response.send_message(
                embed=create_error_embed("Invalid Key", f"Valid keys:\n{valid}"), ephemeral=True
            )
            return
        if value < 0:
            await interaction.response.send_message(
                embed=create_error_embed("Invalid Value", "Weight value must be ≥ 0."), ephemeral=True
            )
            return
        await self.db.set_score_weight(interaction.guild.id, key, value, interaction.user.id)
        await interaction.response.send_message(
            embed=create_success_embed("Weight Updated", f"`{key}` → **{value}**"), ephemeral=True
        )

    @weights_group.command(name="reset", description="Reset all weights to defaults")
    @app_commands.default_permissions(administrator=True)
    async def staffweights_reset(self, interaction: discord.Interaction):
        await self.db.reset_score_weights(interaction.guild.id)
        await interaction.response.send_message(
            embed=create_success_embed("Weights Reset", "All scoring weights have been reset to defaults."),
            ephemeral=True,
        )

    # ─── /staffconfig ─────────────────────────────────────────────────────────

    @app_commands.command(name="staffconfig", description="View staff analytics configuration")
    @app_commands.default_permissions(administrator=True)
    async def staffconfig(self, interaction: discord.Interaction):
        embed = discord.Embed(title="⚙️ Staff Analytics Config", color=EMBED_COLORS["primary"])

        role_mentions = []
        for role_id in self.config.staff_roles:
            role = interaction.guild.get_role(role_id)
            if role:
                role_mentions.append(role.mention)
        embed.add_field(
            name="👥 Staff Roles",
            value="\n".join(role_mentions) or "None configured  *(set MOD_ROLES env var)*",
            inline=False,
        )

        channel_names = ["trial_chat", "mod_log", "staff_chat", "alert_channel", "weekly_report_channel", "dashboard_channel"]
        ch_lines = []
        for name in channel_names:
            ch_id = self.config.get_channel_id(name)
            if ch_id:
                ch_obj = interaction.guild.get_channel(ch_id)
                ch_lines.append(f"• **{name}**: {ch_obj.mention if ch_obj else ch_id}")
        embed.add_field(
            name="📺 Tracked Channels",
            value="\n".join(ch_lines) or "None configured",
            inline=False,
        )

        settings_lines = [
            f"• Inactivity threshold: **{self.config.get_setting('inactivity_days')}d**",
            f"• Min VC session: **{self.config.get_setting('min_vc_session')}s**",
            f"• Report cooldown: **{self.config.get_setting('report_cooldown')}s**",
            f"• Data retention: **{self.config.get_setting('db_retention_days')}d**",
            f"• Response time window: **{self.config.get_setting('response_time_window')}s**",
            f"• New member threshold: **{self.config.get_setting('new_member_threshold_days')}d**",
        ]
        embed.add_field(name="⚙️ Settings", value="\n".join(settings_lines), inline=False)
        embed.set_footer(text="Override settings via environment variables  ·  See docs for full list")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ─── /staffdashboard ──────────────────────────────────────────────────────

    @app_commands.command(name="staffdashboard", description="Post or refresh the live staff dashboard (Admin only)")
    @app_commands.default_permissions(manage_guild=True)
    async def staffdashboard(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        channel_id = self.config.get_channel_id("dashboard_channel")
        if not channel_id:
            await interaction.followup.send(embed=create_error_embed(
                "Not Configured",
                "Set the `DASHBOARD_CHANNEL_ID` environment variable to enable the dashboard.",
            ))
            return
        try:
            await self._update_dashboard()
            channel = interaction.guild.get_channel(channel_id)
            ch_mention = channel.mention if channel else f"<#{channel_id}>"
            await interaction.followup.send(embed=create_success_embed(
                "Dashboard Updated", f"Dashboard refreshed in {ch_mention}."
            ))
        except Exception as e:
            await interaction.followup.send(embed=create_error_embed("Update Failed", str(e)))

    # ─── /staffexport ─────────────────────────────────────────────────────────

    @app_commands.command(name="staffexport", description="Export staff data as JSON (Admin only)")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(member="Staff member to export", days="Days to include (default 30)")
    async def staffexport(
        self, interaction: discord.Interaction, member: discord.Member, days: int = 30
    ):
        await interaction.response.defer(ephemeral=True)
        if not self.is_staff(member):
            await interaction.followup.send(
                embed=create_error_embed("Not Staff", f"{member.mention} is not a staff member.")
            )
            return

        data = await self.db.export_staff_data(member.id, days)
        filename = f"staff_export_{member.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

        # BUG FIX: original wrote to a local file path without cleanup, leaking
        # temp files on disk and using an unsafe filename derived from user input.
        # Now streams directly to an in-memory buffer — no disk I/O, no cleanup needed.
        buf = io.BytesIO(json.dumps(data, indent=2, default=str).encode("utf-8"))
        buf.seek(0)
        await interaction.followup.send(
            file=discord.File(buf, filename=filename),
            content=f"📊 Export for **{member.display_name}** (last {days} days)",
        )
    # ─── Background tasks ─────────────────────────────────────────────────────

    async def _weekly_report_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            now = datetime.now()
            is_monday_9am = now.weekday() == 0 and now.hour == 9

            if is_monday_9am:
                # BUG FIX: original used sleep(86400) after sending, but a bot
                # restart at exactly 09:xx on Monday would re-evaluate the condition
                # as true and send a second report.  We now gate on a date comparison.
                already_sent_today = (
                    self._last_weekly_report is not None
                    and self._last_weekly_report.date() == now.date()
                )
                if not already_sent_today:
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

        all_staff: List[discord.Member] = []
        for guild in self.bot.guilds:
            for m in guild.members:
                if self.is_staff(m):
                    all_staff.append(m)

        if not all_staff:
            return

        guild   = self.bot.guilds[0]
        weights = await self.db.get_score_weights(guild.id)
        health  = await self.db.get_server_health([m.id for m in all_staff], 7)
        inactive_ids = await self.db.get_inactive_staff([m.id for m in all_staff], 7)

        embed = discord.Embed(
            title       = "📊 Weekly Staff Report",
            description = f"Week ending **{datetime.now().strftime('%B %d, %Y')}**",
            color       = EMBED_COLORS["primary"],
            timestamp   = datetime.now(),
        )

        stats_list = []
        for member in all_staff[:25]:
            s = await self.db.get_staff_stats(member.id, 7, weights)
            c = await self.db.get_consistency_stats(member.id)
            if c:
                s.consistency = c
            stats_list.append((member, s))
        stats_list.sort(key=lambda x: x[1].engagement_score, reverse=True)

        top_lines = []
        for idx, (member, s) in enumerate(stats_list[:5], 1):
            prefix = MEDAL_EMOJIS[idx - 1] if idx <= 3 else f"{idx}."
            streak = s.consistency.current_streak if s.consistency else 0
            top_lines.append(
                f"{prefix} **{member.display_name}** {s.rank_tier.display.split()[0]} "
                f"— **{s.engagement_score:.0f}** pts  "
                f"💬{s.total_messages} 🤝{s.help_events} 🛡️{s.moderation_actions} 🔥{streak}d"
            )
        if top_lines:
            embed.add_field(name="🏆 Top Performers", value="\n".join(top_lines), inline=False)

        active_pct = (health["active_staff"] / health["total_staff"] * 100) if health["total_staff"] else 0
        embed.add_field(
            name  = "📈 7-Day Server Summary",
            value = (
                f"`Staff active      ` **{health['active_staff']}/{health['total_staff']}** ({active_pct:.0f}%)\n"
                f"`Help events       ` **{health['total_help']}**\n"
                f"`Mod actions       ` **{health['total_moderation']}**\n"
                f"`VC hours          ` **{health['vc_hours']:.1f}h**\n"
                f"`New members helped` **{health['new_members_helped']}** 🌱\n"
                f"`First responses   ` **{health['first_responses']}** 🚀\n"
                f"`Avg response time ` **{format_seconds(health['avg_response_secs'])}**"
            ),
            inline=False,
        )

        if inactive_ids:
            inactive = [m for m in all_staff if m.id in inactive_ids]
            embed.add_field(
                name  = f"⚠️ Inactive 7+ Days ({len(inactive)})",
                value = ", ".join(m.display_name for m in inactive[:10])
                        + (f" +{len(inactive) - 10}" if len(inactive) > 10 else ""),
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
            # Also prune stale staff-cache entries
            now = datetime.now()
            self._staff_cache = {
                uid: (res, ts)
                for uid, (res, ts) in self._staff_cache.items()
                if (now - ts).total_seconds() < _STAFF_CACHE_TTL * 4
            }
            await asyncio.sleep(3600)



    # ─── Notification loop ────────────────────────────────────────────────────

    async def _notification_loop(self):
        """Runs every 30 minutes. Checks every staff member for:
          - Rank-up since last check → DM congratulations
          - Streak about to break (active today but not yet today AND streak > 0) → DM warning
          - Milestone achievements newly earned → DM
        """
        await self.bot.wait_until_ready()
        await asyncio.sleep(120)   # give DB time to settle on startup
        while not self.bot.is_closed():
            try:
                await self._run_notifications()
            except Exception:
                logger.exception("Notification loop error")
            await asyncio.sleep(1800)   # 30 min

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
                    logger.debug("Notification check failed for %d: %s", member.id, exc_info=True)

    async def _check_member_notifications(self, member: discord.Member, weights: Dict):
        stats  = await self.db.get_staff_stats(member.id, 30, weights)
        cons   = await self.db.get_consistency_stats(member.id)
        if cons:
            stats.consistency = cons

        now      = datetime.now()
        today    = now.date().isoformat()
        tier     = stats.rank_tier.value
        streak   = cons.current_streak if cons else 0

        # ── Rank-up notification ──────────────────────────────────────────────
        prev_tier = self._last_known_rank.get(member.id)
        if prev_tier is not None and prev_tier != tier:
            tier_order = ["Bronze", "Silver", "Gold", "Platinum", "Diamond"]
            if tier_order.index(tier) > tier_order.index(prev_tier):
                await self._dm_rank_up(member, prev_tier, tier, stats)
        self._last_known_rank[member.id] = tier

        # ── Streak warning ────────────────────────────────────────────────────
        warn_threshold = self.config.get_setting("streak_warn_days", 1)
        if (
            streak >= 3                                      # only warn if streak is worth protecting
            and member.id not in self._streak_warned_today.get(today, set())
        ):
            # Check if they've sent any message today yet
            cutoff_today = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
            row = await (await self.db.pool.execute(
                "SELECT COUNT(*) as c FROM staff_messages WHERE staff_id=? AND timestamp>?",
                (member.id, cutoff_today),
            )).fetchone()
            active_today = (row["c"] or 0) > 0

            # Warn if it's late in the day (after 8pm) and they haven't been active
            if not active_today and now.hour >= 20:
                self._streak_warned_today.setdefault(today, set()).add(member.id)
                await self._dm_streak_warning(member, streak)

        # ── Achievement notifications ─────────────────────────────────────────
        prev_streak = self._last_known_streak.get(member.id, 0)
        self._last_known_streak[member.id] = streak
        # Check for newly-crossed achievement thresholds this loop tick
        await self._check_achievement_unlocks(member, stats, prev_streak)

    async def _dm_rank_up(self, member: discord.Member, from_tier: str, to_tier: str, stats):
        if not self.config.dm_notifications_enabled:
            return
        from_info = RANK_TIERS[from_tier]
        to_info   = RANK_TIERS[to_tier]
        embed = discord.Embed(
            title       = f"🎉 Rank Up! {to_info['emoji']} {to_tier}",
            description = (
                f"You've ranked up from **{from_info['emoji']} {from_tier}** "
                f"to **{to_info['emoji']} {to_tier}** — {to_info['label']}!\n\n"
                f"**Score:** `{stats.engagement_score:.0f} pts`"
            ),
            color     = to_info["colour"],
            timestamp = datetime.now(),
        )
        embed.add_field(
            name  = "📊 This month",
            value = (
                f"🤝 **{stats.help_events}** help events\n"
                f"🛡️ **{stats.moderation_actions}** mod actions\n"
                f"🔥 **{stats.consistency.current_streak if stats.consistency else 0}d** streak"
            ),
            inline = True,
        )
        next_tier_name, next_threshold = self._next_tier(stats.engagement_score)
        if next_tier_name:
            next_info = RANK_TIERS[next_tier_name]
            needed = next_threshold - stats.engagement_score
            embed.add_field(
                name  = f"🎯 Next: {next_info['emoji']} {next_tier_name}",
                value = f"`{needed:.0f} pts` to go",
                inline = True,
            )
        embed.set_footer(text="Keep it up! Use /mystats to track your progress.")
        try:
            await member.send(embed=embed)
        except discord.Forbidden:
            pass   # DMs closed — silently skip

    async def _dm_streak_warning(self, member: discord.Member, streak: int):
        if not self.config.dm_notifications_enabled:
            return
        embed = discord.Embed(
            title       = f"⚠️ Streak at risk — {streak} days",
            description = (
                f"Your **{streak}-day activity streak** will reset at midnight "
                f"if you don't send a message in the server today.\n\n"
                f"Even a single message keeps it alive. 🔥"
            ),
            color     = EMBED_COLORS["warning"],
            timestamp = datetime.now(),
        )
        embed.set_footer(text="This reminder only fires after 8 PM when you haven't been active.")
        try:
            await member.send(embed=embed)
        except discord.Forbidden:
            pass

    async def _check_achievement_unlocks(self, member: discord.Member, stats, prev_streak: int):
        """DM staff when they cross an achievement threshold for the first time ever."""
        if not self.config.dm_notifications_enabled:
            return
        streak = stats.consistency.current_streak if stats.consistency else 0
        metric_map = {
            "help_events":           stats.help_events,
            "moderation_actions":    stats.moderation_actions,
            "vc_hours":              stats.vc_hours,
            "new_members_helped":    stats.new_members_helped,
            "fast_responses":        stats.fast_responses,
            "first_responses":       stats.first_responses,
            "threads_created":       stats.threads_created,
            "reactions_given":       stats.reactions_given,
            "current_streak":        streak,
            "off_peak_messages":     stats.off_peak_messages,
            "conversation_starters": stats.conversation_starters,
            "new_member_greetings":  stats.new_member_greetings,
            "dead_chat_revivals":    stats.dead_chat_revivals,
            "reactions_received":    stats.reactions_received,
        }
        for key, info in ACHIEVEMENT_THRESHOLDS.items():
            val = metric_map.get(info["metric"], 0)
            if val >= info["value"]:
                # DB check — survives restarts
                if not await self.db.has_notified_achievement(member.id, key):
                    await self.db.mark_achievement_notified(member.id, key)
                    await self._dm_achievement(member, info)

    async def _dm_achievement(self, member: discord.Member, info: dict):
        embed = discord.Embed(
            title       = f"{info['emoji']} Achievement Unlocked!",
            description = f"**{info['desc']}**",
            color       = EMBED_COLORS["success"],
            timestamp   = datetime.now(),
        )
        embed.set_footer(text="Use /staffachievements to see all your badges.")
        try:
            await member.send(embed=embed)
        except discord.Forbidden:
            pass

    # ─── Dashboard loop ───────────────────────────────────────────────────────

    async def _dashboard_loop(self):
        """Posts and continuously updates a live staff dashboard embed in
        DASHBOARD_CHANNEL_ID.  Refreshes every DASHBOARD_UPDATE_INTERVAL seconds
        (default 1 hour).  On first run it posts a new message and pins it;
        on subsequent runs it edits the existing message in-place so the channel
        stays clean.
        """
        await self.bot.wait_until_ready()
        await asyncio.sleep(180)   # let everything settle first
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
                    # Try to fetch the exact message by ID
                    msg = await channel.fetch_message(msg_id)
                    await msg.edit(embed=embed)
                    logger.info(f"Dashboard updated for guild {guild.id}")
                    continue
                except discord.NotFound:
                    # Message was deleted - remove from cache and create new one
                    logger.info(f"Dashboard message {msg_id} not found in guild {guild.id}, creating new one")
                    self._dashboard_message_ids.pop(guild.id, None)
                except discord.Forbidden:
                    logger.warning(f"No permission to fetch/edit dashboard in guild {guild.id}")
                    continue
                except discord.HTTPException as e:
                    logger.warning(f"HTTP error updating dashboard in guild {guild.id}: {e}")
                    continue

            # If we get here, either no cached ID or message was deleted
            # Check if there's already a pinned dashboard message we can use
            try:
                async for message in channel.history(limit=20):
                    if message.author == self.bot.user and message.pinned and message.embeds:
                        # Found an existing pinned dashboard message
                        if "Staff Dashboard" in (message.embeds[0].title or ""):
                            await message.edit(embed=embed)
                            self._dashboard_message_ids[guild.id] = message.id
                            logger.info(f"Found and updated existing dashboard message in guild {guild.id}")
                            return
            except Exception as e:
                logger.warning(f"Error searching for existing dashboard: {e}")

            # Post fresh
            try:
                msg = await channel.send(embed=embed)
                await msg.pin()
                self._dashboard_message_ids[guild.id] = msg.id
                logger.info(f"Created new dashboard message in guild {guild.id}")
            except discord.Forbidden:
                logger.warning(f"Cannot send/pin message in dashboard channel for guild {guild.id}")
            except discord.HTTPException as e:
                logger.warning(f"HTTP error posting dashboard in guild {guild.id}: {e}")

    async def _build_dashboard_embed(self, guild: discord.Guild) -> Optional[discord.Embed]:
        staff = [m for m in guild.members if self.is_staff(m)]
        if not staff:
            return None

        weights  = await self.db.get_score_weights(guild.id)
        health   = await self.db.get_server_health([m.id for m in staff], 7)
        inactive_ids = await self.db.get_inactive_staff([m.id for m in staff], 7)

        # Build stats for all staff
        stats_list = []
        for member in staff:
            s = await self.db.get_staff_stats(member.id, 30, weights)
            c = await self.db.get_consistency_stats(member.id)
            if c:
                s.consistency = c
            stats_list.append((member, s))
        stats_list.sort(key=lambda x: x[1].engagement_score, reverse=True)

        now        = datetime.now()
        active_pct = (health["active_staff"] / health["total_staff"] * 100) if health["total_staff"] else 0

        embed = discord.Embed(
            title       = f"📊 Staff Dashboard — {guild.name}",
            description = (
                f"**Live leaderboard** · Last updated <t:{int(now.timestamp())}:R>\n"
                f"Scores reflect the last **30 days**  ·  Health stats reflect last **7 days**"
            ),
            color     = EMBED_COLORS["primary"],
            timestamp = now,
        )

        # ── Top performers ────────────────────────────────────────────────────
        podium_lines = []
        for idx, (member, s) in enumerate(stats_list[:10], 1):
            streak     = s.consistency.current_streak if s.consistency else 0
            tier_emoji = RANK_TIERS[s.rank_tier.value]["emoji"]
            prefix     = MEDAL_EMOJIS[idx - 1] if idx <= 3 else f"`{idx:>2}.`"
            streak_str = f" 🔥{streak}d" if streak >= 3 else ""
            podium_lines.append(
                f"{prefix} {tier_emoji} **{member.display_name}**"
                f"  `{s.engagement_score:.0f}`"
                f"  💬{s.total_messages} 🤝{s.help_events} 🛡️{s.moderation_actions}"
                f"{streak_str}"
            )
        embed.add_field(
            name   = "🏆 Leaderboard (30d score)",
            value  = "\n".join(podium_lines) or "No data yet.",
            inline = False,
        )

        # ── 7-day server health ───────────────────────────────────────────────
        coverage_bar = create_progress_bar(health["active_staff"], health["total_staff"], length=10)
        avg_rt_str   = format_seconds(health["avg_response_secs"]) if health["avg_response_secs"] else "N/A"
        embed.add_field(
            name  = "📈 7-Day Health",
            value = (
                f"`Coverage ` `{coverage_bar}` **{health['active_staff']}/{health['total_staff']}** ({active_pct:.0f}%)\n"
                f"`Help     ` **{health['total_help']}** events  ·  "
                f"`Mod      ` **{health['total_moderation']}** actions\n"
                f"`Response ` avg **{avg_rt_str}**  ·  "
                f"`VC       ` **{health['vc_hours']:.1f}h**\n"
                f"`🌱 New   ` **{health['new_members_helped']}** helped  ·  "
                f"`🚀 First ` **{health['first_responses']}** responses"
            ),
            inline = False,
        )

        # ── Rank distribution bar ─────────────────────────────────────────────
        tier_counts: Dict[str, int] = {t: 0 for t in RANK_TIERS}
        for _, s in stats_list:
            tier_counts[s.rank_tier.value] += 1
        tier_parts = []
        for tier_name in ("Diamond", "Platinum", "Gold", "Silver", "Bronze"):
            count = tier_counts[tier_name]
            if count:
                info = RANK_TIERS[tier_name]
                tier_parts.append(f"{info['emoji']} {count}")
        embed.add_field(
            name   = "🎖️ Tier Distribution",
            value  = "  ·  ".join(tier_parts) or "No ranked staff",
            inline = True,
        )

        # ── Streak spotlight ──────────────────────────────────────────────────
        streak_leaders = sorted(
            [(m, s) for m, s in stats_list if s.consistency and s.consistency.current_streak >= 3],
            key=lambda x: x[1].consistency.current_streak,
            reverse=True,
        )[:3]
        if streak_leaders:
            streak_lines = [
                f"🔥 **{m.display_name}** — {s.consistency.current_streak}d"
                for m, s in streak_leaders
            ]
            embed.add_field(name="🔥 Streak Leaders", value="\n".join(streak_lines), inline=True)

        # ── At-risk / inactive ────────────────────────────────────────────────
        if inactive_ids:
            inactive = [m for m in staff if m.id in inactive_ids]
            embed.add_field(
                name   = f"⚠️ Inactive 7d+ ({len(inactive_ids)})",
                value  = "  ".join(m.mention for m in inactive[:6])
                       + (f"\n…+{len(inactive_ids) - 6} more" if len(inactive_ids) > 6 else ""),
                inline = False,
            )

        embed.set_footer(text=f"Auto-refreshes every {self.config.get_setting('dashboard_update_interval', 3600)//60} min  ·  /staffstats for individual profiles")
        return embed

    def _is_question_message(self, content: str) -> bool:
        """
        Decide whether a non-staff message looks like a genuine help request.

        BUG FIX: original used bare `"?" in content` which fires on any sentence
        ending with a question mark, including:
          • formatting like "option1?option2?"
          • single-character messages like "?"
          • rhetorical or sarcastic "really?" responses
        We now require the message to be at least 5 chars long AND either match
        the QUESTION_RE pattern OR contain a known help-intent keyword.
        """
        if len(content) < 5:
            return False
        lower = content.lower()
        has_kw = any(kw in lower for kw in QUESTION_KEYWORDS)
        has_q  = bool(QUESTION_RE.search(content))
        return has_kw or has_q


    async def _try_track_disboard_bump(self, message: discord.Message) -> None:
            """Credit the staff member who triggered a successful Disboard bump."""
            DISBOARD_ID = 302050872383242240
            if message.author.id != DISBOARD_ID:
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

            await self.db.insert_bump(
                Bump(
                    staff_id=bumper.id,
                    timestamp=message.created_at,
                    bump_type="disboard",
                )
            )

async def setup(bot: commands.Bot):
    await bot.add_cog(StaffAnalytics(bot))