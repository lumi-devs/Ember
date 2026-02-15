from .cog import RoleMentionTracker
from discord.ext import commands

async def setup(bot: commands.Bot):
    await bot.add_cog(RoleMentionTracker(bot))