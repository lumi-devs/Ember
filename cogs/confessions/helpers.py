import datetime
import hashlib
import re
import aiosqlite
import discord
import aiohttp
import io
import os
from typing import Optional, List
from urllib.parse import urlparse
from pathlib import Path

TENOR_REGEX = r"(https?:\/\/(www\.)?tenor\.com\/\S+)"
DB_PATH = "data/confession.db"
DB_BACKUP_PATH = "data/confession_backup.db"
CONFESSION_CHANNEL_ID = int(os.getenv("CONFESSION_CHANNEL_ID"))  # Set this in your .env file
REPORT_CHANNEL_ID = int(os.getenv("REPORT_CHANNEL_ID"))
MEDIA_CHANNEL_ID = int(os.getenv("MEDIA_CHANNEL_ID"))
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID"))       # Admin-only log — full user info (unanonymized)
MOD_LOG_CHANNEL_ID = int(os.getenv("MOD_LOG_CHANNEL_ID"))                     # ⚠️ TODO: Set to your anonymized mod-log channel ID
URL_REGEX = re.compile(r"https?://\S+")


# ==============================
# SHA-256 ANONYMIZATION UTILS
# ==============================

def sha256_user_id(user_id: int) -> str:
    """Generate a stable SHA-256 hex digest for a Discord user ID."""
    return hashlib.sha256(str(user_id).encode()).hexdigest()


async def lookup_user_id_from_hash(hash_str: str) -> Optional[int]:
    """
    Reverse-lookup a user_id from its SHA-256 hash by iterating all known
    user IDs in the database.  Fine for typical server sizes.
    Returns None if no match is found.
    """
    hash_str = hash_str.lower().strip()
    if len(hash_str) != 64:
        return None
    try:
        user_ids: set[int] = set()
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT DISTINCT user_id FROM confessions") as cur:
                async for (uid,) in cur:
                    user_ids.add(uid)
            async with db.execute("SELECT DISTINCT user_id FROM replies") as cur:
                async for (uid,) in cur:
                    user_ids.add(uid)
        for uid in user_ids:
            if sha256_user_id(uid) == hash_str:
                return uid
        return None
    except Exception as e:
        print(f"[hash_lookup] Error: {e}")
        return None


# ==============================
# GENERAL HELPERS
# ==============================

def extract_url(text: str) -> Optional[str]:
    """Extract the first URL from text if present."""
    match = URL_REGEX.search(text)
    return match.group(0) if match else None


async def get_hourly_reply_count(user_id: int) -> int:
    """Get the number of replies a user has made in the last hour."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            one_hour_ago = discord.utils.utcnow() - datetime.timedelta(hours=1)
            cursor = await db.execute(
                "SELECT COUNT(*) FROM replies WHERE user_id = ? AND timestamp >= ?",
                (user_id, one_hour_ago.isoformat()),
            )
            return (await cursor.fetchone())[0]
    except Exception as e:
        print(f"Error getting hourly reply count: {e}")
        return 0


async def can_user_reply(user_id: int, hourly_limit: int = 5) -> tuple[bool, str, int]:
    """
    Check if a user can make a reply based on hourly limit.

    Returns: (can_reply: bool, reason: str, current_count: int)
    """
    try:
        current_count = await get_hourly_reply_count(user_id)

        if current_count >= hourly_limit:
            try:
                async with aiosqlite.connect(DB_PATH) as db:
                    one_hour_ago = discord.utils.utcnow() - datetime.timedelta(hours=1)
                    cursor = await db.execute(
                        "SELECT timestamp FROM replies WHERE user_id = ? AND timestamp >= ? "
                        "ORDER BY timestamp ASC LIMIT 1",
                        (user_id, one_hour_ago.isoformat()),
                    )
                    result = await cursor.fetchone()
                    if result:
                        # BUG FIX: was `datetime.fromisoformat` — module imported, not class
                        oldest_time = datetime.datetime.fromisoformat(result[0])
                        reset_in = (
                            3600
                            - (discord.utils.utcnow() - oldest_time).total_seconds()
                        )
                        minutes = int(reset_in // 60)
                        seconds = int(reset_in % 60)
                        return (
                            False,
                            f"❌ You've reached the hourly limit of {hourly_limit} replies. "
                            f"You can reply again in {minutes}m {seconds}s.",
                            current_count,
                        )
            except Exception as e:
                print(f"Error calculating reset time: {e}")

            return (
                False,
                f"❌ You've reached the hourly limit of {hourly_limit} replies. "
                "Please wait an hour before replying again.",
                current_count,
            )

        return True, "", current_count

    except Exception as e:
        print(f"Error checking reply limit: {e}")
        return True, "", 0


async def get_user_reply_stats(user_id: int) -> dict:
    """Get detailed reply statistics for a user."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM replies WHERE user_id = ?", (user_id,)
            )
            total = (await cursor.fetchone())[0]

            one_hour_ago = discord.utils.utcnow() - datetime.timedelta(hours=1)
            cursor = await db.execute(
                "SELECT COUNT(*) FROM replies WHERE user_id = ? AND timestamp >= ?",
                (user_id, one_hour_ago.isoformat()),
            )
            hourly = (await cursor.fetchone())[0]

            today_start = discord.utils.utcnow().replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            cursor = await db.execute(
                "SELECT COUNT(*) FROM replies WHERE user_id = ? AND timestamp >= ?",
                (user_id, today_start.isoformat()),
            )
            daily = (await cursor.fetchone())[0]

            return {
                "total_replies": total,
                "hourly_replies": hourly,
                "daily_replies": daily,
            }
    except Exception as e:
        print(f"Error getting user reply stats: {e}")
        return {"total_replies": 0, "hourly_replies": 0, "daily_replies": 0}


