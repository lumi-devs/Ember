# cogs/confessions/cleaner.py  —  Components V2
#
# any stray non-bot messages to keep the channel a clean feed of layout cards.

import asyncio
import os

import discord
from discord.ext import commands

CONFESSION_CHANNEL_ID = int(os.getenv("CONFESSION_CHANNEL_ID"))


class ConfessionCleaner(commands.Cog):
    def __init__(self, bot: discord.Client) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if message.channel.id != CONFESSION_CHANNEL_ID:
            return
        # Never touch messages inside threads — reply cards live there
        if isinstance(message.channel, discord.Thread):
            return

        async def _sweep() -> None:
            await asyncio.sleep(2)
            try:
                await message.delete()
            except discord.NotFound:
                pass
            except discord.HTTPException as e:
                print(f"[ConfessionCleaner] Delete failed: {e}")
                await asyncio.sleep(1)
                try:
                    await message.delete()
                except Exception:
                    pass

        asyncio.create_task(_sweep())


async def setup(bot: discord.Client) -> None:
    await bot.add_cog(ConfessionCleaner(bot))

