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
    """Temp Voice with stability queue and organized channel ordering"""

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
                    vcType TEXT NOT NULL,  -- 'Duo', 'Trio', 'Squad', or 'Other'
                    channelNumber INTEGER NOT NULL,  -- The number for this type
                    locked BOOLEAN DEFAULT FALSE,
                    hidden BOOLEAN DEFAULT FALSE,
                    channelName TEXT DEFAULT '',
                    channelLimit INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # Create indexes for faster queries
            await db.execute("CREATE INDEX IF NOT EXISTS idx_guild_type ON voiceChannel(guildID, vcType)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_guild ON voiceChannel(guildID)")
            await db.commit()
        print("[TempVoice] Database ready ✅")

    # ---------------- EVENTS ----------------
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if not member or member.bot:
            return

        # Track when VC owners leave their channels
        if before.channel:
            # Check if this member owns the channel they're leaving
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute(
                    "SELECT 1 FROM voiceChannel WHERE voiceID = ? AND userID = ?",
                    (before.channel.id, member.id)
                ) as c:
                    if await c.fetchone():
                        # This is the owner leaving their own channel
                        self.owner_leave_times[before.channel.id] = time.time()
        
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
        
        # If owner rejoins their channel, remove the leave time
        if after.channel:
            if after.channel.id in self.owner_leave_times:
                # Check if this member owns the channel
                async with aiosqlite.connect(DB_PATH) as db:
                    async with db.execute(
                        "SELECT 1 FROM voiceChannel WHERE voiceID = ? AND userID = ?",
                        (after.channel.id, member.id)
                    ) as c:
                        if await c.fetchone():
                            # Owner rejoined, remove the leave time
                            self.owner_leave_times.pop(after.channel.id, None)

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
        
        # Get base name and limit for this generator type
        base_name, limit = JOIN_CHANNELS.get(join_channel.id, ("VC ", 0))
        
        # Clean base name for comparison and use
        clean_base = base_name.strip()
        
        # Determine VC type based on generator channel name - this NEVER changes
        vc_type = "Other"
        if clean_base.lower() == "duo":
            vc_type = "Duo"
        elif clean_base.lower() == "trio":
            vc_type = "Trio"
        elif clean_base.lower() == "squad":
            vc_type = "Squad"
        
        # Use a lock to prevent race conditions
        async with self._vc_creation_lock:
            # STEP 1: Find the next available number for this specific VC type
            async with aiosqlite.connect(DB_PATH) as db:
                # Get ALL channel numbers for this specific type
                async with db.execute(
                    "SELECT channelNumber FROM voiceChannel WHERE guildID = ? AND vcType = ?",
                    (guild.id, vc_type)
                ) as c:
                    rows = await c.fetchall()
                
                # Extract all used numbers for this type
                used_numbers = {row[0] for row in rows}
                
                # Find the lowest available number starting from 1
                channel_number = 1
                while channel_number in used_numbers:
                    channel_number += 1
            
            # Generate initial channel name
            name = f"{clean_base} {channel_number}"
            
            # STEP 2: Calculate position based on database ordering
            # We want: Generators → Duo 1,2,3 → Trio 1,2,3 → Squad 1,2,3 → Other
            async with aiosqlite.connect(DB_PATH) as db:
                # Get all bot channels in this guild, sorted by type then number
                async with db.execute(
                    """SELECT voiceID, vcType, channelNumber 
                    FROM voiceChannel 
                    WHERE guildID = ? 
                    ORDER BY 
                        CASE vcType 
                            WHEN 'Duo' THEN 1
                            WHEN 'Trio' THEN 2
                            WHEN 'Squad' THEN 3
                            ELSE 4
                        END,
                        channelNumber""",
                    (guild.id,)
                ) as c:
                    all_bot_channels = await c.fetchall()
            
            # Now figure out where our new channel should go
            position = None
            generator_vcs = [vc for vc in category.voice_channels if vc.id in JOIN_CHANNELS]
            
            # Count how many bot channels exist in this category
            bot_channels_in_category = []
            for voice_id, vc_type_db, ch_number in all_bot_channels:
                vc = guild.get_channel(voice_id)
                if vc and vc.category and vc.category.id == category.id:
                    bot_channels_in_category.append((vc, vc_type_db, ch_number))
            
            if not bot_channels_in_category:
                # First bot channel in this category
                if generator_vcs:
                    position = max(vc.position for vc in generator_vcs) + 1
                else:
                    position = 0
            else:
                # Find where this channel should go in the sorted list
                # Create a sorted list of what SHOULD exist
                target_order = []
                
                # Add all existing bot channels in their proper order
                for vc, vc_type_db, ch_number in bot_channels_in_category:
                    target_order.append((vc_type_db, ch_number, vc))
                
                # Add our new channel to the list
                target_order.append((vc_type, channel_number, None))
                
                # Sort the combined list
                def sort_key(item):
                    vc_type_key, ch_number, _ = item
                    type_order = {"Duo": 1, "Trio": 2, "Squad": 3, "Other": 4}
                    return (type_order.get(vc_type_key, 5), ch_number)
                
                target_order.sort(key=sort_key)
                
                # Find our new channel's index in the sorted list
                new_index = -1
                for i, (vc_type_key, ch_number, vc) in enumerate(target_order):
                    if vc_type_key == vc_type and ch_number == channel_number:
                        new_index = i
                        break
                
                if new_index == 0:
                    # Our channel should be first among bot channels
                    if generator_vcs:
                        position = max(vc.position for vc in generator_vcs) + 1
                    else:
                        position = 0
                else:
                    # Find the channel that should come before ours
                    prev_vc_type, prev_ch_number, prev_vc = target_order[new_index - 1]
                    if prev_vc:
                        position = prev_vc.position + 1
                    else:
                        # This shouldn't happen, but just in case
                        position = bot_channels_in_category[-1][0].position + 1
            
            # STEP 3: Create the actual channel
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

            # STEP 4: Store in database with the correct type and number
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    """INSERT OR REPLACE INTO voiceChannel 
                    (guildID, userID, voiceID, vcType, channelNumber, channelName, channelLimit) 
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (guild.id, member.id, vc.id, vc_type, channel_number, name, limit)
                )
                await db.commit()

            # STEP 5: Reorganize to fix any positioning issues
            await self._reorganize_bot_channels(category)
            
            await self._send_panel(vc, member.id)
            print(f"[TempVoice] ✅ Created {name} (Type: {vc_type}, Number: {channel_number}) at position {position}")

    async def _reorganize_bot_channels(self, category):
        """Reorganize bot-created channels based ONLY on database entries"""
        try:
            guild = category.guild
            
            # Get all bot channels from database for this guild, sorted properly
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute(
                    """SELECT voiceID, vcType, channelNumber 
                    FROM voiceChannel 
                    WHERE guildID = ? 
                    ORDER BY 
                        CASE vcType 
                            WHEN 'Duo' THEN 1
                            WHEN 'Trio' THEN 2
                            WHEN 'Squad' THEN 3
                            ELSE 4
                        END,
                        channelNumber""",
                    (guild.id,)
                ) as c:
                    all_bot_channels = await c.fetchall()
            
            # Filter to only channels that exist in this category
            channels_to_position = []
            for voice_id, vc_type, channel_number in all_bot_channels:
                vc = guild.get_channel(voice_id)
                if vc and vc.category and vc.category.id == category.id:
                    channels_to_position.append(vc)
            
            # If no bot channels, nothing to do
            if not channels_to_position:
                return
            
            # Get generator channels
            generator_vcs = [vc for vc in category.voice_channels if vc.id in JOIN_CHANNELS]
            
            # Calculate starting position
            start_position = 0
            if generator_vcs:
                # Sort generators by their current position
                generator_vcs.sort(key=lambda x: x.position)
                # Position bot channels after the last generator
                start_position = generator_vcs[-1].position + 1
            
            # Reposition channels in the order they appear in the sorted database list
            for idx, vc in enumerate(channels_to_position):
                target_pos = start_position + idx
                if vc.position != target_pos:
                    try:
                        await vc.edit(position=target_pos)
                        await asyncio.sleep(0.05)  # Small delay
                    except Exception as e:
                        print(f"[TempVoice] Error repositioning {vc.name}: {e}")
                            
        except Exception as e:
            print(f"[TempVoice] Error reorganizing bot channels: {e}")
            
    # ---------------- CLEANUP ----------------
    async def _schedule_cleanup(self, ch):
        # Only cleanup channels we created
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
            
            # Double-check this is our channel
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute("SELECT 1 FROM voiceChannel WHERE voiceID = ?", (ch.id,)) as c:
                    if not await c.fetchone():
                        return
            
            await ch.delete(reason="Empty temp VC cleanup")
            
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("DELETE FROM voiceChannel WHERE voiceID = ?", (ch.id,))
                await db.commit()
            
            print(f"[TempVoice] 🧹 Cleaned {ch.name}")
            
            # Remove from leave times tracking if it exists
            self.owner_leave_times.pop(ch.id, None)
            
            # Reorganize remaining bot channels
            if ch.category:
                await self._reorganize_bot_channels(ch.category)
                
        except Exception:
            print(f"[TempVoice] cleanup error:\n{traceback.format_exc()}")
        finally:
            self.cleanup_tasks.pop(ch.id, None)

    async def _cleanup_ghost(self, guild):
        """Clean up database entries for channels that no longer exist"""
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT voiceID FROM voiceChannel WHERE guildID = ?", (guild.id,)) as c:
                rows = await c.fetchall()
            for (vid,) in rows:
                if not guild.get_channel(vid):
                    await db.execute("DELETE FROM voiceChannel WHERE voiceID = ?", (vid,))
            await db.commit()

    async def _cleanup_empty(self, guild):
        """Schedule cleanup for empty channels we created"""
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
                title="🎯 Voice Channel Controls", 
                description=(
                    "**Use the buttons below to manage your voice channel:**\n"
                    "**Use `/panel` to access these controls anytime**\n\n"
                    "**🔒 Access Controls:**\n"
                    "• Lock/Unlock - Control who can join\n"
                    "• Hide/Unhide - Control visibility\n"
                    "**👥 Member Management:**\n"
                    "• Trust users - Allow specific users to join\n"
                    "• Ban users - Block specific users\n"
                    "• Kick users - Remove from VC\n\n"
                    "**🔄 Advanced:**\n"
                    "• Reset - Reset all settings\n"
                    "• Transfer - Give ownership to someone else\n\n"
                    "**📌 Need help dragging members?**\n"
                    "Use <#1418501302626816010> for drag requests!"
                ),
                color=discord.Color.orange()
            )
            embed.set_footer(text="Only the VC owner can use these controls")
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
                "• Transfer - Give ownership to someone else\n\n"
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
    print("[TempVoice] loaded with organized channel system ✅")