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
        
        self.init_db()
        self.total_checks = 0
        self.last_status_cache = {}  # Cache for status changes
        
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
        print(f"🚀 ULTRA-FAST PromoterRole cog loaded!")
        print(f"📊 INSTANT checking: Status & Primary Guild Tag '{self.server_tag}'")
        self.fallback_check.start()
        self.weekly_cleanup.start()
        self.cleanup_old_stats.start()
    
    @commands.Cog.listener()
    async def on_presence_update(self, before, after):
        """INSTANT status change detection"""
        if after.bot:
            return
            
        # Get cache key
        cache_key = f"{after.guild.id}-{after.id}"
        
        # Get current and previous status content
        current_status = self.get_status_content(after)
        previous_status = self.last_status_cache.get(cache_key)
        
        # Update cache
        self.last_status_cache[cache_key] = current_status
        
        # Check if status content actually changed
        if current_status != previous_status:
            await self.instant_member_check(after, "Status change")

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        """INSTANT member update detection - PRIMARY GUILD TAG"""
        if after.bot:
            return
            
        # Check primary guild tag changes
        before_has_tag = self.has_primary_guild_tag(before)
        after_has_tag = self.has_primary_guild_tag(after)
        
        if before_has_tag != after_has_tag:
            reason = f"Primary tag {'added' if after_has_tag else 'removed'}"
            await self.instant_member_check(after, reason)
        
        # Also check if global name changed (might affect primary guild)
        if before.global_name != after.global_name:
            await self.instant_member_check(after, "Global name change")

    def get_status_content(self, member):
        """Get status text for comparison - OPTIMIZED"""
        if member.status == discord.Status.offline:
            return None
            
        for activity in member.activities:
            if isinstance(activity, discord.CustomActivity) and activity.name:
                return activity.name.lower()
        return None

    def has_primary_guild_tag(self, member):
        """ULTRA-FAST primary guild tag check"""
        try:
            # Direct attribute access for speed
            if not hasattr(member, 'primary_guild') or member.primary_guild is None:
                return False
            
            primary_guild = member.primary_guild
            if not hasattr(primary_guild, 'tag') or primary_guild.tag is None:
                return False
            
            return primary_guild.tag == self.server_tag
        except Exception:
            return False

    async def has_promotion_content(self, member):
        """ULTRA-FAST: Check status and primary guild tag ONLY"""
        
        # 1. Check Primary Guild Tag (INSTANT - no API call)
        if self.has_primary_guild_tag(member):
            return True
        
        # 2. Check Custom Status (ONLY for online members)
        if member.status != discord.Status.offline:
            status_content = self.get_status_content(member)
            if status_content:
                for invite in self.your_server_invites:
                    if invite.lower() in status_content:
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
            status_content = self.get_status_content(member)
            if status_content:
                sources.append(f"Status: {status_content}")
        
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

    async def instant_member_check(self, member, reason):
        """ULTRA-FAST instant check - called immediately on changes"""
        try:
            role = member.guild.get_role(self.promoter_role_id)
            if not role:
                return
            
            current_has_promotion = await self.has_promotion_content(member)
            currently_has_role = role in member.roles

            if current_has_promotion and not currently_has_role:
                # Give promoter role INSTANTLY
                promotion_content = await self.get_promotion_content(member)
                await member.add_roles(role, reason=f"Instant: {reason}")
                self.log_promotion_action(
                    member.guild.id, member.id, str(member),
                    'ROLE_ASSIGNED', f'Instant: {reason}', promotion_content
                )
                print(f"⚡ INSTANT ✅ {member.display_name} - Role assigned - {promotion_content}")
                await self.send_promotion_log(member, "assigned", promotion_content)
                
            elif not current_has_promotion and currently_has_role:
                # Remove promoter role INSTANTLY
                promotion_content = await self.get_promotion_content(member)
                await member.remove_roles(role, reason=f"Instant: {reason}")
                self.log_promotion_action(
                    member.guild.id, member.id, str(member),
                    'ROLE_REMOVED', f'Instant: {reason}', promotion_content
                )
                print(f"⚡ INSTANT 🔄 {member.display_name} - Role removed - {promotion_content}")
                await self.send_promotion_log(member, "removed", promotion_content)
                
        except Exception as e:
            print(f"❌ Instant check error with {member.display_name}: {e}")

    @tasks.loop(seconds=30)
    async def fallback_check(self):
        """30-SECOND FALLBACK: Check for missed updates"""
        for guild in self.bot.guilds:
            role = guild.get_role(self.promoter_role_id)
            if not role:
                continue
                
            checked = 0
            corrections = 0
            
            # Quick check all members for mismatches
            for member in guild.members:
                if not member.bot:
                    has_tag = self.has_primary_guild_tag(member)
                    has_role = role in member.roles
                    
                    # If mismatch detected, do full check
                    if has_tag != has_role:
                        full_promotion = await self.has_promotion_content(member)
                        if full_promotion != has_role:
                            await self.instant_member_check(member, "30s fallback")
                            corrections += 1
                    
                    checked += 1
                    self.total_checks += 1
            
            if corrections > 0:
                print(f"🔄 30s Fallback: {guild.name} - {checked} checked, {corrections} corrected")

    @tasks.loop(hours=24)
    async def weekly_cleanup(self):
        """Update weekly stats"""
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

    @tasks.loop(hours=1)
    async def cleanup_old_stats(self):
        """Clean up old system stats"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM system_stats WHERE timestamp < datetime("now", "-7 days")')
            conn.commit()

    async def send_promotion_log(self, member, action_type, content=""):
        """Send log to designated channel"""
        try:
            channel = member.guild.get_channel(self.log_channel_id)
            if not channel:
                return
            
            if action_type == "assigned":
                embed = discord.Embed(
                    title="⚡ New Promoter!",
                    description=f"**{member.mention}** is promoting our server!",
                    color=0x00ff00,
                    timestamp=datetime.now()
                )
            else:
                embed = discord.Embed(
                    title="⚡Promoter Removed", 
                    description=f"**{member.mention}** stopped promoting",
                    color=0xff9900,
                    timestamp=datetime.now()
                )
            
            if content and content != "Unknown":
                embed.add_field(name="Promotion Source", value=f"```{content}```", inline=False)
            
            embed.add_field(name="Role Action", value=f"<@&{self.promoter_role_id}>", inline=True)
            embed.add_field(name="Detection", value="⚡ INSTANT", inline=True)
            embed.set_thumbnail(url=member.display_avatar.url)
            
            await channel.send(embed=embed)
            
        except Exception:
            pass

    # COMMANDS
    @commands.command(name='pgtag')
    async def pgtag(self, ctx, member: discord.Member = None):
        """Check a member's primary guild tag"""
        member = member or ctx.author

        try:
            primary_guild = member.primary_guild
            if primary_guild is None:
                return await ctx.reply("❌ No primary guild set.")

            tag = primary_guild.tag
            if tag is None:
                return await ctx.reply("⚠️ Primary guild but no tag set.")

            would_promote = tag == self.server_tag
            
            embed = discord.Embed(title="🏷️ Primary Guild Tag", color=0x7289da)
            embed.add_field(name="Member", value=member.mention, inline=True)
            embed.add_field(name="Tag", value=f"**{tag}**", inline=True)
            embed.add_field(name="Promoter", value="✅ YES" if would_promote else "❌ NO", inline=True)
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            await ctx.reply(f"❌ Error: {e}")

    @commands.command(name='test_status')
    async def test_status(self, ctx):
        """Test if status detection is working"""
        member = ctx.author
        current_status = self.get_status_content(member)
        has_promotion = await self.has_promotion_content(member)
        
        embed = discord.Embed(title="🧪 Status Test", color=0x7289da)
        embed.add_field(name="Current Status", value=f"`{current_status or 'None'}`", inline=True)
        embed.add_field(name="Has Promotion", value="✅ Yes" if has_promotion else "❌ No", inline=True)
        embed.add_field(name="Primary Tag", value="✅ Yes" if self.has_primary_guild_tag(member) else "❌ No", inline=True)
        
        await ctx.send(embed=embed)

    @commands.command(name='promoter_stats')
    @commands.has_permissions(manage_roles=True)
    async def promoter_stats(self, ctx):
        """Check bot performance"""
        try:
            memory_usage = psutil.Process(os.getpid()).memory_info().rss / 1024 ** 2
            cpu_usage = psutil.cpu_percent()
            
            role = ctx.guild.get_role(self.promoter_role_id)
            total_promoters = len([m for m in ctx.guild.members if role in m.roles and not m.bot]) if role else 0
            
            members_with_tag = 0
            for member in ctx.guild.members:
                if not member.bot and self.has_primary_guild_tag(member):
                    members_with_tag += 1
            
            embed = discord.Embed(title="🤖 ULTRA-FAST Promoter System", color=0x7289da)
            embed.add_field(name="💾 Memory", value=f"{memory_usage:.1f} MB", inline=True)
            embed.add_field(name="⚡ CPU", value=f"{cpu_usage:.1f}%", inline=True)
            embed.add_field(name="📊 Checks", value=f"{self.total_checks:,}", inline=True)
            embed.add_field(name="👥 Promoters", value=total_promoters, inline=True)
            embed.add_field(name="🏷️ Raही Tags", value=members_with_tag, inline=True)
            embed.add_field(name="🔄 Fallback", value="30 seconds", inline=True)
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            await ctx.send(f"❌ Error: {e}")

    @commands.command(name='force_check')
    @commands.has_permissions(manage_roles=True)
    async def force_check(self, ctx, member: discord.Member = None):
        """Force instant check"""
        if member:
            await self.instant_member_check(member, "Manual force check")
            await ctx.send(f"✅ Instantly checked {member.mention}")
        else:
            message = await ctx.send("⚡ Force checking all members...")
            checked = 0
            for member in ctx.guild.members:
                if not member.bot:
                    await self.instant_member_check(member, "Manual bulk check")
                    checked += 1
            
            await message.edit(content=f"✅ Instantly checked {checked} members")

    @commands.command(name='debug_promoter')
    @commands.has_permissions(manage_roles=True)
    async def debug_promoter(self, ctx, member: discord.Member = None):
        """Debug info"""
        target = member or ctx.author
        
        embed = discord.Embed(title=f"🐛 Debug - {target.display_name}", color=0xff9900)
        
        # Primary guild info
        primary_guild = target.primary_guild
        tag_value = primary_guild.tag if primary_guild and primary_guild.tag else "None"
        has_primary_tag = self.has_primary_guild_tag(target)
        
        embed.add_field(name="🏷️ Primary Tag", value=tag_value, inline=True)
        embed.add_field(name="🎯 Has Raही", value="✅ Yes" if has_primary_tag else "❌ No", inline=True)
        
        # Status info
        current_status = self.get_status_content(target)
        embed.add_field(name="📝 Status", value=f"`{current_status or 'None'}`", inline=True)
        
        # Promotion status
        has_promotion = await self.has_promotion_content(target)
        role = ctx.guild.get_role(self.promoter_role_id)
        has_role = role in target.roles if role else False
        
        embed.add_field(name="📢 Promotion", value="✅ Yes" if has_promotion else "❌ No", inline=True)
        embed.add_field(name="🎪 Has Role", value="✅ Yes" if has_role else "❌ No", inline=True)
        embed.add_field(name="🔍 Match", value="✅ Yes" if has_promotion == has_role else "❌ No", inline=True)
        
        await ctx.send(embed=embed)

    @commands.command(name='set_log_channel')
    @commands.has_permissions(administrator=True)
    async def set_log_channel(self, ctx):
        """Set log channel"""
        self.log_channel_id = ctx.channel.id
        await ctx.send("✅ Log channel set - Instant detection active!")

    @commands.command(name='promo_check')
    @commands.has_permissions(manage_roles=True)
    async def manual_promo_check(self, ctx, member: discord.Member = None):
        """Manual check"""
        target = member or ctx.author
        await self.instant_member_check(target, "Manual check command")
        await ctx.send(f"✅ Instantly checked {target.mention}")

    @commands.command(name='promo_help')
    async def promo_help(self, ctx):
        """Help"""
        embed = discord.Embed(title="🤖 ULTRA-FAST Promotion System", color=0x7289da)
        embed.add_field(name="⚡ Features", value="• **INSTANT** status detection\n• **INSTANT** primary tag detection\n• **30-second** fallback", inline=False)
        embed.add_field(name="🎯 Monitoring", value=f"• Status with: {', '.join(self.your_server_invites)}\n• Primary Guild Tag: `{self.server_tag}`", inline=False)
        embed.add_field(name="📝 Commands", value="`!pgtag` `!test_status` `!promoter_stats` `!force_check` `!debug_promoter` `!promo_check`", inline=False)
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(PromoterRole(bot))