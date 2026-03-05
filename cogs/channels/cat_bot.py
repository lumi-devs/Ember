# cogs/auto_delete.py
import discord
from discord.ext import commands
import asyncio
import re

DELETE_DELAY = 10  # 10 minutes

# Matches most "caught cat" message variants
CAUGHT_CAT_PATTERN = re.compile(
    r"(cought.*cat|cat.*cought|fella.*cought|☔.*cats|</rain:\d+>)",
    re.IGNORECASE | re.DOTALL,
)


class AutoDelete(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.author.bot:
            return

        # Check for "caught cat" messages of any pattern
        if CAUGHT_CAT_PATTERN.search(message.content):
            await asyncio.sleep(DELETE_DELAY)
            try:
                await message.delete()
                # print(f"🧹 Deleted a caught-cat message in #{message.channel}")
            except discord.NotFound:
                pass
            except discord.Forbidden:
                print(f"❌ Missing permissions to delete in {message.channel}")
            except Exception as e:
                print(f"❌ Failed to delete caught-cat message: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoDelete(bot))
