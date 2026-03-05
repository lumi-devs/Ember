import discord
from discord.ext import commands
from discord import app_commands
import json
import os
from typing import Optional


class TwoWayMessaging(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.banned_users_file = "banned_users.json"
        self.banned_users = self.load_banned_users()
        self.owner_dm_channel = None
        self.owner_id = None

    def load_banned_users(self):
        """Load banned users from JSON file"""
        try:
            if os.path.exists(self.banned_users_file):
                with open(self.banned_users_file, "r") as f:
                    return set(json.load(f))
        except Exception as e:
            print(f"Error loading banned users: {e}")
        return set()

    def save_banned_users(self):
        """Save banned users to JSON file"""
        try:
            with open(self.banned_users_file, "w") as f:
                json.dump(list(self.banned_users), f)
        except Exception as e:
            print(f"Error saving banned users: {e}")

    async def get_owner_info(self):
        """Get owner ID and DM channel"""
        if self.owner_id is None:
            app_info = await self.bot.application_info()
            self.owner_id = app_info.owner.id
            owner_user = await self.bot.fetch_user(self.owner_id)
            self.owner_dm_channel = (
                owner_user.dm_channel or await owner_user.create_dm()
            )
        return self.owner_id, self.owner_dm_channel

    async def is_owner(self, interaction: discord.Interaction) -> bool:
        """Check if the user is the bot owner"""
        owner_id, _ = await self.get_owner_info()
        return interaction.user.id == owner_id

    def extract_user_id_from_footer(self, footer_text: str) -> Optional[int]:
        """Extract user ID from footer text"""
        try:
            # Look for pattern like "UserID:123456789" in footer
            if "UserID:" in footer_text:
                user_id_str = footer_text.split("UserID:")[1].split()[
                    0
                ]  # Get the ID part
                return int(user_id_str)
        except (ValueError, IndexError):
            pass
        return None

    @commands.Cog.listener()
    async def on_ready(self):
        """Initialize owner info when bot is ready"""
        try:
            await self.get_owner_info()
            print("✅ Feedback system ready! Messages will go to owner's DMs")
        except Exception as e:
            print(f"❌ Failed to setup owner info: {e}")

    async def handle_user_dm(self, message):
        """Handle DMs from users"""
        # Check if user is banned
        if message.author.id in self.banned_users:
            ban_embed = discord.Embed(
                title="❌ Message Blocked",
                description="You are banned from using this feedback service.",
                color=discord.Color.red(),
            )
            await message.author.send(embed=ban_embed)
            return

        # Get owner's DM channel
        try:
            owner_id, owner_channel = await self.get_owner_info()
        except Exception as e:
            print(f"Error getting owner info: {e}")
            return

        # Create embed for owner - STORE USER ID IN FOOTER
        embed = discord.Embed(
            title="📩 New Feedback",
            description=message.content,
            color=discord.Color.blue(),
            timestamp=message.created_at,
        )

        # Show limited user info to maintain privacy
        embed.set_author(
            name="Anonymous User",  # Hide actual username
            icon_url=message.author.display_avatar.url,  # Keep avatar for identification
        )

        # Store user ID in footer for tracking - this survives bot restarts!
        embed.set_footer(text=f"Reply to respond • UserID:{message.author.id}")

        # Add any attachments
        if message.attachments:
            attachment_list = "\n".join([att.url for att in message.attachments])
            embed.add_field(
                name="📎 Attachments", value=attachment_list[:1024], inline=False
            )

        try:
            # Send to owner's DMs
            await owner_channel.send(embed=embed)

            # Send confirmation to user
        except discord.Forbidden:
            # Owner has DMs disabled
            error_embed = discord.Embed(
                title="❌ Cannot Deliver",
                description="The staff has DMs disabled. Please try again later.",
                color=discord.Color.red(),
            )
            await message.author.send(embed=error_embed)

    @commands.Cog.listener()
    async def on_message(self, message):
        """Handle incoming messages - both DMs and replies"""
        # Ignore messages from bots
        if message.author.bot:
            return

        # Get owner info
        try:
            owner_id, owner_channel = await self.get_owner_info()
        except Exception:
            return

        # Handle owner replies in their DMs
        if not message.guild and message.author.id == owner_id and message.reference:
            await self.handle_owner_reply(message)
            return

        # Ignore messages from owner himself in guilds
        if message.author.id == owner_id:
            return

        # Handle user DMs to the bot
        if not message.guild:
            await self.handle_user_dm(message)

    async def handle_owner_reply(self, message):
        """Handle owner replies to bot messages in their DMs"""
        if not message.reference:
            return

        # Get the referenced message (the bot's message)
        try:
            referenced_msg = await message.channel.fetch_message(
                message.reference.message_id
            )
        except Exception:
            return

        # Check if this is one of our bot's messages
        if referenced_msg.author.id != self.bot.user.id:
            return

        # Check if the message has embeds and extract user ID from footer
        if not referenced_msg.embeds:
            return

        embed = referenced_msg.embeds[0]
        if not embed.footer.text:
            return

        # Extract user ID from footer
        user_id = self.extract_user_id_from_footer(embed.footer.text)
        if not user_id:
            return

        try:
            user = await self.bot.fetch_user(user_id)
        except discord.NotFound:
            # User not found
            error_embed = discord.Embed(
                title="❌ User Not Found",
                description="The original user cannot be found.",
                color=discord.Color.red(),
            )
            await message.reply(embed=error_embed, delete_after=10)
            return

        # Check if user is banned
        if user.id in self.banned_users:
            error_embed = discord.Embed(
                title="❌ Cannot Send",
                description="This user is banned from receiving messages.",
                color=discord.Color.red(),
            )
            await message.reply(embed=error_embed, delete_after=5)
            return

        # Create embed for user - HIDE OWNER IDENTITY
        user_embed = discord.Embed(
            title="📨 Staff Response",
            description=message.content,
            color=discord.Color.green(),
            timestamp=message.created_at,
        )

        # Hide owner identity - show as generic staff
        user_embed.set_author(
            name="Staff Team",
            icon_url=self.bot.user.display_avatar.url,  # Use bot's avatar instead of owner's
        )

        user_embed.set_footer(text="You can reply to continue the conversation")

        # Add any attachments from owner reply
        if message.attachments:
            attachment_list = "\n".join([att.url for att in message.attachments])
            user_embed.add_field(
                name="📎 Attachments", value=attachment_list[:1024], inline=False
            )

        try:
            await user.send(embed=user_embed)

            # Edit the original bot message to show it was replied to
            embed.color = discord.Color.green()
            embed.set_footer(text=f"✅ Replied • UserID:{user_id}")
            await referenced_msg.edit(embed=embed)

        except discord.Forbidden:
            # User has DMs disabled
            error_embed = discord.Embed(
                title="❌ Cannot Deliver",
                description="This user has DMs disabled or has blocked the bot.",
                color=discord.Color.red(),
            )
            await message.reply(embed=error_embed, delete_after=10)

    # Single feedback command group
    feedback_group = app_commands.Group(
        name="feedback", description="Manage the feedback system"
    )

    @feedback_group.command(name="stats", description="Show feedback system statistics")
    async def feedback_stats(self, interaction: discord.Interaction):
        """Show feedback statistics"""
        if not await self.is_owner(interaction):
            embed = discord.Embed(
                title="❌ Access Denied",
                description="Only the bot owner can use this command.",
                color=discord.Color.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        banned_count = len(self.banned_users)

        stats_embed = discord.Embed(
            title="📊 Feedback Statistics", color=discord.Color.blue()
        )
        stats_embed.add_field(name="Banned Users", value=banned_count, inline=True)
        stats_embed.add_field(name="Delivery Channel", value="Your DMs", inline=True)

        await interaction.response.send_message(embed=stats_embed, ephemeral=True)

    @feedback_group.command(
        name="test", description="Test if the feedback system is working"
    )
    async def feedback_test(self, interaction: discord.Interaction):
        """Test the feedback system"""
        if not await self.is_owner(interaction):
            embed = discord.Embed(
                title="❌ Access Denied",
                description="Only the bot owner can use this command.",
                color=discord.Color.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        try:
            owner_id, owner_channel = await self.get_owner_info()
            test_embed = discord.Embed(
                title="✅ System Working",
                description="User feedback will be delivered to your DMs.",
                color=discord.Color.green(),
            )
            await interaction.response.send_message(embed=test_embed, ephemeral=True)
        except Exception as e:
            error_embed = discord.Embed(
                title="❌ System Error",
                description=f"Failed to access your DMs: {e}",
                color=discord.Color.red(),
            )
            await interaction.response.send_message(embed=error_embed, ephemeral=True)

    @feedback_group.command(
        name="ban", description="Ban a user from using the feedback system"
    )
    @app_commands.describe(user_id="The user ID to ban")
    async def feedback_ban(self, interaction: discord.Interaction, user_id: str):
        """Ban a user from sending feedback"""
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

        if user.id in self.banned_users:
            embed = discord.Embed(
                title="ℹ️ Info",
                description="User is already banned.",
                color=discord.Color.orange(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        self.banned_users.add(user.id)
        self.save_banned_users()

        embed = discord.Embed(
            title="🔨 User Banned",
            description=f"{user.mention} has been banned from using the feedback system.",
            color=discord.Color.red(),
        )
        await interaction.response.send_message(embed=embed)

    @feedback_group.command(
        name="unban", description="Unban a user from the feedback system"
    )
    @app_commands.describe(user_id="The user ID to unban")
    async def feedback_unban(self, interaction: discord.Interaction, user_id: str):
        """Unban a user from sending feedback"""
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

        if user.id not in self.banned_users:
            embed = discord.Embed(
                title="ℹ️ Info",
                description="User is not banned.",
                color=discord.Color.orange(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        self.banned_users.remove(user.id)
        self.save_banned_users()

        embed = discord.Embed(
            title="✅ User Unbanned",
            description=f"{user.mention} has been unbanned from the feedback system.",
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed)

    @feedback_group.command(name="banlist", description="Show all banned users")
    async def feedback_banlist(self, interaction: discord.Interaction):
        """Show list of banned users"""
        if not await self.is_owner(interaction):
            embed = discord.Embed(
                title="❌ Access Denied",
                description="Only the bot owner can use this command.",
                color=discord.Color.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if not self.banned_users:
            embed = discord.Embed(
                title="📋 Banned Users",
                description="No users are currently banned.",
                color=discord.Color.blue(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        banned_users_info = []
        for user_id in self.banned_users:
            try:
                user = await self.bot.fetch_user(user_id)
                banned_users_info.append(f"{user.mention} (ID: {user_id})")
            except discord.NotFound:
                banned_users_info.append(f"Unknown User (ID: {user_id})")

        embed = discord.Embed(
            title="📋 Banned Users",
            description=(
                "\n".join(banned_users_info) if banned_users_info else "No banned users"
            ),
            color=discord.Color.orange(),
        )
        embed.set_footer(text=f"Total banned: {len(self.banned_users)}")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @feedback_group.command(name="check", description="Check if a user is banned")
    @app_commands.describe(user_id="The user ID to check")
    async def feedback_check(self, interaction: discord.Interaction, user_id: str):
        """Check if a user is banned"""
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

        if user.id in self.banned_users:
            embed = discord.Embed(
                title="🔨 User Status",
                description=f"{user.mention} is **BANNED** from the feedback system.",
                color=discord.Color.red(),
            )
        else:
            embed = discord.Embed(
                title="✅ User Status",
                description=f"{user.mention} is **NOT BANNED** from the feedback system.",
                color=discord.Color.green(),
            )

        await interaction.response.send_message(embed=embed)


async def setup(bot):
    cog = TwoWayMessaging(bot)
    await bot.add_cog(cog)
