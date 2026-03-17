import asyncio
import re
import aiohttp
from typing import Tuple, Optional

import discord
from discord import Color

# Import your Database class or get_db function
from .database import get_db


async def validate_role_name(
    name: str, guild: discord.Guild, user_id: int
) -> Tuple[bool, str]:
    """Validate role name"""
    if len(name) < 2:
        return False, "Role name must be at least 2 characters long."
    if len(name) > 100:
        return False, "Role name must be less than 100 characters."

    # Check for disallowed content
    disallowed_patterns = ["@everyone", "@here", "discord.gg/", "http://", "https://"]
    for pattern in disallowed_patterns:
        if pattern in name.lower():
            return False, "Role name contains disallowed content."

    # Check for excessive special characters
    special_chars = sum(1 for c in name if not c.isalnum() and not c.isspace())
    if special_chars > 10:
        return False, "Role name contains too many special characters."

    # Check if role name already exists (excluding user's own role)
    existing_role = discord.utils.find(
        lambda r: r.name.lower() == name.lower(), guild.roles
    )
    if existing_role:
        # Check if this is the user's own role
        db = await get_db()  # Get database instance
        user_role_data = await db.get_role(user_id)  # Changed from load_db()
        if not user_role_data or existing_role.id != user_role_data.get("role_id"):
            return False, "A role with this name already exists."

    return True, "✓ Name is valid"


async def validate_color(color_str: str) -> Tuple[bool, Optional[Color]]:
    """Validate and parse color string"""
    try:
        # Remove # if present
        if color_str.startswith("#"):
            color_str = color_str[1:]

        # Check if it's a valid hex color
        if len(color_str) not in [3, 6, 8]:
            return False, None

        # Convert to Color object
        color = Color.from_str(
            f"#{color_str}" if not color_str.startswith("#") else color_str
        )
        return True, color

    except Exception:
        return False, None


async def ensure_role_position(
    guild: discord.Guild, role: discord.Role, fixed_role_id: int
) -> bool:
    """Ensure role is correctly positioned above the fixed role with retry logic"""
    fixed_role = guild.get_role(fixed_role_id)
    if not fixed_role:
        print(f"Fixed role {fixed_role_id} not found")
        return False

    max_retries = 3
    for attempt in range(max_retries):
        try:
            # Get current position of fixed role
            current_pos = fixed_role.position

            # Move role to position above fixed role
            await role.edit(position=current_pos + 1)

            # Small delay for Discord to update
            await asyncio.sleep(1.5)

            # Refresh role object to get updated position
            role = guild.get_role(role.id)

            # Verify position
            if role.position > fixed_role.position:
                return True

            print(f"⚠ Position verification failed (attempt {attempt + 1})")

            if attempt < max_retries - 1:
                await asyncio.sleep(2)

        except discord.Forbidden:
            print("✗ Missing permissions to edit role position")
            return False
        except discord.HTTPException as e:
            print(f"⚠ HTTP error setting role position (attempt {attempt + 1}): {e}")
            if attempt == max_retries - 1:
                return False
            await asyncio.sleep(2)

    return False


def extract_emoji(text: str):
    """Extract emoji from text"""
    # Check for custom emoji format
    custom_emoji_pattern = r"<a?:(\w+):(\d+)>"
    custom_match = re.search(custom_emoji_pattern, text)

    # Check for unicode emoji
    unicode_emoji_pattern = re.compile(
        r"[\U0001F600-\U0001F64F"  # emoticons
        r"\U0001F300-\U0001F5FF"  # symbols & pictographs
        r"\U0001F680-\U0001F6FF"  # transport & map symbols
        r"\U0001F1E0-\U0001F1FF"  # flags (iOS)
        r"\U00002702-\U000027B0"  # dingbats
        r"\U000024C2-\U0001F251]",
        flags=re.UNICODE,
    )
    unicode_match = unicode_emoji_pattern.search(text)

    return custom_match, unicode_match


