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
                (channel_id, guild_id, number, created_at)
                VALUES (?, ?, ?, ?)
            """, (channel_id, guild_id, number, time.time()))
            await db.commit()
        self._cache[channel_id] = {'number': number}
    
    async def remove_lounge(self, channel_id: int):
        """Remove lounge from database"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM lounges WHERE channel_id = ?", (channel_id,))
            await db.commit()
        self._cache.pop(channel_id, None)

class DynamicLounges(commands.Cog):
    """Simple lounge system - creates when ALL lounges have 2+ users"""
    
    def __init__(self, bot):
        self.bot = bot
        self.state = PersistentLoungeManager()
        
        # Configuration
        self.initial_channel_id = 1455864869030068285  # Lounge 3
        self.base_name = "┊ 🔊﹕₊˚⌇lounge"
        self.name_suffix = "  ꜝꜝ‹3"
        
        # Settings
        self.creation_delay = 30  # Wait 30 seconds before checking
        self.max_lounges = 15
        self.min_users_per_lounge = 2  # Must have 2+ users
        
        # Base capacity rules
        self.base_capacity = {
            4: 8, 5: 8,   # Lounge 4-5: 8 users
            6: 6, 7: 6,   # Lounge 6-7: 6 users
            8: 4,         # Lounge 8+: 4 users
        }
        
        # Track creation cooldown
        self._last_creation = {}
        self._cooldown = 60  # 1 minute cooldown between creations
    
    async def cog_load(self):
        """Initialize on load"""
        os.makedirs("data", exist_ok=True)
        await self.state.initialize()
        logger.info("✅ Lounge system loaded")
    
    def _get_lounge_name(self, number: int) -> str:
        """Generate lounge name"""
        return f"{self.base_name} {number}{self.name_suffix}"
    
    def _parse_lounge_number(self, name: str) -> Optional[int]:
        """Parse number from lounge name"""
        match = re.search(r'lounge\s*(\d+)', name, re.IGNORECASE)
        return int(match.group(1)) if match else None
    
    def _get_capacity(self, number: int) -> int:
        """Get capacity based on lounge number"""
        return self.base_capacity.get(number, 4)  # Default to 4
    
    async def _should_create_lounge(self, guild: discord.Guild) -> bool:
        """
        DECIDES WHEN TO CREATE A NEW LOUNGE
        
        CONDITIONS:
        1. Someone must be in Lounge 3
        2. ALL existing dynamic lounges (4+) must have 2+ users
        3. Must be under max lounges (15)
        4. Not in cooldown period
        
        CREATION FLOW:
        • Someone joins Lounge 3 → wait 30 seconds → check conditions
        • If all conditions met → create next lounge below Lounge 3
        """
        
        # 1. Check if Lounge 3 exists and has users
        lounge3 = guild.get_channel(self.initial_channel_id)
        if not lounge3 or len(lounge3.members) == 0:
            return False  # No one in Lounge 3
        
        # 2. Check max lounges
        dynamic_count = 0
        for channel in guild.voice_channels:
            num = self._parse_lounge_number(channel.name)
            if num and num >= 4:  # Count dynamic lounges (4+)
                dynamic_count += 1
        
        if dynamic_count >= (self.max_lounges - 3):  # -3 because Lounge 3 is fixed
            return False  # Max lounges reached
        
        # 3. Check if ALL dynamic lounges have minimum users
        for channel in guild.voice_channels:
            num = self._parse_lounge_number(channel.name)
            if num and num >= 4:  # Only check dynamic lounges
                if len(channel.members) < self.min_users_per_lounge:
                    return False  # Found a lounge without enough users
        
        # 4. Check cooldown
        current_time = time.time()
        last_creation = self._last_creation.get(guild.id, 0)
        if current_time - last_creation < self._cooldown:
            return False  # In cooldown
        
        # ALL CONDITIONS MET - Create new lounge!
        return True
    
    async def _create_next_lounge(self, guild: discord.Guild):
        """Create the next lounge in sequence"""
        try:
            lounge3 = guild.get_channel(self.initial_channel_id)
            if not lounge3:
                return
            
            # Find existing lounge numbers
            existing_numbers = {3}  # Always include Lounge 3
            
            for channel in guild.voice_channels:
                num = self._parse_lounge_number(channel.name)
                if num and num >= 3:
                    existing_numbers.add(num)
            
            # Find next available number (4, 5, 6...)
            next_num = 4
            while next_num in existing_numbers:
                next_num += 1
            
            # Check max
            if next_num > self.max_lounges:
                return
            
            # Get capacity for this lounge number
            capacity = self._get_capacity(next_num)
            
            # Create RIGHT BELOW Lounge 3
            name = self._get_lounge_name(next_num)
            new_channel = await guild.create_voice_channel(
                name=name,
                category=lounge3.category,
                position=lounge3.position + 1,  # Directly below Lounge 3
                user_limit=capacity
            )
            
            # Add to database
            await self.state.add_lounge(guild.id, new_channel.id, next_num)
            
            # Update cooldown
            self._last_creation[guild.id] = time.time()
            
            logger.info(f"✅ Created {name} (Capacity: {capacity})")
            
            # Reorder lounges to keep sequence
            await self._reorder_lounges(guild)
            
        except Exception as e:
            logger.error(f"❌ Create error: {e}")
    
    async def _reorder_lounges(self, guild: discord.Guild):
        """Reorder lounges to keep them sequential below Lounge 3"""
        try:
            lounge3 = guild.get_channel(self.initial_channel_id)
            if not lounge3:
                return
            
            # Get all lounge channels
            lounges = []
            for channel in guild.voice_channels:
                num = self._parse_lounge_number(channel.name)
                if num and num >= 3:
                    lounges.append((num, channel))
            
            # Sort by number
            lounges.sort(key=lambda x: x[0])
            
            # Reposition starting right below Lounge 3
            current_pos = lounge3.position + 1
            
            # Skip Lounge 3 (index 0) and position the rest
            for num, channel in lounges[1:]:  # Start from Lounge 4
                if channel.position != current_pos:
                    try:
                        await channel.edit(position=current_pos)
                    except:
                        pass  # Discord might limit rate
                current_pos += 1
                
        except Exception as e:
            logger.error(f"Reorder error: {e}")
    
    async def _delete_empty_lounges(self, guild: discord.Guild):
        """Delete empty dynamic lounges (except Lounge 3)"""
        for channel in guild.voice_channels:
            if channel.id == self.initial_channel_id:
                continue  # Never delete Lounge 3
            
            num = self._parse_lounge_number(channel.name)
            if num and num >= 4 and len(channel.members) == 0:
                try:
                    await channel.delete()
                    await self.state.remove_lounge(channel.id)
                    logger.info(f"🧹 Deleted empty {channel.name}")
                    await self._reorder_lounges(guild)
                except:
                    pass
    
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        """Trigger lounge creation checks"""
        if member.bot:
            return
        
        # Check when someone joins Lounge 3
        if after.channel and after.channel.id == self.initial_channel_id:
            # Wait for the delay, then check conditions
            await asyncio.sleep(self.creation_delay)
            
            # Check if we should create a new lounge
            if await self._should_create_lounge(after.channel.guild):
                await self._create_next_lounge(after.channel.guild)
        
        # Check for empty lounges to delete
        await self._delete_empty_lounges(member.guild)
    
    @commands.command(name="ls", aliases=["lounges"])
    async def lounge_stats_cmd(self, ctx):
        """Show lounge status with clear creation rules"""
        try:
            lounge3 = ctx.guild.get_channel(self.initial_channel_id)
            if not lounge3:
                await ctx.send("❌ Lounge 3 not found!")
                return
            
            # Get all lounge channels
            lounge_channels = []
            for channel in ctx.guild.voice_channels:
                num = self._parse_lounge_number(channel.name)
                if num and num >= 3:
                    capacity = self._get_capacity(num)
                    lounge_channels.append({
                        'number': num,
                        'channel': channel,
                        'users': len(channel.members),
                        'capacity': capacity
                    })
            
            # Sort by number
            lounge_channels.sort(key=lambda x: x['number'])
            
            # Create embed
            embed = discord.Embed(
                title="🎯 Lounge System Status",
                color=discord.Color.blue(),
                timestamp=discord.utils.utcnow()
            )
            
            # Show Lounge 3 status
            embed.add_field(
                name="🔧 Generator (Lounge 3)",
                value=f"**Users:** {len(lounge3.members)}/∞\n{'✅ Active' if len(lounge3.members) > 0 else '❌ Empty'}",
                inline=False
            )
            
            # Show dynamic lounges if any
            if len(lounge_channels) > 1:
                dynamic_lounges = lounge_channels[1:]  # Skip Lounge 3
                
                lounge_info = []
                total_users = 0
                lounges_with_enough_users = 0
                
                for lounge in dynamic_lounges:
                    channel = lounge['channel']
                    users = lounge['users']
                    capacity = lounge['capacity']
                    total_users += users
                    
                    # Check if this lounge meets minimum requirement
                    has_enough = users >= self.min_users_per_lounge
                    if has_enough:
                        lounges_with_enough_users += 1
                    
                    status = "✅" if has_enough else "❌"
                    lounge_info.append(f"{status} **{channel.name}:** {users}/{capacity}")
                
                embed.add_field(
                    name=f"🎭 Dynamic Lounges ({len(dynamic_lounges)}/{self.max_lounges-3})",
                    value="\n".join(lounge_info) if lounge_info else "None",
                    inline=False
                )
                
                # Show creation status
                can_create = await self._should_create_lounge(ctx.guild)
                
                if can_create:
                    status_text = "✅ **READY TO CREATE NEXT LOUNGE**"
                    status_color = discord.Color.green()
                else:
                    # Show what's missing
                    missing = []
                    
                    if len(lounge3.members) == 0:
                        missing.append("• Someone in Lounge 3")
                    
                    if lounges_with_enough_users < len(dynamic_lounges):
                        missing.append(f"• All lounges need {self.min_users_per_lounge}+ users")
                    
                    if len(dynamic_lounges) >= (self.max_lounges - 3):
                        missing.append("• Max lounges reached")
                    
                    if missing:
                        status_text = "❌ **CANNOT CREATE**\n" + "\n".join(missing)
                    else:
                        status_text = "⚠️ **CHECKING CONDITIONS...**"
                    
                    status_color = discord.Color.orange()
                
                # Add status field with color
                status_embed = discord.Embed(
                    title="⚡ Creation Status",
                    description=status_text,
                    color=status_color
                )
                
                # Add stats
                status_embed.add_field(
                    name="📊 Stats",
                    value=(
                        f"**Total Lounges:** {len(lounge_channels)}\n"
                        f"**Total Users:** {total_users + len(lounge3.members)}\n"
                        f"**Lounges Ready:** {lounges_with_enough_users}/{len(dynamic_lounges)}"
                    ),
                    inline=False
                )
                
                # Add rules
                rules = (
                    f"**Creation Rules:**\n"
                    f"1. ✅ Someone in Lounge 3\n"
                    f"2. ✅ All lounges (4+) have {self.min_users_per_lounge}+ users\n"
                    f"3. ✅ Under {self.max_lounges} lounges total\n"
                    f"4. ✅ Not in cooldown ({self._cooldown}s)\n\n"
                    f"**Capacity Rules:**\n"
                    f"• Lounge 4-5: 8 users\n"
                    f"• Lounge 6-7: 6 users\n"
                    f"• Lounge 8+: 4 users"
                )
                
                status_embed.add_field(name="📝 Rules", value=rules, inline=False)
                
                # Send both embeds
                await ctx.send(embed=embed)
                await ctx.send(embed=status_embed)
                
            else:
                # No dynamic lounges yet
                embed.add_field(
                    name="🎭 Dynamic Lounges",
                    value="No lounges created yet! Join Lounge 3 to start.",
                    inline=False
                )
                
                rules = (
                    f"**To create Lounge 4:**\n"
                    f"1. Join Lounge 3 (wait {self.creation_delay}s)\n"
                    f"2. System checks conditions\n"
                    f"3. Creates Lounge 4 below Lounge 3"
                )
                
                embed.add_field(name="📝 How to Start", value=rules, inline=False)
                await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Error in ls command: {e}")
            await ctx.send(f"❌ Error: {str(e)}")
    
    @commands.command(name="lounge_test")
    @commands.has_permissions(administrator=True)
    async def test_conditions(self, ctx):
        """Test if creation conditions are met"""
        try:
            result = await self._should_create_lounge(ctx.guild)
            
            if result:
                await ctx.send("✅ **ALL CONDITIONS MET!** Ready to create next lounge.")
                await self._create_next_lounge(ctx.guild)
            else:
                # Show what's missing
                lounge3 = ctx.guild.get_channel(self.initial_channel_id)
                
                embed = discord.Embed(
                    title="❌ Conditions NOT Met",
                    color=discord.Color.red()
                )
                
                checks = []
                
                # Check 1: Lounge 3 has users
                if not lounge3 or len(lounge3.members) == 0:
                    checks.append("❌ No one in Lounge 3")
                else:
                    checks.append(f"✅ Lounge 3: {len(lounge3.members)} users")
                
                # Check 2: All dynamic lounges have 2+ users
                dynamic_lounges = []
                for channel in ctx.guild.voice_channels:
                    num = self._parse_lounge_number(channel.name)
                    if num and num >= 4:
                        dynamic_lounges.append((num, channel))
                
                if not dynamic_lounges:
                    checks.append("✅ No dynamic lounges yet (first creation ready)")
                else:
                    all_good = True
                    for num, channel in dynamic_lounges:
                        if len(channel.members) < 2:
                            all_good = False
                            checks.append(f"❌ Lounge {num}: Only {len(channel.members)}/2 users")
                        else:
                            checks.append(f"✅ Lounge {num}: {len(channel.members)}/2 users")
                    
                    if all_good:
                        checks.append("✅ All lounges have 2+ users")
                
                # Check 3: Max lounges
                if len(dynamic_lounges) >= (self.max_lounges - 3):
                    checks.append(f"❌ Max lounges reached ({self.max_lounges})")
                else:
                    checks.append(f"✅ Under max ({len(dynamic_lounges)}/{self.max_lounges-3})")
                
                embed.description = "\n".join(checks)
                await ctx.send(embed=embed)
                
        except Exception as e:
            logger.error(f"Test error: {e}")
            await ctx.send(f"❌ Error: {str(e)}")

async def setup(bot):
    await bot.add_cog(DynamicLounges(bot))