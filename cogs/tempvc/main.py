import discord
from discord.ext import commands
import aiosqlite
import asyncio
import time
from collections import deque
from .config import JOIN_CHANNELS, DB_PATH
from .views import BasicControlsView

class TempVoice(commands.Cog):
    """Advanced Temp voice Cog with interactive buttons and proper queue system"""
    
    def __init__(self, bot):
        self.bot = bot
        self.creation_cooldowns = {}
        
        # Global locks to prevent race conditions
        self.global_cleanup_lock = asyncio.Lock()
        self.global_db_lock = asyncio.Lock()
        self.creation_locks = {}  # per-guild creation locks
        
        # Queue system per generator channel
        self.creation_queues = {}  # join_channel.id -> deque()
        self.queue_locks = {}      # join_channel.id -> asyncio.Lock()
        self.queue_workers = {}    # join_channel.id -> asyncio.Task()
        self.worker_stop_flags = {} # join_channel.id -> bool flag to stop worker
        
        # Cleanup system - single task per guild instead of per channel
        self.cleanup_tasks = {}  # guild.id -> asyncio.Task()
        self.pending_cleanups = {}  # guild.id -> set[channel_id]
        self.cleanup_locks = {}  # guild.id -> asyncio.Lock()

    # -------------------------
    # Database initialization
    # -------------------------
    async def _init_db(self):
        """Initialize database with proper async handling"""
        try:
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
        except aiosqlite.Error as e:
            print(f"Database initialization error: {e}")

    async def cog_load(self):
        """Async cog loader that prepares DB and any startup state."""
        await self._init_db()
        # Load existing tracked VCs first
        await self._load_existing_tracked_vcs()
        # Start persistent queue workers for all join channels
        await self._start_persistent_workers()

    async def _start_persistent_workers(self):
        """Start persistent workers for all known join channels"""
        for guild in self.bot.guilds:
            for join_channel_id in JOIN_CHANNELS:
                channel = guild.get_channel(join_channel_id)
                if channel:
                    await self._ensure_queue_worker_running(join_channel_id)

    # -------------------------
    # Startup VC Loading
    # -------------------------
    async def _load_existing_tracked_vcs(self):
        """Load all existing tracked VCs into memory on startup"""
        try:
            print("[TempVoice] Loading existing tracked VCs from database...")
            
            for guild in self.bot.guilds:
                # Initialize guild-specific structures
                self.pending_cleanups[guild.id] = set()
                self.cleanup_locks[guild.id] = asyncio.Lock()
                self.creation_locks[guild.id] = asyncio.Lock()
                
                async with aiosqlite.connect(DB_PATH) as db:
                    async with db.execute("SELECT voiceID FROM voiceChannel") as cursor:
                        tracked_channels = await cursor.fetchall()
                
                cleanup_count = 0
                for (voice_id,) in tracked_channels:
                    channel = guild.get_channel(voice_id)
                    if channel and isinstance(channel, discord.VoiceChannel):
                        # Check if channel is empty and schedule cleanup if needed
                        if not channel.members:
                            print(f"[TempVoice] Found empty tracked VC on startup: {channel.name}")
                            await self._schedule_cleanup(channel)
                            cleanup_count += 1
                    else:
                        # Channel doesn't exist - remove ghost entry
                        async with aiosqlite.connect(DB_PATH) as db:
                            await db.execute("DELETE FROM voiceChannel WHERE voiceID = ?", (voice_id,))
                            await db.commit()
                        print(f"[TempVoice] Removed ghost entry on startup: {voice_id}")
                
                if cleanup_count > 0:
                    print(f"[TempVoice] Scheduled {cleanup_count} VCs for cleanup in {guild.name}")
                    
        except Exception as e:
            print(f"[TempVoice] Error loading existing VCs: {e}")

    # -------------------------
    # Ghost Entry Cleanup
    # -------------------------
    async def _cleanup_ghost_entries(self, guild):
        """Remove database entries for channels that no longer exist"""
        try:
            async with self.global_db_lock:
                async with aiosqlite.connect(DB_PATH) as db:
                    async with db.execute("SELECT voiceID FROM voiceChannel") as cursor:
                        channels = await cursor.fetchall()
                    
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
    # Queue Management System - FIXED
    # -------------------------

    async def _ensure_queue_worker_running(self, queue_id):
        """Ensure a queue worker is always running for a join channel"""
        if queue_id not in self.queue_workers or self.queue_workers[queue_id].done():
            # Clear stop flag if it exists
            self.worker_stop_flags[queue_id] = False
            # Start new worker
            self.queue_workers[queue_id] = asyncio.create_task(
                self._persistent_queue_worker(queue_id)
            )
            print(f"[TempVoice] 🔄 Started persistent worker for queue {queue_id}")

    async def _add_to_queue(self, member, join_channel):
        """Add a creation request to the queue and start worker if needed"""
        queue_id = join_channel.id
        
        # Initialize queue and lock for this generator if not exists
        if queue_id not in self.creation_queues:
            self.creation_queues[queue_id] = deque()
            self.queue_locks[queue_id] = asyncio.Lock()
        
        # Ensure worker is running
        await self._ensure_queue_worker_running(queue_id)
        
        # Create request object
        request = {
            'member': member,
            'join_channel': join_channel,
            'timestamp': time.time(),
            'event': asyncio.Event()
        }
        
        # Add to queue
        async with self.queue_locks[queue_id]:
            self.creation_queues[queue_id].append(request)
        
        print(f"[TempVoice] 📥 Added to queue {queue_id}: {member.display_name} (Queue size: {len(self.creation_queues[queue_id])})")
        
        # Wait for this request to be processed
        await request['event'].wait()
        return request.get('success', False)

    async def _persistent_queue_worker(self, queue_id):
        """Persistent worker that never stops and processes creation requests"""
        print(f"[TempVoice] 🔄 Starting persistent queue worker for {queue_id}")
        
        while not self.worker_stop_flags.get(queue_id, False):
            try:
                # Get next request from queue with timeout
                try:
                    async with self.queue_locks[queue_id]:
                        if self.creation_queues[queue_id]:
                            request = self.creation_queues[queue_id].popleft()
                        else:
                            request = None
                    
                    if request is None:
                        # No requests, wait a bit and check again
                        await asyncio.sleep(1)
                        continue
                
                except KeyError:
                    # Queue was deleted, wait and continue
                    await asyncio.sleep(1)
                    continue
                
                # Process the request
                member = request['member']
                join_channel = request['join_channel']
                
                # FIX: Check if member and guild still exist
                if not member or member.guild is None:
                    print(f"[TempVoice] ❌ Member {member} no longer in guild")
                    request['success'] = False
                    request['event'].set()
                    continue
                
                # Check if member is still in the join channel
                if not member.voice or member.voice.channel.id != queue_id:
                    print(f"[TempVoice] ❌ Member {member} left join channel before processing")
                    request['success'] = False
                    request['event'].set()
                    continue
                
                # Execute creation with cooldown check
                success = await self._execute_creation(member, join_channel)
                request['success'] = success
                request['event'].set()
                
                # Small delay between creations to ensure proper sequencing
                await asyncio.sleep(0.5)
                
            except asyncio.CancelledError:
                print(f"[TempVoice] 🔄 Queue worker {queue_id} was cancelled")
                break
            except Exception as e:
                print(f"[TempVoice] ❌ Queue worker error for {queue_id}: {type(e).__name__}: {str(e)}")
                import traceback
                print(f"[TempVoice] Traceback: {traceback.format_exc()}")
                # Mark failed request as done to prevent hanging
                if 'request' in locals():
                    request['success'] = False
                    request['event'].set()
                await asyncio.sleep(1)
        
        print(f"[TempVoice] 🔄 Queue worker {queue_id} stopped")

    async def _execute_creation(self, member, join_channel):
        """Execute the actual VC creation with all safety checks"""
        print(f"[TempVoice] 🔧 Executing creation for {member} in {join_channel.name}")
        
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

        # FIX: Ensure guild locks exist
        guild_id = member.guild.id
        if guild_id not in self.creation_locks:
            self.creation_locks[guild_id] = asyncio.Lock()
        if guild_id not in self.cleanup_locks:
            self.cleanup_locks[guild_id] = asyncio.Lock()
        if guild_id not in self.pending_cleanups:
            self.pending_cleanups[guild_id] = set()

        # Use guild lock to prevent race conditions between creation and cleanup
        async with self.creation_locks[guild_id]:
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
        """Handle voice state updates with queue system"""
        
        # FIX: Ensure member is still in a guild
        if member.guild is None:
            return
        
        # CREATE TEMP VC: when user joins a generator channel
        if after and after.channel and after.channel.id in JOIN_CHANNELS:
            # Add to queue instead of immediate creation
            asyncio.create_task(self._add_to_queue(member, after.channel))

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
                        # Schedule cleanup with improved task management
                        await self._schedule_cleanup(before.channel)
                except Exception as e:
                    print(f"[TempVoice] Error checking if channel is tracked: {e}")

    async def _schedule_cleanup(self, channel):
        """Schedule cleanup for a channel with proper task management"""
        guild_id = channel.guild.id
        
        async with self.cleanup_locks[guild_id]:
            # Add to pending cleanups
            self.pending_cleanups[guild_id].add(channel.id)
            
            # Start cleanup task if not running
            if guild_id not in self.cleanup_tasks or self.cleanup_tasks[guild_id].done():
                self.cleanup_tasks[guild_id] = asyncio.create_task(
                    self._cleanup_worker(guild_id)
                )

    async def _cleanup_worker(self, guild_id):
        """Single worker per guild that processes all pending cleanups"""
        print(f"[TempVoice] 🧹 Cleanup worker started for guild {guild_id}")
        
        try:
            # Wait before processing to handle quick reconnects
            await asyncio.sleep(5)
            
            guild = self.bot.get_guild(guild_id)
            if not guild:
                print(f"[TempVoice] ❌ Guild {guild_id} not found")
                return
            
            async with self.cleanup_locks[guild_id]:
                channels_to_clean = self.pending_cleanups[guild_id].copy()
                self.pending_cleanups[guild_id].clear()
            
            cleaned_count = 0
            for channel_id in channels_to_clean:
                channel = guild.get_channel(channel_id)
                if channel:
                    success = await self._cleanup_single_vc(channel)
                    if success:
                        cleaned_count += 1
                else:
                    # Channel doesn't exist - remove from DB
                    async with aiosqlite.connect(DB_PATH) as db:
                        await db.execute("DELETE FROM voiceChannel WHERE voiceID = ?", (channel_id,))
                        await db.commit()
                    print(f"[TempVoice] Removed ghost entry for {channel_id}")
            
            print(f"[TempVoice] ✅ Cleanup worker completed: {cleaned_count}/{len(channels_to_clean)} VCs cleaned")
            
        except Exception as e:
            print(f"[TempVoice] 💥 Cleanup worker error for guild {guild_id}: {e}")
        finally:
            # Clean up task entry
            self.cleanup_tasks.pop(guild_id, None)

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
                        success = await self._cleanup_single_vc(channel)
                        if success:
                            cleaned_count += 1
            
            if cleaned_count > 0:
                print(f"[TempVoice] Cleaned {cleaned_count} empty VCs in {guild.name}")
                
        except Exception as e:
            print(f"[TempVoice] Error cleaning empty VCs in {guild.name}: {e}")

    async def _cleanup_single_vc(self, channel):
        """Clean up a single empty VC - returns True if successful"""
        try:
            # Double-check if channel is still empty and tracked
            if channel.members:
                return False
            
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute("SELECT 1 FROM voiceChannel WHERE voiceID = ?", (channel.id,)) as cursor:
                    is_tracked = await cursor.fetchone()
            
            if not is_tracked:
                return False
            
            # Delete the channel
            await channel.delete(reason="Temp VC empty → auto-deleted")
            print(f"[TempVoice] ✅ Successfully deleted empty VC: {channel.name} ({channel.id})")
            
            # Remove from DB
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("DELETE FROM voiceChannel WHERE voiceID = ?", (channel.id,))
                await db.commit()
            
            return True
            
        except discord.NotFound:
            # Channel already deleted - remove ghost entry
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("DELETE FROM voiceChannel WHERE voiceID = ?", (channel.id,))
                await db.commit()
            return True
        except discord.Forbidden:
            print(f"[TempVoice] ❌ Missing permissions to delete channel: {channel.name}")
            return False
        except discord.HTTPException as e:
            print(f"[TempVoice] ❌ HTTP error deleting channel {channel.name}: {e}")
            return False
        except Exception as e:
            print(f"[TempVoice] ❌ Unexpected error cleaning {channel.name}: {e}")
            return False

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
    @commands.command()
    @commands.has_permissions(manage_channels=True)
    async def queue_status(self, ctx):
        """Check the current VC creation queue status"""
        embed = discord.Embed(title="🎯 VC Creation Queue Status", color=discord.Color.blue())
        
        if not self.creation_queues:
            embed.description = "No active queues"
        else:
            for queue_id, queue in self.creation_queues.items():
                channel = ctx.guild.get_channel(queue_id)
                channel_name = channel.name if channel else f"Unknown ({queue_id})"
                queue_size = len(queue)
                worker_running = queue_id in self.queue_workers and not self.queue_workers[queue_id].done()
                
                embed.add_field(
                    name=f"#{channel_name}",
                    value=f"Pending: {queue_size}\nWorker: {'✅ Running' if worker_running else '❌ Stopped'}",
                    inline=True
                )
        
        await ctx.send(embed=embed)

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
        cleanup_running = ctx.guild.id in self.cleanup_tasks
        
        embed.add_field(
            name="Cleanup Status",
            value=f"Pending: {pending_count}\nWorker: {'✅ Running' if cleanup_running else '❌ Stopped'}",
            inline=False
        )
        
        # Show queue worker status
        worker_status = []
        for queue_id in JOIN_CHANNELS:
            channel = ctx.guild.get_channel(queue_id)
            if channel:
                worker_alive = (
                    queue_id in self.queue_workers and 
                    not self.queue_workers[queue_id].done()
                )
                status = "✅" if worker_alive else "❌"
                worker_status.append(f"{status} #{channel.name}")
        
        if worker_status:
            embed.add_field(
                name="Queue Workers",
                value="\n".join(worker_status),
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

    @commands.command(nick='forceclean')
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
        
        success = await self._cleanup_single_vc(channel)
        if success:
            await ctx.send(f"✅ Force cleaned {channel.name}")
        else:
            await ctx.send(f"❌ Failed to clean {channel.name}")

    @commands.command()
    @commands.has_permissions(manage_channels=True)
    async def restart_workers(self, ctx):
        """Restart all queue workers"""
        # Stop all workers
        for queue_id in list(self.queue_workers.keys()):
            self.worker_stop_flags[queue_id] = True
            if queue_id in self.queue_workers:
                self.queue_workers[queue_id].cancel()
        
        # Clear queues
        self.creation_queues.clear()
        self.queue_workers.clear()
        self.worker_stop_flags.clear()
        
        # Restart workers
        await self._start_persistent_workers()
        
        await ctx.send("✅ Restarted all queue workers!")

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

    # -------------------------
    # Cog unload
    # -------------------------
    def cog_unload(self):
        # Set stop flags for all workers
        for queue_id in list(self.worker_stop_flags.keys()):
            self.worker_stop_flags[queue_id] = True
        
        # cancel pending cleanup tasks
        for t in list(self.cleanup_tasks.values()):
            try:
                t.cancel()
            except Exception:
                pass
        # cancel queue workers
        for t in list(self.queue_workers.values()):
            try:
                t.cancel()
            except Exception:
                pass

async def setup(bot):
    cog = TempVoice(bot)
    await bot.add_cog(cog)