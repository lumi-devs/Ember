import os

# ─── CONFIG ──────────────────────────────
JOIN_CHANNELS = {
    1431227839050682579: ("🍩˚⌇ duo ", 2),
    1431227950275235911: ("🥐₊˚⌇trio ", 3),
    1411247245092978698: ("🍕₊˚⌇squad ", 4),
}

GAME_ROLES = os.environ.get("GAME_ROLES")

# Gaming generator channel ID - ONE CHANNEL ONLY
GAMING_GENERATOR_ID = 1475188995121086625  # Replace with your actual gaming generator ID
MAIN_GAMING_CHAT=1475188661426327613
# Gaming-specific database path
GAMING_DB_PATH = "data/gaming_voice.db"


DB_PATH = "data/voice.db"