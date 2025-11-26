import discord
from discord.ext import tasks, commands
import sqlite3
from datetime import datetime, timedelta
import asyncio

class PromoterRole(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_path = "promoter_role.db"
        
        # HARDCODED SETTINGS
        self.promoter_role_id = 1440636027923333182
        self.your_server_invites = [".gg/aakhristation", "discord.gg/aakhristation", "aakhristation"]
        self.server_tag = "Raही"  # Specific server tag to monitor
        self.log_channel_id = 1443119503285293098 
        
        self.init_db()
        self.active_check = False
        
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
            
            conn.commit()

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"🚀 Optimized PromoterRole cog loaded!")
        print(f"📊 Monitoring: Status, Global Name & Server Tag '{self.server_tag}' for {self.your_server_invites}")
        self.quick_check.start()
        self.weekly_cleanup.start()
    
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
    
    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        """Check global name and server tag changes - ALL MEMBERS (including offline)"""
        if self.active_check:
            return
            
        self.active_check = True
        try:
            # Check for global name changes OR server tag changes
            if (before.global_name != after.global_name or 
                self.has_server_tag(before) != self.has_server_tag(after)):
                await self.fast_member_check(after)
        finally:
            self.active_check = False

    def has_server_tag(self, member):
        """Check if member has the specific server tag in their display name"""
        if not member.display_name:
            return False
        return self.server_tag in member.display_name

    async def has_promotion_content(self, member):
        """ULTRA-FAST: Check status, global name, and server tag"""
        
        # 1. Check Server Tag (works for ALL members - online/offline)
        if self.has_server_tag(member):
            return True
        
        # 2. Check Custom Status (ONLY for online members)
        if member.status != discord.Status.offline:
            for activity in member.activities:
                if isinstance(activity, discord.CustomActivity) and activity.name:
                    status_text = activity.name.lower()
                    for invite in self.your_server_invites:
                        if invite.lower() in status_text:
                            return True
        
        # 3. Check Global Name (works for ALL members)
        if member.global_name:
            global_name_lower = member.global_name.lower()
            for invite in self.your_server_invites:
                if invite.lower() in global_name_lower:
                    return True
        
        return False

    async def get_promotion_content(self, member):
        """Get promotion source for logging"""
        sources = []
        
        # Check server tag
        if self.has_server_tag(member):
            sources.append(f"Server Tag: {member.display_name}")
        
        # Check status (only for online members)
        if member.status != discord.Status.offline:
            for activity in member.activities:
                if isinstance(activity, discord.CustomActivity) and activity.name:
                    status_text = activity.name
                    for invite in self.your_server_invites:
                        if invite.lower() in status_text.lower():
                            sources.append(f"Status: {status_text}")
                            break
        
        # Check global name
        if member.global_name:
            for invite in self.your_server_invites:
                if invite.lower() in member.global_name.lower():
                    sources.append(f"Global Name: {member.global_name}")
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
        """ULTRA-FAST member check - ALL MEMBERS (server tag works for offline too)"""
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
                    'ROLE_ASSIGNED', 'Status/Name/Tag promotion', promotion_content
                )
                print(f"✅ {member.display_name} - Promoter role assigned")
                await self.send_promotion_log(member, "assigned", promotion_content)
                
            elif not current_has_promotion and currently_has_role:
                # Remove promoter role
                promotion_content = await self.get_promotion_content(member)
                await member.remove_roles(role, reason="Promotion removed")
                self.log_promotion_action(
                    member.guild.id, member.id, str(member),
                    'ROLE_REMOVED', 'Stopped promoting', promotion_content
                )
                print(f"🔄 {member.display_name} - Promoter role removed")
                await self.send_promotion_log(member, "removed", promotion_content)
                
        except Exception as e:
            print(f"❌ Error with {member.display_name}: {e}")

    @tasks.loop(minutes=3)
    async def quick_check(self):
        """Fast check every 3 minutes - ALL MEMBERS (for server tag)"""
        for guild in self.bot.guilds:
            role = guild.get_role(self.promoter_role_id)
            if not role:
                continue
                
            checked = 0
            # Check ALL members (including offline) for server tag
            for member in guild.members:
                if not member.bot:
                    await self.fast_member_check(member)
                    checked += 1
                    await asyncio.sleep(0.03)  # Reduced rate limit
            
            if checked > 0:
                print(f"🔍 Quick: {guild.name} - {checked} total members")

    @tasks.loop(hours=24)
    async def weekly_cleanup(self):
        """Update weekly stats - ALL PROMOTERS (including offline with server tag)"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Get current promoters count (ALL members - including offline with server tag)
            for guild in self.bot.guilds:
                role = guild.get_role(self.promoter_role_id)
                if role:
                    # Count ALL promoters (including offline with server tag)
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

    # COMMANDS
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
        embed.add_field(name="Has Server Tag", value="✅ Yes" if self.has_server_tag(target) else "❌ No", inline=True)
        
        if promotion_content != "Unknown":
            embed.add_field(name="Detected In", value=f"```{promotion_content}```", inline=False)
        
        await ctx.send(embed=embed)

    @commands.command(name='promo_stats')
    @commands.has_permissions(manage_roles=True)
    async def promo_stats(self, ctx):
        """Show current promotion statistics"""
        try:
            role = ctx.guild.get_role(self.promoter_role_id)
            if not role:
                await ctx.send("❌ Promoter role not found!")
                return
            
            # Current promoters (ALL - including offline)
            total_promoters = len([m for m in ctx.guild.members if role in m.roles and not m.bot])
            online_promoters = len([m for m in ctx.guild.members if role in m.roles and not m.bot and m.status != discord.Status.offline])
            
            # This week's stats
            week_start = datetime.now().date() - timedelta(days=datetime.now().weekday())
            
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Get this week's data
                cursor.execute('''
                    SELECT promoters_count FROM weekly_stats 
                    WHERE week_start = ? AND guild_id = ?
                ''', (week_start, ctx.guild.id))
                
                week_data = cursor.fetchone()
                weekly_high = week_data[0] if week_data else 0
                
                # Get last week's data for comparison
                last_week_start = week_start - timedelta(days=7)
                cursor.execute('''
                    SELECT promoters_count FROM weekly_stats 
                    WHERE week_start = ? AND guild_id = ?
                ''', (last_week_start, ctx.guild.id))
                
                last_week_data = cursor.fetchone()
                last_week_high = last_week_data[0] if last_week_data else 0
            
            # Calculate trend
            if last_week_high > 0:
                trend = ((weekly_high - last_week_high) / last_week_high) * 100
                trend_text = f"{trend:+.1f}% from last week"
            else:
                trend_text = "N/A (no previous data)"
            
            embed = discord.Embed(
                title="📊 Promotion Statistics",
                description="*Tracking ALL promoters (including offline with server tag)*",
                color=0x7289da,
                timestamp=datetime.now()
            )
            
            embed.add_field(name="👥 Total Promoters", value=total_promoters, inline=True)
            embed.add_field(name="🟢 Online Promoters", value=online_promoters, inline=True)
            embed.add_field(name="📈 This Week High", value=weekly_high, inline=True)
            embed.add_field(name="📉 Trend", value=trend_text, inline=True)
            
            embed.set_footer(text="Monitoring: Status (Online) • Global Name • Server Tag (All)")
            await ctx.send(embed=embed)
            
        except Exception as e:
            await ctx.send(f"❌ Error fetching stats: {e}")

    @commands.command(name='promo_help')
    async def promo_help(self, ctx):
        """Show help for promotion commands"""
        embed = discord.Embed(
            title="🤖 Promotion System Help",
            description="Automatically detects when members promote the server in their status/global name/server tag",
            color=0x7289da
        )
        
        commands_list = [
            ("`!promo_check [member]`", "Check a member's promotion status (Manage Roles required)"),
            ("`!promo_stats`", "View promotion statistics (Manage Roles required)"),
            ("`!set_log_channel`", "Set current channel as log channel (Admin only)"),
            ("`!promo_help`", "Show this help message")
        ]
        
        for cmd, desc in commands_list:
            embed.add_field(name=cmd, value=desc, inline=False)
        
        embed.add_field(
            name="🔍 Monitoring", 
            value=f"• **Status** (online members only)\n• **Global Name** (all members)\n• **Server Tag** `{self.server_tag}` (all members)\n• Updates every 3 minutes", 
            inline=False
        )
        
        embed.add_field(
            name="⚡ Optimized", 
            value="• Server tag works for offline members\n• Faster response times\n• Reduced API calls", 
            inline=False
        )
        
        embed.set_footer(text=f"Monitoring: {', '.join(self.your_server_invites)} + '{self.server_tag}' tag")
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(PromoterRole(bot))