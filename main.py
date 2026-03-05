import os
from pathlib import Path
import asyncio
import logging

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

# ------------------ LOGGING ------------------
LOG_FILE = Path("logs/bot.log")
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler()],
)
logger = logging.getLogger("BotLogger")

# ------------------ DISCORD BOT ------------------
intents = discord.Intents.all()
intents.members = True
bot = commands.Bot(command_prefix=".", intents=intents)

GUILD_ID = int(os.environ.get("GUILD_ID"))
BOT_TOKEN = os.environ.get("BOT_TOKEN")


async def ensure_voice_connection():
    """Ensure bot is connected to the voice channel, reconnect if disconnected, resume if already connected"""
    try:
        voice_channel_id = int(os.environ.get("VOICE_CHANNEL_ID_ON_START"))
        voice_channel = bot.get_channel(voice_channel_id)
        
        if not voice_channel:
            logger.error(f"❌ Voice channel with ID {voice_channel_id} not found")
            return
        
        guild = voice_channel.guild
        voice_client = guild.voice_client
        
        # Check if already connected
        if voice_client and voice_client.is_connected():
            if voice_client.channel.id == voice_channel_id:
                logger.info(f"✅ Already connected to voice channel: {voice_channel.name}")
                # Just ensure proper state
                await voice_client.guild.change_voice_state(
                    channel=voice_channel, self_mute=True, self_deaf=True
                )
                return
            else:
                # Connected to wrong channel, move to correct one
                logger.info(f"🔄 Moving from {voice_client.channel.name} to {voice_channel.name}")
                await voice_client.move_to(voice_channel)
        else:
            # Not connected, establish new connection
            logger.info(f"🔌 Connecting to voice channel: {voice_channel.name}")
            voice_client = await voice_channel.connect()
        
        # Set mute/deaf state
        await voice_client.guild.change_voice_state(
            channel=voice_channel, self_mute=True, self_deaf=True
        )
        logger.info(f"✅ Voice connection established in {voice_channel.name}")
        
    except Exception as e:
        logger.error(f"❌ Error managing voice connection: {e}")


@bot.event
async def on_ready():
    logger.info(f"✅ Logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        logger.info(f"🔗 Synced {len(synced)} slash commands to guild {GUILD_ID}")
        logger.info("📜 Registered app commands:")
        for cmd in bot.tree.walk_commands():
            logger.info(f"- {cmd.qualified_name}")

        # Handle voice connection
        await ensure_voice_connection()
        
    except Exception as e:
        logger.error(f"❌ Error in on_ready: {e}")


@bot.event
async def on_voice_state_update(member, before, after):
    """Monitor voice state changes to handle disconnections"""
    if member == bot.user:
        if before.channel and not after.channel:
            # Bot was disconnected
            logger.warning("⚠️ Bot was disconnected from voice channel")
            # Attempt to reconnect after a short delay
            await asyncio.sleep(2)
            await ensure_voice_connection()
        elif before.channel != after.channel and after.channel:
            # Bot moved to different channel
            logger.info(f"🔄 Bot moved to channel: {after.channel.name}")
            # Ensure mute/deaf state is maintained
            await after.channel.guild.change_voice_state(
                channel=after.channel, self_mute=True, self_deaf=True
            )


@bot.event
async def on_resumed():
    """Handle session resumption"""
    logger.info("🔄 Session resumed")
    # Verify voice connection is still valid after resume
    await ensure_voice_connection()


# ------------------ LOAD COGS ------------------
async def load_cogs():
    cogs_path = Path("cogs")
    if not cogs_path.exists():
        logger.warning(f"⚠️ Cogs directory not found: {cogs_path}")
        return
        
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
        
        # Add a small delay before starting to ensure everything is ready
        await asyncio.sleep(1)
        
        await bot.start(BOT_TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Bot shutdown requested")
    except Exception as e:
        logger.error(f"💥 Fatal error: {e}")