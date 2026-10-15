import os
from discord.ext import commands

CHANNEL_IDS = [
    int(x) for x in os.environ.get("WELCOME_CHANNEL_IDS", "").split(",") if x.strip()
]


class WelcomePing(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.channel_ids = CHANNEL_IDS

    @commands.Cog.listener()
    async def on_member_join(self, member):
        for channel_id in self.channel_ids:
            channel = self.bot.get_channel(channel_id)
            if channel:
                msg = await channel.send(f"{member.mention}")
                await msg.delete(delay=5)


# Setup function to load the cog
async def setup(bot):
    await bot.add_cog(WelcomePing(bot))
