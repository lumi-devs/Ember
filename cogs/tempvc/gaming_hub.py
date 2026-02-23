# gaming_voice.py
import discord
from discord.ext import commands, tasks
import aiosqlite
import asyncio
from discord import app_commands
import traceback
from collections import deque
import time
import re

from .config import GAME_ROLES, GAMING_DB_PATH, GAMING_GENERATOR_ID, MAIN_GAMING_CHAT
from .views import BasicControlsView

class GameSelect(discord.ui.Select):
    """Game selection dropdown"""
    def __init__(self, cog, member, join_channel, message):
        self.cog = cog
        self.member = member
        self.join_channel = join_channel
        self.message = message
        
        # Create options from GAME_ROLES - Preserve order from config
        options = []
        for game_name, role_id in GAME_ROLES.items():
            role = member.guild.get_role(role_id)
            if role:  # Show all games, but indicate if user has role
                has_role = role in member.roles
                options.append(
                    discord.SelectOption(
                        label=game_name,
                        description=f"Create a {game_name} voice channel" + (" (✅ You have this role)" if has_role else " (❌ You need this role)"),
                        emoji="🎮" if has_role else "🔒",
                        value=game_name
                    )
                )
            else:
                # Role doesn't exist in server
                options.append(
                    discord.SelectOption(
                        label=game_name,
                        description="⚠️ Role not configured in server",
                        emoji="⚠️",
                        value=game_name,
                        default=False
                    )
                )
        
        if not options:
            options = [discord.SelectOption(
                label="No Games Configured",
                description="Contact server admin",
                emoji="⚠️"
            )]
        
        super().__init__(
            placeholder="Select a game to create a voice channel...",
            min_values=1,
            max_values=1,
            options=options
        )
    
    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.member.id:
            await interaction.response.send_message("❌ This selection menu isn't for you!", ephemeral=True)
            return
        
        selected_game = self.values[0]
        role_id = GAME_ROLES.get(selected_game)
        role = interaction.guild.get_role(role_id) if role_id else None
        
        # Check if user has the required role
        if role and role not in interaction.user.roles:
            await interaction.response.send_message(
                f"❌ You need the {role.mention} role to create a {selected_game} voice channel!",
                ephemeral=True
            )
            return
        elif role_id and not role:
            await interaction.response.send_message(
                f"❌ The {selected_game} role doesn't exist in this server!",
                ephemeral=True
            )
            return
        
        # Delete the selection message immediately
        try:
            await self.message.delete()
        except:
            pass
        
        # Acknowledge immediately to prevent timeout
        await interaction.response.defer(ephemeral=True)
        
        # Create channel instantly
        await self.cog.create_gaming_vc(self.member, self.join_channel, selected_game, role, interaction)


class GameSelectView(discord.ui.View):
    """View containing game selection dropdown"""
    def __init__(self, cog, member, join_channel, message):
        super().__init__(timeout=30)
        self.add_item(GameSelect(cog, member, join_channel, message))
    
    async def on_timeout(self):
        """Delete the message when timeout occurs"""
        try:
            message = self.children[0].message if self.children else None
            if message:
                await message.delete()
        except:
            pass


