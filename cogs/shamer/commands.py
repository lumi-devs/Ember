import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional, Union
from .database import ShameDatabase
from .utils import ShameUtils
from .config import ShameConfig
import aiosqlite
from datetime import datetime, timedelta



class ConfirmResetView(discord.ui.View):
    def __init__(self, db, reset_type, requester):
        super().__init__(timeout=60)
        self.db = db
        self.reset_type = reset_type
        self.requester = requester

    @discord.ui.button(label="🚨 CONFIRM RESET", style=discord.ButtonStyle.danger)
    async def confirm_reset(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Double check it's the same user who initiated
        if interaction.user.id != self.requester.id:
            await interaction.response.send_message("❌ Only the user who started the reset can confirm it!", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            async with aiosqlite.connect(self.db.db_path) as db:
                if self.reset_type == "full":
                    # FULL RESET - Delete everything
                    await db.execute('DELETE FROM users')
                    await db.execute('DELETE FROM blacklist')
                    await db.execute('DELETE FROM system_state')
                    await db.execute('DELETE FROM leaderboard_history')
                    
                    result_message = "✅ **FULL RESET COMPLETE**\n• All user data deleted\n• Blacklist cleared\n• System state reset\n• Fresh start!"

                elif self.reset_type == "points":
                    # POINTS RESET - Keep users, zero points
                    await db.execute('UPDATE users SET points = 0, hourly_points = 0')
                    await db.execute('DELETE FROM system_state WHERE key IN ("last_leaderboard", "last_leaderboard_sent")')
                    
                    # Get affected user count
                    cursor = await db.execute('SELECT COUNT(*) FROM users')
                    user_count = (await cursor.fetchone())[0]
                    
                    result_message = f"✅ **POINTS RESET COMPLETE**\n• Zeroed points for {user_count} users\n• Preserved message counts\n• Ready for new season!"

                elif self.reset_type == "messages":
                    # MESSAGES RESET - Keep users, zero message counts
                    await db.execute('UPDATE users SET total_messages = 0')
                    
                    # Get affected user count
                    cursor = await db.execute('SELECT COUNT(*) FROM users WHERE total_messages > 0')
                    user_count = (await cursor.fetchone())[0]
                    
                    result_message = f"✅ **MESSAGES RESET COMPLETE**\n• Zeroed message counts for {user_count} users\n• Points preserved\n• Message tracking restarted!"

                elif self.reset_type == "points_messages":
                    # POINTS & MESSAGES RESET - Keep users, zero both
                    await db.execute('UPDATE users SET points = 0, hourly_points = 0, total_messages = 0')
                    await db.execute('DELETE FROM system_state WHERE key IN ("last_leaderboard", "last_leaderboard_sent")')
                    
                    # Get affected user count
                    cursor = await db.execute('SELECT COUNT(*) FROM users')
                    user_count = (await cursor.fetchone())[0]
                    
                    result_message = f"✅ **POINTS & MESSAGES RESET COMPLETE**\n• Zeroed points & messages for {user_count} users\n• Complete fresh start!\n• Ready for new season!"

                else:  # leaderboard reset
                    # LEADERBOARD RESET - Just reset timers
                    await db.execute('DELETE FROM system_state WHERE key IN ("last_leaderboard", "last_leaderboard_sent")')
                    
                    result_message = "✅ **LEADERBOARD RESET COMPLETE**\n• Rotation timers reset\n• Next message will trigger new leaderboard\n• User data preserved!"

                await db.commit()

            # Create success embed
            success_embed = discord.Embed(
                title="✅ Reset Successful",
                description=result_message,
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow()
            )
            
            success_embed.add_field(
                name="👤 Requested By",
                value=self.requester.mention,
                inline=True
            )
            
            success_embed.add_field(
                name="🕒 Reset Time", 
                value=f"<t:{int(discord.utils.utcnow().timestamp())}:F>",
                inline=True
            )

            # Remove the buttons after confirmation
            await interaction.edit_original_response(embed=success_embed, view=None)

            print(f"[ShameSystem] 🚨 HARD RESET performed by {self.requester} - Type: {self.reset_type}")

        except Exception as e:
            error_embed = discord.Embed(
                title="❌ Reset Failed",
                description=f"An error occurred during the reset:\n```{str(e)}```",
                color=discord.Color.red()
            )
            await interaction.edit_original_response(embed=error_embed, view=None)

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_reset(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.requester.id:
            await interaction.response.send_message("❌ Only the user who started the reset can cancel it!", ephemeral=True)
            return

        cancel_embed = discord.Embed(
            title="✅ Reset Cancelled",
            description="The hard reset operation has been cancelled.",
            color=discord.Color.orange()
        )
        
        await interaction.response.edit_message(embed=cancel_embed, view=None)
class ShameCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = ShameDatabase(ShameConfig.DB_PATH)
        self.utils = ShameUtils(self.db, self.bot)
    


# Add this to your existing commands.py in the ShameCommands class


    shame = app_commands.Group(name="shame", description="Hall of Shame leaderboard commands")
    
    @shame.command(name="health", description="Check system health status")
    async def shame_health(self, interaction: discord.Interaction):
        """System health check"""
        try:
            embed = discord.Embed(title="🩺 Shame System Health", color=discord.Color.green())
            
            # Check database connection
            try:
                test_data = await self.db.get_leaderboard(1)
                embed.add_field(name="Database", value="✅ Connected", inline=True)
            except Exception as e:
                embed.add_field(name="Database", value="❌ Error", inline=True)
                embed.color = discord.Color.red()
            
            # Check channel access
            channel = self.bot.get_channel(ShameConfig.LEADERBOARD_CHANNEL_ID)
            embed.add_field(name="Leaderboard Channel", 
                           value="✅ Accessible" if channel else "❌ Not Found", 
                           inline=True)
            
            # Check role access
            shame_role = None
            for guild in self.bot.guilds:
                role = guild.get_role(ShameConfig.SHAME_ROLE_ID)
                if role:
                    shame_role = role
                    break
            
            embed.add_field(name="Shame Role", 
                           value="✅ Found" if shame_role else "❌ Not Found", 
                           inline=True)
            
            # System stats
            total_users = len(await self.db.get_leaderboard(10000))
            blacklisted = len(await self.db.get_blacklist())
            
            embed.add_field(name="Total Users", value=str(total_users), inline=True)
            embed.add_field(name="Blacklisted", value=str(blacklisted), inline=True)
            embed.add_field(name="Uptime", value="✅ Running", inline=True)
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
        except Exception as e:
            await interaction.response.send_message(
                f"❌ Error checking system health: {str(e)}", 
                ephemeral=True
            )


    @shame.command(name="tracking_info", description="Check which channels/categories are being tracked (Admin only)")
    async def shame_tracking_info(self, interaction: discord.Interaction):
        """Show tracking configuration information (Admin only)"""
        try:
            # Check admin permissions
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message(
                    "❌ You need administrator permissions to use this command!", 
                    ephemeral=True
                )
                return
                
            embed = discord.Embed(
                title="📊 Tracking Configuration",
                color=discord.Color.blue(),
                timestamp=discord.utils.utcnow()
            )
            
            # Blacklisted categories
            blacklisted_categories = []
            for cat_id in ShameConfig.BLACKLISTED_CATEGORY_IDS:
                category = interaction.guild.get_channel(cat_id)
                if category:
                    blacklisted_categories.append(f"📁 {category.name} (`{cat_id}`)")
                else:
                    blacklisted_categories.append(f"📁 Unknown Category (`{cat_id}`)")
            
            # Blacklisted channels
            blacklisted_channels = []
            for channel_id in ShameConfig.BLACKLISTED_CHANNEL_IDS:
                channel = interaction.guild.get_channel(channel_id)
                if channel:
                    blacklisted_channels.append(f"#{channel.name} (`{channel_id}`)")
                else:
                    blacklisted_channels.append(f"#unknown-channel (`{channel_id}`)")
            
            if blacklisted_categories:
                embed.add_field(
                    name="🚫 Blacklisted Categories",
                    value="\n".join(blacklisted_categories[:15]),  # Increased limit
                    inline=False
                )
            
            if blacklisted_channels:
                embed.add_field(
                    name="🚫 Blacklisted Channels", 
                    value="\n".join(blacklisted_channels[:15]),  # Increased limit
                    inline=False
                )
            
            if not blacklisted_categories and not blacklisted_channels:
                embed.description = "✅ All channels and categories are being tracked!"
            
            # Add configuration info
            embed.add_field(
                name="⚙️ Configuration",
                value=(
                    f"**Database Blacklist:** {len(await self.db.get_blacklist())} items\n"
                    f"**Hardcoded Categories:** {len(ShameConfig.BLACKLISTED_CATEGORY_IDS)}\n"
                    f"**Hardcoded Channels:** {len(ShameConfig.BLACKLISTED_CHANNEL_IDS)}"
                ),
                inline=False
            )
            
            embed.set_footer(text="To modify blacklists, edit config.py and restart the bot")
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
        except Exception as e:
            await interaction.response.send_message(
                f"❌ Error retrieving tracking info: {str(e)}", 
                ephemeral=True
            )

    @shame.command(name="send", description="Force send the leaderboard immediately (Admin only)")
    @app_commands.choices(mode=[
        app_commands.Choice(name="Full Rotation (Leaderboard + Roles)", value="full"),
        app_commands.Choice(name="Leaderboard Only", value="leaderboard"),
        app_commands.Choice(name="Roles Only", value="roles")
    ])
    async def shame_send(self, interaction: discord.Interaction, mode: app_commands.Choice[str] = None):
        """Force send the leaderboard immediately with optional modes"""
        try:
            # Check admin permissions
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("You need administrator permissions to use this command!", ephemeral=True)
                return
            
            # Default to full rotation
            selected_mode = mode.value if mode else "full"
            
            await interaction.response.defer(ephemeral=True)
            
            # Get top 5 for leaderboard
            leaderboard = await self.db.get_leaderboard(5)
            
            if not leaderboard:
                await interaction.followup.send("❌ No users have points yet!", ephemeral=True)
                return
            
            channel = self.bot.get_channel(ShameConfig.LEADERBOARD_CHANNEL_ID)
            if not channel:
                await interaction.followup.send("❌ Leaderboard channel not found!", ephemeral=True)
                return
            
            messages_sent = []
            roles_assigned = False
            
            # Send leaderboard if requested
            if selected_mode in ["full", "leaderboard"]:
                # Create leaderboard embed
                embed = discord.Embed(
                    title="🏆 Hall of Shame - Leaderboard Update",
                    color=discord.Color.gold(),
                    timestamp=discord.utils.utcnow()
                )
                
                symbols = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
                
                for i, user_data in enumerate(leaderboard):
                    user = self.bot.get_user(user_data['user_id'])
                    username = user.display_name if user else f"Unknown User ({user_data['user_id']})"
                    
                    symbol = symbols[i] if i < len(symbols) else "•"
                    
                    embed.add_field(
                        name=f"{symbol} {username}",
                        value=f"**{user_data['points']}** points • {user_data['total_messages']} messages",
                        inline=False
                    )
                
                # Calculate next rotation time
                next_reroll = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=4)
                time_until_reroll = next_reroll - datetime.now()
                days = time_until_reroll.days
                hours = time_until_reroll.seconds // 3600
                
                embed.set_footer(text=f"You can do better gng, rerolling in {days}d {hours}h")
                
                # Send leaderboard
                leaderboard_msg = await channel.send(embed=embed)
                messages_sent.append(f"Leaderboard: [Message {leaderboard_msg.id}]({leaderboard_msg.jump_url})")
                
                # Send role announcement if full mode
                if selected_mode == "full":
                    top_5_ids = [user['user_id'] for user in leaderboard[:5]]
                    winners_mentions = [f"<@{user_id}>" for user_id in top_5_ids]
                    
                    role_embed = discord.Embed(
                        title="🎉 New Shamers!",
                        description=f"Congratulations to {', '.join(winners_mentions)} for earning the shame role!",
                        color=discord.Color.orange()
                    )
                    
                    role_msg = await channel.send(f"<@&{ShameConfig.SHAME_ROLE_ID}>", embed=role_embed)
                    messages_sent.append(f"Role Announcement: [Message {role_msg.id}]({role_msg.jump_url})")
            
            # Assign roles if requested
            if selected_mode in ["full", "roles"]:
                role_assignment_success = True
                for guild in self.bot.guilds:
                    top_5_ids = [user['user_id'] for user in leaderboard[:5]]
                    try:
                        await self.utils.assign_shame_role(guild, top_5_ids)
                        roles_assigned = True
                    except Exception as e:
                        role_assignment_success = False
                        print(f"[Shamer] Error assigning roles: {e}")
            
            # Update database
            await self.db.set_system_state('last_leaderboard', datetime.now().isoformat())
            await self.db.set_system_state('last_leaderboard_sent', datetime.now().isoformat())
            
            # Build success message
            result_message = "✅ **Force Send Complete!**\n"
            
            if messages_sent:
                result_message += f"**Messages Sent:**\n" + "\n".join(messages_sent) + "\n\n"
            
            if selected_mode in ["full", "roles"]:
                if roles_assigned:
                    result_message += "**Roles:** ✅ Successfully assigned to top 5 users\n"
                else:
                    result_message += "**Roles:** ⚠️ Some role assignments may have failed\n"
            
            result_message += f"\n**Mode:** {selected_mode.upper()}\n"
            result_message += f"**Time:** <t:{int(datetime.now().timestamp())}:F>"
            
            await interaction.followup.send(result_message, ephemeral=True)
            
        except Exception as e:
            await interaction.followup.send(f"❌ Error force sending leaderboard: {str(e)}", ephemeral=True)

    @shame.command(name="status", description="Check when last leaderboard was sent")
    async def shame_status(self, interaction: discord.Interaction):
        """Check if and when the last leaderboard was sent"""
        try:
            last_rotation = await self.db.get_system_state('last_rotation_time')
            last_sent = await self.db.get_system_state('last_leaderboard_sent')
            
            embed = discord.Embed(
                title="🔄 Shame System Status",
                color=discord.Color.blue(),
                timestamp=discord.utils.utcnow()
            )
            
            if last_rotation:
                last_rotation_dt = datetime.fromisoformat(last_rotation)
                embed.add_field(
                    name="🔄 Last Rotation",
                    value=f"<t:{int(last_rotation_dt.timestamp())}:F>\n(<t:{int(last_rotation_dt.timestamp())}:R>)",
                    inline=False
                )
                
                # Calculate next rotation
                next_rotation = last_rotation_dt + timedelta(days=ShameConfig.LEADERBOARD_INTERVAL_DAYS)
                embed.add_field(
                    name="⏰ Next Rotation",
                    value=f"<t:{int(next_rotation.timestamp())}:F>\n(<t:{int(next_rotation.timestamp())}:R>)",
                    inline=False
                )
            else:
                embed.add_field(
                    name="🔄 Last Rotation",
                    value="Never",
                    inline=False
                )
            
            if last_sent:
                last_sent_dt = datetime.fromisoformat(last_sent)
                embed.add_field(
                    name="📅 Last Leaderboard Sent",
                    value=f"<t:{int(last_sent_dt.timestamp())}:F>\n(<t:{int(last_sent_dt.timestamp())}:R>)",
                    inline=False
                )
            else:
                embed.add_field(
                    name="📅 Last Leaderboard Sent", 
                    value="Never",
                    inline=False
                )
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error checking status: {str(e)}", ephemeral=True)


            
    @shame.command(name="rank", description="Check your rank in the shame leaderboard")
    async def shame_rank(self, interaction: discord.Interaction):
        try:
            user_data = await self.db.get_user(interaction.user.id)
            
            if not user_data or user_data['points'] == 0:
                await interaction.response.send_message("You don't have any points yet!", ephemeral=True)
                return
            
            rank = await self.db.get_user_rank(interaction.user.id)
            
            # Calculate time until next reroll (4 days from now)
            now = datetime.now()
            next_reroll = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=4)
            time_until_reroll = next_reroll - now
            
            days = time_until_reroll.days
            hours = time_until_reroll.seconds // 3600
            
            embed = discord.Embed(
                title="Your Shame Rank",
                color=discord.Color.orange(),
                timestamp=discord.utils.utcnow()
            )
            embed.add_field(name="Rank", value=f"#{rank}", inline=True)
            embed.add_field(name="Points", value=user_data['points'], inline=True)
            embed.add_field(name="Total Messages", value=user_data['total_messages'], inline=True)
            
            # Set user avatar as embed thumbnail
            embed.set_thumbnail(url=interaction.user.display_avatar.url)
            
            # Set custom footer
            embed.set_footer(text=f"Top 5 become the next SHAMERS!!, rerolling in {days}d {hours}h")
            
            await interaction.response.send_message(embed=embed)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error checking rank: {str(e)}", ephemeral=True)
    
    @shame.command(name="top", description="Show top 10 in shame leaderboard")
    async def shame_top(self, interaction: discord.Interaction):
        try:
            leaderboard = await self.db.get_leaderboard(10)
            
            if not leaderboard:
                await interaction.response.send_message("No one has any points yet!", ephemeral=True)
                return
            
            # Calculate time until next reroll
            now = datetime.now()
            next_reroll = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=4)
            time_until_reroll = next_reroll - now
            
            days = time_until_reroll.days
            hours = time_until_reroll.seconds // 3600
            
            embed = discord.Embed(
                title="🏆 Hall of Shame - Top 10",
                color=discord.Color.gold(),
                timestamp=discord.utils.utcnow()
            )
            
            # Define symbols for each position
            symbols = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
            
            for i, user_data in enumerate(leaderboard):
                user = self.bot.get_user(user_data['user_id'])
                username = user.display_name if user else f"Unknown User ({user_data['user_id']})"
                
                symbol = symbols[i] if i < len(symbols) else "•"
                
                embed.add_field(
                    name=f"{symbol} {username}",
                    value=f"**{user_data['points']}** points • {user_data['total_messages']} messages",
                    inline=False
                )
            
            embed.set_footer(text=f"You can do better gng, rerolling in {days}d {hours}h")
            
            await interaction.response.send_message(embed=embed)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error showing leaderboard: {str(e)}", ephemeral=True)
    

    @shame.command(name="hard_reset", description="🚨 COMPLETELY reset leaderboard and all user data (Admin only)")
    @app_commands.choices(reset_type=[
        app_commands.Choice(name="Full Reset - All user data + blacklist", value="full"),
        app_commands.Choice(name="Points Only - Keep users, reset points", value="points"),
        app_commands.Choice(name="Messages Only - Reset message counts", value="messages"),
        app_commands.Choice(name="Points & Messages - Reset both", value="points_messages"),
        app_commands.Choice(name="Leaderboard Only - Reset rotation timers", value="leaderboard"),
    ])
    async def shame_hard_reset(self, interaction: discord.Interaction, reset_type: app_commands.Choice[str]):
        """🚨 COMPLETELY reset the leaderboard system"""
        try:
            # Check admin permissions
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message(
                    "❌ You need administrator permissions to use this command!", 
                    ephemeral=True
                )
                return

            # Send confirmation modal/button for dangerous operation
            embed = discord.Embed(
                title="🚨 HARD RESET CONFIRMATION",
                description=(
                    f"**You are about to perform a {reset_type.name.upper()} RESET**\n\n"
                    f"⚠️ **THIS ACTION IS IRREVERSIBLE!** ⚠️\n\n"
                ),
                color=discord.Color.red()
            )

            # Add consequences based on reset type
            if reset_type.value == "full":
                embed.add_field(
                    name="🗑️ What will be deleted:",
                    value=(
                        "• All user points and statistics\n"
                        "• All user message counts\n" 
                        "• Entire blacklist\n"
                        "• All system timestamps\n"
                        "• EVERYTHING - Fresh start"
                    ),
                    inline=False
                )
            elif reset_type.value == "points":
                embed.add_field(
                    name="🔄 What will be reset:",
                    value=(
                        "• All user points set to 0\n"
                        "• Hourly points reset to 0\n"
                        "• Message counts preserved\n"
                        "• Blacklist preserved\n"
                        "• Users remain in database"
                    ),
                    inline=False
                )
            elif reset_type.value == "messages":
                embed.add_field(
                    name="📊 What will be reset:",
                    value=(
                        "• All user message counts set to 0\n"
                        "• Points preserved\n"
                        "• Hourly points preserved\n"
                        "• Blacklist preserved\n"
                        "• Users remain in database"
                    ),
                    inline=False
                )
            elif reset_type.value == "points_messages":
                embed.add_field(
                    name="🔄📊 What will be reset:",
                    value=(
                        "• All user points set to 0\n"
                        "• All user message counts set to 0\n"
                        "• Hourly points reset to 0\n"
                        "• Blacklist preserved\n"
                        "• Users remain in database"
                    ),
                    inline=False
                )
            else:  # leaderboard
                embed.add_field(
                    name="⏰ What will be reset:",
                    value=(
                        "• Leaderboard rotation timers\n"
                        "• Last sent timestamps\n"
                        "• User data preserved\n"
                        "• Blacklist preserved\n"
                        "• Forces immediate new rotation"
                    ),
                    inline=False
                )

            embed.add_field(
                name="🔐 Confirmation Required:",
                value=f"Click **CONFIRM RESET** below to proceed.\nThis action will affect **{len(await self.db.get_leaderboard(10000))}** users.",
                inline=False
            )

            embed.set_footer(text="This cannot be undone!")

            # Create confirmation view
            view = ConfirmResetView(self.db, reset_type.value, interaction.user)

            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

        except Exception as e:
            await interaction.response.send_message(
                f"❌ Error preparing reset: {str(e)}", 
                ephemeral=True
            )


    @shame.command(name="stats", description="Check your detailed shame statistics")
    async def shame_stats(self, interaction: discord.Interaction):
        try:
            user_data = await self.db.get_user(interaction.user.id)
            
            if not user_data:
                user_data = {'points': 0, 'total_messages': 0, 'hourly_points': 0}
            
            rank = await self.db.get_user_rank(interaction.user.id)
            is_blacklisted = await self.db.is_blacklisted(
                interaction.user.id, 
                [role.id for role in interaction.user.roles]
            )
            
            # Calculate time until next reroll
            now = datetime.now()
            next_reroll = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=4)
            time_until_reroll = next_reroll - now
            
            days = time_until_reroll.days
            hours = time_until_reroll.seconds // 3600
            
            embed = discord.Embed(
                title="Your Shame Statistics",
                color=discord.Color.green() if not is_blacklisted else discord.Color.red(),
                timestamp=discord.utils.utcnow()
            )
            
            embed.add_field(name="Rank", value=f"#{rank if rank > 0 else 'N/A'}", inline=True)
            embed.add_field(name="Total Points", value=user_data['points'], inline=True)
            embed.add_field(name="Total Messages", value=user_data['total_messages'], inline=True)
            embed.add_field(name="Status", value="Blacklisted ❌" if is_blacklisted else "Slayyin! ✅", inline=True)
            
            # Set user avatar as embed thumbnail
            embed.set_thumbnail(url=interaction.user.display_avatar.url)
            
            # Set custom footer
            embed.set_footer(text=f"You can do better gng, rerolling in {days}d {hours}h")
            
            await interaction.response.send_message(embed=embed)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error checking stats: {str(e)}", ephemeral=True)

    # Blacklist commands as a separate group (not nested)
    blacklist_group = app_commands.Group(name="shame_blacklist", description="Manage blacklist for shame system")
    
    @blacklist_group.command(name="add", description="Add a user or role to blacklist")
    @app_commands.describe(target="User or role to blacklist")
    async def blacklist_add(self, interaction: discord.Interaction, target: Union[discord.Member, discord.Role]):
        try:
            # Check admin permissions
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("You need administrator permissions to use this command!", ephemeral=True)
                return
            
            entity_type = "user" if isinstance(target, discord.Member) else "role"
            
            await self.db.add_to_blacklist(target.id, entity_type)
            await interaction.response.send_message(f"✅ {entity_type.capitalize()} {target.mention} has been added to blacklist!")
        except Exception as e:
            await interaction.response.send_message(f"❌ Error adding to blacklist: {str(e)}", ephemeral=True)
    
    @blacklist_group.command(name="remove", description="Remove a user or role from blacklist")
    @app_commands.describe(target="User or role to remove from blacklist")
    async def blacklist_remove(self, interaction: discord.Interaction, target: Union[discord.Member, discord.Role]):
        try:
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("You need administrator permissions to use this command!", ephemeral=True)
                return
            
            await self.db.remove_from_blacklist(target.id)
            await interaction.response.send_message(f"✅ {target.mention} has been removed from blacklist!")
        except Exception as e:
            await interaction.response.send_message(f"❌ Error removing from blacklist: {str(e)}", ephemeral=True)
    
    @blacklist_group.command(name="list", description="Show all blacklisted users and roles")
    async def blacklist_list(self, interaction: discord.Interaction):
        try:
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("You need administrator permissions to use this command!", ephemeral=True)
                return
            
            blacklist = await self.db.get_blacklist()
            
            if not blacklist:
                await interaction.response.send_message("No users or roles are blacklisted!", ephemeral=True)
                return
            
            embed = discord.Embed(title="Blacklisted Users/Roles", color=discord.Color.red())
            
            users = []
            roles = []
            
            for item in blacklist:
                if item['entity_type'] == 'user':
                    user = self.bot.get_user(item['entity_id'])
                    users.append(user.mention if user else f"Unknown User ({item['entity_id']})")
                else:
                    role = interaction.guild.get_role(item['entity_id'])
                    roles.append(role.mention if role else f"Unknown Role ({item['entity_id']})")
            
            if users:
                embed.add_field(name="Users", value="\n".join(users), inline=False)
            if roles:
                embed.add_field(name="Roles", value="\n".join(roles), inline=False)
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error listing blacklist: {str(e)}", ephemeral=True)
    
    @blacklist_group.command(name="clear", description="Clear all blacklisted items")
    async def blacklist_clear(self, interaction: discord.Interaction):
        try:
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("You need administrator permissions to use this command!", ephemeral=True)
                return
            
            # Clear all blacklist entries
            async with aiosqlite.connect(self.db.db_path) as db:
                await db.execute('DELETE FROM blacklist')
                await db.commit()
            
            await interaction.response.send_message("✅ All blacklisted items have been cleared!")
        except Exception as e:
            await interaction.response.send_message(f"❌ Error clearing blacklist: {str(e)}", ephemeral=True)