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
        self.server_tag = "Raही"
        self.log_channel_id = 1443119503285293098 
        
        # Debug settings
        self.dry_run = False  # Set to True for testing without role changes
        self.debug_mode = True
        
        self.init_db()
        self.total_checks = 0
        
    def init_db(self):
        """Initialize the database tables"""
        try:
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
                print("✅ Database initialized successfully")
        except Exception as e:
            print(f"❌ Database initialization failed: {e}")

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"🚀 PromoterRole cog loaded!")
        print(f"📊 Monitoring: Status & Primary Guild Tag '{self.server_tag}'")
        print(f"🔧 Dry Run Mode: {self.dry_run}")
        print(f"🐛 Debug Mode: {self.debug_mode}")
        
        # Test basic functionality
        await self.test_basic_functionality()
        
        self.fallback_check.start()
        self.weekly_cleanup.start()
        self.cleanup_old_stats.start()
    
    async def test_basic_functionality(self):
        """Test basic cog functionality"""
        print("🧪 Running basic functionality tests...")
        
        # Test database
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = cursor.fetchall()
                print(f"✅ Database tables: {[table[0] for table in tables]}")
        except Exception as e:
            print(f"❌ Database test failed: {e}")
        
        # Test role access
        for guild in self.bot.guilds:
            role = guild.get_role(self.promoter_role_id)
            if role:
                print(f"✅ Role found in {guild.name}: {role.name}")
            else:
                print(f"⚠️ Role not found in {guild.name} (ID: {self.promoter_role_id})")
        
        print("🧪 Basic tests completed")

    @commands.Cog.listener()
    async def on_presence_update(self, before, after):
        """Status change detection"""
        if after.bot:
            return
            
        if self.debug_mode:
            print(f"🔍 Presence update: {after.display_name}")
            
        # Check if status content changed
        before_status = self.get_status_content(before)
        after_status = self.get_status_content(after)
        
        if before_status != after_status:
            if self.debug_mode:
                print(f"🔄 Status changed for {after.display_name}: '{before_status}' -> '{after_status}'")
            await self.instant_member_check(after, "Status changed")

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        """Member update detection"""
        if after.bot:
            return
            
        if self.debug_mode:
            print(f"🔍 Member update: {after.display_name}")
            
        # Check primary guild tag changes
        before_tag = self.has_primary_guild_tag(before)
        after_tag = self.has_primary_guild_tag(after)
        
        if before_tag != after_tag:
            reason = f"Primary tag {'added' if after_tag else 'removed'}"
            if self.debug_mode:
                print(f"🏷️ Tag changed for {after.display_name}: {reason}")
            await self.instant_member_check(after, reason)

    def get_status_content(self, member):
        """Get status text for comparison"""
        try:
            if member.status == discord.Status.offline:
                return None
                
            for activity in member.activities:
                if isinstance(activity, discord.CustomActivity) and activity.name:
                    return activity.name.lower()
            return None
        except Exception as e:
            if self.debug_mode:
                print(f"❌ Error getting status for {member.display_name}: {e}")
            return None

    def has_primary_guild_tag(self, member):
        """Check if member has the specific primary guild tag"""
        try:
            # Method 1: Try primary_guild attribute
            if hasattr(member, 'primary_guild') and member.primary_guild:
                tag = getattr(member.primary_guild, 'tag', None)
                if tag and tag == self.server_tag:
                    return True
            
            # Method 2: Check guild properties (fallback)
            for guild in self.bot.guilds:
                guild_member = guild.get_member(member.id)
                if guild_member and hasattr(guild, 'tag') and guild.tag == self.server_tag:
                    return True
                    
            return False
        except Exception as e:
            if self.debug_mode:
                print(f"❌ Error checking primary tag for {member.display_name}: {e}")
            return False

    async def has_promotion_content(self, member):
        """Check status and primary guild tag for promotion content"""
        try:
            # 1. Check Primary Guild Tag
            if self.has_primary_guild_tag(member):
                if self.debug_mode:
                    print(f"✅ {member.display_name} has primary guild tag")
                return True
            
            # 2. Check Custom Status
            if member.status != discord.Status.offline:
                for activity in member.activities:
                    if isinstance(activity, discord.CustomActivity) and activity.name:
                        status_text = activity.name.lower()
                        for invite in self.your_server_invites:
                            if invite.lower() in status_text:
                                if self.debug_mode:
                                    print(f"✅ {member.display_name} has invite in status: {status_text}")
                                return True
            
            if self.debug_mode:
                print(f"❌ {member.display_name} has no promotion content")
            return False
            
        except Exception as e:
            print(f"❌ Error checking promotion content for {member.display_name}: {e}")
            return False

    async def get_promotion_content(self, member):
        """Get promotion source for logging"""
        sources = []
        
        try:
            # Check primary guild tag
            if self.has_primary_guild_tag(member):
                sources.append(f"Primary Guild Tag: {self.server_tag}")
            
            # Check status
            if member.status != discord.Status.offline:
                for activity in member.activities:
                    if isinstance(activity, discord.CustomActivity) and activity.name:
                        status_text = activity.name
                        for invite in self.your_server_invites:
                            if invite.lower() in status_text.lower():
                                sources.append(f"Status: {status_text}")
                                break
            
            return " | ".join(sources) if sources else "No promotion content detected"
        except Exception as e:
            return f"Error: {e}"

    def log_promotion_action(self, guild_id, member_id, member_name, action, reason, content=""):
        """Fast database logging"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO promoter_logs (guild_id, member_id, member_name, action, reason, content)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (guild_id, member_id, member_name, action, reason, content))
                conn.commit()
        except Exception as e:
            print(f"❌ Database logging failed: {e}")

    async def instant_member_check(self, member, reason):
        """Instant check - called immediately on changes"""
        try:
            role = member.guild.get_role(self.promoter_role_id)
            if not role:
                print(f"❌ Role not found in {member.guild.name}")
                return
            
            current_has_promotion = await self.has_promotion_content(member)
            currently_has_role = role in member.roles

            if self.debug_mode:
                print(f"🔍 Checking {member.display_name}: Promotion={current_has_promotion}, Role={currently_has_role}")

            if current_has_promotion and not currently_has_role:
                # Should add role
                promotion_content = await self.get_promotion_content(member)
                
                if self.dry_run:
                    print(f"🧪 DRY RUN: Would add role to {member.display_name} - {promotion_content}")
                    return
                
                await member.add_roles(role, reason=f"Promotion: {reason}")
                self.log_promotion_action(
                    member.guild.id, member.id, str(member),
                    'ROLE_ASSIGNED', f'Instant: {reason}', promotion_content
                )
                print(f"✅ {member.display_name} - Promoter role assigned - {promotion_content}")
                await self.send_promotion_log(member, "assigned", promotion_content)
                
            elif not current_has_promotion and currently_has_role:
                # Should remove role
                promotion_content = await self.get_promotion_content(member)
                
                if self.dry_run:
                    print(f"🧪 DRY RUN: Would remove role from {member.display_name} - {promotion_content}")
                    return
                
                await member.remove_roles(role, reason=f"Removal: {reason}")
                self.log_promotion_action(
                    member.guild.id, member.id, str(member),
                    'ROLE_REMOVED', f'Instant: {reason}', promotion_content
                )
                print(f"🔄 {member.display_name} - Promoter role removed - {promotion_content}")
                await self.send_promotion_log(member, "removed", promotion_content)
                
        except discord.Forbidden:
            print(f"❌ Missing permissions to manage roles for {member.display_name}")
        except Exception as e:
            print(f"❌ Check error with {member.display_name}: {e}")

    @tasks.loop(seconds=30)
    async def fallback_check(self):
        """Fallback: Check for missed updates every 30 seconds"""
        if self.debug_mode:
            print("🔄 Running fallback check...")
            
        for guild in self.bot.guilds:
            role = guild.get_role(self.promoter_role_id)
            if not role:
                continue
                
            checked = 0
            corrections = 0
            
            for member in guild.members:
                if not member.bot:
                    try:
                        has_tag = self.has_primary_guild_tag(member)
                        has_role = role in member.roles
                        
                        # If mismatch detected
                        if has_tag != has_role:
                            if self.debug_mode:
                                print(f"🔄 Mismatch found: {member.display_name} (Tag: {has_tag}, Role: {has_role})")
                            await self.instant_member_check(member, "Fallback check mismatch")
                            corrections += 1
                        
                        checked += 1
                        self.total_checks += 1
                        
                    except Exception as e:
                        print(f"❌ Error checking {member.display_name}: {e}")
            
            if corrections > 0 or self.debug_mode:
                print(f"📊 Fallback: {guild.name} - {checked} checked, {corrections} corrected")

    @tasks.loop(hours=24)
    async def weekly_cleanup(self):
        """Update weekly stats"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                for guild in self.bot.guilds:
                    role = guild.get_role(self.promoter_role_id)
                    if role:
                        promoters_count = len([m for m in guild.members if role in m.roles and not m.bot])
                        
                        today = datetime.now().date()
                        week_start = today - timedelta(days=today.weekday())
                        
                        cursor.execute('''
                            INSERT OR REPLACE INTO weekly_stats (week_start, guild_id, promoters_count)
                            VALUES (?, ?, ?)
                        ''', (week_start, guild.id, promoters_count))
                
                conn.commit()
                print("📈 Weekly stats updated")
        except Exception as e:
            print(f"❌ Weekly cleanup failed: {e}")

    @tasks.loop(hours=1)
    async def cleanup_old_stats(self):
        """Clean up old system stats"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('DELETE FROM system_stats WHERE timestamp < datetime("now", "-7 days")')
                conn.commit()
        except Exception as e:
            print(f"❌ Stats cleanup failed: {e}")

    async def send_promotion_log(self, member, action_type, content=""):
        """Send log to designated channel"""
        try:
            channel = member.guild.get_channel(self.log_channel_id)
            if not channel:
                print(f"❌ Log channel not found: {self.log_channel_id}")
                return
            
            if action_type == "assigned":
                embed = discord.Embed(
                    title="⚡ Promoter Added",
                    description=f"**{member.mention}** is now promoting",
                    color=0x00ff00,
                    timestamp=datetime.now()
                )
            else:
                embed = discord.Embed(
                    title="⚡ Promoter Removed", 
                    description=f"**{member.mention}** stopped promoting",
                    color=0xff9900,
                    timestamp=datetime.now()
                )
            
            if content and content != "Unknown":
                embed.add_field(name="Promotion Source", value=f"```{content}```", inline=False)
            
            embed.add_field(name="Role Action", value=f"Promoter Role", inline=True)
            embed.set_thumbnail(url=member.display_avatar.url)
            
            await channel.send(embed=embed)
            
        except Exception as e:
            print(f"❌ Failed to send log: {e}")

    # COMMANDS
    @commands.command(name='dry_run')
    @commands.has_permissions(manage_roles=True)
    async def dry_run_toggle(self, ctx, mode: str = None):
        """Toggle dry run mode (no actual role changes)"""
        if mode and mode.lower() in ['on', 'true', 'yes']:
            self.dry_run = True
            await ctx.send("🧪 **DRY RUN MODE ENABLED** - No role changes will be made")
        elif mode and mode.lower() in ['off', 'false', 'no']:
            self.dry_run = False
            await ctx.send("⚡ **LIVE MODE ENABLED** - Role changes will be made")
        else:
            status = "ENABLED" if self.dry_run else "DISABLED"
            await ctx.send(f"🧪 Dry Run Mode is currently **{status}**")

    @commands.command(name='debug_mode')
    @commands.has_permissions(manage_roles=True)
    async def debug_mode_toggle(self, ctx, mode: str = None):
        """Toggle debug mode"""
        if mode and mode.lower() in ['on', 'true', 'yes']:
            self.debug_mode = True
            await ctx.send("🐛 **DEBUG MODE ENABLED** - Detailed logging active")
        elif mode and mode.lower() in ['off', 'false', 'no']:
            self.debug_mode = False
            await ctx.send("🔒 **DEBUG MODE DISABLED** - Minimal logging")
        else:
            status = "ENABLED" if self.debug_mode else "DISABLED"
            await ctx.send(f"🐛 Debug Mode is currently **{status}**")

    @commands.command(name='test_check')
    @commands.has_permissions(manage_roles=True)
    async def test_check(self, ctx, member: discord.Member = None):
        """Test check without making changes"""
        target = member or ctx.author
        
        embed = discord.Embed(title=f"🧪 Test Check - {target.display_name}", color=0x7289da)
        
        # Check all conditions
        has_primary_tag = self.has_primary_guild_tag(target)
        has_promotion = await self.has_promotion_content(target)
        role = ctx.guild.get_role(self.promoter_role_id)
        has_role = role in target.roles if role else False
        promotion_content = await self.get_promotion_content(target)
        
        embed.add_field(name="🏷️ Primary Tag", value="✅ Yes" if has_primary_tag else "❌ No", inline=True)
        embed.add_field(name="📢 Promotion Content", value="✅ Yes" if has_promotion else "❌ No", inline=True)
        embed.add_field(name="🎪 Has Role", value="✅ Yes" if has_role else "❌ No", inline=True)
        embed.add_field(name="🔍 Action Needed", value="❌ No" if has_promotion == has_role else "✅ Yes", inline=True)
        embed.add_field(name="🧪 Dry Run", value="✅ Active" if self.dry_run else "❌ Inactive", inline=True)
        
        if promotion_content:
            embed.add_field(name="📝 Content Found", value=f"```{promotion_content}```", inline=False)
        
        await ctx.send(embed=embed)

    @commands.command(name='pgtag')
    async def pgtag(self, ctx, member: discord.Member = None):
        """Check a member's primary guild tag"""
        member = member or ctx.author

        try:
            has_tag = self.has_primary_guild_tag(member)
            tag_info = "Unknown"
            
            # Try to get actual tag value
            if hasattr(member, 'primary_guild') and member.primary_guild:
                tag_info = getattr(member.primary_guild, 'tag', 'No tag set')
            
            embed = discord.Embed(title="🏷️ Primary Guild Tag Check", color=0x7289da)
            embed.add_field(name="Member", value=member.mention, inline=True)
            embed.add_field(name="Has Server Tag", value="✅ Yes" if has_tag else "❌ No", inline=True)
            embed.add_field(name="Tag Value", value=f"`{tag_info}`", inline=True)
            embed.add_field(name="Expected Tag", value=f"`{self.server_tag}`", inline=True)
            embed.add_field(name="Would Promote", value="✅ Yes" if has_tag else "❌ No", inline=True)
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            await ctx.reply(f"❌ Error: {e}")

    @commands.command(name='promoter_stats')
    @commands.has_permissions(manage_roles=True)
    async def promoter_stats(self, ctx):
        """Check bot performance and system health"""
        try:
            memory_usage = psutil.Process(os.getpid()).memory_info().rss / 1024 ** 2
            cpu_usage = psutil.cpu_percent()
            
            role = ctx.guild.get_role(self.promoter_role_id)
            total_promoters = len([m for m in ctx.guild.members if role in m.roles and not m.bot]) if role else 0
            
            members_with_tag = 0
            for member in ctx.guild.members:
                if not member.bot and self.has_primary_guild_tag(member):
                    members_with_tag += 1
            
            embed = discord.Embed(title="🤖 Promoter System Stats", color=0x7289da)
            embed.add_field(name="💾 Memory", value=f"{memory_usage:.1f} MB", inline=True)
            embed.add_field(name="⚡ CPU", value=f"{cpu_usage:.1f}%", inline=True)
            embed.add_field(name="📊 Total Checks", value=f"{self.total_checks:,}", inline=True)
            embed.add_field(name="👥 Promoters", value=total_promoters, inline=True)
            embed.add_field(name="🏷️ Server Tags", value=members_with_tag, inline=True)
            embed.add_field(name="🧪 Dry Run", value="✅ On" if self.dry_run else "❌ Off", inline=True)
            embed.add_field(name="🐛 Debug", value="✅ On" if self.debug_mode else "❌ Off", inline=True)
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            await ctx.send(f"❌ Error: {e}")

    @commands.command(name='force_check')
    @commands.has_permissions(manage_roles=True)
    async def force_check(self, ctx, member: discord.Member = None):
        """Force instant check"""
        if member:
            await self.instant_member_check(member, "Manual force check")
            await ctx.send(f"✅ Checked {member.mention}")
        else:
            message = await ctx.send("⚡ Checking all members...")
            checked = 0
            corrections = 0
            
            for member in ctx.guild.members:
                if not member.bot:
                    old_has_role = ctx.guild.get_role(self.promoter_role_id) in member.roles
                    await self.instant_member_check(member, "Manual bulk check")
                    new_has_role = ctx.guild.get_role(self.promoter_role_id) in member.roles
                    
                    if old_has_role != new_has_role:
                        corrections += 1
                    
                    checked += 1
                    await asyncio.sleep(0.01)
            
            await message.edit(content=f"✅ Checked {checked} members, {corrections} corrections made")

    @commands.command(name='debug_promoter')
    @commands.has_permissions(manage_roles=True)
    async def debug_promoter(self, ctx, member: discord.Member = None):
        """Debug info"""
        target = member or ctx.author
        
        embed = discord.Embed(title=f"🐛 Debug - {target.display_name}", color=0xff9900)
        embed.add_field(name="🆔 ID", value=target.id, inline=True)
        embed.add_field(name="🌐 Status", value=str(target.status).title(), inline=True)
        
        has_primary_tag = self.has_primary_guild_tag(target)
        
        embed.add_field(name="🎯 Server Tag", value="✅ Yes" if has_primary_tag else "❌ No", inline=True)
        
        has_promotion = await self.has_promotion_content(target)
        role = ctx.guild.get_role(self.promoter_role_id)
        has_role = role in target.roles if role else False
        
        embed.add_field(name="📢 Promotion", value="✅ Yes" if has_promotion else "❌ No", inline=True)
        embed.add_field(name="🎪 Has Role", value="✅ Yes" if has_role else "❌ No", inline=True)
        embed.add_field(name="🔧 Dry Run", value="✅ Active" if self.dry_run else "❌ Inactive", inline=True)
        
        # Get activities
        activities = []
        for activity in target.activities:
            if isinstance(activity, discord.CustomActivity):
                activities.append(f"Custom: {activity.name}")
            else:
                activities.append(str(activity.type).split('.')[-1].title())
        
        if activities:
            embed.add_field(name="📋 Activities", value=", ".join(activities), inline=False)
        
        await ctx.send(embed=embed)

    @commands.command(name='set_log_channel')
    @commands.has_permissions(administrator=True)
    async def set_log_channel(self, ctx):
        """Set log channel"""
        self.log_channel_id = ctx.channel.id
        await ctx.send("✅ Log channel set - Detection active!")

    @commands.command(name='promo_check')
    @commands.has_permissions(manage_roles=True)
    async def manual_promo_check(self, ctx, member: discord.Member = None):
        """Manual check"""
        target = member or ctx.author
        role = ctx.guild.get_role(self.promoter_role_id)
        
        if not role:
            await ctx.send("❌ Role not found!")
            return
        
        has_promotion = await self.has_promotion_content(target)
        has_role = role in target.roles
        
        embed = discord.Embed(title=f"🔍 Check - {target.display_name}", color=0x7289da)
        embed.add_field(name="Promotion", value="✅ Yes" if has_promotion else "❌ No", inline=True)
        embed.add_field(name="Has Role", value="✅ Yes" if has_role else "❌ No", inline=True)
        embed.add_field(name="Match", value="✅ Yes" if has_promotion == has_role else "❌ No", inline=True)
        embed.add_field(name="Server Tag", value="✅ Yes" if self.has_primary_guild_tag(target) else "❌ No", inline=True)
        
        await ctx.send(embed=embed)

    @commands.command(name='promo_help')
    async def promo_help(self, ctx):
        """Help"""
        embed = discord.Embed(title="🤖 Promotion System Help", color=0x7289da)
        embed.add_field(name="⚡ Features", value="• Status detection\n• Primary tag detection\n• Fallback every 30 seconds", inline=False)
        embed.add_field(name="🎯 Monitoring", value=f"• Status with invites\n• Primary Guild Tag: `{self.server_tag}`", inline=False)
        embed.add_field(name="🔧 Debug Commands", value="`!dry_run on/off` `!debug_mode on/off` `!test_check`", inline=False)
        embed.add_field(name="📝 Main Commands", value="`!pgtag` `!promoter_stats` `!force_check` `!debug_promoter` `!promo_check`", inline=False)
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(PromoterRole(bot))