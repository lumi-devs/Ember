# cogs/promoter/promoter.py  —  Components V2 with prefix commands
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from typing import Optional

import aiosqlite
import discord
from discord import ui
from discord.ext import commands, tasks

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# PERSISTENT BUTTONS
# ──────────────────────────────────────────────────────────────────────────────

class PromoterInfoButton(ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="How to Promote",
            style=discord.ButtonStyle.primary,
            custom_id="promoter_info_button",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        # Create thumbnail
        thumbnail_component = ui.Thumbnail(
            media="https://cdn.discordapp.com/emojis/1390067566503788625.png",  # Your emoji/image URL
            description="Promoter role info"
        )
        
        # Create text content
        text_content = (
            "Get the **Promoter** role by helping us grow!\n\n"
            "**Ways to promote:**\n"
            "Either of these works:\n"
            "1️⃣ Set your **Primary Guild Tag** to `Raही`\n"
            "2️⃣ Put one of these in your custom status:\n"
            "   • `.gg/aakhristation`\n"
            "   • `discord.gg/aakhristation`\n"
            "   • `aakhristation`\n\n"
            "✨ **Perks:**\n"
            "• Special role and recognition\n"
            "• Community appreciation"
        )
        
        # Combine all text into one TextDisplay
        combined_text = (
            "## 🌟 How to Get the Promoter Role\n\n"
            f"{text_content}\n\n"
            f"-# Requested by {interaction.user.display_name} • {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        
        # Create section with thumbnail
        section = ui.Section(
            ui.TextDisplay(combined_text),
            accessory=thumbnail_component
        )
        
        view = ui.LayoutView()
        view.add_item(ui.Container(section, accent_color=discord.Color.gold()))
        
        await interaction.response.send_message(view=view, ephemeral=True)


class ForceCheckButton(ui.Button):
    def __init__(self, target_id: Optional[int] = None) -> None:
        super().__init__(
            label="Force Check All" if not target_id else "Force Check",
            style=discord.ButtonStyle.danger,
            custom_id=f"promoter_force_check_{target_id if target_id else 'all'}",
        )
        self.target_id = target_id

    async def callback(self, interaction: discord.Interaction) -> None:
        if not interaction.user.guild_permissions.manage_roles:
            view = ui.LayoutView()
            view.add_item(ui.Container(
                ui.TextDisplay("## ❌ Permission Denied"),
                ui.TextDisplay("You need `Manage Roles` permission."),
                accent_color=discord.Color.red()
            ))
            return await interaction.response.send_message(view=view, ephemeral=True)
        
        await interaction.response.defer(ephemeral=True)
        cog = interaction.client.get_cog("PromoterRole")
        
        if not cog:
            return await interaction.followup.send(
                "❌ System not available.", ephemeral=True
            )
        
        if self.target_id:
            member = interaction.guild.get_member(self.target_id)
            if member:
                await cog.instant_member_check(member, "Manual force check (button)")
                await interaction.followup.send(f"✅ Checked {member.mention}", ephemeral=True)
            else:
                await interaction.followup.send("❌ Member not found.", ephemeral=True)
        else:
            msg = await interaction.followup.send("⚡ Checking all members...", ephemeral=True)
            checked = 0
            for member in interaction.guild.members:
                if not member.bot:
                    await cog.instant_member_check(member, "Manual bulk check (button)")
                    checked += 1
                    await asyncio.sleep(0.3)
            await msg.edit(content=f"✅ Checked {checked} members")


class DebugButton(ui.Button):
    def __init__(self, member_id: int) -> None:
        super().__init__(
            label="Debug",
            style=discord.ButtonStyle.secondary,
            custom_id=f"promoter_debug_{member_id}",
        )
        self.member_id = member_id

    async def callback(self, interaction: discord.Interaction) -> None:
        if not interaction.user.guild_permissions.manage_roles:
            view = ui.LayoutView()
            view.add_item(ui.Container(
                ui.TextDisplay("## ❌ Permission Denied"),
                ui.TextDisplay("You need `Manage Roles` permission."),
                accent_color=discord.Color.red()
            ))
            return await interaction.response.send_message(view=view, ephemeral=True)
        
        cog = interaction.client.get_cog("PromoterRole")
        if not cog:
            view = ui.LayoutView()
            view.add_item(ui.Container(
                ui.TextDisplay("## ❌ System Not Available"),
                accent_color=discord.Color.red()
            ))
            return await interaction.response.send_message(view=view, ephemeral=True)
        
        member = interaction.guild.get_member(self.member_id)
        if not member:
            view = ui.LayoutView()
            view.add_item(ui.Container(
                ui.TextDisplay("## ❌ Member Not Found"),
                accent_color=discord.Color.red()
            ))
            return await interaction.response.send_message(view=view, ephemeral=True)
        
        pg = getattr(member, "primary_guild", None)
        tag_value = str(pg.tag) if pg and getattr(pg, "tag", None) else "None"
        guild_id = pg.id if pg else "None"

        has_primary_tag = cog._has_primary_guild_tag(member)
        has_promotion = await cog._has_promotion_content(member)
        role = interaction.guild.get_role(cog.promoter_role_id)
        has_role = role in member.roles if role else False
        
        # Create thumbnail with member's avatar
        thumbnail_component = ui.Thumbnail(
            media=member.display_avatar.url,
            description=f"{member.display_name}'s avatar"
        )
        
        # Build status lines as a single string
        status_lines = [
            f"**🆔 ID:** {member.id}",
            f"**🌐 Status:** {str(member.status).title()}",
            f"**🎯 Primary Tag:** {'✅' if has_primary_tag else '❌'}",
            f"**🏷️ Tag Value:** {tag_value}",
            f"**🏛️ Guild ID:** {guild_id}",
            f"**🎯 Target Guild:** {cog.target_guild_id}",
            f"**📢 Promoting:** {'✅' if has_promotion else '❌'}",
            f"**🎪 Has Role:** {'✅' if has_role else '❌'}",
            f"**💾 Cached Status:** {cog._status_cache.get(cog._cache_key(member), '—')}",
        ]
        
        # Combine all text into one TextDisplay
        parts = [
            f"## 🐛 Debug — {member.display_name}",
            "\n".join(status_lines),
            f"-# Debugged by {interaction.user.display_name} • {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        ]
        combined_text = "\n\n".join(parts)
        
        # Create section with thumbnail
        section = ui.Section(
            ui.TextDisplay(combined_text),
            accessory=thumbnail_component
        )
        
        view = ui.LayoutView()
        view.add_item(ui.Container(section, accent_color=discord.Color.orange()))
        
        await interaction.response.send_message(view=view, ephemeral=True)

# ──────────────────────────────────────────────────────────────────────────────
# ACTION ROW
# ──────────────────────────────────────────────────────────────────────────────

class PromoterActionRow(ui.ActionRow):
    def __init__(self, member_id: Optional[int] = None) -> None:
        super().__init__()
        self.add_item(PromoterInfoButton())
        if member_id:
            self.add_item(DebugButton(member_id))


class PromoterAdminRow(ui.ActionRow):
    def __init__(self, member_id: Optional[int] = None) -> None:
        super().__init__()
        self.add_item(ForceCheckButton(member_id))


# ──────────────────────────────────────────────────────────────────────────────
# PROMOTER LAYOUT VIEW
# ──────────────────────────────────────────────────────────────────────────────

class PromoterLayoutView(ui.LayoutView):
    def __init__(
        self,
        member_id: Optional[int] = None,
        show_admin: bool = False,
    ) -> None:
        super().__init__(timeout=None)
        
        children: list = [
            ui.TextDisplay("## 🎯 Promoter System"),
            ui.Separator(spacing=discord.SeparatorSpacing.small),
            ui.TextDisplay(
                "Get the **Promoter** role by helping us grow!\n\n"
                "**Ways to promote:**\n"
                "• Set your **Primary Guild Tag** to `Raही`\n"
                "• Add one of these to your custom status:\n"
                "  - `.gg/aakhristation`\n"
                "  - `discord.gg/aakhristation`\n"
                "  - `aakhristation`\n\n"
                "✨ **Perks:**\n"
                "• Special role and recognition\n"
                "• Community appreciation"
            ),
            ui.Separator(spacing=discord.SeparatorSpacing.small),
            ui.TextDisplay("-# Click the buttons below for more info"),
        ]
        
        self.add_item(ui.Container(*children, accent_color=discord.Color.gold()))
        self.add_item(PromoterActionRow(member_id))
        
        if show_admin:
            self.add_item(PromoterAdminRow(member_id))


# ──────────────────────────────────────────────────────────────────────────────
# COG
# ──────────────────────────────────────────────────────────────────────────────

class PromoterRole(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db_path = "data/promoter_role.db"

        # Server-specific settings
        self.promoter_role_id = 1440636027923333182
        self.your_server_invites = [
            ".gg/aakhristation",
            "discord.gg/aakhristation",
            "aakhristation",
        ]
        self.server_tag = "Raही"
        self.log_channel_id = 1443119503285293098
        self.target_guild_id = int(os.environ.get("GUILD_ID", "0"))

        # Cache with size limit
        self._status_cache: dict[str, str] = {}
        self._STATUS_CACHE_MAX = 5000

        self._db_ready = False
        self._initialized = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def cog_load(self):
        """Initialize the cog when loaded."""
        await self._init_db()
        self._db_ready = True
        self._initialized = True
        
        # Start background tasks
        if not self.fallback_check.is_running():
            self.fallback_check.start()
        
        # Register persistent views
        await self._load_persistent_views()
        
        logger.info("PromoterRole cog loaded")

    async def cog_unload(self):
        self.fallback_check.cancel()

    async def _load_persistent_views(self) -> None:
        """Load persistent views for the system."""
        try:
            # Create and register the main view
            view = PromoterLayoutView()
            self.bot.add_view(view)
            
            logger.info("[PromoterRole] ✅ Persistent views loaded.")
        except Exception as e:
            logger.error(f"[PromoterRole] ❌ View load error: {e}")

    # ── Database ──────────────────────────────────────────────────────────────

    async def _init_db(self):
        """Initialize the database with proper schema."""
        from pathlib import Path
        
        data_dir = Path("data")
        data_dir.mkdir(parents=True, exist_ok=True)
        
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
            await db.execute("""
                CREATE TABLE IF NOT EXISTS promoter_views (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    member_id   INTEGER,
                    message_id  INTEGER UNIQUE,
                    active      BOOLEAN DEFAULT 1,
                    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await db.commit()
            logger.info(f"Database initialized at {self.db_path}")

    async def _log_promotion_action(
        self,
        guild_id: int,
        member_id: int,
        member_name: str,
        action: str,
        reason: str,
        content: str = "",
    ):
        """Log promotion actions to database."""
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
        Evicts oldest entries when over the size limit.
        """
        key = self._cache_key(member)
        new_val = self._get_status_content(member)
        old_val = self._status_cache.get(key, "")

        if len(self._status_cache) >= self._STATUS_CACHE_MAX and key not in self._status_cache:
            # Evict ~10% of oldest entries
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

            has_promotion = await self._has_promotion_content(member)
            currently_has = role in member.roles

            if has_promotion and not currently_has:
                promo_content = self._build_promotion_source_string(member)
                await member.add_roles(role, reason=f"Promotion detected: {reason}")
                await self._log_promotion_action(
                    member.guild.id, member.id, str(member),
                    "ROLE_ASSIGNED", reason, promo_content,
                )
                await self._send_promotion_log(member, "assigned", promo_content)

            elif not has_promotion and currently_has:
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
        """Recheck all members when the guild's clan tag changes."""
        before_tag = getattr(before, "tag", None)
        after_tag = getattr(after, "tag", None)
        if before_tag == after_tag:
            return

        logger.info("Guild tag changed: '%s' -> '%s'", before_tag, after_tag)
        if after_tag == self.server_tag or before_tag == self.server_tag:
            asyncio.create_task(
                self._bulk_check_guild(after, f"Server tag changed to {after_tag}")
            )

    # ── Background task ───────────────────────────────────────────────────────

    @tasks.loop(minutes=5)
    async def fallback_check(self):
        """Periodic sanity check — repairs any members whose role is out of sync."""
        if not self._initialized:
            return
            
        for guild in self.bot.guilds:
            role = guild.get_role(self.promoter_role_id)
            if not role:
                continue
            for member in guild.members:
                if member.bot:
                    continue
                has_tag = self._has_primary_guild_tag(member)
                has_role = role in member.roles
                if has_tag != has_role:
                    full = await self._has_promotion_content(member)
                    if full != has_role:
                        await self.instant_member_check(member, "5m fallback")
                        await asyncio.sleep(0.5)

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

    # ── Promotion Log (V2 Components) ─────────────────────────────────────────

    async def _send_promotion_log(
        self, member: discord.Member, action_type: str, content: str = ""
    ):
        """Send promotion log using Components V2 with thumbnail."""
        try:
            channel = member.guild.get_channel(self.log_channel_id)
            if not channel:
                logger.warning("Log channel %s not found", self.log_channel_id)
                return

            # Get member avatar URL for thumbnail
            avatar_url = member.display_avatar.url
            
            # Create the thumbnail component
            thumbnail_component = ui.Thumbnail(
                media=avatar_url,
                description=f"{member.display_name}'s avatar"
            )

            if action_type == "assigned":
                # Create text content
                text_content = (
                    f"**{member.mention}** is promoting our server! <:ehm:1390067566503788625>\n\n"
                    f"**YOU JUST RECEIVED SOME PERKS!!!**"
                )
                
                # Build the main content - combine all text into fewer components
                main_content = []
                
                # Title
                main_content.append("## :fish_cake: THANK YOU!!!!")
                
                # Main message
                main_content.append(text_content)
                
                if content and content != "No promotion detected":
                    main_content.append(f"**Promotion Source:**\n```{content}```")
                
                # Combine into a single TextDisplay with separators
                combined_text = "\n\n".join(main_content)
                
                # Create section with the combined text and thumbnail
                section = ui.Section(
                    ui.TextDisplay(combined_text),
                    accessory=thumbnail_component
                )
                
                view = ui.LayoutView()
                view.add_item(ui.Container(section, accent_color=discord.Color.green()))
                
            else:
                # Removed promotion view
                text_content = f"**{member.mention}** stopped promoting us :broken_heart:"
                
                # Build the main content
                main_content = []
                
                # Title
                main_content.append("## :fish_cake: GOODBYE :(")
                
                # Main message
                main_content.append(text_content)
                
                # Promotion source if exists
                if content and content != "No promotion detected":
                    main_content.append(f"**Previous Promotion Source:**\n```{content}```")
                
                # Combine into a single TextDisplay with separators
                combined_text = "\n\n".join(main_content)
                
                # Create section with the combined text and thumbnail
                section = ui.Section(
                    ui.TextDisplay(combined_text),
                    accessory=thumbnail_component
                )
                
                view = ui.LayoutView()
                view.add_item(ui.Container(section, accent_color=discord.Color.orange()))
            
            # Send the V2 component with thumbnail
            await channel.send(view=view)
            
        except discord.Forbidden:
            logger.warning("No permission to send to log channel %s", self.log_channel_id)
        except Exception as e:
            logger.error("send_promotion_log error: %s", e)

    # ── Prefix Commands ───────────────────────────────────────────────────────

    @commands.command(name="promoter", aliases=["promo"])
    async def promoter_command(self, ctx: commands.Context):
        """Show the promoter system interface."""
        if not self._initialized:
            view = ui.LayoutView()
            view.add_item(ui.Container(
                ui.TextDisplay("## ❌ Still Initializing"),
                ui.TextDisplay("Try again in a moment."),
                accent_color=discord.Color.red()
            ))
            return await ctx.send(view=view)
        
        # Check if user has manage roles permission to show admin features
        has_admin = ctx.author.guild_permissions.manage_roles
        
        # Create and send the view
        view = PromoterLayoutView(
            member_id=ctx.author.id if has_admin else None,
            show_admin=has_admin,
        )
        
        # Create V2 layout view for the promoter command
        children: list = [
            ui.TextDisplay("## 🌟 Promoter System"),
            ui.Separator(spacing=discord.SeparatorSpacing.small),
            ui.TextDisplay("Click the buttons below to interact with the promoter system!"),
            ui.Separator(spacing=discord.SeparatorSpacing.small),
            ui.TextDisplay(f"-# Requested by {ctx.author.display_name} • {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"),
        ]
        
        main_view = ui.LayoutView()
        main_view.add_item(ui.Container(*children, accent_color=discord.Color.gold()))
        
        await ctx.send(view=main_view)
        await ctx.send(view=view)  # Send the interactive view with buttons

    @commands.command(name="pgtag")
    async def pgtag_command(self, ctx: commands.Context, member: discord.Member = None):
        """Check a member's primary guild tag."""
        target = member or ctx.author
        
        try:
            pg = getattr(target, "primary_guild", None)
            if pg is None:
                view = ui.LayoutView()
                view.add_item(ui.Container(
                    ui.TextDisplay("## ❌ No Primary Guild"),
                    ui.TextDisplay("This user has no primary guild set."),
                    accent_color=discord.Color.red()
                ))
                return await ctx.send(view=view)
            
            tag = getattr(pg, "tag", None)
            if tag is None:
                view = ui.LayoutView()
                view.add_item(ui.Container(
                    ui.TextDisplay("## ⚠️ No Tag"),
                    ui.TextDisplay("Primary guild set but no tag."),
                    accent_color=discord.Color.orange()
                ))
                return await ctx.send(view=view)

            would_promote = (str(tag) == str(self.server_tag) and pg.id == self.target_guild_id)
            
            # Build status lines
            info_lines = [
                f"**Member:** {target.mention}",
                f"**Tag:** **{tag}**",
                f"**Guild ID:** **{pg.id}**",
                f"**Target Guild ID:** **{self.target_guild_id}**",
                f"**Promoter Status:** {'✅ Would Get Role' if would_promote else '❌ No Role'}",
            ]
            
            # Create V2 layout view
            children: list = [
                ui.TextDisplay("## 🏷️ Primary Guild Tag"),
                ui.Separator(spacing=discord.SeparatorSpacing.small),
                ui.TextDisplay("\n".join(info_lines)),
                ui.Separator(spacing=discord.SeparatorSpacing.small),
                ui.TextDisplay(f"-# Checked by {ctx.author.display_name} • {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"),
            ]
            
            view = ui.LayoutView()
            view.add_item(ui.Container(*children, accent_color=discord.Color.blue()))
            
            await ctx.send(view=view)
        except Exception as e:
            logger.error("pgtag error: %s", e)
            view = ui.LayoutView()
            view.add_item(ui.Container(
                ui.TextDisplay("## ❌ Error"),
                ui.TextDisplay(f"Error: {e}"),
                accent_color=discord.Color.red()
            ))
            await ctx.send(view=view)

    @commands.command(name="force_check")
    @commands.has_permissions(manage_roles=True)
    async def force_check_command(self, ctx: commands.Context, member: discord.Member = None):
        """Force check promotion status for a member."""
        if member:
            await self.instant_member_check(member, "Manual force check")
            view = ui.LayoutView()
            view.add_item(ui.Container(
                ui.TextDisplay("## ✅ Check Complete"),
                ui.TextDisplay(f"Checked {member.mention}"),
                accent_color=discord.Color.green()
            ))
            await ctx.send(view=view)
        else:
            msg = await ctx.send("⚡ Checking all members...")
            checked = 0
            for guild_member in ctx.guild.members:
                if not guild_member.bot:
                    await self.instant_member_check(guild_member, "Manual bulk check")
                    checked += 1
                    await asyncio.sleep(0.3)
            
            view = ui.LayoutView()
            view.add_item(ui.Container(
                ui.TextDisplay("## ✅ Bulk Check Complete"),
                ui.TextDisplay(f"Checked {checked} members"),
                accent_color=discord.Color.green()
            ))
            await msg.edit(content=None, view=view)

    @commands.command(name="promoter_stats")
    @commands.has_permissions(manage_roles=True)
    async def promoter_stats_command(self, ctx: commands.Context):
        """Show promoter system statistics."""
        role = ctx.guild.get_role(self.promoter_role_id)
        total_promoters = len([m for m in ctx.guild.members if role in m.roles and not m.bot]) if role else 0
        members_with_tag = sum(1 for m in ctx.guild.members if not m.bot and self._has_primary_guild_tag(m))
        
        # Create V2 layout view for stats
        children: list = [
            ui.TextDisplay("## 🤖 Promoter System Stats"),
            ui.Separator(spacing=discord.SeparatorSpacing.small),
            ui.TextDisplay(
                f"**👥 Promoters:** {total_promoters}\n"
                f"**🏷️ Primary Tags:** {members_with_tag}\n"
                f"**🔄 Check Interval:** 5 minutes\n"
                f"**🎯 Target Guild ID:** {self.target_guild_id}\n"
                f"**💾 Status Cache:** {len(self._status_cache)}"
            ),
            ui.Separator(spacing=discord.SeparatorSpacing.small),
            ui.TextDisplay(f"-# Requested by {ctx.author.display_name} • {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"),
        ]
        
        view = ui.LayoutView()
        view.add_item(ui.Container(*children, accent_color=discord.Color.blue()))
        
        await ctx.send(view=view)

    @commands.command(name="debug_promoter", aliases=["dbgpromo"])
    @commands.has_permissions(manage_roles=True)
    async def debug_promoter_command(self, ctx: commands.Context, member: discord.Member = None):
        """Show debug info for a member."""
        target = member or ctx.author
        
        pg = getattr(target, "primary_guild", None)
        tag_value = str(pg.tag) if pg and getattr(pg, "tag", None) else "None"
        guild_id = pg.id if pg else "None"

        has_primary_tag = self._has_primary_guild_tag(target)
        has_promotion = await self._has_promotion_content(target)
        role = ctx.guild.get_role(self.promoter_role_id)
        has_role = role in target.roles if role else False
        
        # Build status lines
        status_lines = [
            f"**🆔 ID:** {target.id}",
            f"**🌐 Status:** {str(target.status).title()}",
            f"**🎯 Primary Tag:** {'✅' if has_primary_tag else '❌'}",
            f"**🏷️ Tag Value:** {tag_value}",
            f"**🏛️ Guild ID:** {guild_id}",
            f"**🎯 Target Guild:** {self.target_guild_id}",
            f"**📢 Promoting:** {'✅' if has_promotion else '❌'}",
            f"**🎪 Has Role:** {'✅' if has_role else '❌'}",
            f"**💾 Cached Status:** {self._status_cache.get(self._cache_key(target), '—')}",
        ]
        
        # Create V2 layout view for debug info
        children: list = [
            ui.TextDisplay(f"## 🐛 Debug — {target.display_name}"),
            ui.Separator(spacing=discord.SeparatorSpacing.small),
            ui.TextDisplay("\n".join(status_lines)),
            ui.Separator(spacing=discord.SeparatorSpacing.small),
            ui.TextDisplay(f"-# Debugged by {ctx.author.display_name} • {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"),
        ]
        
        view = ui.LayoutView()
        view.add_item(ui.Container(*children, accent_color=discord.Color.orange()))
        
        await ctx.send(view=view)


async def setup(bot: commands.Bot):
    """Setup function for the cog."""
    await bot.add_cog(PromoterRole(bot))