from dotenv import load_dotenv
load_dotenv()
import os
from pathlib import Path
import asyncio
import threading
from flask import Flask
import discord
from discord.ext import commands
from discord import Activity, ActivityType
from pathlib import Path

# ------------------ FLASK ------------------
app = Flask(__name__)

@app.route("/")
def home():
    return "Flask server is running 🚀"

def run_flask():
    app.run(host="0.0.0.0", port=5000)


# ------------------ DISCORD BOT ------------------
intents = discord.Intents.all()
intents.guilds = True
intents.guild_messages = True
intents.message_content = True
intents.guild_reactions = True
intents.members = True

bot = commands.Bot(command_prefix="-", intents=intents)

GUILD_ID = int(os.environ.get("GUILD_ID"))  # Add your test server ID
BOT_TOKEN = os.environ.get("BOT_TOKEN")

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")

    try:
        guild_obj = discord.Object(id=GUILD_ID)
        synced = await bot.tree.sync()
    #Fashion shit
        await bot.change_presence(status=discord.Status.dnd)
        voice_channel = bot.get_channel(int(os.environ.get("VOICE_CHANNEL_ID_ON_START")))
        print(voice_channel)
        voice_client = await voice_channel.connect()
        await voice_client.guild.change_voice_state(
                    channel=voice_channel, 
                    self_mute=True, 
                    self_deaf=True
        )

        print(f"🔗 Synced {len(synced)} slash commands to guild {GUILD_ID}")
        print("📜 Registered app commands:")
        for cmd in bot.tree.walk_commands():
            print(f"- {cmd.qualified_name}")
    except Exception as e:
        print(f"❌ Error syncing commands: {e}")


# ------------------ LOAD COGS ------------------
async def load_cogs():
    cogs_path = Path("cogs")  # root folder for cogs
    for py_file in cogs_path.rglob("*.py"):
        if py_file.name == "__init__.py":
            continue
        module = ".".join(py_file.with_suffix("").parts)
        try:
            await bot.load_extension(module)
        except Exception as e:
            print(f"Failed to load {module}: {e}")


# ------------------ MAIN ------------------
async def main():
    # Start Flask server in a separate thread
    threading.Thread(target=run_flask, daemon=True).start()

    async with bot:
        await load_cogs()
        await bot.start(BOT_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
