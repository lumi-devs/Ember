# cogs/auto_delete.py
import discord
from discord.ext import commands, tasks
import asyncio

BOT_ID = 966695034340663367 
DELETE_DELAY = 3600


class AutoDelete(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.id == BOT_ID:
            await asyncio.sleep(DELETE_DELAY)
            try:
                await message.delete()
            except discord.NotFound:
                pass
            except discord.Forbidden:
                print(f"❌ Missing permissions to delete message in {message.channel}")
            except Exception as e:
                print(f"❌ Failed to delete message: {e}")

async def setup(bot: commands.Bot):
    await bot.add_cog(AutoDelete(bot))
