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


@bot.event
async def on_ready():
    logger.info(f"✅ Logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        logger.info(f"🔗 Synced {len(synced)} slash commands to guild {GUILD_ID}")
        logger.info("📜 Registered app commands:")
        for cmd in bot.tree.walk_commands():
            logger.info(f"- {cmd.qualified_name}")

    except Exception as e:
        logger.error(f"❌ Error syncing commands: {e}")


@bot.event
async def on_resumed():
    """Handle session resumption"""
    logger.info("🔄 Session resumed")


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

        # Add a small delay before starting
        await asyncio.sleep(1)

        try:
            await bot.start(BOT_TOKEN)
        except KeyboardInterrupt:
            logger.info("👋 Bot shutdown requested")
        except Exception as e:
            logger.error(f"💥 Fatal error: {e}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Bot shutdown requested")
    except Exception as e:
        logger.error(f"💥 Fatal error: {e}")
