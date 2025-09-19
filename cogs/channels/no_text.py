import discord
from discord.ext import commands

WATCH_CHANNEL_IDS = [
    1418501302626816010,  # Add your channel IDs here
    123456789012345678,
    987654321098765432
]
async def setup(bot: commands.Bot):
    @bot.event
    async def on_message(message: discord.Message):
        if message.author.bot:
            return
        if message.channel.id not in WATCH_CHANNEL_IDS:
            return
        try:
            await message.delete()
        except Exception as e:
            print(f"[DeleteAll] Failed to delete message: {e}")
