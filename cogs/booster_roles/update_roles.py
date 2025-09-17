import os
import discord
from discord import app_commands, Color
from discord.ext import commands
import re
from helpers.github_data import load_db, save_db

async def setup(bot: discord.Client):
    update_group = app_commands.Group(name="update", description="Update your custom role")

    # --- Helper ---
    def get_role_data(interaction):
        db, file = load_db()
        if str(interaction.user.id) in db.get("blacklist", []):
            return None, None, "🚫 You are blacklisted and cannot use this command."
        role_data = db["roles"].get(str(interaction.user.id))
        if not role_data:
            return None, None, "❌ You don't have a role."
        role = interaction.guild.get_role(role_data["role_id"])
        if not role:
            return None, None, "⚠️ Role not found."
        return db, file, (role, role_data)

    # --- /update name ---
    @update_group.command(name="name", description="Update your role name")
    async def update_name(interaction: discord.Interaction, new_name: str):
        await interaction.response.defer(ephemeral=False)
        db, file, result = get_role_data(interaction)
        if not db:
            await interaction.followup.send(result, ephemeral=True)
            return
        role, role_data = result
        await role.edit(name=new_name)
        role_data["name"] = new_name
        save_db(db, file)
        await interaction.followup.send(f"✅ Role name updated to **{new_name}**!")

    # --- /update color (primary) ---
    @update_group.command(name="color", description="Update your role primary color")
    async def update_color(interaction: discord.Interaction, color: str):
        await interaction.response.defer(ephemeral=False)
        db, file, result = get_role_data(interaction)
        if not db:
            await interaction.followup.send(result, ephemeral=True)
            return
        role, role_data = result
        try:
            primary = Color.from_str(color)
            await role.edit(colour=primary)
            role_data["color"] = color
            if role_data.get("secondary_colour"):
                role_data["color_type"] = "gradient"
            else:
                role_data["color_type"] = "normal"
            save_db(db, file)
            await interaction.followup.send(f"✅ Primary color updated to `{color}`!")
        except Exception as e:
            await interaction.followup.send(f"❌ Invalid color: {e}", ephemeral=True)

    # --- /update secondary ---
    @update_group.command(name="secondary", description="Update or remove secondary color")
    async def update_secondary(interaction: discord.Interaction, secondary: str = ''):
        await interaction.response.defer(ephemeral=False)
        db, file, result = get_role_data(interaction)
        if not db:
            await interaction.followup.send(result, ephemeral=True)
            return
        role, role_data = result
        # Check if primary color exists
        primary_hex = role_data.get("color")
        if not primary_hex or primary_hex.lower() == "default":
            await interaction.followup.send("⚠️ Please set a primary color first using `/update color`.", ephemeral=True)
            return
        try:
            primary_obj = Color.from_str(primary_hex)
        except Exception:
            await interaction.followup.send("❌ Stored primary color is invalid. Please set it again with `/update color`.", ephemeral=True)
            return
        # --- If secondary provided ---
        if secondary.strip():
            try:
                secondary_obj = Color.from_str(secondary)

                # save in DB
                role_data["secondary_colour"] = secondary
                role_data["color_type"] = "gradient"

                # NOTE: Discord roles don’t support gradients natively, so we just store it in DB
                await role.edit(colour=primary_obj, secondary_colour=secondary_obj)

                save_db(db, file)
                await interaction.followup.send(
                    f"✅ Secondary color set to `{secondary}` (gradient stored in DB)."
                )
            except Exception as e:
                await interaction.followup.send(f"❌ Invalid secondary color: {e}", ephemeral=True)

        # --- If no secondary (remove gradient) ---
        else:
            if role_data.get("secondary_colour"):
                role_data.pop("secondary_colour", None)
                role_data["color_type"] = "normal"

                await role.edit(colour=primary_obj)  # reset to primary only

                save_db(db, file)
                await interaction.followup.send("ℹ️ Secondary color removed, role is now primary only.")
            else:
                await interaction.followup.send("ℹ️ No secondary color set.", ephemeral=True)

    async def set_role_icon(role: discord.Role, emoji_str: str):
        """ Sets the role icon based on input emoji. Returns (icon_type, icon_value) for DB storage. """
        if not emoji_str:
            await role.edit(unicode_emoji=None, display_icon=None)
            return None, None

        # Check if custom emoji like <:smile:1234567890>
        match = re.match(r"<a?:\w+:(\d+)>", emoji_str)
        if match:
            emoji_id = int(match.group(1))
            emoji = discord.utils.get(role.guild.emojis, id=emoji_id)
            if not emoji:
                raise ValueError("Emoji not found in this server.")
            emoji_bytes = await emoji.read()
            await role.edit(display_icon=emoji_bytes)
            return "custom", str(emoji_id)

        # Otherwise assume unicode emoji
        await role.edit(display_icon=emoji_str)
        return "unicode", emoji_str

    # --- /update icon ---
    @update_group.command(name="icon", description="Update or remove role icon")
    async def update_icon(interaction: discord.Interaction, icon: str = None):
        await interaction.response.defer(ephemeral=False)
        db, file, result = get_role_data(interaction)
        if not db:
            await interaction.followup.send(result, ephemeral=True)
            return

        role, role_data = result  # ✅ correctly unpack role + data

        if icon:
            try:
                icon_type, icon_value = await set_role_icon(role, icon)
                role_data["icon_type"] = icon_type
                role_data["icon_value"] = icon_value
                save_db(db, file)
                await interaction.followup.send("✅ Icon updated!")
            except Exception as e:
                await interaction.followup.send(f"❌ Invalid icon: {e}", ephemeral=True)
        else: 
            if role_data.get("icon_value"): 
                await role.edit(display_icon=None) 
                role_data.pop("icon_type", None) 
                role_data.pop("icon_value", None) 
                save_db(db, file) 
                await interaction.followup.send("ℹ️ Icon removed.") 
            else: 
                await interaction.followup.send("ℹ️ No icon set.", ephemeral=True)

    # register the group
    bot.tree.add_command(update_group)
