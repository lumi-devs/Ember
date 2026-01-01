import discord
from discord.ext import commands, tasks
import asyncio
import aiosqlite
import time
import os
import re
import logging
from typing import Dict, List, Optional
from collections import defaultdict

logger = logging.getLogger(__name__)
DB_PATH = os.path.join("data", "lounges.db")

class PersistentLoungeManager:
    """Simple database-backed lounge manager"""
    
    def __init__(self):
        self.db_path = DB_PATH
        self._cache: Dict[int, dict] = {}
    
    async def initialize(self):
        """Create database if it doesn't exist"""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS lounges (
                    channel_id INTEGER PRIMARY KEY,
                    guild_id INTEGER NOT NULL,
                    number INTEGER NOT NULL,
                    tier INTEGER DEFAULT 1,
                    last_active REAL NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE(guild_id, number)
                )
            """)
            await db.commit()
    
    async def add_lounge(self, guild_id: int, channel_id: int, number: int):
        """Add a lounge to database"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO lounges 
                (channel_id, guild_id, number, tier, last_active, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (channel_id, guild_id, number, 1, time.time(), time.time()))
            await db.commit()
        self._cache[channel_id] = {
            'number': number,
            'tier': 1,
            'last_active': time.time()
        }
    
    async def update_lounge(self, channel_id: int, **kwargs):
        """Update lounge properties"""
        if channel_id in self._cache:
            for key, value in kwargs.items():
                if key in self._cache[channel_id]:
                    self._cache[channel_id][key] = value
            
            if 'tier' in kwargs or 'last_active' in kwargs:
                async with aiosqlite.connect(self.db_path) as db:
                    await db.execute("""
                        UPDATE lounges SET tier = ?, last_active = ? 
                        WHERE channel_id = ?
                    """, (
                        self._cache[channel_id]['tier'],
                        self._cache[channel_id]['last_active'],
                        channel_id
                    ))
                    await db.commit()
    
    async def remove_lounge(self, channel_id: int):
        """Remove lounge from database"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM lounges WHERE channel_id = ?", (channel_id,))
            await db.commit()
        self._cache.pop(channel_id, None)

class DynamicLounges(commands.Cog):
    """Sleek lounge system with all essential features"""
    
    def __init__(self, bot):
        self.bot = bot
        self.state = PersistentLoungeManager()
        
        # Configuration
        self.initial_channel_id = 1455864869030068285  # Lounge 3
        self.base_name = "┊ 🔊﹕₊˚⌇lounge"
        self.name_suffix = "  ꜝꜝ‹3"
        
        # Settings
        self.creation_delay = 30  # Wait 30 seconds
        self.max_lounges = 15
        self.min_users_per_lounge = 2  # Lounges 4+ need 2+ users
        self.inactivity_timer = 300  # 5 minutes for auto-delete
        self.lounge3_min_users = 3  # Lounge 3 needs 3+ users
        
        # Capacity rules
        self.base_capacity = {
            4: 8, 5: 8,   # Lounge 4-5: 8 users
            6: 6, 7: 6,   # Lounge 6-7: 6 users
            8: 4,         # Lounge 8+: 4 users
        }
        
        # Promotion system
        self.promotion_boost = {1: 0, 2: 2, 3: 4, 4: 6}
        self.promotion_emoji = {1: "", 2: "⭐", 3: "💎", 4: "👑"}
        
        # Task
        self.cleanup_task = None
        self.promotion_task = None
        
        # Track ongoing creations
        self._creating = False
    
    async def cog_load(self):
        """Initialize on load"""
        os.makedirs("data", exist_ok=True)
        await self.state.initialize()
        
        # Start background tasks
        self.cleanup_task = self._create_cleanup_task()
        self.promotion_task = self._create_promotion_task()
        
        self.cleanup_task.start()
        self.promotion_task.start()
        
        logger.info("✅ Lounge system loaded")
    
    def _create_cleanup_task(self):
        """Create task to delete inactive lounges"""
        @tasks.loop(minutes=1)
        async def task():
            for guild in self.bot.guilds:
                try:
                    await self._cleanup_inactive_lounges(guild)
                except Exception as e:
                    logger.error(f"Cleanup error: {e}")
        
        @task.before_loop
        async def before():
            await self.bot.wait_until_ready()
        
        return task
    
    def _create_promotion_task(self):
        """Create task to promote active lounges"""
        @tasks.loop(minutes=2)
        async def task():
            for guild in self.bot.guilds:
                try:
                    await self._check_and_promote_lounges(guild)
                except Exception as e:
                    logger.error(f"Promotion error: {e}")
        
        @task.before_loop
        async def before():
            await self.bot.wait_until_ready()
        
        return task
    
    async def cog_unload(self):
        """Cleanup on unload"""
        if self.cleanup_task:
            self.cleanup_task.cancel()
        if self.promotion_task:
            self.promotion_task.cancel()
    
    def _get_lounge_name(self, number: int, tier: int = 1) -> str:
        """Generate lounge name with emoji"""
        base_name = f"{self.base_name} {number}{self.name_suffix}"
        emoji = self.promotion_emoji.get(tier, "")
        return f"{base_name} {emoji}" if emoji else base_name
    
    def _parse_lounge_number(self, name: str) -> Optional[int]:
        """Parse number from lounge name"""
        clean_name = re.sub(r'[⭐💎👑]', '', name).strip()
        match = re.search(r'lounge\s*(\d+)', clean_name, re.IGNORECASE)
        return int(match.group(1)) if match else None
    
    def _get_capacity(self, number: int, tier: int) -> int:
        """Calculate capacity based on number and tier"""
        base = self.base_capacity.get(number, 4)  # Default to 4
        boost = self.promotion_boost.get(tier, 0)
        return base + boost
    
    async def _should_create_lounge(self, guild: discord.Guild) -> bool:
        """
        DECIDES WHEN TO CREATE A NEW LOUNGE
        
        ALL conditions must be TRUE:
        1. Lounge 3 has 3+ users
        2. ALL dynamic lounges (4+) have 2+ users
        3. Under max lounges limit (15 total)
        """
        
        # 1. Check Lounge 3
        lounge3 = guild.get_channel(self.initial_channel_id)
        if not lounge3 or len(lounge3.members) < self.lounge3_min_users:
            return False  # Lounge 3 needs 3+ users
        
        # 2. Check max lounges
        dynamic_count = 0
        for channel in guild.voice_channels:
            num = self._parse_lounge_number(channel.name)
            if num and num >= 4:
                dynamic_count += 1
        
        if dynamic_count >= (self.max_lounges - 3):
            return False  # Max lounges reached
        
        # 3. Check ALL dynamic lounges have minimum users
        for channel in guild.voice_channels:
            num = self._parse_lounge_number(channel.name)
            if num and num >= 4:
                if len(channel.members) < self.min_users_per_lounge:
                    return False  # Found a lounge without enough users
        
        return True  # ALL conditions met!
    
    async def _create_next_lounge(self, guild: discord.Guild):
        """Create the next lounge below Lounge 3"""
        try:
            lounge3 = guild.get_channel(self.initial_channel_id)
            if not lounge3:
                return
            
            # Find existing lounge numbers
            existing_numbers = {3}
            for channel in guild.voice_channels:
                num = self._parse_lounge_number(channel.name)
                if num and num >= 3:
                    existing_numbers.add(num)
            
            # Find next available number
            next_num = 4
            while next_num in existing_numbers:
                next_num += 1
            
            if next_num > self.max_lounges:
                return
            
            # Create lounge
            name = self._get_lounge_name(next_num, 1)
            capacity = self._get_capacity(next_num, 1)
            
            new_channel = await guild.create_voice_channel(
                name=name,
                category=lounge3.category,
                position=lounge3.position + 1,  # Directly below Lounge 3
                user_limit=capacity
            )
            
            # Add to database
            await self.state.add_lounge(guild.id, new_channel.id, next_num)
            logger.info(f"✅ Created {name} (Capacity: {capacity})")
            
            # Reorder to keep sequence
            await self._reorder_lounges(guild)
            
        except Exception as e:
            logger.error(f"❌ Create error: {e}")
    
    async def _reorder_lounges(self, guild: discord.Guild):
        """Keep lounges in sequential order below Lounge 3"""
        try:
            lounge3 = guild.get_channel(self.initial_channel_id)
            if not lounge3:
                return
            
            # Get all lounges
            lounges = []
            for channel in guild.voice_channels:
                num = self._parse_lounge_number(channel.name)
                if num and num >= 3:
                    lounges.append((num, channel))
            
            # Sort by number
            lounges.sort(key=lambda x: x[0])
            
            # Position sequentially below Lounge 3
            current_pos = lounge3.position + 1
            for num, channel in lounges[1:]:  # Skip Lounge 3
                if channel.position != current_pos:
                    try:
                        await channel.edit(position=current_pos)
                    except:
                        pass
                current_pos += 1
                
        except Exception as e:
            logger.error(f"Reorder error: {e}")
    
    async def _cleanup_inactive_lounges(self, guild: discord.Guild):
        """Delete lounges inactive for 5 minutes (except Lounge 3)"""
        current_time = time.time()
        
        for channel in guild.voice_channels:
            if channel.id == self.initial_channel_id:
                continue  # Never delete Lounge 3
            
            num = self._parse_lounge_number(channel.name)
            if num and num >= 4:
                # Check if empty
                if len(channel.members) == 0:
                    # Check last active time from database
                    entry = self.state._cache.get(channel.id)
                    if entry:
                        last_active = entry.get('last_active', 0)
                        inactive_for = current_time - last_active
                        
                        if inactive_for >= self.inactivity_timer:
                            try:
                                await channel.delete()
                                await self.state.remove_lounge(channel.id)
                                logger.info(f"🧹 Deleted inactive {channel.name}")
                                await self._reorder_lounges(guild)
                            except:
                                pass
    
    async def _check_and_promote_lounges(self, guild: discord.Guild):
        """Promote lounges based on activity and users"""
        for channel in guild.voice_channels:
            num = self._parse_lounge_number(channel.name)
            if not num or num < 4:
                continue
            
            users = len(channel.members)
            if users == 0:
                continue
            
            entry = self.state._cache.get(channel.id, {})
            current_tier = entry.get('tier', 1)
            last_active = entry.get('last_active', 0)
            
            # Update last active time
            await self.state.update_lounge(channel.id, last_active=time.time())
            
            # Calculate how long lounge has been active
            active_duration = time.time() - last_active
            
            # Check promotion conditions
            new_tier = current_tier
            
            # Tier 4: Elite - 10+ users for 30+ minutes
            if current_tier < 4 and users >= 10 and active_duration >= 1800:
                new_tier = 4
            
            # Tier 3: Premium - 8+ users for 20+ minutes  
            elif current_tier < 3 and users >= 8 and active_duration >= 1200:
                new_tier = 3
            
            # Tier 2: Popular - 5+ users for 10+ minutes
            elif current_tier < 2 and users >= 5 and active_duration >= 600:
                new_tier = 2
            
            # Apply promotion if needed
            if new_tier > current_tier:
                await self.state.update_lounge(channel.id, tier=new_tier)
                
                # Update capacity
                capacity = self._get_capacity(num, new_tier)
                try:
                    await channel.edit(user_limit=capacity)
                except:
                    pass
                
                # Update name with emoji
                new_name = self._get_lounge_name(num, new_tier)
                if channel.name != new_name:
                    try:
                        await channel.edit(name=new_name)
                    except:
                        pass
                
                logger.info(f"🌟 {channel.name} promoted to tier {new_tier}")
    
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        """Trigger lounge creation when Lounge 3 gets users"""
        if member.bot or self._creating:
            return
        
        # When someone joins Lounge 3
        if after.channel and after.channel.id == self.initial_channel_id:
            await asyncio.sleep(self.creation_delay)
            
            if await self._should_create_lounge(after.channel.guild):
                self._creating = True
                try:
                    await self._create_next_lounge(after.channel.guild)
                finally:
                    self._creating = False
    
    @commands.command(name="ls", aliases=["lounges"])
    async def lounge_stats_cmd(self, ctx):
        """Show lounge status with all info"""
        try:
            lounge3 = ctx.guild.get_channel(self.initial_channel_id)
            if not lounge3:
                await ctx.send("❌ Lounge 3 not found!")
                return
            
            # Get all lounges
            lounges_data = []
            for channel in ctx.guild.voice_channels:
                num = self._parse_lounge_number(channel.name)
                if num and num >= 3:
                    entry = self.state._cache.get(channel.id, {})
                    tier = entry.get('tier', 1)
                    capacity = self._get_capacity(num, tier)
                    
                    lounges_data.append({
                        'number': num,
                        'channel': channel,
                        'users': len(channel.members),
                        'tier': tier,
                        'capacity': capacity,
                        'entry': entry
                    })
            
            # Sort by number
            lounges_data.sort(key=lambda x: x['number'])
            
            # Create main embed
            embed = discord.Embed(
                title="🎯 Lounge System Status",
                color=discord.Color.blue(),
                timestamp=discord.utils.utcnow()
            )
            
            # Show Lounge 3 status
            lounge3_users = len(lounge3.members)
            lounge3_status = "✅" if lounge3_users >= self.lounge3_min_users else "❌"
            embed.add_field(
                name="🔧 Lounge 3 (Generator)",
                value=f"{lounge3_status} **Users:** {lounge3_users}/{self.lounge3_min_users}+ required",
                inline=False
            )
            
            # Show dynamic lounges
            if len(lounges_data) > 1:
                dynamic = lounges_data[1:]  # Skip Lounge 3
                
                lounge_info = []
                for data in dynamic:
                    channel = data['channel']
                    users = data['users']
                    tier = data['tier']
                    capacity = data['capacity']
                    
                    # Check if meets minimum
                    has_enough = users >= self.min_users_per_lounge
                    status = "✅" if has_enough else "❌"
                    
                    # Add tier emoji
                    tier_emoji = self.promotion_emoji.get(tier, "")
                    
                    lounge_info.append(
                        f"{status} **{channel.name}:** {users}/{capacity} {tier_emoji}"
                    )
                
                if lounge_info:
                    embed.add_field(
                        name=f"🎭 Dynamic Lounges ({len(dynamic)}/{self.max_lounges-3})",
                        value="\n".join(lounge_info[:10]),  # First 10
                        inline=False
                    )
                    
                    # Create second embed if more than 10
                    if len(lounge_info) > 10:
                        embed2 = discord.Embed(
                            title="🎭 Dynamic Lounges (Continued)",
                            color=discord.Color.blue()
                        )
                        embed2.add_field(
                            name="",
                            value="\n".join(lounge_info[10:]),
                            inline=False
                        )
                        await ctx.send(embed=embed)
                        await ctx.send(embed=embed2)
                    else:
                        await ctx.send(embed=embed)
                else:
                    await ctx.send(embed=embed)
            else:
                embed.add_field(
                    name="🎭 Dynamic Lounges",
                    value="No dynamic lounges yet",
                    inline=False
                )
                await ctx.send(embed=embed)
            
            # Create status embed
            status_embed = discord.Embed(
                title="⚡ System Status",
                color=discord.Color.green()
            )
            
            # Check creation conditions
            can_create = await self._should_create_lounge(ctx.guild)
            
            if can_create:
                status_text = "✅ **READY TO CREATE NEXT LOUNGE**"
            else:
                # Show what's missing
                missing = []
                
                if len(lounge3.members) < self.lounge3_min_users:
                    missing.append(f"• Lounge 3 needs {self.lounge3_min_users}+ users (currently {len(lounge3.members)})")
                
                # Check dynamic lounges
                dynamic_lounges = lounges_data[1:] if len(lounges_data) > 1 else []
                if dynamic_lounges:
                    lounges_with_enough = sum(1 for d in dynamic_lounges if d['users'] >= self.min_users_per_lounge)
                    if lounges_with_enough < len(dynamic_lounges):
                        missing.append(f"• All lounges need {self.min_users_per_lounge}+ users ({lounges_with_enough}/{len(dynamic_lounges)} ready)")
                
                if len(dynamic_lounges) >= (self.max_lounges - 3):
                    missing.append(f"• Max lounges reached ({self.max_lounges} total)")
                
                status_text = "❌ **CANNOT CREATE**\n" + "\n".join(missing) if missing else "⚠️ **WAITING FOR CONDITIONS**"
            
            status_embed.add_field(
                name="Creation Status",
                value=status_text,
                inline=False
            )
            
            # Add rules
            rules = (
                f"**Creation Rules:**\n"
                f"1. Lounge 3: {self.lounge3_min_users}+ users\n"
                f"2. All lounges (4+): {self.min_users_per_lounge}+ users\n"
                f"3. Max: {self.max_lounges} total lounges\n\n"
                
                f"**Auto-Delete:**\n"
                f"• Empty for {self.inactivity_timer//60} minutes\n"
                f"• Never deletes Lounge 3\n\n"
                
                f"**Promotions:**\n"
                f"⭐ Popular: 5+ users for 10min → +2 capacity\n"
                f"💎 Premium: 8+ users for 20min → +4 capacity\n"
                f"👑 Elite: 10+ users for 30min → +6 capacity"
            )
            
            status_embed.add_field(
                name="📝 Rules & Features",
                value=rules,
                inline=False
            )
            
            await ctx.send(embed=status_embed)
            
        except Exception as e:
            logger.error(f"Error in ls command: {e}")
            await ctx.send(f"❌ Error: {str(e)}")
    
    @commands.command(name="lounge_promote", aliases=["promote"])
    @commands.has_permissions(administrator=True)
    async def promote_cmd(self, ctx, lounge_number: int):
        """Manually promote a lounge"""
        try:
            # Find lounge
            target = None
            for channel in ctx.guild.voice_channels:
                num = self._parse_lounge_number(channel.name)
                if num == lounge_number and num >= 4:
                    target = channel
                    break
            
            if not target:
                await ctx.send(f"❌ Lounge {lounge_number} not found!")
                return
            
            # Promote
            entry = self.state._cache.get(target.id, {})
            current_tier = entry.get('tier', 1)
            
            if current_tier >= 4:
                await ctx.send(f"❌ {target.name} is already Elite!")
                return
            
            new_tier = current_tier + 1
            await self.state.update_lounge(target.id, tier=new_tier)
            
            # Update capacity and name
            capacity = self._get_capacity(lounge_number, new_tier)
            new_name = self._get_lounge_name(lounge_number, new_tier)
            
            await target.edit(user_limit=capacity, name=new_name)
            
            await ctx.send(f"✅ {target.name} promoted to tier {new_tier}!")
            
        except Exception as e:
            logger.error(f"Promote error: {e}")
            await ctx.send(f"❌ Error: {str(e)}")

async def setup(bot):
    await bot.add_cog(DynamicLounges(bot))