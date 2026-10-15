"""Confession modal for the confessions cog."""

from __future__ import annotations

import asyncio
import io
import logging
import traceback

import discord
from discord import ui

from .helpers import sha256_user_id
from .services.confessions_service import ConfessionsService
from .views import ConfessionLayoutView, build_confession_layout_view

log = logging.getLogger("ember.confessions")

async def _get_cfg(bot, guild_id: int, key: str, fallback: int = 0) -> int:
    """Read int config from the Confessions cog's Store."""
    cog = bot.cogs.get("Confessions")
    if cog is None:
        return fallback
    val = await cog.db.guild(guild_id).get(key, default=fallback)
    return val or fallback


class ConfessionModal(ui.Modal, title="Submit a Confession"):
    def __init__(self, bot: discord.Client, guild_id: int = 0) -> None:
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id
        self.user_id: int | None = None
        self.confessions_service = ConfessionsService()

        self.title_input = ui.TextInput(
            label="Title (optional)",
            max_length=100,
            required=False,
            placeholder="Give your confession a title…",
        )
        self.add_item(self.title_input)

        self.description_input = ui.TextInput(
            label="Confession",
            style=discord.TextStyle.paragraph,
            max_length=1000,
            placeholder="Share your thoughts…",
            required=True,
        )
        self.add_item(self.description_input)

        self.image_upload = ui.FileUpload(required=False)
        self.add_item(ui.Label(text="Attach an image (optional)", component=self.image_upload))

    @staticmethod
    async def _delete_after(msg: discord.WebhookMessage, delay: int) -> None:
        await asyncio.sleep(delay)
        try:
            await msg.delete()
        except (discord.NotFound, discord.HTTPException):
            pass

    async def _reupload(self, attachment: discord.Attachment, label: str) -> tuple[bool, str]:
        """Download a FileUpload attachment and re-host it in the media channel."""
        media_ch_id = await _get_cfg(self.bot, self.guild_id, "media_channel_id")
        media_ch = self.bot.get_channel(media_ch_id)
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

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.user_id = interaction.user.id
        await interaction.response.defer(ephemeral=True, thinking=True)

        if await self.confessions_service.is_user_banned(self.user_id):
            msg = await interaction.followup.send(
                "❌ You are banned from submitting confessions.", ephemeral=True, wait=True
            )
            asyncio.create_task(self._delete_after(msg, 8))
            return

        gid = self.guild_id or (interaction.guild.id if interaction.guild else 0)
        daily_limit = await _get_cfg(self.bot, gid, "daily_limit", 3)
        can_confess, reason, current_count = (
            await self.confessions_service.can_user_confess_with_join_check(
                interaction.guild, self.user_id, daily_limit
            )
        )
        if not can_confess:
            msg = await interaction.followup.send(reason, ephemeral=True, wait=True)
            asyncio.create_task(self._delete_after(msg, 8))
            return

        confession_ch_id = await _get_cfg(self.bot, gid, "confession_channel_id")
        channel = self.bot.get_channel(confession_ch_id)
        if not channel:
            msg = await interaction.followup.send(
                "❌ Confession channel not found.", ephemeral=True, wait=True
            )
            asyncio.create_task(self._delete_after(msg, 8))
            return

        try:
            prev_id = await self.confessions_service.get_max_confession_id()

            confession_id = await self.confessions_service.save_confession(
                self.user_id, self.title_input.value, self.description_input.value
            )

            image_url: str | None = None
            attachment: discord.Attachment | None = (
                self.image_upload.values[0] if self.image_upload.values else None
            )
            if attachment is not None:
                proc = await interaction.followup.send(
                    "⏳ Processing image…", ephemeral=True, wait=True
                )
                success, result = await self._reupload(
                    attachment, f"**Media for confession #{confession_id}**"
                )
                try:
                    await proc.delete()
                except Exception:
                    pass

                if success:
                    image_url = result
                    await self.confessions_service.update_confession_image(confession_id, image_url)
                else:
                    warn = await interaction.followup.send(
                        f"⚠️ Image upload failed ({result}). Continuing without media.",
                        ephemeral=True,
                        wait=True,
                    )
                    asyncio.create_task(self._delete_after(warn, 10))

            if prev_id:
                asyncio.create_task(self._strip_prev_confess_button(prev_id, gid))

            view = ConfessionLayoutView(
                confession_id,
                title=self.title_input.value,
                body=self.description_input.value,
                show_confess_button=True,
                image_url=image_url,
            )
            sent = await channel.send(view=view)

            thread: discord.Thread | None = None
            try:
                thread = await sent.create_thread(
                    name=f"Confession #{confession_id}", auto_archive_duration=60
                )
            except discord.HTTPException as e:
                log.warning("[Confessions] Thread error: %s", e)

            await self.confessions_service.update_confession_message_id(confession_id, sent.id)

            self.bot.add_view(view)

            await self._log_confession(confession_id, image_url, gid)

            thread_ref = thread.mention if thread else "*(thread creation failed)*"
            ok = await interaction.followup.send(
                f"✅ Confession **#{confession_id}** is live! {thread_ref}",
                ephemeral=True,
                wait=True,
            )
            asyncio.create_task(self._delete_after(ok, 30))

        except Exception:
            traceback.print_exc()
            err = await interaction.followup.send(
                "❌ An error occurred while submitting your confession.",
                ephemeral=True,
                wait=True,
            )
            asyncio.create_task(self._delete_after(err, 8))

    async def _strip_prev_confess_button(self, confession_id: int, guild_id: int = 0) -> None:
        try:
            message_id = await self.confessions_service.get_confession_message_id(confession_id)
            if not message_id:
                return
            ch_id = await _get_cfg(self.bot, guild_id, "confession_channel_id")
            channel = self.bot.get_channel(ch_id)
            if not channel:
                return
            msg = await channel.fetch_message(message_id)
            view = await build_confession_layout_view(confession_id, show_confess_button=False)
            await msg.edit(view=view)
        except discord.NotFound:
            pass
        except Exception as e:
            log.error("[Confessions] _strip_prev_confess_button error: %s", e)

    async def _log_confession(
        self, confession_id: int, image_url: str | None, guild_id: int = 0
    ) -> None:
        try:
            confession = await self.confessions_service.get_confession_by_id(confession_id)
            if not confession:
                return
            user_id, title, description = confession[1], confession[2], confession[3]
        except Exception as e:
            log.error("[Confessions] log fetch error: %s", e)
            return

        log_ch_id = await _get_cfg(self.bot, guild_id, "log_channel_id")
        log_ch = self.bot.get_channel(log_ch_id) if log_ch_id else None
        if log_ch:
            try:
                user = await self.bot.fetch_user(user_id)
                log_view = discord.ui.LayoutView(timeout=None)
                log_children = [
                    discord.ui.TextDisplay(f"## 📋 Confession #{confession_id}"),
                    discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                    discord.ui.TextDisplay(description or "(no content)"),
                ]
                log_children.append(discord.ui.TextDisplay(f"**User:** {user.mention} ({user.id})"))
                if title:
                    log_children.append(discord.ui.TextDisplay(f"**Title:** {title}"))
                if image_url:
                    log_children.append(
                        discord.ui.MediaGallery(discord.MediaGalleryItem(media=image_url))
                    )
                log_children.append(
                    discord.ui.TextDisplay(
                        f"-# Logged at <t:{int(discord.utils.utcnow().timestamp())}:F>"
                    )
                )
                log_view.add_item(
                    discord.ui.Container(*log_children, accent_color=discord.Color.blurple())
                )
                await log_ch.send(view=log_view)
            except Exception as e:
                log.error("[Confessions] admin log error: %s", e)

        mod_log_id = await _get_cfg(self.bot, guild_id, "mod_log_channel_id")
        mod_ch = self.bot.get_channel(mod_log_id) if mod_log_id else None
        if mod_ch:
            try:
                mod_view = discord.ui.LayoutView(timeout=None)
                mod_children = [
                    discord.ui.TextDisplay(f"## 📋 Confession #{confession_id}"),
                    discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                    discord.ui.TextDisplay(description or "(no content)"),
                ]
                mod_children.append(
                    discord.ui.TextDisplay(f"**User (hashed):** `{sha256_user_id(user_id)}`")
                )
                if title:
                    mod_children.append(discord.ui.TextDisplay(f"**Title:** {title}"))
                if image_url:
                    mod_children.append(
                        discord.ui.MediaGallery(discord.MediaGalleryItem(media=image_url))
                    )
                mod_children.append(
                    discord.ui.TextDisplay(
                        f"-# Mod log at <t:{int(discord.utils.utcnow().timestamp())}:F>"
                    )
                )
                mod_view.add_item(
                    discord.ui.Container(*mod_children, accent_color=discord.Color.blurple())
                )
                await mod_ch.send(view=mod_view)
            except Exception as e:
                log.error("[Confessions] mod log error: %s", e)
