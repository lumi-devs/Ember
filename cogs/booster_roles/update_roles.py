import os
import discord
from discord.ext import commands
from discord import Color, app_commands
import re
import json
import aiohttp
import io

# --- Local JSON helpers ---
def load_db(file_path="data/custom_roles.json"):
    """Load database from local JSON file"""
    try:
        with open(file_path, 'r') as f:
            return json.load(f), file_path
    except FileNotFoundError:
        # Return empty database structure if file doesn't exist
        return {"roles": {}, "blacklist": []}, file_path

def save_db(db, file_path="data/custom_roles.json"):
    """Save database to local JSON file"""
    with open(file_path, 'w') as f:
        json.dump(db, f, indent=4)

class RoleUpdate(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # --- Helper ---
    def get_role_data(self, interaction):
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

    async def download_emoji_image(self, emoji_url):
        """Download emoji image and return as bytes"""
        async with aiohttp.ClientSession() as session:
            async with session.get(emoji_url) as resp:
                if resp.status == 200:
                    return await resp.read()
                else:
                    raise ValueError(f"Failed to download emoji image: {resp.status}")

    async def set_role_icon(self, role: discord.Role, emoji_input: str):
        """Sets the role icon based on input emoji. Returns (icon_type, icon_value) for DB storage."""
        if not emoji_input:
            await role.edit(unicode_emoji=None, display_icon=None)
            return None, None

        match = re.match(r"<a?:(\w+):(\d+)>", emoji_input)
        if match:
            emoji_name, emoji_id = match.groups()
            emoji_id = int(emoji_id)
            
            emoji = discord.utils.get(role.guild.emojis, id=emoji_id)
            if emoji:
                emoji_bytes = await emoji.read()
                await role.edit(display_icon=emoji_bytes)
                return "custom", str(emoji_id)
            else:
                emoji_url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{'gif' if emoji_input.startswith('<a:') else 'png'}"
                try:
                    emoji_bytes = await self.download_emoji_image(emoji_url)
                    await role.edit(display_icon=emoji_bytes)
                    return "custom", str(emoji_id)
                except Exception as e:
                    raise ValueError(f"Could not use custom emoji: {e}")

        await role.edit(unicode_emoji=emoji_input, display_icon=None)
        return "unicode", emoji_input

    # --- /tasname ---
    @app_commands.command(name='tasname', description='Update your role name')
    async def tasname(self, interaction: discord.Interaction, new_name: str):
        db, file, result = self.get_role_data(interaction)
        if not db:
            await interaction.response.send_message(result, ephemeral=True)
            return
        role, role_data = result
        await role.edit(name=new_name)
        role_data["name"] = new_name
        save_db(db)
        await interaction.response.send_message(f"✅ Role name updated to **{new_name}**!", ephemeral=True)

    # --- /tasprimary ---
    @app_commands.command(name='tasprimary', description='Update your role primary color')
    async def tasprimary(self, interaction: discord.Interaction, color: str):
        db, file, result = self.get_role_data(interaction)
        if not db:
            await interaction.response.send_message(result, ephemeral=True)
            return
        role, role_data = result
        try:
            primary = Color.from_str(color)
            await role.edit(colour=primary)
            role_data["color"] = color
            role_data["color_type"] = "normal"
            save_db(db)
            await interaction.response.send_message(f"✅ Primary color updated to `{color}`!", ephemeral=True)
        except Exception:
            await interaction.response.send_message("❌ Invalid color format. Use hex like #ff0000", ephemeral=True)

    # --- /tassecondary ---
    @app_commands.command(name='tassecondary', description='Update or remove secondary color')
    async def tassecondary(self, interaction: discord.Interaction, secondary: str = None):
        db, file, result = self.get_role_data(interaction)
        if not db:
            await interaction.response.send_message(result, ephemeral=True)
            return
        
        role, role_data = result
        primary_hex = role_data.get("color")
        if not primary_hex:
            await interaction.response.send_message("⚠️ Please set a primary color first using `/tasprimary`.", ephemeral=True)
            return

        primary = Color.from_str(primary_hex)

        if secondary is None or secondary.lower() == 'remove':
            if role_data.get("secondary_colour"):
                await role.edit(color=primary)
                role_data.pop("secondary_colour", None)
                role_data["color_type"] = "normal"
                save_db(db)
                await interaction.response.send_message("✅ Secondary color removed!", ephemeral=True)
            else:
                await interaction.response.send_message("ℹ️ No secondary color set.", ephemeral=True)
            return

        try:
            secondary_obj = Color.from_str(secondary)
            await role.edit(secondary_color=secondary_obj)
            role_data["secondary_colour"] = secondary
            role_data["color_type"] = "gradient"
            save_db(db)
            await interaction.response.send_message(f"✅ Secondary color set to `{secondary}`!", ephemeral=True)
        except Exception:
            await interaction.response.send_message("❌ Invalid color format. Use hex like #ff0000", ephemeral=True)

    # --- /tasicon ---
    @app_commands.command(name='tasicon', description='Update or remove role icon')
    async def tasicon(self, interaction: discord.Interaction, icon: str = None):
        db, file, result = self.get_role_data(interaction)
        if not db:
            await interaction.response.send_message(result, ephemeral=True)
            return

        role, role_data = result

        if icon and icon.lower() != 'remove':
            try:
                match = re.match(r"<a?:(\w+):(\d+)>", icon)
                if match:
                    emoji_name, emoji_id = match.groups()
                    emoji_id = int(emoji_id)
                    emoji = discord.utils.get(interaction.guild.emojis, id=emoji_id)
                    if emoji:
                        emoji_bytes = await emoji.read()
                        await role.edit(display_icon=emoji_bytes)
                        icon_type = "custom"
                        icon_value = str(emoji_id)
                    else:
                        emoji_url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{'gif' if icon.startswith('<a:') else 'png'}"
                        async with aiohttp.ClientSession() as session:
                            async with session.get(emoji_url) as resp:
                                if resp.status == 200:
                                    emoji_bytes = await resp.read()
                                    await role.edit(display_icon=emoji_bytes)
                                    icon_type = "custom"
                                    icon_value = str(emoji_id)
                                else:
                                    raise ValueError("Could not download emoji image")
                else:
                    try:
                        codepoints = '-'.join([f'{ord(c):x}' for c in icon])
                        emoji_url = f"https://cdnjs.cloudflare.com/ajax/libs/twemoji/14.0.2/72x72/{codepoints}.png"
                        async with aiohttp.ClientSession() as session:
                            async with session.get(emoji_url) as resp:
                                if resp.status == 200:
                                    emoji_bytes = await resp.read()
                                    await role.edit(display_icon=emoji_bytes)
                                    icon_type = "unicode"
                                    icon_value = icon
                                else:
                                    raise ValueError("Unicode emoji could not be converted to image")
                    except Exception as e:
                        raise ValueError(f"Could not set unicode emoji: {e}")

                role_data["icon_type"] = icon_type
                role_data["icon_value"] = icon_value
                save_db(db)
                await interaction.response.send_message(f"✅ Role icon updated to: {icon}", ephemeral=True)
                
            except Exception as e:
                await interaction.response.send_message(f"❌ Failed to set icon: {e}", ephemeral=True)
                
        else:
            if role_data.get("icon_value"): 
                try:
                    await role.edit(display_icon=None)
                    role_data.pop("icon_type", None) 
                    role_data.pop("icon_value", None) 
                    save_db(db)
                    await interaction.response.send_message("✅ Role icon removed.", ephemeral=True)
                except Exception as e:
                    await interaction.response.send_message(f"❌ Failed to remove icon: {e}", ephemeral=True)
            else: 
                await interaction.response.send_message("ℹ️ No icon is currently set.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(RoleUpdate(bot))
