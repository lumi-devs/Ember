import discord
from discord.ext import commands, tasks
import asyncio
import aiosqlite
import time
from discord import app_commands
import traceback
from collections import deque
from .config import JOIN_CHANNELS, DB_PATH
from .views import BasicControlsView


class TempVoice(commands.Cog):
    """Temp Voice with stability queue (same functionality, no behavior changes)"""

    def __init__(self, bot):
        self.bot = bot
        self.creation_cooldowns = {}
        self.cleanup_tasks = {}
        self.creation_queues = {}   # join_channel.id -> deque()
        self.queue_locks = {}       # join_channel.id -> asyncio.Lock()
        self.cleanup_locks = {}     # guild.id -> asyncio.Lock()
        self._heartbeat.start()

    async def cog_load(self):
        print("[TempVoice] Cog loaded with queue protection ✅")
        await self._init_db()

    def cog_unload(self):
        self._heartbeat.cancel()

    # ---------------- HEARTBEAT ----------------
    @tasks.loop(minutes=10)
    async def _heartbeat(self):
        print(f"[TempVoice] ❤️ heartbeat OK - {time.strftime('%X')}")

    @_heartbeat.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()

    # ---------------- HELPERS ----------------
    def _ensure_structs(self, join_id, guild_id):
        if join_id not in self.creation_queues:
            self.creation_queues[join_id] = deque()
        if join_id not in self.queue_locks:
            self.queue_locks[join_id] = asyncio.Lock()
        if guild_id not in self.cleanup_locks:
            self.cleanup_locks[guild_id] = asyncio.Lock()

    def _cooldown_ok(self, user_id, cooldown=15):
        now = time.time()
        last = self.creation_cooldowns.get(user_id, 0)
        if now - last < cooldown:
            return False, cooldown - (now - last)
        self.creation_cooldowns[user_id] = now
        return True, 0

    # ---------------- DATABASE ----------------
    async def _init_db(self):
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
        print("[TempVoice] Database ready ✅")

    # ---------------- EVENTS ----------------
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if not member or member.bot:
            return

        # JOIN HANDLER
        if after.channel and after.channel.id in JOIN_CHANNELS:
            join_id = after.channel.id
            guild_id = member.guild.id
            self._ensure_structs(join_id, guild_id)
            self.creation_queues[join_id].append((member, after.channel))
            asyncio.create_task(self._queue_worker(join_id))

        # LEAVE HANDLER
        if before.channel and before.channel != after.channel:
            try:
                async with aiosqlite.connect(DB_PATH) as db:
                    async with db.execute("SELECT 1 FROM voiceChannel WHERE voiceID = ?", (before.channel.id,)) as c:
                        if await c.fetchone():
                            await self._schedule_cleanup(before.channel)
            except Exception as e:
                print(f"[TempVoice] cleanup schedule fail: {e}")

    # ---------------- QUEUE SYSTEM ----------------
    async def _queue_worker(self, join_id):
        async with self.queue_locks[join_id]:
            queue = self.creation_queues[join_id]
            while queue:
                member, channel = queue.popleft()
                try:
                    await self._process_creation(member, channel)
                except Exception:
                    print(f"[TempVoice] queue task error:\n{traceback.format_exc()}")
                await asyncio.sleep(1)  # small delay for safety

    # ---------------- CORE CREATION ----------------
    async def _process_creation(self, member, join_channel):
        ok, wait = self._cooldown_ok(member.id)
        if not ok:
            try:
                await member.move_to(None)
            except Exception:
                pass
            try:
                await member.send(
                    embed=discord.Embed(
                        description=f"⏳ Slow down — wait {wait:.1f}s before creating another channel.",
                        color=discord.Color.orange()
                    )
                )
            except Exception:
                pass
            return

        try:
            await self._cleanup_ghost(member.guild)
            await self._cleanup_empty(member.guild)
            await self._create_vc(member, join_channel)
        except Exception:
            print(f"[TempVoice] creation error:\n{traceback.format_exc()}")

    async def _create_vc(self, member, join_channel):
        guild = member.guild
        category = join_channel.category
        base_name, limit = JOIN_CHANNELS.get(join_channel.id, ("VC ", 0))

        used_nums = []
        channel_map = {}

        # Scan all existing VCs with same base name
        for vc in category.voice_channels:
            if vc.name.startswith(base_name):
                try:
                    n = int(vc.name[len(base_name):].split()[0])
                    used_nums.append(n)
                    channel_map[n] = vc
                except Exception:
                    pass

        # Find the lowest unused number
        num = 1
        while num in used_nums:
            num += 1

        name = f"{base_name}{num}"

        # Determine position
        try:
            if num == 1:
                # Place directly below generator
                position = join_channel.position
            else:
                # Find the previous channel (e.g., Duo 1 before creating Duo 2)
                prev_vc = channel_map.get(num - 1)
                if prev_vc:
                    position = prev_vc.position
                else:
                    # fallback — if previous VC somehow missing
                    position = join_channel.position + num
        except Exception:
            position = None

        # Create VC at correct spot
        vc = await guild.create_voice_channel(
            name=name,
            category=category,
            user_limit=limit or None,
            position=position,
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
                (guild.id, member.id, vc.id, name, limit)
            )
            await db.commit()

        # keep control panel same
        await self._send_panel(vc, member.id)
        print(f"[TempVoice] ✅ Created {name}")

    # ---------------- CLEANUP ----------------
    async def _schedule_cleanup(self, ch):
        guild_id = ch.guild.id
        self._ensure_structs(ch.id, guild_id)
        async with self.cleanup_locks[guild_id]:
            if ch.id in self.cleanup_tasks:
                return
            task = asyncio.create_task(self._cleanup_task(ch))
            self.cleanup_tasks[ch.id] = task

    async def _cleanup_task(self, ch):
        await asyncio.sleep(5)
        try:
            if ch.members:
                return
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute("SELECT 1 FROM voiceChannel WHERE voiceID = ?", (ch.id,)) as c:
                    if not await c.fetchone():
                        return
            await ch.delete(reason="Empty temp VC cleanup")
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("DELETE FROM voiceChannel WHERE voiceID = ?", (ch.id,))
                await db.commit()
            print(f"[TempVoice] 🧹 Cleaned {ch.name}")
        except Exception:
            print(f"[TempVoice] cleanup error:\n{traceback.format_exc()}")
        finally:
            self.cleanup_tasks.pop(ch.id, None)

    async def _cleanup_ghost(self, guild):
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT voiceID FROM voiceChannel WHERE guildID = ?", (guild.id,)) as c:
                rows = await c.fetchall()
            for (vid,) in rows:
                if not guild.get_channel(vid):
                    await db.execute("DELETE FROM voiceChannel WHERE voiceID = ?", (vid,))
            await db.commit()

    async def _cleanup_empty(self, guild):
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT voiceID FROM voiceChannel WHERE guildID = ?", (guild.id,)) as c:
                rows = await c.fetchall()
        for (vid,) in rows:
            ch = guild.get_channel(vid)
            if ch and not ch.members:
                await self._schedule_cleanup(ch)

    # ---------------- PANEL ----------------
    async def _send_panel(self, channel, owner_id):
        try:
            embed = discord.Embed(
                title="🎯 Voice Channel Controls", description=( "**Use the buttons below to manage your voice channel:**\n\n" "**🔒 Access Controls:**\n" "• Lock/Unlock - Control who can join\n" "• Hide/Unhide - Control visibility\n" "**👥 Member Management:**\n" "• Trust users - Allow specific users to join\n" "• Ban users - Block specific users\n" "• Kick users - Remove from VC\n\n" "**🔄 Advanced:**\n" "• Reset - Reset all settings\n" "• Transfer - Give ownership to someone else" ),
            
                color=discord.Color.orange()
            )
            embed.set_footer(text="Use `/panel` to access these controls anytime")
            await channel.send(embed=embed, view=BasicControlsView(self, channel.id, owner_id))
        except Exception:
            pass


    @app_commands.command(name="panel", description="Open your voice channel control panel")
    @app_commands.guild_only()
    async def panel(self, interaction: discord.Interaction):
        """Send the control panel interface to the user"""
        
        # Check if user is in a voice channel
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message(
                "❌ You must be in a voice channel to use this command!", 
                ephemeral=True
            )
            return
        
        voice_channel = interaction.user.voice.channel
        
        # Check if user is the owner of this voice channel
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT userID FROM voiceChannel WHERE voiceID = ?", 
                (voice_channel.id,)
            ) as cursor:
                data = await cursor.fetchone()
        
        if not data:
            await interaction.response.send_message(
                "❌ This voice channel is not managed by the bot or doesn't exist in our database!", 
                ephemeral=True
            )
            return
        
        owner_id = data[0]
        
        if interaction.user.id != owner_id:
            await interaction.response.send_message(
                "❌ You are not the owner of this voice channel!", 
                ephemeral=True
            )
            return
        
        # All conditions met - send the exact same interface as _send_panel
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
        
        view = BasicControlsView(self, voice_channel.id, owner_id)
        
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def setup(bot):
    await bot.add_cog(TempVoice(bot))
    print("[TempVoice] loaded with queue system ✅")