async def get_user_join_date(
    guild: discord.Guild, user_id: int
) -> Optional[datetime.datetime]:
    """Get the date when a user joined the guild."""
    try:
        member = guild.get_member(user_id)
        if member and member.joined_at:
            return member.joined_at
    except Exception as e:
        print(f"Error getting join date for user {user_id}: {e}")
    return None


async def can_user_confess(
    guild: discord.Guild, user_id: int, daily_limit: int = 3
) -> tuple[bool, str]:
    """
    Check if a user can make a confession based on:
    - Must have been in server for at least 1 day
    - Cannot exceed daily limit

    Returns: (can_confess: bool, reason: str)
    """
    join_date = await get_user_join_date(guild, user_id)
    if join_date:
        time_in_server = discord.utils.utcnow() - join_date
        if time_in_server.total_seconds() < 86400:
            hours_left = 24 - (time_in_server.total_seconds() / 3600)
            return (
                False,
                f"⏳ You need to be in the server for at least 1 day before making confessions. "
                f"Please wait {hours_left:.1f} more hours.",
            )

    try:
        async with aiosqlite.connect(DB_PATH) as db:
            today_start = discord.utils.utcnow().replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            cursor = await db.execute(
                "SELECT COUNT(*) FROM confessions WHERE user_id = ? AND created_at >= ?",
                (user_id, today_start.isoformat()),
            )
            count = (await cursor.fetchone())[0]

            if count >= daily_limit:
                return (
                    False,
                    f"❌ You've reached the daily limit of {daily_limit} confessions. "
                    "Please use replies to engage with existing confessions instead.",
                )
    except Exception as e:
        print(f"Error checking daily confession count: {e}")

    return True, ""


async def get_daily_confession_count(user_id: int) -> int:
    """Get the number of confessions a user has made today."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            today_start = discord.utils.utcnow().replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            cursor = await db.execute(
                "SELECT COUNT(*) FROM confessions WHERE user_id = ? AND created_at >= ?",
                (user_id, today_start.isoformat()),
            )
            return (await cursor.fetchone())[0]
    except Exception as e:
        print(f"Error getting daily confession count: {e}")
        return 0


async def get_user_stats(user_id: int) -> dict:
    """Get comprehensive user statistics for confessions."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM confessions WHERE user_id = ?", (user_id,)
            )
            total = (await cursor.fetchone())[0]

            today_start = discord.utils.utcnow().replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            cursor = await db.execute(
                "SELECT COUNT(*) FROM confessions WHERE user_id = ? AND created_at >= ?",
                (user_id, today_start.isoformat()),
            )
            today = (await cursor.fetchone())[0]

            cursor = await db.execute(
                "SELECT COUNT(*) FROM replies WHERE user_id = ?", (user_id,)
            )
            replies = (await cursor.fetchone())[0]

            return {
                "total_confessions": total,
                "today_confessions": today,
                "total_replies": replies,
            }
    except Exception as e:
        print(f"Error getting user stats: {e}")
        return {"total_confessions": 0, "today_confessions": 0, "total_replies": 0}


