import re
import aiosqlite
import discord
TENOR_REGEX = r"(https?:\/\/(www\.)?tenor\.com\/\S+)"
DB_PATH = "data/database.db"
CONFESSION_CHANNEL_ID = 1424080881265541241
REPORT_CHANNEL_ID = 1393171149038293023
MEDIA_CHANNEL_ID = 1424139009931743382
URL_REGEX = re.compile(r'https?://\S+')

def extract_url(text: str) -> str | None:
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
    if attachments:
        # Take first attachment
        file = await attachments[0].to_file()
        sent_msg = await media_channel.send(file=file)
        media_url = sent_msg.attachments[0].url
    else:
        # Check for URLs (Tenor or others)
        url = extract_url(content)
        if url:
            # Re-upload URL content as a file to Discord
            import aiohttp, io
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = io.BytesIO(await resp.read())
                        filename = url.split("/")[-1].split("?")[0]  # simple filename
                        sent_msg = await media_channel.send(file=discord.File(data, filename=filename))
                        media_url = sent_msg.attachments[0].url
    return media_url

