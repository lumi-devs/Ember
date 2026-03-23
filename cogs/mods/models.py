"""Data models for staff analytics."""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any


class RankTier(Enum):
    BRONZE   = "Bronze"
    SILVER   = "Silver"
    GOLD     = "Gold"
    PLATINUM = "Platinum"
    DIAMOND  = "Diamond"

    @classmethod
    def from_score(cls, score: float) -> "RankTier":
        from .constants import RANK_TIERS
        for name in ("Diamond", "Platinum", "Gold", "Silver", "Bronze"):
            if score >= RANK_TIERS[name]["min"]:
                return cls(name)
        return cls.BRONZE

    @property
    def display(self) -> str:
        from .constants import RANK_TIERS
        info = RANK_TIERS[self.value]
        return f"{info['emoji']} {self.value}"

    @property
    def label(self) -> str:
        from .constants import RANK_TIERS
        return RANK_TIERS[self.value].get("label", self.value)


@dataclass
class StaffMessage:
    staff_id:   int
    channel_id: int
    message_id: int
    timestamp:  datetime
    content:    Optional[str] = None
    word_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "staff_id": self.staff_id, "channel_id": self.channel_id,
            "message_id": self.message_id, "timestamp": self.timestamp.isoformat(),
            "content": self.content, "word_count": self.word_count,
        }


@dataclass
class Interaction:
    staff_id: int
    user_id: int
    channel_id: int
    message_id: int
    interaction_type: str
    timestamp: datetime

    def to_dict(self) -> Dict[str, Any]:
        return {
            "staff_id": self.staff_id, "user_id": self.user_id,
            "channel_id": self.channel_id, "message_id": self.message_id,
            "interaction_type": self.interaction_type, "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class ConversationSession:
    staff_id: int
    start_time: datetime
    end_time: Optional[datetime]
    channel_ids: List[int]
    user_ids: List[int]
    message_count: int

    def duration(self) -> float:
        if not self.end_time:
            return 0.0
        return (self.end_time - self.start_time).total_seconds() / 60

    def to_dict(self) -> Dict[str, Any]:
        return {
            "staff_id": self.staff_id, "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "channel_ids": self.channel_ids, "user_ids": self.user_ids,
            "message_count": self.message_count, "duration_minutes": self.duration(),
        }


@dataclass
class ModerationAction:
    moderator_id: int
    target_id: int
    action_type: str
    timestamp: datetime
    reason: Optional[str] = None
    session_id: Optional[str] = None
    severity: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "moderator_id": self.moderator_id, "target_id": self.target_id,
            "action_type": self.action_type, "timestamp": self.timestamp.isoformat(),
            "reason": self.reason, "session_id": self.session_id, "severity": self.severity,
        }


@dataclass
class VCSession:
    staff_id: int
    channel_id: int
    join_time: datetime
    leave_time: Optional[datetime]
    peak_occupancy: int = 1   # max people in VC during this session

    def duration(self) -> float:
        if not self.leave_time:
            return 0.0
        return (self.leave_time - self.join_time).total_seconds() / 60

    def to_dict(self) -> Dict[str, Any]:
        return {
            "staff_id": self.staff_id, "channel_id": self.channel_id,
            "join_time": self.join_time.isoformat(),
            "leave_time": self.leave_time.isoformat() if self.leave_time else None,
            "duration_minutes": self.duration(),
            "peak_occupancy": self.peak_occupancy,
        }


@dataclass
class Report:
    staff_id: int
    timestamp: datetime
    message_link: Optional[str] = None
    report_type: str = "message"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "staff_id": self.staff_id, "timestamp": self.timestamp.isoformat(),
            "message_link": self.message_link, "report_type": self.report_type,
        }


@dataclass
class Bump:
    staff_id: int
    timestamp: datetime
    bump_type: str

    def to_dict(self) -> Dict[str, Any]:
        return {"staff_id": self.staff_id, "timestamp": self.timestamp.isoformat(), "bump_type": self.bump_type}


@dataclass
class RolePing:
    staff_id: int
    role_id: int
    channel_id: int
    message_id: int
    timestamp: datetime

    def to_dict(self) -> Dict[str, Any]:
        return {
            "staff_id": self.staff_id, "role_id": self.role_id,
            "channel_id": self.channel_id, "message_id": self.message_id,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class HelpEvent:
    helper_id: int
    helped_id: int
    channel_id: int
    message_id: int
    event_type: str          # reactive | proactive
    timestamp: datetime
    is_new_member: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "helper_id": self.helper_id, "helped_id": self.helped_id,
            "channel_id": self.channel_id, "message_id": self.message_id,
            "event_type": self.event_type, "timestamp": self.timestamp.isoformat(),
            "is_new_member": self.is_new_member,
        }


