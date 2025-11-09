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
        # new: per-generator (join channel id) locks to serialize VC creation
        self.vc_creation_locks = {}  # join_channel.id -> asyncio.Lock()
        
        # DB init will run in cog_load
        # (do not call long-running async from __init__)

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
    # Periodic ghost cleanup
    # -------------------------
    async def _periodic_ghost_cleanup(self):
        """Background task to clean up ghost entries periodically"""
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                for guild in self.bot.guilds:
                    await self._cleanup_ghost_entries(guild)
                # Run every 30 minutes
                await asyncio.sleep(1800)
            except Exception as e:
                print(f"[TempVoice] Periodic cleanup error: {e}")
                await asyncio.sleep(300)  # Wait 5 minutes on error

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
    # Ghost entry cleanup
    # -------------------------
    async def _cleanup_ghost_entries(self, guild):
        """Remove database entries for channels that no longer exist"""
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                # Get all voice channel entries from DB
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
                    print(f"[TempVoice] Cleaned up {deleted_count} ghost entries")
                    
        except Exception as e:
            print(f"[TempVoice] Ghost entry cleanup error: {e}")

    # -------------------------
    # Check and cleanup empty tracked VCs
    # -------------------------
    async def _cleanup_all_empty_tracked_vcs(self, guild):
        """Check all tracked VCs and delete empty ones"""
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                # Get all tracked voice channels
                async with db.execute("SELECT voiceID FROM voiceChannel") as cursor:
                    tracked_channels = await cursor.fetchall()
            
            deleted_count = 0
            for (voice_id,) in tracked_channels:
                channel = guild.get_channel(voice_id)
                if channel and isinstance(channel, discord.VoiceChannel):
                    # Check if channel is empty
                    if not channel.members:
                        try:
                            await channel.delete(reason="Auto-cleanup: empty tracked VC")
                            # Remove from DB
                            async with aiosqlite.connect(DB_PATH) as db:
                                await db.execute("DELETE FROM voiceChannel WHERE voiceID = ?", (voice_id,))
                                await db.commit()
                            deleted_count += 1
                            print(f"[TempVoice] Auto-deleted empty tracked VC: {channel.name}")
                        except discord.NotFound:
                            # Channel already deleted - remove ghost entry
                            async with aiosqlite.connect(DB_PATH) as db:
                                await db.execute("DELETE FROM voiceChannel WHERE voiceID = ?", (voice_id,))
                                await db.commit()
                        except Exception as e:
                            print(f"[TempVoice] Failed to delete empty VC {channel.name}: {e}")
                elif not channel:
                    # Channel doesn't exist - remove ghost entry
                    async with aiosqlite.connect(DB_PATH) as db:
                        await db.execute("DELETE FROM voiceChannel WHERE voiceID = ?", (voice_id,))
                        await db.commit()
                    print(f"[TempVoice] Removed ghost entry for non-existent channel {voice_id}")
            
            if deleted_count > 0:
                print(f"[TempVoice] Auto-cleanup completed: deleted {deleted_count} empty VCs")
                
        except Exception as e:
            print(f"[TempVoice] Auto-cleanup error: {e}")

    # -------------------------
    # Voice state update
    # -------------------------
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        """Handle voice state updates for temp VC creation and cleanup"""
        
        # CREATE TEMP VC: when user joins a generator channel listed in JOIN_CHANNELS
        if after and after.channel and after.channel.id in JOIN_CHANNELS:
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
                return

            # Run cleanup of empty tracked VCs before creating new one
            await self._cleanup_all_empty_tracked_vcs(member.guild)

            # Get or create an asyncio.Lock for this generator channel id
            lock = self.vc_creation_locks.setdefault(after.channel.id, asyncio.Lock())
            
            # optional small jitter to reduce simultaneous API bursts (0-600ms)
            jitter = (hash(member.id) % 3) * 0.2  # 0.0, 0.2, or 0.4 seconds
            
            async with lock:
                try:
                    if jitter:
                        await asyncio.sleep(jitter)
                    await self._create_temp_vc(member, after.channel)
                    self.creation_cooldowns[member.id] = time.time()
                except Exception as e:
                    # prevent lock-holding errors; log and continue
                    print(f"[TempVoice] Error during queued VC creation: {e}")

        # DELETE EMPTY TEMP VC: schedule cleanup task when member leaves a channel
        if before and before.channel and (not after or not after.channel or before.channel.id != (after.channel.id if after and after.channel else None)):
            # Check if this is a tracked temp VC before scheduling cleanup
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute("SELECT 1 FROM voiceChannel WHERE voiceID = ?", (before.channel.id,)) as cursor:
                    is_tracked = await cursor.fetchone()
            
            if not is_tracked:
                return  # Not a temp VC, skip cleanup
            
            # cancel any existing cleanup task for that channel to reset timer
            if before.channel.id in self.cleanup_tasks:
                try:
                    self.cleanup_tasks[before.channel.id].cancel()
                except Exception:
                    pass
            
            # schedule new cleanup task
            self.cleanup_tasks[before.channel.id] = asyncio.create_task(
                self._cleanup_empty_vc(before.channel)
            )

    # -------------------------
    # Create temp VC (serialized per generator)
    # -------------------------
    async def _create_temp_vc(self, member: discord.Member, join_channel: discord.VoiceChannel):
        """Create a temporary VC with number reuse, batching, and stable positioning."""
        try:
            guild = member.guild
            category = join_channel.category
            base_name, user_limit = JOIN_CHANNELS.get(join_channel.id, ("Temp VC ", 0))

            # Cache all category voice channels once
            all_voice_channels = list(category.voice_channels)

            # Identify related channels (same base)
            related_channels = [c for c in all_voice_channels if c.name.startswith(base_name)]

            # Find used numbers
            used_numbers = []
            for ch in related_channels:
                suffix = ch.name[len(base_name):].strip().split(" ")[0]
                if suffix.isdigit():
                    used_numbers.append(int(suffix))

            # Reuse smallest missing number
            vc_number = 1
            while vc_number in used_numbers:
                vc_number += 1

            vc_name = f"{base_name}{vc_number}"

            # Create new VC
            vc = await category.create_voice_channel(
                vc_name,
                user_limit=user_limit or None,
                reason=f"{base_name} channel created by {member.display_name}"
            )

            # Give owner manage permission on the channel
            await vc.set_permissions(member, manage_channels=True, connect=True, speak=True)

            # Update related_channels list with new VC (re-query category)
            related_channels = [c for c in category.voice_channels if c.name.startswith(base_name)]

            # Sort them numerically by suffix
            def _suffix_key(c):
                rest = c.name[len(base_name):].strip().split(' ')[0]
                return int(rest) if rest.isdigit() else 9999

            related_channels.sort(key=_suffix_key)

            # Find position of generator channel
            base_pos = join_channel.position

            # Batch reposition (skip generator)
            edits = []
            idx = 0
            for ch in related_channels:
                if ch.id == join_channel.id:
                    continue
                desired_pos = base_pos + idx + 1
                idx += 1
                if ch.position != desired_pos:
                    edits.append((ch, desired_pos))

            # Apply all position edits sequentially (only if needed)
            for ch, pos in edits:
                try:
                    await ch.edit(position=pos)
                except Exception:
                    # ignore position edit failures
                    pass

            # Move member to new VC
            try:
                await member.move_to(vc)
            except Exception:
                # moving sometimes fails if member disconnected in between; ignore
                pass

            # Save to DB
            try:
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute(
                        """
                        INSERT OR REPLACE INTO voiceChannel (userID, voiceID, locked, hidden, channelName, channelLimit)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (member.id, vc.id, False, False, vc.name, user_limit)
                    )
                    await db.commit()
            except Exception as e:
                print(f"[TempVoice] DB write error on VC create: {e}")

            # Send control panel
            await self._send_control_panel(vc, member.id)

        except discord.Forbidden:
            print(f"Missing permissions to create or move user in {guild.name}")
        except discord.HTTPException as e:
            print(f"HTTP Error creating temp VC for {member.display_name}: {e}")
        except Exception as e:
            print(f"Unexpected error creating temp VC: {e}")

    # -------------------------
    # Cleanup empty VC - IMPROVED VERSION
    # -------------------------
    async def _cleanup_empty_vc(self, channel: discord.VoiceChannel):
        """Clean up empty temporary voice channels based on DB entry (works even if renamed)."""
        channel_id = channel.id
        
        try:
            # Wait before checking to handle quick reconnects
            await asyncio.sleep(3)
            
            # Refresh channel object
            channel = self.bot.get_channel(channel_id)
            if not channel:
                # Channel doesn't exist - remove ghost entry from DB
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("DELETE FROM voiceChannel WHERE voiceID = ?", (channel_id,))
                    await db.commit()
                self.cleanup_tasks.pop(channel_id, None)
                return

            # Double-check if this is still a tracked temp VC
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

            # If channel has members, cancel cleanup
            if channel.members:
                self.cleanup_tasks.pop(channel_id, None)
                return

            # Channel is empty and tracked - delete it
            try:
                await channel.delete(reason="Temp VC empty → auto-deleted")
            except discord.NotFound:
                # Channel already deleted elsewhere - remove ghost entry
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("DELETE FROM voiceChannel WHERE voiceID = ?", (channel_id,))
                    await db.commit()
            except discord.HTTPException as e:
                print(f"Failed to delete channel {channel_id}: {e}")
                # Keep the task for retry later?
                return

            # Remove from DB after successful deletion
            try:
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("DELETE FROM voiceChannel WHERE voiceID = ?", (channel_id,))
                    await db.commit()
            except Exception as e:
                print(f"[TempVoice] DB cleanup error: {e}")

            # Clean up task entry
            self.cleanup_tasks.pop(channel_id, None)

        except asyncio.CancelledError:
            # Task cancelled because user rejoined - exit quietly
            return
        except Exception as e:
            print(f"Cleanup error for channel {channel_id}: {e}")
            # Ensure task entry is cleaned up even on error
            try:
                self.cleanup_tasks.pop(channel_id, None)
            except Exception:
                pass

    # -------------------------
    # Manual cleanup command
    # -------------------------
    @commands.command()
    @commands.has_permissions(manage_channels=True)
    async def cleanup_vcs(self, ctx):
        """Manually clean up empty tracked VCs and ghost entries"""
        await self._cleanup_all_empty_tracked_vcs(ctx.guild)
        await ctx.send("✅ Cleanup completed!")

    # -------------------------
    # Legacy vc group (optional)
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
        # cancel pending cleanup tasks
        for t in list(self.cleanup_tasks.values()):
            try:
                t.cancel()
            except Exception:
                pass
        # no need to cancel locks; they will be garbage-collected

async def setup(bot):
    cog = TempVoice(bot)
    await bot.add_cog(cog)