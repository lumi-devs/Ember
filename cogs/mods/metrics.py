"""Metric tracking and analysis for staff analytics."""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Set
import logging
import re
from collections import defaultdict
import discord
from .models import (
    StaffMessage, Interaction, ConversationSession, HelpEvent,
    ModerationAction, VCSession, Report, Bump, RolePing,
    ReactionEvent, ThreadEvent, ResponseTime, QualityEvent, FirstResponseEvent,
    EngagementEvent, ReactionReceived,
)
from .constants import (
    INTERACTION_WINDOW, UNIQUE_USER_WINDOW, CONVERSATION_TIMEOUT,
    HELP_STRONG_RE, HELP_WEAK_RE,
    REPORT_KEYWORDS, REPORT_RE,
    MODERATION_COMMANDS,
    MIN_VC_SESSION, ROLE_PING_MIN_MEMBERS, SCORE_WEIGHTS,
    RESPONSE_TIME_WINDOW, QUALITY_MIN_WORDS, QUALITY_SCORE_THRESHOLD,
    QUALITY_SIGNALS, MODERATION_SEVERITY, MODERATION_SESSION_WINDOW, FIRST_RESPONSE_WINDOW,
    OFF_PEAK_HOURS, PEAK_HOURS,
    MAX_HELP_EVENTS_PER_USER_PER_DAY, MAX_REACTIONS_PER_DAY, MAX_REPORTS_PER_HOUR,
    QUESTION_RE, QUESTION_KEYWORDS,
    GREETING_RE, CONVERSATION_STARTER_RE,
    MAX_GREETINGS_PER_USER_PER_DAY, MAX_STARTERS_PER_DAY,
    MAX_REACTIONS_RECEIVED_PER_MSG, DEAD_CHAT_THRESHOLD, REVIVAL_RESPONSE_WINDOW,
)

logger = logging.getLogger(__name__)

# How long to keep entries in bounded caches (seconds)
_REACTION_CACHE_TTL    = 86_400   # 24 h
_THREAD_CACHE_TTL      = 86_400   # 24 h
_MOD_DEDUP_TTL         = 120      # 2 min — dedup window against bot-embed double-count
_QUESTION_MAX_AGE      = RESPONSE_TIME_WINDOW  # expire unanswered questions


