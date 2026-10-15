# cogs/confessions/reply.py  —  Components V2 (discord.py master / ≥ 2.7)
from __future__ import annotations

import asyncio
import io
import logging
import traceback

import discord
from discord import ui

from .helpers import (
    can_user_reply,
    can_user_report,
    get_parent_reference,
    sha256_user_id,
)
from .services.confessions_service import ConfessionsService

log = logging.getLogger("ember.confessions.reply")

HOURLY_REPLY_LIMIT = 8
HOURLY_REPORT_LIMIT = 5

# Global service instance (will be set by cog)
_service: ConfessionsService | None = None


async def _get_store_cfg(bot: discord.Client, guild_id: int, key: str, fallback: int = 0) -> int:
    """Read an int config value from the Confessions cog's Store."""
    cog = bot.cogs.get("Confessions")
    if cog is None:
        return fallback
    val = await cog.db.guild(guild_id).get(key, default=fallback)
    return val or fallback


def set_service(service: ConfessionsService):
    """Set the global service instance."""
    global _service
    _service = service


# ──────────────────────────────────────────────────────────────────────────────
# REPLY BUTTONS
# ──────────────────────────────────────────────────────────────────────────────


class ReplyToReplyButton(ui.Button):
    def __init__(self, reply_id: int) -> None:
        super().__init__(
            label="💬 Reply",
            style=discord.ButtonStyle.secondary,
            custom_id=f"reply_msg_reply_{reply_id}",
        )
        self.reply_id = reply_id

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            if _service is None:
                return await interaction.response.send_message(
                    "❌ Service not initialized.", ephemeral=True
                )

            # Get reply data from service
            reply = await _service.get_reply_by_id(self.reply_id)
            if not reply:
                return await interaction.response.send_message(
                    "❌ This reply no longer exists.", ephemeral=True
                )

            confession_id = reply["confession_id"]
            reply_author_id = reply["user_id"]
            reply_message_id = reply.get("message_id")

            # Get confession data
            confession = await _service.get_confession_by_id(confession_id)
            if not confession:
                return await interaction.response.send_message(
                    "❌ The parent confession no longer exists.", ephemeral=True
                )

            # confession tuple: (id, user_id, title, description, image_url, message_id, created_at)
            confession_message_id = confession[5]  # message_id
            confession_author_id = confession[1]  # user_id

            await interaction.response.send_modal(
                ReplyToReplyModal(
                    bot=interaction.client,
                    confession_id=confession_id,
                    parent_reply_id=self.reply_id,
                    parent_reply_message_id=reply_message_id,
                    parent_reply_author_id=reply_author_id,
                    confession_author_id=confession_author_id,
                    confession_message_id=confession_message_id,
                    guild_id=interaction.guild.id if interaction.guild else 0,
                )
            )
        except Exception as e:
            log.error("[ReplyToReplyButton] %s", e)
            await interaction.response.send_message(
                "❌ An error occurred. Please try again.", ephemeral=True
            )


class ReportReplyButton(ui.Button):
    def __init__(self, reply_id: int) -> None:
        super().__init__(
            label="🚨 Report",
            style=discord.ButtonStyle.danger,
            custom_id=f"reply_msg_report_{reply_id}",
        )
        self.reply_id = reply_id

    async def callback(self, interaction: discord.Interaction) -> None:
        if _service is None:
            return await interaction.response.send_message(
                "❌ Service not initialized.", ephemeral=True
            )

        can_report, reason, _ = await can_user_report(interaction.user.id, HOURLY_REPORT_LIMIT)
        if not can_report:
            return await interaction.response.send_message(reason, ephemeral=True)

        try:
            # Get reply data
            reply = await _service.get_reply_by_id(self.reply_id)
            if not reply:
                return await interaction.response.send_message(
                    "❌ This reply no longer exists.", ephemeral=True
                )

            confession_id = reply["confession_id"]
            author_id = reply["user_id"]
            description = reply["description"]
            image_url = reply.get("image_url")

            # Save report
            await _service.save_report("reply", self.reply_id, interaction.user.id)

            report_ch_id = await _get_store_cfg(
                interaction.client, interaction.guild.id, "report_channel_id"
            )
            report_ch = interaction.client.get_channel(report_ch_id) if report_ch_id else None
            if report_ch:
                report_view = discord.ui.LayoutView(timeout=None)
                children = [
                    discord.ui.TextDisplay(f"## 🚨 Reply Report — Confession #{confession_id}"),
                    discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                    discord.ui.TextDisplay(
                        f"**Reply ID:** {self.reply_id}\n"
                        f"**Confession:** #{confession_id}\n"
                        f"**Author ID:** {sha256_user_id(author_id)}\n"
                        f"**Reported by:** {interaction.user} ({interaction.user.id})\n"
                        f"**Content:** {description or 'No content'}"
                    ),
                ]
                if image_url:
                    children.append(
                        discord.ui.MediaGallery(discord.MediaGalleryItem(media=image_url))
                    )
                children.append(
                    discord.ui.TextDisplay(
                        f"-# Reported at <t:{int(discord.utils.utcnow().timestamp())}:F>"
                    )
                )
                report_view.add_item(
                    discord.ui.Container(*children, accent_color=discord.Color.from_str("#ED4245"))
                )
                await report_ch.send(view=report_view)

            await interaction.response.send_message("✅ Report submitted.", ephemeral=True)
        except Exception as e:
            log.error("[ReportReplyButton] %s", e)
            await interaction.response.send_message("❌ An error occurred.", ephemeral=True)


