# cogs/auto_delete.py
import discord
from discord.ext import commands
import asyncio
import re

DELETE_DELAY = 600  # 10 minutes (was accidentally 10 seconds)

# Matches most "caught cat" message variants.
# Note: "cought" is kept intentionally to match the bot's known typo output,
# but "caught" is also included to catch corrected spellings.
# <:\w+:\d+> is the correct Discord custom emoji format (was </rain:\d+> which never matched).
CAUGHT_CAT_PATTERN = re.compile(
    r"(c(?:aught|ought).*cat|cat.*c(?:aught|ought)|fella.*c(?:aught|ought)|☔.*cats|<:\w+:\d+>.*cat|cat.*<:\w+:\d+>)",
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