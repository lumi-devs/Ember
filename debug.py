from dotenv import load_dotenv
load_dotenv()

# debug.py
import os
import discord
from discord.ext import commands
import asyncio
import aioconsole  # pip install aioconsole

TOKEN = os.getenv("BOT_TOKEN")

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    print("🔧 Async Debug REPL started (type Python code below)\n")

    async def repl():
        while True:
            try:
                # Read line asynchronously
                line = await aioconsole.ainput(">>> ")
                if not line.strip():
                    continue
                if line.strip() in ("exit", "quit"):
                    print("👋 Exiting REPL…")
                    await bot.close()
                    break

                # Evaluate code inside globals + locals
                try:
                    result = eval(line, globals(), locals())
                    if asyncio.iscoroutine(result):
                        result = await result
                    print(result)
                except Exception as e:
                    try:
                        exec(line, globals(), locals())
                    except Exception as e2:
                        print(f"❌ {e2}")
            except (EOFError, KeyboardInterrupt):
                print("👋 Stopping bot…")
                await bot.close()
                break

    bot.loop.create_task(repl())


bot.run(TOKEN)
