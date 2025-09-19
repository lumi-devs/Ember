import asyncio
from datetime import datetime, timezone, timedelta
import os
import discord
from discord import app_commands, Color, Embed
from helpers.github_data import load_db, save_db

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

        db, file = load_db(file_path="custom_roles.json")

        if str(interaction.user.id) in db.get("blacklist", []):
            await interaction.followup.send("🚫 You are blacklisted and cannot use this command.", ephemeral=True)
            return

        if str(interaction.user.id) in db["roles"]:
            await interaction.followup.send("❌ You already have a role.")
            return

        try:
            primary_color = Color.from_str(color) if color else Color.default()
        except Exception:
            await interaction.followup.send("❌ Invalid color format. Use hex like #ff0000", ephemeral=True)
            return

        try:
            role = await interaction.guild.create_role(
                name=name,
                colour=primary_color,
                reason=f"Custom booster role for {interaction.user}"
            )
            await asyncio.sleep(2)
            role = await interaction.guild.fetch_role(role.id)
            fixed_role = interaction.guild.get_role(int(os.environ.get("FIXED_ROLE_ID", 0)))
            if fixed_role:
                await role.edit(position=fixed_role.position - 1)
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

        # --- Save to DB ---
        ist_time = datetime.now(timezone(timedelta(hours=5, minutes=30)))
        db["roles"][str(interaction.user.id)] = {
            "role_id": role.id,
            "name": name,
            "color": color or "default",
            "created_at": ist_time.isoformat(),
            "requested_by": str(interaction.user)
        }

        try:
            save_db(db,file,file_path="custom_roles.json")
            print("saved to db")
        except Exception as e:
           print(f"Failed to save db: {e}")
