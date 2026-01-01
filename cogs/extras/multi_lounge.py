import discord
from discord.ext import commands, tasks
import asyncio
import json
import os
import tempfile
import shutil
from datetime import datetime
import re

class LoungeCog(commands.Cog):
    """Auto-creating voice lounges with tiered capacity, activity-based promotion, and specific naming"""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.base_lounge_id = 1394677032922648747  # Your preconfigured Lounge 4
        self.lounge_category = None
        self.lounges = {}  # lounge_id: LoungeData
        self.next_lounge_number = 3  # Start from lounge 4
        self.data_file = "lounges_data.json"
        self.creation_cooldowns = {}  # Prevent spam creation
        
        # Load saved data
        self.load_data()
        
        # Configuration
        self.config = {
            'creation_threshold': 2,  # ✅ ALL lounges must have at least 2 users
            'auto_delete_minutes': 10,  # Delete empty lounges after X minutes
            'promotion_check_minutes': 10,
            'creation_cooldown_minutes': 5,  # Prevent rapid lounge creation
            
            # Tiered base capacity system (ꜝꜝ‹X shows current/max)
            'tier_capacities': {
                1: {'base_max': 99, 'range': (1, 3), 'emoji': '🔊'},    # Lounges 1-3: normal
                2: {'base_max': 8, 'range': (4, 5), 'emoji': '🔊'},     # Lounges 4-5: max 8
                3: {'base_max': 6, 'range': (6, 7), 'emoji': '🎵'},     # Lounges 6-7: max 6
                4: {'base_max': 4, 'range': (8, 999), 'emoji': '💎'},   # Lounges 8+: max 4
            },
            
            # Promotion requirements (minutes of activity)
            'promotion_thresholds': {
                'active': 30,      # 30 minutes of continuous activity
                'elite': 60,      # 2 hours for elite status
                'premium': 120,    # 5 hours for premium
            },
            
            # Promotion bonuses (capacity increase)
            'promotion_bonuses': {
                'normal': {'capacity_multiplier': 1.0, 'capacity_flat': 0},
                'active': {'capacity_multiplier': 1.25, 'capacity_flat': 2},
                'elite': {'capacity_multiplier': 1.5, 'capacity_flat': 4},
                'premium': {'capacity_multiplier': 2.0, 'capacity_flat': 6},
            },
            
            # Name templates
            'name_templates': {
                'normal': "┊ {emoji}﹕₊˚⌇lounge {number}  ꜝꜝ‹3",
                'active': "┊ 🌟﹕₊˚⌇active lounge {number}  ꜝꜝ‹3",
                'elite': "┊ 💎﹕₊˚⌇elite lounge {number}  ꜝꜝ‹3",
                'premium': "┊ ✨﹕₊˚⌇premium lounge {number}  ꜝꜝ‹3",
            }
        }
        
    def load_data(self):
        """Load lounge data from JSON file using safe temp file method"""
        try:
            if os.path.exists(self.data_file):
                with tempfile.NamedTemporaryFile(mode='w', delete=False) as temp_file:
                    temp_path = temp_file.name
                    
                try:
                    shutil.copy2(self.data_file, temp_path)
                    
                    with open(temp_path, 'r') as f:
                        data = json.load(f)
                        self.next_lounge_number = data.get('next_lounge_number', 4)
                        self.saved_promotions = data.get('lounge_promotions', {})
                        
                    print(f"📂 Loaded saved data. Next lounge: {self.next_lounge_number}")
                    
                finally:
                    if os.path.exists(temp_path):
                        os.unlink(temp_path)
                        
            else:
                print("📂 No saved data found, starting fresh")
                self.saved_promotions = {}
        except json.JSONDecodeError:
            print("❌ Corrupted JSON data, starting fresh")
            self.next_lounge_number = 4
            self.saved_promotions = {}
        except Exception as e:
            print(f"❌ Error loading data: {e}")
            self.next_lounge_number = 4
            self.saved_promotions = {}
            
    def save_data(self):
        """Save lounge data to JSON file using atomic write"""
        try:
            data = {
                'next_lounge_number': self.next_lounge_number,
                'lounge_promotions': {},
                'last_saved': datetime.now().isoformat(),
                'total_lounges_tracked': len(self.lounges)
            }
            
            for lounge_id, lounge_data in self.lounges.items():
                if 'number' in lounge_data and 'status' in lounge_data:
                    data['lounge_promotions'][str(lounge_data['number'])] = {
                        'status': lounge_data['status'],
                        'promotion_timer': lounge_data.get('promotion_timer', 0),
                        'last_updated': lounge_data.get('last_updated', datetime.now().isoformat())
                    }
            
            with tempfile.NamedTemporaryFile(mode='w', suffix='.tmp', delete=False) as temp_file:
                temp_path = temp_file.name
                json.dump(data, temp_file, indent=2)
                
            shutil.move(temp_path, self.data_file)
            
        except Exception as e:
            print(f"❌ Error saving data: {e}")
            if 'temp_path' in locals() and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except:
                    pass
                    
    @commands.Cog.listener()
    async def on_ready(self):
        """Initialize the lounge system when bot is ready"""
        print(f"✅ Lounge Cog loaded as {self.bot.user}")
        
        self.active_check.start()
        self.cleanup_check.start()
        
        base_channel = self.bot.get_channel(self.base_lounge_id)
        if base_channel and hasattr(base_channel, 'category'):
            self.lounge_category = base_channel.category
            print(f"📁 Found lounge category: {self.lounge_category.name}")
            
            await self.initialize_existing_lounges()
        else:
            print("❌ Could not find base lounge or its category!")
            
    async def initialize_existing_lounges(self):
        """Scan and initialize all existing lounge voice channels"""
        if not self.lounge_category:
            return
            
        for channel in self.lounge_category.voice_channels:
            match = re.search(r'lounge\s+(\d+)', channel.name, re.IGNORECASE)
            if match:
                lounge_num = int(match.group(1))
                
                if lounge_num >= 3:
                    saved_status_data = self.saved_promotions.get(str(lounge_num), {})
                    
                    self.lounges[channel.id] = {
                        'channel': channel,
                        'number': lounge_num,
                        'created_at': datetime.now(),
                        'last_activity': datetime.now() if channel.members else None,
                        'status': saved_status_data.get('status', 'normal'),
                        'promotion_timer': saved_status_data.get('promotion_timer', 0),
                        'member_count': len(channel.members),
                        'last_updated': datetime.now().isoformat()
                    }
                    
                    await self.update_lounge_name(channel, self.lounges[channel.id])
                    
                    if lounge_num >= self.next_lounge_number:
                        self.next_lounge_number = lounge_num + 1
                        
        print(f"📊 Initialized {len(self.lounges)} existing lounges")
        print(f"➡️ Next lounge number will be: {self.next_lounge_number}")
        
    def get_capacity_for_lounge(self, lounge_number: int, status: str = 'normal'):
        """Get max capacity and emoji for a lounge number with promotion bonuses"""
        base_capacity = 99
        emoji = '🔊'
        
        for tier, data in self.config['tier_capacities'].items():
            min_num, max_num = data['range']
            if min_num <= lounge_number <= max_num:
                base_capacity = data['base_max']
                emoji = data['emoji']
                break
        
        bonus = self.config['promotion_bonuses'].get(status, self.config['promotion_bonuses']['normal'])
        multiplier = bonus['capacity_multiplier']
        flat_bonus = bonus['capacity_flat']
        
        promoted_capacity = int(base_capacity * multiplier)
        promoted_capacity = max(promoted_capacity, base_capacity + flat_bonus)
        promoted_capacity = min(promoted_capacity, 99)
        
        if status == 'active':
            emoji = '🌟'
        elif status == 'elite':
            emoji = '💎'
        elif status == 'premium':
            emoji = '✨'
            
        return promoted_capacity, emoji
    
    def get_name_template(self, status: str):
        """Get name template based on lounge status"""
        return self.config['name_templates'].get(status, self.config['name_templates']['normal'])
    
    async def update_lounge_name(self, channel, lounge_data):
        """Update lounge name with proper formatting"""
        lounge_number = lounge_data['number']
        status = lounge_data.get('status', 'normal')
        max_capacity, emoji = self.get_capacity_for_lounge(lounge_number, status)
        current_users = len(channel.members)
        
        template = self.get_name_template(status)
        new_name = template.format(
            emoji=emoji,
            number=lounge_number,
            current=current_users,
            max=max_capacity
        )
        
        if len(new_name) > 32:
            new_name = new_name[:29] + "..."
            
        if channel.name != new_name:
            try:
                await channel.edit(name=new_name, user_limit=max_capacity)
                lounge_data['last_updated'] = datetime.now().isoformat()
                self.save_data()
            except discord.Forbidden:
                print(f"⚠️ No permission to edit {channel.name}")
            except Exception as e:
                print(f"❌ Error updating {channel.name}: {e}")
    
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        """Handle users joining/leaving voice channels"""
        # User joined a voice channel
        if after.channel and after.channel.category == self.lounge_category:
            await self.handle_voice_join(member, after.channel)
            
        # User left a voice channel
        if before.channel and before.channel.category == self.lounge_category:
            await self.handle_voice_leave(member, before.channel)
            
        # User moved between channels
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
            match = re.search(r'lounge\s+(\d+)', channel.name, re.IGNORECASE)
            if match:
                lounge_num = int(match.group(1))
                saved_status_data = self.saved_promotions.get(str(lounge_num), {})
                
                self.lounges[lounge_id] = {
                    'channel': channel,
                    'number': lounge_num,
                    'created_at': datetime.now(),
                    'last_activity': datetime.now().isoformat(),
                    'status': saved_status_data.get('status', 'normal'),
                    'promotion_timer': saved_status_data.get('promotion_timer', 0),
                    'member_count': len(channel.members),
                    'last_updated': datetime.now().isoformat()
                }
                
                if lounge_num >= self.next_lounge_number:
                    self.next_lounge_number = lounge_num + 1
                    self.save_data()
            else:
                return
                
        self.lounges[lounge_id]['last_activity'] = datetime.now().isoformat()
        self.lounges[lounge_id]['member_count'] = len(channel.members)
        
        await self.update_lounge_name(channel, self.lounges[lounge_id])
        
        await self.check_lounge_creation()
        
    async def handle_voice_leave(self, member, channel):
        """Handle user leaving a lounge"""
        lounge_id = channel.id
        
        if lounge_id in self.lounges:
            self.lounges[lounge_id]['member_count'] = len(channel.members)
            await self.update_lounge_name(channel, self.lounges[lounge_id])
            
            if len(channel.members) == 0:
                self.lounges[lounge_id]['last_activity'] = None
                
    async def check_lounge_creation(self):
        """✅ CORRECTED: Create next lounge when ALL existing lounges have 2+ users"""
        if not self.lounge_category:
            return
            
        # Check cooldown
        now = datetime.now()
        if 'last_creation' in self.creation_cooldowns:
            last_creation = self.creation_cooldowns['last_creation']
            if isinstance(last_creation, str):
                last_creation = datetime.fromisoformat(last_creation)
            cooldown_minutes = (now - last_creation).total_seconds() / 60
            if cooldown_minutes < self.config['creation_cooldown_minutes']:
                return
        
        # Get ALL existing lounges (numbers >= 3)
        all_lounges = []
        for lounge_id, data in self.lounges.items():
            if data['channel'] and data['number'] >= 3:
                all_lounges.append((data['number'], data['channel']))
        
        # Need at least lounge 3
        if not all_lounges:
            return
            
        # Sort by lounge number
        all_lounges.sort(key=lambda x: x[0])
        
        # Check if ALL lounges have 2+ users
        all_have_two_plus = True
        for lounge_num, channel in all_lounges:
            if len(channel.members) < self.config['creation_threshold']:
                all_have_two_plus = False
                break
        
        # If ALL lounges have 2+ users, create next lounge
        if all_have_two_plus:
            highest_num = max([num for num, _ in all_lounges])
            next_lounge_num = highest_num + 1
            
            # Check if already exists
            exists = any(data['number'] == next_lounge_num for data in self.lounges.values())
            if not exists:
                self.creation_cooldowns['last_creation'] = datetime.now().isoformat()
                await self.create_new_lounge(next_lounge_num)
    
    async def create_new_lounge(self, lounge_number: int):
        """Create a new lounge channel below the previous one"""
        if not self.lounge_category:
            return
            
        # Check if lounge already exists
        existing = next((l_id for l_id, data in self.lounges.items() 
                        if data['number'] == lounge_number), None)
        if existing:
            return
            
        print(f"🚀 Creating lounge {lounge_number}...")
        
        # Find position (below previous lounge)
        position = None
        
        # Find previous lounge number
        prev_lounge_num = lounge_number - 1
        prev_channel = None
        
        # Look for the actual previous lounge
        for data in self.lounges.values():
            if data['number'] == prev_lounge_num:
                prev_channel = data['channel']
                break
                
        if prev_channel:
            position = prev_channel.position + 1
        else:
            # If previous doesn't exist, find highest existing lounge
            existing_numbers = [data['number'] for data in self.lounges.values()]
            if existing_numbers:
                max_existing = max(existing_numbers)
                for data in self.lounges.values():
                    if data['number'] == max_existing:
                        position = data['channel'].position + 1
                        break
        
        # Get capacity
        max_capacity, emoji = self.get_capacity_for_lounge(lounge_number, 'normal')
        
        # Create initial name
        initial_name = f"┊ {emoji}﹕₊˚⌇lounge {lounge_number}  ꜝꜝ‹3"
        
        try:
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
                'last_updated': datetime.now().isoformat()
            }
            
            if lounge_number >= self.next_lounge_number:
                self.next_lounge_number = lounge_number + 1
                self.save_data()
                
            print(f"✅ Created lounge {lounge_number} at position {position}")
            
        except discord.Forbidden:
            print(f"❌ No permission to create lounge {lounge_number}")
        except Exception as e:
            print(f"❌ Error creating lounge {lounge_number}: {e}")
            
    @tasks.loop(minutes=1)
    async def active_check(self):
        """Check for active lounges and update promotions"""
        if not self.lounges:
            return
            
        for lounge_id, data in list(self.lounges.items()):
            channel = data['channel']
            if not channel:
                continue
                
            if len(channel.members) > 0:
                data['promotion_timer'] = data.get('promotion_timer', 0) + 1
                old_status = data['status']
                
                if data['promotion_timer'] >= self.config['promotion_thresholds']['premium']:
                    data['status'] = 'premium'
                elif data['promotion_timer'] >= self.config['promotion_thresholds']['elite']:
                    data['status'] = 'elite'
                elif data['promotion_timer'] >= self.config['promotion_thresholds']['active']:
                    data['status'] = 'active'
                    
                if old_status != data['status']:
                    await self.update_lounge_name(channel, data)
                    print(f"🏆 Lounge {data['number']} promoted to {data['status']}!")
            else:
                if data.get('promotion_timer', 0) > 0:
                    data['promotion_timer'] = 0
                    
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
                
            if len(channel.members) == 0:
                last_activity = data.get('last_activity')
                if last_activity and data['number'] > 3:
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
        for lounge_id in lounges_to_delete:
            data = self.lounges[lounge_id]
            channel = data['channel']
            
            if channel and data['number'] > 3:
                try:
                    await channel.delete(reason="Auto-deleted empty lounge")
                    print(f"🗑️ Deleted empty lounge {data['number']}")
                except:
                    pass
                    
            if lounge_id in self.lounges:
                del self.lounges[lounge_id]
                
        self.save_data()
        
    @active_check.before_loop
    async def before_active_check(self):
        await self.bot.wait_until_ready()
        
    @cleanup_check.before_loop
    async def before_cleanup_check(self):
        await self.bot.wait_until_ready()
        
    @commands.command(name="loungestats")
    async def lounge_stats(self, ctx):
        """Show current lounge statistics"""
        if not self.lounges:
            await ctx.send("No lounges are currently active.")
            return
            
        embed = discord.Embed(
            title="🏢 Lounge System Stats",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        
        active_lounges = [l for l in self.lounges.values() if l['channel'] and len(l['channel'].members) > 0]
        total_users = sum(len(l['channel'].members) for l in active_lounges if l['channel'])
        
        embed.add_field(name="Total Lounges", value=str(len(self.lounges)), inline=True)
        embed.add_field(name="Active Lounges", value=str(len(active_lounges)), inline=True)
        embed.add_field(name="Total Users", value=str(total_users), inline=True)
        embed.add_field(name="Next Lounge #", value=str(self.next_lounge_number), inline=True)
        
        # Show all lounges status
        lounge_info = []
        for lounge_num in sorted([data['number'] for data in self.lounges.values()]):
            lounge_data = next((l for l in self.lounges.values() if l['number'] == lounge_num), None)
            if lounge_data and lounge_data['channel']:
                users = len(lounge_data['channel'].members)
                status = lounge_data['status']
                max_cap, _ = self.get_capacity_for_lounge(lounge_num, status)
                lounge_info.append(f"**Lounge {lounge_num}:** {users}/{max_cap} users ({status})")
        
        if lounge_info:
            embed.add_field(
                name="All Lounges",
                value="\n".join(lounge_info),
                inline=False
            )
        
        await ctx.send(embed=embed)
        
    @commands.command(name="forcelounge")
    @commands.has_permissions(administrator=True)
    async def force_create_lounge(self, ctx, number: int = None):
        """Force create a lounge (Admin only)"""
        if not number:
            number = self.next_lounge_number
            
        if number < 4:
            await ctx.send("❌ Can only create lounges 4 and above.")
            return
            
        await self.create_new_lounge(number)
        await ctx.send(f"✅ Force-created lounge {number}")
        
    @commands.command(name="resetlounges")
    @commands.has_permissions(administrator=True)
    async def reset_lounges(self, ctx):
        """Reset all lounges except base lounge 3 (Admin only)"""
        confirm = await ctx.send("⚠️ This will delete ALL lounges except Lounge 3. Type `CONFIRM` to proceed.")
        
        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel and m.content == "CONFIRM"
            
        try:
            await self.bot.wait_for('message', timeout=30.0, check=check)
        except asyncio.TimeoutError:
            await ctx.send("❌ Reset cancelled.")
            return
            
        deleted_count = 0
        for lounge_id, data in list(self.lounges.items()):
            if data['number'] > 3 and data['channel']:
                try:
                    await data['channel'].delete()
                    deleted_count += 1
                except:
                    pass
                del self.lounges[lounge_id]
                
        self.next_lounge_number = 4
        self.save_data()
        
        await ctx.send(f"✅ Reset complete. Deleted {deleted_count} lounges. Next lounge will be #4.")

async def setup(bot):
    await bot.add_cog(LoungeCog(bot))