import discord
from discord.ext import commands
from discord import app_commands
import asyncio
from typing import Optional


class BroadcastCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def is_owner(self, interaction: discord.Interaction) -> bool:
        """Check if the user is the bot owner"""
        owner_id = self.bot.owner_id or (await self.bot.application_info()).owner.id
        return interaction.user.id == owner_id

    def security_check(self, member_count: int) -> bool:
        """Security check to prevent mass DM abuse"""
        # Limit to prevent accidental mass DMs
        MAX_MEMBERS_WARNING = 100

        if member_count > MAX_MEMBERS_WARNING:
            return False
        return True

    # Broadcast command group
    broadcast_group = app_commands.Group(
        name="broadcast", description="Broadcast messages to users"
    )

    @broadcast_group.command(name="user", description="Send a DM to a specific user")
    @app_commands.describe(
        user_id="The user ID to message",
        title="Embed title",
        description="Embed description",
        footer="Embed footer text",
        color="Embed color (hex code without #)",
        thumbnail_url="Thumbnail URL",
        image_url="Image URL",
    )
    async def broadcast_user(
        self,
        interaction: discord.Interaction,
        user_id: str,
        title: str,
        description: str,
        footer: Optional[str] = None,
        color: Optional[str] = "5865F2",
        thumbnail_url: Optional[str] = None,
        image_url: Optional[str] = None,
    ):
        """Send a DM to a specific user"""
        if not await self.is_owner(interaction):
            embed = discord.Embed(
                title="❌ Access Denied",
                description="Only the bot owner can use this command.",
                color=discord.Color.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        try:
            user = await self.bot.fetch_user(int(user_id))
        except (discord.NotFound, ValueError):
            embed = discord.Embed(
                title="❌ Error",
                description="User not found.",
                color=discord.Color.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Security check - don't allow messaging banned users from feedback system
        # You could add additional security checks here

        # Create embed
        try:
            embed_color = discord.Color(int(color, 16))
        except ValueError:
            embed_color = discord.Color.blue()

        embed = discord.Embed(
            title=title,
            description=description,
            color=embed_color,
            timestamp=discord.utils.utcnow(),
        )

        if footer:
            embed.set_footer(text=footer)

        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)

        if image_url:
            embed.set_image(url=image_url)

        try:
            await user.send(embed=embed)
            success_embed = discord.Embed(
                title="✅ Message Sent",
                description=f"Successfully sent message to {user.mention}",
                color=discord.Color.green(),
            )
            await interaction.response.send_message(embed=success_embed, ephemeral=True)
        except discord.Forbidden:
            error_embed = discord.Embed(
                title="❌ Cannot Send",
                description=f"Could not send message to {user.mention}. They may have DMs disabled.",
                color=discord.Color.red(),
            )
            await interaction.response.send_message(embed=error_embed, ephemeral=True)

    @broadcast_group.command(
        name="role", description="Send a DM to all users with a specific role"
    )
    @app_commands.describe(
        role="The role to broadcast to",
        title="Embed title",
        description="Embed description",
        footer="Embed footer text",
        color="Embed color (hex code without #)",
        thumbnail_url="Thumbnail URL",
        image_url="Image URL",
    )
    async def broadcast_role(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        title: str,
        description: str,
        footer: Optional[str] = None,
        color: Optional[str] = "5865F2",
        thumbnail_url: Optional[str] = None,
        image_url: Optional[str] = None,
    ):
        """Send a DM to all users with a specific role"""
        if not await self.is_owner(interaction):
            embed = discord.Embed(
                title="❌ Access Denied",
                description="Only the bot owner can use this command.",
                color=discord.Color.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Get all members with the role
        members = [member for member in role.members if not member.bot]

        if not members:
            error_embed = discord.Embed(
                title="❌ No Members",
                description=f"No human members found with the {role.mention} role.",
                color=discord.Color.orange(),
            )
            await interaction.response.send_message(embed=error_embed, ephemeral=True)
            return

        # Security check
        if not self.security_check(len(members)):
            warning_embed = discord.Embed(
                title="⚠️ Security Warning",
                description=f"This would send DMs to {len(members)} members. This is a large number of users.",
                color=discord.Color.orange(),
            )
            warning_embed.add_field(
                name="Confirmation Required",
                value="Use the command again if you're sure you want to proceed.",
                inline=False,
            )
            await interaction.response.send_message(embed=warning_embed, ephemeral=True)
            return

        # Create embed
        try:
            embed_color = discord.Color(int(color, 16))
        except ValueError:
            embed_color = discord.Color.blue()

        embed = discord.Embed(
            title=title,
            description=description,
            color=embed_color,
            timestamp=discord.utils.utcnow(),
        )

        if footer:
            embed.set_footer(text=footer)

        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)

        if image_url:
            embed.set_image(url=image_url)

        # Send initial response
        progress_embed = discord.Embed(
            title="📤 Starting Broadcast",
            description=f"Sending message to {len(members)} members with {role.mention}...",
            color=discord.Color.blue(),
        )
        await interaction.response.send_message(embed=progress_embed, ephemeral=True)

        # Send messages with rate limiting
        success_count = 0
        fail_count = 0
        failed_users = []

        for member in members:
            try:
                await member.send(embed=embed)
                success_count += 1
                await asyncio.sleep(1)  # Rate limiting - 1 second between messages
            except discord.Forbidden:
                fail_count += 1
                failed_users.append(member.display_name)
            except Exception:
                fail_count += 1
                failed_users.append(member.display_name)

        # Send results
        result_embed = discord.Embed(
            title="📊 Broadcast Complete",
            color=(
                discord.Color.green() if success_count > 0 else discord.Color.orange()
            ),
        )
        result_embed.add_field(name="✅ Successful", value=success_count, inline=True)
        result_embed.add_field(name="❌ Failed", value=fail_count, inline=True)
        result_embed.add_field(name="📝 Total", value=len(members), inline=True)

        if failed_users:
            failed_list = ", ".join(failed_users[:5])  # Show first 5 failed users
            if len(failed_users) > 5:
                failed_list += f" and {len(failed_users) - 5} more..."
            result_embed.add_field(name="Failed Users", value=failed_list, inline=False)

        await interaction.edit_original_response(embed=result_embed)

    @broadcast_group.command(
        name="all", description="Send a DM to all server members (USE WITH CAUTION)"
    )
    @app_commands.describe(
        title="Embed title",
        description="Embed description",
        footer="Embed footer text",
        color="Embed color (hex code without #)",
        thumbnail_url="Thumbnail URL",
        image_url="Image URL",
    )
    async def broadcast_all(
        self,
        interaction: discord.Interaction,
        title: str,
        description: str,
        footer: Optional[str] = None,
        color: Optional[str] = "5865F2",
        thumbnail_url: Optional[str] = None,
        image_url: Optional[str] = None,
    ):
        """Send a DM to all server members - USE WITH CAUTION"""
        if not await self.is_owner(interaction):
            embed = discord.Embed(
                title="❌ Access Denied",
                description="Only the bot owner can use this command.",
                color=discord.Color.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Get all members from all guilds
        all_members = []
        for guild in self.bot.guilds:
            for member in guild.members:
                if not member.bot and member not in all_members:
                    all_members.append(member)

        if not all_members:
            error_embed = discord.Embed(
                title="❌ No Members",
                description="No human members found in any server.",
                color=discord.Color.orange(),
            )
            await interaction.response.send_message(embed=error_embed, ephemeral=True)
            return

        # Security check - always warn for mass DMs
        warning_embed = discord.Embed(
            title="🚨 MASS BROADCAST WARNING",
            description=f"This will send DMs to **{len(all_members)}** members across **{len(self.bot.guilds)}** servers!",
            color=discord.Color.red(),
        )
        warning_embed.add_field(
            name="⚠️ Are you sure?",
            value="This action cannot be undone and may result in rate limits or other issues.",
            inline=False,
        )
        warning_embed.add_field(
            name="✅ To proceed:",
            value="Run this command again to confirm the broadcast.",
            inline=False,
        )

        await interaction.response.send_message(embed=warning_embed, ephemeral=True)
        return

    @broadcast_group.command(
        name="preview", description="Preview your embed before sending"
    )
    @app_commands.describe(
        title="Embed title",
        description="Embed description",
        footer="Embed footer text",
        color="Embed color (hex code without #)",
        thumbnail_url="Thumbnail URL",
        image_url="Image URL",
    )
    async def broadcast_preview(
        self,
        interaction: discord.Interaction,
        title: str,
        description: str,
        footer: Optional[str] = None,
        color: Optional[str] = "5865F2",
        thumbnail_url: Optional[str] = None,
        image_url: Optional[str] = None,
    ):
        """Preview your embed before sending"""
        if not await self.is_owner(interaction):
            embed = discord.Embed(
                title="❌ Access Denied",
                description="Only the bot owner can use this command.",
                color=discord.Color.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Create embed
        try:
            embed_color = discord.Color(int(color, 16))
        except ValueError:
            embed_color = discord.Color.blue()

        embed = discord.Embed(
            title=title,
            description=description,
            color=embed_color,
            timestamp=discord.utils.utcnow(),
        )

        if footer:
            embed.set_footer(text=footer)

        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)

        if image_url:
            embed.set_image(url=image_url)

        preview_embed = discord.Embed(
            title="👀 Embed Preview",
            description="This is how your embed will look:",
            color=discord.Color.blue(),
        )

        await interaction.response.send_message(embed=preview_embed, ephemeral=True)
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot):
    cog = BroadcastCog(bot)
    await bot.add_cog(cog)
