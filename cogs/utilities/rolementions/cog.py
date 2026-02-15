import discord
from discord.ext import commands
import pytz
from datetime import datetime

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
            timestamp=datetime.now(pytz.UTC)
        )
        
        # Add author info
        embed.set_author(
            name=f"{message.author.name} ({message.author.id})",
            icon_url=message.author.avatar.url if message.author.avatar else None
        )
        
        # Add message link
        embed.add_field(
            name="Message Link",
            value=f"[Jump to Message]({message.jump_url})",
            inline=True
        )
        
        # Add message content preview
        content_preview = message.content[:200] + "..." if len(message.content) > 200 else message.content
        if not content_preview.strip():
            content_preview = "*[No text content]*"
        
        embed.add_field(
            name="Message Content",
            value=f"```{content_preview}```",
            inline=False
        )
        
        # Add mentioned roles section
        roles_text = ""
        for role in mentioned_roles:
            role_count = self.tracker.get_role_count(guild_id, str(role.id))
            roles_text += f"• {role.mention} - **{role_count}** mention{'s' if role_count != 1 else ''} today\n"
        
        embed.add_field(
            name=f"Mentioned Roles ({len(mentioned_roles)})",
            value=roles_text or "*No roles mentioned*",
            inline=False
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
        
        embed.add_field(
            name="📊 Today's Statistics",
            value=stats_text,
            inline=False
        )
        
        # Add command info
        command_usage = ""
        if message.content.startswith('.'):
            command_usage = f"**Command Detected:** `{message.content.split()[0]}`\n"
        
        embed.add_field(
            name="⚙️ Available Commands",
            value=f"{command_usage}**`.rm stats [role]`** - Get role mention statistics\n"
                  f"**`.rm top [limit]`** - See most mentioned roles\n"
                  f"**`.rm reset`** - Reset counters (Admin only)\n"
                  f"**`.automod list`** - View active blocks",
            inline=True
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
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name="Available Commands",
            value=(
                "**`.rm stats [role]`** - Get role mention statistics\n"
                "**`.rm top [limit]`** - Get top mentioned roles (default: 5)\n"
                "**`.rm reset`** - Manually reset counters (Admin only)\n"
                "**`.automod list`** - View active auto-mod blocks"
            ),
            inline=False
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
            inline=False
        )
        
        embed.add_field(
            name="Current Log Channel",
            value=f"<#{self.LOG_CHANNEL_ID}>",
            inline=True
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
                title=f"Mention Stats for {role.name}",
                color=role.color
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
                color=discord.Color.blue()
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
                inline=False
            )
            
            if len(roles_list) > 15:
                embed.set_footer(text=f"Showing 15 of {len(roles_list)} roles | Resets daily at 4 AM IST")
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
            title=f"Top {limit} Most Mentioned Roles",
            color=discord.Color.gold()
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
                inline=False
            )
        
        embed.add_field(
            name="📊 Summary",
            value=f"**Total Mentions Today:** {stats['total']}\n"
                  f"**Unique Roles Mentioned:** {stats['unique']}",
            inline=False
        )
        
        # List all unique roles if less than 10
        if stats['unique'] <= 10:
            all_roles_text = ""
            for role_name, count, role_id, role_obj in roles_data:
                all_roles_text += f"• {role_name} ({count})\n"
            
            embed.add_field(
                name="All Roles Mentioned Today",
                value=all_roles_text,
                inline=False
            )
        else:
            embed.add_field(
                name="All Roles Mentioned Today",
                value=f"{stats['unique']} unique roles have been mentioned today. Use `.rm stats` to see all.",
                inline=False
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
                color=discord.Color.orange()
            )
            embed.add_field(
                name="Reset By",
                value=f"{ctx.author.mention} ({ctx.author.id})",
                inline=True
            )
            embed.set_footer(text=f"Reset at {datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%Y-%m-%d %H:%M:%S')} IST")
            await log_channel.send(embed=embed)
        
        await ctx.send("✅ Role mention counters have been reset for this server!")
    
    # Add auto-mod commands to the main cog
    @commands.group(name="automod", aliases=["am"])
    @commands.has_permissions(administrator=True)
    async def automod_group(self, ctx):
        """Manage auto-mod blocking settings"""
        await self.automod.automod_group(ctx)
    
    @automod_group.command(name="list")
    async def automod_list(self, ctx):
        """List all active auto-mod rules"""
        await self.automod.automod_list(ctx)
    
    @automod_group.command(name="remove")
    async def automod_remove(self, ctx, rule_id: str):
        """Manually remove an auto-mod rule"""
        await self.automod.automod_remove(ctx, rule_id)
    
    @automod_group.group(name="keywords")
    async def automod_keywords(self, ctx):
        """Manage trigger keywords"""
        await self.automod.automod_keywords(ctx)
    
    @automod_keywords.command(name="add")
    async def keywords_add(self, ctx, *, keyword: str):
        """Add a trigger keyword"""
        await self.automod.keywords_add(ctx, keyword)
    
    @automod_keywords.command(name="remove")
    async def keywords_remove(self, ctx, *, keyword: str):
        """Remove a trigger keyword"""
        await self.automod.keywords_remove(ctx, keyword)

