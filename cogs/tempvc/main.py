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

    async def _create_temp_vc(self, member: discord.Member, join_channel: discord.VoiceChannel):
        """Create a temporary VC with number reuse, batching, and stable positioning (generator untouched)."""
        try:
            guild = member.guild
            category = join_channel.category
            base_name, user_limit = JOIN_CHANNELS.get(join_channel.id, ("Temp VC ", 0))

            # ── Cache all category voice channels once ──
            all_voice_channels = list(category.voice_channels)

            # ── Identify related channels (same base) ──
            related_channels = [c for c in all_voice_channels if c.name.startswith(base_name)]

            # ── Find used numbers ──
            used_numbers = []
            for ch in related_channels:
                suffix = ch.name[len(base_name):].strip().split(" ")[0]
                if suffix.isdigit():
                    used_numbers.append(int(suffix))

            # ── Reuse smallest missing number ──
            vc_number = 1
            while vc_number in used_numbers:
                vc_number += 1
            vc_name = f"{base_name}{vc_number}"

            # ── Create new VC ──
            vc = await category.create_voice_channel(
                vc_name,
                user_limit=user_limit or None,
                reason=f"{base_name} channel created by {member.display_name}"
            )
            await vc.set_permissions(member, manage_channels=True, connect=True, speak=True)
            # ── Update related_channels list with new VC ──
            related_channels = [c for c in category.voice_channels if c.name.startswith(base_name)]

            # ── Sort them numerically by suffix ──
            related_channels.sort(
                key=lambda c: (
                    int(c.name[len(base_name):].strip().split(' ')[0])
                    if c.name[len(base_name):].strip().split(' ')[0].isdigit()
                    else 9999
                )
            )

            # ── Find position of generator ──
            base_pos = join_channel.position

            # ── Batch reposition (skip generator) ──
            edits = []
            for i, ch in enumerate(related_channels):
                if ch.id == join_channel.id:
                    continue
                desired_pos = base_pos + i + 1
                if ch.position != desired_pos:
                    edits.append((ch, desired_pos))

            # ── Apply all position edits sequentially (only if needed) ──
            for ch, pos in edits:
                await ch.edit(position=pos)

            # ── Move member to new VC ──
            await member.move_to(vc)

            # ── Save to DB ──
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    """
                    INSERT OR REPLACE INTO voiceChannel (userID, voiceID, locked, hidden, channelName, channelLimit)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (member.id, vc.id, False, False, vc.name, user_limit)
                )
                await db.commit()

            # ── Send control panel ──
            await self._send_control_panel(vc, member.id)

        except discord.Forbidden:
            print(f"Missing permissions to create or move user in {guild.name}")
        except discord.HTTPException as e:
            print(f"HTTP Error creating temp VC for {member.display_name}: {e}")
        except Exception as e:
            print(f"Unexpected error creating temp VC: {e}")




    async def _cleanup_empty_vc(self, channel: discord.VoiceChannel):
        """Clean up empty temporary voice channels based on DB entry (works even if renamed)."""
        try:
            await asyncio.sleep(2)  # small buffer in case user reconnects quickly
            channel = self.bot.get_channel(channel.id)
            if not channel:
                return

            # ── Check if this channel exists in DB ──
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute(
                    "SELECT userID FROM voiceChannel WHERE voiceID = ?", (channel.id,)
                ) as cursor:
                    row = await cursor.fetchone()

            if not row:
                return  # not a tracked temp VC

            # ── Skip deletion if not empty ──
            if channel.members:
                return

            # ── Delete VC ──
            try:
                await channel.delete(reason="Temp VC empty → auto-deleted")
            except discord.NotFound:
                pass
            except discord.HTTPException as e:
                print(f"Failed to delete channel {channel.id}: {e}")
                return

            # ── Remove from DB ──
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("DELETE FROM voiceChannel WHERE voiceID = ?", (channel.id,))
                await db.commit()

            self.cleanup_tasks.pop(channel.id, None)

        except asyncio.CancelledError:
            return
        except Exception as e:
            print(f"Cleanup error: {e}")


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
