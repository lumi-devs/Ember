import discord
from discord.ext import commands, tasks
import random

DURATION = 120

class CustomStatusRotator(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.statuses = [
            "Living the femboy life",
            "Rebiz is a femboy",
            "Garamchai: 99% 1% hooman",
            "Muse: Playing roblox like its doomsday.",
            "Aster makes everyday feel like a festival",
            "Dabi is suffering from main character syndrome",
            "Rae is pretty as the first drop of rain",
            "Chilling at The Aakhri Station",
            "Watching cat videos",
            "Meow Meow Cuddles?",
            "Mango and her aam zindagi :p ",
            "Sophicticasted monkey, definetely not human!",
            "honey bunny honey bunny",
            "avii is a professional yapper",
            "Hannan the fiddler",
            "LEMME CATCH THE CATS!!",
            "Mother HONEY??",
            "Gaming with Daemon didi!",
            "Kims IS A 13YO!"
        ]
        self.queue = []          # Random order list
        self.last_status = None  # To avoid accidental repeat
        self.update_status.start()

    def get_next_status(self):
        # Refill + shuffle if queue finished
        if not self.queue:
            self.queue = self.statuses.copy()
            random.shuffle(self.queue)

        next_status = self.queue.pop(0)

        # Safety check so the same thing never happens twice in a row
        if next_status == self.last_status and len(self.queue) > 0:
            # Swap with another random one
            alt = random.choice(self.queue)
            idx = self.queue.index(alt)
            self.queue[idx] = next_status
            next_status = alt

        self.last_status = next_status
        return next_status

    def cog_unload(self):
        self.update_status.cancel()

    @tasks.loop(seconds=DURATION)
    async def update_status(self):
        next_status = self.get_next_status()
        activity = discord.CustomActivity(name=next_status)
        await self.bot.change_presence(status=discord.Status.idle, activity=activity)
        print(f"🔁 Status changed to: {next_status}")

    @update_status.before_loop
    async def before_update(self):
        await self.bot.wait_until_ready()

async def setup(bot: commands.Bot):
    await bot.add_cog(CustomStatusRotator(bot))