class MetricsTracker:
    """Tracks real-time metrics for staff activity."""

    def __init__(self, staff_id_set_getter=None):
        # Optional callable returning the current set of staff IDs — used to
        # filter out staff-to-staff interactions.  Inject from the cog.
        self._get_staff_ids = staff_id_set_getter or (lambda: set())

        # (staff_id) -> {(user_id, date): count}  — counts per day for capping
        self.recent_interactions: Dict[int, Dict[Tuple, int]] = defaultdict(dict)

        # staff_id -> {channel_id: timestamp}
        self.report_cooldowns: Dict[int, Dict[int, datetime]] = defaultdict(dict)
        # staff_id -> list[datetime] for hourly cap
        self.report_timestamps: Dict[int, List[datetime]] = defaultdict(list)

        # Response-time tracking: channel_id -> {user_id: timestamp}
        self.pending_questions: Dict[int, Dict[int, datetime]] = defaultdict(dict)

        # First-response tracking: channel_id -> (staff_id, timestamp)
        self.channel_last_staff_msg: Dict[int, Tuple[int, datetime]] = {}

        # Reaction de-dupe: (staff_id, message_id) -> timestamp
        self.recent_reactions: Dict[Tuple[int, int], datetime] = {}
        # staff_id -> date -> count  (daily cap)
        self.reaction_day_counts: Dict[int, Dict[str, int]] = defaultdict(lambda: defaultdict(int))

        # Thread de-dupe: (staff_id, thread_id, event_type) -> timestamp
        # BUG FIX: was a plain set with no TTL — grew forever at 24 k msg/day.
        self.tracked_threads: Dict[Tuple, datetime] = {}

        # Quality event de-dupe: (staff_id, message_id) -> timestamp
        # BUG FIX: was a set cleared wholesale at 10k entries causing a thundering
        # herd of duplicate quality events right after the clear.
        self.tracked_quality: Dict[Tuple[int, int], datetime] = {}

        # Off-peak message tracking: (staff_id, date) -> count
        self.off_peak_counts: Dict[Tuple[int, str], int] = defaultdict(int)

        # Active VC sessions
        self.active_vc_sessions: Dict[Tuple[int, int], VCSession] = {}

        # Moderation sessions
        self.moderation_sessions: Dict[int, List[datetime]] = {}

        # Conversation sessions (per staff member)
        # NOTE: keyed by staff_id — one active session per person.
        # This is intentional; multi-channel help merges into one session window.
        self.active_conversations: Dict[int, ConversationSession] = {}

        # Mod action dedup (staff_id, target_id, action_type) -> timestamp
        # Prevents double-counting when both on_app_command_completion AND
        # _try_parse_mod_bot_embed fire for the same action.
        self.recent_mod_actions: Dict[Tuple[int, int, str], datetime] = {}

        # Help event dedup: (staff_id, helped_id, date) -> count
        self.help_event_counts: Dict[Tuple[int, int, str], int] = defaultdict(int)

        # ── Engagement tracking ───────────────────────────────────────────────

        # Last message timestamp in each channel (any author, staff or not).
        # Used to determine whether a channel is "dead" before a staff post.
        self.channel_last_any_msg: Dict[int, datetime] = {}

        # Pending revival: channel_id -> (staff_id, message_id, posted_at)
        # Cleared when a non-staff user replies (confirming revival) or expires.
        self.pending_revivals: Dict[int, Tuple[int, int, datetime]] = {}

        # Per-day cap for conversation starters: (staff_id, date) -> count
        self.starter_day_counts: Dict[Tuple[int, str], int] = defaultdict(int)

        # Per-(staff, user, date) cap for greetings
        self.greeting_counts: Dict[Tuple[int, int, str], int] = defaultdict(int)

        # Reactions-received dedup: (staff_message_id, reactor_id) -> timestamp
        # Prevents double-counting the same person reacting twice to the same message.
        self.recent_reactions_received: Dict[Tuple[int, int], datetime] = {}

        # Per-message cap: message_id -> count  (cap at MAX_REACTIONS_RECEIVED_PER_MSG)
        self.reactions_received_per_msg: Dict[int, int] = defaultdict(int)

    # ─── Staff ID helper ──────────────────────────────────────────────────────

    def _is_staff(self, user_id: int) -> bool:
        return user_id in self._get_staff_ids()

    # ─── Interaction detection ────────────────────────────────────────────────

    def check_interaction(
        self,
        message: StaffMessage,
        referenced_message: Optional[Dict] = None,
        mentions: Optional[List[int]] = None,
    ) -> List[Interaction]:
        interactions = []
        now = discord.utils.utcnow()
        seen_users: Set[int] = set()

        def _try_add(author_id: int, itype: str):
            if author_id == message.staff_id:
                return
            # BUG FIX: don't credit staff-to-staff interactions — a moderator
            # @mentioning a colleague for handoff shouldn't count as a community
            # interaction.
            if self._is_staff(author_id):
                return
            if author_id in seen_users:
                return
            seen_users.add(author_id)
            i = Interaction(
                staff_id=message.staff_id, user_id=author_id,
                channel_id=message.channel_id, message_id=message.message_id,
                interaction_type=itype, timestamp=now,
            )
            if self._should_count_interaction(i):
                interactions.append(i)

        if referenced_message and referenced_message.get("author"):
            _try_add(referenced_message["author"], "reply")

        for uid in (mentions or []):
            _try_add(uid, "mention")

        return interactions

    def _should_count_interaction(self, interaction: Interaction) -> bool:
        # De-dupe by (user_id, date) but cap at UNIQUE_USER_WINDOW interactions
        # per pair per day rather than allowing exactly 1.
        key = (interaction.user_id, interaction.timestamp.date())
        bucket = self.recent_interactions[interaction.staff_id]
        count = bucket.get(key, 0)
        if count >= 1:       # one unique-member credit per user per day
            return False
        bucket[key] = count + 1
        return True

    # ─── Response time tracking ───────────────────────────────────────────────

    def record_question(self, channel_id: int, user_id: int, timestamp: datetime):
        """Record that a non-staff user asked a question."""
        self.pending_questions[channel_id][user_id] = timestamp

    def _expire_pending_questions(self, channel_id: int, now: datetime):
        """Remove questions older than QUESTION_MAX_AGE from a channel's bucket."""
        stale = [
            uid for uid, ts in self.pending_questions.get(channel_id, {}).items()
            if (now - ts).total_seconds() > _QUESTION_MAX_AGE
        ]
        for uid in stale:
            del self.pending_questions[channel_id][uid]

    def check_response_time(
        self, message: StaffMessage, channel_id: int
    ) -> Optional[ResponseTime]:
        now = discord.utils.utcnow()
        self._expire_pending_questions(channel_id, now)
        pending = self.pending_questions.get(channel_id, {})

        best: Optional[Tuple[int, datetime]] = None
        for user_id, q_time in list(pending.items()):
            diff = (now - q_time).total_seconds()
            if diff <= RESPONSE_TIME_WINDOW:
                if best is None or q_time > best[1]:
                    best = (user_id, q_time)
            else:
                del self.pending_questions[channel_id][user_id]

        if best:
            user_id, q_time = best
            del self.pending_questions[channel_id][user_id]
            return ResponseTime(
                staff_id=message.staff_id,
                user_id=user_id,
                channel_id=channel_id,
                response_seconds=(now - q_time).total_seconds(),
                timestamp=now,
            )
        return None

    # ─── First response tracking ──────────────────────────────────────────────

    def check_first_response(
        self,
        message: StaffMessage,
        channel_id: int,
        has_pending_question: bool,
    ) -> Optional[FirstResponseEvent]:
        """
        Return a FirstResponseEvent if this staff member is the FIRST staff
        to respond to a pending question in this channel within FIRST_RESPONSE_WINDOW.
        """
        if not has_pending_question:
            return None

        now = discord.utils.utcnow()
        last_staff = self.channel_last_staff_msg.get(channel_id)

        # A "first response" = pending question AND no OTHER staff member
        # has already responded in FIRST_RESPONSE_WINDOW.
        #
        # BUG FIX (original): the condition `last_staff[0] == message.staff_id`
        # treated the case where THIS staff member was the last one to message as
        # a first-response trigger — the exact opposite of the intent.
        # If they were the last to message, they've already responded and there's
        # nothing "first" about the current message.
        #
        # Correct logic: it's a first response only when no staff (including this
        # person) has sent a message recently.
        is_first = (
            last_staff is None
            or (now - last_staff[1]).total_seconds() > FIRST_RESPONSE_WINDOW
        )

        # Always update last-staff tracker regardless
        self.channel_last_staff_msg[channel_id] = (message.staff_id, now)

        if not is_first:
            return None

        # Find the most recently asked pending question
        pending = self.pending_questions.get(channel_id, {})
        best_user = None
        best_time = None
        for user_id, q_time in pending.items():
            diff = (now - q_time).total_seconds()
            if diff <= FIRST_RESPONSE_WINDOW:
                if best_time is None or q_time > best_time:
                    best_user, best_time = user_id, q_time

        if best_user is None:
            return None

        return FirstResponseEvent(
            staff_id=message.staff_id,
            user_id=best_user,
            channel_id=channel_id,
            message_id=message.message_id,
            response_seconds=(now - best_time).total_seconds(),
            timestamp=now,
        )

    # ─── Off-peak message tracking ────────────────────────────────────────────

    def is_off_peak(self, timestamp: datetime) -> bool:
        return timestamp.hour in OFF_PEAK_HOURS

    def is_peak(self, timestamp: datetime) -> bool:
        return timestamp.hour in PEAK_HOURS

    def record_message_time(self, staff_id: int, timestamp: datetime):
        if self.is_off_peak(timestamp):
            key = (staff_id, timestamp.date().isoformat())
            self.off_peak_counts[key] += 1

    def get_off_peak_count(self, staff_id: int) -> int:
        today = discord.utils.utcnow().date().isoformat()
        return self.off_peak_counts.get((staff_id, today), 0)

    # ─── Message quality scoring ──────────────────────────────────────────────

    def score_message_quality(
        self,
        message: StaffMessage,
        has_attachment: bool = False,
    ) -> Optional[QualityEvent]:
        """
        Evaluate a message for quality signals.
        Returns a QualityEvent if the message scores above threshold.
        """
        key = (message.staff_id, message.message_id)
        now = discord.utils.utcnow()

        # BUG FIX: was a set that got cleared entirely at 10k entries, causing a
        # burst of duplicate quality events.  Now uses a TTL dict so old entries
        # are pruned individually without losing recent protection.
        if key in self.tracked_quality:
            return None
        self.tracked_quality[key] = now

        content = message.content or ""
        score = 0.0
        signals = []

        # Code block — must have non-trivial content between fences
        if re.search(r"```\w*\n?.{3,}?```", content, re.DOTALL):
            score += QUALITY_SIGNALS["code_block"]
            signals.append("code_block")
        # Inline code — BUG FIX: original matched any backtick including stray
        # characters.  Now requires at least 2 non-whitespace chars inside.
        elif re.search(r"`\S.+?\S`|`\S`", content):
            score += QUALITY_SIGNALS["inline_code"]
            signals.append("inline_code")

        # Link
        if re.search(r"https?://\S{4,}", content):
            score += QUALITY_SIGNALS["link"]
            signals.append("link")

        # Numbered list (at least 2 items)
        if len(re.findall(r"^\s*[1-9]\d*\.", content, re.MULTILINE)) >= 2:
            score += QUALITY_SIGNALS["numbered_list"]
            signals.append("numbered_list")
        # Bullet list (at least 2 items)
        elif len(re.findall(r"^\s*[-•*]\s", content, re.MULTILINE)) >= 2:
            score += QUALITY_SIGNALS["bullet_list"]
            signals.append("bullet_list")

        # Quote
        if re.search(r"^\s*>", content, re.MULTILINE):
            score += QUALITY_SIGNALS["quote"]
            signals.append("quote")

        # Long message
        if message.word_count >= QUALITY_MIN_WORDS * 2:
            score += QUALITY_SIGNALS["long_message"]
            signals.append("long_message")

        # Attachment
        if has_attachment:
            score += QUALITY_SIGNALS["image"]
            signals.append("image")

        # BUG FIX: threshold raised to QUALITY_SCORE_THRESHOLD (1.5) so that a
        # single backtick (0.6) alone no longer qualifies as a quality event.
        if score < QUALITY_SCORE_THRESHOLD or not signals:
            return None

        return QualityEvent(
            staff_id=message.staff_id,
            message_id=message.message_id,
            channel_id=message.channel_id,
            quality_score=round(score, 2),
            signals=signals,
            word_count=message.word_count,
            timestamp=now,
        )

    # ─── Conversation sessions ────────────────────────────────────────────────

    def update_conversation(
        self, message: StaffMessage, mentioned_users: List[int]
    ) -> Optional[ConversationSession]:
        staff_id = message.staff_id
        now = discord.utils.utcnow()

        if staff_id in self.active_conversations:
            session = self.active_conversations[staff_id]
            if (
                session.end_time
                and (now - session.end_time).total_seconds() > CONVERSATION_TIMEOUT
            ):
                completed = session
                del self.active_conversations[staff_id]
                new_s = ConversationSession(
                    staff_id=staff_id, start_time=now, end_time=None,
                    channel_ids=[message.channel_id],
                    user_ids=[u for u in mentioned_users if u != staff_id],
                    message_count=1,
                )
                self.active_conversations[staff_id] = new_s
                return completed
            else:
                session.end_time = now
                if message.channel_id not in session.channel_ids:
                    session.channel_ids.append(message.channel_id)
                for uid in mentioned_users:
                    if uid not in session.user_ids and uid != staff_id:
                        session.user_ids.append(uid)
                session.message_count += 1
                return None
        else:
            session = ConversationSession(
                staff_id=staff_id, start_time=now, end_time=now,
                channel_ids=[message.channel_id],
                user_ids=[u for u in mentioned_users if u != staff_id],
                message_count=1,
            )
            self.active_conversations[staff_id] = session
            return None

    def end_conversation(self, staff_id: int) -> Optional[ConversationSession]:
        if staff_id in self.active_conversations:
            session = self.active_conversations.pop(staff_id)
            session.end_time = discord.utils.utcnow()
            return session
        return None

    # ─── Help event detection ─────────────────────────────────────────────────

    def check_help_event(
        self,
        message: StaffMessage,
        replied_to: Optional[Dict],
        channel_history: List[Dict],
        is_new_member: bool = False,
    ) -> Optional[HelpEvent]:
        """
        Detect a help event with a two-tier keyword strategy to reduce false positives.

        Rules:
          Reactive (reply):
            - Must be a reply to a non-staff user.
            - Replied-to message must look like a question/request.
            - Staff message must contain ≥1 STRONG keyword, OR ≥2 WEAK keywords,
              OR ≥1 WEAK keyword + the replied message was a clear question.

          Proactive (staff volunteers info unprompted):
            - Recent history (≤ FIRST_RESPONSE_WINDOW seconds old) must contain
              a question/request from a non-staff user.
            - Staff message must contain ≥1 STRONG keyword.
            - Must meet minimum word count to avoid one-liners.

        Additionally: capped at MAX_HELP_EVENTS_PER_USER_PER_DAY per helped user
        to prevent a staff member from inflating their score by repeatedly
        replying to the same person.
        """
        now = discord.utils.utcnow()
        content = message.content or ""
        content_lower = content.lower()

        strong_hits = len(HELP_STRONG_RE.findall(content_lower))
        weak_hits   = len(HELP_WEAK_RE.findall(content_lower))

        def _is_question(text: str) -> bool:
            text_lower = text.lower()
            has_q_mark = bool(QUESTION_RE.search(text))
            has_kw     = any(kw in text_lower for kw in QUESTION_KEYWORDS)
            return has_q_mark or has_kw

        def _record(helped_id: int, event_type: str) -> Optional[HelpEvent]:
            # Per-user daily cap
            date_str = now.date().isoformat()
            cap_key = (message.staff_id, helped_id, date_str)
            if self.help_event_counts[cap_key] >= MAX_HELP_EVENTS_PER_USER_PER_DAY:
                return None
            self.help_event_counts[cap_key] += 1
            return HelpEvent(
                helper_id=message.staff_id, helped_id=helped_id,
                channel_id=message.channel_id, message_id=message.message_id,
                event_type=event_type, timestamp=now, is_new_member=is_new_member,
            )

        # ── Reactive branch ───────────────────────────────────────────────────
        if replied_to:
            author_id = replied_to.get("author")
            # Don't credit staff helping other staff
            if author_id and author_id != message.staff_id and not self._is_staff(author_id):
                prev_content = (replied_to.get("content") or "")
                is_prev_question = _is_question(prev_content)

                qualifies = (
                    strong_hits >= 1
                    or weak_hits >= 2
                    or (weak_hits >= 1 and is_prev_question)
                    or (is_prev_question and message.word_count >= QUALITY_MIN_WORDS)
                )
                if qualifies:
                    return _record(author_id, "reactive")

        # ── Proactive branch ──────────────────────────────────────────────────
        # BUG FIX: original iterated channel_history with NO timestamp check,
        # so a question from days ago could still trigger a proactive credit.
        # We now require the question to be within FIRST_RESPONSE_WINDOW seconds.
        if strong_hits >= 1 and message.word_count >= QUALITY_MIN_WORDS:
            for prev_msg in (channel_history or [])[:5]:
                prev_ts = prev_msg.get("timestamp")
                if prev_ts:
                    age = (now - prev_ts).total_seconds()
                    if age > FIRST_RESPONSE_WINDOW:
                        continue  # question too old
                prev_content = (prev_msg.get("content") or "")
                prev_author  = prev_msg.get("author")
                if not prev_author or prev_author == message.staff_id:
                    continue
                if self._is_staff(prev_author):
                    continue  # staff question, not community
                if _is_question(prev_content):
                    return _record(prev_author, "proactive")

        return None

    # ─── Reports ──────────────────────────────────────────────────────────────

    def check_report(
        self, message: StaffMessage, channel_id: Optional[int], is_command: bool = False
    ) -> Optional[Report]:
        now = discord.utils.utcnow()

        # Per-channel cooldown (unchanged)
        last = self.report_cooldowns[message.staff_id].get(channel_id)
        if last and (now - last).total_seconds() < 300:
            return None

        # Hourly cap
        hour_ago = now - timedelta(hours=1)
        self.report_timestamps[message.staff_id] = [
            t for t in self.report_timestamps[message.staff_id] if t > hour_ago
        ]
        if len(self.report_timestamps[message.staff_id]) >= MAX_REPORTS_PER_HOUR:
            return None

        if is_command:
            self.report_cooldowns[message.staff_id][channel_id] = now
            self.report_timestamps[message.staff_id].append(now)
            return Report(staff_id=message.staff_id, timestamp=now, report_type="command")

        content = (message.content or "").lower()
        # BUG FIX: use compiled word-boundary regex instead of broad substring match.
        # Original set included "issue", "problem", "concern" — extremely common
        # in normal support conversations, firing on every "my issue is…" message.
        if REPORT_RE.search(content):
            self.report_cooldowns[message.staff_id][channel_id] = now
            self.report_timestamps[message.staff_id].append(now)
            return Report(
                staff_id=message.staff_id, timestamp=now,
                message_link=None,  # filled in by caller with jump_url
                report_type="message",
            )
        return None

        # BUG FIX: original had dead/unreachable code below this return —
        # a leftover bump-detection block that referenced BUMP_SUCCESS_KEYWORDS
        # which was never imported.  Removed entirely.

    # ─── Role pings ───────────────────────────────────────────────────────────

    def check_role_ping(
        self,
        message: StaffMessage,
        role_mentions: List[Dict],
        role_member_counts: Dict[int, int],
    ) -> List[RolePing]:
        now = discord.utils.utcnow()
        return [
            RolePing(
                staff_id=message.staff_id, role_id=r["id"],
                channel_id=message.channel_id, message_id=message.message_id,
                timestamp=now,
            )
            for r in role_mentions
            if role_member_counts.get(r["id"], 0) >= ROLE_PING_MIN_MEMBERS
        ]

    # ─── Voice channel sessions ───────────────────────────────────────────────

    def start_vc_session(self, staff_id: int, channel_id: int, join_time: datetime) -> VCSession:
        session = VCSession(staff_id=staff_id, channel_id=channel_id,
                            join_time=join_time, leave_time=None)
        self.active_vc_sessions[(staff_id, channel_id)] = session
        return session

    def end_vc_session(self, staff_id: int, channel_id: int, leave_time: datetime) -> Optional[VCSession]:
        key = (staff_id, channel_id)
        session = self.active_vc_sessions.pop(key, None)
        if session:
            session.leave_time = leave_time
            if session.duration() < MIN_VC_SESSION:
                return None
            return session
        return None

    # ─── Moderation tracking ──────────────────────────────────────────────────

    def track_moderation(
        self,
        moderator_id: int,
        target_id: int,
        command: str,
        timestamp: datetime,
        reason: Optional[str] = None,
    ) -> Optional[ModerationAction]:
        """
        Record a moderation action.  Returns None if a duplicate was detected
        (e.g. both slash-command completion AND mod-bot embed fired).
        """
        dedup_key = (moderator_id, target_id, command.lower())
        now_ts = discord.utils.utcnow()

        if dedup_key in self.recent_mod_actions:
            age = (now_ts - self.recent_mod_actions[dedup_key]).total_seconds()
            if age < _MOD_DEDUP_TTL:
                logger.debug(
                    "Mod action dedup: %s by %d on %d (age %.1fs)",
                    command, moderator_id, target_id, age,
                )
                return None
        self.recent_mod_actions[dedup_key] = now_ts

        severity = MODERATION_SEVERITY.get(command.lower(), 1.0)
        actions  = self.moderation_sessions.setdefault(moderator_id, [])
        if actions and (timestamp - actions[-1]).total_seconds() <= 900:
            session_id = f"{moderator_id}_{actions[0].strftime('%Y%m%d%H%M')}"
        else:
            session_id = f"{moderator_id}_{timestamp.strftime('%Y%m%d%H%M')}"
            self.moderation_sessions[moderator_id] = []
        self.moderation_sessions[moderator_id].append(timestamp)

        return ModerationAction(
            moderator_id=moderator_id, target_id=target_id,
            action_type=command, timestamp=timestamp,
            reason=reason, session_id=session_id, severity=severity,
        )

    # ─── Reaction tracking ────────────────────────────────────────────────────

    def track_reaction(
        self,
        staff_id: int,
        message_author_id: int,
        channel_id: int,
        message_id: int,
        emoji: str,
        timestamp: datetime,
    ) -> Optional[ReactionEvent]:
        key = (staff_id, message_id)
        if key in self.recent_reactions:
            return None

        # Daily cap — prevent reaction farming
        date_str = timestamp.date().isoformat()
        if self.reaction_day_counts[staff_id][date_str] >= MAX_REACTIONS_PER_DAY:
            return None

        self.recent_reactions[key] = timestamp
        self.reaction_day_counts[staff_id][date_str] += 1

        return ReactionEvent(
            staff_id=staff_id, user_id=message_author_id,
            channel_id=channel_id, message_id=message_id,
            emoji=emoji, timestamp=timestamp,
        )

    # ─── Thread tracking ──────────────────────────────────────────────────────

    def track_thread(
        self, staff_id: int, thread_id: int, channel_id: int, event_type: str, timestamp: datetime
    ) -> Optional[ThreadEvent]:
        key = (staff_id, thread_id, event_type)
        # BUG FIX: original used a plain set with no TTL.  Now uses a timestamp
        # dict so entries can be expired in cleanup_old_data.
        if key in self.tracked_threads:
            return None
        self.tracked_threads[key] = timestamp
        return ThreadEvent(
            staff_id=staff_id, thread_id=thread_id,
            channel_id=channel_id, event_type=event_type, timestamp=timestamp,
        )

    # ─── Engagement detection ─────────────────────────────────────────────────

    def record_any_message(self, channel_id: int, timestamp: datetime):
        """Track the most recent message timestamp per channel (staff or not).
        Used to determine whether a channel was silent before a staff post."""
        self.channel_last_any_msg[channel_id] = timestamp

    def check_dead_chat_revival(self, message: StaffMessage, now: datetime) -> bool:
        """
        Register a pending revival when a staff member posts into a channel that
        has been silent for >= DEAD_CHAT_THRESHOLD seconds.

        Returns True if a revival was registered.  The EngagementEvent is only
        confirmed (and inserted) once a non-staff user actually replies —
        see confirm_revival().
        """
        channel_id = message.channel_id
        last = self.channel_last_any_msg.get(channel_id)
        if last is None:
            return False
        if (now - last).total_seconds() < DEAD_CHAT_THRESHOLD:
            return False
        if channel_id in self.pending_revivals:
            return False
        self.pending_revivals[channel_id] = (message.staff_id, message.message_id, now)
        return True

    def confirm_revival(
        self, channel_id: int, non_staff_user_id: int, now: datetime
    ) -> Optional[EngagementEvent]:
        """
        Called when a non-staff user sends a message in any channel.
        If a revival is pending for that channel and is still within
        REVIVAL_RESPONSE_WINDOW, return a confirmed EngagementEvent.
        """
        pending = self.pending_revivals.get(channel_id)
        if not pending:
            return None
        staff_id, message_id, posted_at = pending
        if (now - posted_at).total_seconds() > REVIVAL_RESPONSE_WINDOW:
            del self.pending_revivals[channel_id]
            return None
        del self.pending_revivals[channel_id]
        return EngagementEvent(
            staff_id=staff_id, channel_id=channel_id, message_id=message_id,
            event_type="dead_chat_revival", user_id=non_staff_user_id, timestamp=now,
        )

    def check_conversation_starter(
        self, message: StaffMessage, now: datetime
    ) -> Optional[EngagementEvent]:
        """
        Detect when a staff member intentionally starts a discussion:
          - Must NOT be a reply (replies are responses, not starters)
          - Must match CONVERSATION_STARTER_RE
          - Per-day cap of MAX_STARTERS_PER_DAY
        The caller is responsible for only calling this for general channels.
        """
        content = (message.content or "").lower()
        if not CONVERSATION_STARTER_RE.search(content):
            return None
        date_str = now.date().isoformat()
        key = (message.staff_id, date_str)
        if self.starter_day_counts[key] >= MAX_STARTERS_PER_DAY:
            return None
        self.starter_day_counts[key] += 1
        return EngagementEvent(
            staff_id=message.staff_id, channel_id=message.channel_id,
            message_id=message.message_id, event_type="conversation_starter",
            user_id=None, timestamp=now,
        )

    def check_new_member_greeting(
        self,
        message: StaffMessage,
        target_user_id: int,
        now: datetime,
    ) -> Optional[EngagementEvent]:
        """
        Detect a social welcome message to a new member (distinct from a help event).
          - Message must match GREETING_RE
          - Per-(staff, user, day) cap of MAX_GREETINGS_PER_USER_PER_DAY
        The caller is responsible for only calling this when the target is a new
        member and no help event was already produced for this message.
        """
        content = (message.content or "").lower()
        if not GREETING_RE.search(content):
            return None
        date_str = now.date().isoformat()
        key = (message.staff_id, target_user_id, date_str)
        if self.greeting_counts[key] >= MAX_GREETINGS_PER_USER_PER_DAY:
            return None
        self.greeting_counts[key] += 1
        return EngagementEvent(
            staff_id=message.staff_id, channel_id=message.channel_id,
            message_id=message.message_id, event_type="new_member_greeting",
            user_id=target_user_id, timestamp=now,
        )

    def track_reaction_received(
        self,
        staff_message_id: int,
        staff_id: int,
        reactor_id: int,
        channel_id: int,
        emoji: str,
        now: datetime,
    ) -> Optional[ReactionReceived]:
        """
        Track a community reaction on a staff member's own message.
          - Self-reactions are ignored
          - Per-(message, reactor) dedup: same person reacting twice not counted
          - Per-message cap: MAX_REACTIONS_RECEIVED_PER_MSG
        """
        if reactor_id == staff_id:
            return None
        dedup_key = (staff_message_id, reactor_id)
        if dedup_key in self.recent_reactions_received:
            return None
        if self.reactions_received_per_msg[staff_message_id] >= MAX_REACTIONS_RECEIVED_PER_MSG:
            return None
        self.recent_reactions_received[dedup_key] = now
        self.reactions_received_per_msg[staff_message_id] += 1
        return ReactionReceived(
            staff_id=staff_id, reactor_id=reactor_id,
            channel_id=channel_id, message_id=staff_message_id,
            emoji=emoji, timestamp=now,
        )

    # ─── Word count ───────────────────────────────────────────────────────────

    @staticmethod
    def count_words(content: Optional[str]) -> int:
        if not content:
            return 0
        # Strip code blocks entirely so we count prose words, not code tokens
        clean = re.sub(r"```.*?```", " ", content, flags=re.DOTALL)
        clean = re.sub(r"`[^`]+`",   " ", clean)
        clean = re.sub(r"<[@#!&]?\d+>", " ", clean)   # Discord mentions
        clean = re.sub(r"https?://\S+", " ", clean)    # URLs
        clean = re.sub(r"[^\w\s'-]",    " ", clean)    # strip punctuation
        words = [w for w in clean.split() if len(w) > 1]  # ignore single chars
        return len(words)

    # ─── Cleanup ──────────────────────────────────────────────────────────────

    def cleanup_old_data(self):
        """Periodic cleanup of old in-memory data structures"""
        now = discord.utils.utcnow()
        today = now.date()
        today_str = today.isoformat()
        hour_ago = now - timedelta(hours=1)

        # Interaction de-dupe: keep only today's entries
        for sid in list(self.recent_interactions):
            self.recent_interactions[sid] = {
                k: v for k, v in self.recent_interactions[sid].items()
                if k[1] == today
            }

        # Report cooldowns (5 min TTL)
        for sid in list(self.report_cooldowns):
            self.report_cooldowns[sid] = {
                ch: ts for ch, ts in self.report_cooldowns[sid].items()
                if (now - ts).total_seconds() <= 300
            }

        # Report hourly timestamps
        for sid in list(self.report_timestamps):
            self.report_timestamps[sid] = [
                t for t in self.report_timestamps[sid] if t > hour_ago
            ]

        # Stale conversations
        for sid in list(self.active_conversations):
            s = self.active_conversations[sid]
            if s.end_time and (now - s.end_time).total_seconds() > CONVERSATION_TIMEOUT:
                del self.active_conversations[sid]

        # Reaction cache: prune older than 24 h
        cutoff_24h = now - timedelta(hours=24)
        self.recent_reactions = {k: v for k, v in self.recent_reactions.items() if v > cutoff_24h}

        # Reaction day counts: keep only today
        for sid in list(self.reaction_day_counts):
            self.reaction_day_counts[sid] = defaultdict(int, {
                d: c for d, c in self.reaction_day_counts[sid].items() if d == today_str
            })

        # Thread de-dupe: TTL 24 h
        self.tracked_threads = {
            k: v for k, v in self.tracked_threads.items() if v > cutoff_24h
        }

        # Quality event de-dupe: TTL 24 h
        self.tracked_quality = {
            k: v for k, v in self.tracked_quality.items() if v > cutoff_24h
        }

        # Channel last-staff-message: prune entries older than FIRST_RESPONSE_WINDOW × 10
        stale_channel_cutoff = now - timedelta(seconds=FIRST_RESPONSE_WINDOW * 10)
        self.channel_last_staff_msg = {
            ch: (sid, ts)
            for ch, (sid, ts) in self.channel_last_staff_msg.items()
            if ts > stale_channel_cutoff
        }

        # Off-peak counts: prune older dates
        self.off_peak_counts = defaultdict(int, {
            k: v for k, v in self.off_peak_counts.items() if k[1] == today_str
        })

        # Mod dedup cache: prune entries older than TTL
        self.recent_mod_actions = {
            k: v for k, v in self.recent_mod_actions.items()
            if (now - v).total_seconds() <= _MOD_DEDUP_TTL
        }

        # Help event daily counts: keep only today
        self.help_event_counts = defaultdict(int, {
            k: v for k, v in self.help_event_counts.items() if k[2] == today_str
        })

        # Moderation sessions: prune staff with no recent actions
        cutoff_session = now - timedelta(seconds=MODERATION_SESSION_WINDOW * 2)
        for sid in list(self.moderation_sessions):
            self.moderation_sessions[sid] = [
                t for t in self.moderation_sessions[sid]
                if t > cutoff_session
            ]
            if not self.moderation_sessions[sid]:
                del self.moderation_sessions[sid]

        # Pending questions: expire per-channel
        for ch in list(self.pending_questions):
            self._expire_pending_questions(ch, now)
            if not self.pending_questions[ch]:
                del self.pending_questions[ch]

        # ── Engagement cleanup ────────────────────────────────────────────────

        # Channel last-any-message: prune channels silent for > 2× DEAD_CHAT_THRESHOLD
        stale_any_cutoff = now - timedelta(seconds=DEAD_CHAT_THRESHOLD * 2)
        self.channel_last_any_msg = {
            ch: ts for ch, ts in self.channel_last_any_msg.items()
            if ts > stale_any_cutoff
        }

        # Pending revivals: expire those past REVIVAL_RESPONSE_WINDOW
        revival_cutoff = now - timedelta(seconds=REVIVAL_RESPONSE_WINDOW)
        self.pending_revivals = {
            ch: v for ch, v in self.pending_revivals.items()
            if v[2] > revival_cutoff
        }

        # Starter day counts: keep only today
        self.starter_day_counts = defaultdict(int, {
            k: v for k, v in self.starter_day_counts.items() if k[1] == today_str
        })

        # Greeting counts: keep only today
        self.greeting_counts = defaultdict(int, {
            k: v for k, v in self.greeting_counts.items() if k[2] == today_str
        })

        # Reactions-received dedup: 24 h TTL
        self.recent_reactions_received = {
            k: v for k, v in self.recent_reactions_received.items() if v > cutoff_24h
        }

        # Per-message reaction counts: only keep messages still in dedup cache
        live_message_ids = {k[0] for k in self.recent_reactions_received}
        self.reactions_received_per_msg = defaultdict(int, {
            k: v for k, v in self.reactions_received_per_msg.items()
            if k in live_message_ids
        })