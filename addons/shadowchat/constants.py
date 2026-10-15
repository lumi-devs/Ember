import os
from pathlib import Path

GUILD_ID = int(os.getenv("GUILD_ID", "0"))
ENTRY_CHANNEL_ID = int(os.getenv("SC_ENTRY_CHANNEL_ID", "0"))
CATEGORY_ID = int(os.getenv("SC_CATEGORY_ID", "0"))
MOD_LOG_CHANNEL_ID = int(os.getenv("SC_MOD_LOG_CHANNEL_ID", "0"))
REPORT_CHANNEL_ID = int(os.getenv("SC_REPORT_CHANNEL_ID", "0"))

DB_PATH = "data/shadowchat_v2.db"
ASSETS_DIR = Path(__file__).parent / "assets"

INACTIVITY_WARN_SECONDS = 15 * 60
INACTIVITY_TIMEOUT_SECONDS = 30 * 60
DATE_CHANNEL_INACTIVITY_WARN_SECONDS = 11 * 3600  # warn at 11 h idle
DATE_CHANNEL_INACTIVITY_TIMEOUT_SECONDS = 12 * 3600  # auto-close at 12 h idle
MESSAGE_RATE_LIMIT = 5  # messages per window
MESSAGE_RATE_WINDOW = 2  # seconds
SESSION_COOLDOWN_SECONDS = 30
REVEAL_TIMEOUT_SECONDS = 60
CHANNEL_CLEANUP_DELAY = 600  # default: 10 min (inactivity)
CHANNEL_CLEANUP_DELAY_FAST = 30  # end/skip/report: 30 s — read the card, then gone
MATCHMAKING_INTERVAL = 2.0  # seconds between matchmaking ticks
DM_MUTE_HOURS = 12  # default DM mute duration

# ── Content filtering ───────────────────────────────────────────────────────
PROFANITY_WORDS = {
    "shit",
    "bitch",
    "damn",
    "hell",
    "ass",
    "bastard",
    "bastards",
    "crap",
    "piss",
    "fuck",
    "cunt",
    "dick",
    "cock",
    "pussy",
    "asshole",
    "bullshit",
    "motherfucker",
    "nigger",
    "nigga",
    "faggot",
    "fagot",
    "slut",
    "whore",
    "penis",
    "vagina",
    "breast",
}
REPORT_RATE_LIMIT = 3  # max reports per hour
REPORT_RATE_WINDOW = 3600  # 1 hour in seconds
COOLDOWN_EXTENSION_REPORTED = 300  # 5 min extra cooldown for reported users

# ── Reactions ───────────────────────────────────────────────────────────────
REACTION_RATE_LIMIT = 3  # max reactions per second
REACTION_RATE_WINDOW = 1  # 1 second window

# ── Ratings ─────────────────────────────────────────────────────────────────
RATING_TTL_DAYS = 30  # each rating deleted 30 days after it was given
RATING_CLEANUP_INTERVAL = 3600  # run expired-rating cleanup every 1 hour

# ── Ping via @ ───────────────────────────────────────────────────────────────
PING_COOLDOWN_SECONDS = 30  # max 2 @-pings per user per this window
PING_MAX_PER_WINDOW = 2

# ── Mid-session promo tips ────────────────────────────────────────────────────
PROMO_TIPS = [
    "💡 **Tip:** Higher ratings = better matches. Be kind, stay real.",
    "⚡ **Did you know?** Low ratings bump your queue time. Keep it genuine!",
    "🌟 **Reminder:** Rate your partner after the chat — it shapes who you meet next.",
    "🔒 **Privacy:** Your real identity stays hidden until *both* of you agree to reveal.",
    "💬 **Reveal:** Use the Reveal button when you're ready to unmask — it's mutual!",
    "📊 **Stats:** Your rating is based on recent chats. A few good convos can turn it around.",
    "🎯 **Matching:** Consistent high ratings give you a slight edge in the queue — it adds up.",
    "🤝 **Respect:** Even a short chat deserves a fair rating. It only takes one tap.",
    "✨ **Fun fact:** Users who rate consistently get matched faster on average.",
    "💬 **Tip:** React to messages with any emoji — your partner sees it in real-time!",
]

# ── Mid-session stat drop templates ──────────────────────────────────────────
# Each entry is a (template_key, display_string) pair.
# template_key tells the relay handler which stat to fetch.
STAT_DROP_TEMPLATES = [
    ("recent_hour", "📊 **{x}** chats kicked off in the last hour"),
    ("active_now", "💬 **{x}** people are chatting right now"),
    ("total_sessions", "📈 **{total}** sessions all-time — you're part of the story!"),
    ("total_hours", "⏱️ ShadowChat users have racked up **{hours}h** of conversation total"),
    ("today", "🔥 **{x}** new matches today — the queue never sleeps"),
    ("total_users", "🌐 **{users}** unique users have used ShadowChat so far"),
    ("queue_now", "👥 **{queue}** people waiting in queue right now — swipe fast!"),
]

