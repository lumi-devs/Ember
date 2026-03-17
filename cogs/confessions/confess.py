import ast
import os

import discord
from discord import ui, app_commands
from discord.ext import commands
from datetime import datetime
import asyncio
import aiosqlite
import aiohttp
import io

from .helpers import (
    DB_PATH,
    extract_url,
    init_db,
    is_user_banned,
    get_confession_by_id,
    can_user_confess,
    sha256_user_id,
    LOG_CHANNEL_ID,
    MOD_LOG_CHANNEL_ID,
)
from .reply import ReplyModal, ReplyMessageView

DAILY_CONFESSION_LIMIT = int(os.getenv("DAILY_CONFESSION_LIMIT", 3))

# ==============================
# CONFIGURATION
# ==============================

CONFESSION_CHANNEL_ID = int(os.getenv("CONFESSION_CHANNEL_ID"))
REPORT_CHANNEL_ID = int(os.getenv("REPORT_CHANNEL_ID"))
MEDIA_CHANNEL_ID = int(os.getenv("MEDIA_CHANNEL_ID"))
HOURLY_REPORT_LIMIT = int(os.getenv("HOURLY_REPORT_LIMIT", 5))
ALLOWED_ROLE_IDS_STR = os.environ.get("ALLOWED_ROLE_IDS", "[]")

try:
    ALLOWED_ROLE_IDS = ast.literal_eval(ALLOWED_ROLE_IDS_STR)
except (SyntaxError, ValueError):
    ALLOWED_ROLE_IDS = [int(role_id.strip()) for role_id in ALLOWED_ROLE_IDS_STR.strip("[]").split(",") if role_id.strip()]

