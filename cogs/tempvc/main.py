# cogs/tempvoice.py
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
                "• Hide/Unhide - Control visibility\n                "  # small spacing preserved
                "\n**👥 Member Management:**\n"
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
    # Cleanup orphaned DB entries
    # -------------------------
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
        if before and before.channel:
            # Only schedule cleanup if this channel is tracked in DB (reduces task noise)
            channel_id = before.channel.id
            try:
                async with aiosqlite.connect(DB_PATH) as db:
                    async with db.execute("SELECT 1 FROM voiceChannel WHERE voiceID = ?", (channel_id,)) as cursor:
                        exists = await cursor.fetchone()
                if not exists:
                    # not a temp VC managed by us — do nothing
                    return
            except Exception as e:
                print(f"[TempVoice] DB check error when scheduling cleanup: {e}")
                # If DB fails, still attempt scheduling (safer to try)
            
            # cancel any existing cleanup task for that channel to reset timer
            if channel_id in self.cleanup_tasks:
                try:
                    self.cleanup_tasks[channel_id].cancel()
                except Exception:
                    pass

            # schedule new cleanup task (store by channel_id)
            self.cleanup_tasks[channel_id] = asyncio.create_task(
                self._cleanup_empty_vc(channel_id)
            )

    # -------------------------
    # Cleanup empty VC
    # -------------------------
    async def _cleanup_empty_vc(self, channel_id: int):
        """Clean up empty temporary voice channels based on DB entry (works even if renamed)."""
        try:
            # small buffer in case user reconnects quickly
            await asyncio.sleep(2)

            # Re-fetch channel object by id each time (it may have been deleted)
            channel = self.bot.get_channel(channel_id)
            if not channel:
                # channel already gone — ensure we clean the task entry and DB if necessary
                self.cleanup_tasks.pop(channel_id, None)
                # try to remove DB entry just in case (channel deleted but db still has it)
                try:
                    async with aiosqlite.connect(DB_PATH) as db:
                        await db.execute("DELETE FROM voiceChannel WHERE voiceID = ?", (channel_id,))
                        await db.commit()
                except Exception:
                    pass
                return

            # Confirm that this channel is tracked in DB (double-check)
            try:
                async with aiosqlite.connect(DB_PATH) as db:
                    async with db.execute(
                        "SELECT userID FROM voiceChannel WHERE voiceID = ?", (channel_id,)
                    ) as cursor:
                        row = await cursor.fetchone()
            except Exception as e:
                print(f"[TempVoice] DB read error during cleanup for {channel_id}: {e}")
                row = None

            if not row:
                # not a tracked temp VC
                self.cleanup_tasks.pop(channel_id, None)
                return

            # If channel has members, give a second chance
            if channel.members:
                # re-check after short delay to handle transient moves
                await asyncio.sleep(3)
                channel = self.bot.get_channel(channel_id)
                if channel and channel.members:
                    # still occupied — cancel cleanup
                    self.cleanup_tasks.pop(channel_id, None)
                    return

            # Attempt delete
            try:
                await channel.delete(reason="Temp VC empty → auto-deleted")
            except discord.NotFound:
                # already deleted, ok
                pass
            except discord.Forbidden:
                # missing permission to delete — log and keep DB entry so manual cleanup can occur
                print(f"[TempVoice] Missing permission to delete channel {channel_id}")
                self.cleanup_tasks.pop(channel_id, None)
                return
            except discord.HTTPException as e:
                # deletion failed for some transient reason; log and keep DB entry
                print(f"[TempVoice] Failed to delete channel {channel_id}: {e}")
                self.cleanup_tasks.pop(channel_id, None)
                return

            # Remove from DB after deletion
            try:
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("DELETE FROM voiceChannel WHERE voiceID = ?", (channel_id,))
                    await db.commit()
            except Exception as e:
                print(f"[TempVoice] DB cleanup error for {channel_id}: {e}")

            # clean entry
            self.cleanup_tasks.pop(channel_id, None)

        except asyncio.CancelledError:
            # task cancelled because maybe a user rejoined — just exit quietly
            return
        except Exception as e:
            print(f"[TempVoice] Cleanup error for {channel_id}: {e}")
            try:
                self.cleanup_tasks.pop(channel_id, None)
            except Exception:
                pass

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
