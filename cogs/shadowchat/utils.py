"""
SHADOW CHAT - UTILITY FUNCTIONS (OPTIMIZED)
Shared helpers for ID generation, formatting, validation, etc.
"""

import random
import string
import datetime
import re
import math
from typing import Optional, Tuple
import discord
from discord import Embed

# Import constants (circular import handled)
try:
    from . import constants
except ImportError:
    import constants

# ==================== ID & CODE GENERATION ====================

def generate_session_id() -> str:
    """
    Generate a unique session ID.
    Format: shadow_XXXXXXXX (8 alphanumeric chars)
    """
    chars = string.ascii_lowercase + string.digits
    return f"shadow_{''.join(random.choices(chars, k=8))}"

def generate_user_code() -> str:
    chars = string.ascii_lowercase + string.digits
    code = ''.join(random.choices(chars, k=4))
    random.seed()
    return code

CUTE_WORDS = [
    "Meow", "Bubbles", "Snuggles", "Puddle", "Sprinkle",
    "Bumblebee", "Jellybean", "Fluffy", "Peaches", "Dandelion",
    "Marshmallow", "Pebbles", "Sunshine", "Raindrop", "Butterfly",
    "Starlight", "Chirpy", "Panda", "Kittens", "Honeybee",
    "Cupcake", "Sparkle", "Twinkle", "Cotton", "Berry",
    "Bunny", "Fuzzy", "Cuddle", "Sweetie", "Sugar",
    "Mitten", "Paws", "Whisker", "Glitter", "Daisy",
    "Petal", "Blossom", "Cocoa", "Muffin", "Noodle"
]

CUTE_EMOJIS = [
    "🐱", "🐶", "🐰", "🦊", "🦝", "🐼", "🐨", "🐻", "🦄", "🐥",
    "🐤", "🐣", "🦋", "🐝", "🐞", "🌸", "🌼", "🍩", "🧁", "🍪",
    "🍓", "🍒", "🍑", "☁️", "⭐", "✨", "🌟", "💫", "🎀", "🧸",
    "💐", "🍬", "🍭", "🐾", "😺", "😸", "😻", "🐯", "🫧", "🩷"
]

def format_thread_name(session_id: str, user1_id: int, user2_id: int) -> Tuple[str, str]:
    """
    Generate cute thread names for both sides including an emoji and the word 'Room'.
    Format: "<emoji> Room <Word1> <Word2>"
    Returns: (thread1_name, thread2_name)
    """
    # Seed random deterministically per pairing
    random.seed(f"{session_id}_{user1_id}_{user2_id}")

    # Pick unique emojis and words
    emoji_choices = random.sample(CUTE_EMOJIS, 2)
    selected_words = random.sample(CUTE_WORDS, 4)

    thread1_name = f"{emoji_choices[0]} Room {selected_words[0]} {selected_words[1]}"
    thread2_name = f"{emoji_choices[1]} Room {selected_words[2]} {selected_words[3]}"

    # Reset random seed
    random.seed()

    # Ensure thread names are within Discord's 100 char limit
    thread1_name = thread1_name[:100]
    thread2_name = thread2_name[:100]

    return thread1_name, thread2_name
    # Reset random seed
    random.seed()
    
    # Ensure thread names are within Discord's 100 char limit
    thread1_name = thread1_name[:100]
    thread2_name = thread2_name[:100]
    
    return thread1_name, thread2_name

# ==================== TIME & DURATION HELPERS ====================

def format_duration(seconds: int) -> str:
    """Format seconds into human-readable duration."""
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        minutes = seconds // 60
        seconds_remaining = seconds % 60
        if seconds_remaining > 0:
            return f"{minutes}m {seconds_remaining}s"
        return f"{minutes}m"
    else:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        if minutes > 0:
            return f"{hours}h {minutes}m"
        return f"{hours}h"

