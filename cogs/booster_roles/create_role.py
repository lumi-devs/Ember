import asyncio
from datetime import datetime, timezone, timedelta
import os
import json
from pathlib import Path
import discord
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

async def setup(bot: discord.Client):
    @bot.tree.command(name="create_role", description="Create a custom booster role")
    @app_commands.describe(
        name="Name of your role",
        color="Color hex like #ff0000 (optional)"
    )
    async def create_role(interaction: discord.Interaction, name: str, color: str = None):
        await interaction.response.defer()
        booster_role = interaction.guild.get_role(int(os.environ.get("BOOSTER_ROLE_ID")))
        if booster_role not in interaction.user.roles:
            await interaction.followup.send("🚫 Booster role required.")
            return

        db = load_db()

        if str(interaction.user.id) in db.get("blacklist", []):
            await interaction.followup.send("🚫 You are blacklisted and cannot use this command.", ephemeral=True)
            return

        if str(interaction.user.id) in db["roles"]:
            await interaction.followup.send("❌ You already have a role.")
            return

        # Validate color
        try:
            primary_color = Color.from_str(color) if color else Color.default()
        except Exception:
            await interaction.followup.send("❌ Invalid color format. Use hex like #ff0000", ephemeral=True)
            return

        try:
            # Create role
            role = await interaction.guild.create_role(
                name=name,
                colour=primary_color,
                reason=f"Custom booster role for {interaction.user}"
            )
            role = await interaction.guild.fetch_role(role.id)
            await asyncio.sleep(2)

            # Fix role position
            fixed_role = interaction.guild.get_role(int(os.environ.get("FIXED_ROLE_ID", 0)))
            if fixed_role:
                await role.edit(position=fixed_role.position)

            await interaction.user.add_roles(role)

            # Embed confirmation
            IST = timezone(timedelta(hours=5, minutes=30))
            now_ist = datetime.now(IST)
            sidebar_color = role.color if role.color.value != 0 else Color.light_grey()

            embed = Embed(
                color=sidebar_color,
                timestamp=now_ist
            )
            embed.set_author(
                name="New Booster Role created!",
                icon_url=interaction.user.avatar.url if interaction.user.avatar else None
            )
            embed.add_field(name="Name", value=str(role), inline=True)
            embed.add_field(name="Color", value=color or "Default", inline=True)
            embed.set_footer(
                text="Thanks for boosting",
                icon_url=interaction.guild.icon.url if interaction.guild.icon else None
            )
            embed.set_image(url="https://cdn.discordapp.com/attachments/1393171142436458558/1415000838329339945/GWygmv0.gif")

            await interaction.followup.send(embed=embed)

        except Exception as e:
            print(e)
            await interaction.followup.send(f"❌ Role creation failed: {e}", ephemeral=True)
            return

        # --- Save to DB locally ---
        ist_time = datetime.now(timezone(timedelta(hours=5, minutes=30)))
        db["roles"][str(interaction.user.id)] = {
            "role_id": role.id,
            "name": name,
            "color": color or "default",
            "created_at": ist_time.isoformat(),
            "requested_by": str(interaction.user)
        }

        try:
            save_db(db)
            print("Saved to data/custom_roles.json")
        except Exception as e:
            print(f"Failed to save DB: {e}")
