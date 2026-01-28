import discord
from discord.ext import commands, tasks
import asyncio
import aiosqlite
import time
from discord import app_commands
import traceback
from collections import deque
from config import GAMING_HUB_CHANNEL, GAME_ROLES, GAMING_DB_PATH
import re


class GamingHub(commands.Cog):
    """Gaming-specific voice channels with role-based permissions"""

    def __init__(self, bot):
        self.bot = bot
        self._vc_creation_lock = asyncio.Lock()
        self.creation_cooldowns = {}
        self.cleanup_tasks = {}
        self.owner_leave_times = {}
        self.active_selections = {}  # user_id -> (guild_id, hub_channel_id)
        self._heartbeat.start()

        # Cache for speed
        self._channel_cache = {}  # guild_id -> {voice_id: (game_name, owner_id)}
        self._last_cache_update = {}
        self._cache_ttl = 5

    async def cog_load(self):
        print("[GamingHub] 🎮 Cog loaded with role-based permissions ✅")
        await self._init_db()

    def cog_unload(self):
        self._heartbeat.cancel()

    # ---------------- HEARTBEAT ----------------
    @tasks.loop(minutes=10)
    async def _heartbeat(self):
        print(f"[GamingHub] ❤️ heartbeat OK - {time.strftime('%X')}")

    @_heartbeat.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()

    # ---------------- CACHE SYSTEM ----------------
    async def _get_cached_channels(self, guild_id, force_refresh=False):
        """Get cached gaming channel data with TTL"""
        now = time.time()

        if (not force_refresh and
                guild_id in self._channel_cache and
                guild_id in self._last_cache_update and
                now - self._last_cache_update[guild_id] < self._cache_ttl):
            return self._channel_cache[guild_id].copy()

        async with aiosqlite.connect(GAMING_DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                    "SELECT voiceID, gameName, userID FROM gamingChannel WHERE guildID = ?",
                    (guild_id,)
            ) as c:
                rows = await c.fetchall()

        cache = {row['voiceID']: (row['gameName'], row['userID']) for row in rows}
        self._channel_cache[guild_id] = cache
        self._last_cache_update[guild_id] = now
        return cache

    def _update_cache(self, guild_id, voice_id, game_name, owner_id):
        """Update cache when a new gaming channel is created"""
        if guild_id in self._channel_cache:
            self._channel_cache[guild_id][voice_id] = (game_name, owner_id)

    def _remove_from_cache(self, guild_id, voice_id):
        """Remove from cache when channel is deleted"""
        if guild_id in self._channel_cache:
            self._channel_cache[guild_id].pop(voice_id, None)

    # ---------------- DATABASE ----------------
    async def _init_db(self):
        async with aiosqlite.connect(GAMING_DB_PATH) as db:
            await db.execute("""
                             CREATE TABLE IF NOT EXISTS gamingChannel
                             (
                                 guildID
                                 INTEGER
                                 NOT
                                 NULL,
                                 userID
                                 INTEGER
                                 NOT
                                 NULL,
                                 voiceID
                                 INTEGER
                                 PRIMARY
                                 KEY,
                                 gameName
                                 TEXT
                                 NOT
                                 NULL,
                                 channelName
                                 TEXT
                                 NOT
                                 NULL,
                                 is_private
                                 BOOLEAN
                                 DEFAULT
                                 0,
                                 created_at
                                 TIMESTAMP
                                 DEFAULT
                                 CURRENT_TIMESTAMP
                             )
                             """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_guild_game ON gamingChannel(guildID, gameName)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_guild_user ON gamingChannel(guildID, userID)")
            await db.commit()
        print("[GamingHub] Gaming database initialized ✅")

    # ---------------- EVENTS ----------------
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if not member or member.bot:
            return

        # Track when gaming channel owners leave
        if before.channel:
            guild_cache = await self._get_cached_channels(before.channel.guild.id)
            if before.channel.id in guild_cache:
                game_name, owner_id = guild_cache[before.channel.id]
                if member.id == owner_id:
                    self.owner_leave_times[before.channel.id] = time.time()

        # JOIN GAMING HUB - Show game selection
        if after.channel and after.channel.id == GAMING_HUB_CHANNEL:
            asyncio.create_task(self._show_game_selection(member, after.channel))

        # LEAVE HANDLER for gaming channels
        if before.channel and before.channel != after.channel:
            guild_cache = await self._get_cached_channels(before.channel.guild.id)
            if before.channel.id in guild_cache:
                asyncio.create_task(self._schedule_cleanup(before.channel))

    # ---------------- GAME SELECTION ----------------
    async def _show_game_selection(self, member, hub_channel):
        """Show game selection dropdown when user joins hub"""
        await asyncio.sleep(0.5)  # Small delay to ensure user is fully joined

        try:
            # Check if user is already in a selection process
            if member.id in self.active_selections:
                return

            # Store active selection
            self.active_selections[member.id] = (member.guild.id, hub_channel.id)

            # Create selection view
            view = GameSelectionView(self, member.id)

            # Try to send as ephemeral message

            if channel.permissions_for(member.guild.me).send_messages:
                        await channel.send(
                            f"{member.mention} Please select your game:",
                            embed=discord.Embed(
                            title="🎮 Select Your Game",
                            description="Choose a game from the dropdown below to create a voice channel:",
                            color=discord.Color.purple()
                        ),
                        view=view,
                        delete_after=60
            )

            # Remove selection after timeout
            await asyncio.sleep(60)
            if member.id in self.active_selections:
                self.active_selections.pop(member.id, None)

        except Exception as e:
            print(f"[GamingHub] Error showing game selection: {e}")
            self.active_selections.pop(member.id, None)

    # ---------------- CHANNEL CREATION ----------------
    async def create_gaming_vc(self, member, hub_channel, game_name):
        """Create a gaming voice channel for the selected game"""
        ok, wait = self._cooldown_ok(member.id)
        if not ok:
            try:
                await member.move_to(None)
                await member.send(
                    embed=discord.Embed(
                        description=f"⏳ Please wait {wait:.1f}s before creating another gaming channel.",
                        color=discord.Color.orange()
                    )
                )
            except Exception:
                pass
            return False

        try:
            async with self._vc_creation_lock:
                # Clean up any empty gaming channels first
                await self._cleanup_empty(member.guild)

                # Get or create gaming category
                category = hub_channel.category
                if not category:
                    # Create a gaming category
                    category = await member.guild.create_category("🎮 Gaming Channels")

                # Get game role
                game_role_id = GAME_ROLES.get(game_name)
                game_role = member.guild.get_role(game_role_id) if game_role_id else None

                # Generate channel name
                channel_name = f"🎮 {game_name} - {member.display_name}"

                # Create the channel with role permissions
                overwrites = {
                    member.guild.default_role: discord.PermissionOverwrite(
                        view_channel=False,
                        connect=False
                    ),
                    member: discord.PermissionOverwrite(
                        manage_channels=True,
                        move_members=True,
                        mute_members=True,
                        deafen_members=True,
                        connect=True,
                        view_channel=True
                    )
                }

                # Add game role permissions
                if game_role:
                    overwrites[game_role] = discord.PermissionOverwrite(
                        view_channel=True,
                        connect=True,
                        speak=True
                    )

                # Create the voice channel
                vc = await member.guild.create_voice_channel(
                    name=channel_name,
                    category=category,
                    overwrites=overwrites,
                    reason=f"Gaming VC created by {member} for {game_name}"
                )

                # Position channel after hub channel
                try:
                    await vc.edit(position=hub_channel.position + 1)
                except Exception:
                    pass

                # Move member to new channel
                try:
                    await member.move_to(vc)
                except Exception:
                    pass

                # Store in database
                async with aiosqlite.connect(GAMING_DB_PATH) as db:
                    await db.execute(
                        """INSERT INTO gamingChannel
                               (guildID, userID, voiceID, gameName, channelName)
                           VALUES (?, ?, ?, ?, ?)""",
                        (member.guild.id, member.id, vc.id, game_name, channel_name)
                    )
                    await db.commit()

                # Update cache
                self._update_cache(member.guild.id, vc.id, game_name, member.id)

                # Send control panel
                await self._send_gaming_panel(vc, member.id, game_name)

                # Remove from active selections
                self.active_selections.pop(member.id, None)

                return True

        except Exception as e:
            print(f"[GamingHub] Error creating gaming VC: {e}")
            traceback.print_exc()
            return False

    # ---------------- CLEANUP ----------------
    async def _schedule_cleanup(self, ch):
        """Schedule cleanup for empty gaming channels"""
        if ch.members:
            return

        if ch.id in self.cleanup_tasks:
            return

        guild_cache = await self._get_cached_channels(ch.guild.id)
        if ch.id not in guild_cache:
            return

        task = asyncio.create_task(self._cleanup_task(ch))
        self.cleanup_tasks[ch.id] = task

    async def _cleanup_task(self, ch):
        await asyncio.sleep(10)  # Wait 10 seconds before cleanup

        try:
            if ch.members:
                return

            guild_cache = await self._get_cached_channels(ch.guild.id)
            if ch.id not in guild_cache:
                return

            await ch.delete(reason="Empty gaming channel cleanup")

            async with aiosqlite.connect(GAMING_DB_PATH) as db:
                await db.execute("DELETE FROM gamingChannel WHERE voiceID = ?", (ch.id,))
                await db.commit()

            self._remove_from_cache(ch.guild.id, ch.id)
            self.owner_leave_times.pop(ch.id, None)
            print(f"[GamingHub] 🧹 Cleaned gaming channel {ch.name}")

        except Exception:
            print(f"[GamingHub] Cleanup error:\n{traceback.format_exc()}")
        finally:
            self.cleanup_tasks.pop(ch.id, None)

    async def _cleanup_empty(self, guild):
        """Schedule cleanup for all empty gaming channels"""
        guild_cache = await self._get_cached_channels(guild.id)

        for voice_id in guild_cache:
            ch = guild.get_channel(voice_id)
            if ch and not ch.members:
                await self._schedule_cleanup(ch)

    # ---------------- CONTROL PANEL ----------------
    async def _send_gaming_panel(self, channel, owner_id, game_name):
        """Send gaming-specific control panel to the channel"""
        try:
            embed = discord.Embed(
                title=f"🎮 {game_name} Voice Channel",
                description=(
                    f"**Owner:** <@{owner_id}>\n"
                    f"**Game:** {game_name}\n\n"
                    "**⚡ Quick Controls:**\n"
                    "• **Lock/Unlock** - Toggle who can join\n"
                    "• **Friends Mode** - Switch between role-based and friends-only\n"
                    "• **Kick Members** - Remove users from your VC\n"
                    "• **Rename** - Change the channel name\n\n"
                    "**🔧 Advanced:**\n"
                    "• **Transfer** - Give ownership to someone else\n"
                    "• **Delete** - Remove this channel\n\n"
                    "Use `/gamingpanel` to access these controls anytime!"
                ),
                color=discord.Color.purple()
            )

            embed.set_footer(text="Only the channel owner can use these controls")

            view = GamingControlsView(self, channel.id, owner_id, game_name)
            await channel.send(embed=embed, view=view)
        except Exception as e:
            print(f"[GamingHub] Error sending panel: {e}")

    # ---------------- COOLDOWN CHECK ----------------
    def _cooldown_ok(self, user_id, cooldown=15):
        """Check if user is on cooldown for creating channels"""
        now = time.time()
        last = self.creation_cooldowns.get(user_id, 0)
        if now - last < cooldown:
            return False, cooldown - (now - last)
        self.creation_cooldowns[user_id] = now
        return True, 0

    # ---------------- COMMANDS ----------------
    @app_commands.command(name="gamingpanel", description="Open your gaming voice channel control panel")
    @app_commands.guild_only()
    async def gaming_panel(self, interaction: discord.Interaction):
        """Send the gaming control panel interface"""

        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message(
                "❌ You must be in a gaming voice channel to use this command!",
                ephemeral=True
            )
            return

        voice_channel = interaction.user.voice.channel

        # Check if it's a gaming channel
        guild_cache = await self._get_cached_channels(interaction.guild_id)
        if voice_channel.id not in guild_cache:
            await interaction.response.send_message(
                "❌ This is not a gaming voice channel!",
                ephemeral=True
            )
            return

        game_name, owner_id = guild_cache[voice_channel.id]

        if interaction.user.id != owner_id:
            await interaction.response.send_message(
                "❌ Only the channel owner can use the control panel!",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f"🎮 {game_name} Voice Channel",
            description=(
                f"**Owner:** <@{owner_id}>\n"
                f"**Game:** {game_name}\n\n"
                "**⚡ Quick Controls:**\n"
                "• **Lock/Unlock** - Toggle who can join\n"
                "• **Friends Mode** - Switch between role-based and friends-only\n"
                "• **Kick Members** - Remove users from your VC\n"
                "• **Rename** - Change the channel name\n\n"
                "**🔧 Advanced:**\n"
                "• **Transfer** - Give ownership to someone else\n"
                "• **Delete** - Remove this channel"
            ),
            color=discord.Color.purple()
        )

        view = GamingControlsView(self, voice_channel.id, owner_id, game_name)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# ---------------- GAME SELECTION VIEW ----------------
class GameSelectionView(discord.ui.View):
    """View for selecting a game when joining the gaming hub"""

    def __init__(self, cog, user_id):
        super().__init__(timeout=60)
        self.cog = cog
        self.user_id = user_id

        # Add dropdown with available games
        options = [
            discord.SelectOption(
                label=game_name,
                value=game_name,
                emoji="🎮"
            ) for game_name in GAME_ROLES.keys()
        ]

        self.add_item(GameDropdown(options))


class GameDropdown(discord.ui.Select):
    def __init__(self, options):
        super().__init__(
            placeholder="🎮 Select a game...",
            options=options,
            min_values=1,
            max_values=1
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        # Check if user is still in the selection process
        cog = self.view.cog
        if interaction.user.id not in cog.active_selections:
            await interaction.followup.send("❌ Selection timed out. Please rejoin the gaming hub.", ephemeral=True)
            return

        guild_id, hub_channel_id = cog.active_selections[interaction.user.id]
        guild = interaction.guild
        hub_channel = guild.get_channel(hub_channel_id)

        if not hub_channel:
            await interaction.followup.send("❌ Gaming hub not found!", ephemeral=True)
            return

        # Create the gaming VC
        game_name = self.values[0]
        success = await cog.create_gaming_vc(interaction.user, hub_channel, game_name)

        if success:
            await interaction.followup.send(f"✅ Created {game_name} voice channel!", ephemeral=True)
        else:
            await interaction.followup.send("❌ Failed to create voice channel. Please try again.", ephemeral=True)


# ---------------- GAMING CONTROLS VIEW ----------------
class GamingControlsView(discord.ui.View):
    """Control panel for gaming voice channels"""

    def __init__(self, cog, channel_id, owner_id, game_name):
        super().__init__(timeout=None)
        self.cog = cog
        self.channel_id = channel_id
        self.owner_id = owner_id
        self.game_name = game_name

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "❌ Only the channel owner can use these controls!",
                ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="🔒 Lock", style=discord.ButtonStyle.secondary, row=0)
    async def lock_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        channel = interaction.guild.get_channel(self.channel_id)
        if not channel:
            await interaction.followup.send("❌ Channel not found!", ephemeral=True)
            return

        # Check current state
        overwrite = channel.overwrites_for(interaction.guild.default_role)
        is_locked = overwrite.connect is False

        if is_locked:
            # Unlock: allow @everyone to connect
            await channel.set_permissions(
                interaction.guild.default_role,
                connect=True,
                view_channel=overwrite.view_channel
            )
            button.label = "🔒 Lock"
            await interaction.followup.send("🔓 Channel unlocked!", ephemeral=True)
        else:
            # Lock: deny @everyone from connecting
            await channel.set_permissions(
                interaction.guild.default_role,
                connect=False,
                view_channel=overwrite.view_channel
            )
            button.label = "🔓 Unlock"
            await interaction.followup.send("🔒 Channel locked!", ephemeral=True)

        # Update the view
        await interaction.edit_original_response(view=self)

    @discord.ui.button(label="👥 Friends", style=discord.ButtonStyle.secondary, row=0)
    async def toggle_friends(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        channel = interaction.guild.get_channel(self.channel_id)
        if not channel:
            await interaction.followup.send("❌ Channel not found!", ephemeral=True)
            return

        # Check if private (friends mode) from database
        async with aiosqlite.connect(GAMING_DB_PATH) as db:
            async with db.execute(
                    "SELECT is_private FROM gamingChannel WHERE voiceID = ?",
                    (self.channel_id,)
            ) as cursor:
                row = await cursor.fetchone()
                is_private = row[0] if row else 0

        game_role_id = GAME_ROLES.get(self.game_name)
        game_role = interaction.guild.get_role(game_role_id) if game_role_id else None

        if is_private:
            # Switch to role-based: remove all member overwrites, add game role
            if game_role:
                await channel.set_permissions(
                    game_role,
                    view_channel=True,
                    connect=True,
                    speak=True
                )

            # Remove individual member permissions (except owner)
            for target, overwrite in channel.overwrites.items():
                if isinstance(target, discord.Member) and target.id != self.owner_id:
                    await channel.set_permissions(target, overwrite=None)

            # Update database
            async with aiosqlite.connect(GAMING_DB_PATH) as db:
                await db.execute(
                    "UPDATE gamingChannel SET is_private = 0 WHERE voiceID = ?",
                    (self.channel_id,)
                )
                await db.commit()

            button.label = "👥 Friends"
            await interaction.followup.send("✅ Switched to role-based access!", ephemeral=True)
        else:
            # Switch to friends mode: remove game role, keep current members
            if game_role:
                await channel.set_permissions(game_role, overwrite=None)

            # Grant access to current members
            for member in channel.members:
                if member.id != self.owner_id:
                    await channel.set_permissions(
                        member,
                        connect=True,
                        view_channel=True
                    )

            # Update database
            async with aiosqlite.connect(GAMING_DB_PATH) as db:
                await db.execute(
                    "UPDATE gamingChannel SET is_private = 1 WHERE voiceID = ?",
                    (self.channel_id,)
                )
                await db.commit()

            button.label = "🎮 Role"
            await interaction.followup.send("✅ Switched to friends-only mode!", ephemeral=True)

        await interaction.edit_original_response(view=self)

    @discord.ui.button(label="👢 Kick", style=discord.ButtonStyle.secondary, row=0)
    async def kick_member(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        channel = interaction.guild.get_channel(self.channel_id)
        if not channel:
            await interaction.followup.send("❌ Channel not found!", ephemeral=True)
            return

        # Get members in channel (excluding owner)
        members_in_vc = [m for m in channel.members if m.id != self.owner_id]

        if not members_in_vc:
            await interaction.followup.send("❌ No one to kick!", ephemeral=True)
            return

        # Create dropdown for selecting members to kick
        options = [
            discord.SelectOption(
                label=member.display_name,
                value=str(member.id),
                description=f"Click to kick {member.display_name}"
            ) for member in members_in_vc[:25]
        ]

        view = discord.ui.View(timeout=30)
        select = discord.ui.Select(
            placeholder="Select members to kick...",
            options=options,
            min_values=1,
            max_values=min(5, len(options))
        )

        async def select_callback(select_interaction: discord.Interaction):
            if select_interaction.user.id != self.owner_id:
                await select_interaction.response.send_message("❌ Only the owner can kick members!", ephemeral=True)
                return

            await select_interaction.response.defer(ephemeral=True)
            kicked = []

            for user_id in select.values:
                member = interaction.guild.get_member(int(user_id))
                if member and member.voice and member.voice.channel == channel:
                    try:
                        await member.move_to(None)
                        kicked.append(member.display_name)
                    except Exception:
                        pass

            if kicked:
                await select_interaction.followup.send(f"✅ Kicked: {', '.join(kicked)}", ephemeral=True)
            else:
                await select_interaction.followup.send("❌ Failed to kick members", ephemeral=True)

        select.callback = select_callback
        view.add_item(select)

        await interaction.followup.send("Select members to kick:", view=view, ephemeral=True)

    @discord.ui.button(label="✏️ Rename", style=discord.ButtonStyle.secondary, row=1)
    async def rename_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(RenameGamingChannelModal(self.cog, self.channel_id))

    @discord.ui.button(label="🔄 Transfer", style=discord.ButtonStyle.secondary, row=1)
    async def transfer_ownership(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TransferGamingChannelModal(self.cog, self.channel_id))

    @discord.ui.button(label="🗑️ Delete", style=discord.ButtonStyle.danger, row=1)
    async def delete_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        channel = interaction.guild.get_channel(self.channel_id)
        if not channel:
            await interaction.followup.send("❌ Channel not found!", ephemeral=True)
            return

        # Create confirmation view
        confirm_view = discord.ui.View(timeout=30)

        async def confirm_callback(confirm_interaction: discord.Interaction):
            if confirm_interaction.user.id != self.owner_id:
                await confirm_interaction.response.send_message("❌ Only the owner can delete!", ephemeral=True)
                return

            await confirm_interaction.response.defer(ephemeral=True)

            try:
                # Delete from database
                async with aiosqlite.connect(GAMING_DB_PATH) as db:
                    await db.execute("DELETE FROM gamingChannel WHERE voiceID = ?", (self.channel_id,))
                    await db.commit()

                # Remove from cache
                self.cog._remove_from_cache(interaction.guild.id, self.channel_id)

                # Delete channel
                await channel.delete(reason="Gaming channel deleted by owner")

                await confirm_interaction.followup.send("✅ Channel deleted!", ephemeral=True)
            except Exception:
                await confirm_interaction.followup.send("❌ Failed to delete channel", ephemeral=True)

        async def cancel_callback(cancel_interaction: discord.Interaction):
            await cancel_interaction.response.edit_message(content="Deletion cancelled.", view=None)

        confirm_button = discord.ui.Button(label="✅ Confirm", style=discord.ButtonStyle.danger)
        confirm_button.callback = confirm_callback

        cancel_button = discord.ui.Button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
        cancel_button.callback = cancel_callback

        confirm_view.add_item(confirm_button)
        confirm_view.add_item(cancel_button)

        await interaction.followup.send(
            "⚠️ **Are you sure you want to delete this gaming channel?**\nThis cannot be undone!",
            view=confirm_view,
            ephemeral=True
        )


# ---------------- MODALS FOR GAMING CHANNELS ----------------
class RenameGamingChannelModal(discord.ui.Modal, title="Rename Gaming Channel"):
    def __init__(self, cog, channel_id):
        super().__init__()
        self.cog = cog
        self.channel_id = channel_id

        self.name_input = discord.ui.TextInput(
            label="New Channel Name",
            placeholder="Enter new name...",
            max_length=100,
            required=True
        )
        self.add_item(self.name_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        channel = interaction.guild.get_channel(self.channel_id)
        if not channel:
            await interaction.followup.send("❌ Channel not found!", ephemeral=True)
            return

        new_name = self.name_input.value.strip()
        if not new_name:
            await interaction.followup.send("❌ Please enter a valid name!", ephemeral=True)
            return

        old_name = channel.name
        await channel.edit(name=new_name)

        # Update database
        async with aiosqlite.connect(GAMING_DB_PATH) as db:
            await db.execute(
                "UPDATE gamingChannel SET channelName = ? WHERE voiceID = ?",
                (new_name, self.channel_id)
            )
            await db.commit()

        await interaction.followup.send(f"✏️ Renamed `{old_name}` to `{new_name}`", ephemeral=True)


class TransferGamingChannelModal(discord.ui.Modal, title="Transfer Gaming Channel"):
    def __init__(self, cog, channel_id):
        super().__init__()
        self.cog = cog
        self.channel_id = channel_id

        self.user_input = discord.ui.TextInput(
            label="User ID or Mention",
            placeholder="Enter user ID or paste mention...",
            required=True
        )
        self.add_item(self.user_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        try:
            # Extract user ID
            input_text = self.user_input.value.strip()
            if input_text.isdigit():
                user_id = int(input_text)
            else:
                user_id = int(input_text.replace('<@', '').replace('>', ''))

            new_owner = interaction.guild.get_member(user_id)
            if not new_owner:
                await interaction.followup.send("❌ User not found!", ephemeral=True)
                return

            if new_owner.bot:
                await interaction.followup.send("❌ Cannot transfer to a bot!", ephemeral=True)
                return

            channel = interaction.guild.get_channel(self.channel_id)
            if not channel:
                await interaction.followup.send("❌ Channel not found!", ephemeral=True)
                return

            # Update database
            async with aiosqlite.connect(GAMING_DB_PATH) as db:
                await db.execute(
                    "UPDATE gamingChannel SET userID = ? WHERE voiceID = ?",
                    (new_owner.id, self.channel_id)
                )
                await db.commit()

            # Update cache
            guild_cache = await self.cog._get_cached_channels(interaction.guild.id, force_refresh=True)
            if self.channel_id in guild_cache:
                game_name, _ = guild_cache[self.channel_id]
                self.cog._update_cache(interaction.guild.id, self.channel_id, game_name, new_owner.id)

            # Transfer permissions
            await channel.set_permissions(
                interaction.user,  # old owner
                manage_channels=False,
                move_members=False
            )

            await channel.set_permissions(
                new_owner,  # new owner
                manage_channels=True,
                move_members=True,
                mute_members=True,
                deafen_members=True
            )

            await interaction.followup.send(f"🔁 Ownership transferred to {new_owner.mention}!", ephemeral=True)

            # Send notification in channel
            try:
                embed = discord.Embed(
                    title="🔁 Ownership Transferred",
                    description=f"{interaction.user.mention} transferred channel ownership to {new_owner.mention}!",
                    color=discord.Color.green()
                )
                await channel.send(embed=embed)
            except Exception:
                pass

        except ValueError:
            await interaction.followup.send("❌ Please enter a valid user ID or mention!", ephemeral=True)
        except Exception as e:
            print(f"[GamingHub] Transfer error: {e}")
            await interaction.followup.send("❌ Failed to transfer ownership!", ephemeral=True)


async def setup(bot):
    await bot.add_cog(GamingHub(bot))
    print("[GamingHub] 🎮 Gaming Hub loaded successfully ✅")