# ──────────────────────────────────────────────────────────────────────────────
# REPLY ACTION ROW
# ──────────────────────────────────────────────────────────────────────────────


class ReplyActionRow(ui.ActionRow):
    def __init__(self, reply_id: int) -> None:
        super().__init__()
        self.add_item(ReplyToReplyButton(reply_id))
        self.add_item(ReportReplyButton(reply_id))


# ──────────────────────────────────────────────────────────────────────────────
# REPLY LAYOUT VIEW
# ──────────────────────────────────────────────────────────────────────────────


class ReplyLayoutView(ui.LayoutView):
    def __init__(
        self,
        reply_id: int,
        confession_id: int,
        body: str,
        *,
        is_op: bool = False,
        image_url: str | None = None,
        parent_reference: str | None = None,
        parent_quote: str | None = None,
    ) -> None:
        super().__init__(timeout=None)

        accent = discord.Color.from_str("#FEE75C") if is_op else discord.Color.from_str("#AB766F")
        display_body = body if body and body.strip() else "*(empty)*"

        children: list = [
            ui.TextDisplay(f"## ↩️ Reply to Confession #{confession_id}"),
            ui.Separator(spacing=discord.SeparatorSpacing.small),
        ]

        if parent_quote:
            children.append(ui.TextDisplay(parent_quote))
            children.append(
                ui.Separator(spacing=discord.SeparatorSpacing.small),
            )

        children.append(ui.TextDisplay(display_body))

        if image_url:
            children.append(ui.MediaGallery(discord.MediaGalleryItem(media=image_url)))

        children.extend(
            [
                ui.Separator(spacing=discord.SeparatorSpacing.small),
            ]
        )

        if is_op:
            children.append(ui.TextDisplay("-# 👑 OP"))

        children.append(ReplyActionRow(reply_id))

        self.add_item(ui.Container(*children, accent_color=accent))

    @classmethod
    async def from_db(cls, reply_id: int) -> ReplyLayoutView:
        """Reconstruct a ReplyLayoutView from the DB (bot restart persistence)."""
        if _service is None:
            return cls(reply_id, 0, "(service error)")

        row = await _service.get_reply_by_id(reply_id)
        if not row:
            return cls(reply_id, 0, "(deleted)")

        confession_id = row["confession_id"]
        user_id = row["user_id"]
        description = row["description"]
        image_url = row.get("image_url")

        # Get confession to check if OP
        confession = await _service.get_confession_by_id(confession_id)
        if not confession:
            is_op = False
        else:
            is_op = bool(confession[1] == user_id)  # confession[1] = user_id

        parent_ref, parent_quote = await get_parent_reference(reply_id)

        return cls(
            reply_id,
            confession_id=confession_id,
            body=description or "",
            is_op=is_op,
            image_url=image_url,
            parent_reference=parent_ref,
            parent_quote=parent_quote,
        )


# ──────────────────────────────────────────────────────────────────────────────
# SHARED MODAL MIXIN
# ──────────────────────────────────────────────────────────────────────────────


