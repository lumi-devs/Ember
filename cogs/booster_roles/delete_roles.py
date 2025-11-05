import discord
import os
from discord.ext import commands
from discord import app_commands
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

def save_db(db):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=4)


class CustomRoleDelete(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.mod_ids = [int(x) for x in os.environ.get("MOD_IDS", "").split(",") if x]

    @app_commands.command(name="tasdelete", description="Delete your custom role (mods can delete others)")
    async def tasdelete(self, interaction: discord.Interaction, user: discord.Member = None):
        """Slash command: /tasdelete [@user] (mods only)"""

        db = load_db()

        # Blacklist check
        if str(interaction.user.id) in db.get("blacklist", []):
            await interaction.response.send_message(
                "🚫 You are blacklisted and cannot use this command.",
                ephemeral=True
            )
            return

        target_user = interaction.user
        if user:
            if interaction.user.id not in self.mod_ids:
                await interaction.response.send_message(
                    "🚫 You are not allowed to delete other users' roles.",
                    ephemeral=True
                )
                return
            target_user = user

        role_data = db["roles"].get(str(target_user.id))
        if not role_data:
            await interaction.response.send_message(
                f"❌ {target_user.mention} does not have a custom role.",
                ephemeral=True
            )
            return

        role = interaction.guild.get_role(role_data["role_id"])
        if not role:
            await interaction.response.send_message("❌ Role not found.", ephemeral=True)
            return

        # Delete role immediately
        db["roles"].pop(str(target_user.id), None)
        save_db(db)
        await role.delete(reason=f"Deleted by {interaction.user}")

        if target_user.id == interaction.user.id:
            await interaction.response.send_message("✅ Your role has been deleted.", ephemeral=True)
        else:
            await interaction.response.send_message(
                f"✅ Deleted {target_user.mention}'s role.",
                ephemeral=False
            )


async def setup(bot):
    await bot.add_cog(CustomRoleDelete(bot))