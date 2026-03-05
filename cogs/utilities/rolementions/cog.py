import discord
from discord.ext import commands
import pytz
from datetime import datetime, timedelta

from .data_manager import DataManager
from .tracker import RoleMentionTracker
from .automod_manager import AutoModManager


class RoleMentionCog(commands.Cog):  # ← Better name
    def __init__(self, bot):
        self.bot = bot

        # HARDCODED LOG CHANNEL ID
        self.LOG_CHANNEL_ID = 1447323258478133298

        # Initialize managers
        self.data_manager = DataManager()
        self.tracker = RoleMentionTracker(self)
        self.automod = AutoModManager(self)

        print(f"[RoleMentions] Cog loaded with log channel ID: {self.LOG_CHANNEL_ID}")

    def cog_unload(self):
        """Cleanup when cog is unloaded"""
        self.tracker.cog_unload()
        self.automod.cog_unload()

    def get_log_channel(self):
        """Get the log channel"""
        return self.bot.get_channel(self.LOG_CHANNEL_ID)

    async def log_role_mentions(self, message: discord.Message, mentioned_roles):
        """Log role mentions to hardcoded channel"""
        log_channel = self.get_log_channel()

        if not log_channel:
            print(f"Error: Log channel with ID {self.LOG_CHANNEL_ID} not found!")
            return

        guild_id = str(message.guild.id)
        stats = self.tracker.get_guild_stats(guild_id)

        # Get list of unique roles mentioned today with their counts
        unique_roles_info = []
        for role_id, count in stats["roles"].items():
            role_obj = message.guild.get_role(int(role_id))
            if role_obj:
                unique_roles_info.append(f"{role_obj.mention} ({count})")
            else:
                unique_roles_info.append(f"Deleted Role ({role_id}) ({count})")

        # Create detailed embed
        embed = discord.Embed(
            title="📢 New Role Mention(s) Detected",
            color=discord.Color.blue(),
            timestamp=datetime.now(pytz.UTC),
        )

        # Add author info
        embed.set_author(
            name=f"{message.author.name} ({message.author.id})",
            icon_url=message.author.avatar.url if message.author.avatar else None,
        )

        # Add message link
        embed.add_field(
            name="Message Link",
            value=f"[Jump to Message]({message.jump_url})",
            inline=True,
        )

        # Add message content preview
        content_preview = (
            message.content[:200] + "..."
            if len(message.content) > 200
            else message.content
        )
        if not content_preview.strip():
            content_preview = "*[No text content]*"

        embed.add_field(
            name="Message Content", value=f"```{content_preview}```", inline=False
        )

        # Add mentioned roles section
        roles_text = ""
        for role in mentioned_roles:
            role_count = self.tracker.get_role_count(guild_id, str(role.id))
            roles_text += f"• {role.mention} - **{role_count}** mention{'s' if role_count != 1 else ''} today\n"

        embed.add_field(
            name=f"Mentioned Roles ({len(mentioned_roles)})",
            value=roles_text or "*No roles mentioned*",
            inline=False,
        )

        # Add statistics
        stats_text = f"**Total Role Mentions:** {stats['total']}\n"
        stats_text += f"**Unique Roles Mentioned:** {stats['unique']}\n\n"

        if unique_roles_info:
            if len(unique_roles_info) <= 5:
                stats_text += "**Roles mentioned today:**\n"
                for role_info in unique_roles_info:
                    stats_text += f"• {role_info}\n"
            else:
                stats_text += "**Roles mentioned today (first 5):**\n"
                for role_info in unique_roles_info[:5]:
                    stats_text += f"• {role_info}\n"
                stats_text += f"*...and {stats['unique'] - 5} more*"
        else:
            stats_text += "**No roles mentioned yet today**"

        embed.add_field(name="📊 Today's Statistics", value=stats_text, inline=False)

        # Add command info
        command_usage = ""
        if message.content.startswith("."):
            command_usage = f"**Command Detected:** `{message.content.split()[0]}`\n"

        embed.add_field(
            name="⚙️ Available Commands",
            value=f"{command_usage}**`.rm stats [role]`** - Get role mention statistics\n"
            f"**`.rm top [limit]`** - See most mentioned roles\n"
            f"**`.rm reset`** - Reset counters (Admin only)\n"
            f"**`.automod list`** - View active blocks",
            inline=True,
        )

        embed.set_footer(text=f"Logged at")

        # Send the log message
        await log_channel.send(content=f"{message.author.mention}", embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Track role mentions in messages"""
        if message.author.bot:
            return

        if not isinstance(message.channel, discord.TextChannel):
            return

        if message.role_mentions:
            guild_id = str(message.guild.id)
            has_new_mentions = False

            for role in message.role_mentions:
                # Increment counter
                self.tracker.increment_mentions(guild_id, str(role.id))
                has_new_mentions = True

                # Check if this role should trigger auto-mod blocking
                await self.automod.check_and_block_role(message, role)

            if has_new_mentions:
                # Log the mention
                await self.log_role_mentions(message, message.role_mentions)

    @commands.group(name="rolementions", aliases=["rm"], invoke_without_command=True)
    @commands.has_permissions(manage_messages=True)
    async def role_mentions_group(self, ctx):
        """Get role mention statistics"""
        embed = discord.Embed(
            title="📊 Role Mention Tracker",
            description="Track and log all role mentions in your server",
            color=discord.Color.blue(),
        )

        embed.add_field(
            name="Available Commands",
            value=(
                "**`.rm stats [role]`** - Get role mention statistics\n"
                "**`.rm top [limit]`** - Get top mentioned roles (default: 5)\n"
                "**`.rm reset`** - Manually reset counters (Admin only)\n"
                "**`.automod list`** - View active auto-mod blocks"
            ),
            inline=False,
        )

        embed.add_field(
            name="Features",
            value=(
                "• Automatically logs every role mention\n"
                "• Provides real-time statistics\n"
                "• Daily reset at 4 AM IST\n"
                "• Auto-mod blocks for sensitive roles\n"
                "• All logs sent to a dedicated channel"
            ),
            inline=False,
        )

        embed.add_field(
            name="Current Log Channel", value=f"<#{self.LOG_CHANNEL_ID}>", inline=True
        )

        embed.set_footer(text="Requires 'Manage Server' permission")
        await ctx.send(embed=embed)

    @role_mentions_group.command(name="stats")
    @commands.has_permissions(manage_messages=True)
    async def role_mentions_stats(self, ctx, role: discord.Role = None):
        """Get role mention statistics for this server"""
        guild_id = str(ctx.guild.id)
        stats = self.tracker.get_guild_stats(guild_id)

        if stats["total"] == 0:
            await ctx.send("No role mentions recorded yet today!")
            return

        if role:
            # Get specific role stats
            role_count = self.tracker.get_role_count(guild_id, str(role.id))
            embed = discord.Embed(
                title=f"Mention Stats for {role.name}", color=role.color
            )
            embed.add_field(name="Today's Mentions", value=str(role_count), inline=True)
            embed.add_field(name="Role ID", value=f"`{role.id}`", inline=True)
            embed.set_footer(text=f"Data resets daily at 4 AM IST")

        else:
            # Get all role stats
            embed = discord.Embed(
                title=f"Role Mention Statistics",
                description=f"**Total mentions today:** `{stats['total']}`\n"
                f"**Unique roles mentioned:** `{stats['unique']}`",
                color=discord.Color.blue(),
            )

            # Show all roles with their mention counts
            roles_list = []
            for role_id, count in stats["roles"].items():
                role_obj = ctx.guild.get_role(int(role_id))
                if role_obj:
                    roles_list.append((role_obj.name, count, role_obj.id))
                else:
                    roles_list.append((f"Deleted Role ({role_id})", count, role_id))

            roles_list.sort(key=lambda x: x[1], reverse=True)

            roles_list_text = ""
            for i, (role_name, count, role_id) in enumerate(roles_list[:15], 1):
                roles_list_text += f"{i}. **{role_name}** - {count} mention{'s' if count != 1 else ''} (ID: `{role_id}`)\n"

            embed.add_field(
                name="Roles Mentioned Today",
                value=roles_list_text if roles_list_text else "*No roles mentioned*",
                inline=False,
            )

            if len(roles_list) > 15:
                embed.set_footer(
                    text=f"Showing 15 of {len(roles_list)} roles | Resets daily at 4 AM IST"
                )
            else:
                embed.set_footer(text="Data resets daily at 4 AM IST")

        await ctx.send(embed=embed)

    @role_mentions_group.command(name="top")
    @commands.has_permissions(manage_messages=True)
    async def role_mentions_top(self, ctx, limit: int = 5):
        """Get top mentioned roles (default: top 5)"""
        if limit < 1 or limit > 25:
            await ctx.send("Please specify a limit between 1 and 25")
            return

        guild_id = str(ctx.guild.id)
        stats = self.tracker.get_guild_stats(guild_id)

        if stats["total"] == 0:
            await ctx.send("No role mentions recorded yet today!")
            return

        # Get all roles with counts
        roles_data = []
        for role_id, count in stats["roles"].items():
            role_obj = ctx.guild.get_role(int(role_id))
            if role_obj:
                roles_data.append((role_obj.name, count, role_obj.id, role_obj))
            else:
                roles_data.append((f"Deleted Role ({role_id})", count, role_id, None))

        # Sort by count
        roles_data.sort(key=lambda x: x[1], reverse=True)
        top_roles = roles_data[:limit]

        embed = discord.Embed(
            title=f"Top {limit} Most Mentioned Roles", color=discord.Color.gold()
        )

        # Add each top role
        for i, (role_name, count, role_id, role_obj) in enumerate(top_roles, 1):
            if role_obj:
                role_display = role_obj.mention
            else:
                role_display = f"**{role_name}**"

            embed.add_field(
                name=f"{i}. {role_name}",
                value=f"{role_display}\n**{count}** mention{'s' if count != 1 else ''}\nID: `{role_id}`",
                inline=False,
            )

        embed.add_field(
            name="📊 Summary",
            value=f"**Total Mentions Today:** {stats['total']}\n"
            f"**Unique Roles Mentioned:** {stats['unique']}",
            inline=False,
        )

        # List all unique roles if less than 10
        if stats["unique"] <= 10:
            all_roles_text = ""
            for role_name, count, role_id, role_obj in roles_data:
                all_roles_text += f"• {role_name} ({count})\n"

            embed.add_field(
                name="All Roles Mentioned Today", value=all_roles_text, inline=False
            )
        else:
            embed.add_field(
                name="All Roles Mentioned Today",
                value=f"{stats['unique']} unique roles have been mentioned today. Use `.rm stats` to see all.",
                inline=False,
            )

        embed.set_footer(text=f"Resets daily at 4 AM IST")

        await ctx.send(embed=embed)

    @role_mentions_group.command(name="reset")
    @commands.has_permissions(administrator=True)
    async def role_mentions_reset(self, ctx):
        """Manually reset all role mention counters for this server (Admin only)"""
        guild_id = str(ctx.guild.id)

        self.tracker.reset_guild(guild_id)

        # Log the manual reset
        log_channel = self.get_log_channel()
        if log_channel:
            embed = discord.Embed(
                title="🔄 Manual Role Mention Reset",
                description=f"Role mention counters have been manually reset",
                color=discord.Color.orange(),
            )
            embed.add_field(
                name="Reset By",
                value=f"{ctx.author.mention} ({ctx.author.id})",
                inline=True,
            )
            embed.set_footer(
                text=f"Reset at {datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%Y-%m-%d %H:%M:%S')} IST"
            )
            await log_channel.send(embed=embed)

        await ctx.send("✅ Role mention counters have been reset for this server!")

    @commands.group(name="automod", aliases=["am"], invoke_without_command=True)
    @commands.has_permissions(manage_emojis=True)
    async def automod(self, ctx):
        """Manage auto-mod protection for roles"""
        embed = discord.Embed(
            title="🛡️ Auto-Mod Manager",
            description="Dynamic role protection system",
            color=discord.Color.blue(),
        )

        # Format protected roles list
        protected_list = ""
        for role_id, minutes in list(self.automod.PROTECTED_ROLES.items())[:5]:
            if minutes >= 60:
                duration_display = f"{minutes/60:.1f}h"
            else:
                duration_display = f"{minutes}m"
            role = ctx.guild.get_role(int(role_id))
            if role:
                protected_list += f"• {role.mention} - {duration_display}\n"
            else:
                protected_list += f"• Unknown Role ({role_id}) - {duration_display}\n"

        if len(self.automod.PROTECTED_ROLES) > 5:
            protected_list += f"*...and {len(self.automod.PROTECTED_ROLES) - 5} more*"

        embed.add_field(
            name="Protected Roles (with durations)",
            value=protected_list or "None configured",
            inline=False,
        )

        embed.add_field(
            name="Current Status",
            value=f"Currently Blocked: {len(self.automod.active_blocks)}\n"
            f"Mention Limit: {self.automod.MENTION_LIMIT} per message\n"
            f"Default Duration: {self.automod.DEFAULT_DURATION} minutes",
            inline=False,
        )

        # Show currently blocked roles with remaining time
        if self.automod.active_blocks:
            blocks_list = ""
            for role_id, block_data in list(self.automod.active_blocks.items())[:5]:
                expires = block_data["expires_at"]
                remaining = self.automod.get_remaining_time(expires)
                duration_minutes = block_data.get("duration_minutes", 120)
                role = ctx.guild.get_role(int(role_id))

                if duration_minutes >= 60:
                    duration_display = f"{duration_minutes/60:.1f}h"
                else:
                    duration_display = f"{duration_minutes}m"

                if role:
                    blocks_list += f"• {role.mention} - {duration_display} (remaining: {remaining})\n"
                else:
                    blocks_list += f"• Unknown Role ({role_id}) - {duration_display} (remaining: {remaining})\n"

            if len(self.automod.active_blocks) > 5:
                blocks_list += f"*...and {len(self.automod.active_blocks) - 5} more*"

            embed.add_field(
                name="Currently Blocked Roles", value=blocks_list, inline=False
            )

        embed.add_field(
            name="Commands",
            value=(
                "**`.automod list`** - List blocked roles\n"
                "**`.automod remove <role>`** - Manually remove a role from protection\n"
                "**`.automod allow <role>`** - Prematurely allow mentions of a role (alias for remove)\n"
                "**`.automod add <role> [duration]`** - Manually add a role (duration in minutes or '2h')\n"
                "**`.automod reload`** - Reload config from environment"
            ),
            inline=False,
        )

        await ctx.send(embed=embed)

    @automod.command(name="list")
    @commands.has_permissions(manage_emojis=True)
    async def automod_list(self, ctx):
        """List all currently blocked roles"""
        if not self.automod.active_blocks:
            await ctx.send("No roles are currently blocked.")
            return

        embed = discord.Embed(
            title=f"Currently Blocked Roles ({len(self.automod.active_blocks)})",
            color=discord.Color.orange(),
        )

        for role_id, block_data in self.automod.active_blocks.items():
            role = ctx.guild.get_role(int(role_id))
            role_mention = role.mention if role else f"Unknown Role ({role_id})"
            expires = block_data["expires_at"]
            remaining = self.automod.get_remaining_time(expires)
            duration_minutes = block_data.get("duration_minutes", 120)

            if duration_minutes >= 60:
                duration_display = f"{duration_minutes/60:.1f} hours"
            else:
                duration_display = f"{duration_minutes} minutes"

            embed.add_field(
                name=f"Role: {block_data.get('role_name', 'Unknown')}",
                value=(
                    f"**Role:** {role_mention}\n"
                    f"**Duration:** {duration_display}\n"
                    f"**Remaining:** {remaining}\n"
                    f"**Expires:** <t:{int(expires.timestamp())}:R>\n"
                    f"**Added:** <t:{int(block_data['created_at'].timestamp())}:R>"
                ),
                inline=False,
            )

        await ctx.send(embed=embed)

    @automod.command(name="add")
    @commands.has_permissions(manage_guild=True)
    async def automod_add(self, ctx, role: discord.Role, duration: str = None):
        """Manually add a role to protection
        Duration can be in minutes (60) or hours (2h, 3h)
        """
        if str(role.id) in self.automod.active_blocks:
            await ctx.send(f"❌ Role {role.mention} is already blocked!")
            return

        # Parse duration
        if duration:
            duration_str = duration.lower()
            if duration_str.endswith("h"):
                try:
                    duration_minutes = int(duration_str[:-1]) * 60
                except ValueError:
                    await ctx.send(
                        "❌ Invalid duration format! Use numbers like '60' for minutes or '2h' for hours."
                    )
                    return
            else:
                try:
                    duration_minutes = int(duration_str)
                except ValueError:
                    await ctx.send(
                        "❌ Invalid duration format! Use numbers like '60' for minutes or '2h' for hours."
                    )
                    return
        else:
            duration_minutes = self.automod.DEFAULT_DURATION

        block_data = {
            "guild_id": str(ctx.guild.id),
            "role_id": str(role.id),
            "role_name": role.name,
            "created_at": datetime.now(self.automod.ist),
            "expires_at": datetime.now(self.automod.ist)
            + timedelta(minutes=duration_minutes),
            "duration_minutes": duration_minutes,
            "trigger_message_id": None,
            "trigger_user_id": str(ctx.author.id),
            "manually_added": True,
        }

        self.automod.active_blocks[str(role.id)] = block_data
        self.data_manager.save_automod_rules(self.automod.active_blocks)

        # Update the main rule
        await self.automod.update_rule_keywords()

        # Log the manual addition
        await self.automod.log_block_created(ctx.guild, role, duration_minutes)

        # Format duration for message
        if duration_minutes >= 60:
            duration_display = f"{duration_minutes/60:.1f} hours"
        else:
            duration_display = f"{duration_minutes} minutes"

        await ctx.send(
            f"✅ Role {role.mention} has been added to protection for {duration_display}!"
        )

    @automod.command(name="remove")
    @commands.has_permissions(manage_guild=True)
    async def automod_remove(self, ctx, role: discord.Role):
        """Manually remove a role from protection"""
        if str(role.id) not in self.automod.active_blocks:
            await ctx.send(f"❌ Role {role.mention} is not currently blocked!")
            return

        block_data = self.automod.active_blocks[str(role.id)]
        del self.automod.active_blocks[str(role.id)]
        self.data_manager.save_automod_rules(self.automod.active_blocks)

        # Update the main rule
        await self.automod.update_rule_keywords()

        # Log the manual removal
        log_channel = self.get_log_channel()
        if log_channel:
            embed = discord.Embed(
                title="🛠️ Role Protection Manually Removed",
                description=f"Role was removed by {ctx.author.mention}",
                color=discord.Color.purple(),
            )
            embed.add_field(name="Role", value=role.mention, inline=True)
            embed.add_field(
                name="Originally Added",
                value=f"<t:{int(block_data['created_at'].timestamp())}:R>",
                inline=True,
            )
            embed.add_field(
                name="Removed Early",
                value=f"Removed with {self.automod.get_remaining_time(block_data['expires_at'])} remaining",
                inline=True,
            )
            await log_channel.send(embed=embed)

        # Format the remaining time for the response
        remaining = self.automod.get_remaining_time(block_data["expires_at"])
        await ctx.send(
            f"✅ Role {role.mention} has been removed from protection early! (Had {remaining} remaining)"
        )

    @automod.command(name="allow")
    @commands.has_permissions(manage_guild=True)
    async def automod_allow(self, ctx, role: discord.Role):
        """Prematurely allow mentions of a role (alias for remove)"""
        await self.automod_remove(ctx, role)

    @automod.command(name="reload")
    @commands.has_permissions(administrator=True)
    async def automod_reload(self, ctx):
        """Reload configuration from environment variables"""
        try:
            old_protected = self.automod.PROTECTED_ROLES.copy()
            old_limit = self.automod.MENTION_LIMIT
            old_default = self.automod.DEFAULT_DURATION

            self.automod.load_config_from_env()

            embed = discord.Embed(
                title="🔄 Auto-Mod Config Reloaded", color=discord.Color.green()
            )

            embed.add_field(
                name="Protected Roles",
                value=f"Old: {len(old_protected)}\nNew: {len(self.automod.PROTECTED_ROLES)}",
                inline=True,
            )

            embed.add_field(
                name="Mention Limit",
                value=f"Old: {old_limit}\nNew: {self.automod.MENTION_LIMIT}",
                inline=True,
            )

            embed.add_field(
                name="Default Duration",
                value=f"Old: {old_default}m\nNew: {self.automod.DEFAULT_DURATION}m",
                inline=True,
            )

            await ctx.send(embed=embed)

        except Exception as e:
            await ctx.send(f"❌ Error reloading config: {str(e)}")