class _MediaMixin:
    """Shared helpers for both ReplyModal and ReplyToReplyModal."""

    bot: discord.Client
    confession_id: int
    guild_id: int

    @staticmethod
    async def _delete_after(msg: discord.WebhookMessage, delay: int) -> None:
        await asyncio.sleep(delay)
        try:
            await msg.delete()
        except (discord.NotFound, discord.HTTPException):
            pass

    async def _reupload(self, attachment: discord.Attachment, label: str) -> tuple[bool, str]:
        """Re-host a FileUpload attachment via the media channel."""
        media_ch_id = await _get_store_cfg(self.bot, self.guild_id, "media_channel_id")
        media_ch = self.bot.get_channel(media_ch_id) if media_ch_id else None
        if not media_ch:
            return False, "Media channel not found"
        try:
            data = await attachment.read()
            if not data:
                return False, "Empty attachment"
            file = discord.File(io.BytesIO(data), filename=attachment.filename)
            msg = await media_ch.send(label, file=file)
            await asyncio.sleep(1)
            url = msg.attachments[0].url if msg.attachments else None
            return (True, url) if url else (False, "CDN URL unavailable")
        except Exception as e:
            return False, str(e)


# ──────────────────────────────────────────────────────────────────────────────
# REPLY MODAL  (reply to a confession directly)
# ──────────────────────────────────────────────────────────────────────────────