def is_image_url(url: str) -> bool:
    """Check if URL points to an image (strict check for common image formats)."""
    if not url:
        return False

    parsed = urlparse(url)
    path = parsed.path.lower()

    image_extensions = [
        ".png", ".jpg", ".jpeg", ".gif", ".webp",
        ".bmp", ".tiff", ".svg", ".jfif", ".pjpeg", ".pjp",
    ]

    for ext in image_extensions:
        if path.endswith(ext):
            return True

    if "cdn.discordapp.com" in parsed.netloc and "/attachments/" in path:
        last_segment = path.split("/")[-1]
        image_patterns = ["png", "jpg", "jpeg", "gif", "webp",
                          "PNG", "JPG", "JPEG", "GIF", "WEBP"]
        for pattern in image_patterns:
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


async def backup_database():
    """Create a backup of the database."""
    try:
        if os.path.exists(DB_PATH):
            import shutil
            backup_dir = Path(DB_BACKUP_PATH).parent
            backup_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(DB_PATH, DB_BACKUP_PATH)
    except Exception as e:
        print(f"Backup failed: {e}")


async def init_db():
    """Initialize database with proper error handling."""
    try:
        await backup_database()

        data_dir = Path("data")
        data_dir.mkdir(parents=True, exist_ok=True)

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            await db.execute("PRAGMA journal_mode = WAL")

            # Confessions table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS confessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    title TEXT,
                    description TEXT NOT NULL,
                    image_url TEXT,
                    message_id INTEGER UNIQUE,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Replies table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS replies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    confession_id INTEGER NOT NULL,
                    parent_reply_id INTEGER,
                    user_id INTEGER NOT NULL,
                    description TEXT NOT NULL,
                    image_url TEXT,
                    message_id INTEGER,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (confession_id) REFERENCES confessions (id) ON DELETE CASCADE
                )
            """)

            # Reports table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_type TEXT NOT NULL,
                    target_id INTEGER NOT NULL,
                    reporter_id INTEGER NOT NULL,
                    reason TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Bans table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS confessban (
                    user_id INTEGER PRIMARY KEY,
                    banned_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    reason TEXT
                )
            """)

            # ── Migrations for existing databases ──────────────────────────
            cursor = await db.execute("PRAGMA table_info(replies)")
            reply_cols = {row[1] for row in await cursor.fetchall()}

            if "message_id" not in reply_cols:
                await db.execute("ALTER TABLE replies ADD COLUMN message_id INTEGER")
                print("✅ Migration: added 'message_id' to replies table")

            if "parent_reply_id" not in reply_cols:
                await db.execute("ALTER TABLE replies ADD COLUMN parent_reply_id INTEGER")
                print("✅ Migration: added 'parent_reply_id' to replies table")

            cursor = await db.execute("PRAGMA table_info(confessban)")
            ban_cols = {row[1] for row in await cursor.fetchall()}
            if "reason" not in ban_cols:
                await db.execute("ALTER TABLE confessban ADD COLUMN reason TEXT")
                print("✅ Migration: added 'reason' to confessban table")

            # Indexes
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_confessions_user_id ON confessions(user_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_confessions_message_id ON confessions(message_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_replies_confession_id ON replies(confession_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_reports_target ON reports(target_type, target_id)"
            )

            await db.commit()
            print(f"Database initialized at {DB_PATH}")
    except Exception as e:
        print(f"Database initialization failed: {e}")
        raise


async def process_media(
    bot, content: str, attachments: List[discord.Attachment], media_channel_id: int
) -> Optional[str]:
    """Process and upload media to the media channel."""
    try:
        media_channel = bot.get_channel(media_channel_id)
        if not media_channel:
            print(f"Media channel {media_channel_id} not found")
            return None

        image_url = None

        if attachments:
            for attachment in attachments:
                if attachment.content_type and attachment.content_type.startswith("image/"):
                    try:
                        file_bytes = await attachment.read()
                        file = discord.File(io.BytesIO(file_bytes), filename=attachment.filename)
                        msg = await media_channel.send(file=file)
                        image_url = msg.attachments[0].url
                        print(f"Uploaded attachment: {attachment.filename} -> {image_url}")
                        break
                    except Exception as e:
                        print(f"Error uploading attachment {attachment.filename}: {e}")
                        continue

        if not image_url and content:
            url = extract_url(content)
            if url and is_image_url(url):
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(url) as response:
                            if response.status == 200:
                                content_type = response.headers.get("Content-Type", "")
                                if not content_type.startswith("image/"):
                                    return None
                                image_data = await response.read()
                                parsed_url = urlparse(url)
                                filename = os.path.basename(parsed_url.path)
                                if not filename or "." not in filename:
                                    ext_map = {
                                        "image/jpeg": ".jpg", "image/jpg": ".jpg",
                                        "image/png": ".png", "image/gif": ".gif",
                                        "image/webp": ".webp", "image/bmp": ".bmp",
                                    }
                                    ext = ext_map.get(content_type, ".jpg")
                                    filename = f"downloaded_image{ext}"
                                file = discord.File(io.BytesIO(image_data), filename=filename)
                                msg = await media_channel.send(file=file)
                                image_url = msg.attachments[0].url
                except Exception as e:
                    print(f"Error downloading/uploading image from URL: {e}")
                    import traceback
                    traceback.print_exc()

        return image_url

    except Exception as e:
        print(f"Error in process_media: {e}")
        import traceback
        traceback.print_exc()
        return None


async def get_confession_by_id(confession_id: int):
    """Safely get confession by ID."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "SELECT * FROM confessions WHERE id = ?", (confession_id,)
            )
            return await cursor.fetchone()
    except Exception as e:
        print(f"Error getting confession {confession_id}: {e}")
        return None


async def is_user_banned(user_id: int) -> bool:
    """Check if user is banned."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "SELECT 1 FROM confessban WHERE user_id = ?", (user_id,)
            )
            return await cursor.fetchone() is not None
    except Exception as e:
        print(f"Error checking ban status for user {user_id}: {e}")
        return False


async def get_user_confessions(user_id: int):
    """Get all confessions by a user."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "SELECT * FROM confessions WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,),
            )
            return await cursor.fetchall()
    except Exception as e:
        print(f"Error getting user confessions: {e}")
        return []


async def get_confession_replies(confession_id: int):
    """Get all replies for a confession."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "SELECT * FROM replies WHERE confession_id = ? ORDER BY timestamp ASC",
                (confession_id,),
            )
            return await cursor.fetchall()
    except Exception as e:
        print(f"Error getting confession replies: {e}")
        return []


