import discord
from discord.ext import commands
from discord import app_commands
import datetime
import json
import aiosqlite
from collections import defaultdict, Counter
from typing import Optional, Dict, Set, DefaultDict, Any

class RaidMode(commands.Cog):
    """Anti‑raid system with SQLite persistence and configurable invite tracking."""

    def __init__(self, bot: commands.Bot, db_path: str = "raidmode.db"):
        self.bot = bot
        self.db_path = db_path
        self.db: Optional[aiosqlite.Connection] = None

        # Per-guild configuration (cached, mirrored in DB)
        self.guild_configs: Dict[int, dict] = {}

        # Invite tracking – per raid session, never persisted
        self.invite_join_counts: DefaultDict[int, Counter] = defaultdict(Counter)
        self.cached_invites: Dict[int, Dict[str, discord.Invite]] = {}

        # Start background initialisation
        self.bot.loop.create_task(self._db_initialize())

    # --------------------------------------------------------------------------
    # DATABASE LAYER (aiosqlite)
    # --------------------------------------------------------------------------
    async def _db_initialize(self):
        """Set up database connection, table, and load all guild configs."""
        await self._db_connect()
        await self._db_create_table()
        await self._db_load_all()

    async def _db_connect(self):
        try:
            self.db = await aiosqlite.connect(self.db_path)
        except Exception as e:
            print(f"[RaidMode] Failed to open SQLite database: {e}")
            raise

    async def _db_create_table(self):
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS raidmode_config (
                guild_id INTEGER PRIMARY KEY,
                config TEXT NOT NULL
            )
        """)
        await self.db.commit()

    async def _db_load_all(self):
        """Load all guild configurations from DB into self.guild_configs."""
        async with self.db.execute("SELECT guild_id, config FROM raidmode_config") as cursor:
            rows = await cursor.fetchall()

        for guild_id, config_json in rows:
            gid = int(guild_id)
            config = json.loads(config_json)

            # Deserialise special types
            if 'muted_members' in config and isinstance(config['muted_members'], list):
                config['muted_members'] = set(config['muted_members'])
            if 'original_verification_level' in config:
                try:
                    config['original_verification_level'] = discord.VerificationLevel[
                        config['original_verification_level'].upper()
                    ]
                except (KeyError, AttributeError):
                    config['original_verification_level'] = discord.VerificationLevel.low
            if 'original_slowmodes' in config:
                config['original_slowmodes'] = {int(k): v for k, v in config['original_slowmodes'].items()}

            self.guild_configs[gid] = config

    async def _db_save_guild(self, guild_id: int):
        """Insert or update a guild's configuration in SQLite."""
        config = self.guild_configs.get(guild_id)
        if config is None:
            return

        serializable = self._db_make_serializable(config.copy())
        json_str = json.dumps(serializable)

        await self.db.execute("""
            INSERT INTO raidmode_config (guild_id, config) VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET config = excluded.config
        """, (guild_id, json_str))
        await self.db.commit()

    async def _db_delete_guild(self, guild_id: int):
        await self.db.execute("DELETE FROM raidmode_config WHERE guild_id = ?", (guild_id,))
        await self.db.commit()

    def _db_make_serializable(self, config: dict) -> dict:
        """Convert sets / enums to JSON‑compatible types."""
        if 'muted_members' in config and isinstance(config['muted_members'], set):
            config['muted_members'] = list(config['muted_members'])
        if 'original_verification_level' in config:
            level = config['original_verification_level']
            if isinstance(level, discord.VerificationLevel):
                config['original_verification_level'] = level.name.lower()
        return config

    # --------------------------------------------------------------------------
    # INVITE TRACKING & AUTO‑DELETION
    # --------------------------------------------------------------------------
    async def _invite_fetch_all(self, guild: discord.Guild) -> Dict[str, discord.Invite]:
        try:
            invites = await guild.invites()
            return {inv.code: inv for inv in invites}
        except discord.Forbidden:
            print(f"[RaidMode] No permission to fetch invites in {guild.name}")
            return {}
        except discord.HTTPException as e:
            print(f"[RaidMode] Failed to fetch invites in {guild.name}: {e}")
            return {}

    async def _invite_find_used(self, member: discord.Member) -> Optional[str]:
        """Return the invite code used by the member, if detectable."""
        guild = member.guild
        current = await self._invite_fetch_all(guild)
        old = self.cached_invites.get(guild.id, {})

        # 1. Look for an invite that increased its uses
        for code, old_inv in old.items():
            cur = current.get(code)
            if cur and cur.uses > old_inv.uses:
                return code

        # 2. Invite may have disappeared (max uses or manual deletion)
        disappeared = set(old.keys()) - set(current.keys())
        if disappeared:
            return next(iter(disappeared))

        return None

    async def _invite_handle_raid(self, guild: discord.Guild, code: str, member: discord.Member):
        """Warn and auto‑delete invites that exceed thresholds."""
        config = self.guild_configs.get(guild.id, {})
        if not config.get('active'):
            return
        if not config.get('invite_tracking_enabled', False):
            return

        # Load thresholds from config, fallback to defaults
        suspicious = config.get('invite_suspicious_threshold', 3)
        raid = config.get('invite_raid_threshold', 5)

        self.invite_join_counts[guild.id][code] += 1
        usage = self.invite_join_counts[guild.id][code]

        mod_channel = await self._mod_get_channel(guild)

        try:
            invite = await self.bot.fetch_invite(code)
        except discord.NotFound:
            invite = None

        if usage >= raid:
            if invite:
                try:
                    await invite.delete(reason="Raid mode: invite used for raiding")
                    delete_msg = f"✅ Deleted raid invite: `{code}`"
                except (discord.Forbidden, discord.NotFound):
                    delete_msg = f"❌ Could not delete invite `{code}` (no permission / already gone)"
            else:
                delete_msg = f"ℹ️ Invite `{code}` already deleted/expired"

            if mod_channel:
                await mod_channel.send(
                    f"🚨 **RAID DETECTED ON INVITE**\n"
                    f"Invite: `{code}`\n"
                    f"Uses during raid: {usage}\n"
                    f"Last joiner: {member.mention}\n"
                    f"{delete_msg}"
                )

        elif usage >= suspicious:
            if mod_channel:
                await mod_channel.send(
                    f"⚠️ **SUSPICIOUS INVITE ACTIVITY**\n"
                    f"Invite: `{code}`\n"
                    f"Uses during raid: {usage}\n"
                    f"Latest joiner: {member.mention}\n"
                    f"Will auto‑delete at {raid} uses"
                )

    # --------------------------------------------------------------------------
    # RAID CORE FUNCTIONALITY
    # --------------------------------------------------------------------------
    async def _mod_get_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        config = self.guild_configs.get(guild.id, {})
        mod_id = config.get('mod_channel_id')
        if mod_id:
            channel = guild.get_channel(mod_id)
            if channel and isinstance(channel, discord.TextChannel):
                return channel
        for name in ('mods', 'mod-log', 'moderators', 'staff'):
            ch = discord.utils.get(guild.text_channels, name=name)
            if ch:
                return ch
        return None

    async def _raid_store_slowmodes(self, guild: discord.Guild) -> Dict[int, int]:
        return {ch.id: ch.slowmode_delay for ch in guild.text_channels}

    async def _raid_apply_slowmode(self, guild: discord.Guild, delay: int):
        for ch in guild.text_channels:
            try:
                await ch.edit(slowmode_delay=delay)
            except discord.Forbidden:
                print(f"No permission to edit slowmode in {ch.name} ({guild.name})")
            except discord.HTTPException as e:
                print(f"Failed to set slowmode in {ch.name}: {e}")

    async def _raid_restore_slowmodes(self, guild: discord.Guild, original: Dict[int, int]):
        for ch in guild.text_channels:
            old = original.get(ch.id, 0)
            try:
                await ch.edit(slowmode_delay=old)
            except discord.Forbidden:
                print(f"No permission to restore slowmode in {ch.name}")
            except discord.HTTPException as e:
                print(f"Failed to restore slowmode in {ch.name}: {e}")

    async def _raid_mute_recent(self, guild: discord.Guild, hours: int) -> Set[int]:
        muted = set()
        now = discord.utils.utcnow()
        cutoff = now - datetime.timedelta(hours=hours)
        reason = "Raid mode: auto‑mute of recent joiners"
        duration = datetime.timedelta(days=28)

        for member in guild.members:
            if member.joined_at and member.joined_at > cutoff:
                try:
                    await member.timeout(duration, reason=reason)
                    muted.add(member.id)
                except discord.Forbidden:
                    print(f"Cannot timeout {member} – missing permissions.")
                except discord.HTTPException as e:
                    print(f"Failed to timeout {member}: {e}")
        return muted

    async def _raid_unmute_all(self, guild: discord.Guild, member_ids: Set[int]):
        reason = "Raid mode disabled – unmuting"
        for mid in member_ids:
                    await member.timeout(None, reason=reason)
                except (discord.Forbidden, discord.HTTPException) as e:
                    print(f"Failed to unmute {member}: {e}")

    async def _raid_mute_new(self, member: discord.Member):
        """Mute a member who just joined, and optionally track the invite used."""
        guild = member.guild
        config = self.guild_configs.get(guild.id)
        if not config or not config.get('active'):
            return False

        reason = "Raid mode active: auto‑mute on join"
        duration = datetime.timedelta(days=28)

        # 1. Track invite (if enabled)
        invite_code = None
        if config.get('invite_tracking_enabled', False):
            invite_code = await self._invite_find_used(member)

        # 2. Mute the member
        try:
            await member.timeout(duration, reason=reason)
        except discord.Forbidden:
            print(f"Cannot timeout {member} in {guild.name} – missing permissions.")
            return False
        except discord.HTTPException as e:
            print(f"Failed to timeout {member}: {e}")
            return False

        # 3. Add to muted set and persist
        if 'muted_members' in config:
            config['muted_members'].add(member.id)
            await self._db_save_guild(guild.id)

        # 4. Handle potential raid invite
        if invite_code:
            await self._invite_handle_raid(guild, invite_code, member)

        # 5. Send notification to mod channel
        mod_channel = await self._mod_get_channel(guild)
        if mod_channel:
            invite_text = f" (Invite: `{invite_code}`)" if invite_code else ""
            try:
                await mod_channel.send(
                    f"{member.mention}, you were muted due to raid mode being enabled. "
                    f"Please hold on until it ends.{invite_text}"
                )
            except discord.Forbidden:
                print(f"Cannot send mute notification to {mod_channel.name}")
            except discord.HTTPException as e:
                print(f"Failed to send mute notification: {e}")

        return True

    # --------------------------------------------------------------------------
    # LISTENERS
    # --------------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        config = self.guild_configs.get(member.guild.id)
        if config and config.get('active'):
            await self._raid_mute_new(member)

    @commands.Cog.listener()
    async def on_ready(self):
        """Refresh cached invites for all guilds with raid mode active."""
        for gid, config in self.guild_configs.items():
            if config.get('active'):
                guild = self.bot.get_guild(gid)
                if guild:
                    self.cached_invites[gid] = await self._invite_fetch_all(guild)

    # --------------------------------------------------------------------------
    # COMMAND GROUP: raidmode
    # --------------------------------------------------------------------------
    @commands.hybrid_group(name='raidmode', fallback='status')
    @commands.has_guild_permissions(administrator=True)
    @app_commands.guild_only()
    async def raidmode(self, ctx: commands.Context):
        """Show current raid mode status."""
        config = self.guild_configs.get(ctx.guild.id, {})
        active = config.get('active', False)

        if active and ctx.guild.id in self.invite_join_counts:
            top = dict(self.invite_join_counts[ctx.guild.id].most_common(5))
            if top:
                lines = "\n".join(f"  `{c}`: {n} joins" for c, n in top.items())
                await ctx.send(f"Raid mode is currently **{'ON' if active else 'OFF'}**.\n"
                               f"**Suspicious invites:**\n{lines}", ephemeral=True)
                return

        await ctx.send(f"Raid mode is currently **{'ON' if active else 'OFF'}**.", ephemeral=True)

    @raidmode.command(name='on')
    @commands.has_permissions(ban_members=True)
    @app_commands.describe(
        slowmode="Slowmode delay in seconds (default 10)",
        mute_hours="Mute members who joined in the last X hours (default 1)"
    )
    async def raidmode_on(
        self,
        ctx: commands.Context,
        slowmode: int = 10,
        mute_hours: int = 1
    ):
        """Enable raid mode: set slowmode, mute recent/joining members, track invites (if enabled)."""
        guild = ctx.guild
        gid = guild.id

        # Already active?
        if gid in self.guild_configs and self.guild_configs[gid].get('active'):
            await ctx.send("⚠️ Raid mode is already enabled.", ephemeral=True)
            return

        # Permission checks
        me = guild.me
        if not me.guild_permissions.manage_guild:
            await ctx.send("❌ I need `Manage Server` permission to change verification level and fetch invites.")
            return
        if not me.guild_permissions.moderate_members:
            await ctx.send("❌ I need `Timeout Members` permission to mute members.")
            return
        if not me.guild_permissions.manage_channels:
            await ctx.send("❌ I need `Manage Channels` permission to set slowmode.")
            return
        if not me.guild_permissions.mention_everyone:
            await ctx.send("❌ I need `Mention @everyone` permission to ping the mod channel.")
            return

        # Prepare guild config
        self.guild_configs.setdefault(gid, {})
        config = self.guild_configs[gid]

        # Set defaults for invite tracking if not present
        if 'invite_tracking_enabled' not in config:
            config['invite_tracking_enabled'] = False
        if 'invite_suspicious_threshold' not in config:
            config['invite_suspicious_threshold'] = 3
        if 'invite_raid_threshold' not in config:
            config['invite_raid_threshold'] = 5

        config['active'] = True

        # Snapshot invites (only if tracking is enabled)
        if config['invite_tracking_enabled']:
            self.cached_invites[gid] = await self._invite_fetch_all(guild)
            self.invite_join_counts[gid] = Counter()

        # 1. Store & set verification level to LOW
        try:
            config['original_verification_level'] = guild.verification_level
            await guild.edit(verification_level=discord.VerificationLevel.low)
        except Exception as e:
            await ctx.send(f"❌ Failed to change verification level: {e}")
            return

        # 2. Store & apply slowmode
        try:
            config['original_slowmodes'] = await self._raid_store_slowmodes(guild)
            await self._raid_apply_slowmode(guild, slowmode)
        except Exception as e:
            await ctx.send(f"❌ Failed to set slowmode: {e}")
            return

        # 3. Mute recent members
        try:
            muted_ids = await self._raid_mute_recent(guild, mute_hours)
            config['muted_members'] = muted_ids
        except Exception as e:
            await ctx.send(f"❌ Failed to mute recent members: {e}")
            return

        # 4. Alert mods channel
        mod_channel = await self._mod_get_channel(guild)
        if mod_channel:
            tracking_status = "**ON**" if config['invite_tracking_enabled'] else "**OFF**"
            try:
                await mod_channel.send(
                    f"@everyone **RAID MODE ENABLED**\n"
                    f"- Verification level: Low\n"
                    f"- Slowmode: {slowmode}s in all channels\n"
                    f"- Muted members who joined in the last {mute_hours} hour(s)\n"
                    f"- New members will be automatically muted on join\n"
                    f"- **Invite tracking:** {tracking_status}\n"
                    f"Enabled by {ctx.author.mention}"
                )
            except discord.Forbidden:
                await ctx.send("⚠️ Could not send message to mod channel (missing permissions).")
            except discord.HTTPException as e:
                await ctx.send(f"⚠️ Failed to send mod alert: {e}")
        else:
            await ctx.send("⚠️ No mod channel set. Use `/raidmode setmodchannel` to configure one.")

        await self._db_save_guild(gid)
        await ctx.send("✅ Raid mode enabled.")

    @raidmode.command(name='off')
    @commands.has_guild_permissions(administrator=True)
    async def raidmode_off(self, ctx: commands.Context):
        """Disable raid mode, restore settings, and unmute all members muted during the raid."""
        guild = ctx.guild
        gid = guild.id

        config = self.guild_configs.get(gid)
        if not config or not config.get('active'):
            await ctx.send("⚠️ Raid mode is not active.", ephemeral=True)
            return

        # 1. Restore verification level
        if 'original_verification_level' in config:
            try:
                await guild.edit(verification_level=config['original_verification_level'])
            except Exception as e:
                await ctx.send(f"❌ Failed to restore verification level: {e}")

        # 2. Restore slowmodes
        if 'original_slowmodes' in config:
            try:
                await self._raid_restore_slowmodes(guild, config['original_slowmodes'])
            except Exception as e:
                await ctx.send(f"❌ Failed to restore slowmodes: {e}")

        # 3. Unmute all members muted by the bot
        if 'muted_members' in config:
            try:
                await self._raid_unmute_all(guild, config['muted_members'])
            except Exception as e:
                await ctx.send(f"❌ Failed to unmute members: {e}")

        # 4. Clear active flag
        config['active'] = False

        # 5. Send raid summary (if invite tracking was used)
        mod_channel = await self._mod_get_channel(guild)
        if mod_channel and gid in self.invite_join_counts:
            try:
                counts = dict(self.invite_join_counts[gid].most_common())
                if counts:
                    summary = "\n".join(f"  • `{c}`: {n} joins" for c, n in counts.items())
                    await mod_channel.send(
                        f"**RAID MODE DISABLED**\n"
                        f"All settings restored. Disabled by {ctx.author.mention}.\n\n"
                        f"**📊 Raid Session Statistics**\n"
                        f"Total muted during raid: {len(config.get('muted_members', []))}\n"
                        f"**Suspicious invites detected:**\n{summary}"
                    )
                else:
                    await mod_channel.send(
                        f"**RAID MODE DISABLED**\n"
                        f"All settings restored. Disabled by {ctx.author.mention}.\n"
                        f"No suspicious invite activity detected."
                    )
            except:
                pass
        elif mod_channel:
            try:
                await mod_channel.send(
                    f"**RAID MODE DISABLED**\n"
                    f"All settings restored. Disabled by {ctx.author.mention}."
                )
            except:
                pass

        # 6. Clean up invite tracking data
        self.cached_invites.pop(gid, None)
        self.invite_join_counts.pop(gid, None)

        await self._db_save_guild(gid)
        await ctx.send("✅ Raid mode disabled.")

    @raidmode.command(name='setmodchannel')
    @commands.has_guild_permissions(administrator=True)
    @app_commands.describe(channel="Channel for raid alerts and join notifications")
    async def set_mod_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        gid = ctx.guild.id
        self.guild_configs.setdefault(gid, {})['mod_channel_id'] = channel.id
        await self._db_save_guild(gid)
        await ctx.send(f"✅ Mod alerts will be sent to {channel.mention}.", ephemeral=True)

    @raidmode.command(name='forget')
    @commands.has_guild_permissions(administrator=True)
    async def forget_data(self, ctx: commands.Context):
        """Delete this guild's stored configuration from the database."""
        gid = ctx.guild.id
        if gid in self.guild_configs:
            del self.guild_configs[gid]
            await self._db_delete_guild(gid)
            await ctx.send("✅ Stored guild configuration cleared.", ephemeral=True)
        else:
            await ctx.send("⚠️ No configuration found for this guild.", ephemeral=True)

    # --------------------------------------------------------------------------
    # INVITE TRACKING CONFIGURATION COMMANDS
    # --------------------------------------------------------------------------
    @raidmode.group(name='invitesettings', aliases=['invite'], fallback='show')
    @commands.has_guild_permissions(administrator=True)
    async def invite_settings(self, ctx: commands.Context):
        """Show current invite tracking settings."""
        gid = ctx.guild.id
        self.guild_configs.setdefault(gid, {})
        cfg = self.guild_configs[gid]

        enabled = cfg.get('invite_tracking_enabled', False)
        suspicious = cfg.get('invite_suspicious_threshold', 3)
        raid = cfg.get('invite_raid_threshold', 5)

        embed = discord.Embed(
            title="📨 Invite Tracking Settings",
            color=discord.Color.blue()
        )
        embed.add_field(name="Status", value="✅ **ON**" if enabled else "❌ **OFF**", inline=False)
        embed.add_field(name="Suspicious Threshold", value=f"{suspicious} joins (warning)", inline=True)
        embed.add_field(name="Raid Threshold", value=f"{raid} joins (auto‑delete)", inline=True)
        embed.set_footer(text="Use /raidmode invitesettings toggle / suspicious / raid to change.")

        await ctx.send(embed=embed, ephemeral=True)

    @invite_settings.command(name='toggle')
    @commands.has_guild_permissions(administrator=True)
    async def invite_toggle(self, ctx: commands.Context):
        """Enable or disable invite tracking for this server."""
        gid = ctx.guild.id
        self.guild_configs.setdefault(gid, {})
        cfg = self.guild_configs[gid]

        current = cfg.get('invite_tracking_enabled', False)
        cfg['invite_tracking_enabled'] = not current
        await self._db_save_guild(gid)

        status = "enabled" if not current else "disabled"
        await ctx.send(f"✅ Invite tracking **{status}**.", ephemeral=True)

    @invite_settings.command(name='suspicious')
    @commands.has_guild_permissions(administrator=True)
    @app_commands.describe(threshold="Number of joins before a warning is sent (default 3)")
    async def invite_suspicious(self, ctx: commands.Context, threshold: int):
        """Set the suspicious threshold (warning)."""
        if threshold < 2:
            await ctx.send("❌ Threshold must be at least 2.", ephemeral=True)
            return

        gid = ctx.guild.id
        self.guild_configs.setdefault(gid, {})
        cfg = self.guild_configs[gid]
        cfg['invite_suspicious_threshold'] = threshold
        await self._db_save_guild(gid)

        await ctx.send(f"✅ Suspicious threshold set to **{threshold}** joins.", ephemeral=True)

    @invite_settings.command(name='raid')
    @commands.has_guild_permissions(administrator=True)
    @app_commands.describe(threshold="Number of joins before the invite is auto‑deleted (default 5)")
    async def invite_raid(self, ctx: commands.Context, threshold: int):
        """Set the raid threshold (auto‑delete)."""
        if threshold < 2:
            await ctx.send("❌ Threshold must be at least 2.", ephemeral=True)
            return

        gid = ctx.guild.id
        self.guild_configs.setdefault(gid, {})
        cfg = self.guild_configs[gid]
        cfg['invite_raid_threshold'] = threshold
        await self._db_save_guild(gid)

        await ctx.send(f"✅ Raid threshold set to **{threshold}** joins.", ephemeral=True)

    @raidmode.command(name='invites')
    @commands.has_guild_permissions(administrator=True)
    async def list_invites(self, ctx: commands.Context):
        """Show all invites used during the current raid session."""
        gid = ctx.guild.id
        if gid not in self.invite_join_counts or not self.invite_join_counts[gid]:
            await ctx.send("No invites have been used during this raid session.", ephemeral=True)
            return

        counts = dict(self.invite_join_counts[gid].most_common())
        lines = "\n".join(f"`{c}`: {n} joins" for c, n in counts.items())

        embed = discord.Embed(
            title="📊 Raid Invite Usage",
            description=f"**Total unique invites used:** {len(counts)}",
            color=discord.Color.red()
        )
        embed.add_field(name="Invites", value=lines[:1024], inline=False)
        await ctx.send(embed=embed, ephemeral=True)

# --------------------------------------------------------------------------
# SETUP
# --------------------------------------------------------------------------
async def setup(bot: commands.Bot):
    await bot.add_cog(RaidMode(bot, db_path="data/raidmode.db"))