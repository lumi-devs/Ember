import os
import discord
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

# --- Command setup ---
async def setup(bot: discord.Client):

    @bot.tree.command(name="blacklist", description="Manage the blacklist (mods only)")
    @app_commands.describe(
        action="Action: add, remove, or list",
        user="User to add/remove (ignored for list)"
    )
    async def blacklist(interaction: discord.Interaction, action: str, user: discord.User = None):
        await interaction.response.defer(ephemeral=True)

        # MOD_IDS stored as comma-separated string in .env, e.g. "123,456,789"
        mod_ids = [int(x) for x in os.environ.get("MOD_IDS", "").split(",") if x.strip()]
        if interaction.user.id not in mod_ids:
            await interaction.followup.send("🚫 You are not allowed to use this command.", ephemeral=True)
            return

        db = load_db()
        action = action.lower()

        if action == "list":
            bl_users = db.get("blacklist", [])
            if not bl_users:
                await interaction.followup.send("📜 Blacklist is empty.", ephemeral=True)
                return
            mentions = [f"<@{uid}>" for uid in bl_users]
            await interaction.followup.send("📜 **Blacklisted Users:**\n" + "\n".join(mentions), ephemeral=True)
            return

        if not user:
            await interaction.followup.send("❌ You must specify a user for this action.", ephemeral=True)
            return

        if action == "add":
            if str(user.id) in db["blacklist"]:
                await interaction.followup.send("⚠️ User already blacklisted.", ephemeral=True)
                return
            db["blacklist"].append(str(user.id))
            save_db(db)
            await interaction.followup.send(f"✅ {user.mention} has been blacklisted.", ephemeral=True)

        elif action == "remove":
            if str(user.id) not in db["blacklist"]:
                await interaction.followup.send("⚠️ User not in blacklist.", ephemeral=True)
                return
            db["blacklist"].remove(str(user.id))
            save_db(db)
            await interaction.followup.send(f"✅ {user.mention} has been removed from blacklist.", ephemeral=True)
        else:
            await interaction.followup.send("❌ Invalid action. Use add, remove, or list.", ephemeral=True)