# ── Random channel name parts ─────────────────────────────────────────────────

CHANNEL_ADJECTIVES = (
    "misty",
    "hollow",
    "silent",
    "velvet",
    "amber",
    "ashen",
    "cobalt",
    "dreamy",
    "fading",
    "gilded",
    "hidden",
    "indigo",
    "jagged",
    "kindle",
    "lunar",
    "marble",
    "nested",
    "onyx",
    "pallid",
    "quartz",
    "rosy",
    "sable",
    "tawny",
    "umber",
    "vivid",
    "waning",
    "xeric",
    "yellow",
    "zephyr",
    "arctic",
    "briny",
    "cedar",
    "dusky",
    "ember",
    "foggy",
    "glacial",
    "hazel",
    "ivory",
    "jade",
    "knotted",
    "lavender",
    "mossy",
    "nightly",
    "obsidian",
    "pearly",
    "quiet",
    "russet",
    "smoky",
    "twilight",
    "willow",
)

CHANNEL_NOUNS = (
    "haven",
    "drift",
    "grove",
    "veil",
    "hollow",
    "shore",
    "bloom",
    "crest",
    "dale",
    "echo",
    "falls",
    "glade",
    "heath",
    "isle",
    "knoll",
    "ledge",
    "mire",
    "nook",
    "opal",
    "peak",
    "quarry",
    "ridge",
    "spring",
    "thicket",
    "vale",
    "wren",
    "yarn",
    "zenith",
    "basin",
    "cairn",
    "dell",
    "eddy",
    "fjord",
    "gust",
    "haze",
    "inlet",
    "jetty",
    "lair",
    "marsh",
    "nimbus",
    "outcrop",
    "pine",
    "ravine",
    "spire",
    "tor",
    "undertow",
    "verdure",
    "wisp",
    "yore",
)


def _parse_gender_roles() -> dict[str, int]:
    """SC_GENDER_ROLES=male:123456,female:789012,other:111222"""
    raw = os.getenv("SC_GENDER_ROLES", "")
    result: dict[str, int] = {}
    for item in raw.split(","):
        item = item.strip()
        if ":" in item:
            name, rid = item.split(":", 1)
            try:
                result[name.strip().lower()] = int(rid.strip())
            except ValueError:
                pass
    return result


GENDER_ROLES: dict[str, int] = _parse_gender_roles()


def _parse_match_pairs() -> list[tuple[str, str]]:
    """SC_MATCH_PAIRS=male:female,male:other — bidirectional.
    If unset, every distinct gender pair is valid."""
    raw = os.getenv("SC_MATCH_PAIRS", "")
    if not raw:
        keys = list(GENDER_ROLES.keys())
        return [(keys[i], keys[j]) for i in range(len(keys)) for j in range(i + 1, len(keys))]
    result: list[tuple[str, str]] = []
    for item in raw.split(","):
        item = item.strip()
        if ":" in item:
            a, b = item.split(":", 1)
            result.append((a.strip().lower(), b.strip().lower()))
    return result


MATCH_PAIRS: list[tuple[str, str]] = _parse_match_pairs()

ANON_NAMES = [
    # Colour + creature — evocative, easy to remember
    "Blue Fox",
    "Crimson Hare",
    "Amber Wolf",
    "Ivory Raven",
    "Jade Crane",
    "Onyx Deer",
    "Scarlet Owl",
    "Silver Moth",
    "Verdant Lynx",
    "Dusk Falcon",
    "Slate Otter",
    "Tawny Kite",
    "Indigo Bear",
    "Copper Swan",
    "Smoky Puma",
    "Gilded Finch",
    "Ashen Tiger",
    "Mossy Crow",
    "Pallid Drake",
    "Velvet Hawk",
    "Russet Vole",
    "Ember Fox",
    "Cobalt Wren",
    "Misty Hound",
    "Pearly Doe",
    "Hollow Kestrel",
    "Lunar Colt",
    "Cedar Mink",
    "Willow Ibis",
    "Quartz Stag",
    "Briny Seal",
    "Obsidian Mare",
    "Teal Lynx",
    "Sable Puffin",
    "Arctic Vixen",
    "Hazel Sparrow",
    "Ochre Badger",
    "Fading Heron",
    "Rosy Weasel",
    "Glacial Elk",
]
