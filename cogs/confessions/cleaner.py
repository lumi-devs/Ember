# cogs/confessions/cleaner.py
import discord
from discord.ext import commands
import asyncio
import time

CONFESSION_CHANNEL_ID = 1424080881265541241


class ConfessionCleaner(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.allowed_users = set()
        self.pending_deletions = {}  # Track pending deletions

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if message.channel.id != CONFESSION_CHANNEL_ID:
            return

        # Allow messages in threads
        if isinstance(message.channel, discord.Thread):
            return

        user_key = (message.author.id, message.channel.id)

        # Messages from allowed users
        if message.author.id in self.allowed_users:
            # Remove user immediately (only 1 message allowed)
            self.allowed_users.discard(message.author.id)

            # Delete after 8 seconds with better error handling
            async def delete_later():
                await asyncio.sleep(8)
                try:
                    await message.delete()
                except discord.NotFound:
                    pass  # Message already deleted
                except discord.HTTPException as e:
                    print(f"Failed to delete allowed user message: {e}")
                finally:
                    if user_key in self.pending_deletions:
                        del self.pending_deletions[user_key]

            self.pending_deletions[user_key] = asyncio.create_task(delete_later())

        else:
            # Delete immediately with retry logic
            async def delete_immediately():
                await asyncio.sleep(2)  # Small delay to let users see feedback
                try:
                    await message.delete()
                except discord.NotFound:
                    pass
                except discord.HTTPException as e:
                    print(f"Failed to delete unauthorized message: {e}")
                    # Try once more after short delay
                    await asyncio.sleep(1)
                    try:
                        await message.delete()
                    except:
                        pass

            asyncio.create_task(delete_immediately())

    async def allow_user_once(self, user_id: int):
        """Allow a user to send a single message with cleanup."""
        # Clean up expired allowances
        current_time = time.time()
        self.allowed_users.add(user_id)

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        """Clean up pending deletions if message is deleted manually"""
        if message.author.bot:
            return

        user_key = (message.author.id, message.channel.id)
        if user_key in self.pending_deletions:
            self.pending_deletions[user_key].cancel()
            del self.pending_deletions[user_key]


async def setup(bot):
    await bot.add_cog(ConfessionCleaner(bot))
