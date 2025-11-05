import asyncio
from datetime import datetime, timezone, timedelta
import os
import json
from pathlib import Path
import discord
from discord.ext import commands
from discord import app_commands, Color, Embed

# Ensure data folder exists
DATA_FILE = Path("data/custom_roles.json")
DATA_FILE.parent.mkdir(parents=True, exist_ok=True)

# --- Load DB ---
def load_db():
    if DATA_FILE.exists():
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    else:
        return {"roles": {}, "blacklist": []}

# --- Save DB ---
def save_db(db):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=4)


class CustomRoles(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # Slash command version
    @app_commands.command(name="tascreate", description="Create a custom booster role")
    async def tascreate(self, interaction: discord.Interaction, name: str):
        """Slash command: /tascreate <role_name>"""

        # Restrict to specific channel
        allowed_channel_id = int(os.environ.get("CUSTOM_ROLE_CHANNEL_ID", 0))
        if interaction.channel.id != allowed_channel_id:
            await interaction.response.send_message(
                "🚫 This command can only be used in the designated role creation channel.",
                ephemeral=True
            )
            return

        booster_role = interaction.guild.get_role(int(os.environ.get("BOOSTER_ROLE_ID")))
        if booster_role not in interaction.user.roles:
            await interaction.response.send_message("🚫 Booster role required.", ephemeral=True)
            return

        db = load_db()

        if str(interaction.user.id) in db.get("blacklist", []):
            await interaction.response.send_message(
                "🚫 You are blacklisted and cannot use this command.",
                ephemeral=True
            )
            return

        if str(interaction.user.id) in db["roles"]:
            await interaction.response.send_message("❌ You already have a role.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        try:
            # Create role with default color
            role = await interaction.guild.create_role(
                name=name,
                reason=f"Custom booster role for {interaction.user}"
            )

            placed_above_role = interaction.guild.get_role(int(os.environ.get("FIXED_ROLE_ID")))
            await role.edit(position=placed_above_role.position)
            await interaction.user.add_roles(role)

            # Embed confirmation
            IST = timezone(timedelta(hours=5, minutes=30))
            now_ist = datetime.now(IST)

            embed = Embed(color=Color.gold(), timestamp=now_ist)
            embed.set_author(
                name="New Booster Role created!",
                icon_url=interaction.user.avatar.url if interaction.user.avatar else None
            )
            embed.add_field(name="Role Name", value=name, inline=True)
            embed.add_field(name="Created For", value=interaction.user.mention, inline=True)
            embed.set_footer(
                text="Thanks for boosting",
                icon_url=interaction.guild.icon.url if interaction.guild.icon else None
            )
            embed.set_image(
                url="https://cdn.discordapp.com/attachments/1393171142436458558/1415000838329339945/GWygmv0.gif"
            )

            await interaction.followup.send(embed=embed)

        except Exception as e:
            print(e)
            await interaction.followup.send(f"❌ Role creation failed: {e}")
            return

        # --- Save to DB locally ---
        ist_time = datetime.now(timezone(timedelta(hours=5, minutes=30)))
        db["roles"][str(interaction.user.id)] = {
            "role_id": role.id,
            "name": name,
            "created_at": ist_time.isoformat(),
            "requested_by": str(interaction.user)
        }

        try:
            save_db(db)
            print("Saved to data/custom_roles.json")
        except Exception as e:
            print(f"Failed to save DB: {e}")


async def setup(bot):
    await bot.add_cog(CustomRoles(bot))