class ReplyModal(_MediaMixin, ui.Modal, title="Anonymous Reply"):
    def __init__(
        self,
        bot: discord.Client,
        confession_id: int,
        confession_message_id: int,
        confession_author_id: int,
        guild_id: int = 0,
    ) -> None:
        super().__init__()
        self.bot = bot
        self.confession_id = confession_id
        self.confession_message_id = confession_message_id
        self.confession_author_id = confession_author_id
        self.guild_id = guild_id
        self.user_id: int | None = None
        self.reply_text: str | None = None

        # Add components with labels (V2 modal pattern)
        self.reply_input = ui.TextInput(
            label="Reply",
            style=discord.TextStyle.paragraph,
            max_length=500,
            placeholder="Type your reply here…",
            required=True,
        )
        self.add_item(self.reply_input)

        self.image_upload = ui.FileUpload(required=False)
        self.add_item(ui.Label(text="Attach an image (optional)", component=self.image_upload))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.user_id = interaction.user.id
        self.reply_text = self.reply_input.value
        if not self.guild_id and interaction.guild:
            self.guild_id = interaction.guild.id
        await interaction.response.defer(ephemeral=True, thinking=True)

        if _service is None:
            err = await interaction.followup.send(
                "❌ Service not initialized.", ephemeral=True, wait=True
            )
            asyncio.create_task(self._delete_after(err, 8))
            return

        can_reply, reason, current_count = await can_user_reply(self.user_id, HOURLY_REPLY_LIMIT)
        if not can_reply:
            msg = await interaction.followup.send(reason, ephemeral=True, wait=True)
            asyncio.create_task(self._delete_after(msg, 8))
            return

        # Store remaining info to show after success
        _remaining = HOURLY_REPLY_LIMIT - (current_count + 1)

        # Process optional image upload
        image_url: str | None = None
        attachment: discord.Attachment | None = (
            self.image_upload.values[0] if self.image_upload.values else None
        )
        if attachment is not None:
            proc = await interaction.followup.send(
                "⏳ Processing image…", ephemeral=True, wait=True
            )
            success, result = await self._reupload(
                attachment,
                f"**Media for reply to confession #{self.confession_id}**",
            )
            try:
                await proc.delete()
            except Exception:
                pass
            if success:
                image_url = result
            else:
                warn = await interaction.followup.send(
                    f"⚠️ Image upload failed ({result}). Posting reply without media.",
                    ephemeral=True,
                    wait=True,
                )
                asyncio.create_task(self._delete_after(warn, 10))

        await self._finalize(interaction, image_url, current_count)

    async def _finalize(
        self,
        interaction: discord.Interaction,
        image_url: str | None,
        current_count: int,
    ) -> None:
        try:
            gid = self.guild_id or (interaction.guild.id if interaction.guild else 0)
            confession_ch_id = await _get_store_cfg(self.bot, gid, "confession_channel_id")
            channel = self.bot.get_channel(confession_ch_id) if confession_ch_id else None
            if not channel:
                err = await interaction.followup.send(
                    "❌ Confession channel not found.", ephemeral=True, wait=True
                )
                asyncio.create_task(self._delete_after(err, 8))
                return

            is_op = self.user_id == self.confession_author_id

            try:
                conf_msg = await channel.fetch_message(self.confession_message_id)
            except discord.NotFound:
                err = await interaction.followup.send(
                    "❌ Original confession not found.", ephemeral=True, wait=True
                )
                asyncio.create_task(self._delete_after(err, 8))
                return
            except discord.Forbidden:
                err = await interaction.followup.send(
                    "❌ No permission to access confession.", ephemeral=True, wait=True
                )
                asyncio.create_task(self._delete_after(err, 8))
                return

            # Ensure thread exists
            thread = conf_msg.thread
            if not thread:
                for name in (
                    f"Confession #{self.confession_id} Replies",
                    f"Replies #{self.confession_id}",
                ):
                    try:
                        thread = await conf_msg.create_thread(name=name, auto_archive_duration=60)
                        break
                    except discord.HTTPException:
                        continue
                if not thread:
                    err = await interaction.followup.send(
                        "❌ Could not create discussion thread.", ephemeral=True, wait=True
                    )
                    asyncio.create_task(self._delete_after(err, 8))
                    return

            # Save reply via service
            reply_db_id = await _service.save_reply(
                confession_id=self.confession_id,
                author_id=self.user_id,
                content=self.reply_text,
                parent_reply_id=None,
            )
            # Update with image if present
            if image_url:
                await _service.db.execute(
                    "UPDATE replies SET image_url=? WHERE id=?", (image_url, reply_db_id)
                )

            parent_ref, parent_quote = await get_parent_reference(reply_db_id)

            # Build V2 card and send
            view = ReplyLayoutView(
                reply_db_id,
                confession_id=self.confession_id,
                body=self.reply_text,
                is_op=is_op,
                image_url=image_url,
                parent_reference=parent_ref,
                parent_quote=parent_quote,
            )
            self.bot.add_view(view)
            sent_msg = await thread.send(view=view)

            # Persist message_id
            await _service.update_reply_message_id(reply_db_id, sent_msg.id)

            await self._log_reply(reply_db_id, image_url, is_op)

            if not is_op:
                asyncio.create_task(self._notify_author(conf_msg, sent_msg))

            remaining = HOURLY_REPLY_LIMIT - (current_count + 1)
            warning = (
                f"\n⚠️ Only {remaining} repl{'y' if remaining == 1 else 'ies'} left this hour!"
                if remaining <= 2
                else ""
            )
            ok = await interaction.followup.send(
                f"✅ Reply posted!{warning}\n📝 {sent_msg.jump_url}",
                ephemeral=True,
                wait=True,
            )
            asyncio.create_task(self._delete_after(ok, 30))

        except Exception:
            traceback.print_exc()
            err = await interaction.followup.send(
                "❌ An error occurred while posting your reply.", ephemeral=True, wait=True
            )
            asyncio.create_task(self._delete_after(err, 8))

    async def _log_reply(self, reply_db_id: int, image_url: str | None, is_op: bool) -> None:
        parent_ref, parent_quote = await get_parent_reference(reply_db_id)
        color = discord.Color.from_str("#FEE75C") if is_op else discord.Color.from_str("#57F287")

        log_ch_id = await _get_store_cfg(self.bot, self.guild_id, "log_channel_id")
        log_ch = self.bot.get_channel(log_ch_id) if log_ch_id else None
        if log_ch:
            try:
                user = await self.bot.fetch_user(self.user_id)
                log_view = discord.ui.LayoutView(timeout=None)
                log_children = [
                    discord.ui.TextDisplay(f"## 💬 Reply — Confession #{self.confession_id}"),
                    discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                    discord.ui.TextDisplay(self.reply_text),
                    discord.ui.TextDisplay(f"**Replier:** {user.mention} ({user.id})"),
                    discord.ui.TextDisplay(f"**Parent:** {parent_ref}"),
                    discord.ui.TextDisplay(f"**Reply DB ID:** {reply_db_id}"),
                ]
                if image_url:
                    log_children.append(
                        discord.ui.MediaGallery(discord.MediaGalleryItem(media=image_url))
                    )
                log_children.append(
                    discord.ui.TextDisplay(
                        f"-# Logged at <t:{int(discord.utils.utcnow().timestamp())}:F>"
                    )
                )
                log_view.add_item(discord.ui.Container(*log_children, accent_color=color))
                await log_ch.send(view=log_view)
            except Exception as e:
                log.error("[ReplyModal] admin log error: %s", e)

        mod_log_id = await _get_store_cfg(self.bot, self.guild_id, "mod_log_channel_id")
        mod_ch = self.bot.get_channel(mod_log_id) if mod_log_id else None
        if mod_ch:
            try:
                mod_view = discord.ui.LayoutView(timeout=None)
                mod_children = [
                    discord.ui.TextDisplay(f"## 💬 Reply — Confession #{self.confession_id}"),
                    discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                    discord.ui.TextDisplay(self.reply_text),
                    discord.ui.TextDisplay(f"**Replier (hash):** `{sha256_user_id(self.user_id)}`"),
                    discord.ui.TextDisplay(f"**Parent:** {parent_ref}"),
                    discord.ui.TextDisplay(f"**Reply DB ID:** {reply_db_id}"),
                ]
                if image_url:
                    mod_children.append(
                        discord.ui.MediaGallery(discord.MediaGalleryItem(media=image_url))
                    )
                mod_children.append(
                    discord.ui.TextDisplay(
                        f"-# Mod log at <t:{int(discord.utils.utcnow().timestamp())}:F>"
                    )
                )
                mod_view.add_item(discord.ui.Container(*mod_children, accent_color=color))
                await mod_ch.send(view=mod_view)
            except Exception as e:
                log.error("[ReplyModal] mod log error: %s", e)

    async def _notify_author(
        self, confession_msg: discord.Message, reply_msg: discord.Message
    ) -> None:
        try:
            if _service is None:
                return

            confession = await _service.get_confession_by_id(self.confession_id)
            if not confession:
                return

            author_id = confession[1]  # user_id
            if author_id == self.user_id:
                return

            author = await self.bot.fetch_user(author_id)
            author_view = discord.ui.LayoutView(timeout=None)
            author_children = [
                discord.ui.TextDisplay("## 💬 Someone Replied to Your Confession"),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                discord.ui.TextDisplay(
                    f'"{self.reply_text[:100]}{"…" if len(self.reply_text) > 100 else ""}"'
                ),
                discord.ui.TextDisplay(f"**🔗 View Reply:** [Click here]({reply_msg.jump_url})"),
                discord.ui.TextDisplay("-# All identities remain anonymous."),
            ]
            author_view.add_item(
                discord.ui.Container(*author_children, accent_color=discord.Color.blurple())
            )
            await author.send(view=author_view)
        except (discord.Forbidden, Exception):
            pass


