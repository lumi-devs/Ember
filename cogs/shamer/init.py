from .main import ShameSystem
from .commands import ShameCommands

async def setup(bot):
    await bot.add_cog(ShameSystem(bot))
    await bot.add_cog(ShameCommands(bot))