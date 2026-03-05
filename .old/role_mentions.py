import discord
from discord.ext import commands, tasks
import json
import os
from datetime import datetime, timedelta
import pytz
from collections import defaultdict
import asyncio


class RoleMentionTracker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.data_file = "data/role_mentions.json"
        self.ist = pytz.timezone("Asia/Kolkata")

        # Load existing data or initialize
        self.role_mentions = self.load_data()

        # HARDCODED LOG CHANNEL ID
        self.LOG_CHANNEL_ID = 1447323258478133298

        # Start the daily reset task
        self.daily_reset.start()

    def cog_unload(self):
        """Cleanup when cog is unloaded"""
        self.daily_reset.cancel()
        self.save_data()

    def load_data(self):
        """Load role mention data from file"""
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, "r") as f:
                    data = json.load(f)

                    # Convert the loaded data back to defaultdict
                    guild_data = {}
                    for guild_id, roles in data.items():
                        guild_data[guild_id] = defaultdict(int, roles)

                    return defaultdict(lambda: defaultdict(int), guild_data)
            except:
                return defaultdict(lambda: defaultdict(int))
        return defaultdict(lambda: defaultdict(int))

    def save_data(self):
        """Save role mention data to file"""
        # Convert defaultdict to regular dict for JSON serialization
        serializable_data = {}
        for guild_id, roles in dict(self.role_mentions).items():
            serializable_data[guild_id] = dict(roles)

        with open(self.data_file, "w") as f:
            json.dump(serializable_data, f, indent=4)

    @tasks.loop(minutes=1)
    async def daily_reset(self):
        """Reset counters daily at 4 AM IST"""
        now = datetime.now(self.ist)

        # Check if it's 4 AM IST
        if now.hour == 4 and now.minute == 0:
            # Log the reset to hardcoded channel
            try:
                log_channel = self.bot.get_channel(self.LOG_CHANNEL_ID)
                if log_channel:
                    embed = discord.Embed(
                        title="📊 Daily Role Mention Reset",
                        description="All role mention counters have been reset for the new day.",
                        color=discord.Color.green(),
                    )
                    embed.add_field(
                        name="Reset Time",
                        value=f"{now.strftime('%Y-%m-%d %H:%M:%S')} IST",
                    )
                    embed.set_footer(text="Counters reset daily at 4:00 AM IST")
                    await log_channel.send(embed=embed)
            except:
                pass

            # Reset all counters
            self.role_mentions.clear()
            self.save_data()

            # Log the reset
            print(
                f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] Role mention counters reset at 4 AM IST"
            )

    @daily_reset.before_loop
    async def before_daily_reset(self):
        """Wait until bot is ready and sync to 4 AM IST"""
        await self.bot.wait_until_ready()

        # Calculate time until next 4 AM IST
        now = datetime.now(self.ist)
        target = now.replace(hour=4, minute=0, second=0, microsecond=0)

        # If it's already past 4 AM, schedule for tomorrow
        if now >= target:
            target += timedelta(days=1)

        # Wait until 4 AM IST
        await discord.utils.sleep_until(target.astimezone(pytz.UTC))

    async def log_role_mentions(self, message: discord.Message, mentioned_roles):
        """Log role mentions to hardcoded channel"""
        # Get the hardcoded log channel
        log_channel = self.bot.get_channel(self.LOG_CHANNEL_ID)

        if not log_channel:
            print(f"Error: Log channel with ID {self.LOG_CHANNEL_ID} not found!")
            return

        guild_id = str(message.guild.id)

        # Get total mentions for this guild
        total_mentions = sum(self.role_mentions[guild_id].values())

        # Get list of unique roles mentioned today with their counts
        unique_roles_info = []
        for role_id, count in self.role_mentions[guild_id].items():
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

        # Add message content preview (truncate if too long)
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
            role_count = self.role_mentions[guild_id].get(str(role.id), 0)
            roles_text += f"• {role.mention} - **{role_count}** mention{'s' if role_count != 1 else ''} today\n"

        embed.add_field(
            name=f"Mentioned Roles ({len(mentioned_roles)})",
            value=roles_text or "*No roles mentioned*",
            inline=False,
        )

        # Add current statistics
        stats_text = f"**Total Role Mentions:** {total_mentions}\n"
        stats_text += (
            f"**Unique Roles Mentioned:** {len(self.role_mentions[guild_id])}\n\n"
        )

        # Show unique roles with counts
        if unique_roles_info:
            # Show all roles if 5 or fewer, otherwise show first 5 with "and X more"
            if len(unique_roles_info) <= 5:
                stats_text += "**Roles mentioned today:**\n"
                for role_info in unique_roles_info:
                    stats_text += f"• {role_info}\n"
            else:
                stats_text += "**Roles mentioned today (first 5):**\n"
                for role_info in unique_roles_info[:5]:
                    stats_text += f"• {role_info}\n"
                stats_text += f"*...and {len(unique_roles_info) - 5} more*"
        else:
            stats_text += "**No roles mentioned yet today**"

        embed.add_field(name="📊 Today's Statistics", value=stats_text, inline=False)

        # Add command usage info
        command_usage = ""
        if message.content.startswith("."):
            command_usage = f"**Command Detected:** `{message.content.split()[0]}`\n"

        embed.add_field(
            name="⚙️ Available Commands",
            value=f"{command_usage}**`.rm stats [role]`** - Get role mention statistics\n"
            f"**`.rm top [limit]`** - See most mentioned roles\n"
            f"**`.rm reset`** - Reset counters (Admin only)",
            inline=True,
        )

        embed.set_footer(text=f"Logged at")

        # Send the log message
        await log_channel.send(content=f"{message.author.mention}", embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Track role mentions in messages"""
        # Ignore bot messages
        if message.author.bot:
            return

        # Ignore non-text channels
        if not isinstance(message.channel, discord.TextChannel):
            return

        # Check if message contains role mentions
        if message.role_mentions:
            guild_id = str(message.guild.id)
            has_new_mentions = False

            for role in message.role_mentions:
                # Increment counter for each mentioned role
                self.role_mentions[guild_id][str(role.id)] += 1
                has_new_mentions = True

            # Save data after update
            if has_new_mentions:
                self.save_data()

                # Log the mention to hardcoded channel
                await self.log_role_mentions(message, message.role_mentions)

    @commands.group(name="rolementions", aliases=["rm"], invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
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
                "**`.rm reset`** - Manually reset counters (Admin only)"
            ),
            inline=False,
        )

        embed.add_field(
            name="Features",
            value=(
                "• Automatically logs every role mention\n"
                "• Provides real-time statistics\n"
                "• Daily reset at 4 AM IST\n"
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
    @commands.has_permissions(manage_guild=True)
    async def role_mentions_stats(self, ctx, role: discord.Role = None):
        """Get role mention statistics for this server"""
        guild_id = str(ctx.guild.id)

        if guild_id not in self.role_mentions or not self.role_mentions[guild_id]:
            await ctx.send("No role mentions recorded yet today!")
            return

        if role:
            # Get specific role stats
            role_count = self.role_mentions[guild_id].get(str(role.id), 0)
            embed = discord.Embed(
                title=f"Mention Stats for {role.name}", color=role.color
            )
            embed.add_field(name="Today's Mentions", value=str(role_count), inline=True)
            embed.add_field(name="Role ID", value=f"`{role.id}`", inline=True)
            embed.set_footer(text=f"Data resets daily at 4 AM IST")

        else:
            # Get all role stats
            total_mentions = sum(self.role_mentions[guild_id].values())

            # Get list of all mentioned roles with counts
            all_roles_info = []
            for role_id, count in self.role_mentions[guild_id].items():
                role_obj = ctx.guild.get_role(int(role_id))
                if role_obj:
                    all_roles_info.append((role_obj.name, count, role_obj.id))
                else:
                    all_roles_info.append((f"Deleted Role ({role_id})", count, role_id))

            # Sort by count
            all_roles_info.sort(key=lambda x: x[1], reverse=True)

            embed = discord.Embed(
                title=f"Role Mention Statistics",
                description=f"**Total mentions today:** `{total_mentions}`\n"
                f"**Unique roles mentioned:** `{len(all_roles_info)}`",
                color=discord.Color.blue(),
            )

            # Show all roles with their mention counts
            roles_list_text = ""
            for i, (role_name, count, role_id) in enumerate(all_roles_info[:15], 1):
                roles_list_text += f"{i}. **{role_name}** - {count} mention{'s' if count != 1 else ''} (ID: `{role_id}`)\n"

            embed.add_field(
                name="Roles Mentioned Today",
                value=roles_list_text if roles_list_text else "*No roles mentioned*",
                inline=False,
            )

            if len(all_roles_info) > 15:
                embed.set_footer(
                    text=f"Showing 15 of {len(all_roles_info)} roles | Resets daily at 4 AM IST"
                )
            else:
                embed.set_footer(text="Data resets daily at 4 AM IST")

        await ctx.send(embed=embed)

    @role_mentions_group.command(name="top")
    @commands.has_permissions(manage_guild=True)
    async def role_mentions_top(self, ctx, limit: int = 5):
        """Get top mentioned roles (default: top 5)"""
        if limit < 1 or limit > 25:
            await ctx.send("Please specify a limit between 1 and 25")
            return

        guild_id = str(ctx.guild.id)

        if guild_id not in self.role_mentions or not self.role_mentions[guild_id]:
            await ctx.send("No role mentions recorded yet today!")
            return

        # Get all roles with counts
        roles_data = []
        for role_id, count in self.role_mentions[guild_id].items():
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

        total_mentions = sum(self.role_mentions[guild_id].values())
        total_unique = len(roles_data)

        embed.add_field(
            name="📊 Summary",
            value=f"**Total Mentions Today:** {total_mentions}\n"
            f"**Unique Roles Mentioned:** {total_unique}",
            inline=False,
        )

        # List all unique roles mentioned
        if total_unique <= 10:
            all_roles_text = ""
            for role_name, count, role_id, role_obj in roles_data:
                all_roles_text += f"• {role_name} ({count})\n"

            embed.add_field(
                name="All Roles Mentioned Today", value=all_roles_text, inline=False
            )
        else:
            embed.add_field(
                name="All Roles Mentioned Today",
                value=f"{total_unique} unique roles have been mentioned today. Use `.rm stats` to see all.",
                inline=False,
            )

        embed.set_footer(text=f"Resets daily at 4 AM IST")

        await ctx.send(embed=embed)

    @role_mentions_group.command(name="reset")
    @commands.has_permissions(administrator=True)
    async def role_mentions_reset(self, ctx):
        """Manually reset all role mention counters for this server (Admin only)"""
        guild_id = str(ctx.guild.id)

        if guild_id in self.role_mentions:
            self.role_mentions[guild_id].clear()
            self.save_data()

            # Log the manual reset
            try:
                log_channel = self.bot.get_channel(self.LOG_CHANNEL_ID)
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
                        text=f"Reset at {datetime.now(self.ist).strftime('%Y-%m-%d %H:%M:%S')} IST"
                    )
                    await log_channel.send(embed=embed)
            except:
                pass

            await ctx.send("✅ Role mention counters have been reset for this server!")
        else:
            await ctx.send("No data to reset for this server.")


async def setup(bot):
    await bot.add_cog(RoleMentionTracker(bot))
