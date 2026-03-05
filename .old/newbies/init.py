from .cog import DynamicMemberList


async def setup(bot):
    cog = DynamicMemberList(bot)
    await bot.add_cog(cog)
    setup(bot)  # Also load the commands
    return bot
