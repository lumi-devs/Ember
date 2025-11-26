import discord
from discord.ext import tasks, commands
import sqlite3
from datetime import datetime, timedelta
import asyncio
import psutil
import os

class PromoterRole(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_path = "promoter_role.db"
        
        # HARDCODED SETTINGS
        self.promoter_role_id = 1440636027923333182
        self.your_server_invites = [".gg/aakhristation", "discord.gg/aakhristation", "aakhristation"]
        self.server_tag = "Raही"  # Specific primary guild tag to monitor
        self.log_channel_id = 1443119503285293098 
        
        self.init_db()
        self.active_check = False
        self.total_checks = 0
        
    def init_db(self):
        """Initialize the database tables"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS promoter_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER,
                    member_id INTEGER,
                    member_name TEXT,
                    action TEXT,
                    reason TEXT,
                    content TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS weekly_stats (
                    week_start DATE,
                    guild_id INTEGER,
                    promoters_count INTEGER DEFAULT 0,
                    PRIMARY KEY (week_start, guild_id)
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS system_stats (
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    memory_usage REAL,
                    cpu_usage REAL,
                    total_checks INTEGER
                )
            ''')
            
            conn.commit()

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"🚀 Optimized PromoterRole cog loaded!")
        print(f"📊 Monitoring: Status & Primary Guild Tag '{self.server_tag}' for {self.your_server_invites}")
        self.quick_check.start()
        self.weekly_cleanup.start()
        self.cleanup_old_stats.start()
    
    @commands.Cog.listener()
    async def on_presence_update(self, before, after):
        """Check status changes - ONLY ONLINE MEMBERS"""
        if after.status == discord.Status.offline:
            return
            
        if self.active_check:
            return
            
        self.active_check = True
        try:
            await self.fast_member_check(after)
        finally:
            self.active_check = False

    def has_primary_guild_tag(self, member):
        """Check if member has the specific primary guild tag"""
        try:
            primary_guild = member.primary_guild
            if primary_guild is None:
                return False
            
            tag = primary_guild.tag
            if tag is None:
                return False
            
            return tag == self.server_tag
        except Exception:
            return False

    async def has_promotion_content(self, member):
        """Check status and primary guild tag ONLY"""
        
        # 1. Check Primary Guild Tag (works for ALL members - online/offline)
        if self.has_primary_guild_tag(member):
            return True
        
        # 2. Check Custom Status (ONLY for online members)
        if member.status != discord.Status.offline:
            for activity in member.activities:
                if isinstance(activity, discord.CustomActivity) and activity.name:
                    status_text = activity.name.lower()
                    for invite in self.your_server_invites:
                        if invite.lower() in status_text:
                            return True
        
        return False

    async def get_promotion_content(self, member):
        """Get promotion source for logging"""
        sources = []
        
        # Check primary guild tag
        if self.has_primary_guild_tag(member):
            sources.append(f"Primary Guild Tag: {self.server_tag}")
        
        # Check status (only for online members)
        if member.status != discord.Status.offline:
            for activity in member.activities:
                if isinstance(activity, discord.CustomActivity) and activity.name:
                    status_text = activity.name
                    for invite in self.your_server_invites:
                        if invite.lower() in status_text.lower():
                            sources.append(f"Status: {status_text}")
                            break
        
        return " | ".join(sources) if sources else "Unknown"

    def log_promotion_action(self, guild_id, member_id, member_name, action, reason, content=""):
        """Fast database logging"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO promoter_logs (guild_id, member_id, member_name, action, reason, content)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (guild_id, member_id, member_name, action, reason, content))
            conn.commit()

    def log_system_stats(self):
        """Log system performance stats"""
        memory_usage = psutil.Process(os.getpid()).memory_info().rss / 1024 ** 2
        cpu_usage = psutil.cpu_percent()
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO system_stats (memory_usage, cpu_usage, total_checks)
                VALUES (?, ?, ?)
            ''', (memory_usage, cpu_usage, self.total_checks))
            conn.commit()

    async def get_log_channel(self, guild):
        """Get the best channel for logging"""
        if self.log_channel_id:
            channel = guild.get_channel(self.log_channel_id)
            if channel:
                return channel
        
        # Fallback to system channel
        return guild.system_channel

    async def send_promotion_log(self, member, action_type, content=""):
        """Send log to designated channel"""
        try:
            channel = await self.get_log_channel(member.guild)
            if not channel:
                return
            
            if action_type == "assigned":
                embed = discord.Embed(
                    title="🎉 PROMOTER DETECTED",
                    description=f"**{member.mention}** is promoting our server!",
                    color=0x00ff00,
                    timestamp=datetime.now()
                )
                if content and content != "Unknown":
                    embed.add_field(name="Promotion Source", value=f"```{content}```", inline=False)
                embed.add_field(name="Role Given", value=f"<@&{self.promoter_role_id}>", inline=True)
                embed.add_field(name="Member Status", value=str(member.status).title(), inline=True)
                
            else:  # removed
                embed = discord.Embed(
                    title="🔄 PROMOTER REMOVED", 
                    description=f"**{member.mention}** stopped promoting",
                    color=0xff9900,
                    timestamp=datetime.now()
                )
                if content and content != "Unknown":
                    embed.add_field(name="Previous Promotion", value=f"```{content}```", inline=False)
                embed.add_field(name="Member Status", value=str(member.status).title(), inline=True)
            
            embed.set_thumbnail(url=member.display_avatar.url)
            
            await channel.send(embed=embed)
            
        except Exception as e:
            print(f"⚠️ Could not send log: {e}")

    async def fast_member_check(self, member):
        """ULTRA-FAST member check - ALL MEMBERS (primary guild tag works for offline too)"""
        if member.bot:
            return
            
        role = member.guild.get_role(self.promoter_role_id)
        if not role:
            return
        
        try:
            current_has_promotion = await self.has_promotion_content(member)
            currently_has_role = role in member.roles

            if current_has_promotion and not currently_has_role:
                # Give promoter role
                promotion_content = await self.get_promotion_content(member)
                await member.add_roles(role, reason="Auto-promotion detected")
                self.log_promotion_action(
                    member.guild.id, member.id, str(member),
                    'ROLE_ASSIGNED', 'Status/Primary Guild Tag promotion', promotion_content
                )
                print(f"✅ {member.display_name} - Promoter role assigned - {promotion_content}")
                await self.send_promotion_log(member, "assigned", promotion_content)
                
            elif not current_has_promotion and currently_has_role:
                # Remove promoter role
                promotion_content = await self.get_promotion_content(member)
                await member.remove_roles(role, reason="Promotion removed")
                self.log_promotion_action(
                    member.guild.id, member.id, str(member),
                    'ROLE_REMOVED', 'Stopped promoting', promotion_content
                )
                print(f"🔄 {member.display_name} - Promoter role removed - {promotion_content}")
                await self.send_promotion_log(member, "removed", promotion_content)
                
        except Exception as e:
            print(f"❌ Error with {member.display_name}: {e}")

    @tasks.loop(minutes=3)
    async def quick_check(self):
        """Fast check every 3 minutes - ALL MEMBERS (including those with static primary guild tags)"""
        for guild in self.bot.guilds:
            role = guild.get_role(self.promoter_role_id)
            if not role:
                continue
                
            checked = 0
            actions_taken = 0
            
            # Check ALL members (including offline) for primary guild tag and status
            for member in guild.members:
                if not member.bot:
                    before_has_role = role in member.roles
                    await self.fast_member_check(member)
                    checked += 1
                    self.total_checks += 1
                    
                    # Check if action was taken
                    after_has_role = role in member.roles
                    if before_has_role != after_has_role:
                        actions_taken += 1
                    
                    await asyncio.sleep(0.02)  # Reduced rate limit
            
            # Log system stats every 10 checks
            if self.total_checks % 10 == 0:
                self.log_system_stats()
            
            if checked > 0:
                print(f"🔍 Quick: {guild.name} - {checked} members, {actions_taken} actions")

    @tasks.loop(hours=24)
    async def weekly_cleanup(self):
        """Update weekly stats - ALL PROMOTERS (including offline with primary guild tag)"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Get current promoters count (ALL members - including offline with primary guild tag)
            for guild in self.bot.guilds:
                role = guild.get_role(self.promoter_role_id)
                if role:
                    # Count ALL promoters (including offline with primary guild tag)
                    promoters_count = len([m for m in guild.members if role in m.roles and not m.bot])
                    
                    # Get current week start (Monday)
                    today = datetime.now().date()
                    week_start = today - timedelta(days=today.weekday())
                    
                    cursor.execute('''
                        INSERT OR REPLACE INTO weekly_stats (week_start, guild_id, promoters_count)
                        VALUES (?, ?, ?)
                    ''', (week_start, guild.id, promoters_count))
            
            conn.commit()
        print("📈 Weekly stats updated (all promoters including offline)")

    @tasks.loop(hours=1)
    async def cleanup_old_stats(self):
        """Clean up old system stats to keep database small"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                DELETE FROM system_stats 
                WHERE timestamp < datetime('now', '-7 days')
            ''')
            conn.commit()

    # COMMANDS
    @commands.command(name='pgtag')
    async def pgtag(self, ctx, member: discord.Member = None):
        """Check a member's primary guild tag"""
        member = member or ctx.author

        try:
            primary_guild = member.primary_guild
            if primary_guild is None:
                return await ctx.reply("❌ This user has no primary guild set.")

            tag = primary_guild.tag
            if tag is None:
                return await ctx.reply("⚠️ This user has a primary guild but no tag set.")

            # Check if this tag would give promoter role
            would_promote = tag == self.server_tag
            
            embed = discord.Embed(
                title="🏷️ Primary Guild Tag",
                color=0x7289da
            )
            embed.add_field(name="Member", value=member.mention, inline=True)
            embed.add_field(name="Primary Guild Tag", value=f"**{tag}**", inline=True)
            embed.add_field(name="Promoter Status", 
                          value="✅ Would Get Role" if would_promote else "❌ No Role", 
                          inline=True)
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            await ctx.reply(f"❌ Error checking primary guild tag: {e}")

    @commands.command(name='promoter_stats')
    @commands.has_permissions(manage_roles=True)
    async def promoter_stats(self, ctx):
        """Check bot performance and system health"""
        try:
            # Get system info
            memory_usage = psutil.Process(os.getpid()).memory_info().rss / 1024 ** 2
            cpu_usage = psutil.cpu_percent()
            
            # Get database stats
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Total logs
                cursor.execute('SELECT COUNT(*) FROM promoter_logs')
                total_logs = cursor.fetchone()[0]
                
                # Today's actions
                cursor.execute('''
                    SELECT COUNT(*) FROM promoter_logs 
                    WHERE DATE(created_at) = DATE('now')
                ''')
                today_actions = cursor.fetchone()[0]
            
            # Get current promoter counts
            role = ctx.guild.get_role(self.promoter_role_id)
            total_promoters = len([m for m in ctx.guild.members if role in m.roles and not m.bot]) if role else 0
            
            # Count members with primary guild tag
            members_with_tag = 0
            for member in ctx.guild.members:
                if not member.bot and self.has_primary_guild_tag(member):
                    members_with_tag += 1
            
            embed = discord.Embed(
                title="🤖 Promoter System Stats",
                color=0x7289da,
                timestamp=datetime.now()
            )
            
            # System Performance
            embed.add_field(name="💾 Memory Usage", value=f"{memory_usage:.1f} MB", inline=True)
            embed.add_field(name="⚡ CPU Usage", value=f"{cpu_usage:.1f}%", inline=True)
            embed.add_field(name="📊 Total Checks", value=f"{self.total_checks:,}", inline=True)
            
            # Promoter Stats
            embed.add_field(name="👥 Total Promoters", value=total_promoters, inline=True)
            embed.add_field(name="🏷️ Members with Raही Tag", value=members_with_tag, inline=True)
            embed.add_field(name="📈 Today's Actions", value=today_actions, inline=True)
            
            # Status
            status = "✅ **HEALTHY**" if memory_usage < 500 and cpu_usage < 80 else "⚠️ **LOAD HIGH**"
            embed.add_field(name="🩺 System Status", value=status, inline=True)
            
            # Next Check
            next_check = datetime.now() + timedelta(minutes=3)
            embed.add_field(name="🔄 Next Auto-Check", value=next_check.strftime("%H:%M:%S"), inline=True)
            
            embed.set_footer(text="Monitoring: Status & Primary Guild Tag only")
            await ctx.send(embed=embed)
            
        except Exception as e:
            await ctx.send(f"❌ Error fetching stats: {e}")

    @commands.command(name='force_check')
    @commands.has_permissions(manage_roles=True)
    async def force_check(self, ctx, member: discord.Member = None):
        """Force check all members or a specific member"""
        if member:
            # Check specific member
            await self.fast_member_check(member)
            embed = discord.Embed(
                title="✅ Force Check Complete",
                description=f"Checked {member.mention}",
                color=0x00ff00
            )
            await ctx.send(embed=embed)
        else:
            # Check all members
            message = await ctx.send("🔄 Force checking all members...")
            
            guild = ctx.guild
            role = guild.get_role(self.promoter_role_id)
            if not role:
                await message.edit(content="❌ Promoter role not found!")
                return
            
            checked = 0
            actions_taken = 0
            
            for member in guild.members:
                if not member.bot:
                    before_has_role = role in member.roles
                    await self.fast_member_check(member)
                    checked += 1
                    
                    # Check if action was taken
                    after_has_role = role in member.roles
                    if before_has_role != after_has_role:
                        actions_taken += 1
                    
                    await asyncio.sleep(0.01)
            
            embed = discord.Embed(
                title="✅ Force Check Complete",
                description=f"**Checked:** {checked} members\n**Actions Taken:** {actions_taken}",
                color=0x00ff00
            )
            await message.edit(content=None, embed=embed)

    @commands.command(name='debug_promoter')
    @commands.has_permissions(manage_roles=True)
    async def debug_promoter(self, ctx, member: discord.Member = None):
        """Debug information for a specific member"""
        target = member or ctx.author
        
        embed = discord.Embed(
            title=f"🐛 Debug Info - {target.display_name}",
            color=0xff9900
        )
        
        # Basic info
        embed.add_field(name="🆔 Member ID", value=target.id, inline=True)
        embed.add_field(name="🌐 Status", value=str(target.status).title(), inline=True)
        embed.add_field(name="🤖 Is Bot", value="✅ Yes" if target.bot else "❌ No", inline=True)
        
        # Primary guild tag info
        has_primary_tag = self.has_primary_guild_tag(target)
        primary_guild = target.primary_guild
        tag_value = primary_guild.tag if primary_guild and primary_guild.tag else "None"
        
        embed.add_field(name="🎯 Has Raही Tag", value="✅ Yes" if has_primary_tag else "❌ No", inline=True)
        embed.add_field(name="🏷️ Primary Guild Tag", value=tag_value, inline=True)
        
        # Promotion checks
        has_promotion = await self.has_promotion_content(target)
        role = ctx.guild.get_role(self.promoter_role_id)
        has_role = role in target.roles if role else False
        
        embed.add_field(name="📢 Has Promotion", value="✅ Yes" if has_promotion else "❌ No", inline=True)
        embed.add_field(name="🎪 Has Promoter Role", value="✅ Yes" if has_role else "❌ No", inline=True)
        
        await ctx.send(embed=embed)

    # Existing commands
    @commands.command(name='set_log_channel')
    @commands.has_permissions(administrator=True)
    async def set_log_channel(self, ctx):
        """Set current channel as promotion log channel"""
        self.log_channel_id = ctx.channel.id
        embed = discord.Embed(
            title="✅ Log Channel Set",
            description="Promotion logs will be sent to this channel",
            color=0x00ff00
        )
        await ctx.send(embed=embed)

    @commands.command(name='promo_check')
    @commands.has_permissions(manage_roles=True)
    async def manual_promo_check(self, ctx, member: discord.Member = None):
        """Manually check a member's promotion status"""
        target = member or ctx.author
        role = ctx.guild.get_role(self.promoter_role_id)
        
        if not role:
            await ctx.send("❌ Promoter role not found!")
            return
        
        has_promotion = await self.has_promotion_content(target)
        has_role = role in target.roles
        promotion_content = await self.get_promotion_content(target)
        
        embed = discord.Embed(
            title=f"🔍 Promotion Check - {target.display_name}",
            color=discord.Color.blue()
        )
        
        embed.add_field(name="Has Promotion", value="✅ Yes" if has_promotion else "❌ No", inline=True)
        embed.add_field(name="Has Role", value="✅ Yes" if has_role else "❌ No", inline=True)
        embed.add_field(name="Status Match", value="✅ Yes" if has_promotion == has_role else "❌ No", inline=True)
        embed.add_field(name="Member Status", value=str(target.status).title(), inline=True)
        embed.add_field(name="Has Raही Primary Tag", value="✅ Yes" if self.has_primary_guild_tag(target) else "❌ No", inline=True)
        
        if promotion_content != "Unknown":
            embed.add_field(name="Detected In", value=f"```{promotion_content}```", inline=False)
        
        await ctx.send(embed=embed)

    @commands.command(name='promo_help')
    async def promo_help(self, ctx):
        """Show help for promotion commands"""
        embed = discord.Embed(
            title="🤖 Promotion System Help",
            description="Automatically detects when members promote the server in their status or primary guild tag",
            color=0x7289da
        )
        
        commands_list = [
            ("`!pgtag [member]`", "**NEW** Check member's primary guild tag"),
            ("`!promo_check [member]`", "Check a member's promotion status"),
            ("`!promoter_stats`", "Check bot performance & system health"),
            ("`!force_check [member]`", "Force check all/specific members"),
            ("`!debug_promoter [member]`", "Debug info for a member"),
            ("`!set_log_channel`", "Set current channel as log channel (Admin only)"),
            ("`!promo_help`", "Show this help message")
        ]
        
        for cmd, desc in commands_list:
            embed.add_field(name=cmd, value=desc, inline=False)
        
        embed.add_field(
            name="🔍 Monitoring", 
            value=f"• **Status** (online members only)\n• **Primary Guild Tag** `{self.server_tag}` (all members)\n• **Auto-check every 3 minutes**", 
            inline=False
        )
        
        embed.set_footer(text=f"Monitoring: {', '.join(self.your_server_invites)} + '{self.server_tag}' primary guild tag")
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(PromoterRole(bot))