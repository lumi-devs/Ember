import os
import asyncio
import threading
from flask import Flask
import discord
from discord.ext import commands

#FLASK---------------------------------------------------------
app = Flask(__name__)
@app.route("/")
def home():
    return "Flask server is running 🚀"
def run_flask():
    app.run(host="0.0.0.0", port=5000)



# --- Discord bot setup ------------------------------------------
intents=discord.Intents.default()
intents.guilds = True
intents.guild_messages = True
intents.message_content = True
intents.guild_reactions = True
intents.members = True
bot = commands.Bot(command_prefix="A!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"🔗 Synced {len(synced)} slash commands")
    except Exception as e:
        print(f"❌ Error syncing: {e}")

async def load_cogs():
    for filename in os.listdir("./cogs"):
        if filename.endswith(".py") and filename != "__init__.py":
            await bot.load_extension(f"cogs.{filename[:-3]}")

async def main():
    # Start Flask in another thread
    threading.Thread(target=run_flask, daemon=True).start()

    async with bot:
        await load_cogs()
        await bot.start(os.environ.get("BOT_TOKEN"))

asyncio.run(main())
