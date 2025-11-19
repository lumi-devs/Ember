import discord
from discord.ext import tasks
import sqlite3
from datetime import datetime
import asyncio

class PromoterRole(discord.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_path = "promoter_role.db"
        
        # HARDCODED SETTINGS
        self.promoter_role_id = 1440636027923333182
        self.your_server_invites = [".gg/aakhristation", "discord.gg/aakhristation", "aakhristation"]
        
        self.init_db()
        self.active_check = False
        
    def init_db(self):
        """Initialize the database tables with optimized indexes"""
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
            
            # Create indexes for faster queries
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_logs_guild ON promoter_logs(guild_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_logs_member ON promoter_logs(member_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_logs_created ON promoter_logs(created_at)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_cache_guild ON member_cache(guild_id)')
            
            conn.commit()
    
    def log_promoter_action(self, guild_id, member_id, member_name, action, reason, content=""):
        """Fast logging with connection reuse"""
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
            
            # Update cache
            cursor.execute('''
                INSERT OR REPLACE INTO member_cache (member_id, guild_id, last_checked, has_promotion, promotion_content)
                VALUES (?, ?, CURRENT_TIMESTAMP, ?, ?)
            ''', (member_id, guild_id, action == 'ROLE_ASSIGNED', content))
            
            conn.commit()
    
    def get_cached_member_status(self, member_id):
        """Get cached member status for faster checks"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT has_promotion, promotion_content FROM member_cache WHERE member_id = ?', (member_id,))
            result = cursor.fetchone()
            return result if result else (False, "")
    
    def get_promoter_stats(self, guild_id):
        """Fast stats retrieval with caching"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Get total promotions
            cursor.execute('SELECT total_promotions FROM promoter_stats WHERE guild_id = ?', (guild_id,))
            result = cursor.fetchone()
            total_promotions = result[0] if result else 0
            
            # Get current active promoters (from cache)
            cursor.execute('SELECT COUNT(*) FROM member_cache WHERE guild_id = ? AND has_promotion = 1', (guild_id,))
            current_promoters = cursor.fetchone()[0]
            
            # Get weekly promotions
            cursor.execute('''
                SELECT COUNT(*) FROM promoter_logs 
                WHERE guild_id = ? AND action = 'ROLE_ASSIGNED' 
                AND created_at > datetime('now', '-7 days')
            ''', (guild_id,))
            weekly_promotions = cursor.fetchone()[0]
            
            return {
                'total_promotions': total_promotions,
                'weekly_promotions': weekly_promotions,
                'current_promoters': current_promoters
            }
    
    def get_top_promoters(self, guild_id, limit=10):
        """Get top promoters by duration"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT member_id, member_name, COUNT(*) as promo_count,
                       MAX(created_at) as last_promo
                FROM promoter_logs 
                WHERE guild_id = ? AND action = 'ROLE_ASSIGNED'
                GROUP BY member_id 
                ORDER BY promo_count DESC 
                LIMIT ?
            ''', (guild_id, limit))
            return cursor.fetchall()
        
    @discord.Cog.listener()
    async def on_ready(self):
        print(f"🚀 Ultra-Fast PromoterRole cog loaded!")
        print(f"📊 Monitoring for: {', '.join(self.your_server_invites)}")
        self.quick_check.start()
        self.detailed_check.start()
        self.stats_report.start()
    
    @discord.Cog.listener()
    async def on_presence_update(self, before, after):
        """Quick check on presence updates"""
        if self.active_check:
            return
            
        self.active_check = True
        try:
            await self.fast_member_check(after)
        finally:
            self.active_check = False
    
    @discord.Cog.listener()
    async def on_member_update(self, before, after):
        """Quick check on member updates"""
        if self.active_check:
            return
            
        self.active_check = True
        try:
            if (before.display_name != after.display_name or 
                before.global_name != after.global_name or
                before.nick != after.nick):
                await self.fast_member_check(after)
        finally:
            self.active_check = False
    
    async def fast_member_check(self, member):
        """Ultra-fast member check with caching"""
        if member.bot:
            return
            
        # Check cache first
        cached_has_promotion, cached_content = self.get_cached_member_status(member.id)
        current_has_promotion = await self.has_promotion_content(member)
        
        # If cache matches current state, skip
        if cached_has_promotion == current_has_promotion:
            return
            
        role = member.guild.get_role(self.promoter_role_id)
        if not role:
            return
        
        try:
            promotion_content = await self.get_promotion_content(member)
            
            if current_has_promotion and not cached_has_promotion:
                await member.add_roles(role, reason="Promoting our server 🎉")
                self.log_promoter_action(
                    member.guild.id, member.id, str(member), 
                    'ROLE_ASSIGNED', 'Auto-detected promotion', promotion_content
                )
                print(f"✅ {member.display_name} - Promoter role assigned")
                await self.send_promotion_alert(member, "assigned", promotion_content)
                
            elif not current_has_promotion and cached_has_promotion:
                await member.remove_roles(role, reason="No longer promoting")
                self.log_promoter_action(
                    member.guild.id, member.id, str(member),
                    'ROLE_REMOVED', 'Stopped promoting', promotion_content
                )
                print(f"🔄 {member.display_name} - Promoter role removed")
                await self.send_promotion_alert(member, "removed", promotion_content)
                
        except Exception as e:
            print(f"❌ Error with {member.display_name}: {e}")
    
    async def has_promotion_content(self, member):
        """Fast content checking with multiple sources"""
        # Check custom status and activities
        for activity in member.activities:
            if activity and activity.name:
                activity_text = activity.name.lower()
                for invite in self.your_server_invites:
                    if invite.lower() in activity_text:
                        return True
        
        # Check names
        name_checks = [
            member.display_name,
            member.global_name, 
            member.nick
        ]
        
        for name in name_checks:
            if name:
                name_lower = name.lower()
                for invite in self.your_server_invites:
                    if invite.lower() in name_lower:
                        return True
        
        # Check bio/profile (if accessible)
        try:
            # Try different methods to get bio
            if hasattr(member, 'bio') and member.bio:
                if any(invite.lower() in member.bio.lower() for invite in self.your_server_invites):
                    return True
                    
            # Try user profile (might require privileged intents)
            async for guild in self.bot.fetch_guilds(limit=10):
                guild_member = guild.get_member(member.id)
                if guild_member:
                    try:
                        profile = await guild_member.profile()
                        if profile and profile.bio:
                            if any(invite.lower() in profile.bio.lower() for invite in self.your_server_invites):
                                return True
                    except:
                        continue
        except:
            pass
            
        return False
    
    async def get_promotion_content(self, member):
        """Get the actual promotion content for logging"""
        contents = []
        
        # Check activities
        for activity in member.activities:
            if activity and activity.name:
                activity_text = activity.name
                for invite in self.your_server_invites:
                    if invite.lower() in activity_text.lower():
                        contents.append(f"Status: {activity_text}")
                        break
        
        # Check names
        name_checks = [
            ("Display", member.display_name),
            ("Global", member.global_name),
            ("Nick", member.nick)
        ]
        
        for name_type, name in name_checks:
            if name:
                for invite in self.your_server_invites:
                    if invite.lower() in name.lower():
                        contents.append(f"{name_type} Name: {name}")
                        break
        
        return " | ".join(contents) if contents else "Unknown"
    
    async def send_promotion_alert(self, member, action_type, content=""):
        """Enhanced embed alerts"""
        try:
            channel = member.guild.system_channel
            if not channel:
                for text_channel in member.guild.text_channels:
                    if text_channel.permissions_for(member.guild.me).send_messages:
                        channel = text_channel
                        break
            
            if channel:
                if action_type == "assigned":
                    embed = discord.Embed(
                        title="🎉 New Server Promoter!",
                        description=f"**{member.mention}** is now promoting our server!",
                        color=0x00ff00,
                        timestamp=datetime.now()
                    )
                    embed.add_field(name="Promotion Content", value=f"```{content[:500]}```", inline=False)
                    embed.add_field(name="Role Assigned", value=f"<@&{self.promoter_role_id}>", inline=True)
                    embed.add_field(name="Member Since", value=f"<t:{int(member.joined_at.timestamp())}:R>", inline=True)
                    embed.set_thumbnail(url=member.display_avatar.url)
                    
                else:  # removed
                    embed = discord.Embed(
                        title="🔄 Promoter Role Removed",
                        description=f"**{member.mention}** stopped promoting our server",
                        color=0xff9900,
                        timestamp=datetime.now()
                    )
                    embed.add_field(name="Previous Content", value=f"```{content[:500]}```", inline=False)
                
                embed.set_footer(text="Auto Promotion System • Real-time")
                await channel.send(embed=embed)
        except:
            pass
    
    @tasks.loop(minutes=5)
    async def quick_check(self):
        """Fast check for recent changes only"""
        for guild in self.bot.guilds:
            role = guild.get_role(self.promoter_role_id)
            if not role:
                continue
                
            # Check only members active in last hour
            async for member in guild.fetch_members(limit=None):
                if not member.bot and member.status != discord.Status.offline:
                    await asyncio.sleep(0.1)  # Prevent rate limits
                    await self.fast_member_check(member)
    
    @tasks.loop(minutes=30)
    async def detailed_check(self):
        """Comprehensive check of all members"""
        print("🔍 Running detailed promoter check...")
        
        for guild in self.bot.guilds:
            role = guild.get_role(self.promoter_role_id)
            if not role:
                continue
                
            updated = 0
            async for member in guild.fetch_members(limit=None):
                if not member.bot:
                    await self.fast_member_check(member)
                    updated += 1
                    await asyncio.sleep(0.05)  # Small delay to prevent flooding
            
            if updated > 0:
                print(f"📊 {guild.name}: Detailed check completed for {updated} members")
    
    @tasks.loop(hours=24)
    async def stats_report(self):
        """Enhanced daily stats with top promoters"""
        for guild in self.bot.guilds:
            stats = self.get_promoter_stats(guild.id)
            top_promoters = self.get_top_promoters(guild.id, 5)
            
            try:
                channel = guild.system_channel
                if channel:
                    embed = discord.Embed(
                        title="📊 Promotion Analytics Dashboard",
                        color=0x7289da,
                        timestamp=datetime.now()
                    )
                    
                    # Stats section
                    embed.add_field(
                        name="📈 Current Stats",
                        value=f"**Active Promoters:** {stats['current_promoters']}\n"
                              f"**Weekly Growth:** {stats['weekly_promotions']}\n"
                              f"**Total Promotions:** {stats['total_promotions']}",
                        inline=False
                    )
                    
                    # Top promoters section
                    if top_promoters:
                        top_text = ""
                        for i, (member_id, name, count, last_promo) in enumerate(top_promoters, 1):
                            top_text += f"{i}. <@{member_id}> - {count} promotions\n"
                        
                        embed.add_field(
                            name="🏆 Top Promoters",
                            value=top_text,
                            inline=False
                        )
                    
                    embed.set_footer(text="Auto Promotion System • Daily Analytics")
                    await channel.send(embed=embed)
                    
            except Exception as e:
                print(f"Stats report error: {e}")

async def setup(bot):
    await bot.add_cog(PromoterRole(bot))