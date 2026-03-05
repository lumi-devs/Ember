import os
import asyncio
from typing import Dict, Optional
from datetime import datetime, timezone, timedelta

import discord
from discord import app_commands, Color, Embed, Interaction
from discord.ext import commands

# Updated imports for SQLite database
from .database import get_db  # Adjust import path as needed
from .helpers import (
    validate_role_name,
    ensure_role_position,
    is_user_banned,
    save_role_to_db,
    get_role_from_db,
    delete_role_from_db,
)
from .modals import CreateRoleModal, RoleNameModal, RoleColorsModal
from .views import BoosterRoleView, RenounceConfirmationView
from .embeds import (
    create_progress_embed,
    create_role_creation_success_embed,
    create_booster_roles_embed,
    create_help_embed,
)


class BoosterRolesCog(commands.Cog):
    """Complete booster role management system with old and new features"""

    def __init__(self, bot):
        self.bot = bot
        self.role_position_lock = asyncio.Lock()

    async def send_progress_embed(
        self,
        interaction: Interaction,
        title: str,
        description: str,
        color: Color = Color.blue(),
        status: str = "Processing",
    ) -> Optional[discord.WebhookMessage]:
        """Helper to send progress embeds"""
        embed = await create_progress_embed(title, description, color, status)
        try:
            return await interaction.followup.send(embed=embed, wait=True)
        except:
            return None

    async def process_role_creation(self, interaction: Interaction, name: str):
        """Process role creation with fun progress tracking"""
        # Validate permissions
        if not interaction.guild:
            await interaction.followup.send(
                "❌ This command must be used in a server.", ephemeral=True
            )
            return

        # Restrict to specific channel if configured
        """
        allowed_channel_id = int(os.environ.get("CUSTOM_ROLE_CHANNEL_ID", 0))
        if allowed_channel_id and interaction.channel.id != allowed_channel_id:
            error_channel = interaction.guild.get_channel(allowed_channel_id)
            channel_mention = f"<#{allowed_channel_id}>" if error_channel else "the designated channel"
            await interaction.followup.send(
                f"🚫 This command can only be used in {channel_mention}.",
                ephemeral=True
            )
            return
        """

        # Check booster role
        booster_role_id = int(os.environ.get("BOOSTER_ROLE_ID", 0))
        booster_role = interaction.guild.get_role(booster_role_id)
        if not booster_role:
            await interaction.followup.send(
                "❌ Booster role not configured properly.", ephemeral=True
            )
            return

        if booster_role not in interaction.user.roles:
            await interaction.followup.send(
                "🚫 You need the booster role to create a custom role.", ephemeral=True
            )
            return

        # Check blacklist (SQLite version)
        if await is_user_banned(interaction.user.id):
            await interaction.followup.send(
                "🚫 You are blacklisted from creating custom roles.", ephemeral=True
            )
            return

        # Check if user already has a role (SQLite version)
        existing_role_data = await get_role_from_db(interaction.user.id)
        if existing_role_data:
            role_id = existing_role_data.get("role_id")
            role = interaction.guild.get_role(role_id)
            if role:
                await interaction.followup.send(
                    f"❌ You already have a custom role: {role.mention}", ephemeral=True
                )
                return
            else:
                # Clean up orphaned entry
                await delete_role_from_db(interaction.user.id)

        # Validate role name
        is_valid, validation_msg = await validate_role_name(
            name, interaction.guild, interaction.user.id
        )
        if not is_valid:
            await interaction.followup.send(f"❌ {validation_msg}", ephemeral=True)
            return

        # Send initial progress embed
        progress_msg = await self.send_progress_embed(
            interaction,
            "🎨 Creating Your Custom Role",
            f"**User:** {interaction.user.mention}\n**Requested Name:** `{name}`",
            Color.gold(),
            "Starting creation process...",
        )

        # Fun ASCII art and loading messages
        fun_messages = [
            "📝 Drafting role specifications...",
            "🎨 Mixing just the right colors...",
            "⚡ Charging role energy...",
            "🐱 Taking a quick cat break...",
            "☕ Brewing some coffee for the process...",
            "🎮 Loading awesome features...",
            "✨ Adding sparkles and magic...",
            "🧹 Cleaning up the role canvas...",
            "🎵 Setting the perfect role soundtrack...",
            "🌈 Creating color spectrum...",
        ]

        status_messages = [
            "Initializing role matrix...",
            "Compiling role data...",
            "Configuring role parameters...",
            "Applying aesthetic algorithms...",
            "Polishing role interface...",
            "Finalizing role structure...",
            "Role creation in progress...",
        ]

        try:
            # Step 1: Initializing role creation with fun messages
            if progress_msg:
                for i in range(2):  # Show 2 fun messages
                    embed = progress_msg.embeds[0]
                    embed.set_field_at(
                        0,
                        name="🔄 Status",
                        value=f"```{fun_messages[i % len(fun_messages)]}\n{status_messages[i % len(status_messages)]}```",
                        inline=False,
                    )
                    embed.color = Color.random()
                    await progress_msg.edit(embed=embed)
                    await asyncio.sleep(1.5)  # Small delay for animation effect

            # Step 2: Create role with progress update
            if progress_msg:
                embed = progress_msg.embeds[0]
                embed.set_field_at(
                    0,
                    name="⚙️ Status",
                    value="```🤖 Summoning role from the void...\nCreating role object in Discord...```",
                    inline=False,
                )
                embed.color = Color.purple()
                await progress_msg.edit(embed=embed)
                await asyncio.sleep(1.0)

            role = await interaction.guild.create_role(
                name=name,
                reason=f"Custom booster role for {interaction.user} ({interaction.user.id})",
                color=Color.gold(),
                hoist=False,
                mentionable=False,
            )

            print(
                f"✓ Created role '{name}' (ID: {role.id}) for user {interaction.user.id}"
            )

            # Fun message after creation
            if progress_msg:
                embed = progress_msg.embeds[0]
                embed.set_field_at(
                    0,
                    name="✅ Status",
                    value="```🎉 Role successfully summoned!\nNow positioning it perfectly...```",
                    inline=False,
                )
                embed.color = Color.green()
                await progress_msg.edit(embed=embed)
                await asyncio.sleep(1.0)

            # Step 3: Set position with fun messages
            fixed_role_id = int(os.environ.get("FIXED_ROLE_ID", 0))
            if progress_msg:
                embed = progress_msg.embeds[0]
                embed.set_field_at(
                    0,
                    name="📊 Status",
                    value="```🧭 Finding the perfect spot...\nAdjusting role hierarchy...```",
                    inline=False,
                )
                embed.color = Color.blue()
                await progress_msg.edit(embed=embed)
                await asyncio.sleep(1.0)

            # Show positioning animation
            if progress_msg:
                positions = ["▱▱▱▱▱", "▰▱▱▱▱", "▰▰▱▱▱", "▰▰▰▱▱", "▰▰▰▰▱", "▰▰▰▰▰"]
                for pos in positions:
                    embed = progress_msg.embeds[0]
                    embed.set_field_at(
                        0,
                        name="⚙️ Status",
                        value=f"```{pos} Positioning role... {int((positions.index(pos)/len(positions))*100)}%```",
                        inline=False,
                    )
                    await progress_msg.edit(embed=embed)
                    await asyncio.sleep(0.3)

            position_success = await ensure_role_position(
                interaction.guild, role, fixed_role_id
            )

            if not position_success:
                # Delete role if position failed
                try:
                    await role.delete(reason="Failed to set position")
                except:
                    pass

                if progress_msg:
                    embed = progress_msg.embeds[0]
                    embed.title = "❌ Role Creation Failed"
                    embed.description = (
                        "Failed to set role position. Please contact an administrator."
                    )
                    embed.color = Color.red()
                    embed.set_field_at(
                        0,
                        name="❌ Status",
                        value="```💔 Position setting failed\nCould not place role in hierarchy```",
                        inline=False,
                    )
                    embed.set_image(
                        url="https://media.giphy.com/media/3o7aD2vscU9WE8uVUQ/giphy.gif"
                    )
                    await progress_msg.edit(embed=embed)
                else:
                    await interaction.followup.send(
                        "❌ Failed to set role position. Please contact an administrator.",
                        ephemeral=True,
                    )
                return

            # Step 4: Assign role to user with fun messages
            if progress_msg:
                embed = progress_msg.embeds[0]
                embed.set_field_at(
                    0,
                    name="👤 Status",
                    value=f"```🎁 Wrapping your gift...\nAssigning role to {interaction.user.display_name}...```",
                    inline=False,
                )
                embed.color = Color.magenta()
                await progress_msg.edit(embed=embed)
                await asyncio.sleep(1.0)

            await interaction.user.add_roles(
                role, reason="Assigned custom booster role"
            )

            # Step 5: Save to database (SQLite version)
            if progress_msg:
                embed = progress_msg.embeds[0]
                embed.set_field_at(
                    0,
                    name="💾 Status",
                    value="```📚 Archiving role data...\nSaving to permanent storage...```",
                    inline=False,
                )
                embed.color = Color.dark_green()
                await progress_msg.edit(embed=embed)
                await asyncio.sleep(1.0)

            ist_time = datetime.now(timezone(timedelta(hours=5, minutes=30)))
            role_data = {
                "role_id": role.id,
                "name": name,
                "color": str(role.color),
                "color_type": "solid",
                "secondary_colour": None,
                "icon_type": None,
                "icon_set_at": None,
                "created_at": ist_time.isoformat(),
                "updated_at": ist_time.isoformat(),
            }

            save_success = await save_role_to_db(interaction.user.id, role_data)

            # Step 6: Final celebration!
            if progress_msg:
                embed = progress_msg.embeds[0]
                embed.title = "🎉 Almost There!"
                embed.description = "**Finalizing your masterpiece...**"
                embed.set_field_at(
                    0,
                    name="✨ Status",
                    value="```🎊 Adding finishing touches...\n✨ Sprinkling some magic dust...```",
                    inline=False,
                )
                embed.color = Color.gold()
                await progress_msg.edit(embed=embed)
                await asyncio.sleep(1.5)

            # Final success embed
            embed = await create_role_creation_success_embed(
                interaction, role, fixed_role_id
            )

            # Delete progress message and send final embed
            if progress_msg:
                try:
                    await progress_msg.delete()
                except:
                    pass

            await interaction.followup.send(embed=embed)

            print(
                f"✓ Successfully completed role creation for user {interaction.user.id}"
            )

        except discord.Forbidden as e:
            error_msg = "❌ Bot lacks permissions to manage roles. Please contact an administrator."
            if progress_msg:
                embed = progress_msg.embeds[0]
                embed.title = "❌ Permission Error"
                embed.description = error_msg
                embed.color = Color.red()
                embed.set_field_at(
                    0,
                    name="❌ Status",
                    value="```🔒 Missing permissions\nCannot create or edit roles```",
                    inline=False,
                )
                embed.set_image(
                    url="https://media.giphy.com/media/3o7aD2vscU9WE8uVUQ/giphy.gif"
                )
                await progress_msg.edit(embed=embed)
            else:
                await interaction.followup.send(error_msg, ephemeral=True)
            print(f"✗ Permission error for user {interaction.user.id}: {e}")

        except discord.HTTPException as e:
            error_msg = f"❌ Discord API error: {e}\nPlease try again in a few moments."
            if progress_msg:
                embed = progress_msg.embeds[0]
                embed.title = "❌ API Error"
                embed.description = error_msg
                embed.color = Color.red()
                embed.set_field_at(
                    0,
                    name="❌ Status",
                    value="```🌐 Discord API issue\nRate limit or server error```",
                    inline=False,
                )
                embed.set_image(
                    url="https://media.giphy.com/media/l0HU7JI1CQgk83ley/giphy.gif"
                )
                await progress_msg.edit(embed=embed)
            else:
                await interaction.followup.send(error_msg, ephemeral=True)
            print(f"✗ HTTP error for user {interaction.user.id}: {e}")

        except Exception as e:
            error_msg = (
                "❌ An unexpected error occurred. Please contact an administrator."
            )
            if progress_msg:
                embed = progress_msg.embeds[0]
                embed.title = "❌ Unexpected Error"
                embed.description = error_msg
                embed.color = Color.red()
                embed.set_field_at(
                    0,
                    name="❌ Status",
                    value="```💥 Something went wrong\nUnexpected error occurred```",
                    inline=False,
                )
                embed.set_image(
                    url="https://media.giphy.com/media/3o7aD2vscU9WE8uVUQ/giphy.gif"
                )
                await progress_msg.edit(embed=embed)
            else:
                await interaction.followup.send(error_msg, ephemeral=True)
            print(f"✗ Unexpected error for user {interaction.user.id}: {e}")

    async def send_booster_roles_embed(self, interaction: Interaction):
        """Send the main booster roles management embed"""
        # Check if user is a booster
        booster_role_id = int(os.environ.get("BOOSTER_ROLE_ID", 0))
        booster_role = interaction.guild.get_role(booster_role_id)

        if not booster_role:
            await interaction.followup.send(
                "❌ Booster role not configured on this server.", ephemeral=True
            )
            return

        if booster_role not in interaction.user.roles:
            await interaction.followup.send(
                "🚫 You need to be a server booster to use this feature!",
                ephemeral=True,
            )
            return

        # Check blacklist (SQLite version)
        if await is_user_banned(interaction.user.id):
            await interaction.followup.send(
                "🚫 You are blacklisted from using custom roles.", ephemeral=True
            )
            return

        # Get user's current role data (SQLite version)
        role_data = await get_role_from_db(interaction.user.id)
        role = None

        if role_data:
            role = interaction.guild.get_role(role_data.get("role_id"))
            if not role:
                # Clean up orphaned entry
                await delete_role_from_db(interaction.user.id)
                role_data = None

        # Create main embed (updated function signature)
        embed = await create_booster_roles_embed(interaction, role, role_data)

        # Create and send the view with the embed
        view = BoosterRoleView(self, interaction.user, role, role_data)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    # --- Slash Commands ---
    @app_commands.command(
        name="boosterroles",
        description="Manage your custom booster role with buttons and modals",
    )
    async def boosterroles(self, interaction: Interaction):
        """Main command for booster role management"""
        await interaction.response.defer(ephemeral=True)
        await self.send_booster_roles_embed(interaction)

    @app_commands.command(
        name="renounce", description="Remove a bestowed role from yourself"
    )
    @app_commands.describe(
        role="Role to renounce (optional - shows menu if not specified)"
    )
    async def renounce(
        self, interaction: Interaction, role: Optional[discord.Role] = None
    ):
        """Allow users to remove bestowed roles from themselves"""
        await interaction.response.defer(ephemeral=True)

        if role:
            # Direct role specified
            await self.process_role_renounce(interaction, role)
        else:
            # Show menu of all bestowed roles
            await self.show_renounce_menu(interaction)

    async def process_role_renounce(self, interaction: Interaction, role: discord.Role):
        """Process renouncing a specific role"""
        db = await get_db()

        # Check if this is a booster role
        role_data = await db.get_role_by_role_id(role.id)
        if not role_data:
            await interaction.followup.send(
                f"❌ {role.mention} is not a booster role.", ephemeral=True
            )
            return

        # Check if user has this role (and it's bestowed to them)
        user_has_role = role in interaction.user.roles

        if not user_has_role:
            await interaction.followup.send(
                f"❌ You don't have the {role.mention} role.", ephemeral=True
            )
            return

        # Check if it's a bestowed role (not their own role)
        if role_data["user_id"] == interaction.user.id:
            await interaction.followup.send(
                f"❌ {role.mention} is your own role. Use `/tasdelete` to delete it instead.",
                ephemeral=True,
            )
            return

        # Check if there's a bestowed entry
        bestowed_entry = await db.get_bestowed(
            role_data["user_id"], interaction.user.id
        )
        if not bestowed_entry or not bestowed_entry.get("accepted"):
            await interaction.followup.send(
                f"❌ {role.mention} was not officially bestowed to you.", ephemeral=True
            )
            return

        # Show confirmation
        await self.show_renounce_confirmation(interaction, role, role_data)

    async def show_renounce_menu(self, interaction: Interaction):
        """Show menu of all bestowed roles user can renounce"""
        db = await get_db()

        # Get all roles user has
        user_roles = interaction.user.roles

        # Filter to only booster roles that are bestowed (not user's own)
        bestowed_roles = []

        for role in user_roles:
            role_data = await db.get_role_by_role_id(role.id)
            if role_data and role_data["user_id"] != interaction.user.id:
                # Check if it's actually bestowed
                bestowed_entry = await db.get_bestowed(
                    role_data["user_id"], interaction.user.id
                )
                if bestowed_entry and bestowed_entry.get("accepted"):
                    bestowed_roles.append(
                        {
                            "role": role,
                            "role_data": role_data,
                            "owner_id": role_data["user_id"],
                        }
                    )

        if not bestowed_roles:
            embed = Embed(
                title="📭 No Bestowed Roles",
                description="You don't have any bestowed roles to renounce.",
                color=Color.blue(),
            )
            embed.add_field(
                name="What are bestowed roles?",
                value="These are roles gifted to you by other boosters using the **Bestow Role** feature.",
                inline=False,
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        # Create selection menu
        if len(bestowed_roles) == 1:
            # Auto-select if only one role
            role_info = bestowed_roles[0]
            await self.show_renounce_confirmation(
                interaction, role_info["role"], role_info["role_data"]
            )
        else:
            # Show selection menu
            embed = Embed(
                title="🎭 Select Role to Renounce",
                description="Choose which bestowed role you want to remove:",
                color=Color.orange(),
            )

            for i, role_info in enumerate(bestowed_roles, 1):
                role = role_info["role"]
                owner = interaction.guild.get_member(role_info["owner_id"])
                owner_name = owner.display_name if owner else "Unknown"

                embed.add_field(
                    name=f"{i}. {role.name}",
                    value=f"Bestowed by: {owner_name}\nColor: `{role.color}`",
                    inline=False,
                )

            embed.set_footer(text="Type the number of the role you want to renounce.")

            # Send embed and wait for response
            await interaction.followup.send(embed=embed, ephemeral=True)

            # Wait for user to type a number
            def check(m):
                return (
                    m.author.id == interaction.user.id
                    and m.channel.id == interaction.channel.id
                    and m.content.isdigit()
                    and 1 <= int(m.content) <= len(bestowed_roles)
                )

            try:
                msg = await self.bot.wait_for("message", timeout=60.0, check=check)
                selection = int(msg.content) - 1

                # Clean up the message
                try:
                    await msg.delete()
                except:
                    pass

                # Process selection
                role_info = bestowed_roles[selection]
                await self.show_renounce_confirmation(
                    interaction, role_info["role"], role_info["role_data"]
                )

            except asyncio.TimeoutError:
                await interaction.followup.send(
                    "⏰ Selection timed out. Use `/renounce @role` to specify a role directly.",
                    ephemeral=True,
                )

    async def show_renounce_confirmation(
        self, interaction: Interaction, role: discord.Role, role_data: Dict
    ):
        """Show confirmation for renouncing a role"""
        owner = interaction.guild.get_member(role_data["user_id"])

        embed = Embed(
            title="⚠️ Renounce Role",
            description=f"Are you sure you want to renounce **{role.name}**?",
            color=Color.orange(),
        )

        embed.add_field(name="Role", value=role.mention, inline=True)
        embed.add_field(name="Color", value=f"`{role.color}`", inline=True)

        if owner:
            embed.add_field(name="Bestowed By", value=owner.mention, inline=True)

        embed.add_field(
            name="Effects",
            value=(
                "• Role will be removed from you\n"
                "• You will lose all role permissions\n"
                "• Owner will be notified\n"
                "• You can be invited again later"
            ),
            inline=False,
        )

        view = RenounceConfirmationView(role, role_data["user_id"])
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    @app_commands.command(
        name="tasdelete", description="Delete your custom role (mods can delete others)"
    )
    @app_commands.describe(
        user="Target user (mods only)", reason="Reason for deletion (mods only)"
    )
    async def tasdelete(
        self,
        interaction: Interaction,
        user: Optional[discord.Member] = None,
        reason: Optional[str] = None,
    ):
        """Traditional delete command with mod support"""
        await interaction.response.defer(ephemeral=True)

        # Check blacklist (SQLite version)
        if await is_user_banned(interaction.user.id):
            await interaction.followup.send(
                "🚫 You are blacklisted and cannot use this command.", ephemeral=True
            )
            return

        # Determine target user
        target_user = interaction.user
        is_mod_action = False

        if user:
            # Check if user is mod
            mod_ids = [
                int(x) for x in os.environ.get("MOD_IDS", "").split(",") if x.strip()
            ]
            if interaction.user.id not in mod_ids:
                await interaction.followup.send(
                    "🚫 You are not allowed to delete other users' roles.",
                    ephemeral=True,
                )
                return
            target_user = user
            is_mod_action = True

        # Check if target has a role (SQLite version)
        role_data = await get_role_from_db(target_user.id)
        if not role_data:
            await interaction.followup.send(
                f"❌ {target_user.mention} does not have a custom role.", ephemeral=True
            )
            return

        # Get role object
        role = interaction.guild.get_role(role_data.get("role_id"))
        if not role:
            # Clean up orphaned entry
            await delete_role_from_db(target_user.id)
            await interaction.followup.send(
                f"❌ Role not found. Database entry has been cleaned up.",
                ephemeral=True,
            )
            return

        # Create confirmation view (you'll need to update this view too)
        from .views import DeleteConfirmationView

        confirm_view = DeleteConfirmationView(self, role, is_mod_action, reason)

        embed = Embed(title="⚠️ Confirm Role Deletion", color=Color.red())

        if is_mod_action:
            embed.description = f"**Moderator:** {interaction.user.mention}\n**Target:** {target_user.mention}\n**Role:** {role.mention}"
            if reason:
                embed.add_field(name="Reason", value=reason, inline=False)
        else:
            embed.description = (
                f"**User:** {interaction.user.mention}\n**Role:** {role.mention}"
            )

        embed.add_field(
            name="Warning",
            value="This action cannot be undone! The role will be permanently deleted.",
            inline=False,
        )

        await interaction.followup.send(embed=embed, view=confirm_view, ephemeral=True)

    @app_commands.command(
        name="tasblacklist", description="Manage the custom role blacklist (mods only)"
    )
    @app_commands.describe(
        action="Action to perform",
        user="User to add/remove",
        reason="Reason for blacklisting (optional)",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="add", value="add"),
            app_commands.Choice(name="remove", value="remove"),
            app_commands.Choice(name="list", value="list"),
            app_commands.Choice(name="check", value="check"),
        ]
    )
    async def tasblacklist(
        self,
        interaction: Interaction,
        action: str,
        user: Optional[discord.User] = None,
        reason: Optional[str] = None,
    ):
        """Blacklist management command"""
        await interaction.response.defer(ephemeral=True)

        # Check mod permissions
        mod_ids = [
            int(x) for x in os.environ.get("MOD_IDS", "").split(",") if x.strip()
        ]
        if interaction.user.id not in mod_ids:
            await interaction.followup.send(
                "🚫 You are not authorized to use this command.", ephemeral=True
            )
            return

        db = await get_db()

        if action == "list":
            blacklist = await db.get_blacklist()
            if not blacklist:
                await interaction.followup.send(
                    "📜 The blacklist is currently empty.", ephemeral=True
                )
                return

            embed = Embed(
                title="📜 Blacklisted Users",
                description=f"**Total:** {len(blacklist)} users",
                color=Color.orange(),
            )

            user_list = []
            for user_id in blacklist[:10]:  # Show first 10
                user_obj = self.bot.get_user(user_id)
                if user_obj:
                    user_list.append(f"• {user_obj.mention} (`{user_id}`)")
                else:
                    user_list.append(f"• `{user_id}`")

            embed.description += "\n\n" + "\n".join(user_list)

            if len(blacklist) > 10:
                embed.set_footer(text=f"...and {len(blacklist) - 10} more users")

            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        if action == "check":
            if not user:
                await interaction.followup.send(
                    "❌ Please specify a user to check.", ephemeral=True
                )
                return

            is_blacklisted = await db.is_user_banned(user.id)
            embed = Embed(
                title=f"Blacklist Status: {user}",
                color=Color.red() if is_blacklisted else Color.green(),
            )

            if is_blacklisted:
                embed.description = "✅ **BLACKLISTED**\nThis user cannot create or update custom roles."
            else:
                embed.description = "❌ **NOT BLACKLISTED**\nThis user can create and manage custom roles."

            embed.add_field(name="User ID", value=f"`{user.id}`", inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        if not user:
            await interaction.followup.send(
                "❌ Please specify a user for this action.", ephemeral=True
            )
            return

        if action == "add":
            is_blacklisted = await db.is_user_banned(user.id)
            if is_blacklisted:
                await interaction.followup.send(
                    f"⚠️ {user.mention} is already blacklisted.", ephemeral=True
                )
                return

            success = await db.add_to_blacklist(user.id)
            if not success:
                await interaction.followup.send(
                    f"❌ Failed to add {user.mention} to blacklist.", ephemeral=True
                )
                return

            embed = Embed(
                title="✅ User Blacklisted",
                description=f"{user.mention} has been added to the blacklist.",
                color=Color.red(),
            )
            embed.add_field(
                name="Blacklisted By", value=interaction.user.mention, inline=True
            )
            if reason:
                embed.add_field(name="Reason", value=reason, inline=False)

            await interaction.followup.send(embed=embed, ephemeral=True)

        elif action == "remove":
            is_blacklisted = await db.is_user_banned(user.id)
            if not is_blacklisted:
                await interaction.followup.send(
                    f"⚠️ {user.mention} is not in the blacklist.", ephemeral=True
                )
                return

            success = await db.remove_from_blacklist(user.id)
            if not success:
                await interaction.followup.send(
                    f"❌ Failed to remove {user.mention} from blacklist.",
                    ephemeral=True,
                )
                return

            embed = Embed(
                title="✅ User Removed from Blacklist",
                description=f"{user.mention} has been removed from the blacklist.",
                color=Color.green(),
            )
            embed.add_field(
                name="Removed By", value=interaction.user.mention, inline=True
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

        else:
            await interaction.followup.send(
                "❌ Invalid action. Use add, remove, list, or check.", ephemeral=True
            )

    @app_commands.command(
        name="tashelp", description="Show help for booster role commands"
    )
    async def tashelp(self, interaction: Interaction):
        """Help command for booster role system"""
        embed = await create_help_embed()
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(BoosterRolesCog(bot))
