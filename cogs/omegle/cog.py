import discord
from discord.ext import commands
from helpers.github_data import load_db, save_db


async def setup(bot: commands.Bot):
    @bot.tree.command(name="omegle_send", description="Send omegle button to a certain ")
    async def help_command(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        


