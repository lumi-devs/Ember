from __future__ import annotations

import asyncio
import logging
import os

import discord
from discord import ui
from discord.ext import commands, tasks

from core import EmberCog, Translator, cog_i18n
from core.branding import EmberColors
from utils.feature_registry import ConfigField, FieldType, feature

from .schema import PromoterGuildConfig

_ = Translator("promoter", __file__)

log = logging.getLogger("ember.promoter")

PROMOTER_ROLE_ID = int(os.getenv("PROMOTER_ROLE_ID", "0"))
PROMOTER_LOG_CHANNEL_ID = int(os.getenv("PROMOTER_LOG_CHANNEL_ID", "0"))
SERVER_TAG = os.getenv("PROMOTER_SERVER_TAG", "")
SERVER_INVITES = [
    s.strip()
    for s in os.getenv(
        "PROMOTER_INVITES", ".gg/aakhristation,discord.gg/aakhristation,aakhristation"
    ).split(",")
    if s.strip()
]


class PromoterInfoButton(ui.Button):
    def __init__(self):
        super().__init__(
            label="How to Promote",
            style=discord.ButtonStyle.secondary,
            custom_id="promoter_info_button",
        )

    async def callback(self, interaction: discord.Interaction):
        invites_list = "\n".join(f"   • `{inv}`" for inv in SERVER_INVITES)
        text = (
            f"## 🌟 How to Get the Promoter Role\n\n"
            f"Get the **Promoter** role by helping us grow!\n\n"
            f"**Ways to promote:**\n"
            f"1️⃣ Set your **Primary Guild Tag** to `{SERVER_TAG}`\n"
            f"2️⃣ Put one of these in your custom status:\n{invites_list}\n\n"
            f"✨ **Perks:** Special role and recognition\n\n"
            f"-# Requested by {interaction.user.display_name}"
        )
        view = ui.LayoutView()
        view.add_item(ui.Container(ui.TextDisplay(text), accent_color=EmberColors.WARNING))
        await interaction.response.send_message(view=view, ephemeral=True)


class PromoterActionRow(ui.ActionRow):
    def __init__(self):
        super().__init__()
        self.add_item(PromoterInfoButton())


class PromoterLayoutView(ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=None)
        invites_list = "\n".join(f"  - `{inv}`" for inv in SERVER_INVITES)
        children: list = [
            ui.TextDisplay("## 🎯 Promoter System"),
            ui.Separator(spacing=discord.SeparatorSpacing.small),
            ui.TextDisplay(
                f"Get the **Promoter** role by helping us grow!\n\n"
                f"**Ways to promote:**\n"
                f"• Set your **Primary Guild Tag** to `{SERVER_TAG}`\n"
                f"• Add one of these to your custom status:\n{invites_list}\n\n"
                f"✨ **Perks:** Special role and recognition"
            ),
            ui.Separator(spacing=discord.SeparatorSpacing.small),
            ui.TextDisplay("-# Click the button below for more info"),
            PromoterActionRow(),
        ]
        self.add_item(ui.Container(*children, accent_color=EmberColors.WARNING))


