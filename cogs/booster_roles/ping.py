import discord
from discord import app_commands

async def setup(bot: discord.Client):

    @bot.tree.command(name="ping", description="Check bot latency")
    async def ping(interaction: discord.Interaction):
        import time
        start = time.perf_counter()
        await interaction.response.send_message("🏓 Pinging...", ephemeral=True)
        end = time.perf_counter()
        latency_ms = round((end - start) * 1000)
        await interaction.followup.send(f"🏓 Pong! Response time: {latency_ms}ms", ephemeral=True)
