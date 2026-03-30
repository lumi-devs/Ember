# cogs/confessions/confess.py  —  Components V2 (discord.py master / ≥ 2.7)
from __future__ import annotations

import ast
import io
import os
import asyncio
import traceback

import aiosqlite
import discord
from discord import ui, app_commands
from discord.ext import commands

from .helpers import (
    DB_PATH,
    init_db,
    is_user_banned,
    get_confession_by_id,
    can_user_confess,
    sha256_user_id,
    LOG_CHANNEL_ID,
    MOD_CONFESSION_LOGS_CHANNEL_ID,
)
from .reply import ReplyModal, ReplyLayoutView

# ──────────────────────────────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────────────────────────────

DAILY_CONFESSION_LIMIT = int(os.getenv("DAILY_CONFESSION_LIMIT", 3))
CONFESSION_CHANNEL_ID  = int(os.getenv("CONFESSION_CHANNEL_ID"))
REPORT_CHANNEL_ID      = int(os.getenv("REPORT_CHANNEL_ID"))
MEDIA_CHANNEL_ID       = int(os.getenv("MEDIA_CHANNEL_ID"))
HOURLY_REPORT_LIMIT    = int(os.getenv("HOURLY_REPORT_LIMIT", 5))
ALLOWED_ROLE_IDS_STR   = os.environ.get("ALLOWED_ROLE_IDS", "[]")

try:
    ALLOWED_ROLE_IDS = ast.literal_eval(ALLOWED_ROLE_IDS_STR)
except (SyntaxError, ValueError):
    ALLOWED_ROLE_IDS = [
        int(r.strip())
        for r in ALLOWED_ROLE_IDS_STR.strip("[]").split(",")
        if r.strip()
    ]


# ──────────────────────────────────────────────────────────────────────────────
# PERSISTENT BUTTONS
# ──────────────────────────────────────────────────────────────────────────────

