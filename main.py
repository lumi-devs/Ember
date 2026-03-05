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

# Track connection state
voice_connection_lock = asyncio.Lock()
is_connecting = False


async def cleanup_voice_connection(guild):
    """Force cleanup of voice connection"""
    try:
        if guild.voice_client:
            logger.info(f"🧹 Cleaning up voice connection for guild {guild.id}")
            await guild.voice_client.disconnect(force=True)
            await asyncio.sleep(1)  # Give Discord time to process
    except Exception as e:
        logger.error(f"❌ Error during voice cleanup: {e}")


async def ensure_voice_connection():
    """Ensure bot is connected to the voice channel with proper state management"""
    global is_connecting
    
    async with voice_connection_lock:
        if is_connecting:
            logger.info("⏳ Voice connection already in progress, skipping...")
            return
        
        is_connecting = True
        
        try:
            voice_channel_id = int(os.environ.get("VOICE_CHANNEL_ID_ON_START"))
            voice_channel = bot.get_channel(voice_channel_id)
            
            if not voice_channel:
                logger.error(f"❌ Voice channel with ID {voice_channel_id} not found")
                return
            
            guild = voice_channel.guild
            voice_client = guild.voice_client
            
            # Check current connection state
            if voice_client and voice_client.is_connected():
                if voice_client.channel.id == voice_channel_id:
                    logger.info(f"✅ Already connected to correct voice channel: {voice_channel.name}")
                    # Ensure proper state
                    await voice_client.guild.change_voice_state(
                        channel=voice_channel, self_mute=True, self_deaf=True
                    )
                    return
                else:
                    # Connected to wrong channel, move
                    logger.info(f"🔄 Moving from {voice_client.channel.name} to {voice_channel.name}")
                    try:
                        await voice_client.move_to(voice_channel)
                        await asyncio.sleep(1)
                        # Verify move was successful
                        if voice_client.channel.id == voice_channel_id:
                            logger.info(f"✅ Successfully moved to {voice_channel.name}")
                            return
                    except Exception as e:
                        logger.error(f"❌ Failed to move voice connection: {e}")
                        # Clean up and reconnect
                        await cleanup_voice_connection(guild)
            
            # If we get here, we need to establish a new connection
            # First, ensure any existing connection is properly cleaned up
            if voice_client:
                await cleanup_voice_connection(guild)
                await asyncio.sleep(2)  # Wait for cleanup
            
            # Attempt connection with retries
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    logger.info(f"🔌 Connecting to voice channel: {voice_channel.name} (Attempt {attempt + 1}/{max_retries})")
                    voice_client = await voice_channel.connect(timeout=30.0, reconnect=True)
                    
                    # Set mute/deaf state
                    await voice_client.guild.change_voice_state(
                        channel=voice_channel, self_mute=True, self_deaf=True
                    )
                    
                    logger.info(f"✅ Successfully connected to {voice_channel.name}")
                    return
                    
                except discord.errors.ConnectionClosed as e:
                    logger.error(f"❌ Voice connection closed (attempt {attempt + 1}): {e.code}")
                    if attempt < max_retries - 1:
                        wait_time = (attempt + 1) * 5
                        logger.info(f"⏳ Retrying in {wait_time} seconds...")
                        await asyncio.sleep(wait_time)
                    else:
                        logger.error("❌ Max retries reached for voice connection")
                        
                except asyncio.TimeoutError:
                    logger.error(f"❌ Voice connection timeout (attempt {attempt + 1})")
                    if attempt < max_retries - 1:
                        wait_time = (attempt + 1) * 5
                        logger.info(f"⏳ Retrying in {wait_time} seconds...")
                        await asyncio.sleep(wait_time)
                        
                except Exception as e:
                    logger.error(f"❌ Unexpected error during voice connection: {e}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(5)
                    else:
                        logger.error("❌ Max retries reached for voice connection")
                        
        except Exception as e:
            logger.error(f"❌ Error managing voice connection: {e}")
            
        finally:
            is_connecting = False


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
            # Check if this was intentional or not
            if not is_connecting:
                # Attempt to reconnect after a delay
                await asyncio.sleep(3)
                await ensure_voice_connection()
                
        elif before.channel != after.channel and after.channel:
            # Bot moved to different channel
            logger.info(f"🔄 Bot moved to channel: {after.channel.name}")
            # Ensure mute/deaf state is maintained
            try:
                await after.channel.guild.change_voice_state(
                    channel=after.channel, self_mute=True, self_deaf=True
                )
            except Exception as e:
                logger.error(f"❌ Failed to set voice state after move: {e}")
                
        elif before.channel and after.channel and before.channel == after.channel:
            # State update for same channel (mute/deaf changes)
            logger.debug(f"📊 Voice state updated: mute={after.self_mute}, deaf={after.self_deaf}")


@bot.event
async def on_resumed():
    """Handle session resumption"""
    logger.info("🔄 Session resumed")
    # Give Discord a moment to stabilize
    await asyncio.sleep(2)
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
        
        try:
            await bot.start(BOT_TOKEN)
        except KeyboardInterrupt:
            logger.info("👋 Bot shutdown requested")
        except Exception as e:
            logger.error(f"💥 Fatal error: {e}")
        finally:
            # Clean up voice connections on shutdown
            for guild in bot.guilds:
                if guild.voice_client:
                    await cleanup_voice_connection(guild)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Bot shutdown requested")
    except Exception as e:
        logger.error(f"💥 Fatal error: {e}")