@dataclass
class PingResponseEvent:
    """Staff responded to a @Moderator/@Staff ping within PING_RESPONSE_WINDOW."""
    staff_id: int
    channel_id: int
    message_id: int
    response_seconds: float
    pinger_id: int
    timestamp: datetime

    def to_dict(self) -> Dict[str, Any]:
        return {
            "staff_id": self.staff_id, "channel_id": self.channel_id,
            "message_id": self.message_id, "response_seconds": self.response_seconds,
            "pinger_id": self.pinger_id, "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class ReactionEvent:
    staff_id: int
    user_id: int
    channel_id: int
    message_id: int
    emoji: str
    timestamp: datetime

    def to_dict(self) -> Dict[str, Any]:
        return {
            "staff_id": self.staff_id, "user_id": self.user_id,
            "channel_id": self.channel_id, "message_id": self.message_id,
            "emoji": str(self.emoji), "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class ThreadEvent:
    staff_id: int
    thread_id: int
    channel_id: int
    event_type: str
    timestamp: datetime

    def to_dict(self) -> Dict[str, Any]:
        return {
            "staff_id": self.staff_id, "thread_id": self.thread_id,
            "channel_id": self.channel_id, "event_type": self.event_type,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class ResponseTime:
    staff_id: int
    user_id: int
    channel_id: int
    response_seconds: float
    timestamp: datetime

    def to_dict(self) -> Dict[str, Any]:
        return {
            "staff_id": self.staff_id, "user_id": self.user_id,
            "channel_id": self.channel_id,
            "response_seconds": self.response_seconds,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class QualityEvent:
    staff_id: int
    message_id: int
    channel_id: int
    quality_score: float
    signals: List[str]
    word_count: int
    timestamp: datetime

    def to_dict(self) -> Dict[str, Any]:
        return {
            "staff_id": self.staff_id, "message_id": self.message_id,
            "channel_id": self.channel_id, "quality_score": self.quality_score,
            "signals": ",".join(self.signals), "word_count": self.word_count,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class FirstResponseEvent:
    staff_id: int
    user_id: int
    channel_id: int
    message_id: int
    response_seconds: float
    timestamp: datetime

    def to_dict(self) -> Dict[str, Any]:
        return {
            "staff_id": self.staff_id, "user_id": self.user_id,
            "channel_id": self.channel_id, "message_id": self.message_id,
            "response_seconds": self.response_seconds,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class EngagementEvent:
    """Social/community engagement actions beyond support and moderation.

    event_type values:
      "dead_chat_revival"   — staff posted in a silent channel and got a reply
      "conversation_starter"— staff initiated a discussion (non-reply) in a general channel
      "new_member_greeting" — staff sent a social welcome to a brand-new member
      "new_member_chat"     — staff talked WITH a new member (interaction)
      "ping_response"       — staff responded to a @staff/@mod ping quickly
    """
    staff_id:   int
    channel_id: int
    message_id: int
    event_type: str
    user_id:    Optional[int]
    timestamp:  datetime

    def to_dict(self) -> Dict[str, Any]:
        return {
            "staff_id": self.staff_id, "channel_id": self.channel_id,
            "message_id": self.message_id, "event_type": self.event_type,
            "user_id": self.user_id, "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class ReactionReceived:
    staff_id:   int
    reactor_id: int
    channel_id: int
    message_id: int
    emoji:      str
    timestamp:  datetime

    def to_dict(self) -> Dict[str, Any]:
        return {
            "staff_id": self.staff_id, "reactor_id": self.reactor_id,
            "channel_id": self.channel_id, "message_id": self.message_id,
            "emoji": str(self.emoji), "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class StaffNote:
    id: Optional[int]
    staff_id: int
    author_id: int
    note: str
    created_at: datetime
    is_positive: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "staff_id": self.staff_id, "author_id": self.author_id,
            "note": self.note, "created_at": self.created_at.isoformat(),
            "is_positive": self.is_positive,
        }


# ─── Aggregated stats ─────────────────────────────────────────────────────────

@dataclass
class ConsistencyStats:
    staff_id: int
    active_days: int = 0
    active_hours: Dict[int, int] = field(default_factory=lambda: defaultdict(int))
    active_days_of_week: Dict[int, int] = field(default_factory=lambda: defaultdict(int))
    longest_streak: int = 0
    current_streak: int = 0
    last_active_date: Optional[datetime] = None
    peak_hours_activity: int = 0
    off_peak_activity: int = 0
    hourly_spread_score: float = 0.0

    # Weekly consistency: list of 7 bools (Mon-Sun) for last 4 weeks
    weekly_active_days: List[int] = field(default_factory=list)

    @property
    def consistency_score(self) -> float:
        from .constants import SCORE_WEIGHTS
        score = self.active_days * SCORE_WEIGHTS["daily_activity"]
        unique_hours = sum(1 for c in self.active_hours.values() if c > 0)
        score += min(unique_hours * SCORE_WEIGHTS["hourly_spread"], 30)
        score += self.peak_hours_activity * SCORE_WEIGHTS["peak_hours_bonus"]
        score += self.off_peak_activity   * SCORE_WEIGHTS["off_peak_coverage"]
        score += self.longest_streak      * SCORE_WEIGHTS["streak_bonus"]
        return round(score, 2)

    @property
    def weekly_consistency_score(self) -> float:
        """0-100: what % of the last 28 days were active."""
        if not self.weekly_active_days:
            return 0.0
        return round(sum(1 for d in self.weekly_active_days if d) / len(self.weekly_active_days) * 100, 1)

    @property
    def active_hours_count(self) -> int:
        return sum(1 for c in self.active_hours.values() if c > 0)

    @property
    def most_active_hour(self) -> Optional[int]:
        if not self.active_hours:
            return None
        return max(self.active_hours, key=self.active_hours.get)

    @property
    def busiest_day_name(self) -> Optional[str]:
        if not self.active_days_of_week:
            return None
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        idx = max(self.active_days_of_week, key=self.active_days_of_week.get)
        return days[int(idx)]


@dataclass
class StaffStats:
    staff_id: int
    unique_members_helped:  int   = 0
    vc_hours:               float = 0.0
    moderation_actions:     int   = 0
    moderation_severity_sum:float = 0.0
    reports_filed:          int   = 0
    bumps_performed:        int   = 0
    role_pings:             int   = 0
    conversation_sessions:  int   = 0
    help_events:            int   = 0
    proactive_help:         int   = 0
    total_messages:         int   = 0
    active_days:            int   = 0
    channels_participated:  int   = 0
    reactions_given:        int   = 0
    threads_created:        int   = 0
    new_members_helped:     int   = 0
    fast_responses:         int   = 0
    first_responses:        int   = 0
    quality_events:         int   = 0
    avg_quality_score:      float = 0.0
    avg_message_words:      float = 0.0
    avg_response_seconds:   float = 0.0
    off_peak_messages:      int   = 0
    # ── Community-building ──────────────────────────────────────────────────
    new_member_chats:       int   = 0
    dead_chat_revivals:     int   = 0
    conversation_starters:  int   = 0
    new_member_greetings:   int   = 0
    reactions_received:     int   = 0
    # ── New metrics ──────────────────────────────────────────────────────────
    unique_talked_with:     int   = 0    # unique non-staff people talked TO (interactions)
    ping_responses:         int   = 0    # responded to @staff pings quickly
    vc_people_bonus:        float = 0.0  # total VC people-bonus accumulated
    unique_newbies_chatted: int   = 0    # unique new members talked with
    vc_people_sessions:     int   = 0    # VC sessions where others were present
    # ── Mod breakdown ────────────────────────────────────────────────────────
    mod_action_breakdown:   Dict[str, int] = field(default_factory=dict)   # e.g. {"warn":5,"ban":2}
    # ── Trend / drop-off ────────────────────────────────────────────────────
    weekly_score_avg:       float = 0.0   # 4-week rolling average score
    this_week_score:        float = 0.0   # score just for current 7 days
    # ── Meta ────────────────────────────────────────────────────────────────
    percentile_rank:        Optional[float] = None
    rank_position:          Optional[int]   = None
    total_staff:            Optional[int]   = None
    consistency:            Optional[ConsistencyStats] = None
    _weights:               Optional[Dict[str, float]] = field(default=None, repr=False)

    def _w(self, key: str) -> float:
        from .constants import SCORE_WEIGHTS
        if self._weights and key in self._weights:
            return float(self._weights[key])
        return float(SCORE_WEIGHTS.get(key, 0.0))

    @property
    def engagement_score(self) -> float:
        word_bonus    = max(0.0, self.avg_message_words - 5) * self._w("message_quality")
        quality_bonus = self.quality_events * self._w("quality_event")
        base = (
            self.unique_members_helped    * self._w("unique_members")
            + self.help_events            * self._w("help_events")
            + self.proactive_help         * self._w("proactive_help")
            + self.moderation_actions     * self._w("moderation_actions")
            + self.moderation_severity_sum* self._w("moderation_severity")
            + self.reports_filed          * self._w("reports")
            + self.vc_hours               * self._w("vc_hours")
            + self.vc_people_bonus        * self._w("vc_people_bonus")
            + self.conversation_sessions  * self._w("conversation_sessions")
            + self.bumps_performed        * self._w("server_bumps")
            + self.reactions_given        * self._w("reactions_given")
            + self.threads_created        * self._w("threads_created")
            + self.new_members_helped     * self._w("new_member_helped")
            + self.fast_responses         * self._w("fast_response")
            + self.first_responses        * self._w("first_response")
            + self.ping_responses         * self._w("ping_response")
            + self.unique_talked_with     * self._w("unique_talked_with")
            + word_bonus
            + quality_bonus
            + self.dead_chat_revivals     * self._w("dead_chat_revival")
            + self.conversation_starters  * self._w("conversation_starter")
            + self.new_member_greetings   * self._w("new_member_greeting")
            + self.reactions_received     * self._w("reactions_received")
            + self.new_member_chats       * self._w("newbie_chat")
        )
        if self.consistency:
            base += self.consistency.consistency_score
        return round(base, 2)

    @property
    def rank_tier(self) -> RankTier:
        return RankTier.from_score(self.engagement_score)

    @property
    def quality_score_display(self) -> str:
        if self.avg_quality_score == 0 and self.quality_events == 0:
            return "N/A"
        if self.total_messages == 0:
            return "N/A"
        rate = min(self.quality_events / self.total_messages, 1.0)
        score = round(rate * 10, 1)
        return f"{score}/10"

    @property
    def response_rate_display(self) -> str:
        if self.avg_response_seconds == 0:
            return "N/A"
        s = self.avg_response_seconds
        if s < 60:
            return f"{s:.0f}s"
        return f"{s / 60:.1f}m"

    @property
    def earned_achievements(self) -> List[str]:
        from .constants import ACHIEVEMENT_THRESHOLDS
        earned = []
        streak = self.consistency.current_streak if self.consistency else 0
        metric_map = {
            "help_events":          self.help_events,
            "moderation_actions":   self.moderation_actions,
            "vc_hours":             self.vc_hours,
            "new_members_helped":   self.new_members_helped,
            "fast_responses":       self.fast_responses,
            "first_responses":      self.first_responses,
            "threads_created":      self.threads_created,
            "reactions_given":      self.reactions_given,
            "current_streak":       streak,
            "off_peak_messages":    self.consistency.off_peak_activity if self.consistency else 0,
            "conversation_starters":self.conversation_starters,
            "new_member_greetings": self.new_member_greetings,
            "dead_chat_revivals":   self.dead_chat_revivals,
            "reactions_received":   self.reactions_received,
            "unique_talked_with":   self.unique_talked_with,
            "ping_responses":       self.ping_responses,
            "vc_people_sessions":   self.vc_people_sessions,
            "unique_newbies_chatted":self.unique_newbies_chatted,
        }
        for key, info in ACHIEVEMENT_THRESHOLDS.items():
            val = metric_map.get(info["metric"], 0)
            if val >= info["value"]:
                earned.append(f"{info['emoji']} {info['desc']}")
        return earned

    def to_dict(self) -> Dict[str, Any]:
        return {
            "staff_id": self.staff_id,
            "unique_members_helped": self.unique_members_helped,
            "vc_hours": self.vc_hours, "moderation_actions": self.moderation_actions,
            "moderation_severity_sum": self.moderation_severity_sum,
            "reports_filed": self.reports_filed, "bumps_performed": self.bumps_performed,
            "role_pings": self.role_pings, "conversation_sessions": self.conversation_sessions,
            "help_events": self.help_events, "proactive_help": self.proactive_help,
            "total_messages": self.total_messages, "active_days": self.active_days,
            "channels_participated": self.channels_participated,
            "reactions_given": self.reactions_given, "threads_created": self.threads_created,
            "new_members_helped": self.new_members_helped, "fast_responses": self.fast_responses,
            "first_responses": self.first_responses, "quality_events": self.quality_events,
            "avg_quality_score": self.avg_quality_score,
            "avg_message_words": self.avg_message_words,
            "avg_response_seconds": self.avg_response_seconds,
            "off_peak_messages": self.off_peak_messages,
            "dead_chat_revivals": self.dead_chat_revivals,
            "conversation_starters": self.conversation_starters,
            "new_member_greetings": self.new_member_greetings,
            "reactions_received": self.reactions_received,
            "unique_talked_with": self.unique_talked_with,
            "ping_responses": self.ping_responses,
            "vc_people_bonus": self.vc_people_bonus,
            "unique_newbies_chatted": self.unique_newbies_chatted,
            "vc_people_sessions": self.vc_people_sessions,
            "engagement_score": self.engagement_score,
            "rank_tier": self.rank_tier.value,
            "mod_action_breakdown": self.mod_action_breakdown,
        }