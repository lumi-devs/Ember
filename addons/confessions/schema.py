"""TypedDict schema for confessions guild configuration."""

from typing import TypedDict


class ConfessionsGuildConfig(TypedDict, total=False):
    confession_channel_id: int | None
    report_channel_id: int | None
    media_channel_id: int | None
    log_channel_id: int | None
    mod_log_channel_id: int | None
    daily_limit: int
    hourly_report_limit: int
    allowed_role_ids: list[int]
    enabled: bool
