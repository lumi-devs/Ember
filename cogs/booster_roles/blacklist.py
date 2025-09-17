import discord
from helpers.github_data import load_db,save_db

@bot.tree.command(name="blacklist", description="Manage the blacklist (mods only)")
@app_commands.describe(
    action="Action: add, remove, or list",
    user="User to add/remove (ignored for list)"
)
async def blacklist(interaction: discord.Interaction, action: str, user: discord.User = None):
    await interaction.response.defer(ephemeral=False)
    if interaction.user.id not in MOD_IDS:
        await interaction.followup.send("🚫 You are not allowed to use this command.")
        return

    db, file = load_db()

    if action.lower() == "list":
        bl_users = db.get("blacklist", [])
        if not bl_users:
            await interaction.followup.send("📜 Blacklist is empty.")
            return
        mentions = [f"<@{uid}>" for uid in bl_users]
        await interaction.followup.send("📜 **Blacklisted Users:**\n" + "\n".join(mentions))
        return

    if not user:
        await interaction.followup.send("❌ You must specify a user for this action.")
        return

    if action.lower() == "add":
        if str(user.id) in db["blacklist"]:
            await interaction.followup.send("⚠️ User already blacklisted.")
            return
        db["blacklist"].append(str(user.id))
        save_db(db, file)
        await interaction.followup.send(f"✅ {user.mention} has been blacklisted.")

    elif action.lower() == "remove":
        if str(user.id) not in db["blacklist"]:
            await interaction.followup.send("⚠️ User not in blacklist.")
            return
        db["blacklist"].remove(str(user.id))
        save_db(db, file)
        await interaction.followup.send(f"✅ {user.mention} has been removed from blacklist.")
    else:
        await interaction.followup.send("❌ Invalid action. Use add, remove, or list.")
    

async def setup(bot: commands.Bot):
    bot.tree.add_command(blacklist)