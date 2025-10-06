import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
import os
import json
from cogs.confessions.helpers import DB_PATH

# Fetch MOD_ROLE_IDS from environment variable as a list
MOD_ROLE_IDS = json.loads(os.environ.get("MOD_ROLE_IDS", "[]"))  # e.g. "[123456, 987654]"

class ConfessBan(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="confessban", description="Ban or unban a user from submitting confessions")
    @app_commands.describe(user="The user to ban/unban")
    @app_commands.choices(action=[
        app_commands.Choice(name="Ban", value="ban"),
        app_commands.Choice(name="Unban", value="unban")
    ])
    async def confessban(self, interaction: discord.Interaction, user: discord.User, action: app_commands.Choice[str]):
        # Only mods
        if not any(role.id in MOD_ROLE_IDS for role in interaction.user.roles):
            return await interaction.response.send_message("❌ You do not have permission.", ephemeral=True)

        async with aiosqlite.connect(DB_PATH) as db:
            if action.value == "ban":
                await db.execute(
                    "INSERT OR IGNORE INTO confessban (user_id) VALUES (?)", (user.id,)
                )
                await db.commit()
                await interaction.response.send_message(f"✅ {user.mention} is now banned from confessions.")
            else:  # unban
                await db.execute(
                    "DELETE FROM confessban WHERE user_id=?", (user.id,)
                )
                await db.commit()
                await interaction.response.send_message(f"✅ {user.mention} is now unbanned from confessions.")


async def setup(bot):
    await bot.add_cog(ConfessBan(bot))
