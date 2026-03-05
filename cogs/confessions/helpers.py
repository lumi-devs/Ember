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
DB_PATH = "data/database.db"
DB_BACKUP_PATH = "data/database_backup.db"
CONFESSION_CHANNEL_ID = 1399658407178272798
REPORT_CHANNEL_ID = 1393171149038293023
MEDIA_CHANNEL_ID = 1424139009931743382
URL_REGEX = re.compile(r"https?://\S+")


def extract_url(text: str) -> Optional[str]:
    """Extract URL from text if present."""
    match = URL_REGEX.search(text)
    return match.group(0) if match else None


def is_image_url(url: str) -> bool:
    """Check if URL points to an image (strict check for common image formats)."""
    if not url:
        return False

    # Parse URL to get path
    parsed = urlparse(url)
    path = parsed.path.lower()

    # Only accept common image extensions
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

    # Check if path ends with image extension
    for ext in image_extensions:
        if path.endswith(ext):
            return True

    # Check for Discord CDN URLs (cdn.discordapp.com/attachments/...)
    if "cdn.discordapp.com" in parsed.netloc and "/attachments/" in path:
        # Discord attachment URLs - check if it looks like an image
        last_segment = path.split("/")[-1]

        # Check common Discord image patterns
        image_patterns = [
            "png",
            "jpg",
            "jpeg",
            "gif",
            "webp",
            "PNG",
            "JPG",
            "JPEG",
            "GIF",
            "WEBP",
        ]

        for pattern in image_patterns:
            if pattern in last_segment:
                return True

    # Also check query parameters for image indicators
    query = parsed.query.lower()
    if "format=" in query or "type=" in query:
        query_parts = query.split("&")
        for part in query_parts:
            if "format=" in part or "type=" in part:
                value = part.split("=")[1] if "=" in part else ""
                if any(
                    img_type in value
                    for img_type in ["jpg", "jpeg", "png", "gif", "webp"]
                ):
                    return True

    return False


async def backup_database():
    """Create a backup of the database."""
    try:
        if os.path.exists(DB_PATH):
            import shutil

            # Ensure backup directory exists
            backup_dir = Path(DB_BACKUP_PATH).parent
            backup_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(DB_PATH, DB_BACKUP_PATH)
    except Exception as e:
        print(f"Backup failed: {e}")


async def init_db():
    """Initialize database with proper error handling."""
    try:
        await backup_database()

        # Ensure data directory exists
        data_dir = Path("data")
        data_dir.mkdir(parents=True, exist_ok=True)

        async with aiosqlite.connect(DB_PATH) as db:
            # Enable foreign keys and WAL mode for better performance
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
                    user_id INTEGER NOT NULL,
                    description TEXT NOT NULL,
                    image_url TEXT,
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

            # Create indexes for better performance
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
    """Process and upload media to the media channel. Downloads URLs and re-uploads as files."""
    try:
        media_channel = bot.get_channel(media_channel_id)
        if not media_channel:
            print(f"Media channel {media_channel_id} not found")
            return None

        image_url = None

        # Check attachments first (highest priority) - Discord uploads
        if attachments:
            for attachment in attachments:
                if attachment.content_type and attachment.content_type.startswith(
                    "image/"
                ):
                    try:
                        # Download and re-upload to media channel
                        file_bytes = await attachment.read()
                        file = discord.File(
                            io.BytesIO(file_bytes), filename=attachment.filename
                        )

                        # Upload as file to media channel
                        msg = await media_channel.send(file=file)
                        image_url = msg.attachments[0].url
                        print(
                            f"Uploaded attachment: {attachment.filename} -> {image_url}"
                        )
                        break
                    except Exception as e:
                        print(f"Error uploading attachment {attachment.filename}: {e}")
                        continue

        # If no attachments, check for direct image URL in content
        if not image_url and content:
            url = extract_url(content)
            if url and is_image_url(url):
                print(f"Downloading image from URL: {url}")

                try:
                    # Download the image from URL
                    async with aiohttp.ClientSession() as session:
                        async with session.get(url) as response:
                            if response.status == 200:
                                # Get content type and file extension
                                content_type = response.headers.get("Content-Type", "")
                                if not content_type.startswith("image/"):
                                    print(
                                        f"URL doesn't point to an image: {content_type}"
                                    )
                                    return None

                                # Read the image data
                                image_data = await response.read()

                                # Determine filename
                                parsed_url = urlparse(url)
                                filename = os.path.basename(parsed_url.path)
                                if not filename or "." not in filename:
                                    # Generate a filename based on content type
                                    ext_map = {
                                        "image/jpeg": ".jpg",
                                        "image/jpg": ".jpg",
                                        "image/png": ".png",
                                        "image/gif": ".gif",
                                        "image/webp": ".webp",
                                        "image/bmp": ".bmp",
                                    }
                                    ext = ext_map.get(content_type, ".jpg")
                                    filename = f"downloaded_image{ext}"

                                # Create Discord file and upload
                                file = discord.File(
                                    io.BytesIO(image_data), filename=filename
                                )
                                msg = await media_channel.send(file=file)
                                image_url = msg.attachments[0].url
                                print(
                                    f"Downloaded and uploaded URL image: {url} -> {image_url}"
                                )
                            else:
                                print(
                                    f"Failed to download image: HTTP {response.status}"
                                )
                                return None

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
            row = await cursor.fetchone()
            return row
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


# Additional helper functions you might need
async def get_user_confessions(user_id: int):
    """Get all confessions by a user."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "SELECT * FROM confessions WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,),
            )
            rows = await cursor.fetchall()
            return rows
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
            rows = await cursor.fetchall()
            return rows
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
