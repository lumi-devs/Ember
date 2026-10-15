"""Confessions service layer - handles business logic for confessions management."""

import hashlib
import logging
import os
from datetime import UTC, datetime, timedelta

import discord

from core.database import DatabaseManager

logger = logging.getLogger("ember.confessions.service")


class ConfessionsService:
    """Business logic for confessions management."""

    def __init__(self):
        self.db = DatabaseManager("confessions")

    async def init_db(self) -> None:
        """Initialize the confessions database with proper schema."""
        await self.db.execute("PRAGMA foreign_keys = ON;")
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS confessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT,
                description TEXT NOT NULL,
                image_url TEXT,
                message_id INTEGER UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS replies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                confession_id INTEGER NOT NULL,
                parent_reply_id INTEGER,
                user_id INTEGER NOT NULL,
                description TEXT NOT NULL,
                image_url TEXT,
                message_id INTEGER,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (confession_id) REFERENCES confessions(id) ON DELETE CASCADE
            )
        """)
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_type TEXT NOT NULL,
                target_id INTEGER NOT NULL,
                reporter_id INTEGER NOT NULL,
                reason TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS confessban (
                user_id INTEGER PRIMARY KEY,
                banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                reason TEXT
            )
        """)
        await self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_confessions_user_id ON confessions(user_id)"
        )
        await self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_confessions_message_id ON confessions(message_id)"
        )
        await self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_replies_confession_id ON replies(confession_id)"
        )
        await self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_reports_target ON reports(target_type, target_id)"
        )
        await self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_confessban_user_id ON confessban(user_id)"
        )
        logger.info("Confessions database initialized")

    async def get_confession_by_id(self, confession_id: int) -> tuple | None:
        """Get confession by ID."""
        row = await self.db.get_one(
            "SELECT id, user_id, title, description, image_url, message_id, created_at "
            "FROM confessions WHERE id=?",
            (confession_id,),
        )
        return tuple(row) if row else None

    async def is_user_banned(self, user_id: int) -> bool:
        """Check if user is banned."""
        row = await self.db.get_one("SELECT 1 FROM confessban WHERE user_id=?", (user_id,))
        return row is not None

    async def can_user_confess(self, user_id: int, daily_limit: int = 3) -> tuple[bool, str, int]:
        """Check if user can submit a confession."""
        count_today = await self.get_daily_confession_count(user_id)

        if count_today >= daily_limit:
            return (
                False,
                f"❌ You've reached your daily limit of {daily_limit} confessions.",
                count_today,
            )

        return True, "✅ You can submit a confession.", count_today

    async def can_user_report(self, user_id: int, hourly_limit: int = 5) -> tuple[bool, str, int]:
        """Check if user can submit a report."""
        count_recent = await self.get_hourly_report_count(user_id)

        if count_recent >= hourly_limit:
            return (
                False,
                f"❌ You've reached your hourly report limit of {hourly_limit}.",
                count_recent,
            )

        return True, "✅ You can submit a report.", count_recent

    async def save_confession(self, user_id: int, title: str, description: str) -> int:
        """Save a new confession and return its ID."""
        result = await self.db.execute(
            "INSERT INTO confessions (user_id, title, description) VALUES (?, ?, ?)",
            (user_id, title, description),
        )
        return result.lastrowid

    async def update_confession_image(self, confession_id: int, image_url: str) -> None:
        """Update confession with image URL."""
        await self.db.execute(
            "UPDATE confessions SET image_url=? WHERE id=?", (image_url, confession_id)
        )

    async def update_confession_message_id(self, confession_id: int, message_id: int) -> None:
        """Update confession with message ID."""
        await self.db.execute(
            "UPDATE confessions SET message_id=? WHERE id=?", (message_id, confession_id)
        )

    async def get_max_confession_id(self) -> int | None:
        """Get the maximum confession ID."""
        row = await self.db.get_one("SELECT MAX(id) FROM confessions")
        return row[0] if row and row[0] is not None else None

    async def get_confession_message_id(self, confession_id: int) -> int | None:
        """Get message ID for a confession."""
        row = await self.db.get_one(
            "SELECT message_id FROM confessions WHERE id=?", (confession_id,)
        )
        return row[0] if row and row[0] is not None else None

    async def save_reply(
        self,
        confession_id: int,
        author_id: int,
        content: str,
        parent_reply_id: int | None = None,
    ) -> int:
        """Save a reply and return its ID."""
        result = await self.db.execute(
            "INSERT INTO replies (confession_id, parent_reply_id, user_id, description) VALUES (?, ?, ?, ?)",
            (confession_id, parent_reply_id, author_id, content),
        )
        return result.lastrowid

    async def update_reply_message_id(self, reply_id: int, message_id: int) -> None:
        """Update reply with message ID."""
        await self.db.execute("UPDATE replies SET message_id=? WHERE id=?", (message_id, reply_id))

    async def get_reply_message_id(self, reply_id: int) -> int | None:
        """Get message ID for a reply."""
        row = await self.db.get_one("SELECT message_id FROM replies WHERE id=?", (reply_id,))
        return row[0] if row and row[0] is not None else None

    async def save_report(
        self, target_type: str, target_id: int, reporter_id: int, reason: str | None = None
    ) -> None:
        """Save a report."""
        await self.db.execute(
            "INSERT INTO reports (target_type, target_id, reporter_id, reason) VALUES (?, ?, ?, ?)",
            (target_type, target_id, reporter_id, reason),
        )

    async def delete_confession(self, confession_id: int) -> tuple[int, int, str, str, int]:
        """Delete a confession and return its data."""
        row = await self.db.get_one(
            "SELECT id, user_id, title, description, message_id " "FROM confessions WHERE id=?",
            (confession_id,),
        )
        if not row:
            raise ValueError(f"Confession {confession_id} not found")

        # Delete replies and confession
        await self.db.execute("DELETE FROM replies WHERE confession_id=?", (confession_id,))
        await self.db.execute("DELETE FROM confessions WHERE id=?", (confession_id,))

        return (row[0], row[1], row[2], row[3], row[4])

    async def renumber_confessions(self, confession_id: int) -> list[tuple[int, int]]:
        """Renumber confessions after deletion and return updated IDs."""
        rows = await self.db.query(
            "SELECT id, message_id FROM confessions WHERE id > ? ORDER BY id ASC", (confession_id,)
        )

        # Update each confession's ID
        updated_confessions = []
        for old_id, msg_id in rows:
            new_id = old_id - 1
            await self.db.execute("UPDATE confessions SET id=? WHERE id=?", (new_id, old_id))
            # Update reply references
            await self.db.execute(
                "UPDATE replies SET confession_id=? WHERE confession_id=?",
                (new_id, old_id),
            )
            updated_confessions.append((new_id, msg_id))

        return updated_confessions

    async def update_sqlite_sequence(self, max_id: int) -> None:
        """Update SQLite sequence after renumbering."""
        await self.db.execute(
            "UPDATE sqlite_sequence SET seq=? WHERE name='confessions'", (max_id - 1,)
        )

    async def get_confession_by_message_id(self, message_id: int) -> tuple | None:
        """Get confession by message ID."""
        row = await self.db.get_one(
            "SELECT id, user_id, title, description, image_url, message_id, created_at "
            "FROM confessions WHERE message_id=?",
            (message_id,),
        )
        return tuple(row) if row else None

    # ========== Helper Methods from helpers.py ==========

    def sha256_user_id(self, user_id: int) -> str:
        """Generate a stable SHA-256 hex digest for a Discord user ID."""
        return hashlib.sha256(str(user_id).encode()).hexdigest()

    async def lookup_user_id_from_hash(self, hash_str: str) -> int | None:
        """Look up user ID from SHA-256 hash."""
        hash_str = hash_str.lower().strip()
        if len(hash_str) != 64:
            return None
        try:
            user_ids: set[int] = set()
            for row in await self.db.fetch("SELECT DISTINCT user_id FROM confessions"):
                user_ids.add(row["user_id"])
            for row in await self.db.fetch("SELECT DISTINCT user_id FROM replies"):
                user_ids.add(row["user_id"])
            for uid in user_ids:
                if self.sha256_user_id(uid) == hash_str:
                    return uid
            return None
        except Exception as e:
            logger.error("[hash_lookup] Error: %s", e)
            return None

    async def get_hourly_reply_count(self, user_id: int) -> int:
        """Get number of replies in the last hour."""
        try:
            one_hour_ago = datetime.now(UTC) - timedelta(hours=1)
            row = await self.db.get_one(
                "SELECT COUNT(*) FROM replies WHERE user_id = ? AND timestamp >= ?",
                (user_id, one_hour_ago.isoformat()),
            )
            return row[0] if row else 0
        except Exception as e:
            logger.error(f"[ConfessionsService] Error getting hourly reply count: {e}")
            return 0

    async def can_user_reply(self, user_id: int, hourly_limit: int = 8) -> tuple[bool, str, int]:
        """Check if user can reply to confessions."""
        current_count = await self.get_hourly_reply_count(user_id)
        remaining = hourly_limit - current_count

        if current_count >= hourly_limit:
            try:
                datetime.now(UTC) - timedelta(hours=1)
                row = await self.db.get_one(
                    "SELECT timestamp FROM replies WHERE user_id = ? ORDER BY timestamp ASC LIMIT 1",
                    (user_id,),
                )
                if row:
                    oldest_time = datetime.datetime.fromisoformat(row[0].replace("Z", "+00:00"))
                    now = datetime.now(UTC)
                    reset_in = 3600 - (now - oldest_time).total_seconds()
                    if reset_in > 0:
                        minutes = int(reset_in // 60)
                        seconds = int(reset_in % 60)
                        return (
                            False,
                            f"❌ You've reached the hourly limit of {hourly_limit} replies.\n"
                            f"⏰ You can reply again in {minutes}m {seconds}s.\n"
                            f"📊 You've used {current_count}/{hourly_limit} replies this hour.",
                            current_count,
                        )
            except Exception as e:
                logger.error(f"[ConfessionsService] Error calculating reset time: {e}")
            return (
                False,
                f"❌ You've reached the hourly limit of {hourly_limit} replies.\n"
                f"⏰ Please wait an hour before replying again.\n"
                f"📊 You've used {current_count}/{hourly_limit} replies this hour.",
                current_count,
            )

        return (
            True,
            f"✅ You have {remaining} repl{'y' if remaining == 1 else 'ies'} remaining this hour.",
            current_count,
        )

    async def get_user_reply_stats(self, user_id: int) -> dict:
        """Get reply statistics for a user."""
        try:
            total_row = await self.db.get_one(
                "SELECT COUNT(*) FROM replies WHERE user_id = ?", (user_id,)
            )
            total = total_row[0] if total_row else 0

            one_hour_ago = datetime.now(UTC) - timedelta(hours=1)
            hourly_row = await self.db.get_one(
                "SELECT COUNT(*) FROM replies WHERE user_id = ? AND timestamp >= ?",
                (user_id, one_hour_ago.isoformat()),
            )
            hourly = hourly_row[0] if hourly_row else 0

            today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
            daily_row = await self.db.get_one(
                "SELECT COUNT(*) FROM replies WHERE user_id = ? AND timestamp >= ?",
                (user_id, today_start.isoformat()),
            )
            daily = daily_row[0] if daily_row else 0

            return {"total_replies": total, "hourly_replies": hourly, "daily_replies": daily}
        except Exception as e:
            logger.error(f"[ConfessionsService] Error getting user reply stats: {e}")
            return {"total_replies": 0, "hourly_replies": 0, "daily_replies": 0}

    async def get_user_join_date(self, guild, user_id: int) -> datetime | None:
        """Get the date a user joined the guild."""
        try:
            member = guild.get_member(user_id)
            if member and member.joined_at:
                return member.joined_at
        except Exception as e:
            logger.error(f"[ConfessionsService] Error getting join date for user {user_id}: {e}")
        return None

    async def can_user_confess_with_join_check(
        self, guild, user_id: int, daily_limit: int = 3
    ) -> tuple[bool, str, int]:
        """Check if user can make a confession (including join date check)."""
        join_date = await self.get_user_join_date(guild, user_id)
        if join_date:
            time_in_server = datetime.now(UTC) - join_date
            if time_in_server.total_seconds() < 86400:
                hours_left = int((86400 - time_in_server.total_seconds()) / 3600)
                minutes_left = int(((86400 - time_in_server.total_seconds()) % 3600) / 60)
                return (
                    False,
                    f"⏳ You need to be in the server for at least **1 day** before making confessions.\n"
                    f"📅 Please wait **{hours_left}h {minutes_left}m** more.",
                    0,
                )

        count_today = await self.get_daily_confession_count(user_id)

        if count_today >= daily_limit:
            return (
                False,
                f"❌ You've reached the daily limit of **{daily_limit}** confessions.\n"
                f"📊 You've used {count_today}/{daily_limit} today.\n"
                f"💡 Use replies to engage with existing confessions instead!",
                count_today,
            )

        return (
            True,
            f"✅ You have **{daily_limit - count_today}** confession{'s' if daily_limit - count_today != 1 else ''} remaining today.",
            count_today,
        )

    async def get_daily_confession_count(self, user_id: int) -> int:
        """Get number of confessions submitted today."""
        try:
            today_start = datetime.now(UTC).date()
            row = await self.db.get_one(
                "SELECT COUNT(*) FROM confessions WHERE user_id = ? AND DATE(created_at) = DATE(?)",
                (user_id, today_start.isoformat()),
            )
            return row[0] if row else 0
        except Exception as e:
            logger.error(f"[ConfessionsService] Error getting daily confession count: {e}")
            return 0

    async def get_user_stats(self, user_id: int) -> dict:
        """Get comprehensive statistics for a user."""
        try:
            total_row = await self.db.get_one(
                "SELECT COUNT(*) FROM confessions WHERE user_id = ?", (user_id,)
            )
            total = total_row[0] if total_row else 0

            today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
            today_row = await self.db.get_one(
                "SELECT COUNT(*) FROM confessions WHERE user_id = ? AND created_at >= ?",
                (user_id, today_start.isoformat()),
            )
            today = today_row[0] if today_row else 0

            replies_row = await self.db.get_one(
                "SELECT COUNT(*) FROM replies WHERE user_id = ?", (user_id,)
            )
            replies = replies_row[0] if replies_row else 0

            return {
                "total_confessions": total,
                "today_confessions": today,
                "total_replies": replies,
            }
        except Exception as e:
            logger.error(f"[ConfessionsService] Error getting user stats: {e}")
            return {"total_confessions": 0, "today_confessions": 0, "total_replies": 0}

    async def get_user_confessions(self, user_id: int) -> list[dict]:
        """Get all confessions by a user."""
        return await self.db.query(
            "SELECT * FROM confessions WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
        )

    async def get_confession_replies(self, confession_id: int) -> list[dict]:
        """Get all replies for a confession (including parent_reply_id)."""
        return await self.db.query(
            "SELECT id, parent_reply_id, user_id, description, image_url, message_id, timestamp FROM replies WHERE confession_id=? ORDER BY timestamp ASC",
            (confession_id,),
        )

    async def get_all_confessions_with_message_ids(self) -> list[tuple[int, int]]:
        """Get all confessions that have a message_id (for persistent view loading)."""
        rows = await self.db.query(
            "SELECT id, message_id FROM confessions WHERE message_id IS NOT NULL"
        )
        return [(row["id"], row["message_id"]) for row in rows]

    async def get_all_replies_with_message_ids(self) -> list[tuple[int, int]]:
        """Get all replies that have a message_id (for persistent view loading)."""
        rows = await self.db.query(
            "SELECT id, message_id FROM replies WHERE message_id IS NOT NULL"
        )
        return [(row["id"], row["message_id"]) for row in rows]

    async def get_confession_reply_ids(self, confession_id: int) -> list[tuple[int, int]]:
        """Get reply IDs and message IDs for a confession."""
        rows = await self.db.query(
            "SELECT id, message_id FROM replies WHERE confession_id=? AND message_id IS NOT NULL",
            (confession_id,),
        )
        return [(row["id"], row["message_id"]) for row in rows]

    async def get_reply_by_id(self, reply_id: int) -> dict | None:
        """Get a reply by its ID."""
        row = await self.db.get_one("SELECT * FROM replies WHERE id=?", (reply_id,))
        return dict(row) if row else None

    async def ban_user(self, user_id: int, reason: str | None = None) -> bool:
        """Ban a user from submitting confessions."""
        try:
            await self.db.execute(
                "INSERT OR REPLACE INTO confessban (user_id, reason) VALUES (?, ?)",
                (user_id, reason),
            )
            return True
        except Exception as e:
            logger.error(f"[ConfessionsService] Error banning user {user_id}: {e}")
            return False

    async def unban_user(self, user_id: int) -> bool:
        """Unban a user from submitting confessions."""
        try:
            result = await self.db.delete("confessban", "user_id = ?", (user_id,))
            return result > 0
        except Exception as e:
            logger.error(f"[ConfessionsService] Error unbanning user {user_id}: {e}")
            return False

    async def get_banned_users(self) -> list[dict]:
        """Get all banned users."""
        return await self.db.query(
            "SELECT user_id, reason, banned_at FROM confessban ORDER BY banned_at DESC"
        )

    async def get_hourly_report_count(self, user_id: int) -> int:
        """Get number of reports in the last hour."""
        try:
            one_hour_ago = datetime.now(UTC) - timedelta(hours=1)
            row = await self.db.get_one(
                "SELECT COUNT(*) FROM reports WHERE reporter_id = ? AND timestamp >= ?",
                (user_id, one_hour_ago.isoformat()),
            )
            return row[0] if row else 0
        except Exception as e:
            logger.error(f"[ConfessionsService] Error getting hourly report count: {e}")
            return 0

    async def delete_user_data(self, user_id: int) -> None:
        """Delete all data associated with a user (GDPR compliance)."""
        # Delete user's confessions (cascades to replies)
        await self.db.execute("DELETE FROM confessions WHERE user_id = ?", (user_id,))
        # Delete replies where user is the author
        await self.db.execute("DELETE FROM replies WHERE user_id = ?", (user_id,))
        # Delete reports where user is the reporter
        await self.db.execute("DELETE FROM reports WHERE reporter_id = ?", (user_id,))
        # Remove from ban list if present
        await self.db.execute("DELETE FROM confessban WHERE user_id = ?", (user_id,))

    async def process_media(
        self, bot, content: str, attachments: list, media_channel_id: int
    ) -> str | None:
        """Process media uploads and return image URL."""
        try:
            import io
            from urllib.parse import urlparse

            import aiohttp

            media_channel = bot.get_channel(media_channel_id)
            if not media_channel:
                logger.warning(f"[ConfessionsService] Media channel {media_channel_id} not found")
                return None

            image_url = None
            if attachments:
                for attachment in attachments:
                    if attachment.content_type and attachment.content_type.startswith("image/"):
                        try:
                            file_bytes = await attachment.read()
                            file = discord.File(
                                io.BytesIO(file_bytes), filename=attachment.filename
                            )
                            msg = await media_channel.send(file=file)
                            image_url = msg.attachments[0].url
                            break
                        except Exception as e:
                            logger.error(
                                f"[ConfessionsService] Error uploading attachment {attachment.filename}: {e}"
                            )
                            continue

            if not image_url and content:
                import re

                url_match = re.search(r"https?://\S+", content)
                if url_match:
                    url = url_match.group(0)
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
                    ]
                    if (
                        any(path.endswith(ext) for ext in image_extensions)
                        or "cdn.discordapp.com" in parsed.netloc
                    ):
                        try:
                            async with aiohttp.ClientSession() as session:
                                async with session.get(url) as response:
                                    if response.status == 200:
                                        content_type = response.headers.get("Content-Type", "")
                                        if content_type.startswith("image/"):
                                            image_data = await response.read()
                                            filename = os.path.basename(parsed.path) or "image.jpg"
                                            file = discord.File(
                                                io.BytesIO(image_data), filename=filename
                                            )
                                            msg = await media_channel.send(file=file)
                                            image_url = msg.attachments[0].url
                        except Exception as e:
                            logger.error(
                                f"[ConfessionsService] Error downloading/uploading image from URL: {e}"
                            )

            return image_url
        except Exception as e:
            logger.error(f"[ConfessionsService] Error in process_media: {e}")
            return None
