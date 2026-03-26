# cogs/confessions/reply.py  —  Components V2 (discord.py master / ≥ 2.7)
from __future__ import annotations

import io
import os
import asyncio
import traceback

import aiosqlite
import discord
from discord import ui

from .helpers import (
    DB_PATH,
    can_user_reply,
    get_hourly_reply_count,
    sha256_user_id,
    LOG_CHANNEL_ID,
    MOD_CONFESSION_LOGS_CHANNEL_ID,
    REPORT_CHANNEL_ID,
)

CONFESSION_CHANNEL_ID = int(os.getenv("CONFESSION_CHANNEL_ID"))
MEDIA_CHANNEL_ID      = int(os.getenv("MEDIA_CHANNEL_ID"))
HOURLY_REPLY_LIMIT    = int(os.getenv("HOURLY_REPLY_LIMIT", 8))
HOURLY_REPORT_LIMIT   = int(os.getenv("HOURLY_REPORT_LIMIT", 5))


# ──────────────────────────────────────────────────────────────────────────────
# PARENT REFERENCE HELPER
# ──────────────────────────────────────────────────────────────────────────────

async def get_parent_reference(reply_id: int) -> tuple[str, str | None]:
    """
    Return (reference_header, parent_quote).
    reference_header: "🔗 In reply to Confession #42" or "🔗 In reply to Reply #8"
    parent_quote: First 150 chars of parent confession/reply (or None)
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                "SELECT confession_id, parent_reply_id FROM replies WHERE id=?",
                (reply_id,),
            )
            row = await cur.fetchone()
            if not row:
                return "🔗 In reply to unknown", None
            
            confession_id, parent_reply_id = row
            
            # If replying to a confession directly
            if parent_reply_id is None:
                cur = await db.execute(
                    "SELECT description FROM confessions WHERE id=?",
                    (confession_id,),
                )
                conf_row = await cur.fetchone()
                if conf_row:
                    text = conf_row[0]
                    quote = text[:150] + "…" if len(text) > 150 else text
                    return f"🔗 In reply to Confession #{confession_id}", quote
                return f"🔗 In reply to Confession #{confession_id}", None
            
            # If replying to another reply
            else:
                cur = await db.execute(
                    "SELECT description FROM replies WHERE id=?",
                    (parent_reply_id,),
                )
                reply_row = await cur.fetchone()
                if reply_row:
                    text = reply_row[0]
                    quote = text[:150] + "…" if len(text) > 150 else text
                    return f"🔗 In reply to Reply #{parent_reply_id}", quote
                return f"🔗 In reply to Reply #{parent_reply_id}", None
                
    except Exception as e:
        print(f"[get_parent_reference] Error: {e}")
        return "🔗 In reply to unknown", None


# ──────────────────────────────────────────────────────────────────────────────
# PERSISTENT REPLY BUTTONS
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
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute(
                    "SELECT confession_id, user_id, message_id FROM replies WHERE id=?",
                    (self.reply_id,),
                )
                reply = await cur.fetchone()
                if not reply:
                    return await interaction.response.send_message(
                        "❌ This reply no longer exists.", ephemeral=True
                    )
                confession_id, reply_author_id, reply_message_id = reply

                cur = await db.execute(
                    "SELECT message_id, user_id FROM confessions WHERE id=?",
                    (confession_id,),
                )
                confession = await cur.fetchone()
                if not confession:
                    return await interaction.response.send_message(
                        "❌ The parent confession no longer exists.", ephemeral=True
                    )
                confession_message_id, confession_author_id = confession

            await interaction.response.send_modal(
                ReplyToReplyModal(
                    bot=interaction.client,
                    confession_id=confession_id,
                    parent_reply_id=self.reply_id,
                    parent_reply_message_id=reply_message_id,
                    parent_reply_author_id=reply_author_id,
                    confession_author_id=confession_author_id,
                    confession_message_id=confession_message_id,
                )
            )
        except Exception as e:
            print(f"[ReplyToReplyButton] {e}")
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
        from .helpers import can_user_report
        can_report, reason, _ = await can_user_report(interaction.user.id, HOURLY_REPORT_LIMIT)
        if not can_report:
            return await interaction.response.send_message(reason, ephemeral=True)
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute(
                    "SELECT confession_id, user_id, description, image_url FROM replies WHERE id=?",
                    (self.reply_id,),
                )
                row = await cur.fetchone()
                if not row:
                    return await interaction.response.send_message(
                        "❌ This reply no longer exists.", ephemeral=True
                    )
                confession_id, author_id, description, image_url = row
                await db.execute(
                    "INSERT INTO reports (target_type, target_id, reporter_id) VALUES (?, ?, ?)",
                    ("reply", self.reply_id, interaction.user.id),
                )
                await db.commit()

            report_ch = interaction.client.get_channel(REPORT_CHANNEL_ID)
            if report_ch:
                embed = discord.Embed(
                    title=f"🚨 Reply Report — Confession #{confession_id}",
                    color=discord.Color.red(),
                    timestamp=discord.utils.utcnow(),
                )
                embed.add_field(name="Reply ID",    value=str(self.reply_id),                          inline=True)
                embed.add_field(name="Confession",  value=f"#{confession_id}",                         inline=True)
                embed.add_field(name="Author ID",   value=sha256_user_id(author_id),                   inline=False)
                embed.add_field(name="Reported by", value=f"{interaction.user} ({interaction.user.id})", inline=False)
                embed.add_field(name="Content",     value=description or "No content",                 inline=False)
                if image_url:
                    embed.set_image(url=image_url)
                await report_ch.send(embed=embed)

            await interaction.response.send_message("✅ Report submitted.", ephemeral=True)
        except Exception as e:
            print(f"[ReportReplyButton] {e}")
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

        accent = discord.Color.gold() if is_op else discord.Color.from_str("#AB766F")

        # Header shows parent reference
        header_parts = [parent_reference or f"🔗 In reply to Confession #{confession_id}"]
        
        # If there's a parent quote, show it in a smaller format
        children: list = [
            ui.TextDisplay(f"## ↩️ Reply to Confession #{confession_id}"),
            ui.Separator(spacing=discord.SeparatorSpacing.small),
        ]
        
        if parent_quote:
            children.append(
                ui.TextDisplay(f"-# > {parent_quote}")
            )
            children.append(
                ui.Separator(spacing=discord.SeparatorSpacing.small),
            )
        
        children.append(
            ui.TextDisplay(body or "*(empty)*"),
        )

        if image_url:
            children.append(
                ui.MediaGallery(discord.MediaGalleryItem(media=image_url))
            )

        footer = "-# 👑 OP" if is_op else "-# Reply"
        
        children.extend([
            ui.Separator(spacing=discord.SeparatorSpacing.small),
            ui.TextDisplay(footer),
            ReplyActionRow(reply_id),
        ])

        self.add_item(ui.Container(*children, accent_color=accent))

    @classmethod
    async def from_db(cls, reply_id: int) -> "ReplyLayoutView":
        """Reconstruct a ReplyLayoutView from the DB (bot restart persistence)."""
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                "SELECT confession_id, parent_reply_id, user_id, description, image_url "
                "FROM replies WHERE id=?",
                (reply_id,),
            )
            row = await cur.fetchone()

        if not row:
            return cls(reply_id, 0, "(deleted)")

        confession_id, parent_reply_id, user_id, description, image_url = row

        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                "SELECT user_id FROM confessions WHERE id=?", (confession_id,)
            )
            conf_row = await cur.fetchone()
        is_op = bool(conf_row and conf_row[0] == user_id)

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

    @staticmethod
    async def _delete_after(msg: discord.WebhookMessage, delay: int) -> None:
        await asyncio.sleep(delay)
        try:
            await msg.delete()
        except (discord.NotFound, discord.HTTPException):
            pass

    async def _reupload(
        self, attachment: discord.Attachment, label: str
    ) -> tuple[bool, str]:
        """Re-host a FileUpload attachment via the media channel."""
        media_ch = self.bot.get_channel(MEDIA_CHANNEL_ID)
        if not media_ch:
            return False, "Media channel not found"
        try:
            data = await attachment.read()
            if not data:
                return False, "Empty attachment"
            file = discord.File(io.BytesIO(data), filename=attachment.filename)
            msg  = await media_ch.send(label, file=file)
            await asyncio.sleep(1)
            url  = msg.attachments[0].url if msg.attachments else None
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
    ) -> None:
        super().__init__()
        self.bot                   = bot
        self.confession_id         = confession_id
        self.confession_message_id = confession_message_id
        self.confession_author_id  = confession_author_id
        self.user_id: int | None   = None
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
        self.add_item(ui.Label(
            text="Attach an image (optional)",
            component=self.image_upload
        ))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.user_id    = interaction.user.id
        self.reply_text = self.reply_input.value
        await interaction.response.defer(ephemeral=True, thinking=True)

        can_reply, reason, current_count = await can_user_reply(
            self.user_id, HOURLY_REPLY_LIMIT
        )
        if not can_reply:
            msg = await interaction.followup.send(reason, ephemeral=True, wait=True)
            asyncio.create_task(self._delete_after(msg, 8))
            return

        # Store remaining info to show after success
        remaining = HOURLY_REPLY_LIMIT - (current_count + 1)
        warning = f"\n⚠️ **{remaining}** repl{'y' if remaining == 1 else 'ies'} remaining this hour!" if remaining <= 2 else ""

        # Process optional image upload
        image_url: str | None = None
        attachment: discord.Attachment | None = self.image_upload.values[0] if self.image_upload.values else None
        if attachment is not None:
            proc = await interaction.followup.send("⏳ Processing image…", ephemeral=True, wait=True)
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
                    ephemeral=True, wait=True,
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
            channel = self.bot.get_channel(CONFESSION_CHANNEL_ID)
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
                        thread = await conf_msg.create_thread(
                            name=name, auto_archive_duration=60
                        )
                        break
                    except discord.HTTPException:
                        continue
                if not thread:
                    err = await interaction.followup.send(
                        "❌ Could not create discussion thread.", ephemeral=True, wait=True
                    )
                    asyncio.create_task(self._delete_after(err, 8))
                    return

            # Insert into DB
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute(
                    "INSERT INTO replies (confession_id, user_id, description, image_url) "
                    "VALUES (?, ?, ?, ?)",
                    (self.confession_id, self.user_id, self.reply_text, image_url),
                )
                await db.commit()
                reply_db_id = cur.lastrowid

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
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "UPDATE replies SET message_id=? WHERE id=?",
                    (sent_msg.id, reply_db_id),
                )
                await db.commit()

            await self._log_reply(reply_db_id, image_url, is_op)

            if not is_op:
                asyncio.create_task(self._notify_author(conf_msg, sent_msg))

            remaining = HOURLY_REPLY_LIMIT - (current_count + 1)
            warning   = (
                f"\n⚠️ Only {remaining} repl{'y' if remaining == 1 else 'ies'} left this hour!"
                if remaining <= 2 else ""
            )
            ok = await interaction.followup.send(
                f"✅ Reply posted!{warning}\n📝 {sent_msg.jump_url}",
                ephemeral=True, wait=True,
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
        color = discord.Color.gold() if is_op else discord.Color.green()

        log_ch = self.bot.get_channel(LOG_CHANNEL_ID)
        if log_ch:
            try:
                user  = await self.bot.fetch_user(self.user_id)
                embed = discord.Embed(
                    title=f"Reply — Confession #{self.confession_id}",
                    description=self.reply_text, color=color,
                    timestamp=discord.utils.utcnow(),
                )
                embed.add_field(name="Replier",     value=f"{user.mention} ({user.id})", inline=False)
                embed.add_field(name="Parent",      value=parent_ref,                     inline=False)
                embed.add_field(name="Reply DB ID", value=str(reply_db_id),              inline=True)
                if image_url:
                    embed.set_image(url=image_url)
                await log_ch.send(embed=embed)
            except Exception as e:
                print(f"[ReplyModal] admin log error: {e}")

        mod_ch = (
            self.bot.get_channel(MOD_CONFESSION_LOGS_CHANNEL_ID)
            if MOD_CONFESSION_LOGS_CHANNEL_ID else None
        )
        if mod_ch:
            try:
                embed = discord.Embed(
                    title=f"Reply — Confession #{self.confession_id}",
                    description=self.reply_text, color=color,
                    timestamp=discord.utils.utcnow(),
                )
                embed.add_field(name="Replier (hash)", value=f"`{sha256_user_id(self.user_id)}`", inline=False)
                embed.add_field(name="Parent",         value=parent_ref,                          inline=False)
                embed.add_field(name="Reply DB ID",    value=str(reply_db_id),                    inline=True)
                if image_url:
                    embed.set_image(url=image_url)
                await mod_ch.send(embed=embed)
            except Exception as e:
                print(f"[ReplyModal] mod log error: {e}")

    async def _notify_author(
        self, confession_msg: discord.Message, reply_msg: discord.Message
    ) -> None:
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute(
                    "SELECT user_id FROM confessions WHERE id=?", (self.confession_id,)
                )
                row = await cur.fetchone()
            if not row or row[0] == self.user_id:
                return
            author = await self.bot.fetch_user(row[0])
            embed  = discord.Embed(
                title="💬 Someone Replied to Your Confession",
                description=f'"{self.reply_text[:100]}{"…" if len(self.reply_text) > 100 else ""}"',
                color=discord.Color.blurple(),
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(
                name="🔗 View Reply", value=f"[Click here]({reply_msg.jump_url})", inline=False
            )
            embed.set_footer(text="All identities remain anonymous.")
            await author.send(embed=embed)
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
    ) -> None:
        super().__init__()
        self.bot                     = bot
        self.confession_id           = confession_id
        self.parent_reply_id         = parent_reply_id
        self.parent_reply_message_id = parent_reply_message_id
        self.parent_reply_author_id  = parent_reply_author_id
        self.confession_author_id    = confession_author_id
        self.confession_message_id   = confession_message_id
        self.user_id: int | None     = None
        self.reply_text: str | None  = None
        
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
        self.add_item(ui.Label(
            text="Attach an image (optional)",
            component=self.image_upload
        ))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.user_id    = interaction.user.id
        self.reply_text = self.reply_input.value
        await interaction.response.defer(ephemeral=True, thinking=True)

        can_reply, reason, current_count = await can_user_reply(
            self.user_id, HOURLY_REPLY_LIMIT
        )
        if not can_reply:
            msg = await interaction.followup.send(reason, ephemeral=True, wait=True)
            asyncio.create_task(self._delete_after(msg, 8))
            return

        image_url: str | None = None
        attachment: discord.Attachment | None = self.image_upload.values[0] if self.image_upload.values else None
        if attachment is not None:
            proc = await interaction.followup.send("⏳ Processing image…", ephemeral=True, wait=True)
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
                    ephemeral=True, wait=True,
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
            channel = self.bot.get_channel(CONFESSION_CHANNEL_ID)
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

            # Insert into DB
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute(
                    "INSERT INTO replies "
                    "(confession_id, parent_reply_id, user_id, description, image_url) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        self.confession_id, self.parent_reply_id,
                        self.user_id, self.reply_text, image_url,
                    ),
                )
                await db.commit()
                reply_db_id = cur.lastrowid

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
                send_kwargs["reference"]      = parent_msg
                send_kwargs["mention_author"] = False
            sent_msg = await thread.send(**send_kwargs)

            # Persist message_id
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "UPDATE replies SET message_id=? WHERE id=?",
                    (sent_msg.id, reply_db_id),
                )
                await db.commit()

            await self._log_reply(reply_db_id, image_url, is_op)
            asyncio.create_task(self._notify_replier(sent_msg, is_op))

            remaining = HOURLY_REPLY_LIMIT - (current_count + 1)
            warning   = (
                f"\n⚠️ Only {remaining} repl{'y' if remaining == 1 else 'ies'} left this hour!"
                if remaining <= 2 else ""
            )
            ok = await interaction.followup.send(
                f"✅ Reply posted!{warning}\n📝 {sent_msg.jump_url}",
                ephemeral=True, wait=True,
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
        color = discord.Color.gold() if is_op else discord.Color.from_str("#AB766F")
        title = f"Reply — Confession #{self.confession_id}"

        parent_ref, parent_quote = await get_parent_reference(reply_db_id)

        log_ch = self.bot.get_channel(LOG_CHANNEL_ID)
        if log_ch:
            try:
                user  = await self.bot.fetch_user(self.user_id)
                embed = discord.Embed(
                    title=title, description=self.reply_text,
                    color=color, timestamp=discord.utils.utcnow(),
                )
                embed.add_field(name="Replier",         value=f"{user.mention} ({user.id})",           inline=False)
                embed.add_field(name="Parent",         value=parent_ref,                                inline=False)
                embed.add_field(name="Reply DB ID",     value=str(reply_db_id),                        inline=True)
                if image_url:
                    embed.set_image(url=image_url)
                await log_ch.send(embed=embed)
            except Exception as e:
                print(f"[ReplyModal] admin log error: {e}")

        mod_ch = (
            self.bot.get_channel(MOD_CONFESSION_LOGS_CHANNEL_ID)
            if MOD_CONFESSION_LOGS_CHANNEL_ID else None
        )
        if mod_ch:
            try:
                embed = discord.Embed(
                    title=title, description=self.reply_text,
                    color=color, timestamp=discord.utils.utcnow(),
                )
                embed.add_field(name="Replier (hash)",  value=f"`{sha256_user_id(self.user_id)}`",     inline=False)
                embed.add_field(name="Parent",         value=parent_ref,                                inline=False)
                embed.add_field(name="Reply DB ID",     value=str(reply_db_id),                        inline=True)
                if image_url:
                    embed.set_image(url=image_url)
                await mod_ch.send(embed=embed)
            except Exception as e:
                print(f"[ReplyModal] mod log error: {e}")

    async def _notify_replier(self, new_reply_msg: discord.Message, is_op: bool) -> None:
        """DM the author of the parent reply."""
        try:
            if self.parent_reply_author_id == self.user_id:
                return
            replier = await self.bot.fetch_user(self.parent_reply_author_id)
            title   = "👑 The OP Replied to Your Reply!" if is_op else "💬 Someone Replied to Your Reply"
            embed   = discord.Embed(
                title=title,
                description=(
                    f'{"**OP** replied" if is_op else "Someone replied"}: '
                    f'"{self.reply_text[:100]}{"…" if len(self.reply_text) > 100 else ""}"'
                ),
                color=discord.Color.gold() if is_op else discord.Color.blurple(),
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(
                name="🔗 View Reply", value=f"[Click here]({new_reply_msg.jump_url})", inline=False
            )
            embed.set_footer(
                text="The confession author (OP) replied to you!"
                if is_op else "All identities remain anonymous."
            )
            await replier.send(embed=embed)
        except (discord.Forbidden, Exception):
            pass