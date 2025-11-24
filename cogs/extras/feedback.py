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
        self.active_conversations = {}  # {bot_message_id: user_id}
        self.owner_dm_channel = None  # Will store the owner's DM channel

    def load_banned_users(self):
        """Load banned users from JSON file"""
        try:
            if os.path.exists(self.banned_users_file):
                with open(self.banned_users_file, 'r') as f:
                    return set(json.load(f))
        except Exception as e:
            print(f"Error loading banned users: {e}")
        return set()

    def save_banned_users(self):
        """Save banned users to JSON file"""
        try:
            with open(self.banned_users_file, 'w') as f:
                json.dump(list(self.banned_users), f)
        except Exception as e:
            print(f"Error saving banned users: {e}")

    async def get_owner_dm_channel(self):
        """Get or create the owner's DM channel"""
        if self.owner_dm_channel is None:
            owner_id = self.bot.owner_id or (await self.bot.application_info()).owner.id
            owner = await self.bot.fetch_user(owner_id)
            self.owner_dm_channel = owner.dm_channel or await owner.create_dm()
        return self.owner_dm_channel

    async def is_owner(self, interaction: discord.Interaction) -> bool:
        """Check if the user is the bot owner"""
        owner_id = self.bot.owner_id or (await self.bot.application_info()).owner.id
        return interaction.user.id == owner_id

    @commands.Cog.listener()
    async def on_ready(self):
        """Initialize owner DM channel when bot is ready"""
        try:
            await self.get_owner_dm_channel()
            print(f"✅ Feedback system ready! Messages will go to owner's DMs")
        except Exception as e:
            print(f"❌ Failed to setup owner DM channel: {e}")

    @commands.Cog.listener()
    async def on_message(self, message):
        """Handle incoming messages - both DMs and replies"""
        # Ignore messages from bots
        if message.author.bot:
            return

        # Handle user DMs to the bot
        if not message.guild and not message.author.bot:
            await self.handle_user_dm(message)
            return

        # Handle owner replies in their DMs
        if not message.guild and message.author.id == (await self.get_owner_dm_channel()).recipient.id:
            await self.handle_owner_reply(message)

    async def handle_user_dm(self, message):
        """Handle DMs from users"""
        # Check if user is banned
        if message.author.id in self.banned_users:
            ban_embed = discord.Embed(
                title="❌ Message Blocked",
                description="You are banned from using this feedback service.",
                color=discord.Color.red()
            )
            await message.author.send(embed=ban_embed)
            return

        # Get owner's DM channel
        try:
            owner_channel = await self.get_owner_dm_channel()
        except Exception as e:
            print(f"Error getting owner DM channel: {e}")
            return

        # Create embed for owner
        embed = discord.Embed(
            title="📩 New Feedback",
            description=message.content,
            color=discord.Color.blue(),
            timestamp=message.created_at
        )
        embed.set_author(
            name=f"{message.author} (ID: {message.author.id})",
            icon_url=message.author.display_avatar.url
        )
        embed.set_footer(text="Reply to this message to respond to the user")

        # Add any attachments
        if message.attachments:
            attachment_list = "\n".join([att.url for att in message.attachments])
            embed.add_field(
                name="📎 Attachments",
                value=attachment_list[:1024],
                inline=False
            )

        try:
            # Send to owner's DMs and store the mapping
            bot_message = await owner_channel.send(embed=embed)
            self.active_conversations[bot_message.id] = message.author.id

            # Send confirmation to user
            confirm_embed = discord.Embed(
                title="✅ Feedback Received",
                description="Your message has been sent. Please wait for a response.",
                color=discord.Color.green()
            )
            await message.author.send(embed=confirm_embed)
        except discord.Forbidden:
            # Owner has DMs disabled
            error_embed = discord.Embed(
                title="❌ Cannot Deliver",
                description="The owner has DMs disabled. Please try again later.",
                color=discord.Color.red()
            )
            await message.author.send(embed=error_embed)

    async def handle_owner_reply(self, message):
        """Handle owner replies to bot messages in their DMs"""
        if not message.reference:
            return

        # Get the referenced message (the bot's message)
        try:
            referenced_msg = await message.channel.fetch_message(message.reference.message_id)
        except:
            return

        # Check if this is one of our bot's messages and if it's in active conversations
        if (referenced_msg.author.id != self.bot.user.id or 
            referenced_msg.id not in self.active_conversations):
            return

        # Get the original user
        user_id = self.active_conversations[referenced_msg.id]
        try:
            user = await self.bot.fetch_user(user_id)
        except discord.NotFound:
            # User not found, remove from active conversations
            del self.active_conversations[referenced_msg.id]
            return

        # Check if user is banned
        if user.id in self.banned_users:
            error_embed = discord.Embed(
                title="❌ Cannot Send",
                description="This user is banned from receiving messages.",
                color=discord.Color.red()
            )
            await message.reply(embed=error_embed, delete_after=5)
            return

        # Create embed for user
        user_embed = discord.Embed(
            title="📨 Response Received",
            description=message.content,
            color=discord.Color.green(),
            timestamp=message.created_at
        )
        user_embed.set_footer(text="You can reply to this message to continue the conversation")

        # Add any attachments from owner reply
        if message.attachments:
            attachment_list = "\n".join([att.url for att in message.attachments])
            user_embed.add_field(
                name="📎 Attachments",
                value=attachment_list[:1024],
                inline=False
            )

        try:
            await user.send(embed=user_embed)
            
            # Edit the original bot message to show it was replied to
            original_embed = referenced_msg.embeds[0]
            original_embed.color = discord.Color.green()
            original_embed.set_footer(text=f"✅ Replied • Reply to this message to respond again")
            await referenced_msg.edit(embed=original_embed)

            # Add reaction to owner message to confirm send
            await message.add_reaction("✅")

        except discord.Forbidden:
            # User has DMs disabled
            error_embed = discord.Embed(
                title="❌ Cannot Deliver",
                description="This user has DMs disabled or has blocked the bot.",
                color=discord.Color.red()
            )
            await message.reply(embed=error_embed, delete_after=10)

    # Single feedback command group
    feedback_group = app_commands.Group(name="feedback", description="Manage the feedback system")

    @feedback_group.command(name="stats", description="Show feedback system statistics")
    async def feedback_stats(self, interaction: discord.Interaction):
        """Show feedback statistics"""
        if not await self.is_owner(interaction):
            embed = discord.Embed(
                title="❌ Access Denied",
                description="Only the bot owner can use this command.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        active_convos = len(self.active_conversations)
        banned_count = len(self.banned_users)
        
        stats_embed = discord.Embed(
            title="📊 Feedback Statistics",
            color=discord.Color.blue()
        )
        stats_embed.add_field(name="Active Conversations", value=active_convos, inline=True)
        stats_embed.add_field(name="Banned Users", value=banned_count, inline=True)
        stats_embed.add_field(name="Delivery Channel", value="Owner DMs", inline=True)
        
        await interaction.response.send_message(embed=stats_embed, ephemeral=True)

    @feedback_group.command(name="test", description="Test if the feedback system is working")
    async def feedback_test(self, interaction: discord.Interaction):
        """Test the feedback system"""
        if not await self.is_owner(interaction):
            embed = discord.Embed(
                title="❌ Access Denied",
                description="Only the bot owner can use this command.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        try:
            owner_channel = await self.get_owner_dm_channel()
            test_embed = discord.Embed(
                title="✅ System Working",
                description="User feedback will be delivered to your DMs.",
                color=discord.Color.green()
            )
            await interaction.response.send_message(embed=test_embed, ephemeral=True)
        except Exception as e:
            error_embed = discord.Embed(
                title="❌ System Error",
                description=f"Failed to access owner DMs: {e}",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=error_embed, ephemeral=True)

    @feedback_group.command(name="ban", description="Ban a user from using the feedback system")
    @app_commands.describe(user_id="The user ID to ban")
    async def feedback_ban(self, interaction: discord.Interaction, user_id: str):
        """Ban a user from sending feedback"""
        if not await self.is_owner(interaction):
            embed = discord.Embed(
                title="❌ Access Denied",
                description="Only the bot owner can use this command.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        try:
            user = await self.bot.fetch_user(int(user_id))
        except (discord.NotFound, ValueError):
            embed = discord.Embed(
                title="❌ Error",
                description="User not found.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if user.id in self.banned_users:
            embed = discord.Embed(
                title="ℹ️ Info",
                description="User is already banned.",
                color=discord.Color.orange()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        self.banned_users.add(user.id)
        self.save_banned_users()

        embed = discord.Embed(
            title="🔨 User Banned",
            description=f"{user.mention} has been banned from using the feedback system.",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed)

    @feedback_group.command(name="unban", description="Unban a user from the feedback system")
    @app_commands.describe(user_id="The user ID to unban")
    async def feedback_unban(self, interaction: discord.Interaction, user_id: str):
        """Unban a user from sending feedback"""
        if not await self.is_owner(interaction):
            embed = discord.Embed(
                title="❌ Access Denied",
                description="Only the bot owner can use this command.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        try:
            user = await self.bot.fetch_user(int(user_id))
        except (discord.NotFound, ValueError):
            embed = discord.Embed(
                title="❌ Error",
                description="User not found.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if user.id not in self.banned_users:
            embed = discord.Embed(
                title="ℹ️ Info",
                description="User is not banned.",
                color=discord.Color.orange()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        self.banned_users.remove(user.id)
        self.save_banned_users()

        embed = discord.Embed(
            title="✅ User Unbanned",
            description=f"{user.mention} has been unbanned from the feedback system.",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed)

    @feedback_group.command(name="banlist", description="Show all banned users")
    async def feedback_banlist(self, interaction: discord.Interaction):
        """Show list of banned users"""
        if not await self.is_owner(interaction):
            embed = discord.Embed(
                title="❌ Access Denied",
                description="Only the bot owner can use this command.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if not self.banned_users:
            embed = discord.Embed(
                title="📋 Banned Users",
                description="No users are currently banned.",
                color=discord.Color.blue()
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
            description="\n".join(banned_users_info) if banned_users_info else "No banned users",
            color=discord.Color.orange()
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
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        try:
            user = await self.bot.fetch_user(int(user_id))
        except (discord.NotFound, ValueError):
            embed = discord.Embed(
                title="❌ Error",
                description="User not found.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if user.id in self.banned_users:
            embed = discord.Embed(
                title="🔨 User Status",
                description=f"{user.mention} is **BANNED** from the feedback system.",
                color=discord.Color.red()
            )
        else:
            embed = discord.Embed(
                title="✅ User Status",
                description=f"{user.mention} is **NOT BANNED** from the feedback system.",
                color=discord.Color.green()
            )
        
        await interaction.response.send_message(embed=embed)

async def setup(bot):
    cog = TwoWayMessaging(bot)
    await bot.add_cog(cog)
    
    # Add the single feedback group to the bot
    bot.tree.add_command(cog.feedback_group)