from .main import TempVoice

async def setup(bot):
    await bot.add_cog(TempVoice(bot))