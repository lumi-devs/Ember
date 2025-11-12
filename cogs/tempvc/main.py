import discord
from discord.ext import commands, tasks
import aiosqlite
import asyncio
import time
import os
import traceback
from collections import deque
from .config import JOIN_CHANNELS, DB_PATH
from .views import BasicControlsView


class TempVoice(commands.Cog):
    """Advanced Temp voice Cog with interactive buttons and resilient queue system"""

    def __init__(self, bot):
        self.bot = bot
        self.creation_cooldowns = {}
        self.creation_queues = {}  # join_channel.id -> deque()
        self.queue_locks = {}      # join_channel.id -> asyncio.Lock()
        self.pending_cleanups = {}  # guild.id -> set[channel_id]
        self.cleanup_locks = {}    # guild.id -> asyncio.Lock()

        # Start heartbeat task
        self._heartbeat.start()

    # -------------------------
    # Cog lifecycle
    # -------------------------
    async def cog_load(self):
        print("[TempVoice] Cog loading...")
        await self._init_db()
        await self._load_existing_tracked_vcs()
        print("[TempVoice] Cog loaded successfully!")

    def cog_unload(self):
        self._heartbeat.cancel()

    # -------------------------
    # Heartbeat watchdog
    # -------------------------
    @tasks.loop(minutes=10)
    async def _heartbeat(self):
        print(f"[TempVoice] ❤️ Heartbeat OK at {time.strftime('%X')}")

    @_heartbeat.before_loop
    async def _before_heartbeat(self):
        await self.bot.wait_until_ready()

    # -------------------------
    # Utility helpers
    # -------------------------
    def _ensure_guild_structures(self, guild_id):
        if guild_id not in self.cleanup_locks:
            self.cleanup_locks[guild_id] = asyncio.Lock()
        if guild_id not in self.pending_cleanups:
            self.pending_cleanups[guild_id] = set()

    def _ensure_queue_structures(self, join_id):
        if join_id not in self.creation_queues:
            self.creation_queues[join_id] = deque()
        if join_id not in self.queue_locks:
            self.queue_locks[join_id] = asyncio.Lock()

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

    # -------------------------
    # Database
    # -------------------------
    async def _init_db(self):
        db_dir = os.path.dirname(DB_PATH)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS voiceChannel(
                    guildID INTEGER,
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
        print(f"[TempVoice] Database ready: {DB_PATH}")

    # -------------------------
    # Voice Event Listener
    # -------------------------
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        """Handles creation and cleanup via queued system"""
        if not member or member.bot or not member.guild:
            return

        # Joining generator VC
        if after and after.channel and after.channel.id in JOIN_CHANNELS:
            join_channel = after.channel
            self._ensure_queue_structures(join_channel.id)
            self._ensure_guild_structures(member.guild.id)

            self.creation_queues[join_channel.id].append((member, join_channel))
            asyncio.create_task(self._queue_worker(join_channel.id))

        # Leaving VC → cleanup
        if before and before.channel:
            left_channel = before.channel and (not after.channel or before.channel.id != after.channel.id)
            if left_channel:
                try:
                    async with aiosqlite.connect(DB_PATH) as db:
                        async with db.execute("SELECT 1 FROM voiceChannel WHERE voiceID = ?", (before.channel.id,)) as c:
                            is_tracked = await c.fetchone()
                    if is_tracked:
                        await self._schedule_cleanup(before.channel)
                except Exception as e:
                    print(f"[TempVoice] Error during cleanup scheduling: {e}")

    # -------------------------
    # Queue Worker
    # -------------------------
    async def _queue_worker(self, join_id):
        """Ensures only one VC is created at a time per join channel"""
        async with self.queue_locks[join_id]:
            queue = self.creation_queues[join_id]
            while queue:
                member, join_channel = queue.popleft()
                try:
                    await self._process_creation(member, join_channel)
                except Exception as e:
                    print(f"[TempVoice] Queue task failed: {traceback.format_exc()}")
                await asyncio.sleep(1)  # small delay between creations

    # -------------------------
    # VC Creation Processor
    # -------------------------
    async def _process_creation(self, member, join_channel):
        cooldown_remaining = self._check_cooldown(member.id)
        if cooldown_remaining > 0:
            try:
                await member.move_to(None)
            except Exception:
                pass
            try:
                await member.send(
                    embed=discord.Embed(
                        description=f"⏳ You're creating channels too quickly! Wait {cooldown_remaining:.1f}s.",
                        color=discord.Color.orange()
                    )
                )
            except Exception:
                pass
            return

        guild = member.guild
        self._ensure_guild_structures(guild.id)

        await self._cleanup_ghost_entries(guild)
        await self._cleanup_all_empty_vcs(guild)

        success = await self._create_temp_vc(member, join_channel)
        if success:
            self.creation_cooldowns[member.id] = time.time()

    # -------------------------
    # VC Creation
    # -------------------------
    async def _create_temp_vc(self, member, join_channel):
        guild = member.guild
        category = join_channel.category
        base_name, user_limit = JOIN_CHANNELS.get(join_channel.id, ("Temp VC ", 0))

        all_vcs = list(category.voice_channels) if category else []
        used_numbers = [
            int(ch.name[len(base_name):].strip().split(" ")[0])
            for ch in all_vcs
            if ch.name.startswith(base_name) and ch.name[len(base_name):].strip().split(" ")[0].isdigit()
        ]

        vc_number = 1
        while vc_number in used_numbers:
            vc_number += 1
        vc_name = f"{base_name}{vc_number}"

        try:
            vc = await guild.create_voice_channel(
                name=vc_name,
                category=category,
                user_limit=user_limit or None,
                reason=f"Temp VC created by {member}"
            )
            await vc.set_permissions(member, manage_channels=True, connect=True, speak=True)
            try:
                await member.move_to(vc)
            except Exception:
                pass

            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "INSERT OR REPLACE INTO voiceChannel (guildID, userID, voiceID, channelName, channelLimit) VALUES (?, ?, ?, ?, ?)",
                    (guild.id, member.id, vc.id, vc_name, user_limit)
                )
                await db.commit()

            await self._send_control_panel(vc, member.id)
            print(f"[TempVoice] ✅ Created VC: {vc_name}")
            return True
        except Exception as e:
            print(f"[TempVoice] ❌ VC creation error: {traceback.format_exc()}")
            return False

    # -------------------------
    # Cleanup Logic
    # -------------------------
    async def _schedule_cleanup(self, channel):
        guild_id = channel.guild.id
        self._ensure_guild_structures(guild_id)
        async with self.cleanup_locks[guild_id]:
            self.pending_cleanups[guild_id].add(channel.id)
        asyncio.create_task(self._execute_cleanup(channel))

    async def _execute_cleanup(self, channel):
        try:
            await asyncio.sleep(5)
            if channel.members:
                async with self.cleanup_locks[channel.guild.id]:
                    self.pending_cleanups[channel.guild.id].discard(channel.id)
                return

            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute("SELECT 1 FROM voiceChannel WHERE voiceID = ?", (channel.id,)) as c:
                    is_tracked = await c.fetchone()
            if not is_tracked:
                return

            await channel.delete(reason="Empty temp VC cleanup")
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("DELETE FROM voiceChannel WHERE voiceID = ?", (channel.id,))
                await db.commit()
            print(f"[TempVoice] 🧹 Deleted {channel.name}")
        except Exception as e:
            print(f"[TempVoice] Cleanup error: {traceback.format_exc()}")
        finally:
            async with self.cleanup_locks[channel.guild.id]:
                self.pending_cleanups[channel.guild.id].discard(channel.id)

    async def _cleanup_ghost_entries(self, guild):
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT voiceID FROM voiceChannel WHERE guildID = ?", (guild.id,)) as c:
                rows = await c.fetchall()
            for (voice_id,) in rows:
                if not guild.get_channel(voice_id):
                    await db.execute("DELETE FROM voiceChannel WHERE voiceID = ?", (voice_id,))
            await db.commit()

    async def _cleanup_all_empty_vcs(self, guild):
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT voiceID FROM voiceChannel WHERE guildID = ?", (guild.id,)) as c:
                rows = await c.fetchall()
        for (voice_id,) in rows:
            ch = guild.get_channel(voice_id)
            if ch and not ch.members:
                await self._schedule_cleanup(ch)

    # -------------------------
    # Control Panel
    # -------------------------
    async def _send_control_panel(self, channel, owner_id):
        embed = discord.Embed(
            title="🎯 Voice Channel Controls",
            description=(
                "**Use the buttons below to manage your voice channel:**\n\n"
                "**🔒 Access Controls:** Lock / Hide\n"
                "**👥 Members:** Trust / Ban / Kick\n"
                "**🔄 Advanced:** Reset / Transfer"
            ),
            color=discord.Color.orange()
        )
        embed.set_footer(text="Only the VC owner can use these controls")
        try:
            await channel.send(embed=embed, view=BasicControlsView(self, channel.id, owner_id))
        except Exception:
            pass

    # -------------------------
    # Commands
    # -------------------------
    @commands.command(name="vcdebug")
    @commands.has_permissions(manage_channels=True)
    async def vcdebug(self, ctx):
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT voiceID, userID FROM voiceChannel WHERE guildID = ?", (ctx.guild.id,)) as c:
                tracked = await c.fetchall()
        embed = discord.Embed(title="🔧 VC Debug Info", color=discord.Color.orange())
        entries = []
        for voice_id, user_id in tracked:
            ch = ctx.guild.get_channel(voice_id)
            if ch:
                entries.append(f"{'🟢' if ch.members else '🔴'} `{ch.name}` - Members: {len(ch.members)}")
            else:
                entries.append(f"👻 {voice_id}")
        embed.description = "\\n".join(entries) or "No tracked VCs"
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(TempVoice(bot))
    print("[TempVoice] Cog setup complete ✅")
