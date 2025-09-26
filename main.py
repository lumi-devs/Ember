from dotenv import load_dotenv
load_dotenv()
import os
from pathlib import Path
import asyncio
import logging
from quart import Quart, Response
import discord
from discord.ext import commands

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

# ------------------ QUART (async Flask) ------------------
app = Quart(__name__)

@app.route("/")
async def home():
    return """
    <html>
    <head>
        <title>Bot Logs</title>
        <style>
            body { background: #111; color: #0f0; font-family: monospace; }
            pre { white-space: pre-wrap; }
        </style>
    </head>
    <body>
        <h2>📜 Live Bot Logs</h2>
        <pre id="logs"></pre>
        <script>
            const evtSource = new EventSource("/logs");
            evtSource.onmessage = function(e) {
                const logs = document.getElementById("logs");
                logs.textContent += e.data + "\\n";
                window.scrollTo(0, document.body.scrollHeight);
            };
        </script>
    </body>
    </html>
    """

@app.route("/logs")
async def stream_logs():
    async def generate():
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            f.seek(0, os.SEEK_END)
            while True:
                line = f.readline()
                if line:
                    yield f"data: {line.strip()}\n\n"
                else:
                    await asyncio.sleep(0.5)
    return Response(generate(), mimetype="text/event-stream")

async def run_quart():
    logger.info("Starting Quart server on 0.0.0.0:5000")
    await app.run_task(host="0.0.0.0", port=5000)

# ------------------ DISCORD BOT ------------------
intents = discord.Intents.all()
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
    # Start Quart server in background task
    asyncio.create_task(run_quart())
    async with bot:
        await load_cogs()
        await bot.start(BOT_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