class GamingTempVoice(commands.Cog):
    """Gaming-specific temp voice channels with role-based access and 8-user limit"""

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
        
        # Create order mapping based on GAME_ROLES order
        self.game_order = {game: idx for idx, game in enumerate(GAME_ROLES.keys())}
        
        # CACHE FOR SPEED
        self._channel_cache = {}    # guild_id -> {voice_id: (game_name, channel_num)}
        self._last_cache_update = {} # guild_id -> timestamp
        self._cache_ttl = 5         # seconds
        
        # SINGLE GAMING GENERATOR CHANNEL ID
        self.GAMING_GENERATOR_ID = GAMING_GENERATOR_ID
        self.MAIN_GAMING_CHAT = MAIN_GAMING_CHAT
        
        # Drag requests channel ID
        self.DRAG_REQUESTS_CHANNEL_ID = 1418501302626816010

    async def cog_load(self):
        print("[GamingTempVoice] Cog loaded with game role integration ✅")
        print(f"[GamingTempVoice] Gaming Generator ID: {self.GAMING_GENERATOR_ID}")
        print(f"[GamingTempVoice] Game order: {list(GAME_ROLES.keys())}")
        await self._init_db()
        
        # Load existing gaming voice channels
        try:
            async with aiosqlite.connect(GAMING_DB_PATH) as db:
                async with db.execute("SELECT voiceID, userID, gameName, channelNumber FROM gamingVoiceChannel") as cursor:
                    async for row in cursor:
                        voice_id, user_id, game_name, channel_num = row
                        channel = self.bot.get_channel(voice_id)
                        if channel:
                            view = BasicControlsView(self, voice_id, user_id)
                            self.bot.add_view(view, message_id=None)
                            # Update cache
                            self._update_cache(channel.guild.id, voice_id, game_name, channel_num)
                            print(f"[GamingTempVoice] Registered persistent view for gaming channel {voice_id}")
        except Exception as e:
            print(f"[GamingTempVoice] Error loading persistent views: {e}")

    def cog_unload(self):
        self._heartbeat.cancel()

    @tasks.loop(minutes=10)
    async def _heartbeat(self):
        print(f"[GamingTempVoice] ❤️ heartbeat OK - {time.strftime('%X')}")

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
        """Get cached gaming channel data"""
        now = time.time()
        
        if (not force_refresh and 
            guild_id in self._channel_cache and 
            guild_id in self._last_cache_update and
            now - self._last_cache_update[guild_id] < self._cache_ttl):
            return self._channel_cache[guild_id].copy()
        
        async with aiosqlite.connect(GAMING_DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT voiceID, gameName, channelNumber FROM gamingVoiceChannel WHERE guildID = ?",
                (guild_id,)
            ) as c:
                rows = await c.fetchall()
        
        cache = {row['voiceID']: (row['gameName'], row['channelNumber']) for row in rows}
        self._channel_cache[guild_id] = cache
        self._last_cache_update[guild_id] = now
        return cache

    def _update_cache(self, guild_id, voice_id, game_name, channel_num):
        if guild_id in self._channel_cache:
            self._channel_cache[guild_id][voice_id] = (game_name, channel_num)

    def _remove_from_cache(self, guild_id, voice_id):
        if guild_id in self._channel_cache:
            self._channel_cache[guild_id].pop(voice_id, None)

    # ---------------- DATABASE ----------------
    async def _init_db(self):
        async with aiosqlite.connect(GAMING_DB_PATH) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS gamingVoiceChannel (
                    guildID INTEGER NOT NULL,
                    userID INTEGER NOT NULL,
                    voiceID INTEGER PRIMARY KEY,
                    gameName TEXT NOT NULL,
                    channelNumber INTEGER NOT NULL,
                    channelName TEXT NOT NULL,
                    channelLimit INTEGER DEFAULT 8,
                    locked BOOLEAN DEFAULT FALSE,
                    hidden BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_guild_game ON gamingVoiceChannel(guildID, gameName)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_guild ON gamingVoiceChannel(guildID)")
            await db.commit()
        print("[GamingTempVoice] Database initialized ✅")

    # ---------------- GAMING VC CREATION ----------------
    async def create_gaming_vc(self, member, join_channel, game_name, game_role, interaction):
        """Create a gaming voice channel with 8-user limit and role-based access - INSTANT"""
        
        # Cooldown check
        ok, wait = self._cooldown_ok(member.id)
        if not ok:
            try:
                await member.move_to(None)
                
                # Send warning message in generator channel that auto-deletes after 15 seconds
                warning_msg = await join_channel.send(
                    f"⚠️ {member.mention} You're creating channels too quickly! Please wait **{wait:.1f} seconds** before creating another gaming channel.",
                    delete_after=15
                )
                
                await interaction.followup.send(
                    f"⏳ Slow down — wait {wait:.1f}s before creating another gaming channel.",
                    ephemeral=True
                )
            except Exception as e:
                print(f"[GamingTempVoice] Error sending cooldown warning: {e}")
            return

        try:
            guild = member.guild
            category = join_channel.category
            
            # Run cleanups in background (don't wait for them)
            asyncio.create_task(self._cleanup_ghost(guild))
            asyncio.create_task(self._cleanup_empty(guild))
            
            async with self._vc_creation_lock:
                # Get existing gaming channels from cache
                db_map = await self._get_cached_channels(guild.id, force_refresh=True)
                
                # Generate channel number for this game
                game_channels = []
                for voice_id, (stored_game, stored_num) in db_map.items():
                    ch = guild.get_channel(voice_id)
                    if ch and ch.category_id == category.id and stored_game == game_name:
                        game_channels.append(stored_num)
                
                channel_number = 1
                while channel_number in game_channels:
                    channel_number += 1
                
                # Create channel name
                channel_name = f"{game_name} #{channel_number}"
                
                # Create the voice channel with 8-user limit - THIS IS THE ONLY BLOCKING OPERATION
                vc = await guild.create_voice_channel(
                    name=channel_name,
                    category=category,
                    user_limit=8,
                    reason=f"Gaming VC created by {member} for {game_name}"
                )
                
                # Set role-based permissions
                await vc.set_permissions(guild.default_role, connect=False, view_channel=True)
                if game_role:
                    await vc.set_permissions(game_role, connect=True, view_channel=True, speak=True, stream=True)
                await vc.set_permissions(member, connect=True, view_channel=True)
                
                # Move member immediately
                try:
                    await member.move_to(vc)
                except:
                    pass
                
                # Save to database (fire and forget)
                asyncio.create_task(self._save_to_db(guild.id, member.id, vc.id, game_name, channel_number, channel_name))
                
                # Update cache immediately
                self._update_cache(guild.id, vc.id, game_name, channel_number)
                
                # Send control panel (fire and forget)
                asyncio.create_task(self._send_gaming_panel(vc, member.id, game_name, game_role))
                
                # Send success message to interaction
                await interaction.followup.send(
                    f"✅ Created **{channel_name}**!",
                    ephemeral=True
                )
                
                # Send confirmation in generator channel that auto-deletes after 15 seconds
                await join_channel.send(
                    f"✅ {member.mention} Created **{channel_name}**!",
                    delete_after=15
                )
                
                print(f"[GamingTempVoice] Created {channel_name} for {member.name} ({game_name})")
                
                # Reorder channels in background (don't block)
                asyncio.create_task(self._reorder_gaming_channels(guild, category.id))
                
        except Exception as e:
            print(f"[GamingTempVoice] Creation error: {traceback.format_exc()}")
            try:
                await interaction.followup.send(
                    f"❌ Failed to create gaming channel: {str(e)}",
                    ephemeral=True
                )
            except:
                pass

    async def _save_to_db(self, guild_id, user_id, voice_id, game_name, channel_number, channel_name):
        """Save channel to database in background"""
        try:
            async with aiosqlite.connect(GAMING_DB_PATH) as db:
                await db.execute(
                    """INSERT INTO gamingVoiceChannel 
                    (guildID, userID, voiceID, gameName, channelNumber, channelName, channelLimit) 
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (guild_id, user_id, voice_id, game_name, channel_number, channel_name, 8)
                )
                await db.commit()
        except Exception as e:
            print(f"[GamingTempVoice] DB save error: {e}")

    async def _reorder_gaming_channels(self, guild, category_id):
        """Reorder gaming channels by game order and number"""
        try:
            await asyncio.sleep(1)  # Small delay to ensure channel is ready
            
            category = guild.get_channel(category_id)
            if not category:
                return
            
            # Get fresh cache
            db_map = await self._get_cached_channels(guild.id, force_refresh=True)
            
            # Get all voice channels in category
            all_vcs = category.voice_channels
            
            # Separate gaming channels from others
            gaming_vcs = []
            other_vcs = []
            generator_vc = None
            
            for ch in all_vcs:
                if ch.id == self.GAMING_GENERATOR_ID:
                    generator_vc = ch
                elif ch.id in db_map:
                    gaming_vcs.append(ch)
                else:
                    other_vcs.append(ch)
            
            # Sort gaming channels by game order and number
            def get_sort_key(ch):
                game_name, channel_num = db_map[ch.id]
                game_index = self.game_order.get(game_name, 999)
                return (game_index, channel_num)
            
            gaming_vcs.sort(key=get_sort_key)
            
            # Build final order: generator first, then gaming channels, then others
            final_order = []
            if generator_vc:
                final_order.append(generator_vc)
            final_order.extend(gaming_vcs)
            final_order.extend(other_vcs)
            
            # Apply positions
            for index, ch in enumerate(final_order):
                if ch.position != index:
                    try:
                        await ch.edit(position=index)
                        await asyncio.sleep(0.2)  # Small delay to avoid rate limits
                    except Exception as e:
                        print(f"[GamingTempVoice] Failed to reposition {ch.name}: {e}")
                        
        except Exception as e:
            print(f"[GamingTempVoice] Reordering error: {e}")

    async def _send_gaming_panel(self, channel, owner_id, game_name, game_role):
        """Send gaming-specific control panel"""
        try:
            embed = discord.Embed(
                title=f"🎮 {game_name} Voice Channel Controls",
                description=(
                    "**Use the buttons below to manage your gaming voice channel:**\n"
                    "• 👤 Name - Rename your channel\n"
                    "• 👥 Limit - Set user limit (currently 8)\n"
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
                    f"**🎯 Game Role:** {game_role.mention if game_role else 'None'}\n"
                ),
                color=discord.Color.orange()
            )
            embed.set_footer(text="Only the VC owner can use these controls")
            
            # Second embed with /panel and drag requests info
            drag_channel_mention = f"<#{self.DRAG_REQUESTS_CHANNEL_ID}>"
            info_embed = discord.Embed(
                description=(
                    "**📌 Need help dragging members?**\n"
                    f"Use {drag_channel_mention} for drag requests!\n\n"
                    "**🖥️ Control Panel**\n"
                    "Use `/panel` to access the controls anytime."
                ),
                color=discord.Color.orange()
            )
            
            # Create and store the view with persistent custom IDs
            view = BasicControlsView(self, channel.id, owner_id)
            
            # Send the message with the view
            await channel.send(embed=embed, view=view)
            await channel.send(embed=info_embed)
            
        except Exception as e:
            print(f"[GamingTempVoice] Error sending panel: {e}")

    # ---------------- CLEANUP ----------------
    async def _schedule_cleanup(self, ch):
        """Schedule cleanup for empty gaming channel"""
        if ch.members:
            return
            
        guild_id = ch.guild.id
        self._ensure_structs(ch.id, guild_id)
        
        async with self.cleanup_locks[guild_id]:
            if ch.id in self.cleanup_tasks:
                return
            
            guild_cache = await self._get_cached_channels(guild_id)
            if ch.id not in guild_cache:
                return
                
            task = asyncio.create_task(self._cleanup_task(ch))
            self.cleanup_tasks[ch.id] = task

    async def _cleanup_task(self, ch):
        await asyncio.sleep(15)
        
        try:
            if ch.members:
                return
            
            guild_cache = await self._get_cached_channels(ch.guild.id)
            if ch.id not in guild_cache:
                return
            
            # Get channel info for logging
            game_name, channel_num = guild_cache[ch.id]
            
            await ch.delete(reason="Empty gaming VC cleanup")
            
            # Delete from database
            async with aiosqlite.connect(GAMING_DB_PATH) as db:
                await db.execute("DELETE FROM gamingVoiceChannel WHERE voiceID = ?", (ch.id,))
                await db.commit()
            
            # Update cache
            self._remove_from_cache(ch.guild.id, ch.id)
            
            print(f"[GamingTempVoice] 🧹 Cleaned {ch.name} ({game_name})")
            
            # Remove from leave times tracking
            self.owner_leave_times.pop(ch.id, None)
                
        except Exception:
            print(f"[GamingTempVoice] cleanup error:\n{traceback.format_exc()}")
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
            async with aiosqlite.connect(GAMING_DB_PATH) as db:
                placeholders = ','.join('?' for _ in to_delete)
                await db.execute(f"DELETE FROM gamingVoiceChannel WHERE voiceID IN ({placeholders})", to_delete)
                await db.commit()
            
            for vid in to_delete:
                self._remove_from_cache(guild.id, vid)

    async def _cleanup_empty(self, guild):
        """Schedule cleanup for empty gaming channels"""
        guild_cache = await self._get_cached_channels(guild.id)
        
        for voice_id in guild_cache:
            ch = guild.get_channel(voice_id)
            if ch and not ch.members:
                await self._schedule_cleanup(ch)

    # ---------------- EVENTS ----------------
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if not member or member.bot:
            return

        # Debug print
        if after.channel and after.channel.id == self.GAMING_GENERATOR_ID:
            print(f"[GamingTempVoice] {member.name} joined gaming generator!")

        # Owner rejoining their gaming channel
        if after.channel and after.channel.id in self.owner_leave_times:
            guild_cache = await self._get_cached_channels(after.channel.guild.id)
            if after.channel.id in guild_cache:
                async with aiosqlite.connect(GAMING_DB_PATH) as db:
                    async with db.execute(
                        "SELECT 1 FROM gamingVoiceChannel WHERE voiceID = ? AND userID = ? LIMIT 1",
                        (after.channel.id, member.id)
                    ) as c:
                        if await c.fetchone():
                            self.owner_leave_times.pop(after.channel.id, None)

        # Track when owners leave
        if before.channel:
            guild_cache = await self._get_cached_channels(before.channel.guild.id)
            if before.channel.id in guild_cache:
                async with aiosqlite.connect(GAMING_DB_PATH) as db:
                    async with db.execute(
                        "SELECT 1 FROM gamingVoiceChannel WHERE voiceID = ? AND userID = ? LIMIT 1",
                        (before.channel.id, member.id)
                    ) as c:
                        if await c.fetchone():
                            self.owner_leave_times[before.channel.id] = time.time()
        
        # JOIN HANDLER - Show game selection menu
        if after.channel and after.channel.id == self.GAMING_GENERATOR_ID:
            join_id = after.channel.id
            guild_id = member.guild.id
            self._ensure_structs(join_id, guild_id)
            
            # Quick check if user already has a channel
            guild_cache = await self._get_cached_channels(guild_id)
            user_has_channel = False
            for voice_id, (game, _) in guild_cache.items():
                ch = member.guild.get_channel(voice_id)
                if ch and ch.category_id == after.channel.category_id:
                    async with aiosqlite.connect(GAMING_DB_PATH) as db:
                        async with db.execute(
                            "SELECT 1 FROM gamingVoiceChannel WHERE voiceID = ? AND userID = ? LIMIT 1",
                            (voice_id, member.id)
                        ) as c:
                            if await c.fetchone():
                                user_has_channel = True
                                break
            
            if user_has_channel:
                # Send warning that auto-deletes after 15 seconds
                await after.channel.send(
                    f"{member.mention} You already have a gaming channel!",
                    delete_after=15
                )
                await member.move_to(None)
                return
            
            # Create selection menu
            embed = discord.Embed(
                title=f"🎮 {member.display_name}'s Gaming Channel",
                description="Select a game to create a voice channel with **8 player limit**!",
                color=discord.Color.orange()
            )
            
            # Add game roles in config order
            roles_text = ""
            for game_name, role_id in GAME_ROLES.items():
                role = member.guild.get_role(role_id)
                if role:
                    has_role = "✅" if role in member.roles else "❌"
                    roles_text += f"{has_role} {game_name}: {role.mention}\n"
            
            if roles_text:
                embed.add_field(name="Your Game Roles", value=roles_text, inline=False)
            
            embed.set_footer(text="Expires in 30s")
            
            view = GameSelectView(self, member, after.channel, None)
            message = await after.channel.send(content=f"{member.mention}", embed=embed, view=view)
            
            # Update view with message reference
            for item in view.children:
                if isinstance(item, GameSelect):
                    item.message = message
            
            # Auto-delete after 30s
            async def delete_later():
                await asyncio.sleep(30)
                try:
                    await message.delete()
                except:
                    pass
            asyncio.create_task(delete_later())
                
        # LEAVE HANDLER
        if before.channel and before.channel != after.channel:
            guild_cache = await self._get_cached_channels(before.channel.guild.id)
            if before.channel.id in guild_cache:
                asyncio.create_task(self._schedule_cleanup(before.channel))




async def setup(bot):
    await bot.add_cog(GamingTempVoice(bot))
    print("[GamingTempVoice] loaded with 8-user limit and single generator ✅")