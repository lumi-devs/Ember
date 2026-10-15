"""Confessions helper functions - now using ConfessionsService for all DB operations."""

import datetime
import hashlib
import logging
import re
from urllib.parse import urlparse

import discord

from .services.confessions_service import ConfessionsService

log = logging.getLogger("ember.confessions")

# Global service instance (will be set by cog)
_service: ConfessionsService | None = None


def set_service(service: ConfessionsService):
    """Set the global service instance."""
    global _service
    _service = service


# ==============================
# SHA-256 ANONYMIZATION UTILS
# ==============================


def sha256_user_id(user_id: int) -> str:
    """Generate a stable SHA-256 hex digest for a Discord user ID."""
    return hashlib.sha256(str(user_id).encode()).hexdigest()


async def lookup_user_id_from_hash(hash_str: str) -> int | None:
    """Look up user ID from SHA-256 hash."""
    if _service is None:
        log.error("Service not initialized")
        return None
    return await _service.lookup_user_id_from_hash(hash_str)


# ==============================
# GENERAL HELPERS
# ==============================


def extract_url(text: str) -> str | None:
    """Extract first URL from text."""
    URL_REGEX = re.compile(r"https?://\S+")
    match = URL_REGEX.search(text)
    return match.group(0) if match else None


def is_image_url(url: str) -> bool:
    """Check if URL points to an image."""
    if not url:
        return False
    parsed = urlparse(url)
    path = parsed.path.lower()
    image_extensions = [
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".bmp",
        ".tiff",
        ".svg",
        ".jfif",
        ".pjpeg",
        ".pjp",
    ]
    for ext in image_extensions:
        if path.endswith(ext):
            return True
    if "cdn.discordapp.com" in parsed.netloc and "/attachments/" in path:
        last_segment = path.split("/")[-1]
        for pattern in ["png", "jpg", "jpeg", "gif", "webp", "PNG", "JPG", "JPEG", "GIF", "WEBP"]:
            if pattern in last_segment:
                return True
    query = parsed.query.lower()
    if "format=" in query or "type=" in query:
        for part in query.split("&"):
            if "format=" in part or "type=" in part:
                value = part.split("=")[1] if "=" in part else ""
                if any(t in value for t in ["jpg", "jpeg", "png", "gif", "webp"]):
                    return True
    return False


async def get_hourly_reply_count(user_id: int) -> int:
    """Get number of replies in the last hour."""
    if _service is None:
        log.error("Service not initialized")
        return 0
    return await _service.get_hourly_reply_count(user_id)


async def can_user_reply(user_id: int, hourly_limit: int = 5) -> tuple[bool, str, int]:
    """Check if user can reply to confessions."""
    if _service is None:
        log.error("Service not initialized")
        return True, "", 0
    return await _service.can_user_reply(user_id, hourly_limit)


async def get_user_reply_stats(user_id: int) -> dict:
    """Get reply statistics for a user."""
    if _service is None:
        log.error("Service not initialized")
        return {"total_replies": 0, "hourly_replies": 0, "daily_replies": 0}
    return await _service.get_user_reply_stats(user_id)


async def get_user_join_date(guild: discord.Guild, user_id: int) -> datetime.datetime | None:
    """Get the date a user joined the guild."""
    if _service is None:
        log.error("Service not initialized")
        return None
    return await _service.get_user_join_date(guild, user_id)


async def can_user_confess(
    guild: discord.Guild, user_id: int, daily_limit: int = 3
) -> tuple[bool, str, int]:
    """Check if user can make a confession."""
    if _service is None:
        log.error("Service not initialized")
        return True, "", 0
    return await _service.can_user_confess_with_join_check(guild, user_id, daily_limit)


async def get_daily_confession_count(user_id: int) -> int:
    """Get number of confessions submitted today."""
    if _service is None:
        log.error("Service not initialized")
        return 0
    return await _service.get_daily_confession_count(user_id)


async def get_user_stats(user_id: int) -> dict:
    """Get comprehensive statistics for a user."""
    if _service is None:
        log.error("Service not initialized")
        return {"total_confessions": 0, "today_confessions": 0, "total_replies": 0}
    return await _service.get_user_stats(user_id)


async def get_user_confessions(user_id: int):
    """Get all confessions by a user."""
    if _service is None:
        log.error("Service not initialized")
        return []
    return await _service.get_user_confessions(user_id)


async def get_confession_replies(confession_id: int):
    """Get all replies for a confession."""
    if _service is None:
        log.error("Service not initialized")
        return []
    return await _service.get_confession_replies(confession_id)


async def get_confession_by_id(confession_id: int):
    """Get confession by ID."""
    if _service is None:
        log.error("Service not initialized")
        return None
    return await _service.get_confession_by_id(confession_id)


async def ban_user(user_id: int, reason: str = None):
    """Ban a user from submitting confessions."""
    if _service is None:
        log.error("Service not initialized")
        return False
    return await _service.ban_user(user_id, reason)


async def unban_user(user_id: int):
    """Unban a user from submitting confessions."""
    if _service is None:
        log.error("Service not initialized")
        return False
    return await _service.unban_user(user_id)


async def is_user_banned(user_id: int) -> bool:
    """Check if user is banned."""
    if _service is None:
        log.error("Service not initialized")
        return False
    return await _service.is_user_banned(user_id)


async def get_hourly_report_count(user_id: int) -> int:
    """Get number of reports in the last hour."""
    if _service is None:
        log.error("Service not initialized")
        return 0
    return await _service.get_hourly_report_count(user_id)


async def can_user_report(user_id: int, hourly_limit: int = 5) -> tuple[bool, str, int]:
    """Check if user can submit a report."""
    if _service is None:
        log.error("Service not initialized")
        return True, "", 0
    return await _service.can_user_report(user_id, hourly_limit)


async def process_media(
    bot, content: str, attachments: list[discord.Attachment], media_channel_id: int
) -> str | None:
    """Process media uploads and return image URL."""
    if _service is None:
        log.error("Service not initialized")
        return None
    return await _service.process_media(bot, content, attachments, media_channel_id)


async def init_db():
    """Initialize database - delegates to service."""
    if _service is None:
        log.error("Service not initialized, cannot init DB")
        return
    await _service.init_db()


async def get_parent_reference(reply_id: int) -> tuple[str, str | None]:
    """Return a (reference_string, quote_string) pair for the parent of a reply.

    Used by ReplyLayoutView to build the replying-to quote block.
    """
    if _service is None:
        return "unknown", None

    row = await _service.get_reply_by_id(reply_id)
    if row is None:
        return "unknown", None

    parent_id = row.get("parent_reply_id")
    if not parent_id:
        return f"Confession #{row.get('confession_id', '?')}", None

    parent = await _service.get_reply_by_id(parent_id)
    if parent is None:
        return f"Reply #{parent_id} (deleted)", None

    snippet = (parent.get("description") or "")[:80]
    if len(parent.get("description") or "") > 80:
        snippet += "…"
    return f"Reply #{parent_id}", f"> {snippet}" if snippet else None
