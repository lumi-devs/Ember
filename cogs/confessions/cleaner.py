# cogs/confessions/cleaner.py
import discord
from discord.ext import commands
import asyncio
import os

CONFESSION_CHANNEL_ID = int(os.getenv("CONFESSION_CHANNEL_ID"))


class ConfessionCleaner(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.allowed_users: set[int] = set()
        self.pending_deletions: dict = {}  # user_key → asyncio.Task

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if message.channel.id != CONFESSION_CHANNEL_ID:
            return

        # Never delete messages inside threads (replies live there)
        if isinstance(message.channel, discord.Thread):
            return

        user_key = (message.author.id, message.channel.id)

        if message.author.id in self.allowed_users:
            # Remove immediately so only 1 message is allowed through
            self.allowed_users.discard(message.author.id)

            # Delete after 8 seconds
            async def delete_later():
                await asyncio.sleep(8)
                try:
                    await message.delete()
                except discord.NotFound:
                    pass
                except discord.HTTPException as e:
                    print(f"Failed to delete allowed user message: {e}")
                finally:
                    self.pending_deletions.pop(user_key, None)

            self.pending_deletions[user_key] = asyncio.create_task(delete_later())

        else:
            # Unauthorized message — delete after a short grace period so the
            # user sees any ephemeral feedback before it disappears.
            async def delete_immediately():
                await asyncio.sleep(2)
                try:
                    await message.delete()
                except discord.NotFound:
                    pass
                except discord.HTTPException as e:
                    print(f"Failed to delete unauthorized message: {e}")
                    # One retry
                    await asyncio.sleep(1)
                    try:
                        await message.delete()
                    except Exception:
                        pass

            asyncio.create_task(delete_immediately())

    async def allow_user_once(self, user_id: int):
        """Allow a user to send a single message to the confession channel."""
        self.allowed_users.add(user_id)

    async def delete_ephemeral_after_delay(self, interaction: discord.Interaction, content: str, delay: int):
        """
        Helper method to send an ephemeral message and delete it after a delay.
        Similar to test.py pattern.
        """
        await interaction.response.send_message(content, ephemeral=True)
        message = await interaction.original_response()
        await asyncio.sleep(delay)
        try:
            await message.delete()
        except discord.NotFound:
            pass
        except discord.HTTPException as e:
            print(f"Failed to delete ephemeral message: {e}")

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        """Cancel pending auto-deletion if a message was already deleted manually."""
        if message.author.bot:
            return
        user_key = (message.author.id, message.channel.id)
        task = self.pending_deletions.pop(user_key, None)
        if task:
            task.cancel()


async def setup(bot):
    await bot.add_cog(ConfessionCleaner(bot))