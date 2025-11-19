import discord
from discord.ext import tasks, commands
import sqlite3
from datetime import datetime
import asyncio

class PromoterRole(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_path = "promoter_role.db"
        
        # HARDCODED SETTINGS
        self.promoter_role_id = 1440636027923333182
        self.your_server_invites = [".gg/aakhristation", "discord.gg/aakhristation", "aakhristation"]
        self.log_channel_id = 1424139009931743382 
        
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
                CREATE TABLE IF NOT EXISTS promoter_stats (
                    guild_id INTEGER PRIMARY KEY,
                    total_promotions INTEGER DEFAULT 0,
                    active_promoters INTEGER DEFAULT 0,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS member_cache (
                    member_id INTEGER PRIMARY KEY,
                    guild_id INTEGER,
                    last_checked TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    has_promotion BOOLEAN DEFAULT FALSE,
                    promotion_content TEXT
                )
            ''')
            
            conn.commit()

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"🚀 Optimized PromoterRole cog loaded!")
        print(f"📊 Monitoring: Status & Global Name for {self.your_server_invites}")
        self.quick_check.start()
        self.detailed_check.start()
    
    @commands.Cog.listener()
    async def on_presence_update(self, before, after):
        """Check status changes"""
        if self.active_check:
            return
            
        self.active_check = True
        try:
            await self.fast_member_check(after)
        finally:
            self.active_check = False
    
    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        """Check global name changes"""
        if self.active_check:
            return
            
        self.active_check = True
        try:
            if before.global_name != after.global_name:
                await self.fast_member_check(after)
        finally:
            self.active_check = False

    async def has_promotion_content(self, member):
        """ULTRA-FAST: Check only status and global name"""
        
        # 1. Check Custom Status (FASTEST)
        for activity in member.activities:
            if isinstance(activity, discord.CustomActivity) and activity.name:
                status_text = activity.name.lower()
                for invite in self.your_server_invites:
                    if invite.lower() in status_text:
                        return True
        
        # 2. Check Global Name (FAST)
        if member.global_name:
            global_name_lower = member.global_name.lower()
            for invite in self.your_server_invites:
                if invite.lower() in global_name_lower:
                    return True
        
        return False

    async def get_promotion_content(self, member):
        """Get promotion source for logging"""
        sources = []
        
        # Check status
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
            
            if action == 'ROLE_ASSIGNED':
                cursor.execute('''
                    INSERT OR REPLACE INTO promoter_stats (guild_id, total_promotions, last_updated)
                    VALUES (?, COALESCE((SELECT total_promotions FROM promoter_stats WHERE guild_id = ?), 0) + 1, CURRENT_TIMESTAMP)
                ''', (guild_id, guild_id))
            
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
                embed.add_field(name="Detected In", value="Status/Global Name", inline=True)
                
            else:  # removed
                embed = discord.Embed(
                    title="🔄 PROMOTER REMOVED", 
                    description=f"**{member.mention}** stopped promoting",
                    color=0xff9900,
                    timestamp=datetime.now()
                )
                if content and content != "Unknown":
                    embed.add_field(name="Previous Promotion", value=f"```{content}```", inline=False)
            
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.set_footer(text="Auto Promotion Logger")
            
            await channel.send(embed=embed)
            
        except Exception as e:
            print(f"⚠️ Could not send log: {e}")

    async def fast_member_check(self, member):
        """ULTRA-FAST member check"""
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
                    'ROLE_ASSIGNED', 'Status/Name promotion', promotion_content
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
        """Fast check every 3 minutes"""
        for guild in self.bot.guilds:
            role = guild.get_role(self.promoter_role_id)
            if not role:
                continue
                
            checked = 0
            # Check only online members (reduces API calls)
            for member in guild.members:
                if not member.bot and member.status != discord.Status.offline:
                    await self.fast_member_check(member)
                    checked += 1
                    await asyncio.sleep(0.05)  # Rate limit protection
            
            if checked > 0:
                print(f"🔍 Quick: {guild.name} - {checked} online members")

    @tasks.loop(minutes=15)
    async def detailed_check(self):
        """Full check every 15 minutes"""
        print("🔍 Running full promoter scan...")
        
        total_checked = 0
        for guild in self.bot.guilds:
            role = guild.get_role(self.promoter_role_id)
            if not role:
                continue
                
            checked = 0
            for member in guild.members:
                if not member.bot:
                    await self.fast_member_check(member)
                    checked += 1
                    total_checked += 1
            
            if checked > 0:
                print(f"📊 {guild.name}: {checked} members scanned")
        
        print(f"✅ Full scan complete: {total_checked} total members")

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
        
        if promotion_content != "Unknown":
            embed.add_field(name="Detected In", value=f"```{promotion_content}```", inline=False)
        
        await ctx.send(embed=embed)

    @commands.command(name='promo_stats')
    async def promo_stats(self, ctx):
        """Show promotion statistics"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Total promotions
                cursor.execute('SELECT COUNT(*) FROM promoter_logs WHERE action = "ROLE_ASSIGNED"')
                total_promotions = cursor.fetchone()[0]
                
                # Current promoters
                cursor.execute('SELECT COUNT(*) FROM member_cache WHERE has_promotion = 1')
                current_promoters = cursor.fetchone()[0]
                
                # Today's promotions
                cursor.execute('SELECT COUNT(*) FROM promoter_logs WHERE action = "ROLE_ASSIGNED" AND date(created_at) = date("now")')
                today_promotions = cursor.fetchone()[0]
            
            embed = discord.Embed(
                title="📊 Promotion Statistics",
                color=0x7289da,
                timestamp=datetime.now()
            )
            
            embed.add_field(name="Current Promoters", value=current_promoters, inline=True)
            embed.add_field(name="Today's Promotions", value=today_promotions, inline=True)
            embed.add_field(name="Total Promotions", value=total_promotions, inline=True)
            
            embed.set_footer(text="Monitoring Status & Global Name")
            await ctx.send(embed=embed)
            
        except Exception as e:
            await ctx.send(f"❌ Error fetching stats: {e}")

def setup(bot):
    bot.add_cog(PromoterRole(bot))