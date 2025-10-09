import discord
import asyncio
from discord.ext import commands

WATCH_CHANNEL_IDS = [
    1418501302626816010,  # dragrequest
    1399658407178272798 
]

class DeleteAll(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Ignore bots
        if message.author.bot:
            return
        
        # Ignore threads
        if isinstance(message.channel, discord.Thread):
            return

        # Ignore messages outside watched channels
        if message.channel.id not in WATCH_CHANNEL_IDS:
            return

        try:
            await asyncio.sleep(2)
            await message.delete()
        except Exception as e:
            print(f"[DeleteAll] Failed to delete message: {e}")

async def setup(bot: commands.Bot):
    await bot.add_cog(DeleteAll(bot))

