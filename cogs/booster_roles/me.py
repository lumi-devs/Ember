import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime
from pathlib import Path
import json

# --- Local DB helpers ---
DATA_FILE = Path("data/custom_roles.json")
DATA_FILE.parent.mkdir(parents=True, exist_ok=True)

def load_db():
    if DATA_FILE.exists():
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"roles": {}, "blacklist": []}

class RoleInfo(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="tasrole", description="View your custom booster role info")
    async def tasrole(self, interaction: discord.Interaction):
        """View your custom role info. Usage: /tasrole"""
        
        db = load_db()
        user_id = str(interaction.user.id)

        # --- Blacklist check ---
        if user_id in db.get("blacklist", []):
            await interaction.response.send_message(
                "🚫 You are blacklisted and cannot use this command.",
                ephemeral=True
            )
            return

        # --- Role check ---
        role_data = db["roles"].get(user_id)
        if not role_data:
            await interaction.response.send_message(
                "❌ You don't have a custom role yet.",
                ephemeral=True
            )
            return

        role = interaction.guild.get_role(role_data["role_id"])
        if not role:
            await interaction.response.send_message(
                "⚠️ Your saved role could not be found on the server.",
                ephemeral=True
            )
            return

        # --- Build embed ---
        embed = discord.Embed(
            title="🌟 Your Custom Role Info",
            color=discord.Color.orange()
        )
        embed.add_field(name="👤 Role Name", value=role.name, inline=True)
        embed.add_field(name="🆔 Role ID", value=str(role.id), inline=True)
        embed.add_field(name="🎨 Color", value=str(role.color), inline=True)
        
        if role_data.get("created_at"):
            embed.add_field(
                name="📅 Created",
                value=role_data["created_at"][:10],
                inline=True
            )

        embed.add_field(
            name="👑 Created For",
            value=interaction.user.mention,
            inline=True
        )

        # --- Footer ---
        timestamp = datetime.now().strftime('%d-%m-%Y %I:%M %p IST')
        if interaction.guild.icon:
            embed.set_footer(
                text=f"{interaction.guild.name} • {timestamp}",
                icon_url=interaction.guild.icon.url
            )
        else:
            embed.set_footer(text=f"{interaction.guild.name} • {timestamp}")

        await interaction.response.send_message(embed=embed, ephemeral=True)

async def setup(bot):
    await bot.add_cog(RoleInfo(bot))
