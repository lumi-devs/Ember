"""
SHADOW CHAT - UTILITY FUNCTIONS (MERGED & SAFE)
Combines O(1) optimizations with original API compatibility.
"""

import string
import datetime
import re
import secrets
from typing import Optional, Tuple
import discord
from discord import Embed

try:
    from . import constants
except ImportError:
    import constants

# ==================== CONSTANTS ====================
CHARS = string.ascii_lowercase + string.digits

# Tuple is faster than list for iteration/lookup
CUTE_WORDS_TUPLE = (
    "Meow",
    "Bubbles",
    "Snuggles",
    "Puddle",
    "Sprinkle",
    "Bumblebee",
    "Jellybean",
    "Fluffy",
    "Peaches",
    "Dandelion",
    "Marshmallow",
    "Pebbles",
    "Sunshine",
    "Raindrop",
    "Butterfly",
    "Starlight",
    "Chirpy",
    "Panda",
    "Kittens",
    "Honeybee",
    "Cupcake",
    "Sparkle",
    "Twinkle",
    "Cotton",
    "Berry",
    "Bunny",
    "Fuzzy",
    "Cuddle",
    "Sweetie",
    "Sugar",
    "Mitten",
    "Paws",
    "Whisker",
    "Glitter",
    "Daisy",
    "Petal",
    "Blossom",
    "Cocoa",
    "Muffin",
    "Noodle",
)

CUTE_EMOJIS_TUPLE = (
    "🐱",
    "🐶",
    "🐰",
    "🦊",
    "🦝",
    "🐼",
    "🐨",
    "🐻",
    "🦄",
    "🐥",
    "🐤",
    "🐣",
    "🦋",
    "🐝",
    "🐞",
    "🌸",
    "🌼",
    "🍩",
    "🧁",
    "🍪",
    "🍓",
    "🍒",
    "🍑",
    "☁️",
    "⭐",
    "✨",
    "🌟",
    "💫",
    "🎀",
    "🧸",
    "💐",
    "🍬",
    "🍭",
    "🐾",
    "😺",
    "😸",
    "😻",
    "🐯",
    "🫧",
    "🩷",
)

# Frozenset provides O(1) lookup
UNSAFE_EXTENSIONS = frozenset(
    {
        ".exe",
        ".bat",
        ".cmd",
        ".sh",
        ".ps1",
        ".js",
        ".vbs",
        ".jar",
        ".dll",
        ".sys",
        ".scr",
        ".pif",
        ".com",
        ".hta",
    }
)

# Compile regex once
NEWLINE_PATTERN = re.compile(r"\n{4,}")

# ==================== ID & CODE GENERATION ====================


def generate_session_id() -> str:
    """Generate a unique session ID using secure random."""
    random_part = "".join(secrets.choice(CHARS) for _ in range(10))
    return f"shadow_{random_part}"


def generate_user_code() -> str:
    """Generate 4-char user code."""
    return "".join(secrets.choice(CHARS) for _ in range(4))


def format_thread_name(
    session_id: str, user1_id: int, user2_id: str
) -> Tuple[str, str]:
    """
    Generate thread names using secure random selection.
    """
    # Use secrets.randbelow() for secure random indices
    emoji_len = len(CUTE_EMOJIS_TUPLE)
    words_len = len(CUTE_WORDS_TUPLE)

    # Generate secure random indices
    emoji_idx1 = secrets.randbelow(emoji_len)
    emoji_idx2 = secrets.randbelow(emoji_len)

    # Use different random indices for words
    w1 = secrets.randbelow(words_len)
    w2 = secrets.randbelow(words_len)
    w3 = secrets.randbelow(words_len)
    w4 = secrets.randbelow(words_len)

    thread1_name = f"{CUTE_EMOJIS_TUPLE[emoji_idx1]} Room {CUTE_WORDS_TUPLE[w1]} {CUTE_WORDS_TUPLE[w2]}"[
        :100
    ]
    thread2_name = f"{CUTE_EMOJIS_TUPLE[emoji_idx2]} Room {CUTE_WORDS_TUPLE[w3]} {CUTE_WORDS_TUPLE[w4]}"[
        :100
    ]

    return thread1_name, thread2_name


# ==================== TIME & DURATION HELPERS ====================