@cog_i18n(_)
@feature(
    display_name="Promoter",
    description="Tracks members who promote the server and optionally rewards them with a role.",
    emoji="📢",
    fields=[
        ConfigField(
            key="channel_id",
            label="Promo Requests Channel",
            type=FieldType.CHANNEL,
            required=True,
            description="Channel where promotion submissions are sent.",
        ),
        ConfigField(
            key="role_id",
            label="Promoter Role",
            type=FieldType.ROLE,
            description="Role awarded to confirmed promoters.",
        ),
    ],
)
class PromoterRole(EmberCog, name="PromoterRole"):

    default_config = {
        "guild": {"enabled": True},
    }

    def __init__(self, bot: commands.Bot):
        super().__init__(bot)
        from .services.promoter_service import PromoterService

        self.db.register_schema("guild", PromoterGuildConfig)

        self.db_path = "data/promoter_role.db"
        self.promoter_role_id = PROMOTER_ROLE_ID
        self.log_channel_id = PROMOTER_LOG_CHANNEL_ID
        self.server_tag = SERVER_TAG
        self.server_invites = SERVER_INVITES
        # Target guild ID now configured per-guild
        self.target_guild_id = int(os.getenv("GUILD_ID", "0"))
        self._status_cache: dict[str, str] = {}
        self._STATUS_CACHE_MAX = 5000
        self._initialized = False
        self.promoter_service = PromoterService()

    async def cog_load(self):
        await self.promoter_service.init_db()
        self._initialized = True
        self.bot.add_view(PromoterLayoutView())
        log.info("PromoterRole cog loaded")

    async def delete_data_for_user(self, user_id: int, guild_id: int | None) -> dict:
        await self.promoter_service.delete_user_data(user_id)
        return {}

    async def _init_db(self):
        await self.promoter_service.init_db()

    async def _log_action(self, guild_id, member_id, member_name, action, reason, content=""):
        # Use service layer for logging
        await self.promoter_service.log_action(
            guild_id, member_id, member_name, action, reason, content
        )

    def _status_text(self, member: discord.Member) -> str:
        if member.status == discord.Status.offline:
            return "offline"
        for act in member.activities:
            if isinstance(act, discord.CustomActivity) and act.name:
                return act.name.lower()
        return ""

    def _has_primary_tag(self, member: discord.Member) -> bool:
        pg = getattr(member, "primary_guild", None)
        if pg is None or pg.id != self.target_guild_id:
            return False
        return str(getattr(pg, "tag", "") or "") == str(self.server_tag)

    async def _is_promoting(self, member: discord.Member) -> bool:
        if self._has_primary_tag(member):
            return True
        if member.status != discord.Status.offline:
            status = self._status_text(member)
            if status and status != "offline":
                return any(inv.lower() in status for inv in self.server_invites)
        return False

    def _cache_key(self, member: discord.Member) -> str:
        return f"{member.guild.id}-{member.id}"

    def _update_cache(self, member: discord.Member):
        key = self._cache_key(member)
        new_val = self._status_text(member)
        old_val = self._status_cache.get(key, "")
        if len(self._status_cache) >= self._STATUS_CACHE_MAX and key not in self._status_cache:
            evict = self._STATUS_CACHE_MAX // 10
            for k in list(self._status_cache)[:evict]:
                del self._status_cache[k]
        self._status_cache[key] = new_val
        return old_val, new_val

    async def instant_member_check(self, member: discord.Member, reason: str):
        if not self.promoter_role_id:
            return
        try:
            role = member.guild.get_role(self.promoter_role_id)
            if not role:
                return
            promoting = await self._is_promoting(member)
            has_role = role in member.roles
            if promoting and not has_role:
                await member.add_roles(role, reason=f"Promotion: {reason}")
                content = self._status_text(member)
                await self._log_action(
                    member.guild.id, member.id, str(member), "ROLE_ASSIGNED", reason, content
                )
                await self._send_log(member, "assigned", content)
            elif not promoting and has_role:
                cached = self._status_cache.get(self._cache_key(member), "")
                await member.remove_roles(role, reason=f"Promotion removed: {reason}")
                await self._log_action(
                    member.guild.id, member.id, str(member), "ROLE_REMOVED", reason, cached
                )
                await self._send_log(member, "removed", cached)
        except discord.Forbidden:
            log.warning("No permission to manage roles for %s", member)
        except Exception as e:
            log.error("instant_member_check error: %s", e)

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member):
        if after.bot:
            return
        if not await self.db.guild(after.guild.id).get("enabled", default=True):
            return
        old, new = self._update_cache(after)
        if old != new:
            await self.instant_member_check(after, "Status changed")

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if after.bot:
            return
        if not await self.db.guild(after.guild.id).get("enabled", default=True):
            return
        if self._has_primary_tag(before) != self._has_primary_tag(after):
            await self.instant_member_check(after, "Primary tag changed")

    @tasks.loop(minutes=5)
    async def fallback_check(self):
        if not self._initialized or not self.promoter_role_id:
            return
        for guild in self.bot.guilds:
            if not await self.db.guild(guild.id).get("enabled", default=True):
                continue
            role = guild.get_role(self.promoter_role_id)
            if not role:
                continue
            for member in guild.members:
                if member.bot:
                    continue
                promoting = await self._is_promoting(member)
                if promoting != (role in member.roles):
                    await self.instant_member_check(member, "5m fallback")
                    await asyncio.sleep(0.5)

    @fallback_check.before_loop
    async def before_fallback(self):
        await self.bot.wait_until_ready()

    async def _send_log(self, member: discord.Member, action: str, content: str):
        if not self.log_channel_id:
            return
        try:
            channel = member.guild.get_channel(self.log_channel_id)
            if not channel:
                return
            if action == "assigned":
                title = "## :fish_cake: THANK YOU!!!!"
                body = f"**{member.mention}** is promoting our server!"
                color = EmberColors.SUCCESS
            else:
                title = "## :fish_cake: GOODBYE :("
                body = f"**{member.mention}** stopped promoting us :broken_heart:"
                color = EmberColors.WARNING
            parts = [title, body]
            if content and content not in ("", "offline"):
                parts.append(
                    f"**{'Promotion' if action == 'assigned' else 'Previous'} Source:**\n```{content}```"
                )
            section = ui.Section(
                ui.TextDisplay("\n\n".join(parts)),
                accessory=ui.Thumbnail(media=member.display_avatar.url),
            )
            view = ui.LayoutView()
            view.add_item(ui.Container(section, accent_color=color))
            await channel.send(view=view)
        except Exception as e:
            log.error("send_log error: %s", e)

    @commands.command(name="promoter", aliases=["promo"])
    async def promoter_cmd(self, ctx: commands.Context):
        view = PromoterLayoutView()
        await ctx.send(view=view)

    @commands.command(name="promoter_stats")
    @commands.has_permissions(manage_roles=True)
    async def promoter_stats(self, ctx: commands.Context):
        role = ctx.guild.get_role(self.promoter_role_id) if self.promoter_role_id else None
        count = len([m for m in ctx.guild.members if role and role in m.roles and not m.bot])
        tags = sum(1 for m in ctx.guild.members if not m.bot and self._has_primary_tag(m))
        view = ui.LayoutView()
        view.add_item(
            ui.Container(
                ui.TextDisplay("## 🤖 Promoter Stats"),
                ui.Separator(spacing=discord.SeparatorSpacing.small),
                ui.TextDisplay(
                    f"**Promoters:** {count}\n"
                    f"**Primary Tags:** {tags}\n"
                    f"**Cache size:** {len(self._status_cache)}\n"
                    f"**Target Guild:** {self.target_guild_id}"
                ),
                accent_color=discord.Color.blurple(),
            )
        )
        await ctx.send(view=view)

    @commands.command(name="force_check")
    @commands.has_permissions(manage_roles=True)
    async def force_check(self, ctx: commands.Context, member: discord.Member = None):
        if member:
            await self.instant_member_check(member, "Manual force check")
            view = ui.LayoutView()
            view.add_item(
                ui.Container(
                    ui.TextDisplay(f"## ✅ Checked {member.display_name}"),
                    accent_color=EmberColors.SUCCESS,
                )
            )
            await ctx.send(view=view)
        else:
            msg = await ctx.send("⚡ Checking all members...")
            checked = 0
            for m in ctx.guild.members:
                if not m.bot:
                    await self.instant_member_check(m, "Bulk force check")
                    checked += 1
                    await asyncio.sleep(0.3)
            view = ui.LayoutView()
            view.add_item(
                ui.Container(
                    ui.TextDisplay(f"## ✅ Checked {checked} members"),
                    accent_color=EmberColors.SUCCESS,
                )
            )
            await msg.edit(content=None, view=view)


setup = PromoterRole.make_setup()
