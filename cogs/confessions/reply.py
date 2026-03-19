import os

import discord
from discord import ui
import asyncio
from datetime import datetime
import aiosqlite
import aiohttp
import io


from .helpers import (
    DB_PATH,
    can_user_reply,
    extract_url,
    get_hourly_reply_count,
    sha256_user_id,
    LOG_CHANNEL_ID,
    MOD_LOG_CHANNEL_ID,
    REPORT_CHANNEL_ID,
)

CONFESSION_CHANNEL_ID = int(os.getenv("CONFESSION_CHANNEL_ID"))
MEDIA_CHANNEL_ID = int(os.getenv("MEDIA_CHANNEL_ID"))
HOURLY_REPLY_LIMIT = int(os.getenv("HOURLY_REPLY_LIMIT", 8))
HOURLY_REPORT_LIMIT = int(os.getenv("HOURLY_REPORT_LIMIT", 5))

# ==============================
# HELPER: Calculate hierarchical reply number
# ==============================
async def get_reply_hierarchy_path(reply_id: int) -> str:
    """
    Calculate the hierarchical path for a reply (e.g., "1.2.1").
    
    Algorithm:
    1. If this is a top-level reply (parent_reply_id is NULL), find its position
       among all top-level replies to this confession.
    2. If this is a nested reply, recursively build the path from the root.
    
    Returns: string like "1" or "1.2" or "1.2.1"
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            # Get the reply's details
            cursor = await db.execute(
                "SELECT confession_id, parent_reply_id FROM replies WHERE id=?",
                (reply_id,)
            )
            result = await cursor.fetchone()
            if not result:
                return "?"
            
            confession_id, parent_reply_id = result
            
            # If this is a top-level reply (direct reply to confession)
            if parent_reply_id is None:
                # Count how many top-level replies came before this one
                cursor = await db.execute(
                    """SELECT COUNT(*) FROM replies 
                       WHERE confession_id=? AND parent_reply_id IS NULL AND id<=?""",
                    (confession_id, reply_id)
                )
                position = (await cursor.fetchone())[0]
                return str(position)
            
            # This is a nested reply - build the path recursively
            # First, get parent's path
            parent_path = await get_reply_hierarchy_path(parent_reply_id)
            
            # Then find this reply's position among siblings (replies to same parent)
            cursor = await db.execute(
                """SELECT COUNT(*) FROM replies 
                   WHERE parent_reply_id=? AND id<=?
                   ORDER BY id ASC""",
                (parent_reply_id, reply_id)
            )
            position = (await cursor.fetchone())[0]
            
            return f"{parent_path}.{position}"
            
    except Exception as e:
        print(f"[get_reply_hierarchy_path] Error: {e}")
        import traceback
        traceback.print_exc()
        return "?"


# ==============================
# PERSISTENT REPLY MESSAGE VIEW
# (attached to every reply in the thread)
# ==============================

class ReplyMessageView(ui.View):
    """Persistent view attached to each reply message — provides Reply & Report buttons."""

    def __init__(self, reply_id: int):
        super().__init__(timeout=None)
        self.add_item(ReplyToReplyButton(reply_id))
        self.add_item(ReportReplyButton(reply_id))


class ReplyToReplyButton(ui.Button):
    def __init__(self, reply_id: int):
        super().__init__(
            label="💬 Reply",
            style=discord.ButtonStyle.secondary,
            custom_id=f"reply_msg_reply_{reply_id}",
        )
        self.reply_id = reply_id

    async def callback(self, interaction: discord.Interaction):
        bot = interaction.client
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute(
                    "SELECT confession_id, user_id, message_id FROM replies WHERE id=?",
                    (self.reply_id,),
                )
                reply = await cursor.fetchone()
                if not reply:
                    return await interaction.response.send_message(
                        "❌ This reply no longer exists.", ephemeral=True
                    )
                confession_id, reply_author_id, reply_message_id = reply

                cursor = await db.execute(
                    "SELECT message_id, user_id FROM confessions WHERE id=?",
                    (confession_id,),
                )
                confession = await cursor.fetchone()
                if not confession:
                    return await interaction.response.send_message(
                        "❌ The parent confession no longer exists.", ephemeral=True
                    )
                confession_message_id, confession_author_id = confession

            modal = ReplyToReplyModal(
                bot=bot,
                confession_id=confession_id,
                parent_reply_id=self.reply_id,
                parent_reply_message_id=reply_message_id,
                parent_reply_author_id=reply_author_id,
                confession_author_id=confession_author_id,
                confession_message_id=confession_message_id,
            )
            await interaction.response.send_modal(modal)

        except Exception as e:
            print(f"[ReplyToReplyButton] Error: {e}")
            await interaction.response.send_message(
                "❌ An error occurred. Please try again.", ephemeral=True
            )


class ReportReplyButton(ui.Button):
    def __init__(self, reply_id: int):
        super().__init__(
            label="🚨 Report",
            style=discord.ButtonStyle.danger,
            custom_id=f"reply_msg_report_{reply_id}",
        )
        self.reply_id = reply_id

    async def callback(self, interaction: discord.Interaction):
        from .helpers import can_user_report
        can_report, reason, current_count = await can_user_report(
            interaction.user.id, HOURLY_REPORT_LIMIT
        )
        if not can_report:
            return await interaction.response.send_message(reason, ephemeral=True)
        bot = interaction.client
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute(
                    "SELECT confession_id, user_id, description, image_url FROM replies WHERE id=?",
                    (self.reply_id,),
                )
                reply = await cursor.fetchone()
                if not reply:
                    return await interaction.response.send_message(
                        "❌ This reply no longer exists.", ephemeral=True
                    )
                confession_id, author_id, description, image_url = reply

                await db.execute(
                    "INSERT INTO reports (target_type, target_id, reporter_id) VALUES (?, ?, ?)",
                    ("reply", self.reply_id, interaction.user.id),
                )
                await db.commit()

            report_channel = bot.get_channel(REPORT_CHANNEL_ID)
            if report_channel:
                embed = discord.Embed(
                    title=f"🚨 Reply Report — Confession #{confession_id}",
                    color=discord.Color.red(),
                    timestamp=discord.utils.utcnow(),
                )
                embed.add_field(name="Reply ID", value=str(self.reply_id), inline=True)
                embed.add_field(name="Confession", value=f"#{confession_id}", inline=True)
                embed.add_field(name="Author ID", value=sha256_user_id(author_id), inline=False)
                embed.add_field(
                    name="Reported by",
                    value=f"{interaction.user} ({interaction.user.id})",
                    inline=False,
                )
                embed.add_field(
                    name="Content", value=description or "No content", inline=False
                )
                if image_url:
                    embed.set_image(url=image_url)
                await report_channel.send(embed=embed)

            await interaction.response.send_message(
                "✅ Report submitted.", ephemeral=True
            )

        except Exception as e:
            print(f"[ReportReplyButton] Error: {e}")
            await interaction.response.send_message(
                "❌ An error occurred.", ephemeral=True
            )


# ==============================
# REPLY MODAL  (reply to a confession)
# ==============================

class ReplyModal(ui.Modal, title="Anonymous Reply"):
    reply_input = ui.TextInput(
        label="Reply",
        style=discord.TextStyle.paragraph,
        max_length=500,
        placeholder="Type your reply here...",
        required=True,
    )

    def __init__(self, bot, confession_id, confession_message_id, confession_author_id):
        super().__init__()
        self.bot = bot
        self.confession_id = confession_id
        self.confession_message_id = confession_message_id
        self.confession_author_id = confession_author_id
        self.user_id = None
        self.reply_text = None

    async def on_submit(self, interaction: discord.Interaction):
        self.user_id = interaction.user.id
        self.reply_text = self.reply_input.value

        await interaction.response.defer(ephemeral=True, thinking=True)

        can_reply, reason, current_count = await can_user_reply(
            self.user_id, HOURLY_REPLY_LIMIT
        )
        if not can_reply:
            error_msg = await interaction.followup.send(reason, ephemeral=True, wait=True)
            asyncio.create_task(self.delete_after_delay(error_msg, 8))
            return

        remaining = HOURLY_REPLY_LIMIT - current_count
        usage_msg = (
            f"📊 You've made {current_count}/{HOURLY_REPLY_LIMIT} replies this hour. "
            f"{remaining} remaining."
        )

        # Send media prompt and schedule deletion
        media_prompt = await interaction.followup.send(
            f"✅ Reply submitted! {usage_msg}\n\n"
            f"You have **60 seconds** to reply with an image URL or attachment.\n"
            f"Send `skip` to continue without media.\n\n"
            f"**Accepted formats:**\n"
            f"• Discord image upload (drag & drop)\n"
            f"• Direct image URL (.png/.jpg/.gif/.webp)",
            ephemeral=True,
            wait=True
        )
        asyncio.create_task(self.delete_after_delay(media_prompt, 60))

        cleaner_cog = self.bot.get_cog("ConfessionCleaner")
        if cleaner_cog:
            await cleaner_cog.allow_user_once(self.user_id)

        image_url = await self.wait_for_media(interaction)
        await self.finalize_reply_directly(image_url, interaction)

    async def delete_after_delay(self, message, delay: int):
        """Helper method to delete a message after a delay."""
        await asyncio.sleep(delay)
        try:
            await message.delete()
        except discord.NotFound:
            pass
        except discord.HTTPException as e:
            print(f"Failed to delete message: {e}")

    async def wait_for_media(self, interaction):
        """Wait for an optional media message from the user."""

        def check(m):
            return (
                m.author.id == self.user_id
                and m.channel.id == interaction.channel.id
            )

        try:
            msg_received = await self.bot.wait_for("message", timeout=60.0, check=check)

            content_lower = msg_received.content.lower().strip()
            if content_lower == "skip":
                try:
                    await msg_received.delete()
                except Exception:
                    pass
                skip_msg = await interaction.followup.send("✅ Skipped media upload.", ephemeral=True, wait=True)
                asyncio.create_task(self.delete_after_delay(skip_msg, 5))
                return None

            has_attachments = len(msg_received.attachments) > 0
            has_valid_url = False
            if msg_received.content and msg_received.content.strip():
                url = extract_url(msg_received.content)
                if url:
                    url_lower = url.lower()
                    image_extensions = [".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff"]
                    if any(url_lower.endswith(ext) for ext in image_extensions):
                        has_valid_url = True
                    elif "media.discordapp.net" in url_lower or "cdn.discordapp.com" in url_lower:
                        has_valid_url = True

            if not has_attachments and not has_valid_url:
                try:
                    await msg_received.delete()
                except Exception:
                    pass
                error_msg = "❌ No valid media found in your message.\n\n"
                if msg_received.content and extract_url(msg_received.content):
                    error_msg += "⚠️ The URL you provided doesn't appear to be an image link.\n"
                    error_msg += "Please use direct image links (ending with .png/.jpg/.gif/.webp)\n\n"
                else:
                    error_msg += (
                        "**Valid options are:**\n"
                        "• Upload an image file (drag & drop)\n"
                        "• Send a direct image URL\n"
                        "• Type `skip` to continue without media\n\n"
                    )
                error_msg += "Continuing without media."
                error_response = await interaction.followup.send(error_msg, ephemeral=True, wait=True)
                asyncio.create_task(self.delete_after_delay(error_response, 10))
                return None

            success, result = await self.download_and_reupload_media(
                msg_received.content, msg_received.attachments
            )

            try:
                await msg_received.delete()
                if success:
                    current_count = await get_hourly_reply_count(self.user_id)
                    remaining = HOURLY_REPLY_LIMIT - current_count
                    warning = ""
                    if remaining <= 2:
                        warning = f"\n⚠️ **Warning:** You only have {remaining} repl{'y' if remaining == 1 else 'ies'} left this hour!"
                    success_msg = await interaction.followup.send(
                        f"✅ Media added to reply!{warning}", ephemeral=True, wait=True
                    )
                    asyncio.create_task(self.delete_after_delay(success_msg, 5))
                    return result
            except discord.Forbidden:
                print(f"No permission to delete message from {msg_received.author}")
            except discord.NotFound:
                print("Message already deleted")
            except Exception as e:
                print(f"Error deleting message: {e}")

            if success:
                success_msg = await interaction.followup.send("✅ Media added to reply!", ephemeral=True, wait=True)
                asyncio.create_task(self.delete_after_delay(success_msg, 5))
                return result
            else:
                error_msg = result if isinstance(result, str) else "Unknown error"
                error_response = await interaction.followup.send(
                    f"❌ Failed to process media: {error_msg}\nContinuing without media.",
                    ephemeral=True, wait=True
                )
                asyncio.create_task(self.delete_after_delay(error_response, 10))
                return None

        except asyncio.TimeoutError:
            current_count = await get_hourly_reply_count(self.user_id)
            remaining = HOURLY_REPLY_LIMIT - current_count
            timeout_msg = await interaction.followup.send(
                f"⏰ Media upload timed out. Continuing without media.\n"
                f"📊 {remaining} replies remaining this hour.",
                ephemeral=True, wait=True
            )
            asyncio.create_task(self.delete_after_delay(timeout_msg, 5))
            return None
        except Exception as e:
            print(f"Error in wait_for_media: {e}")
            import traceback
            traceback.print_exc()
            error_msg = await interaction.followup.send(
                "❌ An error occurred while processing media.", ephemeral=True, wait=True
            )
            asyncio.create_task(self.delete_after_delay(error_msg, 8))
            return None

    async def download_and_reupload_media(self, content, attachments):
        """Download media and re-upload to media channel, returning the new URL."""
        try:
            print(f"[DEBUG] Starting media processing for confession #{self.confession_id}")

            media_url = None
            filename = None

            if attachments and len(attachments) > 0:
                attachment = attachments[0]
                media_url = attachment.url
                filename = attachment.filename
                print(f"[DEBUG] Found attachment: {filename}, URL: {media_url}")
            elif content and content.strip():
                media_url = extract_url(content)
                if media_url:
                    filename = media_url.split("/")[-1].split("?")[0]
                    if "." not in filename:
                        if "tenor.com" in media_url or "giphy.com" in media_url:
                            filename = f"gif_{self.confession_id}.gif"
                        else:
                            filename = f"image_{self.confession_id}.jpg"
                    print(f"[DEBUG] Found URL: {media_url}, filename: {filename}")

            if not media_url:
                return False, "No media found in message"

            async with aiohttp.ClientSession() as session:
                try:
                    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
                    async with session.get(media_url, headers=headers, timeout=30) as response:
                        if response.status != 200:
                            return False, f"Failed to download media: HTTP {response.status}"
                        file_data = await response.read()
                        if not filename:
                            content_type = response.headers.get("Content-Type", "")
                            ext_map = {
                                "image/jpeg": ".jpg", "image/jpg": ".jpg",
                                "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp",
                            }
                            filename = f"image_{self.confession_id}" + ext_map.get(content_type, ".jpg")
                except aiohttp.ClientError as e:
                    return False, f"Failed to download media: {str(e)}"
                except asyncio.TimeoutError:
                    return False, "Download timeout"
                except Exception as e:
                    return False, f"Error downloading: {str(e)}"

            if not file_data or len(file_data) == 0:
                return False, "No media data received"

            media_channel = self.bot.get_channel(MEDIA_CHANNEL_ID)
            if not media_channel:
                return False, "Media channel not found"

            file = discord.File(io.BytesIO(file_data), filename=filename)
            try:
                media_msg = await media_channel.send(
                    f"**Media for reply to confession #{self.confession_id}**", file=file
                )
                await asyncio.sleep(2)

                final_url = None
                if media_msg.attachments:
                    final_url = media_msg.attachments[0].url
                elif media_msg.embeds:
                    for embed in media_msg.embeds:
                        if embed.image:
                            final_url = embed.image.url
                            break
                        elif embed.thumbnail:
                            final_url = embed.thumbnail.url
                            break

                if not final_url:
                    return False, "Could not get URL from uploaded media"
                return True, final_url

            except discord.HTTPException as e:
                return False, f"Failed to upload to media channel: {str(e)}"
            except Exception as e:
                return False, f"Error during upload: {str(e)}"

        except Exception as e:
            print(f"[DEBUG] Error in download_and_reupload_media: {e}")
            import traceback
            traceback.print_exc()
            return False, f"Error: {str(e)}"

    async def finalize_reply_directly(self, image_url, interaction):
        """Finalize reply after waiting for possible media."""
        try:
            channel = self.bot.get_channel(CONFESSION_CHANNEL_ID)
            if not channel:
                error_msg = await interaction.followup.send(
                    "❌ Confession channel not found.", ephemeral=True, wait=True
                )
                asyncio.create_task(self.delete_after_delay(error_msg, 8))
                return

            is_owner_reply = self.user_id == self.confession_author_id
            color = discord.Color.gold() if is_owner_reply else discord.Color.green()

            try:
                confession_msg = await channel.fetch_message(self.confession_message_id)
            except discord.NotFound:
                error_msg = await interaction.followup.send(
                    "❌ Original confession not found.", ephemeral=True, wait=True
                )
                asyncio.create_task(self.delete_after_delay(error_msg, 8))
                return
            except discord.Forbidden:
                error_msg = await interaction.followup.send(
                    "❌ No permission to access confession.", ephemeral=True, wait=True
                )
                asyncio.create_task(self.delete_after_delay(error_msg, 8))
                return

            thread = confession_msg.thread
            if not thread:
                try:
                    thread = await confession_msg.create_thread(
                        name=f"Confession #{self.confession_id} Replies",
                        auto_archive_duration=60,
                    )
                except discord.HTTPException:
                    try:
                        thread = await confession_msg.create_thread(
                            name=f"Replies #{self.confession_id}",
                            auto_archive_duration=60,
                        )
                    except Exception as e2:
                        error_msg = await interaction.followup.send(
                            "❌ Could not create discussion thread.", ephemeral=True, wait=True
                        )
                        asyncio.create_task(self.delete_after_delay(error_msg, 8))
                        return

            # Insert into DB first so we have the reply_db_id for the view custom_id
            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute(
                    "INSERT INTO replies (confession_id, user_id, description, image_url) "
                    "VALUES (?, ?, ?, ?)",
                    (self.confession_id, self.user_id, self.reply_text, image_url),
                )
                await db.commit()
                reply_db_id = cursor.lastrowid

            # Calculate hierarchy path for display
            hierarchy_path = await get_reply_hierarchy_path(reply_db_id)

            # Create embed with hierarchy number
            embed = discord.Embed(
                title=f"↩️ Reply #{hierarchy_path} — Confession #{self.confession_id}",
                description=self.reply_text,
                color=color,
                timestamp=discord.utils.utcnow(),
            )
            if image_url:
                embed.set_image(url=image_url)
            if is_owner_reply:
                embed.set_footer(text="👑 OP")

            # Create + register persistent view BEFORE sending so it's tracked
            reply_view = ReplyMessageView(reply_db_id)
            self.bot.add_view(reply_view)

            # Send reply to thread with the view attached
            sent_msg = await thread.send(embed=embed, view=reply_view)

            # Persist the Discord message ID
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "UPDATE replies SET message_id=? WHERE id=?",
                    (sent_msg.id, reply_db_id),
                )
                await db.commit()

            # Dual-channel logging
            await self.log_reply(reply_db_id, image_url, color)

            # Notify OP if not own reply
            if not is_owner_reply:
                await self.notify_author(confession_msg)

            # Send success message with jump URL and delete after 30 seconds
            success_msg = await interaction.followup.send(
                f"✅ Your reply has been posted!\n📝 View it here: {sent_msg.jump_url}",
                ephemeral=True,
                wait=True
            )
            asyncio.create_task(self.delete_after_delay(success_msg, 30))

        except Exception as e:
            print(f"[DEBUG] Error finalizing reply: {e}")
            import traceback
            traceback.print_exc()
            error_msg = await interaction.followup.send(
                f"❌ An error occurred while posting your reply: {str(e)}", ephemeral=True, wait=True
            )
            asyncio.create_task(self.delete_after_delay(error_msg, 8))

    async def log_reply(self, reply_db_id: int, image_url, color):
        """Log reply to admin log (full IDs) and mod log (anonymized)."""
        # Calculate hierarchical path for this reply
        hierarchy_path = await get_reply_hierarchy_path(reply_db_id)
        
        # ── Admin log ──────────────────────────────────────────────────────
        log_channel = self.bot.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            try:
                user = await self.bot.fetch_user(self.user_id)
                log_embed = discord.Embed(
                    title=f"Reply to Confession #{self.confession_id}",
                    description=self.reply_text,
                    color=color,
                    timestamp=discord.utils.utcnow(),
                )
                log_embed.add_field(
                    name="Replier",
                    value=f"{user.mention} ({user.id})",
                    inline=False,
                )
                log_embed.add_field(
                    name="Confession Author",
                    value=f"<@{self.confession_author_id}> ({self.confession_author_id})",
                    inline=False,
                )
                log_embed.add_field(
                    name="Reply Number", 
                    value=f"Reply **#{hierarchy_path}**", 
                    inline=True
                )
                log_embed.add_field(name="Reply DB ID", value=str(reply_db_id), inline=True)
                if image_url:
                    log_embed.set_image(url=image_url)
                await log_channel.send(embed=log_embed)
            except Exception as e:
                print(f"[log_reply] Admin log error: {e}")

        # ── Mod log (anonymized) ───────────────────────────────────────────
        mod_log_channel = self.bot.get_channel(MOD_LOG_CHANNEL_ID) if MOD_LOG_CHANNEL_ID else None
        if mod_log_channel:
            try:
                replier_hash = sha256_user_id(self.user_id)
                author_hash = sha256_user_id(self.confession_author_id)
                mod_embed = discord.Embed(
                    title=f"Reply to Confession #{self.confession_id}",
                    description=self.reply_text,
                    color=color,
                    timestamp=discord.utils.utcnow(),
                )
                mod_embed.add_field(
                    name="Replier (hash)", value=f"`{replier_hash}`", inline=False
                )
                mod_embed.add_field(
                    name="Confession Author (hash)",
                    value=f"`{author_hash}`",
                    inline=False,
                )
                mod_embed.add_field(
                    name="Reply Number", 
                    value=f"Reply **#{hierarchy_path}**", 
                    inline=True
                )
                mod_embed.add_field(name="Reply DB ID", value=str(reply_db_id), inline=True)
                if image_url:
                    mod_embed.set_image(url=image_url)
                await mod_log_channel.send(embed=mod_embed)
            except Exception as e:
                print(f"[log_reply] Mod log error: {e}")

    async def notify_author(self, confession_msg):
        """Notify confession author about new reply."""
        try:
            confession_author = await self.bot.fetch_user(self.confession_author_id)
            notify_embed = discord.Embed(
                title=f"💌 New Reply to Your Confession #{self.confession_id}",
                description=(
                    f"Someone replied: \"{self.reply_text[:100]}"
                    f"{'...' if len(self.reply_text) > 100 else ''}\""
                ),
                color=discord.Color.blue(),
                timestamp=discord.utils.utcnow(),
            )
            notify_embed.add_field(
                name="View Thread",
                value=f"[Click here to view the conversation]({confession_msg.jump_url})",
                inline=False,
            )
            await confession_author.send(embed=notify_embed)
        except discord.Forbidden:
            pass
        except Exception as e:
            print(f"[notify_author] Error: {e}")


# ==============================
# REPLY-TO-REPLY MODAL
# ==============================

class ReplyToReplyModal(ui.Modal, title="Anonymous Reply"):
    reply_input = ui.TextInput(
        label="Reply",
        style=discord.TextStyle.paragraph,
        max_length=500,
        placeholder="Type your reply here...",
        required=True,
    )

    def __init__(
        self,
        bot,
        confession_id: int,
        parent_reply_id: int,
        parent_reply_message_id: int,
        parent_reply_author_id: int,
        confession_author_id: int,
        confession_message_id: int,
    ):
        super().__init__()
        self.bot = bot
        self.confession_id = confession_id
        self.parent_reply_id = parent_reply_id
        self.parent_reply_message_id = parent_reply_message_id
        self.parent_reply_author_id = parent_reply_author_id
        self.confession_author_id = confession_author_id
        self.confession_message_id = confession_message_id
        self.user_id = None
        self.reply_text = None

    async def on_submit(self, interaction: discord.Interaction):
        self.user_id = interaction.user.id
        self.reply_text = self.reply_input.value

        await interaction.response.defer(ephemeral=True, thinking=True)

        can_reply, reason, current_count = await can_user_reply(
            self.user_id, HOURLY_REPLY_LIMIT
        )
        if not can_reply:
            error_msg = await interaction.followup.send(reason, ephemeral=True, wait=True)
            asyncio.create_task(self.delete_after_delay(error_msg, 8))
            return

        remaining = HOURLY_REPLY_LIMIT - current_count
        usage_msg = (
            f"📊 You've made {current_count}/{HOURLY_REPLY_LIMIT} replies this hour. "
            f"{remaining} remaining."
        )

        # Send media prompt and schedule deletion
        media_prompt = await interaction.followup.send(
            f"✅ Reply submitted! {usage_msg}\n\n"
            f"You have **60 seconds** to add an image (optional).\n"
            f"Send `skip` to continue without media.\n\n"
            f"**Accepted formats:**\n"
            f"• Discord image upload (drag & drop)\n"
            f"• Direct image URL (.png/.jpg/.gif/.webp)",
            ephemeral=True,
            wait=True
        )
        asyncio.create_task(self.delete_after_delay(media_prompt, 60))

        cleaner_cog = self.bot.get_cog("ConfessionCleaner")
        if cleaner_cog:
            await cleaner_cog.allow_user_once(self.user_id)

        image_url = await self._wait_for_media(interaction)
        await self._finalize_reply(image_url, interaction)

    async def delete_after_delay(self, message, delay: int):
        """Helper method to delete a message after a delay."""
        await asyncio.sleep(delay)
        try:
            await message.delete()
        except discord.NotFound:
            pass
        except discord.HTTPException as e:
            print(f"Failed to delete message: {e}")

    # ── Media wait (same logic as ReplyModal) ─────────────────────────────

    async def _wait_for_media(self, interaction) -> str | None:
        def check(m):
            return (
                m.author.id == self.user_id
                and m.channel.id == interaction.channel.id
            )

        try:
            msg_received = await self.bot.wait_for("message", timeout=60.0, check=check)

            if msg_received.content.lower().strip() == "skip":
                try:
                    await msg_received.delete()
                except Exception:
                    pass
                skip_msg = await interaction.followup.send("✅ Skipped media upload.", ephemeral=True, wait=True)
                asyncio.create_task(self.delete_after_delay(skip_msg, 5))
                return None

            has_attachments = len(msg_received.attachments) > 0
            has_valid_url = False
            if msg_received.content and msg_received.content.strip():
                url = extract_url(msg_received.content)
                if url:
                    ul = url.lower()
                    if any(ul.endswith(e) for e in [".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"]):
                        has_valid_url = True
                    elif "media.discordapp.net" in ul or "cdn.discordapp.com" in ul:
                        has_valid_url = True

            if not has_attachments and not has_valid_url:
                try:
                    await msg_received.delete()
                except Exception:
                    pass
                error_msg = await interaction.followup.send(
                    "❌ No valid media found. Continuing without media.", ephemeral=True, wait=True
                )
                asyncio.create_task(self.delete_after_delay(error_msg, 10))
                return None

            success, result = await self._download_and_reupload(
                msg_received.content, msg_received.attachments
            )
            try:
                await msg_received.delete()
            except Exception:
                pass

            if success:
                success_msg = await interaction.followup.send("✅ Media added to reply!", ephemeral=True, wait=True)
                asyncio.create_task(self.delete_after_delay(success_msg, 5))
                return result
            else:
                error_msg = await interaction.followup.send(
                    f"❌ Failed to process media: {result}\nContinuing without media.",
                    ephemeral=True, wait=True
                )
                asyncio.create_task(self.delete_after_delay(error_msg, 10))
                return None

        except asyncio.TimeoutError:
            timeout_msg = await interaction.followup.send(
                "⏰ Media upload timed out. Continuing without media.", ephemeral=True, wait=True
            )
            asyncio.create_task(self.delete_after_delay(timeout_msg, 5))
            return None
        except Exception as e:
            print(f"[ReplyToReplyModal] wait_for_media error: {e}")
            return None

    async def _download_and_reupload(self, content, attachments):
        """Identical media download/reupload logic used by ReplyModal."""
        try:
            media_url = None
            filename = None

            if attachments:
                media_url = attachments[0].url
                filename = attachments[0].filename
            elif content and content.strip():
                media_url = extract_url(content)
                if media_url:
                    filename = media_url.split("/")[-1].split("?")[0]
                    if "." not in filename:
                        filename = f"image_{self.confession_id}.jpg"

            if not media_url:
                return False, "No media found"

            async with aiohttp.ClientSession() as session:
                headers = {"User-Agent": "Mozilla/5.0"}
                try:
                    async with session.get(media_url, headers=headers, timeout=30) as resp:
                        if resp.status != 200:
                            return False, f"HTTP {resp.status}"
                        file_data = await resp.read()
                        if not filename:
                            ct = resp.headers.get("Content-Type", "")
                            ext_map = {"image/jpeg": ".jpg", "image/png": ".png",
                                       "image/gif": ".gif", "image/webp": ".webp"}
                            filename = f"image_{self.confession_id}" + ext_map.get(ct, ".jpg")
                except Exception as e:
                    return False, str(e)

            media_channel = self.bot.get_channel(MEDIA_CHANNEL_ID)
            if not media_channel:
                return False, "Media channel not found"

            file = discord.File(io.BytesIO(file_data), filename=filename)
            media_msg = await media_channel.send(
                f"**Media for reply to confession #{self.confession_id}**", file=file
            )
            await asyncio.sleep(2)
            if media_msg.attachments:
                return True, media_msg.attachments[0].url
            return False, "Could not get URL from uploaded media"

        except Exception as e:
            print(f"[ReplyToReplyModal] download error: {e}")
            return False, str(e)

    # ── Finalize ───────────────────────────────────────────────────────────

    async def _finalize_reply(self, image_url, interaction):
        try:
            confession_channel = self.bot.get_channel(CONFESSION_CHANNEL_ID)
            if not confession_channel:
                error_msg = await interaction.followup.send(
                    "❌ Confession channel not found.", ephemeral=True, wait=True
                )
                asyncio.create_task(self.delete_after_delay(error_msg, 8))
                return

            try:
                confession_msg = await confession_channel.fetch_message(
                    self.confession_message_id
                )
            except discord.NotFound:
                error_msg = await interaction.followup.send(
                    "❌ Original confession not found.", ephemeral=True, wait=True
                )
                asyncio.create_task(self.delete_after_delay(error_msg, 8))
                return

            thread = confession_msg.thread
            if not thread:
                error_msg = await interaction.followup.send(
                    "❌ Discussion thread not found.", ephemeral=True, wait=True
                )
                asyncio.create_task(self.delete_after_delay(error_msg, 8))
                return

            # Try to fetch parent reply message for Discord native reply reference
            parent_msg = None
            if self.parent_reply_message_id:
                try:
                    parent_msg = await thread.fetch_message(self.parent_reply_message_id)
                except discord.NotFound:
                    pass  # Fallback: send without reference if message was deleted

            is_owner_reply = self.user_id == self.confession_author_id
            color = discord.Color.gold() if is_owner_reply else discord.Color.teal()

            # Insert into DB first to get reply_db_id
            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute(
                    "INSERT INTO replies "
                    "(confession_id, parent_reply_id, user_id, description, image_url) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        self.confession_id,
                        self.parent_reply_id,
                        self.user_id,
                        self.reply_text,
                        image_url,
                    ),
                )
                await db.commit()
                reply_db_id = cursor.lastrowid

            # Calculate hierarchy paths for display
            hierarchy_path = await get_reply_hierarchy_path(reply_db_id)
            parent_hierarchy_path = await get_reply_hierarchy_path(self.parent_reply_id)

            # Create embed with hierarchy number
            embed = discord.Embed(
                title=f"↩️ Reply #{hierarchy_path} (to #{parent_hierarchy_path}) — Confession #{self.confession_id}",
                description=self.reply_text,
                color=color,
                timestamp=discord.utils.utcnow(),
            )
            if image_url:
                embed.set_image(url=image_url)
            if is_owner_reply:
                embed.set_footer(text="👑 OP")

            # Register persistent view before sending
            reply_view = ReplyMessageView(reply_db_id)
            self.bot.add_view(reply_view)

            # Send as a Discord "message reply" to the parent reply (if available)
            if parent_msg:
                sent_msg = await thread.send(
                    embed=embed,
                    view=reply_view,
                    reference=parent_msg,
                    mention_author=False,  # keep it anonymous — no ping
                )
            else:
                sent_msg = await thread.send(embed=embed, view=reply_view)

            # Persist the Discord message ID
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "UPDATE replies SET message_id=? WHERE id=?",
                    (sent_msg.id, reply_db_id),
                )
                await db.commit()

            # Dual-channel logging
            await self._log_reply(reply_db_id, image_url, color)

            # DM the person whose reply was replied to
            await self._notify_replier(sent_msg, is_owner_reply)

            # Send success message with jump URL and delete after 30 seconds
            success_msg = await interaction.followup.send(
                f"✅ Reply posted!\n📝 View it here: {sent_msg.jump_url}",
                ephemeral=True,
                wait=True
            )
            asyncio.create_task(self.delete_after_delay(success_msg, 30))

        except Exception as e:
            print(f"[ReplyToReplyModal] finalize error: {e}")
            import traceback
            traceback.print_exc()
            error_msg = await interaction.followup.send(
                "❌ An error occurred while posting your reply.", ephemeral=True, wait=True
            )
            asyncio.create_task(self.delete_after_delay(error_msg, 8))

    async def _log_reply(self, reply_db_id: int, image_url, color):
        # Calculate hierarchical path for this reply
        hierarchy_path = await get_reply_hierarchy_path(reply_db_id)
        parent_hierarchy_path = await get_reply_hierarchy_path(self.parent_reply_id)
        
        # ── Admin log ──────────────────────────────────────────────────────
        log_channel = self.bot.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            try:
                user = await self.bot.fetch_user(self.user_id)
                embed = discord.Embed(
                    title=f"Reply-to-Reply — Confession #{self.confession_id}",
                    description=self.reply_text,
                    color=color,
                    timestamp=discord.utils.utcnow(),
                )
                embed.add_field(name="Replier", value=f"{user.mention} ({user.id})", inline=False)
                embed.add_field(
                    name="Reply Hierarchy", 
                    value=f"Reply **#{hierarchy_path}** (replying to #{parent_hierarchy_path})", 
                    inline=False
                )
                embed.add_field(
                    name="Parent Reply ID", value=str(self.parent_reply_id), inline=True
                )
                embed.add_field(name="Reply DB ID", value=str(reply_db_id), inline=True)
                if image_url:
                    embed.set_image(url=image_url)
                await log_channel.send(embed=embed)
            except Exception as e:
                print(f"[ReplyToReplyModal] admin log error: {e}")

        # ── Mod log (anonymized) ───────────────────────────────────────────
        mod_log_channel = (
            self.bot.get_channel(MOD_LOG_CHANNEL_ID) if MOD_LOG_CHANNEL_ID else None
        )
        if mod_log_channel:
            try:
                replier_hash = sha256_user_id(self.user_id)
                embed = discord.Embed(
                    title=f"Reply-to-Reply — Confession #{self.confession_id}",
                    description=self.reply_text,
                    color=color,
                    timestamp=discord.utils.utcnow(),
                )
                embed.add_field(
                    name="Replier (hash)", value=f"`{replier_hash}`", inline=False
                )
                embed.add_field(
                    name="Reply Hierarchy", 
                    value=f"Reply **#{hierarchy_path}** (replying to #{parent_hierarchy_path})", 
                    inline=False
                )
                embed.add_field(
                    name="Parent Reply ID", value=str(self.parent_reply_id), inline=True
                )
                embed.add_field(name="Reply DB ID", value=str(reply_db_id), inline=True)
                if image_url:
                    embed.set_image(url=image_url)
                await mod_log_channel.send(embed=embed)
            except Exception as e:
                print(f"[ReplyToReplyModal] mod log error: {e}")

    async def _notify_replier(self, new_reply_msg: discord.Message, is_owner_reply: bool):
        """DM the author of the reply that was just replied to."""
        try:
            if self.parent_reply_author_id == self.user_id:
                return  # Don't DM yourself
            
            replier = await self.bot.fetch_user(self.parent_reply_author_id)
            
            # Determine title based on whether OP replied
            title = "👑 The OP Replied to Your Reply!" if is_owner_reply else "💬 Someone Replied to Your Reply"
            
            embed = discord.Embed(
                title=title,
                description=(
                    f"{'**OP** replied' if is_owner_reply else 'Someone replied'}: \"{self.reply_text[:100]}"
                    f"{'...' if len(self.reply_text) > 100 else ''}\""
                ),
                color=discord.Color.gold() if is_owner_reply else discord.Color.blurple(),
                timestamp=discord.utils.utcnow(),
            )
            
            # Link to the NEW reply (the one that just got posted)
            embed.add_field(
                name="🔗 View the Reply", 
                value=f"[Click here to see the reply]({new_reply_msg.jump_url})", 
                inline=False
            )
            
            if is_owner_reply:
                embed.set_footer(text="The confession author (OP) has replied to you!")
            else:
                embed.set_footer(text="All identities remain anonymous.")
                
            await replier.send(embed=embed)
        except discord.Forbidden:
            pass  # User has DMs closed
        except Exception as e:
            print(f"[ReplyToReplyModal] notify_replier error: {e}")