def format_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds_remaining = divmod(seconds, 60)
    if seconds < 3600:
        return (
            f"{minutes}m {seconds_remaining}s" if seconds_remaining else f"{minutes}m"
        )
    hours, minutes_remaining = divmod(minutes, 60)
    return f"{hours}h {minutes_remaining}m" if minutes_remaining else f"{hours}h"


def format_duration_minutes(minutes: float) -> str:
    if minutes < 1:
        return f"{int(minutes * 60)}s"
    if minutes < 60:
        whole_minutes = int(minutes)
        seconds = int((minutes - whole_minutes) * 60)
        return f"{whole_minutes}m {seconds}s" if seconds else f"{whole_minutes}m"
    hours, minutes_remaining = divmod(int(minutes), 60)
    return f"{hours}h {minutes_remaining}m" if minutes_remaining else f"{hours}h"


def format_datetime(dt: datetime.datetime) -> str:
    return f"<t:{int(dt.timestamp())}:R>"


def format_datetime_absolute(dt: datetime.datetime) -> str:
    return f"<t:{int(dt.timestamp())}:f>"


def is_inactive(last_activity: datetime.datetime, timeout_minutes: int) -> bool:
    cutoff = discord.utils.utcnow() - datetime.timedelta(minutes=timeout_minutes)
    return last_activity < cutoff


def calculate_estimated_wait(
    position: int,
    queue_size: int,
    avg_session_minutes: float = 15,
    active_sessions: int = 0,
    max_sessions: int = 2,
) -> float:
    if active_sessions >= max_sessions:
        base_wait = avg_session_minutes * 0.7
    else:
        base_wait = 0.5

    pairs_ahead = (position - 1 + 1) // 2
    estimated = base_wait + (pairs_ahead * avg_session_minutes)
    return max(0.5, estimated)


# ==================== DISCORD HELPERS ====================


async def get_or_create_webhook(
    channel: discord.TextChannel, name: str
) -> discord.Webhook:
    webhooks = await channel.webhooks()
    for webhook in webhooks:
        if webhook.name == name and webhook.user == channel.guild.me:
            return webhook
    return await channel.create_webhook(name=name, reason="Shadow Chat relay")


async def get_thread_by_id(
    guild: discord.Guild, thread_id: int
) -> Optional[discord.Thread]:
    thread = guild.get_thread(thread_id)
    if thread:
        return thread
    try:
        fetched = await guild.fetch_channel(thread_id)
        return fetched if isinstance(fetched, discord.Thread) else None
    except discord.NotFound:
        return None


# ==================== EMBEDS (RESTORED MISSING ONES) ====================


def create_queue_embed(
    position: int,
    queue_size: int,
    joined_at: datetime.datetime,
    estimated_wait_minutes: float = None,
) -> Embed:
    embed = Embed(title="🕶️ Shadow Chat Queue", color=constants.COLOR_ANONYMOUS)

    if position == 1 and queue_size == 1:
        status = "**You're next in line!**\nWaiting for a partner..."
    elif position <= 2 and queue_size >= 2:
        status = "**Match pending!**\nYou should be matched soon."
    else:
        status = f"You're in position **#{position}** of **{queue_size}**"

    embed.description = status
    if estimated_wait_minutes:
        embed.add_field(
            name="⏰ Estimated Wait",
            value=f"**{format_duration_minutes(estimated_wait_minutes)}**",
            inline=True,
        )
    embed.add_field(name="📅 Joined", value=format_datetime(joined_at), inline=True)
    embed.add_field(
        name="🎭 Active Sessions",
        value=f"**{constants.MAX_ACTIVE_SESSIONS}** max concurrent",
        inline=True,
    )

    if queue_size > 1:
        embed.add_field(
            name="Progress",
            value=f"`{create_progress_bar(position, queue_size)}`",
            inline=False,
        )

    embed.set_footer(text="You'll be notified when a match is found")
    return embed


