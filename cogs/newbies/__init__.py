from .cog import DynamicMemberList

async def setup(bot):
    await bot.add_cog(DynamicMemberList(bot))