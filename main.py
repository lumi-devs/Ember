from dotenv import load_dotenv
load_dotenv()
import os
from pathlib import Path
import asyncio
import logging
import discord
from discord.ext import commands
from flask import Flask
from threading import Thread

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is alive!"

def run_flask():
    # Set use_reloader=False to prevent the thread from starting twice
    app.run(host="0.0.0.0", port=8080, use_reloader=False)


# ------------------ LOGGING ------------------
LOG_FILE = Path("logs/bot.log")
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("BotLogger")

# ------------------ DISCORD BOT ------------------
intents = discord.Intents.all()
intents.members = True 
bot = commands.Bot(command_prefix=".", intents=intents)

GUILD_ID = int(os.environ.get("GUILD_ID"))
BOT_TOKEN = os.environ.get("BOT_TOKEN")

@bot.event
async def on_ready():
    logger.info(f"✅ Logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        logger.info(f"🔗 Synced {len(synced)} slash commands to guild {GUILD_ID}")
        logger.info("📜 Registered app commands:")
        for cmd in bot.tree.walk_commands():
            logger.info(f"- {cmd.qualified_name}")

        voice_channel = bot.get_channel(int(os.environ.get("VOICE_CHANNEL_ID_ON_START")))
        logger.info(f"Connecting to voice channel: {voice_channel}")
        voice_client = await voice_channel.connect()
        await voice_client.guild.change_voice_state(
            channel=voice_channel, self_mute=True, self_deaf=True
        )
        logger.info(f"✅ Connected to voice channel {voice_channel.name}")
    except Exception as e:
        logger.error(f"❌ Error syncing commands or connecting voice: {e}")

# ------------------ LOAD COGS ------------------
async def load_cogs():
    cogs_path = Path("cogs")
    for py_file in cogs_path.rglob("*.py"):
        if py_file.name == "__init__.py":
            continue
        module = ".".join(py_file.with_suffix("").parts)
        try:
            await bot.load_extension(module)
            logger.info(f"✅ Loaded cog: {module}")
        except Exception as e:
            logger.error(f"❌ Failed to load {module}: {e}")

# ------------------ MAIN ------------------
async def main():
    async with bot:
        await load_cogs()
        await bot.start(BOT_TOKEN)

if __name__ == "__main__":
    t = Thread(target=run_flask)
    t.daemon = True # This ensures the thread dies when the main script stops
    t.start()
    asyncio.run(main())