async def ban_user(user_id: int, reason: str = None):
    """Ban a user from submitting confessions."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT OR REPLACE INTO confessban (user_id, reason) VALUES (?, ?)",
                (user_id, reason),
            )
            await db.commit()
            return True
    except Exception as e:
        print(f"Error banning user {user_id}: {e}")
        return False


async def unban_user(user_id: int):
    """Unban a user from submitting confessions."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM confessban WHERE user_id = ?", (user_id,))
            await db.commit()
            return True
    except Exception as e:
        print(f"Error unbanning user {user_id}: {e}")
        return False


# Add after other limit functions (around line 134)

async def get_hourly_report_count(user_id: int) -> int:
    """Get the number of reports a user has made in the last hour."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            one_hour_ago = discord.utils.utcnow() - datetime.timedelta(hours=1)
            cursor = await db.execute(
                "SELECT COUNT(*) FROM reports WHERE reporter_id = ? AND timestamp >= ?",
                (user_id, one_hour_ago.isoformat()),
            )
            return (await cursor.fetchone())[0]
    except Exception as e:
        print(f"Error getting hourly report count: {e}")
        return 0


async def can_user_report(user_id: int, hourly_limit: int = 5) -> tuple[bool, str, int]:
    try:
        current_count = await get_hourly_report_count(user_id)
        
        # DEBUG - remove after testing
        print(f"[REPORT LIMIT] User {user_id}: {current_count}/{hourly_limit} reports in last hour")
        
        if current_count >= hourly_limit:
            try:
                async with aiosqlite.connect(DB_PATH) as db:
                    # Make sure we're using proper UTC time
                    one_hour_ago = discord.utils.utcnow() - datetime.timedelta(hours=1)
                    cursor = await db.execute(
                        "SELECT timestamp FROM reports WHERE reporter_id = ? AND timestamp >= ? "
                        "ORDER BY timestamp ASC LIMIT 1",
                        (user_id, one_hour_ago.isoformat()),
                    )
                    result = await cursor.fetchone()
                    if result:
                        # Ensure proper datetime parsing
                        oldest_time = datetime.datetime.fromisoformat(result[0].replace('Z', '+00:00'))
                        reset_in = 3600 - (discord.utils.utcnow() - oldest_time).total_seconds()
                        if reset_in > 0:
                            minutes = int(reset_in // 60)
                            seconds = int(reset_in % 60)
                            return (
                                False,
                                f"❌ You've reached the hourly report limit of {hourly_limit}. "
                                f"You can report again in {minutes}m {seconds}s.",
                                current_count,
                            )
            except Exception as e:
                print(f"Error calculating report reset time: {e}")
            
            return (
                False,
                f"❌ You've reached the hourly report limit of {hourly_limit}. "
                "Please wait an hour before reporting again.",
                current_count,
            )
        
        return True, "", current_count
        
    except Exception as e:
        print(f"Error checking report limit: {e}")
        return True, "", 0