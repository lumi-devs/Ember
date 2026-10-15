import os

# join channels come from env, names/slots stay here since they're just labels
_JOIN_IDS = [
    int(x) for x in os.environ.get("TEMPVC_JOIN_CHANNEL_IDS", "").split(",") if x.strip()
]
_JOIN_META = [
    ("🍩˚⌇ duo ", 2),
    ("🥐₊˚⌇trio ", 3),
    ("🍕₊˚⌇squad ", 4),
]
JOIN_CHANNELS = dict(zip(_JOIN_IDS, _JOIN_META))

GAME_ROLES = os.environ.get("GAME_ROLES")

# Gaming generator channel ID - ONE CHANNEL ONLY
GAMING_GENERATOR_ID = int(os.environ.get("TEMPVC_GAMING_GENERATOR_ID"))
MAIN_GAMING_CHAT = int(os.environ.get("TEMPVC_MAIN_GAMING_CHAT"))
# Gaming-specific database path
GAMING_DB_PATH = "data/gaming_voice.db"


DB_PATH = "data/voice.db"