def format_duration_minutes(minutes: float) -> str:
    """Format minutes into human-readable duration."""
    if minutes < 1:
        seconds = int(minutes * 60)
        return f"{seconds}s"
    elif minutes < 60:
        whole_minutes = int(minutes)
        seconds = int((minutes - whole_minutes) * 60)
        if seconds > 0:
            return f"{whole_minutes}m {seconds}s"
        return f"{whole_minutes}m"
    else:
        hours = int(minutes // 60)
        remaining_minutes = int(minutes % 60)
        if remaining_minutes > 0:
            return f"{hours}h {remaining_minutes}m"
        return f"{hours}h"

def format_datetime(dt: datetime.datetime) -> str:
    """Format datetime for Discord timestamps."""
    return f"<t:{int(dt.timestamp())}:R>"

def format_datetime_absolute(dt: datetime.datetime) -> str:
    """Format datetime absolutely."""
    return f"<t:{int(dt.timestamp())}:f>"

def is_inactive(last_activity: datetime.datetime, timeout_minutes: int) -> bool:
    """Check if last activity is older than timeout."""
    cutoff = discord.utils.utcnow() - datetime.timedelta(minutes=timeout_minutes)
    return last_activity < cutoff

def calculate_estimated_wait(position: int, queue_size: int, avg_session_minutes: float = 15, active_sessions: int = 0, max_sessions: int = 2) -> float:
    """
    Calculate estimated wait time in minutes.
    
    Args:
        position: User's position in queue (1-based)
        queue_size: Total queue size
        avg_session_minutes: Average session duration in minutes
        active_sessions: Current active sessions count
        max_sessions: Maximum allowed sessions
    """
    # Base wait if no slots available
    if active_sessions >= max_sessions:
        base_wait = avg_session_minutes * 0.7  # Assume sessions are 70% through
    else:
        base_wait = 0.5  # Quick match if slots available
    
    # Each pair ahead in queue adds avg session time
    pairs_ahead = math.ceil((position - 1) / 2)
    
    estimated = base_wait + (pairs_ahead * avg_session_minutes)
    
    # Ensure minimum 30 seconds
    return max(0.5, estimated)

# ==================== DISCORD HELPERS ====================

async def get_or_create_webhook(channel: discord.TextChannel, name: str) -> discord.Webhook:
    """
    Get existing webhook or create new one.
    Only creates if no webhook with matching name exists.
    """
    # Try to find existing webhook
    webhooks = await channel.webhooks()
    for webhook in webhooks:
        if webhook.name == name and webhook.user == channel.guild.me:
            return webhook
    
    # Create new webhook
    return await channel.create_webhook(
        name=name,
        reason="Shadow Chat relay"
    )

async def get_thread_by_id(guild: discord.Guild, thread_id: int) -> Optional[discord.Thread]:
    """Retrieve a thread by ID, searching all channels."""
    for channel in guild.text_channels:
        if isinstance(channel, discord.Thread):
            if channel.id == thread_id:
                return channel
        # Also check active threads in the channel
        try:
            threads = channel.threads
            for thread in threads:
                if thread.id == thread_id:
                    return thread
        except AttributeError:
            continue
    return None

def create_queue_embed(position: int, queue_size: int, joined_at: datetime.datetime, estimated_wait_minutes: float = None) -> Embed:
    """Create embed for queue status with estimated wait time."""
    embed = Embed(
        title="🕶️ Shadow Chat Queue",
        color=constants.COLOR_ANONYMOUS
    )
    
    # Position info
    if position == 1 and queue_size == 1:
        status = "**You're next in line!**\nWaiting for a partner..."
    elif position <= 2 and queue_size >= 2:
        status = "**Match pending!**\nYou should be matched soon."
    else:
        status = f"You're in position **#{position}** of **{queue_size}**"
    
    embed.description = status
    
    # Wait time info
    if estimated_wait_minutes:
        wait_time_str = format_duration_minutes(estimated_wait_minutes)
        embed.add_field(
            name="⏰ Estimated Wait",
            value=f"**{wait_time_str}**",
            inline=True
        )
    
    # Queue joined time
    embed.add_field(
        name="📅 Joined",
        value=format_datetime(joined_at),
        inline=True
    )
    
    # Active sessions info
    embed.add_field(
        name="🎭 Active Sessions",
        value=f"**{constants.MAX_ACTIVE_SESSIONS}** max concurrent",
        inline=True
    )
    
    # Queue progress visualization
    if queue_size > 1:
        progress_bar = create_progress_bar(position, queue_size)
        embed.add_field(
            name="Progress",
            value=f"`{progress_bar}`",
            inline=False
        )
    
    embed.set_footer(text="You'll be notified when a match is found")
    return embed

def create_progress_bar(current: int, total: int, length: int = 10) -> str:
    """Create a simple progress bar."""
    if total <= 1:
        return "▰▰▰▰▰▰▰▰▰▰"
    
    progress = (current - 1) / (total - 1)
    filled = int(length * progress)
    bar = "▰" * filled + "▱" * (length - filled)
    return bar

def create_queue_stats_embed(queue_size: int, active_sessions: int, oldest_wait: datetime.datetime, 
                           estimated_next_wait: float, avg_session_duration: float) -> Embed:
    """Create embed for queue statistics."""
    embed = Embed(
        title="📊 Queue Statistics",
        color=constants.COLOR_ANONYMOUS,
        timestamp=discord.utils.utcnow()
    )
    
    embed.add_field(
        name="Queue Size",
        value=f"**{queue_size}** waiting",
        inline=True
    )
    
    embed.add_field(
        name="Active Sessions",
        value=f"**{active_sessions}/{constants.MAX_ACTIVE_SESSIONS}**",
        inline=True
    )
    
    if oldest_wait:
        wait_seconds = (discord.utils.utcnow() - oldest_wait).seconds
        embed.add_field(
            name="Oldest Wait",
            value=format_duration(wait_seconds),
            inline=True
        )
    
    if estimated_next_wait:
        embed.add_field(
            name="Next Match Wait",
            value=format_duration_minutes(estimated_next_wait),
            inline=True
        )
    
    embed.add_field(
        name="Avg Session Duration",
        value=f"{avg_session_duration:.1f}m",
        inline=True
    )
    
    # Queue status
    if queue_size == 0:
        status = "✅ Queue is empty"
    elif queue_size < 3:
        status = "🟡 Small queue"
    else:
        status = "🟠 Moderate queue"
    
    embed.add_field(
        name="Status",
        value=status,
        inline=False
    )
    
    embed.set_footer(text=f"Max {constants.MAX_ACTIVE_SESSIONS} concurrent sessions")
    return embed

def create_session_embed(session_id: str, partner_code: str) -> Embed:
    """Create welcome embed for new session."""
    embed = Embed(
        title="🎭 Shadow Chat Session Started",
        description="Your messages will be relayed anonymously to your partner.",
        color=constants.COLOR_ANONYMOUS
    )
    
    embed.add_field(
        name="Session ID",
        value=f"`{session_id}`",
        inline=False
    )
    
    embed.add_field(
        name="Privacy",
        value="✅ Fully anonymous",
        inline=True
    )
    
    # Rules/guidelines
    rules = [
        "• Messages are relayed via webhook",
        "• No personal info is shared",
        f"• Session auto-ends after {constants.SESSION_TIMEOUT_MINUTES}min inactivity",
        "• Use /reveal to mutually reveal identities"
    ]
    
    embed.add_field(
        name="Guidelines",
        value="\n".join(rules),
        inline=False
    )
    
    embed.set_footer(text="Type /end to leave at any time")
    return embed

def create_reveal_embed(username: str, avatar_url: str) -> Embed:
    """Create embed for identity reveal."""
    embed = Embed(
        title="👁️ Identity Revealed",
        description="Your partner has agreed to reveal their identity.",
        color=constants.COLOR_REVEALED
    )
    
    embed.add_field(
        name="Username",
        value=username,
        inline=True
    )
    
    if avatar_url:
        embed.set_thumbnail(url=avatar_url)
    
    embed.set_footer(text="Both users must consent to reveal")
    return embed

# ==================== VALIDATION & SAFETY ====================

def sanitize_content(content: str, max_length: int = 2000) -> str:
    """Sanitize message content for safety."""
    # Truncate if too long
    if len(content) > max_length:
        content = content[:max_length-3] + "..."
    
    # Remove excessive newlines
    content = re.sub(r'\n{4,}', '\n\n\n', content)
    
    return content

def is_safe_attachment(filename: str) -> bool:
    """Check if attachment filename is safe."""
    unsafe_extensions = {
        '.exe', '.bat', '.cmd', '.sh', '.ps1', '.js', '.vbs',
        '.jar', '.dll', '.sys', '.scr', '.pif', '.com', '.hta'
    }
    
    return not any(filename.lower().endswith(ext) for ext in unsafe_extensions)

async def check_user_permissions(user: discord.Member) -> Tuple[bool, Optional[str]]:
    """
    Check if user can use Shadow Chat.
    Returns: (is_allowed, reason_if_not)
    """
    # Check if user is bot
    if user.bot:
        return False, "Bots cannot use Shadow Chat"
    
    # Check if user is banned (will be checked via database elsewhere)
    # Additional server-specific checks could go here
    
    return True, None

# ==================== MESSAGE COMPONENTS ====================

def create_session_buttons(session_id: str) -> discord.ui.View:
    """Create button view for session management."""
    view = discord.ui.View(timeout=None)  # Persistent
    
    # End Chat button
    end_button = discord.ui.Button(
        style=discord.ButtonStyle.danger,
        label=constants.BUTTON_END_CHAT_LABEL,
        emoji=constants.BUTTON_END_CHAT_EMOJI,
        custom_id=f"shadow_end_{session_id}"
    )
    view.add_item(end_button)
    
    # Reveal Identity button
    reveal_button = discord.ui.Button(
        style=discord.ButtonStyle.primary,
        label=constants.BUTTON_REVEAL_LABEL,
        emoji=constants.BUTTON_REVEAL_EMOJI,
        custom_id=f"shadow_reveal_{session_id}"
    )
    view.add_item(reveal_button)
    
    return view

def create_queue_button() -> discord.ui.View:
    """Create persistent join queue button."""
    view = discord.ui.View(timeout=None)
    
    join_button = discord.ui.Button(
        style=discord.ButtonStyle.success,
        label=constants.BUTTON_JOIN_LABEL,
        emoji=constants.BUTTON_JOIN_EMOJI,
        custom_id="shadow_join_queue"
    )
    view.add_item(join_button)
    
    return view

def create_confirmation_buttons(session_id: str) -> discord.ui.View:
    """Create confirm/cancel buttons for reveal."""
    view = discord.ui.View(timeout=60)  # 60 second timeout
    
    confirm_button = discord.ui.Button(
        style=discord.ButtonStyle.success,
        label="Confirm Reveal",
        emoji="✅",
        custom_id=f"shadow_confirm_reveal_{session_id}"
    )
    
    cancel_button = discord.ui.Button(
        style=discord.ButtonStyle.secondary,
        label="Cancel",
        emoji="❌",
        custom_id=f"shadow_cancel_reveal_{session_id}"
    )
    
    view.add_item(confirm_button)
    view.add_item(cancel_button)
    
    return view

def create_session_header(session_id: str, partner_code: str) -> discord.Embed:
    """Create header embed for new session with control buttons and session codes."""
    embed = discord.Embed(
        description="# 🎭 Shadow Chat Session\n"
                    "**Your anonymous chat has started!** <:sayittagain:1389063232974749848>\n\n"
                    "You are now connected. Identities are hidden until mutually revealed.\n <a:dedede:1392118155790516234>",
        color=constants.COLOR_ANONYMOUS
    )
    
    # Field 1: The physical buttons (Clickable)
    embed.add_field(
        name="Session Controls",
        value="• **Reveal** — Propose mutual identity reveal\n"
              "• **Skip** — Find a new partner\n"
              "• **End** — Disconnect from this partner",
        inline=False
    )
    
    # Field 2: The text commands (Typable)
    embed.add_field(
        name="Commands",
        value="`/complain <reason>` — Report bad behavior\n"
              "`/skip` — Find a new partner\n"
              "`/reveal` — Reveal your identities\n"
              "`/end` — End the conversation",
        inline=False
    )
    
    # Session Codes
    embed.add_field(
        name="Session Codes",
        value=f"Session ID: `{session_id}`\n\n"
              "Use the Session ID to report any misdeeds to moderators.",
        inline=False
    )
    
    # Footer: Important for safety
    embed.set_footer(text="Safety Tip: Use /complain immediately if you feel unsafe. Do not share personal info.")
    embed.color = constants.SESSION_STARTED_COLOR
    
    return embed

def create_session_code_embed(session_id: str, partner_code: str, webhook_code: Optional[str] = None) -> discord.Embed:
    """Create embed displaying session and partner codes (plus optional webhook code)."""
    embed = discord.Embed(
        title="🔐 Session Codes",
        description="Keep these codes for your records. Do not share unless you consent.",
        color=constants.COLOR_ANONYMOUS
    )
    
    embed.add_field(
        name="Session ID",
        value=f"`{session_id}`",
        inline=False
    )

    embed.set_footer(text="Use /reveal or the Reveal button to mutually reveal identities")
    return embed

def generate_webhook_code() -> str:
    """Generate a random 4-character code for webhook naming."""
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))

