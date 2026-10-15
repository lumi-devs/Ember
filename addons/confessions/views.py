"""UI views and components for the confessions cog."""

from __future__ import annotations

import logging

import discord
from discord import ui

log = logging.getLogger("ember.confessions")


class ConfessionActionRow(ui.ActionRow):
    """Action row containing confession interaction buttons."""

    def __init__(self, confession_id: int, show_confess_button: bool = False) -> None:
        super().__init__()
        self.confession_id = confession_id
        self.show_confess_button = show_confess_button


class ConfessionLayoutView(ui.LayoutView):
    """Main confession card view with action buttons."""

    def __init__(
        self,
        confession_id: int,
        title: str,
        body: str,
        *,
        show_confess_button: bool = False,
        image_url: str | None = None,
    ) -> None:
        super().__init__(timeout=None)
        self.confession_id = confession_id
        self.show_confess_button = show_confess_button
        self.image_url = image_url


class PersistentConfessButton(ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Make a Confession",
            style=discord.ButtonStyle.success,
            custom_id="persistent_confess_button",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        from .helpers import _service, is_user_banned
        from .modals import ConfessionModal

        cog = interaction.client.cogs.get("Confessions")
        db = cog.db if cog is not None else None

        async def _get_allowed_role_ids(guild_id: int) -> list[int]:
            if db is None:
                return []
            return await db.guild(guild_id).get("allowed_role_ids", default=[]) or []

        async def _get_cfg(guild_id: int, key: str, fallback: int = 0) -> int:
            if db is None:
                return fallback
            val = await db.guild(guild_id).get(key, default=fallback)
            return val or fallback

        allowed_role_ids = await _get_allowed_role_ids(interaction.guild.id)
        if not any(role.id in allowed_role_ids for role in interaction.user.roles):
            return await interaction.response.send_message(
                "❌ You need at least LVL 5 to make confessions.", ephemeral=True
            )
        if await is_user_banned(interaction.user.id):
            return await interaction.response.send_message(
                "❌ You are banned from making confessions.", ephemeral=True
            )
        daily_limit = await _get_cfg(interaction.guild.id, "daily_limit", 3)
        if _service is None:
            return await interaction.response.send_message(
                "❌ Service not initialized.", ephemeral=True
            )
        can_confess, reason, _ = await _service.can_user_confess(
            interaction.user.id, daily_limit
        )
        if not can_confess:
            return await interaction.response.send_message(reason, ephemeral=True)
        await interaction.response.send_modal(
            ConfessionModal(interaction.client, interaction.guild.id)
        )


class PersistentReplyButton(ui.Button):
    def __init__(self, confession_id: int) -> None:
        super().__init__(
            label="Reply",
            style=discord.ButtonStyle.secondary,
            custom_id=f"confession_reply_{confession_id}",
        )
        self.confession_id = confession_id

    async def callback(self, interaction: discord.Interaction) -> None:
        from .helpers import _service
        from .reply import ReplyModal

        if _service is None:
            return await interaction.response.send_message(
                "❌ Service not initialized.", ephemeral=True
            )
        confession = await _service.get_confession_by_id(self.confession_id)
        if not confession:
            return await interaction.response.send_message(
                "❌ Confession not found.", ephemeral=True
            )
        message_id, author_id = confession[5], confession[1]
        await interaction.response.send_modal(
            ReplyModal(
                interaction.client,
                self.confession_id,
                message_id,
                author_id,
                guild_id=interaction.guild.id if interaction.guild else 0,
            )
        )


class PersistentReportButton(ui.Button):
    def __init__(self, confession_id: int) -> None:
        super().__init__(
            label="Report",
            style=discord.ButtonStyle.danger,
            custom_id=f"confession_report_{confession_id}",
        )
        self.confession_id = confession_id

    async def callback(self, interaction: discord.Interaction) -> None:
        from .helpers import _service, sha256_user_id, can_user_report

        cog = interaction.client.cogs.get("Confessions")
        db = cog.db if cog is not None else None

        async def _get_cfg(guild_id: int, key: str, fallback: int = 0) -> int:
            if db is None:
                return fallback
            val = await db.guild(guild_id).get(key, default=fallback)
            return val or fallback

        hourly_limit = await _get_cfg(
            interaction.guild.id, "hourly_report_limit", 5
        )
        can_report, reason, _ = await can_user_report(interaction.user.id, hourly_limit)
        if not can_report:
            return await interaction.response.send_message(reason, ephemeral=True)
        try:
            if _service is None:
                return await interaction.response.send_message(
                    "❌ Service not initialized.", ephemeral=True
                )
            confession = await _service.get_confession_by_id(self.confession_id)
            if not confession:
                return await interaction.response.send_message(
                    "❌ Confession not found.", ephemeral=True
                )
            author_id, title, description, image_url = (
                confession[1],
                confession[2],
                confession[3],
                confession[4],
            )

            await _service.save_report(
                "confession", self.confession_id, interaction.user.id
            )

            banned = await _service.is_user_banned(author_id)

            report_view = discord.ui.LayoutView(timeout=None)
            children = [
                discord.ui.TextDisplay(f"## 🚨 Confession Report #{self.confession_id}"),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                discord.ui.TextDisplay(
                    f"**Confession ID:** {self.confession_id}\n"
                    f"**Author ID:** {sha256_user_id(author_id)}\n"
                    f"**Reported by:** {interaction.user} ({interaction.user.id})\n"
                    f"**Banned?** {'Yes' if banned else 'No'}\n"
                    f"**Title:** {title or 'No title'}\n"
                    f"**Content:** {description or 'No content'}"
                ),
            ]
            if image_url:
                children.append(discord.ui.MediaGallery(discord.MediaGalleryItem(media=image_url)))
            children.append(
                discord.ui.TextDisplay(
                    f"-# Reported at <t:{int(discord.utils.utcnow().timestamp())}:F>"
                )
            )
            report_view.add_item(
                discord.ui.Container(*children, accent_color=discord.Color.from_str("#ED4245"))
            )

            report_ch_id = await _get_cfg(interaction.guild.id, "report_channel_id", 0)
            mod_ch = interaction.client.get_channel(report_ch_id)
            if mod_ch:
                await mod_ch.send(view=report_view)
            await interaction.response.send_message("✅ Report submitted.", ephemeral=True)
        except Exception as e:
            log.error("[PersistentReportButton] %s", e)
            await interaction.response.send_message("❌ An error occurred.", ephemeral=True)


async def build_confession_layout_view(
    confession_id: int,
    show_confess_button: bool = False,
) -> ConfessionLayoutView:
    """Reconstruct a ConfessionLayoutView from the DB (edits, restarts)."""
    from .helpers import _service

    confession = await _service.get_confession_by_id(confession_id) if _service else None
    if confession:
        title, body, image_url = confession[2], confession[3], confession[4]
    else:
        title, body, image_url = None, "(deleted)", None
    return ConfessionLayoutView(
        confession_id,
        title=title or "",
        body=body or "",
        show_confess_button=show_confess_button,
        image_url=image_url,
    )
