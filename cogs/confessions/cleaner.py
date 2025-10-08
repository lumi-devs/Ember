# cogs/confessions/cleaner.py
import discord
from discord.ext import commands
import asyncio

CONFESSION_CHANNEL_ID = 1424080881265541241

class ConfessionCleaner(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Users allowed to send a single message
        self.allowed_users = set()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if message.channel.id != CONFESSION_CHANNEL_ID:
            return

        # Allow messages in threads
        if isinstance(message.channel, discord.Thread):
            return

        # Messages from allowed users
        if message.author.id in self.allowed_users:
            # Delete after 8 seconds
            async def delete_later():
                await asyncio.sleep(8)
                try:
                    await message.delete()
                except:
                    pass
            asyncio.create_task(delete_later())
            # Remove user immediately (only 1 message allowed)
            self.allowed_users.discard(message.author.id)
        else:
            # Delete immediately
            try:
                await asyncio.sleep(2)
                await message.delete()
            except:
                pass

    async def allow_user_once(self, user_id: int):
        """Allow a user to send a single message."""
        self.allowed_users.add(user_id)


async def setup(bot):
    await asyncio.sleep(2)
    await bot.add_cog(ConfessionCleaner(bot))
