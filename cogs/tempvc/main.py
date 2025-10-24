import discord
from discord.ext import commands
import aiosqlite
import asyncio
import time

from .config import JOIN_CHANNELS, DB_PATH
from .views import BasicControlsView

class TempVoice(commands.Cog):
    """Advanced Temp voice Cog with interactive buttons"""

    def __init__(self, bot):
        self.bot = bot
        self.creation_cooldowns = {}
        self.cleanup_tasks = {}
        self.control_panels = {}

    async def _init_db(self):
        """Initialize database with proper async handling"""
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS voiceChannel(
                        userID INTEGER PRIMARY KEY,
                        voiceID INTEGER UNIQUE,
                        locked BOOLEAN DEFAULT FALSE,
                        hidden BOOLEAN DEFAULT FALSE,
                        channelName TEXT DEFAULT '',
                        channelLimit INTEGER DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                await db.commit()
        except aiosqlite.Error as e:
            print(f"Database initialization error: {e}")

    def _check_cooldown(self, user_id):
        """Check if user is on cooldown, return remaining seconds or 0 if ready"""
        current_time = time.time()
        self.creation_cooldowns = {
            uid: ts for uid, ts in self.creation_cooldowns.items() 
            if current_time - ts < 300
        }
        
        if user_id in self.creation_cooldowns:
            last_creation = self.creation_cooldowns[user_id]
            cooldown_remaining = 15 - (current_time - last_creation)
            if cooldown_remaining > 0:
                return cooldown_remaining
        return 0

    async def _cleanup_orphaned_channels(self, guild):
        """Clean up database entries for channels that no longer exist"""
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute("SELECT voiceID FROM voiceChannel") as cursor:
                    channels = await cursor.fetchall()
                    
                for (voice_id,) in channels:
                    if not guild.get_channel(voice_id):
                        await db.execute("DELETE FROM voiceChannel WHERE voiceID = ?", (voice_id,))
                
                await db.commit()
        except aiosqlite.Error as e:
            print(f"Cleanup error: {e}")

    # ── TEMP VC CREATION ──────────────────────────
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        """Handle voice state updates for temp VC creation and cleanup"""
        # CREATE TEMP VC
        if after and after.channel and after.channel.id in JOIN_CHANNELS:
            cooldown_remaining = self._check_cooldown(member.id)
            if cooldown_remaining > 0:
                embed = discord.Embed(
                    description=f"⏳ You're creating channels too quickly! Please wait {cooldown_remaining:.1f} seconds.",
                    color=discord.Color.orange()
                )
                try: 
                    await member.send(embed=embed)
                except: 
                    pass
                try:
                    await member.move_to(None)
                except:
                    pass
                return

            await self._create_temp_vc(member, after.channel)
            self.creation_cooldowns[member.id] = time.time()

        # DELETE EMPTY TEMP VC
        if before and before.channel and (not after or not after.channel or before.channel.id != after.channel.id):
            if before.channel.id in self.cleanup_tasks:
                self.cleanup_tasks[before.channel.id].cancel()
            
            self.cleanup_tasks[before.channel.id] = asyncio.create_task(
                self._cleanup_empty_vc(before.channel)
            )

    async def _create_temp_vc(self, member, join_channel):
        """Create a temporary voice channel"""
        try:
            base_name, limit = JOIN_CHANNELS[join_channel.id]
            guild = member.guild

            # Check if user already owns a VC
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute("SELECT * FROM voiceChannel WHERE userID = ?", (member.id,)) as cursor:
                    if await cursor.fetchone():
                        embed = discord.Embed(
                            description="❌ You already own a temporary voice channel!",
                            color=discord.Color.orange()
                        )
                        try: 
                            await member.send(embed=embed)
                        except: 
                            pass
                        try:
                            await member.move_to(None)
                        except:
                            pass
                        return

            existing = [
                ch for ch in guild.voice_channels 
                if ch.name.startswith(base_name.rstrip()) and ch.category == join_channel.category
                and ch.name[len(base_name):].strip().split()[0].isdigit()
            ]
            vc_number = len(existing) + 1
            vc_name = f"{base_name}{vc_number} ‹3"

            # Create temp VC
            temp_vc = await guild.create_voice_channel(
                name=vc_name,
                category=join_channel.category,
                position=join_channel.position,
                user_limit=limit,
                reason=f"{base_name} channel created by {member.display_name}"
            )

            try:
                await member.move_to(temp_vc)
            except discord.HTTPException as e:
                await temp_vc.delete(reason="Failed to move user to channel")
                raise e

            # Store in database with default values (not locked, not hidden)
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "INSERT OR REPLACE INTO voiceChannel (userID, voiceID, channelName, channelLimit, locked, hidden) VALUES (?, ?, ?, ?, ?, ?)", 
                    (member.id, temp_vc.id, vc_name, limit, False, False)  # Explicitly set locked and hidden to False
                )
                await db.commit()

            # Send control panel
            await self._send_control_panel(temp_vc, member.id)

        except Exception as e:
            print(f"Error creating temp VC: {e}")
            try:
                await member.send("❌ Failed to create voice channel. Please try again.")
            except:
                pass

    async def _send_control_panel(self, channel, owner_id):
        """Send interactive control panel to the voice channel"""
        view = BasicControlsView(self, channel.id, owner_id)
        
        embed = discord.Embed(
            title="🎯 Voice Channel Controls",
            description=(
                "**Use the buttons below to manage your voice channel:**\n\n"
                "**🔒 Access Controls:**\n"
                "• Lock/Unlock - Control who can join\n"
                "• Hide/Unhide - Control visibility\n"
                "• Limit - Set user limit\n"
                "• Rename - Change channel name\n\n"
                "**👥 Member Management:**\n"
                "• Trust users - Allow specific users to join\n"
                "• Ban users - Block specific users\n"
                "• Kick users - Remove from VC\n\n"
                "**🔄 Advanced:**\n"
                "• Reset - Reset all settings\n"
                "• Transfer - Give ownership to someone else"
            ),
            color=discord.Color.blue()
        )
        embed.set_footer(text="Only the VC owner can use these controls")
        
        try:
            await channel.send(embed=embed, view=view)
        except Exception as e:
            print(f"Failed to send control panel: {e}")

    async def _cleanup_empty_vc(self, channel):
        """Clean up empty temporary voice channels"""
        try:
            await asyncio.sleep(2)
            
            channel = self.bot.get_channel(channel.id)
            if not channel:
                return
            
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute("SELECT * FROM voiceChannel WHERE voiceID = ?", (channel.id,)) as cursor:
                    data = await cursor.fetchone()
                
                if data and len(channel.members) == 0:
                    try:
                        await channel.delete(reason="Temp VC empty → auto-deleted")
                    except discord.NotFound:
                        pass
                    except discord.HTTPException as e:
                        print(f"Failed to delete channel {channel.id}: {e}")
                        return
                    
                    await db.execute("DELETE FROM voiceChannel WHERE voiceID = ?", (channel.id,))
                    await db.commit()
            
            if channel.id in self.cleanup_tasks:
                del self.cleanup_tasks[channel.id]
                
        except Exception as e:
            print(f"Cleanup error: {e}")

    # Keep the old commands as fallback but make them hidden
    @commands.group(aliases=['voice', 'tempvc'], hidden=True)
    async def vc(self, ctx):
        """Temporary Voice Channel Management (Legacy)"""
        if ctx.invoked_subcommand is None:
            embed = discord.Embed(
                title="🎯 Voice Channel Controls Updated!",
                description=(
                    "We've upgraded to interactive buttons! 🎉\n\n"
                    "When you create a temporary voice channel, "
                    "you'll now get a control panel with buttons and dropdowns "
                    "for easy management.\n\n"
                    "**No more typing commands!** ✨"
                ),
                color=discord.Color.blue()
            )
            await ctx.send(embed=embed)

    async def cog_load(self):
        """Initialize database when cog loads"""
        await self._init_db()