import discord
from discord.ext import commands, tasks
import re
import asyncio
import traceback
import time
from datetime import datetime
from typing import Dict, List, Tuple

class SmartLoungeCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        
        # --- CONFIGURATION ---
        self.base_lounge_id = 1394677032922648747  # Lounge 2 (Permanent Base)
        self.busy_threshold = 2     # How many users make a room "Busy"?
        self.lounge_name_fmt = "┊ 🔊﹕₊˚⌇lounge {} ꜝꜝ‹3" 
        
        self.name_pattern = re.compile(r'lounge\s+(\d+)', re.IGNORECASE)
        
        # Safety locks to prevent race conditions
        self.creation_lock = asyncio.Lock()
        self.deletion_lock = asyncio.Lock()
        
        # Track last creation time to prevent rapid creation
        self.last_creation_time = 0
        self.creation_cooldown = 10  # seconds between creations
        
        # Statistics tracking
        self.stats = {
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

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"✅ Smart Lounge System Ready (2-min heartbeat).")
        if not self.manage_lounges_loop.is_running():
            self.manage_lounges_loop.start()

    def cog_unload(self):
        """Clean up when cog is unloaded."""
        if self.manage_lounges_loop.is_running():
            self.manage_lounges_loop.cancel()
        print("🛑 Smart Lounge System stopped.")

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
            'num': 2,
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
            if num > 2: 
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
            print(f"❌ Smart Lounge loop error: {e}")
            traceback.print_exc()

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
                highest_channel = lounges[-1]['channel']
                next_num = highest_num + 1
                
                print(f"📈 All lounges busy. Creating Lounge {next_num}...")
                
                # Create directly below the highest channel
                success = await self.create_lounge(category, next_num, highest_channel.position + 1)
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
                # This prevents the discord list from jumping around weirdly.
                spares_to_delete.sort(key=lambda x: x['num'], reverse=True)

                for item in spares_to_delete:
                    print(f"📉 Excess Spares: Deleting Lounge {item['num']} (Keeping Lounge {spare_to_keep['num']})")
                    success = await self.delete_lounge_safe(item['channel'])
                    if success:
                        self.stats['total_deletions'] += 1
                        self.stats['last_deletion_time'] = datetime.now()
                    await asyncio.sleep(1)  # Safety delay

    async def create_lounge(self, category, number, position):
        """Create a new lounge channel."""
        try:
            name = self.lounge_name_fmt.format(number)
            await category.create_voice_channel(
                name=name,
                position=position,
                reason=f"Lounge Logic: Created Lounge {number}"
            )
            print(f"✅ Created Lounge {number}")
            return True
        except discord.Forbidden:
            print(f"❌ Permission denied to create Lounge {number}")
        except discord.HTTPException as e:
            print(f"❌ HTTP error creating Lounge {number}: {e}")
        except Exception as e:
            print(f"❌ Error creating lounge {number}: {e}")
            traceback.print_exc()
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
                print(f"✅ Deleted Lounge {self.get_lounge_number(channel.name)}")
                return True
            else:
                print(f"⚠️ Lounge {self.get_lounge_number(channel.name)} has users, skipping deletion")
                return False
        except discord.NotFound:
            return False  # Already deleted
        except discord.Forbidden:
            print(f"❌ Permission denied to delete Lounge {self.get_lounge_number(channel.name)}")
            return False
        except Exception as e:
            print(f"❌ Error deleting channel: {e}")
            traceback.print_exc()
            return False

    @manage_lounges_loop.before_loop
    async def before_loop(self):
        await self.bot.wait_until_ready()

    @commands.command(name="ls", aliases=["loungestats", "lstat"])
    @commands.has_permissions(manage_channels=True)
    async def lounge_stats(self, ctx: commands.Context):
        """
        📊 Shows EVERY FUCKIN STAT regarding the Smart Lounge system.
        Only usable by moderators.
        """
        # Get current state
        category, lounges = await self.get_lounge_state()
        
        if not category or not lounges:
            await ctx.send("❌ Could not fetch lounge data. System may not be initialized.")
            return
        
        # Calculate more stats
        total_users = sum(l['count'] for l in lounges)
        total_lounges = len(lounges)
        active_lounges = sum(1 for l in lounges if l['count'] > 0)
        empty_lounges = sum(1 for l in lounges if l['count'] == 0)
        busy_lounges = sum(1 for l in lounges if l['count'] >= self.busy_threshold)
        
        avg_users = total_users / total_lounges if total_lounges > 0 else 0
        utilization = (active_lounges / total_lounges * 100) if total_lounges > 0 else 0
        
        # Create detailed embed
        embed = discord.Embed(
            title="📊 SMART LOUNGE SYSTEM STATISTICS",
            description="EVERY FUCKIN STAT YOU COULD WANT",
            color=discord.Color.purple(),
            timestamp=datetime.now()
        )
        
        # Basic Info
        embed.add_field(
            name="🏢 SYSTEM INFO",
            value=(
                f"**Category:** {category.name}\n"
                f"**Base Lounge ID:** `{self.base_lounge_id}`\n"
                f"**Busy Threshold:** {self.busy_threshold} users\n"
                f"**Loop Interval:** 2 minutes\n"
                f"**Creation Cooldown:** {self.creation_cooldown}s\n"
            ),
            inline=False
        )
        
        # Current State
        current_state = "\n".join([
            f"**Lounge {l['num']}:** {l['count']} user{'s' if l['count'] != 1 else ''} "
            f"{'🚨 BUSY' if l['count'] >= self.busy_threshold else '✅ OK' if l['count'] > 0 else '💤 EMPTY'}"
            for l in lounges[:10]  # Show first 10 lounges
        ])
        if len(lounges) > 10:
            current_state += f"\n... and {len(lounges) - 10} more lounges"
            
        embed.add_field(
            name="📈 CURRENT STATE",
            value=current_state,
            inline=False
        )
        
        # Statistics
        embed.add_field(
            name="🔢 NUMERICAL STATS",
            value=(
                f"**Total Lounges:** {total_lounges}\n"
                f"**Active Lounges:** {active_lounges} ({utilization:.1f}%)\n"
                f"**Empty Lounges:** {empty_lounges}\n"
                f"**Busy Lounges:** {busy_lounges} ({self.stats['busy_percentage']:.1f}%)\n"
                f"**Total Users:** {total_users}\n"
                f"**Avg Users/Lounge:** {avg_users:.2f}\n"
            ),
            inline=True
        )
        
        embed.add_field(
            name="📊 SYSTEM METRICS",
            value=(
                f"**Loop Executions:** {self.stats['loop_executions']}\n"
                f"**Total Creations:** {self.stats['total_creations']}\n"
                f"**Total Deletions:** {self.stats['total_deletions']}\n"
                f"**Net Growth:** {self.stats['total_creations'] - self.stats['total_deletions']}\n"
                f"**Peak Users:** {self.stats['peak_users']}\n"
                f"**Avg Users/Lounge:** {self.stats['avg_users_per_lounge']:.2f}\n"
            ),
            inline=True
        )
        
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
            
        if issues:
            health_status = "⚠️ WARNING"
            health_color = discord.Color.orange()
            embed.add_field(
                name="🚨 SYSTEM ISSUES",
                value="\n".join([f"• {issue}" for issue in issues]),
                inline=False
            )
        
        embed.color = health_color
        embed.set_footer(text=f"Requested by {ctx.author} | System: {health_status}")
        
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
            traceback.print_exc()

async def setup(bot):
    await bot.add_cog(SmartLoungeCog(bot))
    print("✅ Smart Lounge Cog loaded with stats command.")