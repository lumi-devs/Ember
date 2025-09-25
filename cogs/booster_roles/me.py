import discord
from discord import app_commands
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

# --- Setup ---
async def setup(bot: discord.Client):
    @bot.tree.command(name="me", description="View your custom role info")
    async def me(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        db = load_db()

        # --- Blacklist check ---
        if str(interaction.user.id) in db.get("blacklist", []):
            await interaction.followup.send("🚫 You are blacklisted and cannot use this command.", ephemeral=True)
            return

        # --- Role check ---
        role_data = db["roles"].get(str(interaction.user.id))
        if not role_data:
            await interaction.followup.send("❌ You don't have a role.", ephemeral=True)
            return

        role = interaction.guild.get_role(role_data["role_id"])
        if not role:
            await interaction.followup.send("⚠️ Role not found.", ephemeral=True)
            return

        # --- Handle icon rendering ---
        icon_value = role_data.get("icon_value")
        icon_type = role_data.get("icon_type")

        if not icon_value:
            if role.unicode_emoji:
                icon_type = "unicode"
                icon_value = role.unicode_emoji
            elif role.display_icon:
                icon_type = "custom"
                icon_value = str(role.display_icon.id)

        icon_display = "None"
        if icon_value:
            if icon_type == "unicode":
                icon_display = icon_value
            elif icon_type == "custom":
                emoji_obj = interaction.guild.get_emoji(int(icon_value))
                icon_display = str(emoji_obj) if emoji_obj else "(not found)"

        # --- Build embed ---
        embed = discord.Embed(
            title="🌟 Your Role Info",
            color=discord.Color.from_str(role_data["color"]) if role_data.get("color") else discord.Color.blurple()
        )
        embed.add_field(name="👤 Name", value=role.name, inline=True)
        embed.add_field(name="🎨 Primary Color", value=role_data.get("color", "None"), inline=True)
        embed.add_field(name="🌈 Type", value=role_data.get("color_type", "normal"), inline=True)

        if role_data.get("color_type") == "gradient":
            embed.add_field(name="🎨 Secondary Color", value=role_data.get("secondary_colour", "None"), inline=True)

        embed.add_field(name="🔹 Icon", value=icon_display, inline=True)

        # --- Footer with timestamp + guild icon ---
        timestamp = datetime.now().strftime('%d-%m-%Y %I:%M %p IST')
        if interaction.guild.icon:
            embed.set_footer(text=f"{interaction.guild.name} • {timestamp}", icon_url=interaction.guild.icon.url)
        else:
            embed.set_footer(text=f"{interaction.guild.name} • {timestamp}")

        await interaction.followup.send(embed=embed, ephemeral=True)