class ConfessionModal(ui.Modal, title="Submit a Confession"):
    title_input = ui.TextInput(
        label="Title",
        max_length=100,
        required=False,
        placeholder="Optional title for your confession",
    )
    description_input = ui.TextInput(
        label="Description",
        style=discord.TextStyle.paragraph,
        max_length=1000,
        placeholder="Share your thoughts...",
        required=True,
    )

    def __init__(self, bot):
        super().__init__()
        self.bot = bot
        self.user_id = None
        self.last_confession_button = None

    async def on_submit(self, interaction: discord.Interaction):
        self.user_id = interaction.user.id

        await interaction.response.defer(ephemeral=True, thinking=True)

        if await is_user_banned(self.user_id):
            return await interaction.followup.send(
                "❌ You are banned from submitting confessions.", ephemeral=True
            )

        can_confess, reason = await can_user_confess(
            interaction.guild, self.user_id, DAILY_CONFESSION_LIMIT
        )
        if not can_confess:
            return await interaction.followup.send(reason, ephemeral=True)

        channel = self.bot.get_channel(CONFESSION_CHANNEL_ID)
        if not channel:
            return await interaction.followup.send(
                "❌ Confession channel not found.", ephemeral=True
            )

        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute("SELECT MAX(id) FROM confessions")
                result = await cursor.fetchone()
                prev_confession_id = result[0] if result[0] else None

            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute(
                    "INSERT INTO confessions (user_id, title, description) VALUES (?, ?, ?)",
                    (self.user_id, self.title_input.value, self.description_input.value),
                )
                await db.commit()
                confession_id = cursor.lastrowid

            embed = discord.Embed(
                title=f"💌 {self.title_input.value or f'Confession #{confession_id}'}",
                description=self.description_input.value,
                color=discord.Color.purple(),
                timestamp=discord.utils.utcnow(),
            )
            embed.set_footer(text=f"Confession #{confession_id}")

            msg = await channel.send(embed=embed)

            thread_name = f"Confession #{confession_id}"
            try:
                thread = await msg.create_thread(name=thread_name, auto_archive_duration=60)
            except discord.HTTPException as e:
                print(f"Error creating thread: {e}")
                thread = None

            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "UPDATE confessions SET message_id=? WHERE id=?",
                    (msg.id, confession_id),
                )
                await db.commit()

            await interaction.followup.send(
                "✅ Confession submitted! You have **1 minute** to reply with an image URL or "
                "attachment. Send `skip` to continue without media.\n\n"
                f"📝 Your confession ID: #{confession_id}",
                ephemeral=True,
            )

            image_url = await self.wait_for_media(interaction, confession_id, msg)

            if image_url:
                embed.set_image(url=image_url)
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute(
                        "UPDATE confessions SET image_url=? WHERE id=?",
                        (image_url, confession_id),
                    )
                    await db.commit()

            if prev_confession_id:
                await self.remove_prev_confess_button(prev_confession_id)

            final_view = PersistentConfessionView(confession_id, show_confess_button=True)
            await msg.edit(embed=embed, view=final_view)

            await self.log_confession(confession_id, image_url)

            if thread:
                thread_msg = f"A discussion thread has been created: {thread.mention}"
            else:
                thread_msg = "Note: Could not create discussion thread."

            await interaction.followup.send(
                f"✅ Confession #{confession_id} is now public! {thread_msg}",
                ephemeral=True,
            )

        except Exception as e:
            print(f"Error submitting confession: {e}")
            import traceback
            traceback.print_exc()
            await interaction.followup.send(
                "❌ An error occurred while submitting your confession.", ephemeral=True
            )

    async def remove_prev_confess_button(self, confession_id):
        """Remove confess button from previous confession."""
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute(
                    "SELECT message_id FROM confessions WHERE id=?", (confession_id,)
                )
                result = await cursor.fetchone()
                if not result:
                    return
                message_id = result[0]
                channel = self.bot.get_channel(CONFESSION_CHANNEL_ID)
                if not channel or not message_id:
                    return
                try:
                    msg = await channel.fetch_message(message_id)
                    new_view = PersistentConfessionView(confession_id, show_confess_button=False)
                    await msg.edit(view=new_view)
                except discord.NotFound:
                    print(f"Previous confession message {message_id} not found")
                except discord.HTTPException as e:
                    print(f"Error updating previous confession: {e}")
        except Exception as e:
            print(f"Error removing previous confess button: {e}")

    async def wait_for_media(self, interaction, confession_id, confession_msg):
        """Wait for media response with proper timeout handling."""

        def check(m):
            return (
                m.author.id == self.user_id and m.channel.id == interaction.channel.id
            )

        try:
            await interaction.followup.send(
                "⏳ Waiting for media... You have 60 seconds.", ephemeral=True
            )

            msg_received = await self.bot.wait_for("message", timeout=60.0, check=check)

            content_lower = msg_received.content.lower().strip()
            if content_lower == "skip":
                try:
                    await msg_received.delete()
                except Exception:
                    pass
                await interaction.followup.send("✅ Skipped media upload.", ephemeral=True)
                final_view = PersistentConfessionView(confession_id, show_confess_button=True)
                await confession_msg.edit(view=final_view)
                thread = confession_msg.thread
                thread_msg = (
                    f"A discussion thread has been created: {thread.mention}"
                    if thread else "Note: Could not create discussion thread."
                )
                await interaction.followup.send(
                    f"✅ Confession #{confession_id} is now public! {thread_msg}", ephemeral=True
                )
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
                    error_msg += (
                        "⚠️ The URL you provided doesn't appear to be an image link.\n"
                        "Please use direct image links (ending with .png/.jpg/.gif/.webp)\n\n"
                    )
                else:
                    error_msg += (
                        "**Valid options are:**\n"
                        "• Upload an image file (drag & drop)\n"
                        "• Send a direct image URL\n"
                        "• Type `skip` to continue without media\n\n"
                    )
                error_msg += "Continuing without media."
                await interaction.followup.send(error_msg, ephemeral=True)

                final_view = PersistentConfessionView(confession_id, show_confess_button=True)
                await confession_msg.edit(view=final_view)
                thread = confession_msg.thread
                thread_msg = (
                    f"A discussion thread has been created: {thread.mention}"
                    if thread else "Note: Could not create discussion thread."
                )
                await interaction.followup.send(
                    f"✅ Confession #{confession_id} is now public! {thread_msg}", ephemeral=True
                )
                return None

            success, result = await self.download_and_reupload_media(
                msg_received.content, msg_received.attachments, confession_id
            )

            try:
                await msg_received.delete()
            except discord.Forbidden:
                print(f"No permission to delete message from {msg_received.author}")
            except discord.NotFound:
                print("Message already deleted")
            except Exception as e:
                print(f"Error deleting message: {e}")

            if success:
                embed = confession_msg.embeds[0] if confession_msg.embeds else None
                if embed:
                    embed.set_image(url=result)
                    async with aiosqlite.connect(DB_PATH) as db:
                        await db.execute(
                            "UPDATE confessions SET image_url=? WHERE id=?",
                            (result, confession_id),
                        )
                        await db.commit()

                await interaction.followup.send("✅ Media added to confession!", ephemeral=True)

                final_view = PersistentConfessionView(confession_id, show_confess_button=True)
                await confession_msg.edit(embed=embed, view=final_view)

                thread = confession_msg.thread
                thread_msg = (
                    f"A discussion thread has been created: {thread.mention}"
                    if thread else "Note: Could not create discussion thread."
                )
                await interaction.followup.send(
                    f"✅ Confession #{confession_id} is now public! {thread_msg}", ephemeral=True
                )
                return result
            else:
                error_msg = result if isinstance(result, str) else "Unknown error occurred"
                await interaction.followup.send(
                    f"❌ Failed to process media: {error_msg}\nContinuing without media.",
                    ephemeral=True,
                )
                await self.add_fallback_buttons(confession_msg, confession_id, error_msg)
                thread = confession_msg.thread
                thread_msg = (
                    f"A discussion thread has been created: {thread.mention}"
                    if thread else "Note: Could not create discussion thread."
                )
                await interaction.followup.send(
                    f"✅ Confession #{confession_id} is now public! {thread_msg}", ephemeral=True
                )
                return None

        except asyncio.TimeoutError:
            await interaction.followup.send(
                "⏰ Media upload timed out. Continuing without media.", ephemeral=True
            )
            final_view = PersistentConfessionView(confession_id, show_confess_button=True)
            await confession_msg.edit(view=final_view)
            thread = confession_msg.thread
            thread_msg = (
                f"A discussion thread has been created: {thread.mention}"
                if thread else "Note: Could not create discussion thread."
            )
            await interaction.followup.send(
                f"✅ Confession #{confession_id} is now public! {thread_msg}", ephemeral=True
            )
            return None
        except Exception as e:
            print(f"Error in wait_for_media: {e}")
            import traceback
            traceback.print_exc()
            await interaction.followup.send(
                "❌ An error occurred while processing media.", ephemeral=True
            )
            final_view = PersistentConfessionView(confession_id, show_confess_button=True)
            await confession_msg.edit(view=final_view)
            thread = confession_msg.thread
            thread_msg = (
                f"A discussion thread has been created: {thread.mention}"
                if thread else "Note: Could not create discussion thread."
            )
            await interaction.followup.send(
                f"✅ Confession #{confession_id} is now public! {thread_msg}", ephemeral=True
            )
            return None

    async def download_and_reupload_media(self, content, attachments, confession_id):
        """Download media and re-upload to media channel, then return new URL."""
        try:
            print(f"[DEBUG] Starting media processing for confession #{confession_id}")

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
                            filename = f"gif_{confession_id}.gif"
                        else:
                            filename = f"image_{confession_id}.jpg"
                    print(f"[DEBUG] Found URL: {media_url}, filename: {filename}")

            if not media_url:
                print("[DEBUG] No media URL found")
                return False, "No media found in message"

            print(f"[DEBUG] Downloading media from: {media_url}")
            async with aiohttp.ClientSession() as session:
                try:
                    headers = {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                    }
                    async with session.get(media_url, headers=headers, timeout=30) as response:
                        if response.status != 200:
                            return False, f"Failed to download media: HTTP {response.status}"
                        file_data = await response.read()
                        print(f"[DEBUG] Downloaded {len(file_data)} bytes")
                        if not filename:
                            content_type = response.headers.get("Content-Type", "")
                            ext_map = {
                                "image/jpeg": ".jpg", "image/jpg": ".jpg",
                                "image/png": ".png", "image/gif": ".gif",
                                "image/webp": ".webp",
                            }
                            filename = f"image_{confession_id}" + ext_map.get(content_type, ".jpg")
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
                    f"**Media for confession #{confession_id}**", file=file
                )
                await asyncio.sleep(2)

                final_url = None
                if media_msg.attachments and len(media_msg.attachments) > 0:
                    final_url = media_msg.attachments[0].url
                elif media_msg.embeds and len(media_msg.embeds) > 0:
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

    async def add_fallback_buttons(self, confession_msg, confession_id, error_msg):
        """Add fallback buttons to confession when media processing fails."""
        try:
            try:
                await confession_msg.channel.fetch_message(confession_msg.id)
            except discord.NotFound:
                return

            fallback_view = FallbackMediaView(confession_id, error_msg)
            embed = confession_msg.embeds[0] if confession_msg.embeds else None
            await confession_msg.edit(embed=embed, view=fallback_view)

            final_view = PersistentConfessionView(confession_id, show_confess_button=False)
            for item in fallback_view.children:
                final_view.add_item(item)
            await confession_msg.edit(view=final_view)

        except discord.HTTPException as e:
            print(f"HTTP error adding fallback buttons: {e}")
            try:
                simple_view = ui.View(timeout=None)
                simple_view.add_item(RetryMediaButton(confession_id, error_msg))
                simple_view.add_item(ManualUploadButton(confession_id))
                await confession_msg.edit(view=simple_view)
            except Exception as e2:
                print(f"Failed to add simple fallback buttons: {e2}")
        except Exception as e:
            print(f"Error adding fallback buttons: {e}")
            import traceback
            traceback.print_exc()

    async def log_confession(self, confession_id, image_url):
        """Log confession to admin log (full IDs) and mod log (anonymized)."""
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute(
                    "SELECT user_id, title, description FROM confessions WHERE id=?",
                    (confession_id,),
                )
                confession = await cursor.fetchone()
                if not confession:
                    return
                user_id, title, description = confession
        except Exception as e:
            print(f"Error fetching confession for log: {e}")
            return

        # ── Admin log (full user info) ─────────────────────────────────────
        log_channel = self.bot.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            try:
                user = await self.bot.fetch_user(user_id)
                log_embed = discord.Embed(
                    title=f"Confession #{confession_id}",
                    description=description,
                    color=discord.Color.purple(),
                    timestamp=discord.utils.utcnow(),
                )
                log_embed.add_field(
                    name="User", value=f"{user.mention} ({user.id})", inline=False
                )
                if title:
                    log_embed.add_field(name="Title", value=title, inline=False)
                if image_url:
                    log_embed.set_image(url=image_url)
                await log_channel.send(embed=log_embed)
            except Exception as e:
                print(f"Error logging confession (admin): {e}")

        # ── Mod log (anonymized) ───────────────────────────────────────────
        mod_log_channel = self.bot.get_channel(MOD_LOG_CHANNEL_ID) if MOD_LOG_CHANNEL_ID else None
        if mod_log_channel:
            try:
                user_hash = sha256_user_id(user_id)
                mod_embed = discord.Embed(
                    title=f"Confession #{confession_id}",
                    description=description,
                    color=discord.Color.purple(),
                    timestamp=discord.utils.utcnow(),
                )
                mod_embed.add_field(
                    name="User (hash)", value=f"`{user_hash}`", inline=False
                )
                if title:
                    mod_embed.add_field(name="Title", value=title, inline=False)
                if image_url:
                    mod_embed.set_image(url=image_url)
                await mod_log_channel.send(embed=mod_embed)
            except Exception as e:
                print(f"Error logging confession (mod): {e}")


