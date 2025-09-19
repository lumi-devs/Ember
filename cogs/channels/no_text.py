import discord
from discord.ext import commands

WATCH_CHANNEL_IDS = [
    1418501302626816010,
    
]

class DeleteAll(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.id==self.bot.user.id or message.channel.id not in WATCH_CHANNEL_IDS:
            return

        try:
            await message.delete()
        except Exception as e:
            print(f"[DeleteAll] Failed to delete message: {e}")

async def setup(bot: commands.Bot):
    await bot.add_cog(DeleteAll(bot))
