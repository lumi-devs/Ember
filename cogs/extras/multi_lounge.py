import discord
from discord.ext import commands, tasks
import re
import asyncio
import traceback
import time
import json
import os
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
from typing import Dict, List, Tuple

class SmartLoungeCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        
        # --- LOGGING SETUP (Fixes Errno 5) ---
        self.logger = logging.getLogger('SmartLounge')
        self.logger.setLevel(logging.INFO)
        # Create a file handler that rotates logs (max 5MB, keeps 2 backups)
        handler = RotatingFileHandler('lounge.log', maxBytes=5*1024*1024, backupCount=2, encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        if not self.logger.handlers:
            self.logger.addHandler(handler)
        
        # --- CONFIGURATION ---
        self.base_lounge_id = 1393171112753631243  # Lounge 2 (Permanent Base)
        self.busy_threshold = 2     # How many users make a room "Busy"?
        self.lounge_name_fmt = "┊ 🔊﹕₊˚⌇lounge {} ꜝꜝ‹3" 
        
        self.name_pattern = re.compile(r'lounge\s+(\d+)', re.IGNORECASE)
        
        # Safety locks to prevent race conditions
        self.creation_lock = asyncio.Lock()
        self.deletion_lock = asyncio.Lock()
        
        # Track last creation time to prevent rapid creation
        self.last_creation_time = 0
        self.creation_cooldown = 10  # seconds between creations
        
        # JSON file for persistence
        self.data_file = "data/lounge_data.json"
        
        # Initialize or load statistics
        self.stats = self.load_stats()

    def load_stats(self):
        """Load statistics from JSON file or create default."""
        default_stats = {
            'total_creations': 0,
            'total_deletions': 0,
            'last_creation_time': None,
            'last_deletion_time': None,
            'peak_users': 0,
            'peak_users_time': None,
            'errors': [],
            'loop_executions': 0,
            'avg_users_per_lounge': 0.0,
            'busy_percentage': 0.0
        }
        
        try:
            if os.path.exists(self.data_file):
                with open(self.data_file, 'r') as f:
                    loaded = json.load(f)
                    
                    # Convert string timestamps back to datetime objects
                    for time_key in ['last_creation_time', 'last_deletion_time', 'peak_users_time']:
                        if loaded.get(time_key):
                            loaded[time_key] = datetime.fromisoformat(loaded[time_key])
                    
                    return loaded
            else:
                return default_stats
        except Exception as e:
            self.logger.error(f"⚠️ Error loading stats from JSON: {e}")
            return default_stats

    def save_stats(self):
        """Save statistics to JSON file."""
        try:
            # Create a copy to avoid modifying the original
            stats_to_save = self.stats.copy()
            
            # Convert datetime objects to ISO format strings
            for time_key in ['last_creation_time', 'last_deletion_time', 'peak_users_time']:
                if stats_to_save.get(time_key) and isinstance(stats_to_save[time_key], datetime):
                    stats_to_save[time_key] = stats_to_save[time_key].isoformat()
            
            with open(self.data_file, 'w') as f:
                json.dump(stats_to_save, f, indent=2)
            return True
        except Exception as e:
            self.logger.error(f"❌ Error saving stats to JSON: {e}")
            return False

    def save_stats_async(self):
        """Save stats asynchronously to prevent blocking."""
        try:
            stats_to_save = self.stats.copy()
            
            for time_key in ['last_creation_time', 'last_deletion_time', 'peak_users_time']:
                if stats_to_save.get(time_key) and isinstance(stats_to_save[time_key], datetime):
                    stats_to_save[time_key] = stats_to_save[time_key].isoformat()
            
            # Use a temporary file to prevent corruption
            temp_file = self.data_file + ".tmp"
            with open(temp_file, 'w') as f:
                json.dump(stats_to_save, f, indent=2)
            
            # Atomic rename
            os.replace(temp_file, self.data_file)
        except Exception as e:
            self.logger.error(f"❌ Async save error: {e}")

    @commands.Cog.listener()
    async def on_ready(self):
        self.logger.info(f"✅ Smart Lounge System Ready (2-min heartbeat). Data saved to: {self.data_file}")
        if not self.manage_lounges_loop.is_running():
            self.manage_lounges_loop.start()

    def cog_unload(self):
        """Clean up when cog is unloaded."""
        if self.manage_lounges_loop.is_running():
            self.manage_lounges_loop.cancel()
        # Save stats on unload
        self.save_stats()
        self.logger.info(f"🛑 Smart Lounge System stopped. Stats saved.")

    def get_lounge_number(self, channel_name):
        match = self.name_pattern.search(channel_name)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                return 0
        return 0

    async def get_lounge_state(self) -> Tuple[discord.CategoryChannel, List[Dict]]:
        """Scans all lounges and sorts them."""
        base_channel = self.bot.get_channel(self.base_lounge_id)
        if not base_channel or not base_channel.category:
            return None, None

        category = base_channel.category
        lounges = []

        # 1. Add Base Lounge (Lounge 2)
        lounges.append({
            'num': 1,
            'channel': base_channel,
            'count': len(base_channel.members),
            'id': base_channel.id,
            'name': base_channel.name,
            'created_at': base_channel.created_at if hasattr(base_channel, 'created_at') else None
        })

        # 2. Find and ID all other lounges (Lounge 3+)
        for channel in category.voice_channels:
            if channel.id == self.base_lounge_id:
                continue
            
            num = self.get_lounge_number(channel.name)
            if num > 1: 
                lounges.append({
                    'num': num,
                    'channel': channel,
                    'count': len(channel.members),
                    'id': channel.id,
                    'name': channel.name,
                    'created_at': channel.created_at if hasattr(channel, 'created_at') else None
                })
        
        # 3. Sort by Number (2, 3, 4...)
        lounges.sort(key=lambda x: x['num'])
        return category, lounges

    @tasks.loop(minutes=2)
    async def manage_lounges_loop(self):
        """Main loop to manage lounges with error handling."""
        try:
            await self._manage_lounges()
        except Exception as e:
            error_msg = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}: {str(e)}"
            self.stats['errors'].append(error_msg[:200])  # Keep only first 200 chars
            if len(self.stats['errors']) > 10:  # Keep only last 10 errors
                self.stats['errors'] = self.stats['errors'][-10:]
            
            # Use logger instead of print to avoid IO Error
            self.logger.error(f"❌ Smart Lounge loop error: {e}", exc_info=True)
        finally:
            # Always try to save stats after each loop execution
            try:
                # Save stats asynchronously to prevent blocking
                await asyncio.to_thread(self.save_stats_async)
            except Exception as e:
                self.logger.warning(f"⚠️ Could not save stats: {e}")

    async def _manage_lounges(self):
            """Core lounge management logic."""
            self.stats['loop_executions'] += 1
            
            category, lounges = await self.get_lounge_state()
            if not category or not lounges:
                return

            # Update statistics
            total_users = sum(l['count'] for l in lounges)
            total_lounges = len(lounges)
            
            self.stats['avg_users_per_lounge'] = total_users / total_lounges if total_lounges > 0 else 0
            
            busy_lounges = sum(1 for l in lounges if l['count'] >= self.busy_threshold)
            self.stats['busy_percentage'] = (busy_lounges / total_lounges * 100) if total_lounges > 0 else 0
            
            # Track peak users
            if total_users > self.stats['peak_users']:
                self.stats['peak_users'] = total_users
                self.stats['peak_users_time'] = datetime.now()

            # --- PHASE 1: CREATION (The "Fill the Holes" Logic) ---
            
            # Check: Are ALL lounges currently busy?
            all_lounges_busy = all(l['count'] >= self.busy_threshold for l in lounges)
            
            # Check cooldown
            current_time = time.time()
            can_create = (current_time - self.last_creation_time) >= self.creation_cooldown

            if all_lounges_busy and can_create:
                async with self.creation_lock:
                    # Double-check condition inside lock
                    category, lounges = await self.get_lounge_state()
                    if not lounges:
                        return
                        
                    all_lounges_busy = all(l['count'] >= self.busy_threshold for l in lounges)
                    if not all_lounges_busy:
                        return
                    
                    # All rooms are full. We need more space.
                    highest_num = lounges[-1]['num']
                    highest_channel_id = lounges[-1]['id'] # Get the ID
                    next_num = highest_num + 1
                    
                    self.logger.info(f"📈 All lounges busy. Creating Lounge {next_num}...")
                    
                    # --- FIX: FORCE FETCH POSITION ---
                    # We fetch the channel fresh from API to get the absolute real-time position
                    try:
                        fresh_highest_channel = await self.bot.fetch_channel(highest_channel_id)
                        target_position = fresh_highest_channel.position 
                    except:
                        # Fallback to cached position if fetch fails
                        target_position = lounges[-1]['channel'].position 
                    
                    # Create directly below the highest channel using the FRESH position
                    success = await self.create_lounge(category, next_num, target_position)
                    if success:
                        self.stats['total_creations'] += 1
                        self.stats['last_creation_time'] = datetime.now()
                        self.last_creation_time = current_time
                    return  # We created one, wait for next loop.

            # --- PHASE 2: DELETION (The "Keep Lowest Spare" Logic) ---
            
            # Identify "Custom Spares" (Lounge 3+ that are completely empty)
            custom_spares = [l for l in lounges if l['count'] == 0 and l['num'] > 2]

            # We allow exactly 1 custom spare to exist (Lounge 3).
            # If we have > 1, we must delete the extras.
            if len(custom_spares) > 1:
                async with self.deletion_lock:
                    # Re-check inside lock
                    category, lounges = await self.get_lounge_state()
                    if not lounges:
                        return
                        
                    custom_spares = [l for l in lounges if l['count'] == 0 and l['num'] > 2]
                    if len(custom_spares) <= 1:
                        return
                    
                    # Sort spares by number: [3, 4, 5]
                    custom_spares.sort(key=lambda x: x['num'])

                    # Logic: KEEP the lowest number (Index 0). DELETE the rest (Index 1+).
                    spare_to_keep = custom_spares[0]
                    spares_to_delete = custom_spares[1:] 
                    
                    # Sort deletion list from Highest -> Lowest (5, then 4)
                    spares_to_delete.sort(key=lambda x: x['num'], reverse=True)

                    for item in spares_to_delete:
                        self.logger.info(f"📉 Excess Spares: Deleting Lounge {item['num']} (Keeping Lounge {spare_to_keep['num']})")
                        success = await self.delete_lounge_safe(item['channel'])
                        if success:
                            self.stats['total_deletions'] += 1
                            self.stats['last_deletion_time'] = datetime.now()
                        await asyncio.sleep(1)  # Safety delay
            # --- PHASE 3: FIX NUMBERING GAPS ---
            await self.fix_numbering_gaps(lounges)
                        
    async def fix_numbering_gaps(self, lounges):
        """Fix numbering gaps in lounge sequence."""
        fixed = False
        
        # Check for gaps
        expected_number = 1  # Start from Lounge 2
        for lounge in lounges:
            if lounge['num'] != expected_number:
                # Found a gap or mis-numbering
                old_name = lounge['channel'].name
                new_name = self.lounge_name_fmt.format(expected_number)
                
                if old_name != new_name:
                    try:
                        await lounge['channel'].edit(name=new_name, 
                                                    reason=f"Lounge Logic: Fixed numbering gap {lounge['num']} → {expected_number}")
                        self.logger.info(f"🔢 Renamed Lounge {lounge['num']} → Lounge {expected_number}")
                        fixed = True
                    except Exception as e:
                        self.logger.error(f"❌ Failed to rename lounge: {e}")
            
            expected_number += 1
        
        return fixed

    async def create_lounge(self, category, number, position):
        """Create a new lounge channel."""
        try:
            name = self.lounge_name_fmt.format(number)
            await category.create_voice_channel(
                name=name,
                position=position,
                reason=f"Lounge Logic: Created Lounge {number}"
            )
            self.logger.info(f"✅ Created Lounge {number}")
            return True
        except discord.Forbidden:
            self.logger.warning(f"❌ Permission denied to create Lounge {number}")
        except discord.HTTPException as e:
            self.logger.error(f"❌ HTTP error creating Lounge {number}: {e}")
        except Exception as e:
            self.logger.error(f"❌ Error creating lounge {number}: {e}", exc_info=True)
        return False

    async def delete_lounge_safe(self, channel):
        """Safely delete a lounge channel."""
        try:
            # Check if channel still exists
            if not channel or channel.guild.get_channel(channel.id) is None:
                return False
                
            # Final "Last Second" check to make sure no one just joined
            if len(channel.members) == 0:
                await channel.delete(reason="Lounge Logic: Excess Empty Room")
                self.logger.info(f"✅ Deleted Lounge {self.get_lounge_number(channel.name)}")
                return True
            else:
                self.logger.info(f"⚠️ Lounge {self.get_lounge_number(channel.name)} has users, skipping deletion")
                return False
        except discord.NotFound:
            return False  # Already deleted
        except discord.Forbidden:
            self.logger.warning(f"❌ Permission denied to delete Lounge {self.get_lounge_number(channel.name)}")
            return False
        except Exception as e:
            self.logger.error(f"❌ Error deleting channel: {e}", exc_info=True)
            return False

    @manage_lounges_loop.before_loop
    async def before_loop(self):
        await self.bot.wait_until_ready()

    @commands.command(name="ls", aliases=["loungestats", "lstat"])
    @commands.has_permissions(manage_channels=True)
    async def lounge_stats(self, ctx: commands.Context):
        """
        📊 Shows EVERY STAT regarding the Smart Lounge system.
        Only usable by moderators.
        """
        # Get current state
        category, lounges = await self.get_lounge_state()
        
        if not category or not lounges:
            await ctx.send("❌ Could not fetch lounge data. System may not be initialized.")
            return
        
        # Calculate stats
        total_users = sum(l['count'] for l in lounges)
        total_lounges = len(lounges)
        active_lounges = sum(1 for l in lounges if l['count'] > 0)
        empty_lounges = sum(1 for l in lounges if l['count'] == 0)
        busy_lounges = sum(1 for l in lounges if l['count'] >= self.busy_threshold)
        
        avg_users = total_users / total_lounges if total_lounges > 0 else 0
        utilization = (active_lounges / total_lounges * 100) if total_lounges > 0 else 0

        # --- PREDICTIVE LOGIC FOR NEXT ACTION ---
        all_lounges_busy = all(l['count'] >= self.busy_threshold for l in lounges)
        current_time = time.time()
        cooldown_remaining = max(0, int(self.creation_cooldown - (current_time - self.last_creation_time)))
        can_create = cooldown_remaining <= 0
        
        custom_spares = [l for l in lounges if l['count'] == 0 and l['num'] > 2]
        
        if all_lounges_busy:
            if can_create:
                next_action = f"📈 **CREATE:** All rooms busy. Will create Lounge {lounges[-1]['num'] + 1}."
            else:
                next_action = f"⏳ **WAIT:** All rooms busy, but on cooldown for `{cooldown_remaining}s`."
        elif len(custom_spares) > 1:
            spare_nums = [l['num'] for l in custom_spares[1:]]
            next_action = f"📉 **DELETE:** Excess spares detected. Will remove Lounge(s): `{', '.join(map(str, spare_nums))}`."
        else:
            next_action = "✅ **STABLE:** No creation or deletion needed at this time."
        
        # Create detailed embed
        embed = discord.Embed(
            title="📊 SMART LOUNGE SYSTEM STATISTICS",
            description="Lounge Management Metrics",
            color=discord.Color.purple(),
            timestamp=datetime.now()
        )

        # 1. Prediction Field (Added as requested)
        embed.add_field(
            name="🤖 PREDICTED NEXT ACTION",
            value=next_action,
            inline=False
        )
        
        # 2. Basic Info
        embed.add_field(
            name="🏢 SYSTEM INFO",
            value=(
                f"**Category:** {category.name}\n"
                f"**Busy Threshold:** {self.busy_threshold} users\n"
                f"**Creation Cooldown:** {self.creation_cooldown}s\n"
            ),
            inline=True
        )

        # 3. Statistics
        embed.add_field(
            name="🔢 NUMERICAL STATS",
            value=(
                f"**Total Lounges:** {total_lounges}\n"
                f"**Busy Lounges:** {busy_lounges} ({self.stats['busy_percentage']:.1f}%)\n"
                f"**Total Users:** {total_users}\n"
            ),
            inline=True
        )
        
        # 4. Timestamps
        last_creation = self.stats['last_creation_time']
        peak_time = self.stats['peak_users_time']
        
        embed.add_field(
            name="⏰ TIMESTAMPS",
            value=(
                f"**Last Creation:** {last_creation.strftime('%H:%M:%S') if last_creation else 'Never'}\n"
                f"**Peak Time:** {peak_time.strftime('%H:%M:%S') if peak_time else 'Never'}\n"
                f"**Loop Next Run:** <t:{int((datetime.now().timestamp() + 120))}:R>\n"
            ),
            inline=False
        )

        # ... (Remaining parts of the embed like current state and health)
        current_state = "\n".join([
            f"**L{l['num']}:** {l['count']} users {'🚨' if l['count'] >= self.busy_threshold else '💤' if l['count'] == 0 else '✅'}"
            for l in lounges[:10]
        ])
        embed.add_field(name="📈 CURRENT STATE (Top 10)", value=current_state, inline=False)
        
        await ctx.send(embed=embed)
        
        # Timestamps
        last_creation = self.stats['last_creation_time']
        last_deletion = self.stats['last_deletion_time']
        peak_time = self.stats['peak_users_time']
        
        embed.add_field(
            name="⏰ TIMESTAMPS",
            value=(
                f"**Last Creation:** {last_creation.strftime('%Y-%m-%d %H:%M:%S') if last_creation else 'Never'}\n"
                f"**Last Deletion:** {last_deletion.strftime('%Y-%m-%d %H:%M:%S') if last_deletion else 'Never'}\n"
                f"**Peak Time:** {peak_time.strftime('%Y-%m-%d %H:%M:%S') if peak_time else 'Never'}\n"
                f"**Loop Next Run:** <t:{int((datetime.now().timestamp() + 120))}:R>\n"
                f"**Last Save:** Now\n"
            ),
            inline=False
        )
        
        # Error Log (last 5)
        if self.stats['errors']:
            error_list = "\n".join([f"• {e}" for e in self.stats['errors'][-5:]])
            embed.add_field(
                name="⚠️ RECENT ERRORS (Last 5)",
                value=error_list,
                inline=False
            )
        
        # System Health
        health_status = "✅ HEALTHY"
        health_color = discord.Color.green()
        
        # Check for issues
        issues = []
        if len(self.stats['errors']) > 5:
            issues.append("High error count")
        if self.stats['loop_executions'] == 0:
            issues.append("Loop not running")
        if not category:
            issues.append("Category not found")
        if not os.path.exists(self.data_file):
            issues.append("Data file missing")
            
        if issues:
            health_status = "⚠️ WARNING"
            health_color = discord.Color.orange()
            embed.add_field(
                name="🚨 SYSTEM ISSUES",
                value="\n".join([f"• {issue}" for issue in issues]),
                inline=False
            )
        
        embed.color = health_color
        embed.set_footer(text=f"Requested by {ctx.author} | System: {health_status} | Data: {self.data_file}")
        
        await ctx.send(embed=embed)
        
        # Also send detailed lounge list if requested
        if len(lounges) <= 15:  # Only if not too many
            detailed = "\n".join([
                f"**#{l['num']}:** {l['name']} → {l['count']} user{'s' if l['count'] != 1 else ''} "
                f"(ID: `{l['id']}`)"
                for l in lounges
            ])
            
            detailed_embed = discord.Embed(
                title="📋 DETAILED LOUNGE LIST",
                description=detailed,
                color=discord.Color.blue(),
                timestamp=datetime.now()
            )
            detailed_embed.set_footer(text=f"Total: {total_lounges} lounges")
            await ctx.send(embed=detailed_embed)

    @lounge_stats.error
    async def lounge_stats_error(self, ctx, error):
        """Error handler for lounge stats command."""
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ You need `Manage Channels` permission to use this command!")
        elif isinstance(error, commands.BotMissingPermissions):
            await ctx.send("❌ I need `Embed Links` permission to show stats!")
        else:
            await ctx.send(f"❌ Error: {str(error)}")
            # For command errors, print to log
            self.logger.error(f"Command Error: {error}", exc_info=True)

async def setup(bot):
    await bot.add_cog(SmartLoungeCog(bot))
    print("✅ Smart Lounge Cog loaded with JSON persistence.")