def create_queue_stats_embed(
    queue_size: int,
    active_sessions: int,
    oldest_wait: datetime.datetime,
    estimated_next_wait: float,
    avg_session_duration: float,
) -> Embed:
    """Restored from original."""
    embed = Embed(
        title="📊 Queue Statistics",
        color=constants.COLOR_ANONYMOUS,
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Queue Size", value=f"**{queue_size}** waiting", inline=True)
    embed.add_field(
        name="Active Sessions",
        value=f"**{active_sessions}/{constants.MAX_ACTIVE_SESSIONS}**",
        inline=True,
    )

    if oldest_wait:
        wait_seconds = (discord.utils.utcnow() - oldest_wait).seconds
        embed.add_field(
            name="Oldest Wait", value=format_duration(wait_seconds), inline=True
        )
    if estimated_next_wait:
        embed.add_field(
            name="Next Match Wait",
            value=format_duration_minutes(estimated_next_wait),
            inline=True,
        )

    embed.add_field(
        name="Avg Session Duration", value=f"{avg_session_duration:.1f}m", inline=True
    )

    status = (
        "✅ Queue is empty"
        if queue_size == 0
        else "🟡 Small queue" if queue_size < 3 else "🟠 Moderate queue"
    )
    embed.add_field(name="Status", value=status, inline=False)
    embed.set_footer(text=f"Max {constants.MAX_ACTIVE_SESSIONS} concurrent sessions")
    return embed


def create_session_embed(session_id: str, partner_code: str) -> Embed:
    """Restored from original."""
    embed = Embed(
        title="🎭 Shadow Chat Session Started",
        description="Your messages will be relayed anonymously to your partner.",
        color=constants.COLOR_ANONYMOUS,
    )
    embed.add_field(name="Session ID", value=f"`{session_id}`", inline=False)
    embed.add_field(name="Privacy", value="✅ Fully anonymous", inline=True)
    rules = [
        "• Messages are relayed via webhook",
        "• No personal info is shared",
        f"• Session auto-ends after {constants.SESSION_TIMEOUT_MINUTES}min inactivity",
        "• Use /reveal to mutually reveal identities",
    ]
    embed.add_field(name="Guidelines", value="\n".join(rules), inline=False)
    embed.set_footer(text="Type /end to leave at any time")
    return embed


def create_reveal_embed(username: str, avatar_url: str) -> Embed:
    """Restored from original."""
    embed = Embed(
        title="👁️ Identity Revealed",
        description="Your partner has agreed to reveal their identity.",
        color=constants.COLOR_REVEALED,
    )
    embed.add_field(name="Username", value=username, inline=True)
    if avatar_url:
        embed.set_thumbnail(url=avatar_url)
    embed.set_footer(text="Both users must consent to reveal")
    return embed


def create_session_header(session_id: str, partner_code: str) -> discord.Embed:
    embed = discord.Embed(
        description="# 🎭 Shadow Chat Session\n"
        "**Your anonymous chat has started!** <:sayittagain:1389063232974749848>\n\n"
        "You are now connected. Identities are hidden until mutually revealed.\n <a:dedede:1392118155790516234>",
        color=constants.COLOR_ANONYMOUS,
    )
    embed.add_field(
        name="Session Controls",
        value="• **Reveal** — Propose mutual identity reveal\n• **Skip** — Find a new partner\n• **End** — Disconnect from this partner",
        inline=False,
    )
    embed.add_field(
        name="Commands",
        value="`/complain <reason>` — Report bad behavior\n`/skip` — Find a new partner\n`/reveal` — Reveal your identities\n`/end` — End the conversation",
        inline=False,
    )
    embed.color = constants.SESSION_STARTED_COLOR
    return embed


def create_session_code_embed(
    session_id: str, partner_code: str, webhook_code: Optional[str] = None
) -> discord.Embed:
    embed = discord.Embed(
        title="🔐 Session Codes",
        description="Keep these codes for your records. Do not share unless you consent.",
        color=constants.COLOR_ANONYMOUS,
    )
    embed.add_field(name="Session ID", value=f"`{session_id}`", inline=False)
    embed.set_footer(
        text="Use /reveal or the Reveal button to mutually reveal identities"
    )
    return embed


def create_progress_bar(current: int, total: int, length: int = 10) -> str:
    if total <= 1:
        return "▰" * length
    filled = min(int((current - 1) / (total - 1) * length), length)
    return "▰" * filled + "▱" * (length - filled)


# ==================== VALIDATION ====================


def sanitize_content(content: str, max_length: int = 2000) -> str:
    if len(content) > max_length:
        content = content[: max_length - 3] + "..."
    return NEWLINE_PATTERN.sub("\n\n\n", content)


def is_safe_attachment(filename: str) -> bool:
    return not any(filename.lower().endswith(ext) for ext in UNSAFE_EXTENSIONS)


async def check_user_permissions(user: discord.Member) -> Tuple[bool, Optional[str]]:
    if user.bot:
        return False, "Bots cannot use Shadow Chat"
    return True, None


# ==================== UI / BUTTONS ====================


def generate_webhook_code() -> str:
    return "".join(secrets.choice(CHARS) for _ in range(4))


def create_session_buttons(session_id: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(
        discord.ui.Button(
            style=discord.ButtonStyle.danger,
            label=constants.BUTTON_END_CHAT_LABEL,
            emoji=constants.BUTTON_END_CHAT_EMOJI,
            custom_id=f"shadow_end_{session_id}",
        )
    )
    view.add_item(
        discord.ui.Button(
            style=discord.ButtonStyle.primary,
            label=constants.BUTTON_REVEAL_LABEL,
            emoji=constants.BUTTON_REVEAL_EMOJI,
            custom_id=f"shadow_reveal_{session_id}",
        )
    )
    return view


def create_queue_button() -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(
        discord.ui.Button(
            style=discord.ButtonStyle.success,
            label=constants.BUTTON_JOIN_LABEL,
            emoji=constants.BUTTON_JOIN_EMOJI,
            custom_id="shadow_join_queue",
        )
    )
    return view


def create_session_header_buttons(session_id: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(
        discord.ui.Button(
            style=discord.ButtonStyle.primary,
            label="Reveal Identity",
            emoji="👁️",
            custom_id=f"shadow_reveal_{session_id}",
            row=0,
        )
    )
    view.add_item(
        discord.ui.Button(
            style=discord.ButtonStyle.secondary,
            label="Skip Partner",
            emoji="⏭️",
            custom_id=f"shadow_skip_{session_id}",
            row=0,
        )
    )
    view.add_item(
        discord.ui.Button(
            style=discord.ButtonStyle.danger,
            label="End Chat",
            emoji="⏹️",
            custom_id=f"shadow_end_{session_id}",
            row=0,
        )
    )
    return view


def create_queue_status_view() -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(
        discord.ui.Button(
            style=discord.ButtonStyle.secondary,
            label="Refresh Status",
            emoji="🔄",
            custom_id="shadow_refresh_queue",
        )
    )
    return view


# --- Confirmation Buttons (Restored API using generic logic) ---


def create_confirmation_buttons(session_id: str) -> discord.ui.View:
    """Restored API wrapper."""
    view = discord.ui.View(timeout=60)
    view.add_item(
        discord.ui.Button(
            style=discord.ButtonStyle.success,
            label="Confirm Reveal",
            emoji="✅",
            custom_id=f"shadow_confirm_reveal_{session_id}",
        )
    )
    view.add_item(
        discord.ui.Button(
            style=discord.ButtonStyle.secondary,
            label="Cancel",
            emoji="❌",
            custom_id=f"shadow_cancel_reveal_{session_id}",
        )
    )
    return view


def create_skip_confirmation_buttons(session_id: str) -> discord.ui.View:
    """Restored API wrapper."""
    view = discord.ui.View(timeout=60)
    view.add_item(
        discord.ui.Button(
            style=discord.ButtonStyle.success,
            label="Skip & Requeue",
            emoji="⏭️",
            custom_id=f"shadow_confirm_skip_{session_id}",
        )
    )
    view.add_item(
        discord.ui.Button(
            style=discord.ButtonStyle.secondary,
            label="Cancel",
            emoji="❌",
            custom_id=f"shadow_cancel_skip_{session_id}",
        )
    )
    return view


def create_end_confirmation_buttons(session_id: str) -> discord.ui.View:
    """Restored API wrapper."""
    view = discord.ui.View(timeout=60)
    view.add_item(
        discord.ui.Button(
            style=discord.ButtonStyle.danger,
            label="End for me only",
            emoji="👤",
            custom_id=f"shadow_end_me_{session_id}",
        )
    )
    view.add_item(
        discord.ui.Button(
            style=discord.ButtonStyle.danger,
            label="End for both",
            emoji="👥",
            custom_id=f"shadow_end_both_{session_id}",
        )
    )
    view.add_item(
        discord.ui.Button(
            style=discord.ButtonStyle.secondary,
            label="Cancel",
            emoji="❌",
            custom_id=f"shadow_cancel_end_{session_id}",
        )
    )
    return view