# ==============================
# FALLBACK MEDIA VIEW
# ==============================
class FallbackMediaView(ui.View):
    def __init__(self, confession_id, error_msg):
        super().__init__(timeout=None)
        self.confession_id = confession_id
        self.error_msg = error_msg
        self.add_item(RetryMediaButton(confession_id, error_msg))
        self.add_item(ManualUploadButton(confession_id))


class RetryMediaButton(ui.Button):
    def __init__(self, confession_id, error_msg):
        super().__init__(
            label="Retry Media",
            style=discord.ButtonStyle.secondary,
            custom_id=f"retry_media_{confession_id}",
        )
        self.confession_id = confession_id
        self.error_msg = error_msg

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            f"❌ Media processing failed: {self.error_msg}\n\n"
            "Please try again with:\n"
            "• **Direct image URL** (must end with .png/.jpg/.gif/.webp)\n"
            "• **Discord attachment** (drag & drop)\n"
            "• Use the 'Manual Upload' button below",
            ephemeral=True,
        )


class ManualUploadButton(ui.Button):
    def __init__(self, confession_id):
        super().__init__(
            label="Manual Upload",
            style=discord.ButtonStyle.primary,
            custom_id=f"manual_upload_{confession_id}",
        )
        self.confession_id = confession_id

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "📤 **Manual Media Upload Instructions:**\n\n"
            "1. Go to our media channel: <#1424139009931743382>\n"
            "2. Upload your image there\n"
            "3. Copy the image URL\n"
            "4. Click 'Retry Media' and paste the URL\n\n"
            "The bot will fetch the image from our media channel.",
            ephemeral=True,
        )


