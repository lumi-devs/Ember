# cogs/confessions/cleaner.py  —  Components V2
#
# Deletes any stray non-bot messages to keep the channel a clean feed of layout cards.

import asyncio
import logging

import discord
from discord.ext import commands

from core.cog import EmberCog

log = logging.getLogger("ember.confessions.cleaner")

# Use guild_config instead of environment variables
CONFESSION_CHANNEL_ID = 0


class ConfessionCleaner(EmberCog, name="ConfessionCleaner"):
    def __init__(self, bot: discord.Client) -> None:
        super().__init__(bot)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if message.channel.id != CONFESSION_CHANNEL_ID:
            return
        if isinstance(message.channel, discord.Thread):
            return

        async def _sweep() -> None:
            await asyncio.sleep(2)
            try:
                await message.delete()
            except discord.NotFound:
                pass
            except discord.HTTPException as e:
                log.warning("[ConfessionCleaner] Delete failed: %s", e)
                await asyncio.sleep(1)
                try:
                    await message.delete()
                except Exception:
                    pass

        asyncio.create_task(_sweep())


async def setup(bot: discord.Client) -> None:
    await bot.add_cog(ConfessionCleaner(bot))
