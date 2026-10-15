# cogs/confessions/cog.py
from __future__ import annotations

import logging
import traceback
from typing import Literal

import discord
from discord import app_commands

from core.cog import EmberCog
from core.i18n import Translator, cog_i18n
from core import checks
from utils.feature_registry import feature, ConfigField, FieldType

from .helpers import sha256_user_id, set_service
from .reply import ReplyLayoutView
from .schema import ConfessionsGuildConfig
from .services.confessions_service import ConfessionsService
from .views import build_confession_layout_view

_ = Translator("confessions", __file__)

log = logging.getLogger("ember.confessions")


@cog_i18n(_)
@feature(
    display_name="Confessions",
    emoji="💌",
    description="Anonymous confession system with threaded replies, reports, and mod tools.",
    fields=[
        ConfigField(
            key="confession_channel_id",
            label="Confession Channel",
            type=FieldType.CHANNEL,
            required=True,
            description="Channel where confessions are posted.",
        ),
        ConfigField(
            key="report_channel_id",
            label="Report Channel",
            type=FieldType.CHANNEL,
            required=True,
            description="Channel where reports are sent to staff.",
        ),
        ConfigField(
            key="media_channel_id",
            label="Media Channel",
            type=FieldType.CHANNEL,
            description="Channel used to re-host confession images.",
        ),
        ConfigField(
            key="log_channel_id",
            label="Admin Log Channel",
            type=FieldType.CHANNEL,
            description="Channel for full confession logs (includes user IDs).",
        ),
        ConfigField(
            key="mod_log_channel_id",
            label="Mod Log Channel",
            type=FieldType.CHANNEL,
            description="Channel for mod-facing logs (hashed user IDs).",
        ),
        ConfigField(
            key="daily_limit",
            label="Daily Confession Limit",
            type=FieldType.INTEGER,
            placeholder="3",
            default=3,
        ),
        ConfigField(
            key="hourly_report_limit",
            label="Hourly Report Limit (per user)",
            type=FieldType.INTEGER,
            placeholder="5",
            default=5,
        ),
    ],
)
class Confessions(EmberCog, name="Confessions"):
    default_config = {
        "guild": {
            "confession_channel_id": None,
            "report_channel_id": None,
            "media_channel_id": None,
            "log_channel_id": None,
            "mod_log_channel_id": None,
            "daily_limit": 3,
            "hourly_report_limit": 5,
            "allowed_role_ids": [],
            "enabled": True,
        },
    }

    def __init__(self, bot: discord.Client) -> None:
        super().__init__(bot)
        self.db.register_schema("guild", ConfessionsGuildConfig)
        self.confessions_service = ConfessionsService()

    async def cog_load(self) -> None:
        await self.confessions_service.init_db()
        set_service(self.confessions_service)
        await self._load_persistent_views()
        log.info("[Confessions] Initialized.")

    async def red_delete_data_for_user(
        self,
        *,
        requester: Literal["discord_deleted_user", "owner", "user", "user_strict"],
        user_id: int,
    ) -> None:
        """Delete user's confession data for GDPR compliance."""
        await self.confessions_service.delete_user_data(user_id)

    async def _load_persistent_views(self) -> None:
        await self.bot.wait_until_ready()
        try:
            latest_id = await self.confessions_service.get_max_confession_id()

            confessions = await self.confessions_service.get_all_confessions_with_message_ids()
            for cid, _ in confessions:
                try:
                    view = await build_confession_layout_view(
                        cid, show_confess_button=(cid == latest_id)
                    )
                    self.bot.add_view(view)
                except Exception as e:
                    log.error("[Confessions] View load error #%s: %s", cid, e)

            replies = await self.confessions_service.get_all_replies_with_message_ids()
            for rid, _ in replies:
                try:
                    view = await ReplyLayoutView.from_db(rid)
                    self.bot.add_view(view)
                except Exception as e:
                    log.error("[Confessions] Reply view load error #%s: %s", rid, e)

            log.info("[Confessions] Persistent views loaded.")
        except Exception as e:
            log.error("[Confessions] View load error: %s", e)

    async def _cfg(self, guild_id: int, key: str, *, default: int = 0) -> int:
        val = await self.db.guild(guild_id).get(key, default=default)
        return val or default

    async def _allowed_roles(self, guild_id: int) -> list[int]:
        return await self.db.guild(guild_id).get("allowed_role_ids", default=[]) or []

    confession_group = app_commands.Group(name="confession", description="Confession commands")

    @confession_group.command(
        name="delete",
        description="Delete a confession by ID and renumber all subsequent ones",
    )
    @app_commands.describe(
        confession_id="ID of the confession to delete",
        reason="Reason for deletion (optional)",
    )
    @checks.mod()
    async def confession_delete(
        self,
        interaction: discord.Interaction,
        confession_id: int,
        reason: str | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        channel = self.bot.get_channel(
            await self._cfg(interaction.guild.id, "confession_channel_id")
        )

        try:
            target = await self.confessions_service.get_confession_by_id(confession_id)
            if not target:
                return await interaction.followup.send(
                    f"❌ Confession #{confession_id} not found.", ephemeral=True
                )
            confession_id, author_id, title, description, image_url, message_id, created_at = target

            max_id = await self.confessions_service.get_max_confession_id()

            try:
                author = await self.bot.fetch_user(author_id)
            except Exception:
                author = None

            message_deleted = thread_deleted = False
            if channel and message_id:
                try:
                    msg = await channel.fetch_message(message_id)
                    if msg.thread:
                        try:
                            await msg.thread.delete()
                            thread_deleted = True
                        except Exception:
                            pass
                    await msg.delete()
                    message_deleted = True
                except discord.NotFound:
                    message_deleted = True
                except Exception as e:
                    log.error("[confess_delete] Delete error: %s", e)

            reply_count = await self.confessions_service.delete_confession(confession_id)

            updated_confessions = await self.confessions_service.renumber_confessions(confession_id)
            update_count = len(updated_confessions)

            for new_id, msg_id in updated_confessions:
                if channel and msg_id:
                    try:
                        old_msg = await channel.fetch_message(msg_id)
                        new_view = await build_confession_layout_view(new_id)
                        await old_msg.edit(view=new_view)

                        if old_msg.thread:
                            try:
                                await old_msg.thread.edit(name=f"Confession #{new_id}")
                            except Exception as te:
                                log.warning(
                                    f"[confess_delete] Thread rename error #{new_id+1}→#{new_id}: {te}"
                                )

                            try:
                                reply_rows = (
                                    await self.confessions_service.get_confession_reply_ids(new_id)
                                )
                                for reply_db_id, reply_msg_id in reply_rows:
                                    try:
                                        reply_msg = await old_msg.thread.fetch_message(reply_msg_id)
                                        updated_reply_view = await ReplyLayoutView.from_db(
                                            reply_db_id
                                        )
                                        await reply_msg.edit(view=updated_reply_view)
                                    except discord.NotFound:
                                        pass
                                    except Exception as re:
                                        log.warning(
                                            f"[confess_delete] Reply card update error reply#{reply_db_id}: {re}"
                                        )
                            except Exception as re:
                                log.warning(
                                    f"[confess_delete] Reply fetch error for confession #{new_id}: {re}"
                                )

                    except discord.NotFound:
                        pass
                    except Exception as e:
                        log.error(f"[confess_delete] Renumber edit error #{new_id+1}: {e}")

            new_last_id = max_id - 1
            prev = await self.confessions_service.get_confession_by_id(new_last_id)
            if prev and channel and prev[5]:
                try:
                    prev_msg = await channel.fetch_message(prev[5])
                    prev_view = await build_confession_layout_view(
                        prev[0], show_confess_button=True
                    )
                    await prev_msg.edit(view=prev_view)
                except (discord.NotFound, Exception):
                    pass

            await self.confessions_service.update_sqlite_sequence(max_id)

            dm_sent = False
            if author:
                try:
                    dm_view = discord.ui.LayoutView(timeout=None)
                    dm_children = [
                        discord.ui.TextDisplay("## 🗑️ Your Confession Was Deleted"),
                        discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                        discord.ui.TextDisplay(
                            f"Your confession **#{confession_id}** was removed by a moderator."
                        ),
                    ]
                    if title:
                        dm_children.append(discord.ui.TextDisplay(f"**Title:** {title}"))
                    if description:
                        short = description[:200] + ("…" if len(description) > 200 else "")
                        dm_children.append(discord.ui.TextDisplay(f"**Content:** {short}"))
                    dm_children.append(
                        discord.ui.TextDisplay(
                            f"**Reason:** {reason or 'No specific reason provided.'}"
                        )
                    )
                    dm_children.append(
                        discord.ui.TextDisplay(
                            f"-# Deleted at <t:{int(discord.utils.utcnow().timestamp())}:F>"
                        )
                    )
                    dm_view.add_item(
                        discord.ui.Container(
                            *dm_children, accent_color=discord.Color.from_str("#ED4245")
                        )
                    )
                    await author.send(view=dm_view)
                    dm_sent = True
                except (discord.Forbidden, Exception):
                    pass

            result_view = discord.ui.LayoutView(timeout=None)
            result_children = [
                discord.ui.TextDisplay("## ✅ Confession Deleted & Renumbered"),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            ]

            status_lines = [
                "✅ Database entry removed",
                "✅ Message deleted" if message_deleted else "❌ Message not found",
            ]
            if thread_deleted:
                status_lines.append("✅ Thread deleted")
            status_lines.append(
                "✅ Author notified"
                if dm_sent
                else ("❌ DMs disabled" if author else "❌ Could not fetch author")
            )

            details = [
                f"**Deleted ID:** {confession_id}",
                f"**Replies Deleted:** {reply_count}",
                f"**Renumbered:** ✅ {update_count} confession{'s' if update_count != 1 else ''}",
            ]
            if title:
                details.append(f"**Title:** {title}")
            if description:
                short = description[:200] + ("…" if len(description) > 200 else "")
                details.append(f"**Content:** {short}")
            if reason:
                details.append(f"**Reason:** {reason}")

            result_children.append(
                discord.ui.TextDisplay(
                    "\n".join(details + ["", "**Status:**"] + status_lines)
                )
            )
            result_children.append(
                discord.ui.TextDisplay(
                    f"-# Deleted by {interaction.user.display_name} • <t:{int(discord.utils.utcnow().timestamp())}:R>"
                )
            )

            result_view.add_item(
                discord.ui.Container(
                    *result_children, accent_color=discord.Color.from_str("#57F287")
                )
            )
            await interaction.followup.send(view=result_view, ephemeral=True)

            await self._log_deletion(
                interaction,
                confession_id,
                author_id,
                reply_count,
                update_count,
                dm_sent,
                reason,
            )

        except Exception as e:
            traceback.print_exc()
            await interaction.followup.send(
                f"❌ Error deleting confession #{confession_id}: {e}", ephemeral=True
            )

    async def _log_deletion(
        self,
        interaction: discord.Interaction,
        confession_id: int,
        author_id: int,
        reply_count: int,
        renumber_count: int,
        dm_sent: bool,
        reason: str | None,
    ) -> None:
        timestamp = int(discord.utils.utcnow().timestamp())

        admin_children = [
            discord.ui.TextDisplay("## 🗑️ Confession Deleted & Renumbered"),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
        ]
        admin_fields = [
            f"**Deleted ID:** {confession_id}",
            f"**Author:** <@{author_id}>",
            f"**Replies Deleted:** {reply_count}",
            f"**Renumbered:** {renumber_count}",
            f"**DM sent:** {'✅' if dm_sent else '❌'}",
            f"**Deleted by:** {interaction.user.mention} ({interaction.user.id})",
        ]
        if reason:
            admin_fields.append(f"**Reason:** {reason}")
        admin_children.append(discord.ui.TextDisplay("\n".join(admin_fields)))
        admin_children.append(discord.ui.TextDisplay(f"-# Logged at <t:{timestamp}:F>"))
        admin_view = discord.ui.LayoutView(timeout=None)
        admin_view.add_item(
            discord.ui.Container(*admin_children, accent_color=discord.Color.from_str("#FEE75C"))
        )

        log_ch_id = await self._cfg(interaction.guild.id, "log_channel_id")
        log_ch = self.bot.get_channel(log_ch_id) if log_ch_id else None
        if log_ch:
            try:
                await log_ch.send(view=admin_view)
            except Exception as ex:
                log.error("[confess_delete] Admin log error: %s", ex)

        mod_children = [
            discord.ui.TextDisplay("## 🗑️ Confession Deleted & Renumbered"),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
        ]
        mod_fields = [
            f"**Deleted ID:** {confession_id}",
            f"**Author (hash):** `{sha256_user_id(author_id)}`",
            f"**Replies Deleted:** {reply_count}",
            f"**Renumbered:** {renumber_count}",
            f"**DM sent:** {'✅' if dm_sent else '❌'}",
            f"**Deleted by:** {interaction.user.mention}",
        ]
        if reason:
            mod_fields.append(f"**Reason:** {reason}")
        mod_children.append(discord.ui.TextDisplay("\n".join(mod_fields)))
        mod_children.append(discord.ui.TextDisplay(f"-# Mod log at <t:{timestamp}:F>"))
        mod_view = discord.ui.LayoutView(timeout=None)
        mod_view.add_item(
            discord.ui.Container(*mod_children, accent_color=discord.Color.from_str("#FEE75C"))
        )

        mod_log_id = await self._cfg(interaction.guild.id, "mod_log_channel_id")
        mod_ch = self.bot.get_channel(mod_log_id) if mod_log_id else None
        if mod_ch:
            try:
                await mod_ch.send(view=mod_view)
            except Exception as ex:
                log.error("[confess_delete] Mod log error: %s", ex)


setup = Confessions.make_setup()
