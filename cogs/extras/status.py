import discord
from discord.ext import commands, tasks
import itertools

DURATION=120

class CustomStatusRotator(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.statuses = [
            "Morax x PunPun when?",
            "Rebiz is a femboy",
            "ChanChan is sus",
            "honey bunny honey bunny",
            "avii is a professional yapper",
            "Hannan the fiddler",
            "LEMME CATCH THE CATS!!",
            "Mother HONEY??",
            "Watching Morax's Feet Pics"
        ]
        self.status_cycle = itertools.cycle(self.statuses)
        self.update_status.start()

    def cog_unload(self):
        self.update_status.cancel()

    @tasks.loop(seconds=DURATION)
    async def update_status(self):
        next_status = next(self.status_cycle)
        # Set custom status
        activity = discord.CustomActivity(status=discord.Status.dnd, name=next_status)
        await self.bot.change_presence(status=activity=activity)
        print(f"✅ Custom status updated to: {next_status}")

    @update_status.before_loop
    async def before_update(self):
        await self.bot.wait_until_ready()

async def setup(bot: commands.Bot):
    await bot.add_cog(CustomStatusRotator(bot))
