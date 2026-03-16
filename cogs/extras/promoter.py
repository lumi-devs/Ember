import asyncio
import logging
import os
from datetime import datetime
from functools import partial

import aiosqlite
import discord
from discord.ext import commands, tasks

logger = logging.getLogger(__name__)


class PromoterRole(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db_path = "promoter_role.db"

        # Server-specific settings
        self.promoter_role_id     = 1440636027923333182
        self.your_server_invites  = [
            ".gg/aakhristation",
            "discord.gg/aakhristation",
            "aakhristation",
        ]
        self.server_tag      = "Raही"
        self.log_channel_id  = 1443119503285293098
        self.target_guild_id = int(os.environ.get("GUILD_ID", "0"))

        # BUG 3 FIX: cap the cache so it doesn't grow without bound.
        # Stores guild_id-user_id -> status string.
        self._status_cache: dict[str, str] = {}
        self._STATUS_CACHE_MAX = 5000

        self._db_ready = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def cog_load(self):
        """Called once when the cog is loaded — safe replacement for on_ready init."""
        await self._init_db()
        self._db_ready = True
        # BUG 4 FIX: start here (after bot is ready) instead of on_ready, with guard.
        if not self.fallback_check.is_running():
            self.fallback_check.start()
        logger.info("PromoterRole cog loaded")

    async def cog_unload(self):
        self.fallback_check.cancel()

    # ── Database ──────────────────────────────────────────────────────────────

    async def _init_db(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS promoter_logs (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id    INTEGER,
                    member_id   INTEGER,
                    member_name TEXT,
                    action      TEXT,
                    reason      TEXT,
                    content     TEXT,
                    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await db.commit()

    async def _log_promotion_action(
        self,
        guild_id:    int,
        member_id:   int,
        member_name: str,
        action:      str,
        reason:      str,
        content:     str = "",
    ):
        """BUG 2 FIX: fully async DB write."""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "INSERT INTO promoter_logs "
                    "(guild_id, member_id, member_name, action, reason, content) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (guild_id, member_id, member_name, action, reason, content),
                )
                await db.commit()
        except Exception as e:
            logger.error("DB log error: %s", e)

    # ── Presence / status helpers ─────────────────────────────────────────────

    def _get_status_content(self, member: discord.Member) -> str:
        """Return the member's custom status text (lowercased), or '' if none."""
        try:
            if member.status == discord.Status.offline:
                return "offline"
            for activity in member.activities:
                if isinstance(activity, discord.CustomActivity) and activity.name:
                    return activity.name.lower()
            return ""
        except Exception:
            return ""

    def _has_primary_guild_tag(self, member: discord.Member) -> bool:
        """Return True if the member's primary guild tag matches our target."""
        try:
            pg = getattr(member, "primary_guild", None)
            if pg is None:
                return False
            if pg.id != self.target_guild_id:
                return False
            return str(getattr(pg, "tag", "") or "") == str(self.server_tag)
        except Exception as e:
            logger.debug("primary_guild_tag check error for %s: %s", member, e)
            return False

    async def _has_promotion_content(self, member: discord.Member) -> bool:
        """Return True if the member is actively promoting (tag OR status invite)."""
        if self._has_primary_guild_tag(member):
            return True
        if member.status != discord.Status.offline:
            status = self._get_status_content(member)
            if status and status != "offline":
                for invite in self.your_server_invites:
                    if invite.lower() in status:
                        return True
        return False

    def _build_promotion_source_string(self, member: discord.Member) -> str:
        """Build a human-readable string describing current promotion sources."""
        sources = []
        if self._has_primary_guild_tag(member):
            sources.append(f"Primary Guild Tag: {self.server_tag}")
        status = self._get_status_content(member)
        if status and status not in ("", "offline"):
            sources.append(f"Status: {status}")
        return " | ".join(sources) if sources else "No promotion detected"

    # ── Cache helpers ─────────────────────────────────────────────────────────

    def _cache_key(self, member: discord.Member) -> str:
        return f"{member.guild.id}-{member.id}"

    def _update_status_cache(self, member: discord.Member) -> tuple[str, str]:
        """
        Update cache and return (old_value, new_value).
        BUG 3 FIX: evicts oldest entries when over the size limit.
        """
        key      = self._cache_key(member)
        new_val  = self._get_status_content(member)
        old_val  = self._status_cache.get(key, "")

        if len(self._status_cache) >= self._STATUS_CACHE_MAX and key not in self._status_cache:
            # Evict ~10 % of oldest entries (insertion-ordered dict)
            evict_count = self._STATUS_CACHE_MAX // 10
            for old_key in list(self._status_cache.keys())[:evict_count]:
                del self._status_cache[old_key]

        self._status_cache[key] = new_val
        return old_val, new_val

    # ── Core role management ──────────────────────────────────────────────────

    async def instant_member_check(self, member: discord.Member, reason: str):
        """Assign or remove the promoter role based on current promotion state."""
        try:
            role = member.guild.get_role(self.promoter_role_id)
            if not role:
                return

            has_promotion  = await self._has_promotion_content(member)
            currently_has  = role in member.roles

            if has_promotion and not currently_has:
                promo_content = self._build_promotion_source_string(member)
                await member.add_roles(role, reason=f"Promotion detected: {reason}")
                await self._log_promotion_action(
                    member.guild.id, member.id, str(member),
                    "ROLE_ASSIGNED", reason, promo_content,
                )
                await self._send_promotion_log(member, "assigned", promo_content)

            elif not has_promotion and currently_has:
                # BUG 5 FIX: use cached status as "what they had" before removal.
                cached_prev = self._status_cache.get(self._cache_key(member), "")
                prev_content = cached_prev if cached_prev else "Status/tag removed"
                await member.remove_roles(role, reason=f"Promotion removed: {reason}")
                await self._log_promotion_action(
                    member.guild.id, member.id, str(member),
                    "ROLE_REMOVED", reason, prev_content,
                )
                await self._send_promotion_log(member, "removed", prev_content)

        except discord.Forbidden:
            logger.warning("No permission to manage roles for %s", member.display_name)
        except Exception as e:
            logger.error("instant_member_check error for %s: %s", member.display_name, e)

    # ── Event listeners ───────────────────────────────────────────────────────

    # BUG 1 FIX: on_ready removed. Startup now happens in cog_load (called once,
    # not re-fired on reconnects). on_ready is kept only for the log line.
    @commands.Cog.listener()
    async def on_ready(self):
        logger.info(
            "PromoterRole ready | monitoring tag '%s' | guild %s",
            self.server_tag, self.target_guild_id,
        )

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member):
        if after.bot:
            return
        old_val, new_val = self._update_status_cache(after)
        if old_val != new_val:
            await self.instant_member_check(after, "Status changed")

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if after.bot:
            return
        changes = []
        if self._has_primary_guild_tag(before) != self._has_primary_guild_tag(after):
            tag_added = self._has_primary_guild_tag(after)
            changes.append(f"Primary tag {'added' if tag_added else 'removed'}")
        if before.global_name != after.global_name:
            changes.append("Global name changed")
        if changes:
            await self.instant_member_check(after, f"Member update: {', '.join(changes)}")

    @commands.Cog.listener()
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild):
        """
        Recheck all members when the guild's clan tag changes.
        BUG 10 FIX: runs the bulk check as a background task so the listener
        returns immediately, and spaces role operations to avoid rate limits.
        """
        before_tag = getattr(before, "tag", None)
        after_tag  = getattr(after,  "tag", None)
        if before_tag == after_tag:
            return

        logger.info("Guild tag changed: '%s' -> '%s'", before_tag, after_tag)
        if after_tag == self.server_tag or before_tag == self.server_tag:
            asyncio.create_task(
                self._bulk_check_guild(after, f"Server tag changed to {after_tag}")
            )

    # ── Background task ───────────────────────────────────────────────────────

    # BUG 9 FIX: 5-minute interval instead of 30 seconds to avoid hammering
    # Discord's rate limits on large servers.
    @tasks.loop(minutes=5)
    async def fallback_check(self):
        """Periodic sanity check — repairs any members whose role is out of sync."""
        for guild in self.bot.guilds:
            role = guild.get_role(self.promoter_role_id)
            if not role:
                continue
            for member in guild.members:
                if member.bot:
                    continue
                # Fast pre-filter: only call the heavier async check when the
                # cheap tag check disagrees with the current role state.
                has_tag  = self._has_primary_guild_tag(member)
                has_role = role in member.roles
                if has_tag != has_role:
                    full = await self._has_promotion_content(member)
                    if full != has_role:
                        await self.instant_member_check(member, "5m fallback")
                        await asyncio.sleep(0.5)  # breathe between role operations

    # BUG 4 FIX: wait_until_ready guard on the loop.
    @fallback_check.before_loop
    async def before_fallback_check(self):
        await self.bot.wait_until_ready()

    # ── Internal bulk helper ──────────────────────────────────────────────────

    async def _bulk_check_guild(self, guild: discord.Guild, reason: str):
        """Check all non-bot members with small delays to stay under rate limits."""
        logger.info("Bulk checking guild '%s' — reason: %s", guild.name, reason)
        for member in guild.members:
            if not member.bot:
                await self.instant_member_check(member, reason)
                await asyncio.sleep(0.3)

    # ── Logging embed ─────────────────────────────────────────────────────────

    async def _send_promotion_log(
        self, member: discord.Member, action_type: str, content: str = ""
    ):
        # BUG 8 FIX: log errors rather than silently swallowing them.
        try:
            channel = member.guild.get_channel(self.log_channel_id)
            if not channel:
                logger.warning("Log channel %s not found", self.log_channel_id)
                return

            if action_type == "assigned":
                embed = discord.Embed(
                    description=(
                        f"**:fish_cake: THANK YOU!!!!**\n\n"
                        f"**{member.mention}** is promoting our server! "
                        f"<:ehm:1390067566503788625>\n\n"
                        f"**__YOU JUST RECEIVED SOME PERKS!!!__**"
                    ),
                    color=0x00FF00,
                    timestamp=datetime.now(),
                )
            else:
                embed = discord.Embed(
                    description=(
                        f"**:fish_cake: GOODBYE :(**\n\n"
                        f"**{member.mention}** stopped promoting us :broken_heart:"
                    ),
                    color=0xFF9900,
                    timestamp=datetime.now(),
                )

            if content and content != "No promotion detected":
                embed.add_field(name="Promotion Source", value=f"```{content}```", inline=False)
            embed.set_thumbnail(url=member.display_avatar.url)
            await channel.send(embed=embed)

        except discord.Forbidden:
            logger.warning("No permission to send to log channel %s", self.log_channel_id)
        except Exception as e:
            logger.error("send_promotion_log error: %s", e)

    # ── Commands ──────────────────────────────────────────────────────────────

    @commands.command(name="pgtag")
    async def pgtag(self, ctx, member: discord.Member = None):
        """Check a member's primary guild tag."""
        target = member or ctx.author
        try:
            pg = getattr(target, "primary_guild", None)
            if pg is None:
                return await ctx.reply("❌ This user has no primary guild set.")
            tag = getattr(pg, "tag", None)
            if tag is None:
                return await ctx.reply("⚠️ Primary guild set but no tag.")

            would_promote = (str(tag) == str(self.server_tag) and pg.id == self.target_guild_id)
            embed = discord.Embed(title="🏷️ Primary Guild Tag", color=0x7289DA)
            embed.add_field(name="Member",          value=target.mention,          inline=True)
            embed.add_field(name="Tag",             value=f"**{tag}**",             inline=True)
            embed.add_field(name="Guild ID",        value=f"**{pg.id}**",          inline=True)
            embed.add_field(name="Target Guild ID", value=f"**{self.target_guild_id}**", inline=True)
            embed.add_field(
                name="Promoter Status",
                value="✅ Would Get Role" if would_promote else "❌ No Role",
                inline=True,
            )
            await ctx.send(embed=embed)
        except Exception as e:
            logger.error("pgtag error: %s", e)
            await ctx.reply(f"❌ Error: {e}")

    @commands.command(name="promoter_stats")
    @commands.has_permissions(manage_roles=True)
    async def promoter_stats(self, ctx):
        """Show promoter system statistics."""
        role = ctx.guild.get_role(self.promoter_role_id)
        total_promoters  = len([m for m in ctx.guild.members if role in m.roles and not m.bot]) if role else 0
        members_with_tag = sum(1 for m in ctx.guild.members if not m.bot and self._has_primary_guild_tag(m))

        embed = discord.Embed(title="🤖 Promoter System Stats", color=0x7289DA)
        embed.add_field(name="👥 Promoters",       value=total_promoters,           inline=True)
        embed.add_field(name="🏷️ Primary Tags",    value=members_with_tag,          inline=True)
        embed.add_field(name="🔄 Check Interval",  value="5 minutes",               inline=True)
        embed.add_field(name="🎯 Target Guild ID", value=self.target_guild_id,      inline=True)
        embed.add_field(name="💾 Status Cache",    value=len(self._status_cache),   inline=True)
        await ctx.send(embed=embed)

    @commands.command(name="force_check")
    @commands.has_permissions(manage_roles=True)
    async def force_check(self, ctx, target: discord.Member = None):
        """Force an instant check on one or all members."""
        # BUG 7 FIX: renamed loop variable from 'member' to 'guild_member'
        # so it no longer shadows the 'target' parameter.
        if target:
            await self.instant_member_check(target, "Manual force check")
            await ctx.send(f"✅ Checked {target.mention}")
        else:
            msg = await ctx.send("⚡ Checking all members...")
            checked = 0
            for guild_member in ctx.guild.members:
                if not guild_member.bot:
                    await self.instant_member_check(guild_member, "Manual bulk check")
                    checked += 1
            await msg.edit(content=f"✅ Checked {checked} members")

    @commands.command(name="debug_promoter")
    @commands.has_permissions(manage_roles=True)
    async def debug_promoter(self, ctx, member: discord.Member = None):
        """Show full debug info for a member's promoter state."""
        target = member or ctx.author
        pg        = getattr(target, "primary_guild", None)
        tag_value = str(pg.tag) if pg and getattr(pg, "tag", None) else "None"
        guild_id  = pg.id if pg else "None"  

        has_primary_tag = self._has_primary_guild_tag(target)
        has_promotion   = await self._has_promotion_content(target)
        role            = ctx.guild.get_role(self.promoter_role_id)
        has_role        = role in target.roles if role else False

        embed = discord.Embed(title=f"🐛 Debug — {target.display_name}", color=0xFF9900)
        embed.add_field(name="🆔 ID",             value=target.id,                              inline=True)
        embed.add_field(name="🌐 Status",          value=str(target.status).title(),             inline=True)
        embed.add_field(name="🎯 Primary Tag",     value="✅" if has_primary_tag else "❌",       inline=True)
        embed.add_field(name="🏷️ Tag Value",      value=tag_value,                              inline=True)
        embed.add_field(name="🏛️ Guild ID",       value=guild_id,                               inline=True)
        embed.add_field(name="🎯 Target Guild",   value=self.target_guild_id,                   inline=True)
        embed.add_field(name="📢 Promoting",       value="✅" if has_promotion else "❌",         inline=True)
        embed.add_field(name="🎪 Has Role",        value="✅" if has_role else "❌",              inline=True)
        embed.add_field(name="💾 Cached Status",   value=self._status_cache.get(self._cache_key(target), "—"), inline=True)
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(PromoterRole(bot))