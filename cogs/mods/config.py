"""Configuration management for Staff Analytics cog."""

import os
from typing import List, Optional
import logging

logger = logging.getLogger(__name__)


class StaffAnalyticsConfig:
    """Manages configuration via environment variables."""

    DEFAULTS = {
        "interaction_window":         60,
        "unique_user_window":         21600,
        "conversation_timeout":       600,
        "moderation_session_window":  900,
        "report_cooldown":            300,
        "min_vc_session":             120,
        "inactivity_days":            7,
        "inactivity_threshold_days":  7,
        "role_ping_min_members":      3,
        "db_batch_size":              100,
        "db_batch_timeout":           5,
        "db_retention_days":          90,
        "cache_ttl":                  300,
        "cache_max_size":             1000,
        "response_time_window":       300,
        "new_member_threshold_days":  7,
        "dashboard_update_interval":  3600,   # seconds between dashboard refreshes
        "streak_warn_days":           1,       # warn when streak is this many days from breaking
        "rank_up_dm":                 1,       # 1 = DM staff on rank-up, 0 = disabled
    }

    _CHANNEL_ENV_MAP = {
        "trial_chat":            "TRIAL_CHANNEL_ID",
        "mod_log":               "MOD_LOG_CHANNEL_ID",
        "staff_chat":            "STAFF_CHAT_CHANNEL_ID",
        "alert_channel":         "ALERT_CHANNEL_ID",
        "weekly_report_channel": "WEEKLY_REPORT_CHANNEL_ID",
        "dashboard_channel":     "DASHBOARD_CHANNEL_ID",
        "staff_dm_notifications":"STAFF_DM_NOTIFICATIONS",   # not a channel, handled separately
    }

    _SETTING_ENV_MAP = {
        "inactivity_days":           "INACTIVITY_DAYS",
        "inactivity_threshold_days": "INACTIVITY_DAYS",
        "min_vc_session":            "MIN_VC_SESSION",
        "db_retention_days":         "DB_RETENTION_DAYS",
        "report_cooldown":           "REPORT_COOLDOWN",
        "db_batch_size":             "DB_BATCH_SIZE",
        "db_batch_timeout":          "DB_BATCH_TIMEOUT",
        "cache_ttl":                 "CACHE_TTL",
        "response_time_window":      "RESPONSE_TIME_WINDOW",
        "new_member_threshold_days": "NEW_MEMBER_THRESHOLD_DAYS",
        "dashboard_update_interval": "DASHBOARD_UPDATE_INTERVAL",
        "streak_warn_days":          "STREAK_WARN_DAYS",
        "rank_up_dm":                "RANK_UP_DM",
    }

    @property
    def staff_roles(self) -> List[int]:
        roles_str = os.getenv("MOD_ROLES", "")
        return [int(r.strip()) for r in roles_str.split(",") if r.strip().isdigit()]

    @property
    def general_channel_ids(self) -> List[int]:
        """Channel IDs classified as general/casual (not support/mod channels).

        Set via GENERAL_CHANNEL_IDS env var as comma-separated IDs.
        Used to gate conversation-starter and dead-chat-revival detection so
        those events only fire in social channels, not help queues.
        """
        ids_str = os.getenv("GENERAL_CHANNEL_IDS", "")
        return [int(x.strip()) for x in ids_str.split(",") if x.strip().isdigit()]

    def is_general_channel(self, channel_id: int) -> bool:
        ids = self.general_channel_ids
        # If nothing is configured, treat every non-mod channel as general so
        # the feature is at least partially active out of the box.
        if not ids:
            return True
        return channel_id in ids

    def is_staff_role(self, role_id: int) -> bool:
        return role_id in self.staff_roles

    def get_channel_id(self, channel_name: str) -> Optional[int]:
        env_key = self._CHANNEL_ENV_MAP.get(channel_name)
        if not env_key:
            logger.warning(f"Unknown channel name: {channel_name}")
            return None
        val = os.getenv(env_key)
        return int(val) if val and val.isdigit() else None

    def get_setting(self, key: str, default=None):
        env_key = self._SETTING_ENV_MAP.get(key)
        if env_key:
            val = os.getenv(env_key)
            if val and val.isdigit():
                return int(val)
        return self.DEFAULTS.get(key, default)

    def get_alert_setting(self, key: str) -> Optional[int]:
        ch = self.get_channel_id(key)
        if ch:
            return ch
        if key == "inactivity_threshold_days":
            return self.get_setting("inactivity_threshold_days", 7)
        return None

    @property
    def dm_notifications_enabled(self) -> bool:
        """Whether to DM staff members on rank-ups and streak warnings.
        Controlled by RANK_UP_DM env var (1=on, 0=off). Default: on."""
        return os.getenv("RANK_UP_DM", "1") != "0"