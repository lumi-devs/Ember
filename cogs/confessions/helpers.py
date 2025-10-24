import re
import aiosqlite
import discord
import aiohttp
import io
from typing import Optional

TENOR_REGEX = r"(https?:\/\/(www\.)?tenor\.com\/\S+)"
DB_PATH = "data/database.db"
CONFESSION_CHANNEL_ID = 1399658407178272798  # Consistent with main file
REPORT_CHANNEL_ID = 1393171149038293023
MEDIA_CHANNEL_ID = 1424139009931743382
URL_REGEX = re.compile(r'https?://\S+')

def extract_url(text: str) -> Optional[str]:
    match = URL_REGEX.search(text)
    return match.group(0) if match else None

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS confessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                title TEXT,
                description TEXT,
                image_url TEXT,
                message_id INTEGER,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS replies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                confession_id INTEGER,
                user_id INTEGER,
                description TEXT,
                image_url TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_type TEXT,
                target_id INTEGER,
                reporter_id INTEGER,
                reason TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS confessban (
                user_id INTEGER PRIMARY KEY
            )
        """)
        await db.commit()


async def process_media(bot, content, attachments, media_channel_id):
    media_url = None
    media_channel = bot.get_channel(media_channel_id)
    
    if not media_channel:
        print(f"Media channel {media_channel_id} not found")
        return None
        
    try:
        if attachments:
            # Take first attachment
            file = await attachments[0].to_file()
            sent_msg = await media_channel.send(file=file)
            media_url = sent_msg.attachments[0].url
            print(f"Media uploaded from attachment: {media_url}")
        else:
            # Check for URLs
            url = extract_url(content)
            if url:
                print(f"Processing URL: {url}")
                
                # Handle Tenor GIFs specifically
                if 'tenor.com' in url:
                    # Extract GIF ID and use tenor.com/view/ format for direct GIF URL
                    gif_id_match = re.search(r'/view/([^/?]+)', url) or re.search(r'tenor\.com/view/([^/?]+)', url)
                    if gif_id_match:
                        gif_id = gif_id_match.group(1)
                        direct_url = f"https://media.tenor.com/{gif_id}.gif"
                        print(f"Converted Tenor URL to: {direct_url}")
                    else:
                        # Fallback to original URL
                        direct_url = url
                else:
                    direct_url = url
                
                # Re-upload URL content as a file to Discord
                async with aiohttp.ClientSession() as session:
                    async with session.get(direct_url) as resp:
                        if resp.status == 200:
                            # Check content type
                            content_type = resp.headers.get('content-type', '').lower()
                            data = io.BytesIO(await resp.read())
                            
                            # Determine file extension
                            if 'gif' in content_type:
                                filename = "tenor.gif"
                            elif 'png' in content_type:
                                filename = "image.png"
                            elif 'jpeg' in content_type or 'jpg' in content_type:
                                filename = "image.jpg"
                            elif 'webp' in content_type:
                                filename = "image.webp"
                            else:
                                # Try to get from URL or default to png
                                filename = direct_url.split("/")[-1].split("?")[0]
                                if not filename or '.' not in filename:
                                    filename = "media.png"
                            
                            sent_msg = await media_channel.send(file=discord.File(data, filename=filename))
                            media_url = sent_msg.attachments[0].url
                            print(f"Media uploaded from URL: {media_url}")
                        else:
                            print(f"Failed to fetch URL: {resp.status}")
    except Exception as e:
        print(f"Error processing media: {e}")
        return None
        
    return media_url