class PersistentConfessButton(ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Make a Confession",
            style=discord.ButtonStyle.primary,
            custom_id="persistent_confess_button",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not any(role.id in ALLOWED_ROLE_IDS for role in interaction.user.roles):
            return await interaction.response.send_message(
                "❌ You need at least LVL 5 to make confessions.", ephemeral=True
            )
        if await is_user_banned(interaction.user.id):
            return await interaction.response.send_message(
                "❌ You are banned from making confessions.", ephemeral=True
            )
        # Fix: Unpack 3 values instead of 2
        can_confess, reason, _ = await can_user_confess(
            interaction.guild, interaction.user.id, DAILY_CONFESSION_LIMIT
        )
        if not can_confess:
            return await interaction.response.send_message(reason, ephemeral=True)
        await interaction.response.send_modal(ConfessionModal(interaction.client))

class PersistentReplyButton(ui.Button):
    def __init__(self, confession_id: int) -> None:
        super().__init__(
            label="Reply",
            style=discord.ButtonStyle.secondary,
            custom_id=f"confession_reply_{confession_id}",
        )
        self.confession_id = confession_id

    async def callback(self, interaction: discord.Interaction) -> None:
        confession = await get_confession_by_id(self.confession_id)
        if not confession:
            return await interaction.response.send_message(
                "❌ Confession not found.", ephemeral=True
            )
        message_id, author_id = confession[5], confession[1]
        await interaction.response.send_modal(
            ReplyModal(interaction.client, self.confession_id, message_id, author_id)
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
        from .helpers import can_user_report
        can_report, reason, _ = await can_user_report(interaction.user.id, HOURLY_REPORT_LIMIT)
        if not can_report:
            return await interaction.response.send_message(reason, ephemeral=True)
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute(
                    "SELECT user_id, title, description, image_url FROM confessions WHERE id=?",
                    (self.confession_id,),
                )
                row = await cur.fetchone()
                if not row:
                    return await interaction.response.send_message(
                        "❌ Confession not found.", ephemeral=True
                    )
                author_id, title, description, image_url = row
                await db.execute(
                    "INSERT INTO reports (target_type, target_id, reporter_id) VALUES (?, ?, ?)",
                    ("confession", self.confession_id, interaction.user.id),
                )
                await db.commit()
                cur = await db.execute(
                    "SELECT 1 FROM confessban WHERE user_id=?", (author_id,)
                )
                banned = await cur.fetchone()

            embed = discord.Embed(
                title=f"🚨 Confession Report #{self.confession_id}",
                color=discord.Color.red(),
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(name="Confession ID", value=str(self.confession_id),                       inline=False)
            embed.add_field(name="Author ID",     value=sha256_user_id(author_id),                    inline=False)
            embed.add_field(name="Reported by",   value=f"{interaction.user} ({interaction.user.id})", inline=False)
            embed.add_field(name="Banned?",       value="Yes" if banned else "No",                    inline=False)
            embed.add_field(name="Title",         value=title or "No title",                          inline=False)
            embed.add_field(name="Content",       value=description or "No content",                  inline=False)
            if image_url:
                embed.set_image(url=image_url)

            mod_ch = interaction.client.get_channel(REPORT_CHANNEL_ID)
            if mod_ch:
                await mod_ch.send(embed=embed)
            await interaction.response.send_message("✅ Report submitted.", ephemeral=True)
        except Exception as e:
            print(f"[PersistentReportButton] {e}")
            await interaction.response.send_message("❌ An error occurred.", ephemeral=True)


# ──────────────────────────────────────────────────────────────────────────────
# ACTION ROW
# ──────────────────────────────────────────────────────────────────────────────

class ConfessionActionRow(ui.ActionRow):
    def __init__(self, confession_id: int, *, show_confess: bool = False) -> None:
        super().__init__()
        if show_confess:
            self.add_item(PersistentConfessButton())
        self.add_item(PersistentReplyButton(confession_id))
        self.add_item(PersistentReportButton(confession_id))


# ──────────────────────────────────────────────────────────────────────────────
# CONFESSION LAYOUT VIEW
# ──────────────────────────────────────────────────────────────────────────────

class ConfessionLayoutView(ui.LayoutView):
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

        display_title = (
            title.strip() if title and title.strip()
            else f"Confession #{confession_id}"
        )

        children: list = [
            ui.TextDisplay(f"## 💌 {display_title}"),
            ui.Separator(spacing=discord.SeparatorSpacing.small),
            ui.TextDisplay(body or "*(empty)*"),
        ]

        if image_url:
            children.append(
                ui.MediaGallery(discord.MediaGalleryItem(media=image_url))
            )

        children.extend([
            ui.Separator(spacing=discord.SeparatorSpacing.small),
            ui.TextDisplay(f"-# Confession #{confession_id}"),
            ConfessionActionRow(confession_id, show_confess=show_confess_button),
        ])

        self.add_item(ui.Container(*children, accent_color=discord.Color.from_str("#131313")))


async def build_confession_layout_view(
    confession_id: int,
    show_confess_button: bool = False,
) -> ConfessionLayoutView:
    """Reconstruct a ConfessionLayoutView from the DB (edits, restarts)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT title, description, image_url FROM confessions WHERE id=?",
            (confession_id,),
        )
        row = await cur.fetchone()

    title, body, image_url = row if row else (None, "(deleted)", None)
    return ConfessionLayoutView(
        confession_id,
        title=title or "",
        body=body or "",
        show_confess_button=show_confess_button,
        image_url=image_url,
    )


# ──────────────────────────────────────────────────────────────────────────────
# CONFESSION MODAL
# ──────────────────────────────────────────────────────────────────────────────

class ConfessionModal(ui.Modal, title="Submit a Confession"):
    def __init__(self, bot: discord.Client) -> None:
        super().__init__()
        self.bot = bot
        self.user_id: int | None = None
        
        # Add components with labels (V2 modal pattern)
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
        # Add label for file upload (required for V2 modals)
        self.add_item(ui.Label(
            text="Attach an image (optional)",
            component=self.image_upload
        ))

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
        """Download a FileUpload attachment and re-host it in the media channel."""
        media_ch = self.bot.get_channel(MEDIA_CHANNEL_ID)
        if not media_ch:
            return False, "Media channel not found"
        try:
            data = await attachment.read()
            if not data:
                return False, "Empty attachment"
            file = discord.File(io.BytesIO(data), filename=attachment.filename)
            msg  = await media_ch.send(label, file=file)
            await asyncio.sleep(1)      # let Discord finalize the CDN URL
            url  = msg.attachments[0].url if msg.attachments else None
            return (True, url) if url else (False, "CDN URL unavailable")
        except Exception as e:
            return False, str(e)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.user_id = interaction.user.id
        await interaction.response.defer(ephemeral=True, thinking=True)

        if await is_user_banned(self.user_id):
            msg = await interaction.followup.send(
                "❌ You are banned from submitting confessions.", ephemeral=True, wait=True
            )
            asyncio.create_task(self._delete_after(msg, 8))
            return

        can_confess, reason, current_count = await can_user_confess(
            interaction.guild, self.user_id, DAILY_CONFESSION_LIMIT
        )
        if not can_confess:
            msg = await interaction.followup.send(reason, ephemeral=True, wait=True)
            asyncio.create_task(self._delete_after(msg, 8))
            return

        channel = self.bot.get_channel(CONFESSION_CHANNEL_ID)
        if not channel:
            msg = await interaction.followup.send(
                "❌ Confession channel not found.", ephemeral=True, wait=True
            )
            asyncio.create_task(self._delete_after(msg, 8))
            return

        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute("SELECT MAX(id) FROM confessions")
                prev_id = (await cur.fetchone())[0]

            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute(
                    "INSERT INTO confessions (user_id, title, description) VALUES (?, ?, ?)",
                    (self.user_id, self.title_input.value, self.description_input.value),
                )
                await db.commit()
                confession_id = cur.lastrowid

            # Process optional image upload
            image_url: str | None = None
            attachment: discord.Attachment | None = self.image_upload.values[0] if self.image_upload.values else None
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
                    async with aiosqlite.connect(DB_PATH) as db:
                        await db.execute(
                            "UPDATE confessions SET image_url=? WHERE id=?",
                            (image_url, confession_id),
                        )
                        await db.commit()
                else:
                    warn = await interaction.followup.send(
                        f"⚠️ Image upload failed ({result}). Continuing without media.",
                        ephemeral=True, wait=True,
                    )
                    asyncio.create_task(self._delete_after(warn, 10))

            # Strip confess button from the previous confession card
            if prev_id:
                asyncio.create_task(self._strip_prev_confess_button(prev_id))

            # Build and send the LayoutView card
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
                print(f"[Confessions] Thread error: {e}")

            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "UPDATE confessions SET message_id=? WHERE id=?",
                    (sent.id, confession_id),
                )
                await db.commit()

            # Register view for persistence across restarts
            self.bot.add_view(view)

            await self._log_confession(confession_id, image_url)

            thread_ref = thread.mention if thread else "*(thread creation failed)*"
            ok = await interaction.followup.send(
                f"✅ Confession **#{confession_id}** is live! {thread_ref}",
                ephemeral=True, wait=True,
            )
            asyncio.create_task(self._delete_after(ok, 30))

        except Exception:
            traceback.print_exc()
            err = await interaction.followup.send(
                "❌ An error occurred while submitting your confession.",
                ephemeral=True, wait=True,
            )
            asyncio.create_task(self._delete_after(err, 8))

    async def _strip_prev_confess_button(self, confession_id: int) -> None:
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute(
                    "SELECT message_id FROM confessions WHERE id=?", (confession_id,)
                )
                row = await cur.fetchone()
            if not row or not row[0]:
                return
            channel = self.bot.get_channel(CONFESSION_CHANNEL_ID)
            if not channel:
                return
            msg  = await channel.fetch_message(row[0])
            view = await build_confession_layout_view(confession_id, show_confess_button=False)
            await msg.edit(view=view)
        except discord.NotFound:
            pass
        except Exception as e:
            print(f"[Confessions] _strip_prev_confess_button error: {e}")

    async def _log_confession(self, confession_id: int, image_url: str | None) -> None:
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute(
                    "SELECT user_id, title, description FROM confessions WHERE id=?",
                    (confession_id,),
                )
                row = await cur.fetchone()
            if not row:
                return
            user_id, title, description = row
        except Exception as e:
            print(f"[Confessions] log fetch error: {e}")
            return

        log_ch = self.bot.get_channel(LOG_CHANNEL_ID)
        if log_ch:
            try:
                user  = await self.bot.fetch_user(user_id)
                embed = discord.Embed(
                    title=f"Confession #{confession_id}", description=description,
                    color=discord.Color.purple(), timestamp=discord.utils.utcnow(),
                )
                embed.add_field(name="User", value=f"{user.mention} ({user.id})", inline=False)
                if title:
                    embed.add_field(name="Title", value=title, inline=False)
                if image_url:
                    embed.set_image(url=image_url)
                await log_ch.send(embed=embed)
            except Exception as e:
                print(f"[Confessions] admin log error: {e}")

        mod_ch = (
            self.bot.get_channel(MOD_CONFESSION_LOGS_CHANNEL_ID)
            if MOD_CONFESSION_LOGS_CHANNEL_ID else None
        )
        if mod_ch:
            try:
                embed = discord.Embed(
                    title=f"Confession #{confession_id}", description=description,
                    color=discord.Color.purple(), timestamp=discord.utils.utcnow(),
                )
                embed.add_field(name="User (hash)", value=f"`{sha256_user_id(user_id)}`", inline=False)
                if title:
                    embed.add_field(name="Title", value=title, inline=False)
                if image_url:
                    embed.set_image(url=image_url)
                await mod_ch.send(embed=embed)
            except Exception as e:
                print(f"[Confessions] mod log error: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# COG
# ──────────────────────────────────────────────────────────────────────────────

class Confessions(commands.Cog):
    def __init__(self, bot: discord.Client) -> None:
        self.bot = bot
        self.initialized = False
        self.bot.loop.create_task(self._initialize())

    async def _initialize(self) -> None:
        try:
            await init_db()
            await self._load_persistent_views()
            self.initialized = True
            print("[Confessions] ✅ Initialized.")
        except Exception as e:
            print(f"[Confessions] ❌ Init failed: {e}")

    async def _load_persistent_views(self) -> None:
        await self.bot.wait_until_ready()
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute("SELECT MAX(id) FROM confessions")
                latest_id = (await cur.fetchone())[0]

                async with db.execute(
                    "SELECT id FROM confessions WHERE message_id IS NOT NULL"
                ) as cur:
                    async for (cid,) in cur:
                        try:
                            view = await build_confession_layout_view(
                                cid, show_confess_button=(cid == latest_id)
                            )
                            self.bot.add_view(view)
                        except Exception as e:
                            print(f"[Confessions] View load error #{cid}: {e}")

                async with db.execute(
                    "SELECT id FROM replies WHERE message_id IS NOT NULL"
                ) as cur:
                    async for (rid,) in cur:
                        try:
                            view = await ReplyLayoutView.from_db(rid)
                            self.bot.add_view(view)
                        except Exception as e:
                            print(f"[Confessions] Reply view load error #{rid}: {e}")

            print("[Confessions] ✅ Persistent views loaded.")
        except Exception as e:
            print(f"[Confessions] ❌ View load error: {e}")

    @app_commands.command(name="confess", description="Submit an anonymous confession")
    async def confess_command(self, interaction: discord.Interaction) -> None:
        if not self.initialized:
            return await interaction.response.send_message(
                "❌ Still initializing — try again in a moment.", ephemeral=True
            )
        if await is_user_banned(interaction.user.id):
            return await interaction.response.send_message(
                "❌ You are banned from making confessions.", ephemeral=True
            )
        if interaction.channel.id != CONFESSION_CHANNEL_ID:
            return await interaction.response.send_message(
                f"❌ Use this command in <#{CONFESSION_CHANNEL_ID}>.", ephemeral=True
            )
        if not any(role.id in ALLOWED_ROLE_IDS for role in interaction.user.roles):
            return await interaction.response.send_message(
                "❌ You need at least LVL 5 to make confessions.", ephemeral=True
            )
        # Fix: Unpack 3 values instead of 2
        can_confess, reason, _ = await can_user_confess(
            interaction.guild, interaction.user.id, DAILY_CONFESSION_LIMIT
        )
        if not can_confess:
            return await interaction.response.send_message(reason, ephemeral=True)
        await interaction.response.send_modal(ConfessionModal(self.bot))

    @app_commands.command(
        name="confess_delete",
        description="Delete a confession by ID and renumber all subsequent ones",
    )
    @app_commands.describe(
        confession_id="ID of the confession to delete",
        reason="Reason for deletion (optional)",
    )
    @app_commands.default_permissions(manage_messages=True)
    async def confess_delete(
        self,
        interaction: discord.Interaction,
        confession_id: int,
        reason: str | None = None,
    ) -> None:
        if not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message(
                "❌ You need `Manage Server` permission.", ephemeral=True
            )
        await interaction.response.defer(ephemeral=True)
        channel = self.bot.get_channel(CONFESSION_CHANNEL_ID)

        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("BEGIN")
                try:
                    cur = await db.execute(
                        "SELECT id, user_id, title, description, message_id "
                        "FROM confessions WHERE id=?",
                        (confession_id,),
                    )
                    target = await cur.fetchone()
                    if not target:
                        await db.execute("ROLLBACK")
                        return await interaction.followup.send(
                            f"❌ Confession #{confession_id} not found.", ephemeral=True
                        )
                    _, author_id, title, description, message_id = target

                    cur = await db.execute("SELECT MAX(id) FROM confessions")
                    max_id = (await cur.fetchone())[0]

                    try:
                        author = await self.bot.fetch_user(author_id)
                    except Exception:
                        author = None

                    # Delete Discord message + thread
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
                            print(f"[confess_delete] Delete error: {e}")

                    # Delete replies
                    cur = await db.execute(
                        "SELECT COUNT(*) FROM replies WHERE confession_id=?", (confession_id,)
                    )
                    reply_count = (await cur.fetchone())[0]
                    await db.execute(
                        "DELETE FROM replies WHERE confession_id=?", (confession_id,)
                    )
                    await db.execute("DELETE FROM confessions WHERE id=?", (confession_id,))

                    # Renumber subsequent confessions and rebuild their V2 cards
                    cur = await db.execute(
                        "SELECT id, message_id FROM confessions WHERE id > ? ORDER BY id ASC",
                        (confession_id,),
                    )
                    rows = await cur.fetchall()
                    update_count = 0
                    for old_id, msg_id in rows:
                        new_id = old_id - 1
                        await db.execute(
                            "UPDATE confessions SET id=? WHERE id=?", (new_id, old_id)
                        )
                        # Update reply references BEFORE rebuilding reply cards
                        # so that ReplyLayoutView.from_db() reads the correct new confession_id
                        await db.execute(
                            "UPDATE replies SET confession_id=? WHERE confession_id=?",
                            (new_id, old_id),
                        )
                        await db.commit()  # flush so from_db sees the updated rows
                        if channel and msg_id:
                            try:
                                old_msg  = await channel.fetch_message(msg_id)
                                new_view = await build_confession_layout_view(new_id)
                                await old_msg.edit(view=new_view)

                                # Rename the thread so it still matches the confession number
                                if old_msg.thread:
                                    try:
                                        await old_msg.thread.edit(
                                            name=f"Confession #{new_id}"
                                        )
                                    except Exception as te:
                                        print(f"[confess_delete] Thread rename error #{old_id}→#{new_id}: {te}")

                                    # Rebuild every reply card so they say
                                    # "↩️ Reply to Confession #<new_id>" instead of the old number
                                    try:
                                        reply_cur = await db.execute(
                                            "SELECT id, message_id FROM replies "
                                            "WHERE confession_id=? AND message_id IS NOT NULL",
                                            (new_id,),
                                        )
                                        reply_rows = await reply_cur.fetchall()
                                        for reply_db_id, reply_msg_id in reply_rows:
                                            try:
                                                reply_msg = await old_msg.thread.fetch_message(reply_msg_id)
                                                updated_reply_view = await ReplyLayoutView.from_db(reply_db_id)
                                                await reply_msg.edit(view=updated_reply_view)
                                            except discord.NotFound:
                                                pass
                                            except Exception as re:
                                                print(f"[confess_delete] Reply card update error reply#{reply_db_id}: {re}")
                                    except Exception as re:
                                        print(f"[confess_delete] Reply fetch error for confession #{new_id}: {re}")

                            except discord.NotFound:
                                pass
                            except Exception as e:
                                print(f"[confess_delete] Renumber edit error #{old_id}: {e}")
                        update_count += 1
                    new_last_id = max_id - 1
                    cur = await db.execute(
                        "SELECT id, message_id FROM confessions WHERE id=?",
                        (new_last_id,),
                    )
                    prev = await cur.fetchone()
                    if prev and channel and prev[1]:
                        try:
                            prev_msg  = await channel.fetch_message(prev[1])
                            prev_view = await build_confession_layout_view(
                                prev[0], show_confess_button=True
                            )
                            await prev_msg.edit(view=prev_view)
                        except (discord.NotFound, Exception):
                            pass

                    await db.execute(
                        "UPDATE sqlite_sequence SET seq=? WHERE name='confessions'",
                        (max_id - 1,),
                    )
                    await db.commit()

                    # DM the author
                    dm_sent = False
                    if author:
                        try:
                            dm_embed = discord.Embed(
                                title="🗑️ Your Confession Was Deleted",
                                description=f"Your confession **#{confession_id}** was removed by a moderator.",
                                color=discord.Color.red(),
                                timestamp=discord.utils.utcnow(),
                            )
                            if title:
                                dm_embed.add_field(name="Title",   value=title,  inline=False)
                            if description:
                                short = description[:200] + ("…" if len(description) > 200 else "")
                                dm_embed.add_field(name="Content", value=short,  inline=False)
                            dm_embed.add_field(
                                name="Reason",
                                value=reason or "No specific reason provided.",
                                inline=False,
                            )
                            await author.send(embed=dm_embed)
                            dm_sent = True
                        except (discord.Forbidden, Exception):
                            pass

                    # Result summary embed
                    result = discord.Embed(
                        title="✅ Confession Deleted & Renumbered",
                        color=discord.Color.red(),
                        timestamp=discord.utils.utcnow(),
                    )
                    result.add_field(name="Deleted ID",      value=str(confession_id), inline=True)
                    result.add_field(name="Replies Deleted", value=str(reply_count),   inline=True)
                    result.add_field(
                        name="Renumbered",
                        value=f"✅ {update_count} confession{'s' if update_count != 1 else ''}",
                        inline=True,
                    )
                    status = [
                        "✅ Database entry removed",
                        "✅ Message deleted" if message_deleted else "❌ Message not found",
                    ]
                    if thread_deleted:
                        status.append("✅ Thread deleted")
                    status.append(
                        "✅ Author notified" if dm_sent
                        else ("❌ DMs disabled" if author else "❌ Could not fetch author")
                    )
                    result.add_field(name="Status", value="\n".join(status), inline=False)
                    if title:
                        result.add_field(name="Title",   value=title,  inline=False)
                    if description:
                        short = description[:200] + ("…" if len(description) > 200 else "")
                        result.add_field(name="Content", value=short,  inline=False)
                    if reason:
                        result.add_field(name="Reason",  value=reason, inline=False)
                    result.set_footer(text=f"Deleted by {interaction.user.display_name}")
                    await interaction.followup.send(embed=result, ephemeral=True)

                    await self._log_deletion(
                        interaction, confession_id, author_id,
                        reply_count, update_count, dm_sent, reason,
                    )

                except Exception:
                    await db.execute("ROLLBACK")
                    raise

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
        def _base_embed() -> discord.Embed:
            return discord.Embed(
                title="🗑️ Confession Deleted & Renumbered",
                color=discord.Color.orange(),
                timestamp=discord.utils.utcnow(),
            )

        log_ch = self.bot.get_channel(LOG_CHANNEL_ID)
        if log_ch:
            try:
                e = _base_embed()
                e.add_field(name="Deleted ID",      value=str(confession_id),  inline=True)
                e.add_field(name="Author",          value=f"<@{author_id}>",   inline=True)
                e.add_field(name="Replies Deleted", value=str(reply_count),    inline=True)
                e.add_field(name="Renumbered",      value=str(renumber_count), inline=True)
                e.add_field(name="DM sent",         value="✅" if dm_sent else "❌", inline=True)
                e.add_field(name="Deleted by",
                            value=f"{interaction.user.mention} ({interaction.user.id})",
                            inline=False)
                if reason:
                    e.add_field(name="Reason", value=reason, inline=False)
                await log_ch.send(embed=e)
            except Exception as ex:
                print(f"[confess_delete] Admin log error: {ex}")

        mod_ch = (
            self.bot.get_channel(MOD_CONFESSION_LOGS_CHANNEL_ID)
            if MOD_CONFESSION_LOGS_CHANNEL_ID else None
        )
        if mod_ch:
            try:
                e = _base_embed()
                e.add_field(name="Deleted ID",      value=str(confession_id),               inline=True)
                e.add_field(name="Author (hash)",   value=f"`{sha256_user_id(author_id)}`",  inline=True)
                e.add_field(name="Replies Deleted", value=str(reply_count),                  inline=True)
                e.add_field(name="Renumbered",      value=str(renumber_count),               inline=True)
                e.add_field(name="DM sent",         value="✅" if dm_sent else "❌",          inline=True)
                e.add_field(name="Deleted by",      value=interaction.user.mention,           inline=False)
                if reason:
                    e.add_field(name="Reason", value=reason, inline=False)
                await mod_ch.send(embed=e)
            except Exception as ex:
                print(f"[confess_delete] Mod log error: {ex}")


async def setup(bot: discord.Client) -> None:
    await bot.add_cog(Confessions(bot))