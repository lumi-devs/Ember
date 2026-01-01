import discord
from discord.ext import commands, tasks
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List
import time

class LoungeCog(commands.Cog):
    """On-demand lounge system with one spare lounge always available"""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.lounge_2_id = 1394677032922648747  # Your Lounge 2
        self.lounge_2 = None
        self.lounge_category = None
        
        # Track lounges WE create
        self.our_lounges: Dict[int, dict] = {}  # lounge_number -> {channel: VoiceChannel, empty_since: datetime}
        self.next_lounge_number = 3  # Start creating from Lounge 3
        
        # Track creation/deletion
        self.empty_lounges: List[int] = []  # List of empty lounge numbers
        self.last_activity: Dict[int, float] = {}  # lounge_number -> last activity timestamp
        
        # Settings
        self.inactivity_timeout = 300  # 5 minutes in seconds
        self.check_interval = 30  # Check every 30 seconds
        
        print("🚀 Lounge Cog initialized - On-Demand System")
    
    @commands.Cog.listener()
    async def on_ready(self):
        """Initialize when bot is ready"""
        print(f"✅ Lounge Cog loaded as {self.bot.user}")
        
        # Get Lounge 2 from ID
        self.lounge_2 = self.bot.get_channel(self.lounge_2_id)
        
        if not self.lounge_2:
            print("❌ Lounge 2 not found!")
            return
            
        print(f"📊 Found Lounge 2: {self.lounge_2.name}")
        
        if hasattr(self.lounge_2, 'category'):
            self.lounge_category = self.lounge_2.category
            print(f"📁 Found category: {self.lounge_category.name}")
        else:
            print("❌ Lounge 2 has no category!")
            return
        
        # Start the maintenance loop
        self.maintenance_loop.start()
    
    @tasks.loop(seconds=30)
    async def maintenance_loop(self):
        """Main maintenance loop - checks every 30 seconds"""
        if not self.lounge_2 or not self.lounge_category:
            return
        
        print(f"\n🔄 Maintenance check...")
        
        # Update activity tracking for Lounge 2
        lounge_2_users = len(self.lounge_2.members)
        if lounge_2_users > 0:
            self.last_activity[2] = time.time()
        
        # Update activity for our lounges and track empty ones
        current_empty_lounges = []
        
        for lounge_num in list(self.our_lounges.keys()):
            if lounge_num in self.our_lounges and self.our_lounges[lounge_num]['channel']:
                channel = self.our_lounges[lounge_num]['channel']
                users = len(channel.members)
                
                if users > 0:
                    # Lounge has users - update activity
                    self.last_activity[lounge_num] = time.time()
                    self.our_lounges[lounge_num]['empty_since'] = None
                    print(f"   Lounge {lounge_num}: {users} users (active)")
                else:
                    # Lounge is empty
                    if self.our_lounges[lounge_num]['empty_since'] is None:
                        # Just became empty
                        self.our_lounges[lounge_num]['empty_since'] = datetime.now()
                        print(f"   Lounge {lounge_num}: Empty (just now)")
                    
                    current_empty_lounges.append(lounge_num)
                    print(f"   Lounge {lounge_num}: Empty (for {(datetime.now() - self.our_lounges[lounge_num]['empty_since']).total_seconds():.0f}s)")
        
        self.empty_lounges = current_empty_lounges
        
        # RULE 1: If Lounge 2 has 2+ users, ensure we have at least Lounge 3
        lounge_2_users = len(self.lounge_2.members)
        if lounge_2_users >= 2:
            if 3 not in self.our_lounges:
                print(f"🎯 Creating Lounge 3 (Lounge 2 has {lounge_2_users}/2+ users)")
                await self.create_new_lounge(3)
            else:
                # Ensure Lounge 3 is active
                lounge_3 = self.our_lounges[3]['channel']
                if len(lounge_3.members) >= 2:
                    # RULE 2: If Lounge 3 has 2+ users, ensure we have at least Lounge 4
                    if 4 not in self.our_lounges:
                        print(f"🎯 Creating Lounge 4 (Both lounges have 2+ users)")
                        await self.create_new_lounge(4)
        
        # Count non-empty lounges (excluding Lounge 2)
        non_empty_lounges = []
        for lounge_num in self.our_lounges:
            if self.our_lounges[lounge_num]['channel'] and len(self.our_lounges[lounge_num]['channel'].members) > 0:
                non_empty_lounges.append(lounge_num)
        
        print(f"📊 Stats: {len(non_empty_lounges)} active lounges, {len(self.empty_lounges)} empty lounges")
        
        # Manage empty lounges - we want exactly ONE spare (empty) lounge
        if len(self.empty_lounges) > 1:
            # More than one empty lounge - delete the oldest ones
            print(f"🗑️  Too many empty lounges ({len(self.empty_lounges)}), cleaning up...")
            await self.cleanup_extra_empty_lounges()
        elif len(self.empty_lounges) == 0 and len(self.our_lounges) > 0:
            # No empty lounges - create a spare if needed
            print("🆕 No spare lounge, creating one...")
            await self.create_spare_lounge()
        
        # Check for inactive lounges to delete (except we always keep at least one spare)
        await self.check_inactive_lounges()
    
    async def cleanup_extra_empty_lounges(self):
        """Delete extra empty lounges, keeping only one spare"""
        if len(self.empty_lounges) <= 1:
            return
        
        # Sort by lounge number (keep the highest number as spare)
        self.empty_lounges.sort()
        
        # Keep the highest numbered lounge as spare, delete the rest
        lounges_to_delete = self.empty_lounges[:-1]  # All except the last (highest)
        
        for lounge_num in lounges_to_delete:
            if lounge_num in self.our_lounges and lounge_num >= 3:  # Don't delete below lounge 3
                await self.delete_lounge(lounge_num, "Extra empty lounge")
                print(f"   Deleted Lounge {lounge_num} (extra empty)")
    
    async def create_spare_lounge(self):
        """Create a spare lounge if we have none"""
        # Find the next available lounge number
        if not self.our_lounges:
            next_num = 3
        else:
            next_num = max(self.our_lounges.keys()) + 1
        
        print(f"   Creating spare lounge {next_num}")
        await self.create_new_lounge(next_num)
    
    async def check_inactive_lounges(self):
        """Delete lounges that have been inactive for too long"""
        current_time = datetime.now()
        
        for lounge_num in list(self.our_lounges.keys()):
            if lounge_num in self.our_lounges and self.our_lounges[lounge_num]['empty_since']:
                empty_since = self.our_lounges[lounge_num]['empty_since']
                empty_duration = (current_time - empty_since).total_seconds()
                
                # Check if this lounge has been empty for too long
                # But don't delete if it's our only spare lounge
                if (empty_duration >= self.inactivity_timeout and 
                    lounge_num in self.empty_lounges and 
                    len(self.empty_lounges) > 1):  # Only delete if we have more than one empty
                    
                    print(f"🗑️  Deleting Lounge {lounge_num} (inactive for {empty_duration:.0f}s)")
                    await self.delete_lounge(lounge_num, "Inactive for too long")
    
    async def create_new_lounge(self, lounge_number: int):
        """Create a new lounge channel"""
        try:
            # Create the name with proper format
            channel_name = f"┊ 🔊﹕₊˚⌇lounge {lounge_number} ꜝꜝ‹3"
            
            # Create at the end of voice channels
            voice_channels = [c for c in self.lounge_category.channels 
                            if isinstance(c, discord.VoiceChannel)]
            position = len(voice_channels)
            
            # Create the channel
            new_channel = await self.lounge_category.create_voice_channel(
                name=channel_name,
                position=position,
                user_limit=0,  # No limit
                reason=f"Auto-created lounge {lounge_number}"
            )
            
            # Initialize tracking
            self.our_lounges[lounge_number] = {
                'channel': new_channel,
                'empty_since': datetime.now(),  # Starts empty
                'created_at': datetime.now()
            }
            self.last_activity[lounge_number] = time.time()
            
            # Update empty lounges list
            if lounge_number not in self.empty_lounges:
                self.empty_lounges.append(lounge_number)
            
            print(f"✅ Created Lounge {lounge_number}")
            
            # Update next lounge number if needed
            if lounge_number >= self.next_lounge_number:
                self.next_lounge_number = lounge_number + 1
            
            return True
            
        except Exception as e:
            print(f"❌ Failed to create Lounge {lounge_number}: {e}")
            return False
    
    async def delete_lounge(self, lounge_number: int, reason: str = "No longer needed"):
        """Delete a lounge channel"""
        try:
            if lounge_number in self.our_lounges and self.our_lounges[lounge_number]['channel']:
                channel = self.our_lounges[lounge_number]['channel']
                await channel.delete(reason=reason)
                
                # Clean up tracking
                if lounge_number in self.our_lounges:
                    del self.our_lounges[lounge_number]
                if lounge_number in self.last_activity:
                    del self.last_activity[lounge_number]
                if lounge_number in self.empty_lounges:
                    self.empty_lounges.remove(lounge_number)
                
                return True
        except Exception as e:
            print(f"❌ Failed to delete Lounge {lounge_number}: {e}")
        
        return False
    
    @maintenance_loop.before_loop
    async def before_maintenance_loop(self):
        await self.bot.wait_until_ready()
    
    @commands.command(name="loungewaiting")
    async def lounge_waiting(self, ctx):
        """Check lounge status and requirements"""
        if not self.lounge_2:
            await ctx.send("❌ Lounge 2 not found!")
            return
        
        embed = discord.Embed(
            title="🏢 On-Demand Lounge System",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        
        # Lounge 2 status
        lounge_2_users = len(self.lounge_2.members)
        embed.add_field(
            name="🎯 Lounge 2 (Base)",
            value=f"**Users:** {lounge_2_users}/2+\n**Status:** {'✅ Active' if lounge_2_users >= 2 else '❌ Inactive'}",
            inline=False
        )
        
        # Active lounges
        active_lounges = []
        for lounge_num in sorted(self.our_lounges.keys()):
            lounge = self.our_lounges[lounge_num]['channel']
            users = len(lounge.members)
            status = "🟢 Active" if users > 0 else "⚫ Empty"
            active_lounges.append(f"**Lounge {lounge_num}:** {users} users ({status})")
        
        if active_lounges:
            embed.add_field(
                name="📊 Our Lounges",
                value="\n".join(active_lounges),
                inline=False
            )
        else:
            embed.add_field(
                name="📊 Our Lounges",
                value="None created yet",
                inline=False
            )
        
        # Spare lounge status
        if self.empty_lounges:
            embed.add_field(
                name="🆓 Spare Lounge(s)",
                value=f"Lounge {', '.join(map(str, self.empty_lounges))}",
                inline=False
            )
        else:
            embed.add_field(
                name="🆓 Spare Lounge",
                value="None - will create one soon",
                inline=False
            )
        
        # System status
        total_lounges = len(self.our_lounges)
        active_count = sum(1 for l in self.our_lounges.values() if l['channel'] and len(l['channel'].members) > 0)
        
        embed.add_field(
            name="⚙️ System Status",
            value=f"**Total:** {total_lounges} lounges\n**Active:** {active_count}\n**Empty:** {len(self.empty_lounges)}\n**Next:** Lounge {self.next_lounge_number}",
            inline=False
        )
        
        # Rules
        rules_text = """**Rules:**
1. Lounge 3 created when Lounge 2 has 2+ users
2. Lounge 4 created when Lounges 2 & 3 have 2+ users
3. Always keep 1 spare (empty) lounge
4. Delete extra empty lounges after 5min"""
        
        embed.add_field(
            name="📜 System Rules",
            value=rules_text,
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
            await ctx.send("❌ Can only create lounges 3+")
            return
            
        await ctx.send(f"🔨 Force-creating Lounge {number}...")
        
        success = await self.create_new_lounge(number)
        
        if success:
            await ctx.send(f"✅ Created Lounge {number}!")
        else:
            await ctx.send(f"❌ Failed to create Lounge {number}")
    
    @commands.command(name="forcedelete")
    @commands.has_permissions(administrator=True)
    async def force_delete_lounge(self, ctx, number: int):
        """Force delete a lounge (Admin only)"""
        if number < 3:
            await ctx.send("❌ Can only delete lounges 3+")
            return
            
        await ctx.send(f"🗑️ Force-deleting Lounge {number}...")
        
        success = await self.delete_lounge(number, "Admin command")
        
        if success:
            await ctx.send(f"✅ Deleted Lounge {number}!")
        else:
            await ctx.send(f"❌ Failed to delete Lounge {number} (might not exist)")
    
    @commands.command(name="checknow")
    async def check_now(self, ctx):
        """Manually trigger maintenance check"""
        await ctx.send("🔍 Running maintenance check...")
        await self.maintenance_loop()
        await ctx.send("✅ Check complete!")
    
    @commands.command(name="loungestats")
    async def lounge_stats(self, ctx):
        """Show detailed lounge statistics"""
        if not self.lounge_2:
            await ctx.send("❌ System not initialized!")
            return
        
        embed = discord.Embed(
            title="📊 Lounge System Statistics",
            color=discord.Color.green(),
            timestamp=datetime.now()
        )
        
        # Current status
        lounge_2_users = len(self.lounge_2.members)
        total_our_lounges = len(self.our_lounges)
        active_lounges = sum(1 for l in self.our_lounges.values() if l['channel'] and len(l['channel'].members) > 0)
        
        embed.add_field(
            name="Current Status",
            value=f"**Lounge 2 users:** {lounge_2_users}\n"
                  f"**Our lounges:** {total_our_lounges}\n"
                  f"**Active lounges:** {active_lounges}\n"
                  f"**Empty lounges:** {len(self.empty_lounges)}\n"
                  f"**Next lounge:** #{self.next_lounge_number}",
            inline=False
        )
        
        # List all lounges with details
        if self.our_lounges:
            lounge_details = []
            for lounge_num in sorted(self.our_lounges.keys()):
                lounge_data = self.our_lounges[lounge_num]
                channel = lounge_data['channel']
                
                if channel:
                    users = len(channel.members)
                    empty_for = ""
                    
                    if lounge_data['empty_since']:
                        empty_seconds = (datetime.now() - lounge_data['empty_since']).total_seconds()
                        empty_for = f" (empty for {int(empty_seconds)}s)"
                    
                    lounge_details.append(f"**Lounge {lounge_num}:** {users} users{empty_for}")
            
            if lounge_details:
                embed.add_field(
                    name="Lounges Details",
                    value="\n".join(lounge_details),
                    inline=False
                )
        
        # System info
        embed.add_field(
            name="System Info",
            value=f"**Inactivity timeout:** {self.inactivity_timeout//60} minutes\n"
                  f"**Check interval:** {self.check_interval} seconds\n"
                  f"**Spare lounges:** {len(self.empty_lounges)}/{max(1, len(self.our_lounges))}",
            inline=False
        )
        
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(LoungeCog(bot))