# ==============================
# PERSISTENT CONFESSION VIEW (Buttons)
# ==============================
class PersistentConfessionView(ui.View):
    def __init__(self, confession_id, show_confess_button=False):
        super().__init__(timeout=None)
        self.confession_id = confession_id
        if show_confess_button:
            self.add_item(PersistentConfessButton())
        self.add_item(PersistentReplyButton(confession_id))
        self.add_item(PersistentReportButton(confession_id))


# ==============================
# PERSISTENT BUTTONS
# ==============================
class PersistentReplyButton(ui.Button):
    def __init__(self, confession_id):
        super().__init__(
            label="Reply",
            style=discord.ButtonStyle.secondary,
            custom_id=f"confession_reply_{confession_id}",
        )
        self.confession_id = confession_id

    async def callback(self, interaction: discord.Interaction):
        bot = interaction.client
        confession = await get_confession_by_id(self.confession_id)
        if not confession:
            return await interaction.response.send_message(
                "❌ Confession not found.", ephemeral=True
            )
        message_id, author_id = confession[5], confession[1]
        modal = ReplyModal(bot, self.confession_id, message_id, author_id)
        await interaction.response.send_modal(modal)


class PersistentReportButton(ui.Button):
    def __init__(self, confession_id):
        super().__init__(
            label="Report",
            style=discord.ButtonStyle.danger,
            custom_id=f"confession_report_{confession_id}",
        )
        self.confession_id = confession_id

    async def callback(self, interaction: discord.Interaction):
        bot = interaction.client
        from .helpers import can_user_report
        can_report, reason, current_count = await can_user_report(
            interaction.user.id, HOURLY_REPORT_LIMIT
        )
        if not can_report:
            return await interaction.response.send_message(reason, ephemeral=True)
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute(
                    "SELECT user_id, title, description, image_url FROM confessions WHERE id=?",
                    (self.confession_id,),
                )
                confession = await cursor.fetchone()
                if not confession:
                    return await interaction.response.send_message(
                        "❌ Confession not found.", ephemeral=True
                    )
                author_id, title, description, image_url = confession

                await db.execute(
                    "INSERT INTO reports (target_type, target_id, reporter_id) VALUES (?, ?, ?)",
                    ("confession", self.confession_id, interaction.user.id),
                )
                await db.commit()

                cursor = await db.execute(
                    "SELECT 1 FROM confessban WHERE user_id=?", (author_id,)
                )
                banned = await cursor.fetchone()

            embed = discord.Embed(
                title=f"🚨 Confession Report #{self.confession_id}",
                color=discord.Color.red(),
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(name="Confession ID", value=str(self.confession_id), inline=False)
            embed.add_field(name="Author ID", value=sha256_user_id(author_id), inline=False)
            embed.add_field(
                name="Reported by",
                value=f"{interaction.user} ({interaction.user.id})",
                inline=False,
            )
            embed.add_field(name="Banned?", value="Yes" if banned else "No", inline=False)
            embed.add_field(name="Title", value=title or "No title", inline=False)
            embed.add_field(name="Content", value=description or "No content", inline=False)
            if image_url:
                embed.set_image(url=image_url)

            mod_channel = bot.get_channel(REPORT_CHANNEL_ID)
            if mod_channel:
                await mod_channel.send(embed=embed)

            await interaction.response.send_message("✅ Report submitted.", ephemeral=True)

        except Exception as e:
            print(f"Error reporting confession: {e}")
            await interaction.response.send_message(
                "❌ An error occurred while reporting.", ephemeral=True
            )


class PersistentConfessButton(ui.Button):
    def __init__(self):
        super().__init__(
            label="Make a Confession",
            style=discord.ButtonStyle.primary,
            custom_id="persistent_confess_button",
        )

    async def callback(self, interaction: discord.Interaction):
        if not any(role.id in ALLOWED_ROLE_IDS for role in interaction.user.roles):
            return await interaction.response.send_message(
                "❌ You do not have at least LVL 5 required to make confessions.",
                ephemeral=True,
            )
        if await is_user_banned(interaction.user.id):
            return await interaction.response.send_message(
                "❌ You are banned from making confessions.", ephemeral=True
            )
        can_confess, reason = await can_user_confess(
            interaction.guild, interaction.user.id, DAILY_CONFESSION_LIMIT
        )
        if not can_confess:
            return await interaction.response.send_message(reason, ephemeral=True)
        bot = interaction.client
        modal = ConfessionModal(bot)
        await interaction.response.send_modal(modal)


# ==============================
# MAIN COG
# ==============================
class Confessions(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.initialized = False
        self.bot.loop.create_task(self.initialize())

    async def initialize(self):
        """Initialize database and load persistent views."""
        try:
            await init_db()
            await self.load_persistent_views()
            self.initialized = True
            print("[Confessions] ✅ System initialized successfully.")
        except Exception as e:
            print(f"[Confessions] ❌ Initialization failed: {e}")

    async def load_persistent_views(self):
        """Load all persistent views (confessions + replies) on startup."""
        await self.bot.wait_until_ready()
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                # ── Confession views ───────────────────────────────────────
                cursor = await db.execute("SELECT MAX(id) FROM confessions")
                result = await cursor.fetchone()
                latest_confession_id = result[0] if result[0] else None

                async with db.execute(
                    "SELECT id, message_id FROM confessions WHERE message_id IS NOT NULL"
                ) as cursor:
                    async for confession_id, message_id in cursor:
                        try:
                            show_confess_button = confession_id == latest_confession_id
                            view = PersistentConfessionView(
                                confession_id, show_confess_button=show_confess_button
                            )
                            self.bot.add_view(view)
                        except Exception as e:
                            print(f"Error loading view for confession {confession_id}: {e}")

                # ── Reply views ────────────────────────────────────────────
                async with db.execute(
                    "SELECT id FROM replies WHERE message_id IS NOT NULL"
                ) as cursor:
                    async for (reply_id,) in cursor:
                        try:
                            view = ReplyMessageView(reply_id)
                            self.bot.add_view(view)
                        except Exception as e:
                            print(f"Error loading view for reply {reply_id}: {e}")

            print("[Confessions] ✅ Persistent views loaded.")
        except Exception as e:
            print(f"[Confessions] ❌ Error loading views: {e}")

    @app_commands.command(name="confess", description="Submit an anonymous confession")
    async def confess_command(self, interaction: discord.Interaction):
        if not self.initialized:
            return await interaction.response.send_message(
                "❌ Confession system is initializing, please try again in a moment.",
                ephemeral=True,
            )
        if await is_user_banned(interaction.user.id):
            return await interaction.response.send_message(
                "❌ You are banned from making confessions.", ephemeral=True
            )
        if interaction.channel.id != CONFESSION_CHANNEL_ID:
            return await interaction.response.send_message(
                f"❌ Please use this command in <#{CONFESSION_CHANNEL_ID}>.",
                ephemeral=True,
            )
        if not any(role.id in ALLOWED_ROLE_IDS for role in interaction.user.roles):
            return await interaction.response.send_message(
                "❌ You do not have at least LVL 5 required to make confessions.",
                ephemeral=True,
            )
        can_confess, reason = await can_user_confess(
            interaction.guild, interaction.user.id, DAILY_CONFESSION_LIMIT
        )
        if not can_confess:
            return await interaction.response.send_message(reason, ephemeral=True)

        modal = ConfessionModal(self.bot)
        await interaction.response.send_modal(modal)

    @app_commands.command(
        name="confess_delete",
        description="Delete a confession by ID and renumber all subsequent confessions",
    )
    @app_commands.describe(
        confession_id="The ID of the confession to delete",
        reason="Reason for deletion (optional)",
    )
    @app_commands.default_permissions(manage_messages=True)
    async def confess_delete(
        self, interaction: discord.Interaction, confession_id: int, reason: str = None
    ):
        if not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message(
                "❌ You need `Manage Server` permission to use this command.",
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True)

        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute(
                    "SELECT message_id, user_id, title, description FROM confessions WHERE id = ?",
                    (confession_id,),
                )
                confession = await cursor.fetchone()

                if not confession:
                    return await interaction.followup.send(
                        f"❌ Confession #{confession_id} not found in database.",
                        ephemeral=True,
                    )

                message_id, author_id, title, description = confession

                try:
                    author = await self.bot.fetch_user(author_id)
                except Exception:
                    author = None

                cursor = await db.execute("SELECT MAX(id) FROM confessions")
                max_id = (await cursor.fetchone())[0]

                cursor = await db.execute(
                    "SELECT id, message_id FROM confessions WHERE id > ? ORDER BY id",
                    (confession_id,),
                )
                confessions_to_update = await cursor.fetchall()

                cursor = await db.execute(
                    "SELECT COUNT(*) FROM replies WHERE confession_id = ?",
                    (confession_id,),
                )
                reply_count = (await cursor.fetchone())[0]

                await db.execute("BEGIN TRANSACTION")

                try:
                    channel = self.bot.get_channel(CONFESSION_CHANNEL_ID)
                    message_deleted = False
                    thread_deleted = False

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
                            print(f"[Confession Delete] Message for confession #{confession_id} not found")
                        except discord.Forbidden:
                            print(f"[Confession Delete] No permission to delete message for confession #{confession_id}")
                        except Exception as e:
                            print(f"[Confession Delete] Error deleting message: {e}")

                    await db.execute(
                        "DELETE FROM replies WHERE confession_id = ?", (confession_id,)
                    )
                    await db.execute(
                        "DELETE FROM reports WHERE target_type = 'confession' AND target_id = ?",
                        (confession_id,),
                    )
                    await db.execute(
                        "DELETE FROM confessions WHERE id = ?", (confession_id,)
                    )

                    update_count = 0
                    for old_id, old_message_id in confessions_to_update:
                        new_id = old_id - 1
                        await db.execute(
                            "UPDATE confessions SET id = ? WHERE id = ?", (new_id, old_id)
                        )
                        await db.execute(
                            "UPDATE replies SET confession_id = ? WHERE confession_id = ?",
                            (new_id, old_id),
                        )
                        await db.execute(
                            "UPDATE reports SET target_id = ? WHERE target_type = 'confession' AND target_id = ?",
                            (new_id, old_id),
                        )

                        if channel and old_message_id:
                            try:
                                msg = await channel.fetch_message(old_message_id)
                                if msg and msg.embeds:
                                    embed = msg.embeds[0]
                                    if embed.title and f"#{old_id}" in embed.title:
                                        embed.title = embed.title.replace(f"#{old_id}", f"#{new_id}")
                                    if (
                                        embed.footer
                                        and embed.footer.text
                                        and f"#{old_id}" in embed.footer.text
                                    ):
                                        embed.set_footer(
                                            text=embed.footer.text.replace(f"#{old_id}", f"#{new_id}")
                                        )
                                    new_max_id = max_id - 1
                                    is_latest = new_id == new_max_id
                                    view = PersistentConfessionView(new_id, show_confess_button=is_latest)
                                    await msg.edit(embed=embed, view=view)
                            except discord.NotFound:
                                pass
                            except Exception as e:
                                print(f"[Confession Delete] Error updating message for #{old_id}: {e}")
                        update_count += 1

                    # If we deleted the latest, make the previous one show the confess button
                    if confession_id == max_id:
                        cursor = await db.execute(
                            "SELECT id, message_id FROM confessions WHERE id = ?",
                            (confession_id - 1,),
                        )
                        prev_confession = await cursor.fetchone()
                        if prev_confession:
                            prev_id, prev_message_id = prev_confession
                            if channel and prev_message_id:
                                try:
                                    msg = await channel.fetch_message(prev_message_id)
                                    if msg and msg.embeds:
                                        embed = msg.embeds[0]
                                        view = PersistentConfessionView(
                                            prev_id, show_confess_button=True
                                        )
                                        await msg.edit(view=view)
                                except discord.NotFound:
                                    pass
                                except Exception as e:
                                    print(f"[Confession Delete] Error updating previous confession: {e}")

                    await db.execute(
                        "UPDATE sqlite_sequence SET seq = ? WHERE name = 'confessions'",
                        (max_id - 1,),
                    )
                    await db.commit()

                    # DM the author
                    dm_sent = False
                    if author:
                        try:
                            dm_embed = discord.Embed(
                                title="🗑️ Your Confession Was Deleted",
                                description=f"Your confession (ID: #{confession_id}) has been removed by a moderator.",
                                color=discord.Color.red(),
                                timestamp=discord.utils.utcnow(),
                            )
                            if title:
                                dm_embed.add_field(name="Title", value=title, inline=False)
                            if description:
                                short_desc = description[:200] + "..." if len(description) > 200 else description
                                dm_embed.add_field(name="Content", value=short_desc, inline=False)
                            dm_embed.add_field(
                                name="Reason",
                                value=reason or "No specific reason provided.",
                                inline=False,
                            )
                            dm_embed.set_footer(text=f"Confession #{confession_id}")
                            await author.send(embed=dm_embed)
                            dm_sent = True
                        except discord.Forbidden:
                            pass
                        except Exception as e:
                            print(f"[Confession Delete] Error DMing user: {e}")

                    embed = discord.Embed(
                        title="✅ Confession Deleted & Renumbered",
                        color=discord.Color.red(),
                        timestamp=discord.utils.utcnow(),
                    )
                    embed.add_field(name="Deleted ID", value=str(confession_id), inline=True)
                    embed.add_field(name="Replies Deleted", value=str(reply_count), inline=True)
                    embed.add_field(
                        name="Renumbered",
                        value=f"✅ {update_count} confession{'s' if update_count != 1 else ''} renumbered",
                        inline=True,
                    )

                    status = [
                        "✅ Database entry removed and renumbered",
                        "✅ Message deleted" if message_deleted else "❌ Message not found or couldn't be deleted",
                    ]
                    if thread_deleted:
                        status.append("✅ Thread deleted")
                    status.append(
                        "✅ Author notified via DM"
                        if dm_sent
                        else ("❌ Could not DM author (DMs disabled)" if author else "❌ Could not fetch author")
                    )
                    embed.add_field(name="Status", value="\n".join(status), inline=False)

                    if title:
                        embed.add_field(name="Title", value=title, inline=False)
                    if description:
                        short_desc = description[:200] + "..." if len(description) > 200 else description
                        embed.add_field(name="Content", value=short_desc, inline=False)
                    if reason:
                        embed.add_field(name="Reason", value=reason, inline=False)

                    embed.set_footer(text=f"Deleted by {interaction.user.display_name}")
                    await interaction.followup.send(embed=embed, ephemeral=True)

                    await self.log_confession_deletion(
                        interaction, confession_id, author_id,
                        reply_count, update_count, dm_sent, reason,
                    )

                except Exception as e:
                    await db.execute("ROLLBACK")
                    raise e

        except Exception as e:
            print(f"[Confession Delete] Error: {e}")
            import traceback
            traceback.print_exc()
            await interaction.followup.send(
                f"❌ An error occurred while deleting confession #{confession_id}: {str(e)}",
                ephemeral=True,
            )

    async def log_confession_deletion(
        self,
        interaction,
        confession_id,
        author_id,
        reply_count,
        renumber_count,
        dm_sent,
        reason,
    ):
        """Log confession deletion to admin log and mod log."""
        # ── Admin log ──────────────────────────────────────────────────────
        log_channel = self.bot.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            try:
                embed = discord.Embed(
                    title="🗑️ Confession Deleted & Renumbered",
                    color=discord.Color.orange(),
                    timestamp=discord.utils.utcnow(),
                )
                embed.add_field(name="Deleted ID", value=str(confession_id), inline=True)
                embed.add_field(name="Author", value=f"<@{author_id}>", inline=True)
                embed.add_field(name="Replies Deleted", value=str(reply_count), inline=True)
                embed.add_field(name="Renumbered", value=str(renumber_count), inline=True)
                embed.add_field(
                    name="Author Notified", value="✅ Yes" if dm_sent else "❌ No", inline=True
                )
                embed.add_field(
                    name="Deleted by",
                    value=f"{interaction.user.mention} ({interaction.user.id})",
                    inline=False,
                )
                if reason:
                    embed.add_field(name="Reason", value=reason, inline=False)
                await log_channel.send(embed=embed)
            except Exception as e:
                print(f"Error logging confession deletion (admin): {e}")

        # ── Mod log (anonymized) ───────────────────────────────────────────
        mod_log_channel = self.bot.get_channel(MOD_LOG_CHANNEL_ID) if MOD_LOG_CHANNEL_ID else None
        if mod_log_channel:
            try:
                author_hash = sha256_user_id(author_id)
                mod_embed = discord.Embed(
                    title="🗑️ Confession Deleted & Renumbered",
                    color=discord.Color.orange(),
                    timestamp=discord.utils.utcnow(),
                )
                mod_embed.add_field(name="Deleted ID", value=str(confession_id), inline=True)
                mod_embed.add_field(
                    name="Author (hash)", value=f"`{author_hash}`", inline=True
                )
                mod_embed.add_field(name="Replies Deleted", value=str(reply_count), inline=True)
                mod_embed.add_field(name="Renumbered", value=str(renumber_count), inline=True)
                mod_embed.add_field(
                    name="Author Notified", value="✅ Yes" if dm_sent else "❌ No", inline=True
                )
                # Anonymize the deleter too for the mod log
                deleter_hash = sha256_user_id(interaction.user.id)
                mod_embed.add_field(
                    name="Deleted by (hash)", value=f"`{deleter_hash}`", inline=False
                )
                if reason:
                    mod_embed.add_field(name="Reason", value=reason, inline=False)
                await mod_log_channel.send(embed=mod_embed)
            except Exception as e:
                print(f"Error logging confession deletion (mod): {e}")


async def setup(bot):
    await bot.add_cog(Confessions(bot))