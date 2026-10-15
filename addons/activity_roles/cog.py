from __future__ import annotations

import asyncio
import logging
import time

import discord
from discord.ext import commands

from core import EmberCog, Translator, cog_i18n
from utils.feature_registry import ConfigField, FieldType, feature

from .schema import ActivityRolesGuildConfig

_ = Translator("activity_roles", __file__)

log = logging.getLogger("ember.activity_roles")


@cog_i18n(_)
@feature(
    display_name="Activity Roles",
    description="Auto-assigns activity-based roles for Crunchyroll, YouTube, and Spotify presence.",
    emoji="🎮",
    fields=[
        ConfigField(
            key="crunchyroll_role_id",
            label="Crunchyroll Role",
            type=FieldType.ROLE,
            description="Role assigned while watching Crunchyroll.",
        ),
        ConfigField(
            key="youtube_role_id",
            label="YouTube Role",
            type=FieldType.ROLE,
            description="Role assigned while watching YouTube.",
        ),
        ConfigField(
            key="spotify_role_id",
            label="Spotify Role",
            type=FieldType.ROLE,
            description="Role assigned while listening on Spotify.",
        ),
        ConfigField(
            key="interval",
            label="Rate-limit (seconds)",
            type=FieldType.INTEGER,
            placeholder="10",
            default=10,
            description="Minimum seconds between role updates per member.",
        ),
    ],
)
class ActivityRoles(EmberCog, name="ActivityRoles"):

    default_config = {
        "guild": {
            "enabled": True,
            "crunchyroll_role_id": None,
            "youtube_role_id": None,
            "spotify_role_id": None,
            "interval": 10,
        },
    }

    def __init__(self, bot: commands.Bot):
        super().__init__(bot)
        self.processing: set[int] = set()
        self.rate_limit: dict[int, float] = {}
        self._role_cache: dict[int, dict[str, discord.Role | None]] = {}
        self._cleanup_done = False
        self.db.register_schema("guild", ActivityRolesGuildConfig)
        self.db.register_schema("role", ActivityRolesGuildConfig)

    async def cog_load(self):
        asyncio.create_task(self._startup_cleanup())

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role):
        self._role_cache.pop(after.guild.id, None)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        self._role_cache.pop(role.guild.id, None)

    async def _get_roles(self, guild: discord.Guild) -> dict[str, discord.Role | None]:
        if guild.id not in self._role_cache:
            cfg = await self.db.guild(guild.id).all()
            role_ids = {
                "Crunchyroll": cfg.get("crunchyroll_role_id"),
                "YouTube": cfg.get("youtube_role_id"),
                "Spotify": cfg.get("spotify_role_id"),
            }
            self._role_cache[guild.id] = {
                name: guild.get_role(rid) for name, rid in role_ids.items() if rid
            }
        return self._role_cache[guild.id]

    async def _startup_cleanup(self):
        if self._cleanup_done:
            return
        await self.bot.wait_until_ready()
        removed = 0
        for guild in self.bot.guilds:
            roles = [r for r in (await self._get_roles(guild)).values() if r]
            if not roles:
                continue
            count = 0
            async for member in guild.fetch_members(limit=None):
                count += 1
                to_remove = [r for r in roles if r in member.roles]
                if to_remove:
                    try:
                        await member.remove_roles(*to_remove, reason="Startup cleanup")
                        removed += len(to_remove)
                    except (discord.Forbidden, discord.HTTPException):
                        pass
                if count % 50 == 0:
                    await asyncio.sleep(0.1)
        self._cleanup_done = True
        log.info(f"[ActivityRoles] Startup cleanup: removed {removed} role(s)")

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member):
        if after.guild is None or after.bot:
            return
        if not await self.db.guild(after.guild.id).get("enabled", default=True):
            return
        now = time.time()
        interval = await self.db.guild(after.guild.id).get("interval", default=10)
        if after.id in self.rate_limit and now - self.rate_limit[after.id] < interval:
            return
        if after.id in self.processing:
            return

        self.processing.add(after.id)
        self.rate_limit[after.id] = now
        try:
            await self._process(before, after)
        except Exception as e:
            log.error(f"[ActivityRoles] Error for {after.display_name}: {e}")
        finally:
            self.processing.discard(after.id)

    async def _process(self, before: discord.Member, after: discord.Member):
        guild_roles = await self._get_roles(after.guild)
        if not guild_roles:
            return

        active: set = set()
        current_roles = set(after.roles)
        all_activity_roles = {r for r in guild_roles.values() if r}

        for activity in after.activities:
            if activity.type == discord.ActivityType.custom:
                continue
            if isinstance(activity, discord.Spotify):
                r = guild_roles.get("Spotify")
                if r:
                    active.add(r)
                continue
            if isinstance(activity, discord.Activity) and activity.name:
                name = activity.name.lower()
                if "youtube" in name:
                    r = guild_roles.get("YouTube")
                    if r:
                        active.add(r)
                elif "crunchyroll" in name:
                    r = guild_roles.get("Crunchyroll")
                    if r:
                        active.add(r)

        to_add = active - current_roles
        to_remove = (all_activity_roles - active) & current_roles
        if not to_add and not to_remove:
            return

        try:
            if to_add:
                await after.add_roles(*to_add, reason="Activity detected")
            if to_remove:
                await after.remove_roles(*to_remove, reason="Activity ended")
        except discord.Forbidden:
            log.warning(f"[ActivityRoles] No permission for {after.display_name}")
        except discord.HTTPException as e:
            log.error(f"[ActivityRoles] HTTPException for {after.display_name}: {e}")

    @commands.hybrid_command(name="activityroles", description="Show activity role configuration")
    @commands.has_permissions(manage_roles=True)
    async def activity_roles_info(self, ctx: commands.Context):
        view = discord.ui.LayoutView(timeout=None)
        children = [
            discord.ui.TextDisplay("## 🎯 Activity Roles"),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
        ]

        roles = await self._get_roles(ctx.guild)
        if roles:
            for name, role in roles.items():
                value = role.mention if role else "❌ Not configured"
                children.append(discord.ui.TextDisplay(f"**{name}:** {value}"))
        else:
            children.append(discord.ui.TextDisplay("No activity roles configured."))

        view.add_item(discord.ui.Container(*children, accent_color=discord.Color.blurple()))
        await ctx.send(view=view)

    async def cog_unload(self) -> None:
        self._role_cache.clear()



setup = ActivityRoles.make_setup()
