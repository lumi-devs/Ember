from .shamer import Shamer

async def setup(bot):
    await bot.add_cog(Shamer(bot))  