def create_session_header_buttons(session_id: str) -> discord.ui.View:
    """Create header buttons for session control."""
    view = discord.ui.View(timeout=None)
    
    # Reveal button (starts reveal flow)
    reveal_btn = discord.ui.Button(
        style=discord.ButtonStyle.primary,
        label="Reveal Identity",
        emoji="👁️",
        custom_id=f"shadow_reveal_{session_id}",
        row=0
    )
    view.add_item(reveal_btn)
    
    # Skip button (re-add to queue, end for both after 10s)
    skip_btn = discord.ui.Button(
        style=discord.ButtonStyle.secondary,
        label="Skip Partner",
        emoji="⏭️",
        custom_id=f"shadow_skip_{session_id}",
        row=0
    )
    view.add_item(skip_btn)
    
    # End button (ends only for clicking user immediately)
    end_btn = discord.ui.Button(
        style=discord.ButtonStyle.danger,
        label="End Chat",
        emoji="⏹️",
        custom_id=f"shadow_end_{session_id}",
        row=0
    )
    view.add_item(end_btn)
    
    return view


def create_skip_confirmation_buttons(session_id: str) -> discord.ui.View:
    """Create confirmation buttons for skipping."""
    view = discord.ui.View(timeout=60)  # 60 second timeout
    
    confirm_button = discord.ui.Button(
        style=discord.ButtonStyle.success,
        label="Skip & Requeue",
        emoji="⏭️",
        custom_id=f"shadow_confirm_skip_{session_id}"
    )
    
    cancel_button = discord.ui.Button(
        style=discord.ButtonStyle.secondary,
        label="Cancel",
        emoji="❌",
        custom_id=f"shadow_cancel_skip_{session_id}"
    )
    
    view.add_item(confirm_button)
    view.add_item(cancel_button)
    
    return view