# ──────────────────────────────────────────────────────────────────────────────
# REPLY-TO-REPLY MODAL
# ──────────────────────────────────────────────────────────────────────────────


class ReplyToReplyModal(_MediaMixin, ui.Modal, title="Reply to Reply"):
    def __init__(
        self,
        bot: discord.Client,
        confession_id: int,
        parent_reply_id: int,
        parent_reply_message_id: int | None,
        parent_reply_author_id: int,
        confession_author_id: int,
        confession_message_id: int,
        guild_id: int = 0,
    ) -> None:
        super().__init__()
        self.bot = bot
        self.confession_id = confession_id
        self.parent_reply_id = parent_reply_id
        self.parent_reply_message_id = parent_reply_message_id
        self.parent_reply_author_id = parent_reply_author_id
        self.confession_author_id = confession_author_id
        self.confession_message_id = confession_message_id
        self.guild_id = guild_id
        self.user_id: int | None = None
        self.reply_text: str | None = None

        # Add components with labels (V2 modal pattern)
        self.reply_input = ui.TextInput(
            label="Reply",
            style=discord.TextStyle.paragraph,
            max_length=500,
            placeholder="Type your reply here…",
            required=True,
        )
        self.add_item(self.reply_input)

        self.image_upload = ui.FileUpload(required=False)
        self.add_item(ui.Label(text="Attach an image (optional)", component=self.image_upload))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.user_id = interaction.user.id
        self.reply_text = self.reply_input.value
        if not self.guild_id and interaction.guild:
            self.guild_id = interaction.guild.id
        await interaction.response.defer(ephemeral=True, thinking=True)

        if _service is None:
            err = await interaction.followup.send(
                "❌ Service not initialized.", ephemeral=True, wait=True
            )
            asyncio.create_task(self._delete_after(err, 8))
            return

        can_reply, reason, current_count = await can_user_reply(self.user_id, HOURLY_REPLY_LIMIT)
        if not can_reply:
            msg = await interaction.followup.send(reason, ephemeral=True, wait=True)
            asyncio.create_task(self._delete_after(msg, 8))
            return

        image_url: str | None = None
        attachment: discord.Attachment | None = (
            self.image_upload.values[0] if self.image_upload.values else None
        )
        if attachment is not None:
            proc = await interaction.followup.send(
                "⏳ Processing image…", ephemeral=True, wait=True
            )
            success, result = await self._reupload(
                attachment,
                f"**Media for reply to confession #{self.confession_id}**",
            )
            try:
                await proc.delete()
            except Exception:
                pass
            if success:
                image_url = result
            else:
                warn = await interaction.followup.send(
                    f"⚠️ Image upload failed ({result}). Posting reply without media.",
                    ephemeral=True,
                    wait=True,
                )
                asyncio.create_task(self._delete_after(warn, 10))

        await self._finalize(interaction, image_url, current_count)

    async def _finalize(
        self,
        interaction: discord.Interaction,
        image_url: str | None,
        current_count: int,
    ) -> None:
        try:
            gid = self.guild_id or (interaction.guild.id if interaction.guild else 0)
            confession_ch_id = await _get_store_cfg(self.bot, gid, "confession_channel_id")
            channel = self.bot.get_channel(confession_ch_id) if confession_ch_id else None
            if not channel:
                err = await interaction.followup.send(
                    "❌ Confession channel not found.", ephemeral=True, wait=True
                )
                asyncio.create_task(self._delete_after(err, 8))
                return

            is_op = self.user_id == self.confession_author_id

            try:
                conf_msg = await channel.fetch_message(self.confession_message_id)
            except discord.NotFound:
                err = await interaction.followup.send(
                    "❌ Original confession not found.", ephemeral=True, wait=True
                )
                asyncio.create_task(self._delete_after(err, 8))
                return

            thread = conf_msg.thread
            if not thread:
                err = await interaction.followup.send(
                    "❌ Thread not found for this confession.", ephemeral=True, wait=True
                )
                asyncio.create_task(self._delete_after(err, 8))
                return

            # Discord native reply reference (visual threading in the thread)
            parent_msg: discord.Message | None = None
            if self.parent_reply_message_id:
                try:
                    parent_msg = await thread.fetch_message(self.parent_reply_message_id)
                except discord.NotFound:
                    pass

            # Save reply via service
            reply_db_id = await _service.save_reply(
                confession_id=self.confession_id,
                author_id=self.user_id,
                content=self.reply_text,
                parent_reply_id=self.parent_reply_id,
            )
            # Update with image if present
            if image_url:
                await _service.db.execute(
                    "UPDATE replies SET image_url=? WHERE id=?", (image_url, reply_db_id)
                )

            parent_ref, parent_quote = await get_parent_reference(reply_db_id)

            # Build V2 card
            view = ReplyLayoutView(
                reply_db_id,
                confession_id=self.confession_id,
                body=self.reply_text,
                is_op=is_op,
                image_url=image_url,
                parent_reference=parent_ref,
                parent_quote=parent_quote,
            )
            self.bot.add_view(view)

            # Send with Discord's native reply reference when available
            send_kwargs: dict = {"view": view}
            if parent_msg:
                send_kwargs["reference"] = parent_msg
                send_kwargs["mention_author"] = False
            sent_msg = await thread.send(**send_kwargs)

            # Persist message_id
            await _service.update_reply_message_id(reply_db_id, sent_msg.id)

            await self._log_reply(reply_db_id, image_url, is_op)
            asyncio.create_task(self._notify_replier(sent_msg, is_op))

            remaining = HOURLY_REPLY_LIMIT - (current_count + 1)
            warning = (
                f"\n⚠️ Only {remaining} repl{'y' if remaining == 1 else 'ies'} left this hour!"
                if remaining <= 2
                else ""
            )
            ok = await interaction.followup.send(
                f"✅ Reply posted!{warning}\n📝 {sent_msg.jump_url}",
                ephemeral=True,
                wait=True,
            )
            asyncio.create_task(self._delete_after(ok, 30))

        except Exception:
            traceback.print_exc()
            err = await interaction.followup.send(
                "❌ An error occurred while posting your reply.", ephemeral=True, wait=True
            )
            asyncio.create_task(self._delete_after(err, 8))

    async def _log_reply(
        self,
        reply_db_id: int,
        image_url: str | None,
        is_op: bool,
    ) -> None:
        color = discord.Color.from_str("#FEE75C") if is_op else discord.Color.from_str("#AB766F")
        title = f"Reply — Confession #{self.confession_id}"

        parent_ref, parent_quote = await get_parent_reference(reply_db_id)
        timestamp = int(discord.utils.utcnow().timestamp())

        log_ch_id = await _get_store_cfg(self.bot, self.guild_id, "log_channel_id")
        log_ch = self.bot.get_channel(log_ch_id) if log_ch_id else None
        if log_ch:
            try:
                user = await self.bot.fetch_user(self.user_id)
                log_view = discord.ui.LayoutView(timeout=None)
                log_children = [
                    discord.ui.TextDisplay(f"## {title}"),
                    discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                    discord.ui.TextDisplay(self.reply_text),
                    discord.ui.TextDisplay(f"**Replier:** {user.mention} ({user.id})"),
                    discord.ui.TextDisplay(f"**Parent:** {parent_ref}"),
                    discord.ui.TextDisplay(f"**Reply DB ID:** {reply_db_id}"),
                    discord.ui.TextDisplay(f"-# Logged at <t:{timestamp}:F>"),
                ]
                if image_url:
                    log_children.append(
                        discord.ui.MediaGallery(discord.MediaGalleryItem(media=image_url))
                    )
                log_view.add_item(discord.ui.Container(*log_children, accent_color=color))
                await log_ch.send(view=log_view)
            except Exception as e:
                log.error("[ReplyModal] admin log error: %s", e)

        mod_log_id = await _get_store_cfg(self.bot, self.guild_id, "mod_log_channel_id")
        mod_ch = self.bot.get_channel(mod_log_id) if mod_log_id else None
        if mod_ch:
            try:
                mod_view = discord.ui.LayoutView(timeout=None)
                mod_children = [
                    discord.ui.TextDisplay(f"## {title}"),
                    discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                    discord.ui.TextDisplay(self.reply_text),
                    discord.ui.TextDisplay(f"**Replier (hash):** `{sha256_user_id(self.user_id)}`"),
                    discord.ui.TextDisplay(f"**Parent:** {parent_ref}"),
                    discord.ui.TextDisplay(f"**Reply DB ID:** {reply_db_id}"),
                    discord.ui.TextDisplay(f"-# Mod log at <t:{timestamp}:F>"),
                ]
                if image_url:
                    mod_children.append(
                        discord.ui.MediaGallery(discord.MediaGalleryItem(media=image_url))
                    )
                mod_view.add_item(discord.ui.Container(*mod_children, accent_color=color))
                await mod_ch.send(view=mod_view)
            except Exception as e:
                log.error("[ReplyModal] mod log error: %s", e)

    async def _notify_replier(self, new_reply_msg: discord.Message, is_op: bool) -> None:
        """DM the author of the parent reply."""
        try:
            if self.parent_reply_author_id == self.user_id:
                return

            if _service is None:
                return

            reply = await _service.get_reply_by_id(self.parent_reply_id)
            if not reply:
                return

            parent_reply_author_id = reply["user_id"]
            if parent_reply_author_id == self.user_id:
                return

            replier = await self.bot.fetch_user(parent_reply_author_id)
            title = (
                "👑 The OP Replied to Your Reply!" if is_op else "💬 Someone Replied to Your Reply"
            )
            color = discord.Color.from_str("#FEE75C") if is_op else discord.Color.blurple()
            view = discord.ui.LayoutView(timeout=None)
            children = [
                discord.ui.TextDisplay(f"## {title}"),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                discord.ui.TextDisplay(
                    f"{'**OP** replied' if is_op else 'Someone replied'}: "
                    f'"{self.reply_text[:100]}{"…" if len(self.reply_text) > 100 else ""}"'
                ),
                discord.ui.TextDisplay(
                    f"**🔗 View Reply:** [Click here]({new_reply_msg.jump_url})"
                ),
                discord.ui.TextDisplay(
                    "The confession author (OP) replied to you!"
                    if is_op
                    else "All identities remain anonymous."
                ),
            ]
            view.add_item(discord.ui.Container(*children, accent_color=color))
            await replier.send(view=view)
        except (discord.Forbidden, Exception):
            pass
