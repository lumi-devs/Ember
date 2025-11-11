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
        self.cleanup_tasks = {}
        self.control_panels = {}
        
        # NEW: Proper queue system per generator channel
        self.creation_queues = {}  # join_channel.id -> deque()
        self.queue_locks = {}      # join_channel.id -> asyncio.Lock()
        self.queue_workers = {}    # join_channel.id -> asyncio.Task()
        
        # NEW: Track ongoing creations to prevent duplicates
        self.ongoing_creations = set()

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
        # Start background cleanup task
        self.bot.loop.create_task(self._periodic_ghost_cleanup())

    # -------------------------
    # Queue Management System
    # -------------------------
    async def _add_to_queue(self, member, join_channel):
        """Add a creation request to the queue and start worker if needed"""
        queue_id = join_channel.id
        
        # Initialize queue and lock for this generator if not exists
        if queue_id not in self.creation_queues:
            self.creation_queues[queue_id] = deque()
            self.queue_locks[queue_id] = asyncio.Lock()
        
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
        
        # Start worker if not running
        if queue_id not in self.queue_workers or self.queue_workers[queue_id].done():
            self.queue_workers[queue_id] = asyncio.create_task(
                self._queue_worker(queue_id)
            )
        
        # Wait for this request to be processed
        await request['event'].wait()
        return request.get('success', False)

    async def _queue_worker(self, queue_id):
        """Worker that processes creation requests one by one"""
        while True:
            try:
                # Get next request from queue
                async with self.queue_locks[queue_id]:
                    if not self.creation_queues[queue_id]:
                        # No more requests, stop worker
                        del self.queue_workers[queue_id]
                        break
                    
                    request = self.creation_queues[queue_id].popleft()
                
                # Process the request
                member = request['member']
                join_channel = request['join_channel']
                
                # Check if member is still in the join channel
                if not member.voice or member.voice.channel.id != queue_id:
                    request['success'] = False
                    request['event'].set()
                    continue
                
                # Execute creation with cooldown check
                success = await self._execute_creation(member, join_channel)
                request['success'] = success
                request['event'].set()
                
                # Small delay between creations to ensure proper sequencing
                await asyncio.sleep(0.5)
                
            except Exception as e:
                print(f"[TempVoice] Queue worker error for {queue_id}: {e}")
                # Mark failed request as done to prevent hanging
                if 'request' in locals():
                    request['success'] = False
                    request['event'].set()
                await asyncio.sleep(1)

    async def _execute_creation(self, member, join_channel):
        """Execute the actual VC creation with all safety checks"""
        # Check cooldown
        cooldown_remaining = self._check_cooldown(member.id)
        if cooldown_remaining > 0:
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

        # Run cleanup before creation
        await self._cleanup_all_empty_tracked_vcs(member.guild)

        # Create the VC
        try:
            await self._create_temp_vc(member, join_channel)
            self.creation_cooldowns[member.id] = time.time()
            return True
        except Exception as e:
            print(f"[TempVoice] Creation error for {member.display_name}: {e}")
            return False

    # -------------------------
    # Updated Voice State Handler with Queue
    # -------------------------
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        """Handle voice state updates with queue system"""
        
        # CREATE TEMP VC: when user joins a generator channel
        if after and after.channel and after.channel.id in JOIN_CHANNELS:
            # Add to queue instead of immediate creation
            asyncio.create_task(self._add_to_queue(member, after.channel))

        # DELETE EMPTY TEMP VC: schedule cleanup when member leaves
        if before and before.channel and (not after or not after.channel or before.channel.id != (after.channel.id if after and after.channel else None)):
            # Check if this is a tracked temp VC
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute("SELECT 1 FROM voiceChannel WHERE voiceID = ?", (before.channel.id,)) as cursor:
                    is_tracked = await cursor.fetchone()
            
            if not is_tracked:
                return
            
            # Schedule cleanup with improved task management
            await self._schedule_cleanup(before.channel)

    async def _schedule_cleanup(self, channel):
        """Schedule cleanup for a channel with proper task management"""
        # Cancel existing cleanup task
        if channel.id in self.cleanup_tasks:
            try:
                self.cleanup_tasks[channel.id].cancel()
            except Exception:
                pass
        
        # Create new cleanup task
        task = asyncio.create_task(self._cleanup_empty_vc(channel))
        self.cleanup_tasks[channel.id] = task
        
        # Clean up task entry when done
        def cleanup_task_entry(fut):
            self.cleanup_tasks.pop(channel.id, None)
            if fut.exception():
                print(f"[TempVoice] Cleanup task error: {fut.exception()}")
        
        task.add_done_callback(cleanup_task_entry)

    # -------------------------
    # Updated VC Creation with Better Sequencing
    # -------------------------
    async def _create_temp_vc(self, member: discord.Member, join_channel: discord.VoiceChannel):
        """Create temporary VC with proper queue-based sequencing"""
        try:
            guild = member.guild
            category = join_channel.category
            base_name, user_limit = JOIN_CHANNELS.get(join_channel.id, ("Temp VC ", 0))

            # Get all voice channels in category
            all_voice_channels = list(category.voice_channels)

            # Find used numbers for this base name
            used_numbers = []
            for ch in all_voice_channels:
                if ch.name.startswith(base_name):
                    suffix = ch.name[len(base_name):].strip().split(" ")[0]
                    if suffix.isdigit():
                        used_numbers.append(int(suffix))

            # Find smallest available number
            vc_number = 1
            while vc_number in used_numbers:
                vc_number += 1

            vc_name = f"{base_name}{vc_number}"

            # Create the VC
            vc = await category.create_voice_channel(
                vc_name,
                user_limit=user_limit or None,
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

            # Reorganize channels (non-blocking)
            asyncio.create_task(self._reorganize_channels(category, join_channel, base_name))

            return True

        except Exception as e:
            print(f"[TempVoice] VC creation error: {e}")
            return False

    async def _reorganize_channels(self, category, join_channel, base_name):
        """Reorganize channels after creation (non-blocking)"""
        try:
            await asyncio.sleep(1)  # Small delay to ensure all creations are settled
            
            all_voice_channels = list(category.voice_channels)
            related_channels = [c for c in all_voice_channels if c.name.startswith(base_name)]
            
            # Sort numerically
            def _suffix_key(c):
                rest = c.name[len(base_name):].strip().split(' ')[0]
                return int(rest) if rest.isdigit() else 9999
            
            related_channels.sort(key=_suffix_key)
            
            # Reposition
            base_pos = join_channel.position
            edits = []
            idx = 0
            
            for ch in related_channels:
                if ch.id == join_channel.id:
                    continue
                desired_pos = base_pos + idx + 1
                idx += 1
                if ch.position != desired_pos:
                    edits.append((ch, desired_pos))
            
            # Apply edits with small delays between API calls
            for ch, pos in edits:
                try:
                    await ch.edit(position=pos)
                    await asyncio.sleep(0.2)  # Rate limiting
                except Exception:
                    pass
                    
        except Exception as e:
            print(f"[TempVoice] Reorganization error: {e}")

    # -------------------------
    # Queue Status Command
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
                worker_running = queue_id in self.queue_workers
                
                embed.add_field(
                    name=f"#{channel_name}",
                    value=f"Pending: {queue_size}\nWorker: {'✅' if worker_running else '❌'}",
                    inline=True
                )
        
        await ctx.send(embed=embed)

    # -------------------------
    # Existing methods (keep them as is)
    # -------------------------
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

    async def _cleanup_empty_vc(self, channel: discord.VoiceChannel):
        """Clean up empty temporary voice channels with improved reliability."""
        channel_id = channel.id
        
        try:
            # Wait before checking to handle quick reconnects
            await asyncio.sleep(3)
            
            # Double-check if this is still a tracked temp VC using FRESH DB query
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute(
                    "SELECT userID FROM voiceChannel WHERE voiceID = ?", 
                    (channel_id,)
                ) as cursor:
                    row = await cursor.fetchone()

            if not row:
                # Not a tracked temp VC anymore
                self.cleanup_tasks.pop(channel_id, None)
                return

            # Get FRESH channel object from guild
            guild = channel.guild
            fresh_channel = guild.get_channel(channel_id)
            
            if not fresh_channel:
                # Channel doesn't exist - remove from DB
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("DELETE FROM voiceChannel WHERE voiceID = ?", (channel_id,))
                    await db.commit()
                self.cleanup_tasks.pop(channel_id, None)
                return

            # If channel has members, cancel cleanup
            if fresh_channel.members:
                self.cleanup_tasks.pop(channel_id, None)
                return

            # Channel is empty and tracked - delete it
            try:
                await fresh_channel.delete(reason="Temp VC empty → auto-deleted")
                print(f"[TempVoice] Successfully deleted empty VC: {fresh_channel.name} ({channel_id})")
                
                # Remove from DB after successful deletion
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("DELETE FROM voiceChannel WHERE voiceID = ?", (channel_id,))
                    await db.commit()
                    
            except discord.NotFound:
                # Channel already deleted - remove ghost entry
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("DELETE FROM voiceChannel WHERE voiceID = ?", (channel_id,))
                    await db.commit()
            except discord.Forbidden:
                print(f"[TempVoice] Missing permissions to delete channel: {fresh_channel.name}")
            except discord.HTTPException as e:
                print(f"[TempVoice] HTTP error deleting channel {fresh_channel.name}: {e}")

        except asyncio.CancelledError:
            # Task cancelled because user rejoined
            return
        except Exception as e:
            print(f"[TempVoice] Cleanup error for channel {channel_id}: {e}")
        finally:
            # Always clean up task entry
            self.cleanup_tasks.pop(channel_id, None)

async def setup(bot):
    cog = TempVoice(bot)
    await bot.add_cog(cog)