async def download_emoji_image(emoji_text: str) -> Optional[bytes]:
    """Download emoji image from Discord CDN with proper headers"""
    try:
        # Extract emoji ID from text
        custom_emoji_pattern = r"<a?:(\w+):(\d+)>"
        custom_match = re.search(custom_emoji_pattern, emoji_text)

        if not custom_match:
            return None

        emoji_id = int(custom_match.group(2))
        is_animated = emoji_text.startswith("<a:")
        extension = "gif" if is_animated else "png"
        url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{extension}"

        # Try multiple possible formats
        formats_to_try = [
            f"https://cdn.discordapp.com/emojis/{emoji_id}.{extension}",
            f"https://cdn.discordapp.com/emojis/{emoji_id}.gif",
            f"https://cdn.discordapp.com/emojis/{emoji_id}.png",
            f"https://cdn.discordapp.com/emojis/{emoji_id}.webp",
        ]

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://discord.com/",
            "DNT": "1",
        }

        async with aiohttp.ClientSession(headers=headers) as session:
            for url in formats_to_try:
                try:
                    async with session.get(url, timeout=10) as response:
                        if response.status == 200:
                            content_type = response.headers.get("Content-Type", "")
                            if "image" in content_type:
                                return await response.read()
                        elif response.status == 404:
                            continue  # Try next format
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    continue

        # If all formats fail, try one more time with different headers
        headers = {
            "User-Agent": "DiscordBot (https://github.com/discord/discord-api-docs, 1.0)",
            "Accept": "*/*",
        }

        async with aiohttp.ClientSession(headers=headers) as session:
            for url in formats_to_try:
                try:
                    async with session.get(url, timeout=10) as response:
                        if response.status == 200:
                            return await response.read()
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    continue

        return None

    except Exception as e:
        print(f"[ERROR] Failed to download emoji: {e}")
        return None


async def get_emoji_bytes(emoji: discord.Emoji) -> Optional[bytes]:
    """Get bytes from a Discord emoji object"""
    try:
        if emoji.url:
            return await download_emoji_image(str(emoji))
        return None
    except Exception as e:
        print(f"[ERROR] Failed to get emoji bytes: {e}")
        return None


def extract_emoji_id_and_name(
    emoji_text: str,
) -> Tuple[Optional[int], Optional[str], bool]:
    """Extract emoji ID, name, and animation status from text"""
    custom_emoji_pattern = r"<a?:(\w+):(\d+)>"
    custom_match = re.search(custom_emoji_pattern, emoji_text)

    if custom_match:
        emoji_name = custom_match.group(1)
        emoji_id = int(custom_match.group(2))
        is_animated = emoji_text.startswith("<a:")
        return emoji_id, emoji_name, is_animated

    return None, None, False


# New helper functions for SQLite database
async def is_user_banned(user_id: int) -> bool:
    """Check if user is banned (SQLite compatible)"""
    try:
        db = await get_db()
        return await db.is_user_banned(user_id)
    except Exception as e:
        print(f"[ERROR] Failed to check if user is banned: {e}")
        return False


async def save_role_to_db(user_id: int, role_data: dict) -> bool:
    """Save role to database (SQLite compatible)"""
    try:
        db = await get_db()
        return await db.save_role(user_id, role_data)
    except Exception as e:
        print(f"[ERROR] Failed to save role to database: {e}")
        return False


async def get_role_from_db(user_id: int) -> Optional[dict]:
    """Get role from database (SQLite compatible)"""
    try:
        db = await get_db()
        return await db.get_role(user_id)
    except Exception as e:
        print(f"[ERROR] Failed to get role from database: {e}")
        return None


async def delete_role_from_db(user_id: int) -> bool:
    """Delete role from database (SQLite compatible)"""
    try:
        db = await get_db()
        return await db.delete_role(user_id)
    except Exception as e:
        print(f"[ERROR] Failed to delete role from database: {e}")
        return False