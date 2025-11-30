from .main import ShameSystem
from .commands import ShameCommands

async def setup(bot):
    # Add both cogs
    await bot.add_cog(ShameSystem(bot))
    await bot.add_cog(ShameCommands(bot))
    print("[ShameSystem] All cogs loaded successfully! ✅")