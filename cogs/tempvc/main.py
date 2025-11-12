import discord
from discord.ext import commands
import aiosqlite
import asyncio
import time
import os
from collections import deque
from .config import JOIN_CHANNELS, DB_PATH
from .views import BasicControlsView

class TempVoice(commands.Cog):
    """Advanced Temp voice Cog with interactive buttons"""
    
    def __init__(self, bot):
        self.bot = bot
        self.creation_cooldowns = {}
        
        # Simple queue per join channel
        self.creation_queues = {}  # join_channel.id -> deque()
        self.queue_locks = {}      # join_channel.id -> asyncio.Lock()
        
        # Cleanup tracking
        self.pending_cleanups = {}  # guild.id -> set[channel_id]
        self.cleanup_locks = {}    # guild.id -> asyncio.Lock()

    # -------------------------
    # Database initialization
    # -------------------------
    async def _init_db(self):
        """Initialize database with proper async handling"""
        try:
            # Ensure the directory exists
            db_dir = os.path.dirname(DB_PATH)
            if db_dir and not os.path.exists(db_dir):
                os.makedirs(db_dir)
                print(f"[TempVoice] Created database directory: {db_dir}")
            
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS voiceChannel(
                        userID INTEGER,
                        voiceID INTEGER UNIQUE,
                        locked BOOLEAN DEFAULT FALSE,
                        hidden BOOLEAN DEFAULT FALSE,
                        channelName TEXT DEFAULT '',
                        channelLimit INTEGER DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                await db.commit()
                print(f"[TempVoice] Database initialized successfully: {DB_PATH}")
        except aiosqlite.Error as e:
            print(f"[TempVoice] Database initialization error: {e}")
        except Exception as e:
            print(f"[TempVoice] Unexpected error during DB init: {e}")

    async def cog_load(self):
        """Async cog loader that prepares DB and any startup state."""
        print("[TempVoice] Cog loading...")
        await self._init_db()
        # Load existing tracked VCs first
        await self._load_existing_tracked_vcs()
        print("[TempVoice] Cog loaded successfully!")

    # -------------------------
    # Startup VC Loading
    # -------------------------
    async def _load_existing_tracked_vcs(self):
        """Load all existing tracked VCs from database on startup"""
        try:
            print("[TempVoice] Loading existing tracked VCs from database...")
            
            # Check if database file exists
            if not os.path.exists(DB_PATH):
                print(f"[TempVoice] Database file not found: {DB_PATH}")
                return
            
            for guild in self.bot.guilds:
                print(f"[TempVoice] Checking guild: {guild.name}")
                # Initialize guild-specific structures
                self.pending_cleanups[guild.id] = set()
                self.cleanup_locks[guild.id] = asyncio.Lock()
                
                async with aiosqlite.connect(DB_PATH) as db:
                    async with db.execute("SELECT voiceID FROM voiceChannel") as cursor:
                        tracked_channels = await cursor.fetchall()
                
                print(f"[TempVoice] Found {len(tracked_channels)} tracked channels in database for {guild.name}")
                
                cleanup_count = 0
                for (voice_id,) in tracked_channels:
                    channel = guild.get_channel(voice_id)
                    if channel and isinstance(channel, discord.VoiceChannel):
                        # Check if channel is empty and schedule cleanup if needed
                        if not channel.members:
                            print(f"[TempVoice] Found empty tracked VC on startup: {channel.name} ({channel.id})")
                            await self._schedule_cleanup(channel)
                            cleanup_count += 1
                        else:
                            print(f"[TempVoice] Found active tracked VC: {channel.name} with {len(channel.members)} members")
                    else:
                        # Channel doesn't exist - remove ghost entry
                        print(f"[TempVoice] Removing ghost entry: {voice_id}")
                        async with aiosqlite.connect(DB_PATH) as db:
                            await db.execute("DELETE FROM voiceChannel WHERE voiceID = ?", (voice_id,))
                            await db.commit()
                
                if cleanup_count > 0:
                    print(f"[TempVoice] Scheduled {cleanup_count} VCs for cleanup in {guild.name}")
                    
        except Exception as e:
            print(f"[TempVoice] Error loading existing VCs: {e}")
            import traceback
            print(f"[TempVoice] Traceback: {traceback.format_exc()}")

    # -------------------------
    # Ghost Entry Cleanup
    # -------------------------
    async def _cleanup_ghost_entries(self, guild):
        """Remove database entries for channels that no longer exist"""
        try:
            print(f"[TempVoice] Cleaning ghost entries for {guild.name}")
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute("SELECT voiceID FROM voiceChannel") as cursor:
                    channels = await cursor.fetchall()
                
                print(f"[TempVoice] Checking {len(channels)} channels for ghost entries")
                
                deleted_count = 0
                for (voice_id,) in channels:
                    # Check if channel still exists in Discord
                    if not guild.get_channel(voice_id):
                        await db.execute("DELETE FROM voiceChannel WHERE voiceID = ?", (voice_id,))
                        deleted_count += 1
                        print(f"[TempVoice] Removed ghost entry for deleted channel {voice_id}")
                
                if deleted_count > 0:
                    await db.commit()
                    print(f"[TempVoice] Cleaned up {deleted_count} ghost entries in {guild.name}")
                else:
                    print(f"[TempVoice] No ghost entries found in {guild.name}")
                    
        except Exception as e:
            print(f"[TempVoice] Ghost entry cleanup error in {guild.name}: {e}")

    # -------------------------
    # Control panel sender
    # -------------------------
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
                "**👥 Member Management:**\n"
                "• Trust users - Allow specific users to join\n"
                "• Ban users - Block specific users\n"
                "• Kick users - Remove from VC\n\n"
                "**🔄 Advanced:**\n"
                "• Reset - Reset all settings\n"
                "• Transfer - Give ownership to someone else"
            ),
            color=discord.Color.orange()
        )
        embed.set_footer(text="Only the VC owner can use these controls")
        
        try:
            await channel.send(embed=embed, view=view)
        except Exception as e:
            print(f"Failed to send control panel: {e}")

    # -------------------------
    # Cooldown helper
    # -------------------------
    def _check_cooldown(self, user_id):
        """Check if user is on cooldown, return remaining seconds or 0 if ready"""
        current_time = time.time()
        # keep only cooldowns within the last 300 seconds
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

    # -------------------------
    # Simple Processing System
    # -------------------------
    async def _process_creation(self, member, join_channel):
        """Process VC creation immediately without complex workers"""
        print(f"[TempVoice] 🔧 Processing creation for {member} in {join_channel.name}")
        
        # Check cooldown
        cooldown_remaining = self._check_cooldown(member.id)
        if cooldown_remaining > 0:
            print(f"[TempVoice] ⏳ Cooldown active for {member}: {cooldown_remaining}s")
            embed = discord.Embed(
                description=f"⏳ You're creating channels too quickly! Please wait {cooldown_remaining:.1f} seconds.",
                color=discord.Color.orange()
            )
            try:
                await member.send(embed=embed)
            except Exception:
                pass
            try:
                await member.move_to(None)
            except Exception:
                pass
            return False

        # Ensure guild structures exist - FIXED: use member.guild instead of guild
        guild_id = member.guild.id
        if guild_id not in self.cleanup_locks:
            self.cleanup_locks[guild_id] = asyncio.Lock()
        if guild_id not in self.pending_cleanups:
            self.pending_cleanups[guild_id] = set()  # FIXED: use guild_id instead of guild.id

        # Clean ghost entries before creating new VC
        print(f"[TempVoice] 🧹 Running ghost cleanup for {member.guild.name}")
        await self._cleanup_ghost_entries(member.guild)
        
        # Clean any pending empty VCs before creating new ones
        print(f"[TempVoice] 🧹 Running empty VC cleanup for {member.guild.name}")
        await self._cleanup_all_empty_vcs(member.guild)

        # Create the VC
        try:
            print(f"[TempVoice] 🚀 Creating VC for {member}...")
            success = await self._create_temp_vc(member, join_channel)
            if success:
                self.creation_cooldowns[member.id] = time.time()
                print(f"[TempVoice] ✅ VC creation successful for {member}")
            else:
                print(f"[TempVoice] ❌ VC creation failed for {member}")
            return success
        except Exception as e:
            print(f"[TempVoice] 💥 Creation error for {member.display_name}: {repr(e)}")
            return False
    # -------------------------
    # Voice State Handler
    # -------------------------
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        """Handle voice state updates with simple processing"""
        
        # FIX: Ensure member is still in a guild
        if member.guild is None:
            return
        
        # CREATE TEMP VC: when user joins a generator channel
        if after and after.channel and after.channel.id in JOIN_CHANNELS:
            # Process immediately without queue
            asyncio.create_task(self._process_creation(member, after.channel))

        # DELETE EMPTY TEMP VC: schedule cleanup when member leaves
        if before and before.channel:
            # Check if user actually left a channel (not just moved between channels)
            left_channel = (
                before.channel and 
                (not after.channel or before.channel.id != after.channel.id)
            )
            
            if left_channel:
                # Check if this is a tracked temp VC
                try:
                    async with aiosqlite.connect(DB_PATH) as db:
                        async with db.execute("SELECT 1 FROM voiceChannel WHERE voiceID = ?", (before.channel.id,)) as cursor:
                            is_tracked = await cursor.fetchone()
                    
                    if is_tracked:
                        print(f"[TempVoice] Scheduling cleanup for {before.channel.name} ({before.channel.id})")
                        # Schedule cleanup
                        await self._schedule_cleanup(before.channel)
                except Exception as e:
                    print(f"[TempVoice] Error checking if channel is tracked: {e}")

    async def _schedule_cleanup(self, channel):
        """Schedule cleanup for a channel"""
        guild_id = channel.guild.id
        
        async with self.cleanup_locks[guild_id]:
            # Add to pending cleanups
            self.pending_cleanups[guild_id].add(channel.id)
        
        # Start cleanup task
        asyncio.create_task(self._execute_cleanup(channel))

    async def _execute_cleanup(self, channel):
        """Execute cleanup for a single channel after delay"""
        try:
            # Wait before processing to handle quick reconnects
            await asyncio.sleep(5)
            
            # Double-check if channel is still empty and tracked
            if channel.members:
                print(f"[TempVoice] Cleanup cancelled - channel not empty: {channel.name}")
                # Remove from pending cleanups
                async with self.cleanup_locks[channel.guild.id]:
                    self.pending_cleanups[channel.guild.id].discard(channel.id)
                return
            
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute("SELECT 1 FROM voiceChannel WHERE voiceID = ?", (channel.id,)) as cursor:
                    is_tracked = await cursor.fetchone()
            
            if not is_tracked:
                # Remove from pending cleanups
                async with self.cleanup_locks[channel.guild.id]:
                    self.pending_cleanups[channel.guild.id].discard(channel.id)
                return
            
            # Delete the channel
            await channel.delete(reason="Temp VC empty → auto-deleted")
            print(f"[TempVoice] ✅ Successfully deleted empty VC: {channel.name} ({channel.id})")
            
            # Remove from DB
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("DELETE FROM voiceChannel WHERE voiceID = ?", (channel.id,))
                await db.commit()
            
            # Remove from pending cleanups
            async with self.cleanup_locks[channel.guild.id]:
                self.pending_cleanups[channel.guild.id].discard(channel.id)
            
        except discord.NotFound:
            # Channel already deleted - remove ghost entry
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("DELETE FROM voiceChannel WHERE voiceID = ?", (channel.id,))
                await db.commit()
            print(f"[TempVoice] Channel already deleted: {channel.id}")
            # Remove from pending cleanups
            async with self.cleanup_locks[channel.guild.id]:
                self.pending_cleanups[channel.guild.id].discard(channel.id)
        except discord.Forbidden:
            print(f"[TempVoice] ❌ Missing permissions to delete channel: {channel.name}")
            # Remove from pending cleanups
            async with self.cleanup_locks[channel.guild.id]:
                self.pending_cleanups[channel.guild.id].discard(channel.id)
        except discord.HTTPException as e:
            print(f"[TempVoice] ❌ HTTP error deleting channel {channel.name}: {e}")
            # Remove from pending cleanups
            async with self.cleanup_locks[channel.guild.id]:
                self.pending_cleanups[channel.guild.id].discard(channel.id)
        except Exception as e:
            print(f"[TempVoice] ❌ Unexpected error cleaning {channel.name}: {e}")
            # Remove from pending cleanups
            async with self.cleanup_locks[channel.guild.id]:
                self.pending_cleanups[channel.guild.id].discard(channel.id)

    async def _cleanup_all_empty_vcs(self, guild):
        """Clean up all empty tracked VCs in a guild"""
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute("SELECT voiceID FROM voiceChannel") as cursor:
                    tracked_channels = await cursor.fetchall()
            
            cleaned_count = 0
            for (voice_id,) in tracked_channels:
                channel = guild.get_channel(voice_id)
                if channel and isinstance(channel, discord.VoiceChannel):
                    if not channel.members:
                        # Schedule cleanup for each empty VC
                        await self._schedule_cleanup(channel)
                        cleaned_count += 1
            
            if cleaned_count > 0:
                print(f"[TempVoice] Scheduled {cleaned_count} empty VCs for cleanup in {guild.name}")
                
        except Exception as e:
            print(f"[TempVoice] Error cleaning empty VCs in {guild.name}: {e}")

    # -------------------------
    # VC Creation
    # -------------------------
    async def _create_temp_vc(self, member: discord.Member, join_channel: discord.VoiceChannel):
        """Create temporary VC at correct position immediately"""
        try:
            guild = member.guild
            category = join_channel.category
            base_name, user_limit = JOIN_CHANNELS.get(join_channel.id, ("Temp VC ", 0))

            # Get all voice channels in category
            all_voice_channels = list(category.voice_channels)

            # Find used numbers and calculate correct position
            used_numbers = []
            temp_channels = []
            
            for ch in all_voice_channels:
                if ch.name.startswith(base_name):
                    suffix = ch.name[len(base_name):].strip().split(" ")[0]
                    if suffix.isdigit():
                        used_numbers.append(int(suffix))
                        temp_channels.append(ch)

            # Find smallest available number
            vc_number = 1
            while vc_number in used_numbers:
                vc_number += 1

            vc_name = f"{base_name}{vc_number}"

            # Calculate correct position - right after the join channel
            position = join_channel.position
            
            # If there are existing temp channels, find the highest position among them
            if temp_channels:
                highest_temp_position = max(ch.position for ch in temp_channels)
                position = highest_temp_position + 1

            # Create the VC at the correct position from the start
            vc = await category.create_voice_channel(
                vc_name,
                user_limit=user_limit or None,
                position=position,
                reason=f"Temp VC created by {member.display_name}"
            )

            # Give owner permissions
            await vc.set_permissions(member, manage_channels=True, connect=True, speak=True)

            # Move member to new VC
            try:
                await member.move_to(vc)
            except Exception as e:
                print(f"[TempVoice] Failed to move member {member.display_name}: {e}")
                # Still continue with setup even if move fails

            # Save to DB
            try:
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute(
                        """
                        INSERT OR REPLACE INTO voiceChannel (userID, voiceID, locked, hidden, channelName, channelLimit)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (member.id, vc.id, False, False, vc_name, user_limit)
                    )
                    await db.commit()
            except Exception as e:
                print(f"[TempVoice] DB write error: {e}")

            # Send control panel
            await self._send_control_panel(vc, member.id)

            print(f"[TempVoice] ✅ Created VC: {vc_name} for {member.display_name}")
            return True

        except Exception as e:
            print(f"[TempVoice] VC creation error: {e}")
            return False

    # -------------------------
    # Commands
    # -------------------------
    @commands.command(nick='vcdebug')
    @commands.has_permissions(manage_channels=True)
    async def vcdebug(self, ctx):
        """Debug current VC status"""
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT voiceID, userID FROM voiceChannel") as cursor:
                tracked_channels = await cursor.fetchall()
        
        embed = discord.Embed(title="🔧 VC Debug Info", color=discord.Color.orange())
        
        active_vcs = []
        empty_vcs = []
        ghost_entries = []
        
        for voice_id, user_id in tracked_channels:
            channel = ctx.guild.get_channel(voice_id)
            if channel:
                member_count = len(channel.members)
                if member_count > 0:
                    active_vcs.append(f"🟢 `{channel.name}` - Members: {member_count}")
                else:
                    empty_vcs.append(f"🔴 `{channel.name}` - Members: {member_count}")
            else:
                ghost_entries.append(f"👻 {voice_id}")
        
        # Combine all lists
        all_entries = active_vcs + empty_vcs + ghost_entries
        
        embed.add_field(
            name="Tracked VCs",
            value="\n".join(all_entries) if all_entries else "None",
            inline=False
        )
        
        # Show cleanup status
        pending_count = len(self.pending_cleanups.get(ctx.guild.id, set()))
        
        embed.add_field(
            name="Cleanup Status",
            value=f"Pending Cleanups: {pending_count}",
            inline=False
        )
        
        await ctx.send(embed=embed)

    @commands.command()
    @commands.has_permissions(manage_channels=True)
    async def cleanup_vcs(self, ctx):
        """Manually clean up empty tracked VCs and ghost entries"""
        # Clean ghost entries first
        await self._cleanup_ghost_entries(ctx.guild)
        
        # Then clean empty VCs
        await self._cleanup_all_empty_vcs(ctx.guild)
        
        await ctx.send("✅ Manual cleanup completed!")

    @commands.command()
    @commands.has_permissions(manage_channels=True)
    async def force_clean_channel(self, ctx, channel_id: int = None):
        """Force cleanup of a specific channel"""
        if not channel_id:
            # Find any empty VC
            for channel in ctx.guild.voice_channels:
                if not channel.members:
                    channel_id = channel.id
                    break
        
        if not channel_id:
            return await ctx.send("❌ No empty channels found")
        
        channel = ctx.guild.get_channel(channel_id)
        if not channel:
            return await ctx.send("❌ Channel not found")
        
        # Direct cleanup without scheduling
        try:
            if channel.members:
                return await ctx.send("❌ Channel is not empty")
            
            await channel.delete(reason="Manual force cleanup")
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("DELETE FROM voiceChannel WHERE voiceID = ?", (channel.id,))
                await db.commit()
            
            await ctx.send(f"✅ Force cleaned {channel.name}")
        except Exception as e:
            await ctx.send(f"❌ Failed to clean {channel.name}: {e}")

    # -------------------------
    # Legacy vc group
    # -------------------------
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

async def setup(bot):
    cog = TempVoice(bot)
    await bot.add_cog(cog)
    print("[TempVoice] Cog setup complete!")