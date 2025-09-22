import discord
import os
from discord import app_commands
from helpers.github_data import load_db, save_db

async def setup(bot: discord.Client):
    mod_ids = [int(x) for x in os.environ.get("MOD_IDS", "").split(",") if x]

    @bot.tree.command(
        name="delete_role",
        description="Delete your custom role (mods can delete others)"
    )
    @app_commands.describe(user="User whose role to delete (mods only)")
    async def delete_role(interaction: discord.Interaction, user: discord.User = None):
        #interaction
        await interaction.response.defer(ephemeral=True)
        db, file = load_db(file_path="custom_roles.json")

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
                save_db(db, file_path="custom_roles.json")
                await role.delete()
                await interaction.response.edit_message("✅ Your role has been deleted.")

            async def cancel_callback(i: discord.Interaction):
                if i.user.id != interaction.user.id:
                    await interaction.response.edit_message("❌ Not your button.")
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
            save_db(db, file, file_path="custom_roles.json")
            await role.delete(reason=f"Deleted by mod {interaction.user}")
            await interaction.followup.send(
                f"✅ Deleted {target_user.mention}'s role.", ephemeral=True
            )
