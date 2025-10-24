import os
import discord
from discord.ext import commands
from discord import Color
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
    def get_role_data(self, ctx):
        db, file = load_db()
        if str(ctx.author.id) in db.get("blacklist", []):
            return None, None, "🚫 You are blacklisted and cannot use this command."
        role_data = db["roles"].get(str(ctx.author.id))
        if not role_data:
            return None, None, "❌ You don't have a role."
        role = ctx.guild.get_role(role_data["role_id"])
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

        # Check if custom emoji like <:smile:1234567890>
        match = re.match(r"<a?:(\w+):(\d+)>", emoji_input)
        if match:
            emoji_name, emoji_id = match.groups()
            emoji_id = int(emoji_id)
            
            # Try to get emoji from guild
            emoji = discord.utils.get(role.guild.emojis, id=emoji_id)
            if emoji:
                # Use the emoji directly if it's in the guild
                emoji_bytes = await emoji.read()
                await role.edit(display_icon=emoji_bytes)
                return "custom", str(emoji_id)
            else:
                # If emoji not in guild, try to download it
                emoji_url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{'gif' if emoji_input.startswith('<a:') else 'png'}"
                try:
                    emoji_bytes = await self.download_emoji_image(emoji_url)
                    await role.edit(display_icon=emoji_bytes)
                    return "custom", str(emoji_id)
                except Exception as e:
                    raise ValueError(f"Could not use custom emoji: {e}")

        # Otherwise assume unicode emoji
        await role.edit(unicode_emoji=emoji_input, display_icon=None)
        return "unicode", emoji_input

    # --- .TASname ---
    @commands.command(name='TASname', description='Update your role name')
    async def tasname(self, ctx, *, new_name: str):
        """Update your role name. Usage: .TASname <new name>"""
        db, file, result = self.get_role_data(ctx)
        if not db:
            await ctx.send(result, ephemeral=True)
            return
        role, role_data = result
        await role.edit(name=new_name)
        role_data["name"] = new_name
        save_db(db)
        await ctx.send(f"✅ Role name updated to **{new_name}**!", ephemeral=True)

    # --- .TASprimary ---
    @commands.command(name='TASprimary', description='Update your role primary color')
    async def tasprimary(self, ctx, color: str):
        """Update your role primary color. Usage: .TASprimary #hexcolor"""
        db, file, result = self.get_role_data(ctx)
        if not db:
            await ctx.send(result, ephemeral=True)
            return
        role, role_data = result
        try:
            primary = Color.from_str(color)
            await role.edit(colour=primary)
            role_data["color"] = color
            role_data["color_type"] = "normal"  # Reset to normal when setting primary
            save_db(db)
            await ctx.send(f"✅ Primary color updated to `{color}`!", ephemeral=True)
        except Exception as e:
            await ctx.send(f"❌ Invalid color format. Use hex like #ff0000", ephemeral=True)

    # --- .TASsecondary ---
    @commands.command(name='TASsecondary', description='Update or remove secondary color')
    async def tassecondary(self, ctx, secondary: str = None):
        """Update or remove secondary color. Usage: .TASsecondary [#hexcolor] or .TASsecondary remove"""
        db, file, result = self.get_role_data(ctx)
        if not db:
            await ctx.send(result, ephemeral=True)
            return
        
        role, role_data = result
        
        # Check if primary color exists
        primary_hex = role_data.get("color")
        primary = Color.from_str(primary_hex)
        if not primary_hex:
            await ctx.send("⚠️ Please set a primary color first using `.TASprimary`.", ephemeral=True)
            return
        
        # Remove secondary color
        if secondary is None or secondary.lower() == 'remove':
            if role_data.get("secondary_colour"):
                await role.edit(color=primary, secondary_color=None)
                role_data.pop("secondary_colour", None)
                role_data["color_type"] = "normal"
                save_db(db)
                await ctx.send("✅ Secondary color removed!", ephemeral=True)
            else:
                await ctx.send("ℹ️ No secondary color set.", ephemeral=True)
            return

        # Set secondary color
        try:
            secondary_obj = Color.from_str(secondary)
            await role.edit(color=primary, secondary_color=secondary_obj)
            role_data["secondary_colour"] = secondary
            role_data["color_type"] = "gradient"
            save_db(db)
            await ctx.send(f"✅ Secondary color set to `{secondary}`!", ephemeral=True)
        except Exception as e:
            await ctx.send(f"❌ Invalid color format. Use hex like #ff0000", ephemeral=True)

    # --- .TASicon ---
    @commands.command(name='TASicon', description='Update or remove role icon')
    async def tasicon(self, ctx, *, icon: str = None):
        """Update or remove role icon. Usage: .TASicon <emoji> or .TASicon remove"""
        db, file, result = self.get_role_data(ctx)
        if isinstance(result, Embed):
            await ctx.send(embed=result, ephemeral=True)
            return

        role, role_data = result

        if icon and icon.lower() != 'remove':
            try:
                # Check if it's a custom emoji like <:smile:1234567890>
                match = re.match(r"<a?:(\w+):(\d+)>", icon)
                if match:
                    emoji_name, emoji_id = match.groups()
                    emoji_id = int(emoji_id)
                    
                    # Try to get emoji from guild first
                    emoji = discord.utils.get(ctx.guild.emojis, id=emoji_id)
                    if emoji:
                        # Use the emoji directly if it's in the guild
                        emoji_bytes = await emoji.read()
                        await role.edit(display_icon=emoji_bytes)
                        icon_type = "custom"
                        icon_value = str(emoji_id)
                    else:
                        # If emoji not in guild, try to download it
                        emoji_url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{'gif' if icon.startswith('<a:') else 'png'}"
                        try:
                            async with aiohttp.ClientSession() as session:
                                async with session.get(emoji_url) as resp:
                                    if resp.status == 200:
                                        emoji_bytes = await resp.read()
                                        await role.edit(display_icon=emoji_bytes)
                                        icon_type = "custom"
                                        icon_value = str(emoji_id)
                                    else:
                                        raise ValueError("Could not download emoji image")
                        except Exception as e:
                            raise ValueError(f"Custom emoji not found or inaccessible: {e}")
                else:
                    # For unicode emojis, we need to convert them to images
                    # Using Twemoji CDN to convert unicode to image
                    try:
                        # Convert unicode emoji to codepoints
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
                                    # Alternative: use another emoji service
                                    alt_url = f"https://emojiapi.dev/api/v1/{icon}.png"
                                    async with session.get(alt_url) as alt_resp:
                                        if alt_resp.status == 200:
                                            emoji_bytes = await alt_resp.read()
                                            await role.edit(display_icon=emoji_bytes)
                                            icon_type = "unicode"
                                            icon_value = icon
                                        else:
                                            raise ValueError("Unicode emoji could not be converted to image")
                    except Exception as e:
                        raise ValueError(f"Could not set unicode emoji: {e}")

                # Save to database
                role_data["icon_type"] = icon_type
                role_data["icon_value"] = icon_value
                save_db(db)
                
                embed = self.create_success_embed(
                    "✅ Role Icon Updated",
                    f"Your role icon has been updated to: {icon}"
                )
                await ctx.send(embed=embed, ephemeral=True)
                
            except Exception as e:
                embed = self.create_error_embed(
                    f"❌ Failed to set icon: {str(e)}"
                )
                await ctx.send(embed=embed, ephemeral=True)
                
        else: 
            # Remove icon
            if role_data.get("icon_value"): 
                try:
                    await role.edit(display_icon=None)
                    role_data.pop("icon_type", None) 
                    role_data.pop("icon_value", None) 
                    save_db(db)
                    
                    embed = self.create_success_embed(
                        "✅ Role Icon Removed",
                        "Your role icon has been successfully removed."
                    )
                    await ctx.send(embed=embed, ephemeral=True) 
                except Exception as e:
                    embed = self.create_error_embed(
                        f"❌ Failed to remove icon: {str(e)}"
                    )
                    await ctx.send(embed=embed, ephemeral=True)
            else: 
                embed = self.create_info_embed(
                    "ℹ️ No Icon Set",
                    "No role icon is currently set."
                )
                await ctx.send(embed=embed, ephemeral=True)

async def setup(bot):
    await bot.add_cog(RoleUpdate(bot))