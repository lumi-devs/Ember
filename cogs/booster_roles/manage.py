from pathlib import Path
import discord
from discord.ext import commands
from discord import app_commands
import asyncio
from typing import Optional
from datetime import datetime, timezone, timedelta

# Import your database module
from .database import get_db


class BoosterRolesManage(commands.Cog):
    """Admin-only commands for managing booster roles"""

    def __init__(self, bot):
        self.bot = bot
        self.db = None

    async def cog_load(self):
        """Initialize database connection when cog loads"""
        self.db = await get_db()

    async def cog_unload(self):
        """Close database connection when cog unloads"""
        if self.db:
            await self.db.close()

    # Helper methods for formatting
    def format_role_info(self, role_data: dict) -> discord.Embed:
        """Format role information into an embed"""
        embed = discord.Embed(
            title=f"Role Information: {role_data['name']}",
            color=(
                discord.Color(int(role_data["color"].replace("#", ""), 16))
                if role_data["color"].startswith("#")
                else discord.Color.blue()
            ),
            timestamp=datetime.fromisoformat(role_data["created_at"]),
        )

        embed.add_field(name="Owner ID", value=f"`{role_data['user_id']}`", inline=True)
        embed.add_field(name="Role ID", value=f"`{role_data['role_id']}`", inline=True)
        embed.add_field(name="Color", value=f"`{role_data['color']}`", inline=True)
        embed.add_field(
            name="Color Type", value=role_data["color_type"].title(), inline=True
        )

        if role_data["secondary_colour"]:
            embed.add_field(
                name="Secondary Color",
                value=f"`{role_data['secondary_colour']}`",
                inline=True,
            )

        if role_data["icon_type"]:
            embed.add_field(name="Icon Type", value=role_data["icon_type"], inline=True)
            if role_data["icon_set_at"]:
                icon_time = datetime.fromisoformat(role_data["icon_set_at"])
                embed.add_field(
                    name="Icon Set At",
                    value=f"<t:{int(icon_time.timestamp())}:R>",
                    inline=True,
                )

        created_at = datetime.fromisoformat(role_data["created_at"])
        updated_at = datetime.fromisoformat(role_data["updated_at"])

        embed.add_field(
            name="Created", value=f"<t:{int(created_at.timestamp())}:R>", inline=True
        )
        embed.add_field(
            name="Updated", value=f"<t:{int(updated_at.timestamp())}:R>", inline=True
        )

        return embed

    def format_bestowed_info(
        self, bestowed_data: dict, role_data: dict = None
    ) -> discord.Embed:
        """Format bestowed information into an embed"""
        embed = discord.Embed(
            title="Bestowed Information", color=discord.Color.purple()
        )

        embed.add_field(
            name="Owner ID", value=f"`{bestowed_data['owner_id']}`", inline=True
        )
        embed.add_field(
            name="User ID", value=f"`{bestowed_data['user_id']}`", inline=True
        )
        embed.add_field(
            name="Role ID", value=f"`{bestowed_data['role_id']}`", inline=True
        )

        if role_data:
            embed.add_field(name="Role Name", value=role_data["name"], inline=True)
            embed.color = (
                discord.Color(int(role_data["color"].replace("#", ""), 16))
                if role_data["color"].startswith("#")
                else discord.Color.blue()
            )

        invited_at = datetime.fromisoformat(bestowed_data["invited_at"])
        embed.add_field(
            name="Invited", value=f"<t:{int(invited_at.timestamp())}:R>", inline=True
        )

        embed.add_field(
            name="Accepted",
            value="✅ Yes" if bestowed_data["accepted"] else "❌ No",
            inline=True,
        )

        if bestowed_data["accepted"] and bestowed_data["accepted_at"]:
            accepted_at = datetime.fromisoformat(bestowed_data["accepted_at"])
            embed.add_field(
                name="Accepted At",
                value=f"<t:{int(accepted_at.timestamp())}:R>",
                inline=True,
            )

        return embed

    # Command group definition
    broles = app_commands.Group(
        name="broles",
        description="Admin commands for managing booster roles",
        default_permissions=discord.Permissions(administrator=True),
    )

    # ===== STATS COMMAND =====
    @broles.command(name="stats", description="Show database statistics")
    async def booster_stats(self, interaction: discord.Interaction):
        """Show database statistics"""
        await interaction.response.defer(thinking=True)

        stats = await self.db.get_stats()

        embed = discord.Embed(
            title="📊 Booster Roles Statistics",
            color=discord.Color.green(),
            timestamp=datetime.now(),
        )

        embed.add_field(
            name="Total Roles", value=f"`{stats['total_roles']}`", inline=True
        )
        embed.add_field(
            name="Total Bestowed", value=f"`{stats['total_bestowed']}`", inline=True
        )
        embed.add_field(
            name="Accepted Bestowed",
            value=f"`{stats['accepted_bestowed']}`",
            inline=True,
        )
        embed.add_field(
            name="Blacklisted Users",
            value=f"`{stats['blacklisted_users']}`",
            inline=True,
        )

        # Add some additional info
        all_roles = await self.db.get_all_roles()
        if all_roles:
            recent_role = max(
                all_roles, key=lambda x: datetime.fromisoformat(x["updated_at"])
            )
            recent_time = datetime.fromisoformat(recent_role["updated_at"])
            embed.add_field(
                name="Most Recent Update",
                value=f"<t:{int(recent_time.timestamp())}:R>",
                inline=True,
            )

        embed.set_footer(text=f"Requested by {interaction.user}")

        await interaction.followup.send(embed=embed)

    # ===== LIST COMMAND =====
    @broles.command(name="list", description="List all booster roles")
    async def list_roles(
        self, interaction: discord.Interaction, page: Optional[int] = 1
    ):
        """List all booster roles with pagination"""
        await interaction.response.defer(thinking=True)

        all_roles = await self.db.get_all_roles()

        if not all_roles:
            await interaction.followup.send("No booster roles found in the database.")
            return

        # Sort by creation date (newest first)
        all_roles.sort(
            key=lambda x: datetime.fromisoformat(x["created_at"]), reverse=True
        )

        # Pagination
        items_per_page = 10
        total_pages = (len(all_roles) + items_per_page - 1) // items_per_page
        page = max(1, min(page, total_pages))

        start_idx = (page - 1) * items_per_page
        end_idx = min(start_idx + items_per_page, len(all_roles))
        page_roles = all_roles[start_idx:end_idx]

        embed = discord.Embed(
            title=f"📋 Booster Roles (Page {page}/{total_pages})",
            description=f"Total roles: {len(all_roles)}",
            color=discord.Color.blue(),
        )

        for role in page_roles:
            created_at = datetime.fromisoformat(role["created_at"])
            field_value = (
                f"**Role ID:** `{role['role_id']}`\n"
                f"**Owner:** `{role['user_id']}`\n"
                f"**Color:** `{role['color']}`\n"
                f"**Created:** <t:{int(created_at.timestamp())}:R>\n"
            )
            embed.add_field(name=f"🏷️ {role['name']}", value=field_value, inline=False)

        embed.set_footer(text="Use /broles info for detailed information")

        await interaction.followup.send(embed=embed)

    # ===== INFO COMMAND =====
    @broles.command(name="info", description="Get detailed information about a role")
    async def role_info(
        self,
        interaction: discord.Interaction,
        user_id: Optional[str] = None,
        role_id: Optional[str] = None,
    ):
        """Get detailed information about a role"""
        await interaction.response.defer(thinking=True)

        if not user_id and not role_id:
            await interaction.followup.send(
                "Please provide either a user_id or role_id."
            )
            return

        role_data = None

        if user_id:
            try:
                user_id_int = int(user_id)
                role_data = await self.db.get_role(user_id_int)
            except ValueError:
                await interaction.followup.send("Invalid user ID format.")
                return

        if not role_data and role_id:
            try:
                role_id_int = int(role_id)
                role_data = await self.db.get_role_by_role_id(role_id_int)
            except ValueError:
                await interaction.followup.send("Invalid role ID format.")
                return

        if not role_data:
            await interaction.followup.send("Role not found.")
            return

        # Get bestowed information for this role
        bestowed_list = []
        conn = await self.db.get_connection()
        async with conn.execute(
            "SELECT * FROM bestowed WHERE role_id = ?", (role_data["role_id"],)
        ) as cursor:
            rows = await cursor.fetchall()
            bestowed_list = [dict(row) for row in rows]

        embed = self.format_role_info(role_data)

        # Add bestowed count
        total_bestowed = len(bestowed_list)
        accepted_bestowed = len([b for b in bestowed_list if b["accepted"]])

        embed.add_field(
            name="Bestowed Statistics",
            value=f"Total: `{total_bestowed}`\nAccepted: `{accepted_bestowed}`\nPending: `{total_bestowed - accepted_bestowed}`",
            inline=False,
        )

        # Show top 5 bestowed users if any
        if bestowed_list:
            top_users = []
            for bestowed in bestowed_list[:5]:
                status = "✅" if bestowed["accepted"] else "⏳"
                top_users.append(
                    f"{status} <@{bestowed['user_id']}> (`{bestowed['user_id']}`)"
                )

            if top_users:
                embed.add_field(
                    name="Recent Bestowed", value="\n".join(top_users), inline=False
                )

        await interaction.followup.send(embed=embed)

    # ===== SEARCH COMMAND =====
    @broles.command(name="search", description="Search for roles by name or color")
    async def search_roles(self, interaction: discord.Interaction, query: str):
        """Search for roles by name or color"""
        await interaction.response.defer(thinking=True)

        all_roles = await self.db.get_all_roles()

        if not all_roles:
            await interaction.followup.send("No booster roles found in the database.")
            return

        # Case-insensitive search
        query_lower = query.lower()
        results = []

        for role in all_roles:
            if (
                query_lower in role["name"].lower()
                or query_lower in role["color"].lower()
                or (
                    role["secondary_colour"]
                    and query_lower in role["secondary_colour"].lower()
                )
            ):
                results.append(role)

        if not results:
            await interaction.followup.send(f"No roles found matching `{query}`")
            return

        embed = discord.Embed(
            title=f"🔍 Search Results for: {query}",
            description=f"Found {len(results)} role(s)",
            color=discord.Color.orange(),
        )

        for role in results[:10]:  # Limit to 10 results
            created_at = datetime.fromisoformat(role["created_at"])
            field_value = (
                f"**Owner:** `{role['user_id']}`\n"
                f"**Role ID:** `{role['role_id']}`\n"
                f"**Color:** `{role['color']}`\n"
                f"**Created:** <t:{int(created_at.timestamp())}:R>\n"
            )
            embed.add_field(name=f"🏷️ {role['name']}", value=field_value, inline=False)

        if len(results) > 10:
            embed.set_footer(text=f"And {len(results) - 10} more...")

        await interaction.followup.send(embed=embed)

    # ===== BESTOWED LIST COMMAND =====
    @broles.command(name="bestowed_list", description="List all bestowed entries")
    async def list_bestowed(
        self,
        interaction: discord.Interaction,
        user_id: Optional[str] = None,
        owner_id: Optional[str] = None,
    ):
        """List bestowed entries, optionally filtered by user or owner"""
        await interaction.response.defer(thinking=True)

        conn = await self.db.get_connection()

        if user_id:
            try:
                user_id_int = int(user_id)
                async with conn.execute(
                    "SELECT * FROM bestowed WHERE user_id = ?", (user_id_int,)
                ) as cursor:
                    rows = await cursor.fetchall()
                    bestowed_list = [dict(row) for row in rows]
                title = f"Bestowed to User: {user_id}"
            except ValueError:
                await interaction.followup.send("Invalid user ID format.")
                return

        elif owner_id:
            try:
                owner_id_int = int(owner_id)
                async with conn.execute(
                    "SELECT * FROM bestowed WHERE owner_id = ?", (owner_id_int,)
                ) as cursor:
                    rows = await cursor.fetchall()
                    bestowed_list = [dict(row) for row in rows]
                title = f"Bestowed by Owner: {owner_id}"
            except ValueError:
                await interaction.followup.send("Invalid owner ID format.")
                return
        else:
            async with conn.execute(
                "SELECT * FROM bestowed ORDER BY invited_at DESC"
            ) as cursor:
                rows = await cursor.fetchall()
                bestowed_list = [dict(row) for row in rows]
            title = "All Bestowed Entries"

        if not bestowed_list:
            await interaction.followup.send(
                f"No bestowed entries found{f' for {user_id}' if user_id else f' for owner {owner_id}' if owner_id else ''}."
            )
            return

        embed = discord.Embed(
            title=title,
            description=f"Total entries: {len(bestowed_list)}",
            color=discord.Color.purple(),
        )

        for bestowed in bestowed_list[:15]:  # Limit to 15 entries
            # Get role info
            role_data = await self.db.get_role_by_role_id(bestowed["role_id"])
            role_name = role_data["name"] if role_data else "Unknown Role"

            invited_at = datetime.fromisoformat(bestowed["invited_at"])
            status = "✅ Accepted" if bestowed["accepted"] else "⏳ Pending"

            field_value = (
                f"**Owner:** <@{bestowed['owner_id']}>\n"
                f"**User:** <@{bestowed['user_id']}>\n"
                f"**Role:** {role_name}\n"
                f"**Status:** {status}\n"
                f"**Invited:** <t:{int(invited_at.timestamp())}:R>\n"
            )

            embed.add_field(
                name=f"Entry #{bestowed['id']}", value=field_value, inline=False
            )

        if len(bestowed_list) > 15:
            embed.set_footer(text=f"And {len(bestowed_list) - 15} more entries...")

        await interaction.followup.send(embed=embed)

    # ===== BLACKLIST COMMANDS =====
    @broles.command(name="blacklist_add", description="Add a user to blacklist")
    async def blacklist_add(self, interaction: discord.Interaction, user_id: str):
        """Add user to blacklist"""
        await interaction.response.defer(thinking=True)

        try:
            user_id_int = int(user_id)
        except ValueError:
            await interaction.followup.send("Invalid user ID format.")
            return

        success = await self.db.add_to_blacklist(user_id_int)

        if success:
            # Try to find user in guild
            user = interaction.guild.get_member(user_id_int)
            if user:
                await interaction.followup.send(
                    f"✅ User {user.mention} (`{user_id}`) has been added to the blacklist."
                )
            else:
                await interaction.followup.send(
                    f"✅ User `{user_id}` has been added to the blacklist."
                )
        else:
            await interaction.followup.send("❌ Failed to add user to blacklist.")

    @broles.command(name="blacklist_remove", description="Remove a user from blacklist")
    async def blacklist_remove(self, interaction: discord.Interaction, user_id: str):
        """Remove user from blacklist"""
        await interaction.response.defer(thinking=True)

        try:
            user_id_int = int(user_id)
        except ValueError:
            await interaction.followup.send("Invalid user ID format.")
            return

        # Check if actually blacklisted BEFORE trying to remove
        was_blacklisted = await self.db.is_user_banned(user_id_int)
        if not was_blacklisted:
            await interaction.followup.send(
                f"⚠️ User `{user_id}` is not in the blacklist.", ephemeral=False
            )
            return

        success = await self.db.remove_from_blacklist(user_id_int)

        if success:
            await interaction.followup.send(
                f"✅ User `{user_id}` has been removed from the blacklist."
            )
        else:
            await interaction.followup.send("❌ Failed to remove user from blacklist.")

    @broles.command(name="blacklist_list", description="List all blacklisted users")
    async def list_blacklist(self, interaction: discord.Interaction):
        """List all blacklisted users"""
        await interaction.response.defer(thinking=True)

        blacklist = await self.db.get_blacklist()

        if not blacklist:
            await interaction.followup.send("Blacklist is empty.")
            return

        embed = discord.Embed(
            title="🚫 Blacklisted Users",
            description=f"Total blacklisted: {len(blacklist)}",
            color=discord.Color.red(),
        )

        # Display in chunks of 10
        chunks = [blacklist[i : i + 10] for i in range(0, len(blacklist), 10)]

        for i, chunk in enumerate(chunks[:5]):  # Limit to 5 embeds worth
            user_list = "\n".join(
                [f"• <@{user_id}> (`{user_id}`)" for user_id in chunk]
            )
            embed.add_field(name=f"Group {i + 1}", value=user_list, inline=False)

        if len(chunks) > 5:
            embed.set_footer(text=f"And {len(blacklist) - 50} more users...")

        await interaction.followup.send(embed=embed)

    @broles.command(
        name="blacklist_check", description="Check if a user is blacklisted"
    )
    async def blacklist_check(self, interaction: discord.Interaction, user_id: str):
        """Check if user is blacklisted"""
        await interaction.response.defer(thinking=True)

        try:
            user_id_int = int(user_id)
        except ValueError:
            await interaction.followup.send("Invalid user ID format.")
            return

        is_banned = await self.db.is_user_banned(user_id_int)

        if is_banned:
            embed = discord.Embed(
                title="🚫 User Status",
                description=f"User `{user_id}` is **BLACKLISTED**",
                color=discord.Color.red(),
            )
        else:
            embed = discord.Embed(
                title="✅ User Status",
                description=f"User `{user_id}` is **NOT blacklisted**",
                color=discord.Color.green(),
            )

        # Try to get user info
        user = interaction.guild.get_member(user_id_int)
        if user:
            embed.add_field(
                name="Username", value=f"{user.name}#{user.discriminator}", inline=True
            )
            embed.add_field(
                name="Joined Server",
                value=f"<t:{int(user.joined_at.timestamp())}:R>",
                inline=True,
            )
            embed.set_thumbnail(url=user.display_avatar.url)

        await interaction.followup.send(embed=embed)

    # ===== DELETE ROLE COMMAND =====
    @broles.command(name="delete", description="Delete a role and all associated data")
    async def delete_role(
        self,
        interaction: discord.Interaction,
        user_id: str,
        delete_discord_role: Optional[bool] = False,
    ):
        """Delete a role from database and optionally from Discord"""
        await interaction.response.defer(thinking=True)

        try:
            user_id_int = int(user_id)
        except ValueError:
            await interaction.followup.send("Invalid user ID format.")
            return

        # Get role data before deletion
        role_data = await self.db.get_role(user_id_int)

        if not role_data:
            await interaction.followup.send(f"❌ No role found for user `{user_id}`.")
            return

        # Get bestowed entries before deletion
        conn = await self.db.get_connection()
        async with conn.execute(
            "SELECT * FROM bestowed WHERE owner_id = ? OR (user_id = ? AND accepted = 1)",
            (user_id_int, user_id_int),
        ) as cursor:
            rows = await cursor.fetchall()
            bestowed_entries = [dict(row) for row in rows]

        # Create confirmation embed
        embed = discord.Embed(title="⚠️ Role Deletion", color=discord.Color.red())

        embed.add_field(
            name="Role Owner", value=f"<@{user_id_int}> (`{user_id_int}`)", inline=False
        )
        embed.add_field(name="Role Name", value=role_data["name"], inline=False)
        embed.add_field(name="Role ID", value=f"`{role_data['role_id']}`", inline=False)

        if delete_discord_role:
            embed.add_field(
                name="Discord Role", value="Will be deleted from server", inline=False
            )
        else:
            embed.add_field(
                name="Discord Role", value="Will remain in server", inline=False
            )

        # Count affected bestowed entries
        owner_bestowed = len(
            [b for b in bestowed_entries if b["owner_id"] == user_id_int]
        )
        user_bestowed = len(
            [
                b
                for b in bestowed_entries
                if b["user_id"] == user_id_int and b["accepted"]
            ]
        )

        embed.add_field(
            name="Affected Bestowed",
            value=f"Owner's bestowed: {owner_bestowed}\nUser's received: {user_bestowed}",
            inline=False,
        )

        embed.set_footer(text="React with ✅ to confirm deletion or ❌ to cancel")

        msg = await interaction.followup.send(embed=embed)

        # Add reactions for confirmation
        await msg.add_reaction("✅")
        await msg.add_reaction("❌")

        def check(reaction, user):
            return (
                user == interaction.user
                and str(reaction.emoji) in ["✅", "❌"]
                and reaction.message.id == msg.id
            )

        try:
            reaction, user = await self.bot.wait_for(
                "reaction_add", timeout=60.0, check=check
            )

            if str(reaction.emoji) == "✅":
                # Delete Discord role if requested
                if delete_discord_role:
                    try:
                        role = interaction.guild.get_role(role_data["role_id"])
                        if role:
                            await role.delete(
                                reason=f"Deleted by admin {interaction.user} via /broles delete"
                            )
                            discord_role_deleted = True
                        else:
                            discord_role_deleted = False
                    except Exception as e:
                        await msg.edit(
                            content=f"⚠️ Failed to delete Discord role: {e}", embed=None
                        )
                        discord_role_deleted = False
                else:
                    discord_role_deleted = False

                # Delete from database
                success = await self.db.delete_role(user_id_int)

                if success:
                    # Create success embed
                    success_embed = discord.Embed(
                        title="✅ Role Deleted Successfully",
                        color=discord.Color.green(),
                    )

                    success_embed.add_field(
                        name="Role Owner", value=f"<@{user_id_int}>", inline=True
                    )
                    success_embed.add_field(
                        name="Role Name", value=role_data["name"], inline=True
                    )
                    success_embed.add_field(
                        name="Database Cleanup", value="✅ Complete", inline=True
                    )

                    if discord_role_deleted:
                        success_embed.add_field(
                            name="Discord Role", value="✅ Deleted", inline=True
                        )
                    else:
                        success_embed.add_field(
                            name="Discord Role", value="⏸️ Preserved", inline=True
                        )

                    success_embed.add_field(
                        name="Bestowed Entries Removed",
                        value=f"`{len(bestowed_entries)}`",
                        inline=True,
                    )

                    # Notify affected users if possible
                    affected_users = set()
                    for bestowed in bestowed_entries:
                        affected_users.add(bestowed["user_id"])
                        if bestowed["owner_id"] != user_id_int:
                            affected_users.add(bestowed["owner_id"])

                    if affected_users:
                        affected_list = ", ".join(
                            [
                                f"<@{uid}>"
                                for uid in affected_users
                                if uid != user_id_int
                            ]
                        )
                        success_embed.add_field(
                            name="Affected Users",
                            value=affected_list[:1024],
                            inline=False,
                        )

                    success_embed.set_footer(text=f"Deleted by {interaction.user}")

                    await msg.edit(embed=success_embed, content=None)

                else:
                    await msg.edit(
                        content="❌ Failed to delete role from database.", embed=None
                    )

            else:
                await msg.edit(content="❌ Role deletion cancelled.", embed=None)

        except asyncio.TimeoutError:
            await msg.edit(content="⏰ Deletion confirmation timed out.", embed=None)

    # ===== CLEANUP COMMAND =====
    @broles.command(name="cleanup", description="Clean up old or invalid entries")
    async def cleanup_database(
        self, interaction: discord.Interaction, days_old: Optional[int] = 30
    ):
        """Clean up old bestowed entries that were never accepted"""
        await interaction.response.defer(thinking=True)

        cutoff_date = datetime.now(
            timezone(timedelta(hours=5, minutes=30))
        ) - timedelta(days=days_old)
        cutoff_iso = cutoff_date.isoformat()

        conn = await self.db.get_connection()

        # Find old unaccepted bestowed entries
        async with conn.execute(
            """
            SELECT COUNT(*) as count 
            FROM bestowed 
            WHERE accepted = 0 AND invited_at < ?
        """,
            (cutoff_iso,),
        ) as cursor:
            row = await cursor.fetchone()
            old_entries = row["count"] if row else 0

        if old_entries == 0:
            await interaction.followup.send(
                f"No unaccepted bestowed entries older than {days_old} days found."
            )
            return

        # Create confirmation embed
        embed = discord.Embed(
            title="⚠️ Database Cleanup",
            description=f"Found {old_entries} unaccepted bestowed entries older than {days_old} days.",
            color=discord.Color.orange(),
        )

        embed.add_field(
            name="Action",
            value="This will permanently delete these entries.",
            inline=False,
        )
        embed.add_field(
            name="Cutoff Date",
            value=f"<t:{int(cutoff_date.timestamp())}:F>",
            inline=False,
        )
        embed.set_footer(text="React with ✅ to confirm or ❌ to cancel.")

        msg = await interaction.followup.send(embed=embed)

        # Add reactions for confirmation
        await msg.add_reaction("✅")
        await msg.add_reaction("❌")

        def check(reaction, user):
            return (
                user == interaction.user
                and str(reaction.emoji) in ["✅", "❌"]
                and reaction.message.id == msg.id
            )

        try:
            reaction, user = await self.bot.wait_for(
                "reaction_add", timeout=60.0, check=check
            )

            if str(reaction.emoji) == "✅":
                # Perform cleanup - only remove expired unaccepted invitations
                await conn.execute(
                    "DELETE FROM bestowed WHERE accepted = 0 AND invited_at < ?",
                    (cutoff_iso,),
                )
                await conn.commit()

                await msg.edit(content="✅ Cleanup completed successfully!", embed=None)

            else:
                await msg.edit(content="❌ Cleanup cancelled.", embed=None)

        except asyncio.TimeoutError:
            await msg.edit(content="⏰ Cleanup confirmation timed out.", embed=None)

    # ===== BACKUP COMMAND =====
    @broles.command(name="backup", description="Create a database backup")
    async def create_backup(self, interaction: discord.Interaction):
        """Create a database backup"""
        await interaction.response.defer(thinking=True)

        success = await self.db.create_backup()

        if success:
            await interaction.followup.send("✅ Database backup created successfully!")
        else:
            await interaction.followup.send("❌ Failed to create database backup.")

    # ===== EXPORT COMMAND =====
    @broles.command(name="export", description="Export database to JSON")
    async def export_data(self, interaction: discord.Interaction):
        """Export all data to JSON format"""
        await interaction.response.defer(thinking=True)

        try:
            import json
            from pathlib import Path

            # Use load_db function from database module
            from .database import load_db

            data = await load_db()

            export_path = Path("exports")
            export_path.mkdir(exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = export_path / f"booster_roles_export_{timestamp}.json"

            with open(filename, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            await interaction.followup.send(
                f"✅ Data exported successfully!\nFile: `{filename}`"
            )

        except Exception as e:
            await interaction.followup.send(f"❌ Export failed: {str(e)}")

    # ===== MIGRATE COMMAND =====
    @broles.command(name="migrate", description="Migrate from JSON to SQLite")
    async def migrate_json(self, interaction: discord.Interaction):
        """Migrate data from JSON to SQLite"""
        await interaction.response.defer(thinking=True)

        # You might want to specify the JSON path
        json_path = Path("data/booster_roles.json")

        if not json_path.exists():
            await interaction.followup.send(
                "❌ JSON file not found. Make sure it exists at `data/booster_roles.json`"
            )
            return

        success = await self.db.migrate_from_json(json_path)

        if success:
            await interaction.followup.send("✅ Migration completed successfully!")
        else:
            await interaction.followup.send(
                "❌ Migration failed. Check console for errors."
            )

    # ===== USER INFO COMMAND =====
    @broles.command(
        name="user_info",
        description="Get comprehensive info about a user's booster role activity",
    )
    async def user_info(self, interaction: discord.Interaction, user_id: str):
        """Get comprehensive info about a user"""
        await interaction.response.defer(thinking=True)

        try:
            user_id_int = int(user_id)
        except ValueError:
            await interaction.followup.send("Invalid user ID format.")
            return

        # Get user data from guild
        user = interaction.guild.get_member(user_id_int)

        # Get all relevant data
        owned_role = await self.db.get_role(user_id_int)
        received_bestowed = await self.db.get_bestowed_by_user(user_id_int)
        given_bestowed = await self.db.get_bestowed_by_owner(user_id_int)
        is_blacklisted = await self.db.is_user_banned(user_id_int)

        embed = discord.Embed(
            title=f"👤 User Information: {user.display_name if user else f'ID: {user_id}'}",
            color=discord.Color.blue(),
        )

        if user:
            embed.set_thumbnail(url=user.display_avatar.url)
            embed.add_field(
                name="Username", value=f"{user.name}#{user.discriminator}", inline=True
            )
            embed.add_field(name="Nickname", value=user.display_name, inline=True)
            embed.add_field(
                name="Joined Server",
                value=f"<t:{int(user.joined_at.timestamp())}:R>",
                inline=True,
            )

        # Blacklist status
        embed.add_field(
            name="Blacklist Status",
            value="🚫 Yes" if is_blacklisted else "✅ No",
            inline=True,
        )

        # Owned role
        if owned_role:
            embed.add_field(
                name="Owned Role", value=f"✅ {owned_role['name']}", inline=True
            )
            embed.add_field(
                name="Role Color", value=f"`{owned_role['color']}`", inline=True
            )
        else:
            embed.add_field(name="Owned Role", value="❌ None", inline=True)

        # Received bestowed
        accepted_received = [b for b in received_bestowed if b["accepted"]]
        pending_received = [b for b in received_bestowed if not b["accepted"]]

        embed.add_field(
            name="Received Roles",
            value=f"✅ {len(accepted_received)}\n⏳ {len(pending_received)}",
            inline=True,
        )

        # Given bestowed
        embed.add_field(
            name="Given Roles", value=f"`{len(given_bestowed)}`", inline=True
        )

        # Show top 3 received roles
        if accepted_received:
            role_names = []
            for bestowed in accepted_received[:3]:
                role_data = await self.db.get_role_by_role_id(bestowed["role_id"])
                if role_data:
                    role_names.append(
                        f"• {role_data['name']} (from <@{bestowed['owner_id']}>)"
                    )

            if role_names:
                embed.add_field(
                    name="Active Roles", value="\n".join(role_names), inline=False
                )

        await interaction.followup.send(embed=embed)


    # ===== HISTORY COMMAND =====
    @broles.command(
        name="history",
        description="View a user's role change audit log",
    )
    async def role_history(
        self,
        interaction: discord.Interaction,
        user_id: str,
        limit: Optional[int] = 15,
    ):
        """Show per-user audit trail of role actions"""
        await interaction.response.defer(thinking=True)

        try:
            user_id_int = int(user_id)
        except ValueError:
            await interaction.followup.send("Invalid user ID format.")
            return

        entries = await self.db.get_audit_log(user_id_int, limit=min(limit, 25))

        if not entries:
            await interaction.followup.send(
                f"No audit log entries found for user `{user_id}`."
            )
            return

        user = interaction.guild.get_member(user_id_int)
        display = user.display_name if user else f"ID: {user_id}"

        embed = discord.Embed(
            title=f"📋 Role History: {display}",
            description=f"Showing last **{len(entries)}** action(s)",
            color=discord.Color.blurple(),
            timestamp=datetime.now(),
        )

        if user:
            embed.set_thumbnail(url=user.display_avatar.url)

        action_icons = {
            "create": "✨",
            "rename": "✏️",
            "recolor": "🎨",
            "delete": "🗑️",
            "icon_set": "🖼️",
            "icon_remove": "🚫",
        }

        for entry in entries:
            action = entry["action"]
            details = entry["details"]
            icon = action_icons.get(action, "🔹")
            performed_at = datetime.fromisoformat(entry["performed_at"])
            ts = f"<t:{int(performed_at.timestamp())}:R>"

            if action == "create":
                value = (
                    f"**Role:** `{details.get('name', '?')}`\n"
                    f"**Color:** `{details.get('color', '?')}`\n"
                    f"**When:** {ts}"
                )
            elif action == "rename":
                value = (
                    f"**From:** `{details.get('old_name', '?')}`\n"
                    f"**To:** `{details.get('new_name', '?')}`\n"
                    f"**When:** {ts}"
                )
            elif action == "recolor":
                old_c = details.get("old_color", "?")
                new_c = details.get("new_color", "?")
                old_s = details.get("old_secondary")
                new_s = details.get("new_secondary")
                color_line = f"`{old_c}` → `{new_c}`"
                if old_s or new_s:
                    color_line += f"\nSecondary: `{old_s or 'none'}` → `{new_s or 'none'}`"
                value = f"**Colors:** {color_line}\n**Type:** {details.get('color_type', '?').title()}\n**When:** {ts}"
            elif action == "delete":
                deleted_by = details.get("deleted_by")
                by_str = f"<@{deleted_by}>" if deleted_by else "unknown"
                mod = " (mod action)" if details.get("is_mod_action") else ""
                reason = details.get("reason") or "No reason given"
                value = (
                    f"**Role:** `{details.get('role_name', '?')}`\n"
                    f"**Deleted by:** {by_str}{mod}\n"
                    f"**Reason:** {reason}\n"
                    f"**When:** {ts}"
                )
            elif action == "icon_set":
                value = f"**Type:** {details.get('icon_type', '?')}\n**When:** {ts}"
            elif action == "icon_remove":
                value = f"**When:** {ts}"
            else:
                value = f"**Details:** {str(details)[:200]}\n**When:** {ts}"

            embed.add_field(
                name=f"{icon} {action.replace('_', ' ').title()}",
                value=value,
                inline=False,
            )

        embed.set_footer(text=f"Requested by {interaction.user} • Showing newest first")
        await interaction.followup.send(embed=embed)


async def setup(bot):
    """Setup function to add cog to bot"""
    cog = BoosterRolesManage(bot)
    await bot.add_cog(cog)