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
URL_REGEX = re.compile(r'https?://\S+')

def extract_url(text: str) -> Optional[str]:
    """Extract URL from text if present."""
    match = URL_REGEX.search(text)
    return match.group(0) if match else None

def is_image_url(url: str) -> bool:
    """Check if URL points to an image."""
    if not url:
        return False
    
    # Parse URL to get path
    parsed = urlparse(url)
    path = parsed.path.lower()
    
    # Check common image extensions
    image_extensions = ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp']
    return any(path.endswith(ext) for ext in image_extensions)

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
            await db.execute("CREATE INDEX IF NOT EXISTS idx_confessions_user_id ON confessions(user_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_confessions_message_id ON confessions(message_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_replies_confession_id ON replies(confession_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_reports_target ON reports(target_type, target_id)")
            
            await db.commit()
            print(f"Database initialized at {DB_PATH}")
    except Exception as e:
        print(f"Database initialization failed: {e}")
        raise

async def process_media(bot, content: str, attachments: List[discord.Attachment], media_channel_id: int) -> Optional[str]:
    """Process and upload media to the media channel."""
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
                    try:
                        # Download and re-upload to media channel
                        file_bytes = await attachment.read()
                        file = discord.File(io.BytesIO(file_bytes), filename=attachment.filename)
                        
                        msg = await media_channel.send(file=file)
                        image_url = msg.attachments[0].url
                        print(f"Uploaded attachment: {attachment.filename} -> {image_url}")
                        break
                    except Exception as e:
                        print(f"Error uploading attachment {attachment.filename}: {e}")
                        continue
        
        # If no attachments, check for URL in content
        if not image_url and content:
            url = extract_url(content)
            if url:
                print(f"Extracted URL from content: {url}")
                
                # Check if it's an image URL
                if is_image_url(url):
                    # Send URL to media channel
                    try:
                        msg = await media_channel.send(url)
                        # If Discord doesn't automatically embed, use the original URL
                        image_url = url
                        print(f"Posted image URL: {url}")
                    except Exception as e:
                        print(f"Error posting image URL: {e}")
                
                # Optional: Handle Tenor GIFs
                elif 'tenor.com' in url:
                    try:
                        async with aiohttp.ClientSession() as session:
                            async with session.get(url) as response:
                                if response.status == 200:
                                    html = await response.text()
                                    # Try to extract GIF URL from Tenor page
                                    gif_match = re.search(r'content="(https://[^"]+\.gif)"', html)
                                    if gif_match:
                                        gif_url = gif_match.group(1)
                                        msg = await media_channel.send(gif_url)
                                        image_url = gif_url
                                        print(f"Processed Tenor GIF: {gif_url}")
                    except Exception as e:
                        print(f"Error processing Tenor URL: {e}")
        
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
                (user_id,)
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
                (confession_id,)
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
                (user_id, reason)
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