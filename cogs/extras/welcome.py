import discord
from discord.ext import commands, tasks


CHANNEL_IDS=[1388612947344883732, 1400546651046809650, 1393171114158719047]


class WelcomePing(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        
        self.channel_ids = CHANNEL_IDS

    @commands.Cog.listener()
    async def on_member_join(self, member):
        for channel_id in self.channel_ids:
            channel = self.bot.get_channel(channel_id)
            if channel:
                # Send ping message
                msg = await channel.send(f"{member.mention}")
                # Delete after 5 seconds
                await msg.delete(delay=2)

# Setup function to load the cog
async def setup(bot):
    await bot.add_cog(WelcomePing(bot))
