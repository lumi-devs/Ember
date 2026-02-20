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
import re

class TempVoice(commands.Cog):
    """Temp Voice with stability queue and proper positioning"""

    def __init__(self, bot):
        self.bot = bot
        self._vc_creation_lock = asyncio.Lock()
        self.creation_cooldowns = {}
        self.cleanup_tasks = {}
        self.creation_queues = {}   # join_channel.id -> deque()
        self.queue_locks = {}       # join_channel.id -> asyncio.Lock()
        self.cleanup_locks = {}     # guild.id -> asyncio.Lock()
        self.owner_leave_times = {}  # channel_id -> timestamp when owner left
        self._heartbeat.start()
        self.type_order = {"Duo": 1, "Trio": 2, "Squad": 3, "Other": 4}
        
        # CACHE FOR SPEED
        self._channel_cache = {}    # guild_id -> {voice_id: (vc_type, channel_num)}
        self._last_cache_update = {} # guild_id -> timestamp
        self._cache_ttl = 5         # seconds

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
            self.creation_queues[join_id] = deque(maxlen=100)
        if join_id not in self.queue_locks:
            self.queue_locks[join_id] = asyncio.Lock()
        if guild_id not in self.cleanup_locks:
            self.cleanup_locks[guild_id] = asyncio.Lock()

    def _cooldown_ok(self, user_id, cooldown=30):
        now = time.time()
        last = self.creation_cooldowns.get(user_id, 0)
        if now - last < cooldown:
            return False, cooldown - (now - last)
        self.creation_cooldowns[user_id] = now
        return True, 0

    # ---------------- CACHE SYSTEM ----------------
    async def _get_cached_channels(self, guild_id, force_refresh=False):
        """Get cached channel data with TTL"""
        now = time.time()
        
        # Check cache validity
        if (not force_refresh and 
            guild_id in self._channel_cache and 
            guild_id in self._last_cache_update and
            now - self._last_cache_update[guild_id] < self._cache_ttl):
            return self._channel_cache[guild_id].copy()
        
        # Refresh cache from DB
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT voiceID, vcType, channelNumber FROM voiceChannel WHERE guildID = ?",
                (guild_id,)
            ) as c:
                rows = await c.fetchall()
        
        cache = {row['voiceID']: (row['vcType'], row['channelNumber']) for row in rows}
        self._channel_cache[guild_id] = cache
        self._last_cache_update[guild_id] = now
        return cache

    def _update_cache(self, guild_id, voice_id, vc_type, channel_num):
        """Update cache when a new channel is created"""
        if guild_id in self._channel_cache:
            self._channel_cache[guild_id][voice_id] = (vc_type, channel_num)

    def _remove_from_cache(self, guild_id, voice_id):
        """Remove from cache when channel is deleted"""
        if guild_id in self._channel_cache:
            self._channel_cache[guild_id].pop(voice_id, None)

    # ---------------- DATABASE ----------------
    async def _init_db(self):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS voiceChannel (
                    guildID INTEGER NOT NULL,
                    userID INTEGER NOT NULL,
                    voiceID INTEGER PRIMARY KEY,
                    vcType TEXT NOT NULL CHECK (vcType IN ('Duo','Trio','Squad','Other')),
                    channelNumber INTEGER NOT NULL,
                    channelName TEXT NOT NULL,
                    channelLimit INTEGER,
                    locked BOOLEAN DEFAULT FALSE,
                    hidden BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_guild_type ON voiceChannel(guildID, vcType)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_guild ON voiceChannel(guildID)")
            await db.commit()
        print("[TempVoice] Database initialized & validated ✅")

    # ---------------- EVENTS ----------------
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if not member or member.bot:
            return

        # FAST TRACK: Owner rejoining their channel
        if after.channel and after.channel.id in self.owner_leave_times:
            # Quick cache check first
            if after.channel.guild.id in self._channel_cache:
                cached = self._channel_cache[after.channel.guild.id]
                if after.channel.id in cached:
                    # Verify ownership with fast query
                    async with aiosqlite.connect(DB_PATH) as db:
                        async with db.execute(
                            "SELECT 1 FROM voiceChannel WHERE voiceID = ? AND userID = ? LIMIT 1",
                            (after.channel.id, member.id)
                        ) as c:
                            if await c.fetchone():
                                self.owner_leave_times.pop(after.channel.id, None)

        # Track when VC owners leave their channels
        if before.channel:
            # Fast cache check
            guild_cache = await self._get_cached_channels(before.channel.guild.id)
            if before.channel.id in guild_cache:
                # Verify ownership
                async with aiosqlite.connect(DB_PATH) as db:
                    async with db.execute(
                        "SELECT 1 FROM voiceChannel WHERE voiceID = ? AND userID = ? LIMIT 1",
                        (before.channel.id, member.id)
                    ) as c:
                        if await c.fetchone():
                            self.owner_leave_times[before.channel.id] = time.time()
        
        # JOIN HANDLER - FAST PATH
        if after.channel and after.channel.id in JOIN_CHANNELS:
            join_id = after.channel.id
            guild_id = member.guild.id
            self._ensure_structs(join_id, guild_id)
            
            # Add to queue without blocking
            self.creation_queues[join_id].append((member, after.channel))
            
            # Start worker if not already processing
            if len(self.creation_queues[join_id]) == 1:
                asyncio.create_task(self._queue_worker(join_id))

        # LEAVE HANDLER - FAST PATH
        if before.channel and before.channel != after.channel:
            # Quick cache check
            guild_cache = await self._get_cached_channels(before.channel.guild.id)
            if before.channel.id in guild_cache:
                asyncio.create_task(self._schedule_cleanup(before.channel))
        
    # ---------------- QUEUE SYSTEM ----------------
    async def _queue_worker(self, join_id):
        async with self.queue_locks[join_id]:
            queue = self.creation_queues[join_id]
            while queue:
                member, channel = queue.popleft()
                try:
                    await self._process_creation(member, channel)
                    await asyncio.sleep(2.0)
                except Exception:
                    print(f"[TempVoice] queue task error:\n{traceback.format_exc()}")
                await asyncio.sleep(0.5)  # Reduced delay for speed

    # ---------------- CORE CREATION ----------------
    async def _process_creation(self, member, join_channel):
        ok, wait = self._cooldown_ok(member.id)
        if not ok:
            try:
                await member.move_to(None)
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
            # Run cleanups in parallel
            await asyncio.gather(
                self._cleanup_ghost(member.guild),
                self._cleanup_empty(member.guild)
            )
            await self._create_vc(member, join_channel)
        except Exception:
            print(f"[TempVoice] creation error:\n{traceback.format_exc()}")

    async def _create_vc(self, member, join_channel):
        guild = member.guild
        category = join_channel.category
        
        base_name, limit = JOIN_CHANNELS.get(join_channel.id, ("VC ", 0))
        clean_base = base_name.strip()
        
        # Determine VC type - FAST REGEX
        vc_type = "Other"
        if re.search(r'\bduo\b', clean_base, re.IGNORECASE): vc_type = "Duo"
        elif re.search(r'\btrio\b', clean_base, re.IGNORECASE): vc_type = "Trio"
        elif re.search(r'\bsquad\b', clean_base, re.IGNORECASE): vc_type = "Squad"
        
        async with self._vc_creation_lock:
            # Get cached channels instead of DB query
            db_map = await self._get_cached_channels(guild.id, force_refresh=True)
            
            # 2. Number Generation - optimized
            used_numbers = set()
            for voice_id, (type_name, num) in db_map.items():
                ch = guild.get_channel(voice_id)
                if ch and ch.category_id == category.id and type_name == vc_type:
                    used_numbers.add(num)
            
            channel_number = 1
            while channel_number in used_numbers:
                channel_number += 1
            
            name = f"{clean_base} {channel_number}"

            # 3. Create the Channel - owner gets manage_channels by default in voice channels
            vc = await guild.create_voice_channel(
                name=name,
                category=category,
                user_limit=limit or None,
                reason=f"Temp VC created by {member}"
            )

            # 4. OPTIMIZED REORGANIZING LOGIC - SINGLE API CALL
            # Get all channels once
            all_vcs = category.voice_channels
            
            # Pre-calculate groupings
            generators = []
            static_vcs = []
            managed_vcs = []
            
            for ch in all_vcs:
                if ch.id in JOIN_CHANNELS:
                    generators.append(ch)
                elif ch.id in db_map or ch.id == vc.id:
                    managed_vcs.append(ch)
                else:
                    static_vcs.append(ch)
            
            # Sort generators by position
            generators.sort(key=lambda x: x.position)
            
            # Prepare sorting keys for managed channels
            sort_keys = {}
            for ch in managed_vcs:
                if ch.id == vc.id:
                    sort_keys[ch.id] = (self.type_order.get(vc_type, 4), channel_number)
                else:
                    m_type, m_num = db_map[ch.id]
                    sort_keys[ch.id] = (self.type_order.get(m_type, 4), m_num)
            
            # Sort managed channels
            managed_vcs.sort(key=lambda x: sort_keys[x.id])
            
            # Re-assemble final order
            final_order = generators + managed_vcs + static_vcs
            
            # Prepare position mapping for batch update
            position_mapping = {}
            for index, ch in enumerate(final_order):
                if ch.position != index:
                    position_mapping[ch] = index
            
            # Bulk edit positions - SINGLE API CALL if Discord API supports it
            if position_mapping:
                try:
                    # Use Discord's position parameter with channel IDs for batch update
                    # This is more efficient than individual edits
                    payload = []
                    for ch, pos in position_mapping.items():
                        payload.append({"id": ch.id, "position": pos})
                    
                    await guild.edit_channel_positions(payload)
                except Exception as e:
                    print(f"[TempVoice] Batch positioning failed: {e}")
                    # Fallback to individual edits if batch fails
                    for ch, pos in position_mapping.items():
                        try:
                            await ch.edit(position=pos)
                        except Exception as inner_e:
                            print(f"[TempVoice] Individual positioning failed for {ch.name}: {inner_e}")

            # 5. FAST DB INSERT & CACHE UPDATE
            # No need to set permissions - Discord gives manage_channels to channel creator automatically
            
            try:
                await member.move_to(vc)
            except:
                pass

            # Fast DB insert
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    """INSERT OR REPLACE INTO voiceChannel 
                    (guildID, userID, voiceID, vcType, channelNumber, channelName, channelLimit) 
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (guild.id, member.id, vc.id, vc_type, channel_number, name, limit)
                )
                await db.commit()
            
            # Update cache immediately
            self._update_cache(guild.id, vc.id, vc_type, channel_number)

            await self._send_panel(vc, member.id)
                
    # ---------------- CLEANUP ----------------
    async def _schedule_cleanup(self, ch):
        # Fast check: if channel has members, skip
        if ch.members:
            return
            
        guild_id = ch.guild.id
        self._ensure_structs(ch.id, guild_id)
        
        async with self.cleanup_locks[guild_id]:
            if ch.id in self.cleanup_tasks:
                return
            
            # Fast cache check
            guild_cache = await self._get_cached_channels(guild_id)
            if ch.id not in guild_cache:
                return
                
            task = asyncio.create_task(self._cleanup_task(ch))
            self.cleanup_tasks[ch.id] = task

    async def _cleanup_task(self, ch):
        await asyncio.sleep(15)  # Reduced from 5 to 3 seconds
        
        try:
            if ch.members:
                return
            
            # Double-check with cache
            guild_cache = await self._get_cached_channels(ch.guild.id)
            if ch.id not in guild_cache:
                return
            
            await ch.delete(reason="Empty temp VC cleanup")
            
            # Fast DB delete
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("DELETE FROM voiceChannel WHERE voiceID = ?", (ch.id,))
                await db.commit()
            
            # Update cache
            self._remove_from_cache(ch.guild.id, ch.id)
            
            print(f"[TempVoice] 🧹 Cleaned {ch.name}")
            
            # Remove from leave times tracking
            self.owner_leave_times.pop(ch.id, None)
                
        except Exception:
            print(f"[TempVoice] cleanup error:\n{traceback.format_exc()}")
        finally:
            self.cleanup_tasks.pop(ch.id, None)

    async def _cleanup_ghost(self, guild):
        """Clean up database entries for channels that no longer exist"""
        guild_cache = await self._get_cached_channels(guild.id)
        to_delete = []
        
        for voice_id in guild_cache:
            if not guild.get_channel(voice_id):
                to_delete.append(voice_id)
        
        if to_delete:
            async with aiosqlite.connect(DB_PATH) as db:
                placeholders = ','.join('?' for _ in to_delete)
                await db.execute(f"DELETE FROM voiceChannel WHERE voiceID IN ({placeholders})", to_delete)
                await db.commit()
            
            # Update cache
            for vid in to_delete:
                self._remove_from_cache(guild.id, vid)

    async def _cleanup_empty(self, guild):
        """Schedule cleanup for empty channels we created"""
        guild_cache = await self._get_cached_channels(guild.id)
        
        for voice_id in guild_cache:
            ch = guild.get_channel(voice_id)
            if ch and not ch.members:
                await self._schedule_cleanup(ch)

    # ---------------- PANEL ----------------
    async def _send_panel(self, channel, owner_id):
        try:
            embed = discord.Embed(
                title="🎯 Voice Channel Controls", 
                description=(
                    "**Use the buttons below to manage your voice channel:**\n"
                    "• 👤 Name - Rename your channel\n"
                    "• 👥 Limit - Set user limit\n"
                    "• 🗑️ Delete - Delete the channel\n\n"
                    "• 🔒/🔓 Lock/Unlock - Control who can join\n"
                    "• 🕵️/👀 Hide/Unhide - Control visibility\n"
                    "• 👢 Kick - Remove users from VC\n\n"
                    "• ✅ Trust - Allow users to join\n"
                    "• ❌ Untrust - Remove user access\n"
                    "• 🚫 Block - Block users from VC\n"
                    "• ✅ Unblock - Unblock users\n\n"
                    "• 🔄 Transfer - Give ownership to someone in VC\n"
                    "• 🎯 Claim - Claim ownership when owner is gone\n\n"
                    "**📌 Need help dragging members?**\n"
                    "Use <#1418501302626816010> for drag requests!"
                ),
                color=discord.Color.orange()
            )
            embed.set_footer(text="Only the VC owner can use these controls")
            info_embed=discord.Embed(
                description=("Use `/panel` to access the controls anytime."),
                color=discord.Color.orange()
            )
            await channel.send(embed=embed, view=BasicControlsView(self, channel.id, owner_id))
            await channel.send(embed=info_embed)
        except Exception:
            pass

    @app_commands.command(name="panel", description="Open your voice channel control panel")
    @app_commands.guild_only()
    async def panel(self, interaction: discord.Interaction):
        """Send the control panel interface to the user"""
        
        # Fast path: check voice state first
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message(
                "❌ You must be in a voice channel to use this command!", 
                ephemeral=True
            )
            return
        
        voice_channel = interaction.user.voice.channel
        
        # Use cache first
        guild_cache = await self._get_cached_channels(interaction.guild_id)
        if voice_channel.id not in guild_cache:
            await interaction.response.send_message(
                "❌ This voice channel is not managed by the bot!", 
                ephemeral=True
            )
            return
        
        # Verify ownership with fast query
        data=None
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT userID FROM voiceChannel WHERE voiceID = ? LIMIT 1", (voice_channel.id,)) as cursor:  
                data = await cursor.fetchone()
                if not data:
                    await interaction.response.send_message(
                        "❌ Database error!", 
                        ephemeral=True
                    )
                    return
        
        owner_id = data[0]
        
        # All conditions met - send panel
        embed = discord.Embed(
            title="🎯 Voice Channel Controls", 
            description=(
                "**Use the buttons below to manage your voice channel:**\n\n"
                "**📝 Basic Controls (Row 1):**\n"
                "• 👤 Name - Rename your channel\n"
                "• 👥 Limit - Set user limit\n"
                "• 🗑️ Delete - Delete the channel\n\n"
                "**🔒 Privacy & Actions (Row 2):**\n"
                "• 🔒/🔓 Lock/Unlock - Control who can join\n"
                "• 🕵️/👀 Hide/Unhide - Control visibility\n"
                "• 👢 Kick - Remove users from VC\n\n"
                "**👥 Member Management (Row 3):**\n"
                "• ✅ Trust - Allow users to join\n"
                "• ❌ Untrust - Remove user access\n"
                "• 🚫 Block - Block users from VC\n"
                "• ✅ Unblock - Unblock users\n\n"
                "**🔄 Advanced (Row 4):**\n"
                "• 🔄 Transfer - Give ownership to someone in VC\n"
                "• 🎯 Claim - Claim ownership when owner is gone\n\n"
                "**📌 Need help dragging members?**\n"
                "Use <#1418501302626816010> for drag requests!"
            ),
            color=discord.Color.orange()
        )
        embed.set_footer(text="Only the VC owner can use these controls")
        
        view = BasicControlsView(self, voice_channel.id, owner_id)
        
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
async def setup(bot):
    await bot.add_cog(TempVoice(bot))
    print("[TempVoice] loaded with optimized performance ✅")