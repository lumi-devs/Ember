"""
SHADOW CHAT - CONSTANTS
Hardcoded configuration for single-guild operation.
"""

import datetime


# =========================DESGIN SHIT====================

MAIN_MESSAGE_IMAGE="https://i.pinimg.com/originals/0b/2b/94/0b2b940f081791e7d264b6a17c60d4a3.gif"
MAIN_MESSAGE_THUMBNAIL="https://cdn.discordapp.com/attachments/1440394555705790575/1456723517658894389/0e13e882-0b77-4b9a-86a1-1b457a4aca3f.png?ex=695966ff&is=6958157f&hm=c02ad795d7316ae79c1fe4d719e57ddfc643b70683bcf6f2204bcee0f7c2714d&animated=true"
MAIN_MESSAGE_COLOR=0xB87333


SESSION_STARTED_COLOR=0xC7A07A

END_SESSION_THUMBNAIL="https://cdn.discordapp.com/attachments/1456907286751477770/1456920732088143903/download_18.jpg?ex=695a1eaa&is=6958cd2a&hm=3e6b1ad5379a757fa64d61b2efaa63b16f045492a0c917f626ab0a29e81d2f9a&animated=true"
MATCH_FOUND_THUMBNAIL="https://cdn.discordapp.com/attachments/1393171149038293023/1456927366503731341/GhwoPFw.png?ex=695a24d8&is=6958d358&hm=5d93f1abb4e11459cd9a9d9d26cffb2174c9036c6827082abcb2d758ef1ccdd7&"
SHADOW_CHAT_QUEUE_IMAGE="https://cdn.discordapp.com/attachments/1425245166247088290/1456924060658176144/From_KlickPin_CF_Pixel_Art_Loading_Screen_Animation_-_Ko-fi_Where_creators_get_support_from_fans_through_donations_membership...___Pixel_art_background_Pixel_animation_Pixel_artWill_Grayson.gif?ex=695a21c4&is=6958d044&hm=02cfa8fa39814822f4ba28962e974ca7e6ab54e57b34ff35d97e7c67a8a14708&"
SHADOW_CHAT_QUEUE_COLOR=0xF58D42

# ==================== DISCORD IDs (HARDCODED) ====================
# Replace these with your actual IDs
AVATAR_UPLOAD_CHANNEL_ID = 1453780776238845982
GUILD_ID = 1388612461703467128  # Your server ID
ENTRY_CHANNEL_ID = 1453780737294860359  # #shadow-chat entry point
PARENT_CHANNEL_ID = 1453780737294860359  # Channel where threads will be created
MOD_LOG_CHANNEL_ID = 1453780904936734794  # For moderation alerts
SHADOW_CATEGORY_ID = 1400589151413342213  # Optional: category for organization
MOD_CHANNEL_ID = 1456635931707703521  # Mod-only channel for commands

# ==================== SESSION CONFIG ====================
MAX_ACTIVE_SESSIONS = 6    # Maximum concurrent sessions in the guild
SESSION_TIMEOUT_MINUTES = 5  # Auto-terminate after X minutes of inactivity
QUEUE_CHECK_INTERVAL = 30  # Seconds between queue processing checks

# ==================== THREAD CONFIG ====================
THREAD_AUTO_ARCHIVE_DURATION = 60  # Thread auto-archive time (minutes)
THREAD_NAME_FORMAT = "shadow-{session_id_short}-{user1_code}-{user2_code}"
THREAD_MESSAGE_LIMIT = 100  # Max messages before warning/cleanup

# ==================== WEBHOOK CONFIG ====================
WEBHOOK_NAME_ANONYMOUS = "Shadow"
WEBHOOK_AVATAR_ANONYMOUS = None  # Discord default silhouette
WEBHOOK_NAME_REVEALED = "{username}"
WEBHOOK_AVATAR_REVEALED = "{avatar_url}"

# ==================== BUTTONS & COMPONENTS ====================
BUTTON_JOIN_LABEL = "Join Shadow Chat"
BUTTON_JOIN_EMOJI = "🕶️"
BUTTON_END_CHAT_LABEL = "End Chat"
BUTTON_END_CHAT_EMOJI = "⏹️"
BUTTON_REVEAL_LABEL = "Reveal Identity"
BUTTON_REVEAL_EMOJI = "👁️"

# ==================== MESSAGE CONTENT ====================
MESSAGE_QUEUED = "🕶️ You've been added to the Shadow Chat queue. Position: #{position}"
MESSAGE_MATCHED = "🎭 Match found! Your private thread has been created: {thread_mention}"
MESSAGE_SESSION_ENDED = "⚠️ Shadow Chat session ended. Thread will be deleted."
MESSAGE_INACTIVITY_WARNING = "⏰ Session will end in 1 minute due to inactivity."
MESSAGE_REVEAL_PENDING = "👁️ You've requested to reveal your identity. Waiting for partner..."
MESSAGE_REVEAL_CONFIRMED = "✅ Both users agreed! Identities revealed."
MESSAGE_REVEAL_DECLINED = "❌ Partner declined to reveal identities."

# ==================== COLORS (for embeds) ====================
COLOR_ANONYMOUS = 0x2F3136  # Dark gray
COLOR_REVEALED = 0x5865F2   # Blurple
COLOR_WARNING = 0xFEE75C    # Yellow
COLOR_ERROR = 0xED4245      # Red
COLOR_SUCCESS = 0x57F287    # Green

# ==================== PATHS ====================
DB_PATH = "data/shadowchat.db"


# ==================== WEBHOOK CONFIG ====================
WEBHOOK_NAME_ANONYMOUS = "Shadow"
WEBHOOK_NAME_REVEALED = "{username}"
WEBHOOK_AVATAR_REVEALED = "{avatar_url}"


WEBHOOK_URLS = [
    "***WEBHOOK_REMOVED***",
    "***WEBHOOK_REMOVED***",
    "***WEBHOOK_REMOVED***",
    "***WEBHOOK_REMOVED***",
    "***WEBHOOK_REMOVED***",
    "***WEBHOOK_REMOVED***",
    "***WEBHOOK_REMOVED***",
    "***WEBHOOK_REMOVED***",
    "***WEBHOOK_REMOVED***",
    "***WEBHOOK_REMOVED***",
    "***WEBHOOK_REMOVED***",
    "***WEBHOOK_REMOVED***",
    "***WEBHOOK_REMOVED***",
    "***WEBHOOK_REMOVED***",
    "***WEBHOOK_REMOVED***",
]
