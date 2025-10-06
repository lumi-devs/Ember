import discord
from discord.ext import commands
import asyncio

CONFESSION_CHANNEL_ID = 1424080881265541241

class ConfessionCleaner(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Temporarily allowlisted user IDs for media submission
        self.allowed_users = set()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if message.channel.id != CONFESSION_CHANNEL_ID:
            return

        # ✅ Allow messages in threads
        if isinstance(message.channel, discord.Thread):
            return

        # ✅ Allow messages from temporarily allowed users
        if message.author.id in self.allowed_users:
            return

        # ❌ Otherwise delete
        try:
            await message.delete()
        except discord.Forbidden:
            pass
        except discord.HTTPException:
            pass

    async def temporarily_allow_user(self, user_id: int, duration: int = 20):
        """Temporarily allow a user to send messages in the confession channel."""
        self.allowed_users.add(user_id)

        # Give user explicit send_messages permission in channel
        channel = self.bot.get_channel(CONFESSION_CHANNEL_ID)
        member = channel.guild.get_member(user_id)
        if member:
            #await channel.set_permissions(member, send_messages=True)

        # Wait for duration
        await asyncio.sleep(duration)

        # Remove user from allowed list
        self.allowed_users.discard(user_id)

        # Remove explicit permission (locks channel again for user)


async def setup(bot):
    await asyncio.sleep(2)
    await bot.add_cog(ConfessionCleaner(bot))
