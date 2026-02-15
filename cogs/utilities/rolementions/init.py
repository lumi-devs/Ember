from .cog import RoleMentionCog  # ← Updated import
from .automod_manager import AutoModManager
from discord.ext import commands

async def setup(bot: commands.Bot):
    await bot.add_cog(RoleMentionCog(bot))  