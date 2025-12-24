import re
import aiosqlite
import discord
import aiohttp
import io
import os
from typing import Optional
from urllib.parse import urlparse

TENOR_REGEX = r"(https?:\/\/(www\.)?tenor\.com\/\S+)"
DB_PATH = "data/database.db"
DB_BACKUP_PATH = "data/database_backup.db"
CONFESSION_CHANNEL_ID = 1399658407178272798
REPORT_CHANNEL_ID = 1393171149038293023
MEDIA_CHANNEL_ID = 1424139009931743382
URL_REGEX = re.compile(r'https?://\S+')

def is_image_url(url):
    """Check if URL points to an image"""
    image_extensions = ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp']
    return any(url.lower().endswith(ext) for ext in image_extensions)

async def backup_database():
    """Create a backup of the database"""
    try:
        if os.path.exists(DB_PATH):
            import shutil
            shutil.copy2(DB_PATH, DB_BACKUP_PATH)
    except Exception as e:
        print(f"Backup failed: {e}")

async def init_db():
    """Initialize database with proper error handling"""
    try:
        await backup_database()
        
        async with aiosqlite.connect(DB_PATH) as db:
            # Enable foreign keys and WAL mode for better performance
            await db.execute("PRAGMA foreign_keys = ON")
            await db.execute("PRAGMA journal_mode = WAL")
            
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
            await db.execute("""
                CREATE TABLE IF NOT EXISTS confessban (
                    user_id INTEGER PRIMARY KEY,
                    banned_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    reason TEXT
                )
            """)
            
            # Create indexes for better performance
            await db.execute("CREATE INDEX IF NOT EXISTS idx_confessions_user_id ON confessions(user_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_confessions_message_id ON confessions(message_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_replies_confession_id ON replies(confession_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_reports_target ON reports(target_type, target_id)")
            
            await db.commit()
    except Exception as e:
        print(f"Database initialization failed: {e}")
        raise

    async def process_media(bot, content, attachments, media_channel_id):
        """Process and upload media to the media channel"""
        try:
            media_channel = bot.get_channel(media_channel_id)
            if not media_channel:
                print(f"Media channel {media_channel_id} not found")
                return None
            
            image_url = None
            
            # Check attachments first (highest priority)
            if attachments:
                for attachment in attachments:
                    if attachment.content_type and attachment.content_type.startswith('image/'):
                        # Download and re-upload to media channel
                        file_bytes = await attachment.read()
                        file = discord.File(io.BytesIO(file_bytes), filename=attachment.filename)
                        
                        msg = await media_channel.send(file=file)
                        image_url = msg.attachments[0].url
                        break
            
            # If no attachments, check for URL in content
            if not image_url and content:
                url = extract_url(content)
                if url and is_image_url(url):
                    # Send URL to media channel
                    msg = await media_channel.send(url)
                    image_url = url
            
            return image_url
            
        except Exception as e:
            print(f"Error in process_media: {e}")
            return None

async def get_confession_by_id(confession_id: int):
    """Safely get confession by ID"""
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
    """Check if user is banned"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "SELECT 1 FROM confessban WHERE user_id = ?", (user_id,)
            )
            return await cursor.fetchone() is not None
    except Exception as e:
        print(f"Error checking ban status for user {user_id}: {e}")
        return False