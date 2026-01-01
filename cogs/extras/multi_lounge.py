import discord
from discord.ext import commands, tasks
import asyncio
import json
import os
import re
from datetime import datetime
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

class LoungeCog(commands.Cog):
    """Auto-creating voice lounges with tiered capacity and specific naming"""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.base_lounge_id = 1394677032922648747  # Your preconfigured Lounge 3
        self.lounge_category = None
        self.lounges: Dict[int, dict] = {}  # lounge_id: LoungeData
        self.next_lounge_number = 4  # Start from lounge 4 (since lounge 3 is base)
        self.data_file = "lounges_data.json"
        
        # Debug & Stats
        self.creation_log = []
        self.stats = {
            'total_created': 0,
            'total_deleted': 0,
            'promotions': defaultdict(int),
            'last_creation': None,
            'creation_attempts': 0,
            'failed_creations': 0
        }
        
        # Load saved data
        self.load_data()
        
        # Configuration - SIMPLIFIED
        self.config = {
            'creation_threshold': 2,  # ✅ ALL lounges must have at least 2 users
            'auto_delete_minutes': 10,  # Delete empty lounges after X minutes
            'creation_cooldown_minutes': 1,  # Reduced cooldown for testing
            
            # Tiered base capacity system - SIMPLIFIED
            'tier_capacities': {
                1: {'base_max': 99, 'range': (3, 3), 'emoji': '🔊'},    # Lounge 3: normal 99
                2: {'base_max': 8, 'range': (4, 5), 'emoji': '🔊'},     # Lounges 4-5: max 8
                3: {'base_max': 6, 'range': (6, 7), 'emoji': '🎵'},     # Lounges 6-7: max 6
                4: {'base_max': 4, 'range': (8, 999), 'emoji': '💎'},   # Lounges 8+: max 4
            },
            
            # Simplified name templates
            'name_templates': {
                'normal': "┊ {emoji}﹕₊˚⌇lounge {number} ꜝꜝ‹3",
            }
        }
        
    def load_data(self):
        """Load lounge data from JSON file"""
        try:
            if os.path.exists(self.data_file):
                with open(self.data_file, 'r') as f:
                    data = json.load(f)
                    self.next_lounge_number = data.get('next_lounge_number', 4)
                    self.saved_promotions = data.get('lounge_promotions', {})
                    self.stats = data.get('stats', self.stats)
                    print(f"📂 Loaded saved data. Next lounge: {self.next_lounge_number}")
            else:
                print("📂 No saved data found, starting fresh")
                self.saved_promotions = {}
        except Exception as e:
            print(f"❌ Error loading data: {e}")
            self.next_lounge_number = 4
            self.saved_promotions = {}
            
    def save_data(self):
        """Save lounge data to JSON file"""
        try:
            data = {
                'next_lounge_number': self.next_lounge_number,
                'lounge_promotions': {},
                'stats': self.stats,
                'last_saved': datetime.now().isoformat()
            }
            
            for lounge_id, lounge_data in self.lounges.items():
                if 'number' in lounge_data:
                    data['lounge_promotions'][str(lounge_data['number'])] = {
                        'status': lounge_data.get('status', 'normal'),
                        'promotion_timer': lounge_data.get('promotion_timer', 0),
                    }
            
            with open(self.data_file, 'w') as f:
                json.dump(data, f, indent=2)
                
        except Exception as e:
            print(f"❌ Error saving data: {e}")
                    
    @commands.Cog.listener()
    async def on_ready(self):
        """Initialize the lounge system when bot is ready"""
        print(f"✅ Lounge Cog loaded as {self.bot.user}")
        
        self.cleanup_check.start()
        self.status_update.start()
        
        # Find the base lounge channel
        base_channel = self.bot.get_channel(self.base_lounge_id)
        if base_channel and hasattr(base_channel, 'category'):
            self.lounge_category = base_channel.category
            print(f"📁 Found lounge category: {self.lounge_category.name}")
            
            # Initialize all existing lounges
            await self.initialize_existing_lounges()
            
            # Start creation check after initialization
            self.creation_check.start()
        else:
            print("❌ Could not find base lounge or its category!")
            
    async def initialize_existing_lounges(self):
        """Scan and initialize all existing lounge voice channels"""
        if not self.lounge_category:
            return
            
        # Reset tracking
        self.lounges = {}
        found_numbers = []
        
        print(f"🔍 Scanning {len(self.lounge_category.voice_channels)} voice channels...")
        
        for channel in self.lounge_category.voice_channels:
            # Try to extract lounge number from name
            lounge_num = None
            
            # Look for "lounge X" pattern
            match = re.search(r'lounge\s+(\d+)', channel.name, re.IGNORECASE)
            if match:
                lounge_num = int(match.group(1))
                print(f"  Found lounge {lounge_num}: {channel.name}")
            else:
                # Try to find any number in the name
                num_match = re.search(r'(\d+)', channel.name)
                if num_match:
                    # Check if this looks like a lounge number (3 or higher)
                    possible_num = int(num_match.group(1))
                    if possible_num >= 3:
                        lounge_num = possible_num
                        print(f"  Found possible lounge {lounge_num}: {channel.name}")
            
            if lounge_num and lounge_num >= 3:
                found_numbers.append(lounge_num)
                
                saved_status_data = self.saved_promotions.get(str(lounge_num), {})
                
                self.lounges[channel.id] = {
                    'channel': channel,
                    'number': lounge_num,
                    'created_at': datetime.now().isoformat(),
                    'last_activity': datetime.now().isoformat() if channel.members else None,
                    'status': saved_status_data.get('status', 'normal'),
                    'promotion_timer': saved_status_data.get('promotion_timer', 0),
                    'member_count': len(channel.members),
                }
                
                # Update the name to match our format
                await self.update_lounge_name(channel, self.lounges[channel.id])
        
        # Set next lounge number
        if found_numbers:
            max_number = max(found_numbers)
            self.next_lounge_number = max_number + 1
            print(f"📊 Found lounges: {sorted(found_numbers)}")
        else:
            # If no lounges found, start from 4
            self.next_lounge_number = 4
            
        print(f"➡️ Next lounge number will be: {self.next_lounge_number}")
        self.save_data()
        
    def get_capacity_for_lounge(self, lounge_number: int) -> Tuple[int, str]:
        """Get max capacity and emoji for a lounge number"""
        base_capacity = 99
        emoji = '🔊'
        
        for tier, data in self.config['tier_capacities'].items():
            min_num, max_num = data['range']
            if min_num <= lounge_number <= max_num:
                base_capacity = data['base_max']
                emoji = data['emoji']
                break
                
        return base_capacity, emoji
    
    def get_name_template(self) -> str:
        """Get name template"""
        return self.config['name_templates']['normal']
    
    async def update_lounge_name(self, channel, lounge_data: dict):
        """Update lounge name with proper formatting"""
        lounge_number = lounge_data['number']
        max_capacity, emoji = self.get_capacity_for_lounge(lounge_number)
        current_users = len(channel.members)
        
        template = self.get_name_template()
        new_name = template.format(
            emoji=emoji,
            number=lounge_number
        )
        
        # Trim if too long
        if len(new_name) > 32:
            new_name = f"┊ {emoji} lounge {lounge_number}"
            if len(new_name) > 32:
                new_name = new_name[:32]
        
        # Only update if different
        if channel.name != new_name:
            try:
                await channel.edit(name=new_name, user_limit=max_capacity)
                lounge_data['last_updated'] = datetime.now().isoformat()
                print(f"📝 Updated lounge {lounge_number} name to: {new_name}")
            except discord.Forbidden:
                print(f"⚠️ No permission to edit {channel.name}")
            except Exception as e:
                print(f"❌ Error updating {channel.name}: {e}")
    
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        """Handle users joining/leaving voice channels"""
        # User joined a voice channel in our category
        if after.channel and after.channel.category == self.lounge_category:
            await self.handle_voice_join(member, after.channel)
            
        # User left a voice channel in our category
        if before.channel and before.channel.category == self.lounge_category:
            await self.handle_voice_leave(member, before.channel)
            
        # User moved between channels in our category
        if (before.channel and after.channel and 
            before.channel.category == self.lounge_category and 
            after.channel.category == self.lounge_category and
            before.channel != after.channel):
            await self.handle_voice_leave(member, before.channel)
            await self.handle_voice_join(member, after.channel)
    
    async def handle_voice_join(self, member, channel):
        """Handle user joining a lounge"""
        lounge_id = channel.id
        
        if lounge_id not in self.lounges:
            # Try to identify lounge number
            lounge_num = None
            match = re.search(r'lounge\s+(\d+)', channel.name, re.IGNORECASE)
            if match:
                lounge_num = int(match.group(1))
            else:
                num_match = re.search(r'(\d+)', channel.name)
                if num_match:
                    possible_num = int(num_match.group(1))
                    if possible_num >= 3:
                        lounge_num = possible_num
            
            if lounge_num:
                saved_status_data = self.saved_promotions.get(str(lounge_num), {})
                
                self.lounges[lounge_id] = {
                    'channel': channel,
                    'number': lounge_num,
                    'created_at': datetime.now().isoformat(),
                    'last_activity': datetime.now().isoformat(),
                    'status': saved_status_data.get('status', 'normal'),
                    'promotion_timer': saved_status_data.get('promotion_timer', 0),
                    'member_count': len(channel.members),
                }
                
                if lounge_num >= self.next_lounge_number:
                    self.next_lounge_number = lounge_num + 1
                    print(f"📈 Updated next lounge number to: {self.next_lounge_number}")
            else:
                return
        else:
            self.lounges[lounge_id]['last_activity'] = datetime.now().isoformat()
            self.lounges[lounge_id]['member_count'] = len(channel.members)
        
        await self.update_lounge_name(channel, self.lounges[lounge_id])
    
    async def handle_voice_leave(self, member, channel):
        """Handle user leaving a lounge"""
        lounge_id = channel.id
        
        if lounge_id in self.lounges:
            self.lounges[lounge_id]['member_count'] = len(channel.members)
            await self.update_lounge_name(channel, self.lounges[lounge_id])
            
            if len(channel.members) == 0:
                self.lounges[lounge_id]['last_activity'] = None
    
    @tasks.loop(seconds=30)
    async def creation_check(self):
        """Check if we should create a new lounge"""
        if not self.lounge_category:
            return
            
        self.stats['creation_attempts'] += 1
        now = datetime.now()
        
        # Check cooldown
        last_creation = self.stats.get('last_creation')
        if last_creation:
            if isinstance(last_creation, str):
                last_creation = datetime.fromisoformat(last_creation)
            minutes_since = (now - last_creation).total_seconds() / 60
            if minutes_since < self.config['creation_cooldown_minutes']:
                log_msg = f"⏳ Cooldown: {minutes_since:.1f}/{self.config['creation_cooldown_minutes']}m"
                self.creation_log.append(f"{now.strftime('%H:%M:%S')} - {log_msg}")
                if len(self.creation_log) > 50:
                    self.creation_log.pop(0)
                return
        
        # Get all existing lounges (numbers >= 3)
        existing_lounges = []
        for data in self.lounges.values():
            if data['channel'] and data['number'] >= 3:
                existing_lounges.append(data)
        
        # Need at least one lounge
        if not existing_lounges:
            log_msg = "❌ No existing lounges found"
            self.creation_log.append(f"{now.strftime('%H:%M:%S')} - {log_msg}")
            return
        
        # Sort by lounge number
        existing_lounges.sort(key=lambda x: x['number'])
        
        # Check if ALL lounges have threshold users
        all_meet_threshold = True
        status_details = []
        
        for lounge_data in existing_lounges:
            channel = lounge_data['channel']
            users = len(channel.members)
            threshold = self.config['creation_threshold']
            
            status = "✅" if users >= threshold else "❌"
            status_details.append(f"{status} Lounge {lounge_data['number']}: {users}/{threshold}")
            
            if users < threshold:
                all_meet_threshold = False
        
        if all_meet_threshold:
            # Find the highest lounge number
            highest_num = max(lounge['number'] for lounge in existing_lounges)
            next_lounge_num = highest_num + 1
            
            # Check if already exists
            exists = any(data['number'] == next_lounge_num for data in self.lounges.values())
            if not exists:
                self.stats['last_creation'] = now.isoformat()
                self.stats['total_created'] += 1
                
                log_msg = f"✅ Creating Lounge {next_lounge_num}! All lounges have {self.config['creation_threshold']}+ users"
                self.creation_log.append(f"{now.strftime('%H:%M:%S')} - {log_msg}")
                print(f"🎯 TRIGGERED: {log_msg}")
                
                await self.create_new_lounge(next_lounge_num)
            else:
                log_msg = f"⚠️ Lounge {next_lounge_num} already exists"
                self.creation_log.append(f"{now.strftime('%H:%M:%S')} - {log_msg}")
        else:
            log_msg = f"⏳ Waiting: {' | '.join(status_details)}"
            self.creation_log.append(f"{now.strftime('%H:%M:%S')} - {log_msg}")
            
        # Trim log
        if len(self.creation_log) > 50:
            self.creation_log = self.creation_log[-50:]
    
    async def create_new_lounge(self, lounge_number: int):
        """Create a new lounge channel with proper positioning"""
        if not self.lounge_category:
            return False
            
        print(f"🚀 Creating lounge {lounge_number}...")
        
        # Get capacity and name
        max_capacity, emoji = self.get_capacity_for_lounge(lounge_number)
        initial_name = f"┊ {emoji}﹕₊˚⌇lounge {lounge_number}"
        
        try:
            # Create at the end of the category
            position = len([c for c in self.lounge_category.channels if isinstance(c, discord.VoiceChannel)])
            
            new_channel = await self.lounge_category.create_voice_channel(
                name=initial_name,
                position=position,
                user_limit=max_capacity,
                reason=f"Auto-created lounge {lounge_number}"
            )
            
            self.lounges[new_channel.id] = {
                'channel': new_channel,
                'number': lounge_number,
                'created_at': datetime.now().isoformat(),
                'last_activity': None,
                'status': 'normal',
                'promotion_timer': 0,
                'member_count': 0,
            }
            
            if lounge_number >= self.next_lounge_number:
                self.next_lounge_number = lounge_number + 1
                
            self.save_data()
            
            print(f"✅ Successfully created lounge {lounge_number}!")
            return True
            
        except discord.Forbidden:
            print(f"❌ No permission to create lounge {lounge_number}")
            self.stats['failed_creations'] += 1
            return False
        except Exception as e:
            print(f"❌ Error creating lounge {lounge_number}: {e}")
            self.stats['failed_creations'] += 1
            return False
    
    @tasks.loop(minutes=5)
    async def cleanup_check(self):
        """Clean up empty lounges and save data periodically"""
        if not self.lounges:
            return
            
        now = datetime.now()
        lounges_to_delete = []
        
        for lounge_id, data in list(self.lounges.items()):
            channel = data['channel']
            if not channel:
                lounges_to_delete.append(lounge_id)
                continue
                
            # Don't delete lounges 3, 4, 5
            if data['number'] <= 5:
                continue
                
            if len(channel.members) == 0:
                last_activity = data.get('last_activity')
                if last_activity:
                    try:
                        if isinstance(last_activity, str):
                            last_active = datetime.fromisoformat(last_activity)
                        else:
                            last_active = last_activity
                            
                        inactive_time = (now - last_active).total_seconds() / 60
                        
                        if inactive_time >= self.config['auto_delete_minutes']:
                            lounges_to_delete.append(lounge_id)
                    except:
                        pass
        
        # Delete empty lounges
        deleted_count = 0
        for lounge_id in lounges_to_delete:
            data = self.lounges[lounge_id]
            channel = data['channel']
            
            if channel:
                try:
                    await channel.delete(reason="Auto-deleted empty lounge")
                    deleted_count += 1
                    print(f"🗑️ Deleted empty lounge {data['number']}")
                except:
                    pass
                    
            if lounge_id in self.lounges:
                del self.lounges[lounge_id]
                
        if deleted_count > 0:
            self.stats['total_deleted'] += deleted_count
            self.save_data()
    
    @tasks.loop(minutes=2)
    async def status_update(self):
        """Periodic status update and maintenance"""
        # Update all lounge names
        for lounge_id, data in list(self.lounges.items()):
            if data['channel']:
                await self.update_lounge_name(data['channel'], data)
        
        # Save data periodically
        self.save_data()
        
    @creation_check.before_loop
    async def before_creation_check(self):
        await self.bot.wait_until_ready()
        
    @cleanup_check.before_loop
    async def before_cleanup_check(self):
        await self.bot.wait_until_ready()
        
    @status_update.before_loop
    async def before_status_update(self):
        await self.bot.wait_until_ready()
    
    @commands.command(name="loungeinfo")
    async def lounge_info(self, ctx, lounge_number: int = None):
        """Show detailed information about a specific lounge"""
        if lounge_number:
            lounge_data = next((data for data in self.lounges.values() 
                               if data['number'] == lounge_number), None)
            
            if not lounge_data or not lounge_data['channel']:
                await ctx.send(f"❌ Lounge {lounge_number} not found!")
                return
                
            channel = lounge_data['channel']
            users = len(channel.members)
            max_capacity, emoji = self.get_capacity_for_lounge(lounge_number)
            
            embed = discord.Embed(
                title=f"🏢 Lounge {lounge_number} Details",
                color=discord.Color.green() if users > 0 else discord.Color.red()
            )
            
            embed.add_field(name="Status", value=lounge_data['status'].upper(), inline=True)
            embed.add_field(name="Users", value=f"{users}/{max_capacity}", inline=True)
            embed.add_field(name="Capacity Tier", value=f"Tier {self.get_tier_for_lounge(lounge_number)}", inline=True)
            
            if lounge_data.get('last_activity'):
                last_active = datetime.fromisoformat(lounge_data['last_activity'])
                embed.add_field(name="Last Activity", value=last_active.strftime("%H:%M:%S"), inline=True)
            
            await ctx.send(embed=embed)
        else:
            await self.lounge_stats(ctx)
    
    def get_tier_for_lounge(self, lounge_number: int) -> int:
        """Get tier for a lounge number"""
        for tier, data in self.config['tier_capacities'].items():
            min_num, max_num = data['range']
            if min_num <= lounge_number <= max_num:
                return tier
        return 1
        
    @commands.command(name="loungestats")
    async def lounge_stats(self, ctx):
        """Show current lounge statistics"""
        embed = discord.Embed(
            title="🏢 Lounge System - Live Stats",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        
        # System Status
        active_lounges = [l for l in self.lounges.values() if l['channel'] and len(l['channel'].members) > 0]
        total_users = sum(len(l['channel'].members) for l in self.lounges.values() if l['channel'])
        
        embed.add_field(name="📊 System Stats", value="", inline=False)
        embed.add_field(name="Total Lounges", value=str(len(self.lounges)), inline=True)
        embed.add_field(name="Active Lounges", value=str(len(active_lounges)), inline=True)
        embed.add_field(name="Total Users", value=str(total_users), inline=True)
        embed.add_field(name="Next Lounge #", value=str(self.next_lounge_number), inline=True)
        embed.add_field(name="Creation Threshold", value=str(self.config['creation_threshold']), inline=True)
        
        # Lounge Status
        lounge_info = []
        for lounge_num in sorted([data['number'] for data in self.lounges.values()]):
            lounge_data = next((l for l in self.lounges.values() if l['number'] == lounge_num), None)
            if lounge_data and lounge_data['channel']:
                users = len(lounge_data['channel'].members)
                max_cap, _ = self.get_capacity_for_lounge(lounge_num)
                threshold_met = "✅" if users >= self.config['creation_threshold'] else "❌"
                lounge_info.append(f"{threshold_met} **Lounge {lounge_num}:** {users}/{max_cap}")
        
        if lounge_info:
            embed.add_field(
                name="🎯 Lounge Status (Threshold Check)",
                value="\n".join(lounge_info),
                inline=False
            )
        
        # Recent Logs
        if self.creation_log:
            recent_logs = "\n".join(self.creation_log[-5:])
            embed.add_field(
                name="📝 Recent Activity",
                value=recent_logs,
                inline=False
            )
        
        embed.set_footer(text=f"Total Created: {self.stats['total_created']} | Deleted: {self.stats['total_deleted']}")
        
        await ctx.send(embed=embed)
    
    @commands.command(name="loungewaiting")
    async def lounge_waiting(self, ctx):
        """Show which lounges are blocking new creation"""
        if not self.lounges:
            await ctx.send("No lounges found!")
            return
            
        blocking = []
        meeting = []
        
        for data in self.lounges.values():
            if data['channel'] and data['number'] >= 3:
                users = len(data['channel'].members)
                threshold = self.config['creation_threshold']
                if users < threshold:
                    blocking.append(f"**Lounge {data['number']}:** {users}/{threshold} users")
                else:
                    meeting.append(f"**Lounge {data['number']}:** {users}/{threshold} users ✅")
        
        if blocking:
            embed = discord.Embed(
                title="⏳ Lounges Blocking Creation",
                description="These lounges need more users to trigger new lounge creation:\n\n" + "\n".join(blocking),
                color=discord.Color.orange()
            )
            if meeting:
                embed.add_field(
                    name="✅ Meeting Threshold",
                    value="\n".join(meeting),
                    inline=False
                )
        else:
            embed = discord.Embed(
                title="✅ All Conditions Met!",
                description=f"All lounges have {self.config['creation_threshold']}+ users!\nNext lounge (#{self.next_lounge_number}) will be created soon.",
                color=discord.Color.green()
            )
            if meeting:
                embed.add_field(
                    name="Current Status",
                    value="\n".join(meeting),
                    inline=False
                )
        
        embed.add_field(
            name="Requirements",
            value=f"• All lounges need {self.config['creation_threshold']}+ users\n• No cooldown active\n• Next lounge will be #{self.next_lounge_number}",
            inline=False
        )
        
        await ctx.send(embed=embed)
    
    @commands.command(name="forcecreate")
    @commands.has_permissions(administrator=True)
    async def force_create_lounge(self, ctx, number: int = None):
        """Force create a lounge (Admin only)"""
        if not number:
            number = self.next_lounge_number
            
        if number < 3:
            await ctx.send("❌ Can only create lounges 3 and above.")
            return
            
        # Check if exists
        if any(data['number'] == number for data in self.lounges.values()):
            await ctx.send(f"❌ Lounge {number} already exists!")
            return
            
        success = await self.create_new_lounge(number)
        if success:
            await ctx.send(f"✅ Force-created lounge {number}")
        else:
            await ctx.send(f"❌ Failed to create lounge {number}")
    
    @commands.command(name="checkcreate")
    async def check_creation(self, ctx):
        """Manually trigger creation check"""
        await ctx.send("🔍 Running creation check...")
        await self.creation_check()
        await ctx.send("✅ Creation check completed!")
        
    @commands.command(name="debuglogs")
    @commands.has_permissions(administrator=True)
    async def debug_logs(self, ctx, count: int = 10):
        """Show debug logs (Admin only)"""
        if not self.creation_log:
            await ctx.send("No logs available.")
            return
            
        logs_to_show = self.creation_log[-count:] if count <= 50 else self.creation_log[-50:]
        
        embed = discord.Embed(
            title="🔧 Debug Logs",
            description="\n".join(logs_to_show),
            color=discord.Color.dark_gray()
        )
        embed.set_footer(text=f"Showing last {len(logs_to_show)} of {len(self.creation_log)} logs")
        
        await ctx.send(embed=embed)
        
    @commands.command(name="resetnext")
    @commands.has_permissions(administrator=True)
    async def reset_next_number(self, ctx, number: int):
        """Reset the next lounge number (Admin only)"""
        if number < 3:
            await ctx.send("❌ Next lounge number must be 3 or higher.")
            return
            
        self.next_lounge_number = number
        self.save_data()
        await ctx.send(f"✅ Next lounge number set to {number}")

async def setup(bot):
    await bot.add_cog(LoungeCog(bot))