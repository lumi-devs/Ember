import discord
from discord import ui, TextStyle, Interaction, Color
from typing import Dict, Any
from datetime import datetime, timezone, timedelta
import io
from PIL import Image, ImageDraw

# Updated imports for SQLite database
from .database import get_db  # Adjust import path as needed
from .helpers import validate_role_name, validate_color


class CreateRoleModal(ui.Modal, title="Create Booster Role"):
    """Modal for creating a new booster role"""

    role_name = ui.TextInput(
        label="Role Name",
        placeholder="Enter a name for your custom role",
        min_length=2,
        max_length=100,
        style=TextStyle.short,
        required=True,
    )

    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.cog.process_role_creation(interaction, str(self.role_name))


class RoleNameModal(ui.Modal, title="Update Role Name"):
    """Modal for updating role name"""

    name_input = ui.TextInput(
        label="New Role Name",
        placeholder="Enter new name (2-100 characters)",
        min_length=2,
        max_length=100,
        style=TextStyle.short,
        required=True,
    )

    def __init__(self, role: discord.Role):
        super().__init__()
        self.role = role
        self.name_input.default = role.name

    async def on_submit(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)

        new_name = str(self.name_input)

        # Cooldown check: max 3 name changes per hour
        db = await get_db()
        allowed, retry_after = await db.check_cooldown(
            interaction.user.id, "rename", limit=3, window_seconds=3600
        )
        if not allowed:
            minutes = retry_after // 60
            seconds = retry_after % 60
            await interaction.followup.send(
                f"⏳ **Slow down!** You can only rename your role 3 times per hour.\n"
                f"Try again in **{minutes}m {seconds}s**.",
                ephemeral=True,
            )
            return

        # Validate name
        is_valid, validation_msg = await validate_role_name(
            new_name, interaction.guild, interaction.user.id
        )
        if not is_valid:
            await interaction.followup.send(f"❌ {validation_msg}", ephemeral=True)
            return

        try:
            await self.role.edit(name=new_name)

            # Update database (SQLite version)
            db = await get_db()
            role_data = await db.get_role(interaction.user.id)

            if role_data:
                old_name = role_data.get("name", "")
                # Update existing role data
                role_data["name"] = new_name
                role_data["updated_at"] = datetime.now(
                    timezone(timedelta(hours=5, minutes=30))
                ).isoformat()

                # Save updated data
                await db.save_role(interaction.user.id, role_data)

                # Audit log
                await db.log_action(interaction.user.id, "rename", {
                    "old_name": old_name,
                    "new_name": new_name,
                    "role_id": self.role.id,
                })

            embed = discord.Embed(
                title="✅ Role Name Updated",
                description=f"Changed to: **{new_name}**",
                color=Color.green(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

        except discord.Forbidden:
            await interaction.followup.send(
                "❌ Bot lacks permissions to edit roles.", ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(
                f"❌ Failed to update name: {e}", ephemeral=True
            )


class RoleColorsModal(ui.Modal, title="Update Role Colors"):
    """Modal for updating primary and secondary colors"""

    primary_color = ui.TextInput(
        label="Primary Color (Hex)",
        placeholder="#FF0000 or FF0000",
        max_length=9,
        style=TextStyle.short,
        required=True,
    )

    secondary_color = ui.TextInput(
        label="Secondary Color (Hex) - Optional",
        placeholder="#00FF00 or leave empty for solid",
        max_length=9,
        style=TextStyle.short,
        required=False,
    )

    def __init__(self, role: discord.Role, role_data: Dict[str, Any]):
        super().__init__()
        self.role = role
        self.role_data = role_data

        # Set current values as defaults
        if role_data and "color" in role_data:
            self.primary_color.default = role_data["color"]
        if role_data and "secondary_colour" in role_data:
            self.secondary_color.default = role_data["secondary_colour"]

    def hex_to_rgb(self, hex_color: str) -> tuple:
        """Convert hex color to RGB tuple"""
        hex_color = hex_color.lstrip("#")
        # If it's #000001, it will become '000001' which is valid
        if len(hex_color) == 3:
            hex_color = "".join([c * 2 for c in hex_color])
        # Handle case where someone might still try to use 000000
        if hex_color == "000000":
            hex_color = "000001"
        return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))

    def create_solid_color_image(
        self, hex_color: str, size: tuple = (200, 100)
    ) -> io.BytesIO:
        """Create a solid color image"""
        rgb = self.hex_to_rgb(hex_color)
        img = Image.new("RGB", size, rgb)

        # Convert to bytes
        img_bytes = io.BytesIO()
        img.save(img_bytes, format="PNG")
        img_bytes.seek(0)
        return img_bytes

    def create_gradient_image(
        self, primary_hex: str, secondary_hex: str, size: tuple = (200, 100)
    ) -> io.BytesIO:
        """Create a smooth gradient blend between two colors using PIL composite method"""
        primary_rgb = self.hex_to_rgb(primary_hex)
        secondary_rgb = self.hex_to_rgb(secondary_hex)

        # Create gradient using composite method
        base = Image.new("RGB", size, primary_rgb)
        top = Image.new("RGB", size, secondary_rgb)
        mask = Image.new("L", size)

        width, height = size

        # Create vertical gradient mask
        for y in range(height):
            mask_value = int(255 * (y / max(height - 1, 1)))  # Avoid division by zero
            for x in range(width):
                mask.putpixel((x, y), mask_value)

        # Create the gradient image
        img = Image.composite(top, base, mask)

        # Optional: Rotate 90 degrees for horizontal gradient if preferred
        # Uncomment the next line if you want horizontal gradient instead of vertical
        img = img.rotate(90, expand=True)

        # Convert to bytes
        img_bytes = io.BytesIO()
        img.save(img_bytes, format="PNG")
        img_bytes.seek(0)
        return img_bytes

    def create_color_swatch(self, hex_color: str, size: tuple = (50, 50)) -> io.BytesIO:
        """Create a small color swatch for thumbnail"""
        rgb = self.hex_to_rgb(hex_color)
        img = Image.new("RGB", size, rgb)

        # Add border
        draw = ImageDraw.Draw(img)
        draw.rectangle(
            [(0, 0), (size[0] - 1, size[1] - 1)], outline=(255, 255, 255), width=2
        )

        img_bytes = io.BytesIO()
        img.save(img_bytes, format="PNG")
        img_bytes.seek(0)
        return img_bytes

    async def on_submit(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)

        primary_hex = str(self.primary_color).strip()
        secondary_hex = (
            str(self.secondary_color).strip() if self.secondary_color.value else None
        )

        # Cooldown check: max 5 color changes per hour
        db_check = await get_db()
        allowed, retry_after = await db_check.check_cooldown(
            interaction.user.id, "recolor", limit=5, window_seconds=3600
        )
        if not allowed:
            minutes = retry_after // 60
            seconds = retry_after % 60
            await interaction.followup.send(
                f"⏳ **Slow down!** You can only change colors 5 times per hour.\n"
                f"Try again in **{minutes}m {seconds}s**.",
                ephemeral=True,
            )
            return

        # Validate colors
        if primary_hex in ["#000000", "000000"]:
            primary_hex = "#000001"

        primary_valid, primary_color_obj = await validate_color(primary_hex)
        if not primary_valid:
            await interaction.followup.send(
                "❌ Invalid primary color format. Use hex like #FF0000", ephemeral=True
            )
            return

        secondary_color_obj = None
        if secondary_hex:
            # Also check secondary color for pure black
            if secondary_hex in ["#000000", "000000"]:
                secondary_hex = "#000001"

            secondary_valid, secondary_color_obj = await validate_color(secondary_hex)
            if not secondary_valid:
                await interaction.followup.send(
                    "❌ Invalid secondary color format. Use hex like #00FF00",
                    ephemeral=True,
                )
                return

        try:
            if secondary_hex:
                # Apply gradient
                await self.role.edit(
                    color=primary_color_obj, secondary_color=secondary_color_obj
                )
                color_type = "gradient"
                color_desc = (
                    f"**Primary:** `{primary_hex}` → **Secondary:** `{secondary_hex}`"
                )

                # Create gradient image
                gradient_image = self.create_gradient_image(
                    primary_hex, secondary_hex, size=(300, 150)
                )

            else:
                # Apply solid color
                await self.role.edit(color=primary_color_obj)
                color_type = "solid"
                color_desc = f"**Solid Color:** `{primary_hex}`"

                # Create solid color image
                gradient_image = self.create_solid_color_image(
                    primary_hex, size=(300, 150)
                )

            # Update database (SQLite version)
            db = await get_db()
            role_data = await db.get_role(interaction.user.id)

            if role_data:
                old_color = role_data.get("color", "")
                old_secondary = role_data.get("secondary_colour")
                # Update color data
                role_data["color"] = primary_hex
                if secondary_hex:
                    role_data["secondary_colour"] = secondary_hex
                    role_data["color_type"] = "gradient"
                else:
                    # Remove secondary color if switching from gradient to solid
                    role_data.pop("secondary_colour", None)
                    role_data["color_type"] = "solid"

                role_data["updated_at"] = datetime.now(
                    timezone(timedelta(hours=5, minutes=30))
                ).isoformat()

                # Save updated data
                await db.save_role(interaction.user.id, role_data)

                # Audit log
                await db.log_action(interaction.user.id, "recolor", {
                    "old_color": old_color,
                    "new_color": primary_hex,
                    "old_secondary": old_secondary,
                    "new_secondary": secondary_hex,
                    "color_type": color_type,
                    "role_id": self.role.id,
                })

            # Create embed
            embed = discord.Embed(
                title="✅ Role Colors Updated",
                description=color_desc,
                color=primary_color_obj,
                timestamp=datetime.now(),
            )

            embed.add_field(name="🎨 Type", value=color_type.title(), inline=True)
            embed.add_field(name="👤 Role", value=self.role.mention, inline=True)
            embed.add_field(
                name="🔄 Updated",
                value=f"<t:{int(datetime.now().timestamp())}:R>",
                inline=True,
            )

            # Add helpful tips
            if color_type == "gradient":
                embed.set_footer(
                    text="Gradient colors will display beautifully in user profiles! 🎨"
                )
            else:
                embed.set_footer(
                    text="Solid color applied. Try adding a secondary color for a gradient effect! ✨"
                )

            # Send the embed with images
            files = []

            # Attach gradient/solid image
            gradient_file = discord.File(gradient_image, filename="color_preview.png")
            embed.set_image(url="attachment://color_preview.png")
            files.append(gradient_file)

            await interaction.followup.send(embed=embed, files=files, ephemeral=True)

        except discord.Forbidden:
            await interaction.followup.send(
                "❌ Bot lacks permissions to edit roles.", ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(
                f"❌ Failed to update colors: {str(e)[:100]}...", ephemeral=True
            )