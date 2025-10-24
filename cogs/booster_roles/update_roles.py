import os
import discord
from discord.ext import commands
from discord import Color, Embed
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
            return None, None, self.create_error_embed("🚫 You are blacklisted and cannot use this command.")
        role_data = db["roles"].get(str(ctx.author.id))
        if not role_data:
            return None, None, self.create_error_embed("❌ You don't have a role.")
        role = ctx.guild.get_role(role_data["role_id"])
        if not role:
            return None, None, self.create_error_embed("⚠️ Role not found.")
        return db, file, (role, role_data)

    def create_success_embed(self, title, description):
        """Create a success embed"""
        embed = Embed(
            title=title,
            description=description,
            color=Color.green()
        )
        return embed

    def create_error_embed(self, description):
        """Create an error embed"""
        embed = Embed(
            title="❌ Error",
            description=description,
            color=Color.red()
        )
        return embed

    def create_info_embed(self, title, description):
        """Create an info embed"""
        embed = Embed(
            title=title,
            description=description,
            color=Color.orange()
        )
        return embed

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
        if isinstance(result, Embed):
            await ctx.send(embed=result, ephemeral=True)
            return
        role, role_data = result
        
        await role.edit(name=new_name)
        role_data["name"] = new_name
        save_db(db)
        
        embed = self.create_success_embed(
            "✅ Role Name Updated",
            f"Your role name has been updated to **{new_name}**!"
        )
        await ctx.send(embed=embed, ephemeral=True)

    # --- .TASprimary ---
    @commands.command(name='TASprimary', description='Update your role primary color')
    async def tasprimary(self, ctx, color: str):
        """Update your role primary color. Usage: .TASprimary #hexcolor"""
        db, file, result = self.get_role_data(ctx)
        if isinstance(result, Embed):
            await ctx.send(embed=result, ephemeral=True)
            return
        role, role_data = result
        
        try:
            primary = Color.from_str(color)
            await role.edit(colour=primary)
            role_data["color"] = color
            role_data["color_type"] = "normal"  # Reset to normal when setting primary
            save_db(db)
            
            embed = self.create_success_embed(
                "✅ Primary Color Updated",
                f"Your primary color has been updated to `{color}`!"
            )
            await ctx.send(embed=embed, ephemeral=True)
        except Exception as e:
            embed = self.create_error_embed(
                "❌ Invalid color format. Use hex like `#ff0000`"
            )
            await ctx.send(embed=embed, ephemeral=True)

    # --- .TASsecondary ---
    @commands.command(name='TASsecondary', description='Update or remove secondary color')
    async def tassecondary(self, ctx, secondary: str = None):
        """Update or remove secondary color. Usage: .TASsecondary [#hexcolor] or .TASsecondary remove"""
        db, file, result = self.get_role_data(ctx)
        if isinstance(result, Embed):
            await ctx.send(embed=result, ephemeral=True)
            return
        
        role, role_data = result
        
        # Check if primary color exists
        primary_hex = role_data.get("color")
        if not primary_hex:
            embed = self.create_info_embed(
                "⚠️ Set Primary Color First",
                "Please set a primary color first using `.TASprimary`."
            )
            await ctx.send(embed=embed, ephemeral=True)
            return
        
        # Remove secondary color
        if secondary is None or secondary.lower() == 'remove':
            if role_data.get("secondary_colour"):
                role_data.pop("secondary_colour", None)
                role_data["color_type"] = "normal"
                save_db(db)
                
                # Get primary color object for role edit
                try:
                    primary_color = Color.from_str(primary_hex)
                    await role.edit(color=primary_color)
                except Exception as e:
                    print(f"Error updating role color: {e}")
                
                embed = self.create_success_embed(
                    "✅ Secondary Color Removed",
                    "Your secondary color has been removed. Role is now primary only."
                )
                await ctx.send(embed=embed, ephemeral=True)
            else:
                embed = self.create_info_embed(
                    "ℹ️ No Secondary Color",
                    "No secondary color is currently set."
                )
                await ctx.send(embed=embed, ephemeral=True)
            return

        # Set secondary color
        try:
            primary_color = Color.from_str(primary_hex)
            secondary_color = Color.from_str(secondary)
            
            # Update role with both colors
            await role.edit(color=primary_color)
            
            role_data["secondary_colour"] = secondary
            role_data["color_type"] = "gradient"
            save_db(db)
            
            embed = self.create_success_embed(
                "✅ Secondary Color Set",
                f"Your secondary color has been set to `{secondary}`!\n"
                f"*Note: While Discord doesn't display gradient roles, both colors are saved in our system.*"
            )
            await ctx.send(embed=embed, ephemeral=True)
        except Exception as e:
            embed = self.create_error_embed(
                "❌ Invalid color format. Use hex like `#ff0000`"
            )
            await ctx.send(embed=embed, ephemeral=True)
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
                icon_type, icon_value = await self.set_role_icon(role, icon)
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
                await role.edit(unicode_emoji=None, display_icon=None)
                role_data.pop("icon_type", None) 
                role_data.pop("icon_value", None) 
                save_db(db)
                
                embed = self.create_success_embed(
                    "✅ Role Icon Removed",
                    "Your role icon has been removed."
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