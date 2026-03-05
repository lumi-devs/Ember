# Channel IDs (REPLACE WITH YOURS)
FORUM_CHANNEL_ID = 1477764896761315559  # Forum channel for debates
MOD_CHANNEL_ID = 1467580321552662752  # Channel for mod approvals

# Role IDs
MOD_ROLE_ID = 1406324000736542911  # Role that can approve debates

HOT_TAG_NAME = "🔥 Hot Right Now"  # Name for the hot tag
HOT_TAG_EMOJI = "🔥"  # Emoji for the hot tag
HOT_TAG_ID = None  # Will be set automatically when created/loaded

INACTIVE_HOURS = 48  # Hours before auto-close
ACTIVITY_THRESHOLD = {
    "high": 200,  # messages per day for 🔥
    "active": 100,  # messages per day for ⭐
    "slow": 1,  # messages per day for 💤
}

# Emoji indicators
STATUS_EMOJIS = {"high": "🔥", "active": "⭐", "slow": "💤", "closed": "🔒"}

# Colors for embeds
COLORS = {
    "rules": 0x2B2D31,
    "info": 0x3498DB,
    "success": 0x2ECC71,
    "warning": 0xE74C3C,
    "analytics": 0x9B59B6,
}

# Database path
DB_PATH = "data/debate.db"
