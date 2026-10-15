from typing import TypedDict


class ShadowChatGuildConfig(TypedDict, total=False):
    entry_channel_id: int | None
    category_id: int | None
    mod_log_channel_id: int | None
    report_channel_id: int | None
    enabled: bool