def create_end_confirmation_buttons(session_id: str) -> discord.ui.View:
    """Create confirmation buttons for ending chat."""
    view = discord.ui.View(timeout=60)  # 60 second timeout
    
    end_for_me_button = discord.ui.Button(
        style=discord.ButtonStyle.danger,
        label="End for me only",
        emoji="👤",
        custom_id=f"shadow_end_me_{session_id}"
    )
    
    end_for_both_button = discord.ui.Button(
        style=discord.ButtonStyle.danger,
        label="End for both",
        emoji="👥",
        custom_id=f"shadow_end_both_{session_id}"
    )
    
    cancel_button = discord.ui.Button(
        style=discord.ButtonStyle.secondary,
        label="Cancel",
        emoji="❌",
        custom_id=f"shadow_cancel_end_{session_id}"
    )
    
    view.add_item(end_for_me_button)
    view.add_item(end_for_both_button)
    view.add_item(cancel_button)
    
    return view

def create_queue_status_view() -> discord.ui.View:
    """Create view with queue status refresh button."""
    view = discord.ui.View(timeout=None)
    
    refresh_button = discord.ui.Button(
        style=discord.ButtonStyle.secondary,
        label="Refresh Status",
        emoji="🔄",
        custom_id="shadow_refresh_queue"
    )
    view.add_item(refresh_button)
    
    return view