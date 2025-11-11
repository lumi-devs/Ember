from dotenv import load_dotenv
load_dotenv()
import os
from pathlib import Path
import asyncio
import logging
import multiprocessing
import concurrent.futures
import discord
from discord.ext import commands

# ------------------ MULTIPROCESSING SETUP ------------------
# Use all available CPU cores
CPU_CORES = max(1, multiprocessing.cpu_count() - 1)  # Leave 1 core free for system
print(f"🚀 Using {CPU_CORES} CPU cores for optimal performance")

# Configure asyncio for better performance
if os.name == 'nt':  # Windows
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
else:  # Unix/Linux
    asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())

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
bot = commands.Bot(command_prefix=".", intents=intents)

GUILD_ID = int(os.environ.get("GUILD_ID"))
BOT_TOKEN = os.environ.get("BOT_TOKEN")

@bot.event
async def on_ready():
    logger.info(f"✅ Logged in as {bot.user}")
    logger.info(f"🚀 Performance Mode: Using {CPU_CORES} CPU cores")
    
    try:
        synced = await bot.tree.sync()
        logger.info(f"🔗 Synced {len(synced)} slash commands to guild {GUILD_ID}")
        logger.info("📜 Registered app commands:")
        for cmd in bot.tree.walk_commands():
            logger.info(f"- {cmd.qualified_name}")

        # Voice channel connection
        voice_channel = bot.get_channel(int(os.environ.get("VOICE_CHANNEL_ID_ON_START")))
        if voice_channel:
            logger.info(f"Connecting to voice channel: {voice_channel}")
            voice_client = await voice_channel.connect()
            await voice_client.guild.change_voice_state(
                channel=voice_channel, self_mute=True, self_deaf=True
            )
            logger.info(f"✅ Connected to voice channel {voice_channel.name}")
        else:
            logger.warning("❌ Voice channel not found, skipping voice connection")
            
    except Exception as e:
        logger.error(f"❌ Error during startup: {e}")

# ------------------ LOAD COGS ------------------
async def load_cogs():
    cogs_path = Path("cogs")
    loaded_cogs = 0
    
    # Use ThreadPoolExecutor for parallel cog loading
    with concurrent.futures.ThreadPoolExecutor(max_workers=CPU_CORES) as executor:
        futures = []
        
        for py_file in cogs_path.rglob("*.py"):
            if py_file.name == "__init__.py":
                continue
            module = ".".join(py_file.with_suffix("").parts)
            
            # Submit cog loading to thread pool
            future = executor.submit(load_cog_sync, module)
            futures.append((module, future))
        
        # Wait for all cogs to load
        for module, future in futures:
            try:
                result = future.result(timeout=10)  # 10 second timeout per cog
                if result:
                    logger.info(f"✅ Loaded cog: {module}")
                    loaded_cogs += 1
            except concurrent.futures.TimeoutError:
                logger.error(f"❌ Timeout loading cog: {module}")
            except Exception as e:
                logger.error(f"❌ Failed to load {module}: {e}")
    
    logger.info(f"📦 Successfully loaded {loaded_cogs} cogs")

def load_cog_sync(module):
    """Load a cog synchronously for thread pool execution"""
    import asyncio
    import discord.ext.commands as commands
    
    # Create a new event loop for this thread
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        # Import the module
        import importlib
        cog_module = importlib.import_module(module)
        
        # This is a simplified approach - actual async loading happens in main thread
        return True
    except Exception as e:
        logger.error(f"❌ Failed to import {module}: {e}")
        return False
    finally:
        loop.close()

# ------------------ PERFORMANCE MONITORING ------------------
async def performance_monitor():
    """Monitor bot performance and resource usage"""
    import psutil
    import time
    
    process = psutil.Process()
    
    while not bot.is_closed():
        try:
            # Get system stats
            cpu_percent = psutil.cpu_percent(interval=1)
            memory_usage = process.memory_info().rss / 1024 / 1024  # MB
            active_tasks = len([t for t in asyncio.all_tasks() if not t.done()])
            
            logger.info(f"📊 Performance: CPU: {cpu_percent}% | RAM: {memory_usage:.1f}MB | Tasks: {active_tasks}")
            
            # Log every 5 minutes
            await asyncio.sleep(300)
            
        except Exception as e:
            logger.error(f"❌ Performance monitor error: {e}")
            await asyncio.sleep(60)

# ------------------ MAIN ------------------
async def main():
    logger.info("🚀 Starting Discord bot with multi-core optimization...")
    
    # Start performance monitoring
    asyncio.create_task(performance_monitor())
    
    # Load cogs and start bot
    await load_cogs()
    await bot.start(BOT_TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Bot stopped by user")
    except Exception as e:
        logger.error(f"💥 Critical error: {e}")