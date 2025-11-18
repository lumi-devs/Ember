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

def extract_url(text: str) -> Optional[str]:
    match = URL_REGEX.search(text)
    return match.group(0) if match else None

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
    """Process media with better error handling and validation"""
    media_url = None
    media_channel = bot.get_channel(media_channel_id)
    
    if not media_channel:
        print(f"Media channel {media_channel_id} not found")
        return None
        
    try:
        # Validate file size and type for attachments
        if attachments:
            attachment = attachments[0]
            if attachment.size > 10 * 1024 * 1024:  # 10MB limit
                print(f"Attachment too large: {attachment.size}")
                return None
                
            file = await attachment.to_file()
            sent_msg = await media_channel.send(file=file)
            media_url = sent_msg.attachments[0].url
            print(f"Media uploaded from attachment: {media_url}")
            
        else:
            # Check for URLs
            url = extract_url(content)
            if url:
                print(f"Processing URL: {url}")
                
                # Validate URL
                parsed = urlparse(url)
                if not parsed.scheme in ('http', 'https'):
                    print(f"Invalid URL scheme: {url}")
                    return None
                
                # Handle Tenor GIFs
                if 'tenor.com' in url:
                    gif_id_match = re.search(r'/view/([^/?]+)', url) or re.search(r'tenor\.com/view/([^/?]+)', url)
                    if gif_id_match:
                        gif_id = gif_id_match.group(1)
                        direct_url = f"https://media.tenor.com/{gif_id}.gif"
                        print(f"Converted Tenor URL to: {direct_url}")
                    else:
                        direct_url = url
                else:
                    direct_url = url
                
                # Download and validate media
                async with aiohttp.ClientSession() as session:
                    async with session.get(direct_url) as resp:
                        if resp.status == 200:
                            # Check content size
                            content_length = resp.headers.get('content-length')
                            if content_length and int(content_length) > 10 * 1024 * 1024:
                                print(f"URL content too large: {content_length}")
                                return None
                                
                            content_type = resp.headers.get('content-type', '').lower()
                            data = io.BytesIO(await resp.read())
                            
                            # Validate file type
                            allowed_types = ['image/', 'video/', 'gif']
                            if not any(t in content_type for t in allowed_types):
                                print(f"Unsupported content type: {content_type}")
                                return None
                            
                            # Determine filename
                            if 'gif' in content_type:
                                filename = "media.gif"
                            elif 'png' in content_type:
                                filename = "media.png"
                            elif 'jpeg' in content_type or 'jpg' in content_type:
                                filename = "media.jpg"
                            elif 'webp' in content_type:
                                filename = "media.webp"
                            else:
                                filename = "media"
                            
                            sent_msg = await media_channel.send(file=discord.File(data, filename=filename))
                            media_url = sent_msg.attachments[0].url
                            print(f"Media uploaded from URL: {media_url}")
                        else:
                            print(f"Failed to fetch URL: {resp.status}")
                            
    except discord.HTTPException as e:
        print(f"Discord API error processing media: {e}")
    except aiohttp.ClientError as e:
        print(f"Network error processing media: {e}")
    except Exception as e:
        print(f"Unexpected error processing media: {e}")
        
    return media_url

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