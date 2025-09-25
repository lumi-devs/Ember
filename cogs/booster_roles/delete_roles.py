import discord
import os
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
    mod_ids = [int(x) for x in os.environ.get("MOD_IDS", "").split(",") if x]

    @bot.tree.command(
        name="delete_role",
        description="Delete your custom role (mods can delete others)"
    )
    @app_commands.describe(user="User whose role to delete (mods only)")
    async def delete_role(interaction: discord.Interaction, user: discord.User = None):
        await interaction.response.defer(ephemeral=True)
        db = load_db()

        # blacklist check
        if str(interaction.user.id) in db.get("blacklist", []):
            await interaction.followup.send(
                "🚫 You are blacklisted and cannot use this command.", ephemeral=True
            )
            return

        target_user = interaction.user
        if user:
            if interaction.user.id not in mod_ids:
                await interaction.followup.send(
                    "🚫 You are not allowed to delete other users' roles.", ephemeral=True
                )
                return
            target_user = user

        role_data = db["roles"].get(str(target_user.id))
        if not role_data:
            await interaction.followup.send(
                f"❌ {target_user.mention} does not have a custom role.", ephemeral=True
            )
            return

        role = interaction.guild.get_role(role_data["role_id"])
        if not role:
            await interaction.followup.send("❌ Role not found.", ephemeral=True)
            return

        # --- Confirmation for own role ---
        if target_user.id == interaction.user.id:
            delete_btn = discord.ui.Button(label="Yes", style=discord.ButtonStyle.green)
            cancel_btn = discord.ui.Button(label="No", style=discord.ButtonStyle.red)
            view = discord.ui.View(timeout=60)

            async def delete_callback(i: discord.Interaction):
                if i.user.id != interaction.user.id:
                    await interaction.followup.send("❌ Not your button.", ephemeral=True)
                    return
                db["roles"].pop(str(target_user.id), None)
                save_db(db)
                await role.delete()
                await interaction.followup.send(content="✅ Your role has been deleted.", ephemeral=True)

            async def cancel_callback(i: discord.Interaction):
                if i.user.id != interaction.user.id:
                    await interaction.followup.send(content="❌ Not your button.", ephemeral=True)
                    return
                await interaction.followup.send("❌ Cancelled.", ephemeral=True)

            delete_btn.callback = delete_callback
            cancel_btn.callback = cancel_callback
            view.add_item(delete_btn)
            view.add_item(cancel_btn)

            await interaction.followup.send(
                "⚠️ Are you sure you want to delete your role?",
                view=view,
                ephemeral=True
            )
        else:
            # --- Mod deletes immediately ---
            db["roles"].pop(str(target_user.id), None)
            save_db(db)
            await role.delete(reason=f"Deleted by mod {interaction.user}")
            await interaction.followup.send(
                f"✅ Deleted {target_user.mention}'s role.", ephemeral=True
            )
