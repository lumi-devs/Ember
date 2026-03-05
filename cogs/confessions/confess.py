import discord
from discord import ui, app_commands
from discord.ext import commands
from datetime import datetime
import asyncio
import aiosqlite
import aiohttp
import io

from cogs.confessions.helpers import (
    DB_PATH,
    extract_url,
    init_db,
    is_user_banned,
    get_confession_by_id,
)
from cogs.confessions.reply import ReplyModal

# ==============================
# CONFIGURATION
# ==============================
CONFESSION_CHANNEL_ID = 1399658407178272798
REPORT_CHANNEL_ID = 1393171149038293023
LOG_CHANNEL_ID = 1453409054314467339
MEDIA_CHANNEL_ID = 1424139009931743382
ALLOWED_ROLE_IDS = [
    1393171037562077196,
    1393171036635402354,
    1393171035695612055,
    1393171034701561908,
    1393171033774624848,
    1393171032726306966,
    1420878388297465866,
]


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

        # Defer immediately to avoid timeout
        await interaction.response.defer(ephemeral=True, thinking=True)

        # Check blacklist with improved error handling
        if await is_user_banned(self.user_id):
            return await interaction.followup.send(
                "❌ You are banned from submitting confessions.", ephemeral=True
            )

        channel = self.bot.get_channel(CONFESSION_CHANNEL_ID)
        if not channel:
            return await interaction.followup.send(
                "❌ Confession channel not found.", ephemeral=True
            )

        try:
            # Get previous confession ID to update its button
            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute("SELECT MAX(id) FROM confessions")
                result = await cursor.fetchone()
                prev_confession_id = result[0] if result[0] else None

            # Insert confession entry with transaction
            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute(
                    "INSERT INTO confessions (user_id, title, description) VALUES (?, ?, ?)",
                    (
                        self.user_id,
                        self.title_input.value,
                        self.description_input.value,
                    ),
                )
                await db.commit()
                confession_id = cursor.lastrowid

            # Create initial embed
            embed = discord.Embed(
                title=f"💌 {self.title_input.value or f'Confession #{confession_id}'}",
                description=self.description_input.value,
                color=discord.Color.purple(),
                timestamp=datetime.utcnow(),
            )
            embed.set_footer(text=f"Confession #{confession_id}")

            # Send message WITHOUT view initially
            msg = await channel.send(embed=embed)

            # Create thread for this confession with simple name
            thread_name = f"Confession #{confession_id}"

            # Create public thread with 60 minute auto-archive duration
            try:
                thread = await msg.create_thread(
                    name=thread_name, auto_archive_duration=60  # 60 minutes
                )
            except discord.HTTPException as e:
                print(f"Error creating thread: {e}")
                # Continue without thread if creation fails
                thread = None

            # Save only message_id
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "UPDATE confessions SET message_id=? WHERE id=?",
                    (msg.id, confession_id),
                )
                await db.commit()

            # Send media prompt FIRST
            await interaction.followup.send(
                "✅ Confession submitted! You have **1 minute** to reply with an image URL or attachment. "
                "Send `skip` to continue without media.\n\n"
                f"📝 Your confession ID: #{confession_id}",
                ephemeral=True,
            )

            # Wait for optional media with timeout
            image_url = await self.wait_for_media(interaction, confession_id, msg)

            # Update embed with image if available
            if image_url:
                embed.set_image(url=image_url)
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute(
                        "UPDATE confessions SET image_url=? WHERE id=?",
                        (image_url, confession_id),
                    )
                    await db.commit()

            # Update previous confession's button if it exists
            if prev_confession_id:
                await self.remove_prev_confess_button(prev_confession_id)

            # Create and attach persistent view WITH confess button
            final_view = PersistentConfessionView(
                confession_id, show_confess_button=True
            )
            await msg.edit(embed=embed, view=final_view)

            # Log confession
            await self.log_confession(confession_id, image_url)

            # Send final confirmation
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
        """Remove confess button from previous confession"""
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
                    # Create new view without confess button
                    new_view = PersistentConfessionView(
                        confession_id, show_confess_button=False
                    )
                    await msg.edit(view=new_view)
                except discord.NotFound:
                    print(f"Previous confession message {message_id} not found")
                except discord.HTTPException as e:
                    print(f"Error updating previous confession: {e}")
        except Exception as e:
            print(f"Error removing previous confess button: {e}")

    async def wait_for_media(self, interaction, confession_id, confession_msg):
        """Wait for media response with proper timeout handling"""

        def check(m):
            return (
                m.author.id == self.user_id and m.channel.id == interaction.channel.id
            )

        try:
            # First, let user know we're waiting
            await interaction.followup.send(
                "⏳ Waiting for media... You have 60 seconds.", ephemeral=True
            )

            msg_received = await self.bot.wait_for("message", timeout=60.0, check=check)

            # Check if user wants to skip
            content_lower = msg_received.content.lower().strip()
            if content_lower == "skip":
                # User typed 'skip' - delete the skip message
                try:
                    await msg_received.delete()
                except:
                    pass
                await interaction.followup.send(
                    "✅ Skipped media upload.", ephemeral=True
                )
                # Update previous confession's button if it exists
                if hasattr(self, "prev_confession_id") and self.prev_confession_id:
                    await self.remove_prev_confess_button(self.prev_confession_id)

                # Create and attach persistent view WITH confess button
                final_view = PersistentConfessionView(
                    confession_id, show_confess_button=True
                )
                await confession_msg.edit(view=final_view)

                # Send final confirmation
                thread = confession_msg.thread
                if thread:
                    thread_msg = (
                        f"A discussion thread has been created: {thread.mention}"
                    )
                else:
                    thread_msg = "Note: Could not create discussion thread."

                await interaction.followup.send(
                    f"✅ Confession #{confession_id} is now public! {thread_msg}",
                    ephemeral=True,
                )
                return None

            # Check if message has media
            has_attachments = len(msg_received.attachments) > 0

            # Check for valid image URL (not just any URL)
            has_valid_url = False
            if msg_received.content and msg_received.content.strip():
                # Extract URL first
                url = extract_url(msg_received.content)
                if url:
                    # Check if it looks like an image URL
                    url_lower = url.lower()
                    image_extensions = [
                        ".png",
                        ".jpg",
                        ".jpeg",
                        ".gif",
                        ".webp",
                        ".bmp",
                        ".tiff",
                    ]
                    if any(url_lower.endswith(ext) for ext in image_extensions):
                        has_valid_url = True
                    elif (
                        "media.discordapp.net" in url_lower
                        or "cdn.discordapp.com" in url_lower
                    ):
                        has_valid_url = True

            if not has_attachments and not has_valid_url:
                # Invalid input - delete message and send error
                try:
                    await msg_received.delete()
                except:
                    pass

                # Send detailed ephemeral message
                error_msg = "❌ No valid media found in your message.\n\n"

                if msg_received.content and extract_url(msg_received.content):
                    error_msg += (
                        "⚠️ The URL you provided doesn't appear to be an image link.\n"
                    )
                    error_msg += "Please use direct image links (ending with .png/.jpg/.gif/.webp)\n\n"
                else:
                    error_msg += "**Valid options are:**\n"
                    error_msg += "• Upload an image file (drag & drop)\n"
                    error_msg += "• Send a direct image URL\n"
                    error_msg += "• Type `skip` to continue without media\n\n"

                error_msg += "Continuing without media."

                await interaction.followup.send(error_msg, ephemeral=True)

                # Update previous confession's button if it exists
                if hasattr(self, "prev_confession_id") and self.prev_confession_id:
                    await self.remove_prev_confess_button(self.prev_confession_id)

                # Create and attach persistent view WITH confess button
                final_view = PersistentConfessionView(
                    confession_id, show_confess_button=True
                )
                await confession_msg.edit(view=final_view)

                # Send final confirmation
                thread = confession_msg.thread
                if thread:
                    thread_msg = (
                        f"A discussion thread has been created: {thread.mention}"
                    )
                else:
                    thread_msg = "Note: Could not create discussion thread."

                await interaction.followup.send(
                    f"✅ Confession #{confession_id} is now public! {thread_msg}",
                    ephemeral=True,
                )
                return None

            # Process valid media
            success, result = await self.download_and_reupload_media(
                msg_received.content, msg_received.attachments, confession_id
            )

            # Delete the message AFTER successful media processing
            try:
                await msg_received.delete()
            except discord.Forbidden:
                print(f"No permission to delete message from {msg_received.author}")
            except discord.NotFound:
                print("Message already deleted")
            except Exception as e:
                print(f"Error deleting message: {e}")

            if success:
                # Update the embed with image
                embed = confession_msg.embeds[0] if confession_msg.embeds else None
                if embed:
                    embed.set_image(url=result)
                    async with aiosqlite.connect(DB_PATH) as db:
                        await db.execute(
                            "UPDATE confessions SET image_url=? WHERE id=?",
                            (result, confession_id),
                        )
                        await db.commit()

                await interaction.followup.send(
                    "✅ Media added to confession!", ephemeral=True
                )

                # Update previous confession's button if it exists
                if hasattr(self, "prev_confession_id") and self.prev_confession_id:
                    await self.remove_prev_confess_button(self.prev_confession_id)

                # Create and attach persistent view WITH confess button
                final_view = PersistentConfessionView(
                    confession_id, show_confess_button=True
                )
                await confession_msg.edit(embed=embed, view=final_view)

                # Send final confirmation
                thread = confession_msg.thread
                if thread:
                    thread_msg = (
                        f"A discussion thread has been created: {thread.mention}"
                    )
                else:
                    thread_msg = "Note: Could not create discussion thread."

                await interaction.followup.send(
                    f"✅ Confession #{confession_id} is now public! {thread_msg}",
                    ephemeral=True,
                )
                return result  # Returns the media URL
            else:
                # Media processing failed
                error_msg = (
                    result if isinstance(result, str) else "Unknown error occurred"
                )
                await interaction.followup.send(
                    f"❌ Failed to process media: {error_msg}\nContinuing without media.",
                    ephemeral=True,
                )

                # Add fallback buttons to confession
                await self.add_fallback_buttons(
                    confession_msg, confession_id, error_msg
                )

                # Update previous confession's button if it exists
                if hasattr(self, "prev_confession_id") and self.prev_confession_id:
                    await self.remove_prev_confess_button(self.prev_confession_id)

                # Send final confirmation
                thread = confession_msg.thread
                if thread:
                    thread_msg = (
                        f"A discussion thread has been created: {thread.mention}"
                    )
                else:
                    thread_msg = "Note: Could not create discussion thread."

                await interaction.followup.send(
                    f"✅ Confession #{confession_id} is now public! {thread_msg}",
                    ephemeral=True,
                )
                return None

        except asyncio.TimeoutError:
            await interaction.followup.send(
                "⏰ Media upload timed out. Continuing without media.", ephemeral=True
            )

            # Update previous confession's button if it exists
            if hasattr(self, "prev_confession_id") and self.prev_confession_id:
                await self.remove_prev_confess_button(self.prev_confession_id)

            # Create and attach persistent view WITH confess button
            final_view = PersistentConfessionView(
                confession_id, show_confess_button=True
            )
            await confession_msg.edit(view=final_view)

            # Send final confirmation
            thread = confession_msg.thread
            if thread:
                thread_msg = f"A discussion thread has been created: {thread.mention}"
            else:
                thread_msg = "Note: Could not create discussion thread."

            await interaction.followup.send(
                f"✅ Confession #{confession_id} is now public! {thread_msg}",
                ephemeral=True,
            )
            return None
        except Exception as e:
            print(f"Error in wait_for_media: {e}")
            import traceback

            traceback.print_exc()
            await interaction.followup.send(
                "❌ An error occurred while processing media.", ephemeral=True
            )

            # Update previous confession's button if it exists
            if hasattr(self, "prev_confession_id") and self.prev_confession_id:
                await self.remove_prev_confess_button(self.prev_confession_id)

            # Create and attach persistent view WITH confess button
            final_view = PersistentConfessionView(
                confession_id, show_confess_button=True
            )
            await confession_msg.edit(view=final_view)

            # Send final confirmation
            thread = confession_msg.thread
            if thread:
                thread_msg = f"A discussion thread has been created: {thread.mention}"
            else:
                thread_msg = "Note: Could not create discussion thread."

            await interaction.followup.send(
                f"✅ Confession #{confession_id} is now public! {thread_msg}",
                ephemeral=True,
            )
            return None

    async def download_and_reupload_media(self, content, attachments, confession_id):
        """Download media and re-upload to media channel, then return new URL"""
        try:
            print(f"[DEBUG] Starting media processing for confession #{confession_id}")

            # Step 1: Get the media URL
            media_url = None
            filename = None

            # Check attachments first (user uploaded file)
            if attachments and len(attachments) > 0:
                attachment = attachments[0]
                media_url = attachment.url
                filename = attachment.filename
                print(f"[DEBUG] Found attachment: {filename}, URL: {media_url}")
            # Check content for URL
            elif content and content.strip():
                media_url = extract_url(content)
                if media_url:
                    # Try to extract filename from URL
                    filename = media_url.split("/")[-1].split("?")[0]
                    # Add extension if missing
                    if "." not in filename:
                        # Check if it's a Tenor/Giphy URL
                        if "tenor.com" in media_url or "giphy.com" in media_url:
                            filename = f"gif_{confession_id}.gif"
                        else:
                            filename = f"image_{confession_id}.jpg"
                    print(f"[DEBUG] Found URL: {media_url}, filename: {filename}")

            if not media_url:
                print("[DEBUG] No media URL found")
                return False, "No media found in message"

            # Step 2: Download the media
            print(f"[DEBUG] Downloading media from: {media_url}")
            async with aiohttp.ClientSession() as session:
                try:
                    headers = {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                    }
                    async with session.get(
                        media_url, headers=headers, timeout=30
                    ) as response:
                        if response.status != 200:
                            print(
                                f"[DEBUG] Download failed with status: {response.status}"
                            )
                            return (
                                False,
                                f"Failed to download media: HTTP {response.status}",
                            )

                        # Read the image data
                        file_data = await response.read()
                        print(f"[DEBUG] Downloaded {len(file_data)} bytes")

                        # If no filename, try to guess from content-type
                        if not filename:
                            content_type = response.headers.get("Content-Type", "")
                            print(f"[DEBUG] Content-Type: {content_type}")
                            if (
                                "image/jpeg" in content_type
                                or "image/jpg" in content_type
                            ):
                                filename = f"image_{confession_id}.jpg"
                            elif "image/png" in content_type:
                                filename = f"image_{confession_id}.png"
                            elif "image/gif" in content_type:
                                filename = f"image_{confession_id}.gif"
                            elif "image/webp" in content_type:
                                filename = f"image_{confession_id}.webp"
                            else:
                                # Default to jpg
                                filename = f"image_{confession_id}.jpg"

                except aiohttp.ClientError as e:
                    print(f"[DEBUG] Download error: {e}")
                    return False, f"Failed to download media: {str(e)}"
                except asyncio.TimeoutError:
                    print("[DEBUG] Download timeout")
                    return False, "Download timeout"
                except Exception as e:
                    print(f"[DEBUG] Error downloading: {e}")
                    return False, f"Error downloading: {str(e)}"

            if not file_data or len(file_data) == 0:
                print("[DEBUG] No file data received")
                return False, "No media data received"

            # Step 3: Upload to media channel
            media_channel = self.bot.get_channel(MEDIA_CHANNEL_ID)
            if not media_channel:
                print("[DEBUG] Media channel not found")
                return False, "Media channel not found"

            print(f"[DEBUG] Uploading to media channel: {MEDIA_CHANNEL_ID}")
            # Create file object
            file = discord.File(io.BytesIO(file_data), filename=filename)

            # Upload to media channel
            try:
                media_msg = await media_channel.send(
                    f"**Media for confession #{confession_id}**", file=file
                )
                print(f"[DEBUG] Media uploaded, message ID: {media_msg.id}")

                # Wait a moment for Discord to process
                await asyncio.sleep(2)

                # Get the URL from the uploaded message
                final_url = None

                # Check attachments first (most reliable)
                if media_msg.attachments and len(media_msg.attachments) > 0:
                    final_url = media_msg.attachments[0].url
                    print(f"[DEBUG] Got URL from attachment: {final_url}")
                # Check embeds as fallback
                elif media_msg.embeds and len(media_msg.embeds) > 0:
                    for embed in media_msg.embeds:
                        if embed.image:
                            final_url = embed.image.url
                            print(f"[DEBUG] Got URL from embed image: {final_url}")
                            break
                        elif embed.thumbnail:
                            final_url = embed.thumbnail.url
                            print(f"[DEBUG] Got URL from embed thumbnail: {final_url}")
                            break

                if not final_url:
                    print("[DEBUG] Could not get URL from uploaded media")
                    return False, "Could not get URL from uploaded media"

                print(f"[DEBUG] Success! Final URL: {final_url}")
                return True, final_url

            except discord.HTTPException as e:
                print(f"[DEBUG] Upload failed: {e}")
                return False, f"Failed to upload to media channel: {str(e)}"
            except Exception as e:
                print(f"[DEBUG] Error during upload: {e}")
                return False, f"Error during upload: {str(e)}"

        except Exception as e:
            print(f"[DEBUG] Error in download_and_reupload_media: {e}")
            import traceback

            traceback.print_exc()
            return False, f"Error: {str(e)}"

    async def add_fallback_buttons(self, confession_msg, confession_id, error_msg):
        """Add fallback buttons to confession when media processing fails or no media provided"""
        try:
            # Check if the message still exists
            try:
                await confession_msg.channel.fetch_message(confession_msg.id)
            except discord.NotFound:
                print(f"Confession message {confession_msg.id} not found")
                return

            # Create view with fallback buttons
            fallback_view = FallbackMediaView(confession_id, error_msg)

            # Try to get current embed to preserve it
            embed = confession_msg.embeds[0] if confession_msg.embeds else None

            # Edit the message with the new view
            await confession_msg.edit(embed=embed, view=fallback_view)

            # Also add the persistent confession view components
            final_view = PersistentConfessionView(
                confession_id, show_confess_button=False
            )
            # We need to add the fallback buttons to the persistent view
            for item in fallback_view.children:
                final_view.add_item(item)

            await confession_msg.edit(view=final_view)

        except discord.HTTPException as e:
            print(f"HTTP error adding fallback buttons: {e}")
            # Try a simpler approach
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
        """Log confession to log channel"""
        log_channel = self.bot.get_channel(LOG_CHANNEL_ID)
        if not log_channel:
            return

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
                user = await self.bot.fetch_user(user_id)

                log_embed = discord.Embed(
                    title=f"Confession #{confession_id}",
                    description=description,
                    color=discord.Color.purple(),
                    timestamp=datetime.utcnow(),
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
            print(f"Error logging confession: {e}")


# ==============================
# FALLBACK MEDIA VIEW (When media processing fails)
# ==============================
class FallbackMediaView(ui.View):
    def __init__(self, confession_id, error_msg):
        super().__init__(timeout=None)
        self.confession_id = confession_id
        self.error_msg = error_msg

        # Add retry button
        self.add_item(RetryMediaButton(confession_id, error_msg))
        # Add manual upload button
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

        # Add persistent buttons with custom IDs
        if show_confess_button:
            self.add_item(PersistentConfessButton())
        self.add_item(PersistentReplyButton(confession_id))
        self.add_item(PersistentReportButton(confession_id))

        # Add confess button only if specified (for latest confession)


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

        # Check if confession still exists
        confession = await get_confession_by_id(self.confession_id)
        if not confession:
            return await interaction.response.send_message(
                "❌ Confession not found.", ephemeral=True
            )

        message_id, author_id = confession[5], confession[1]  # message_id, user_id
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
                timestamp=datetime.utcnow(),
            )
            embed.add_field(
                name="Confession ID", value=str(self.confession_id), inline=False
            )
            embed.add_field(name="Author ID", value=str(author_id), inline=False)
            embed.add_field(
                name="Reported by",
                value=f"{interaction.user} ({interaction.user.id})",
                inline=False,
            )
            embed.add_field(
                name="Banned?", value="Yes" if banned else "No", inline=False
            )
            embed.add_field(name="Title", value=title or "No title", inline=False)
            embed.add_field(
                name="Content", value=description or "No content", inline=False
            )
            if image_url:
                embed.set_image(url=image_url)

            mod_channel = bot.get_channel(REPORT_CHANNEL_ID)
            if mod_channel:
                await mod_channel.send(
                    "<@&1393170995157798983> <@&1406325951234794615>", embed=embed
                )

            await interaction.response.send_message(
                "✅ Report submitted.", ephemeral=True
            )

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
        # Check if user has required role
        if not any(role.id in ALLOWED_ROLE_IDS for role in interaction.user.roles):
            return await interaction.response.send_message(
                "❌ You do not have at least LVL 5 required to make confessions.",
                ephemeral=True,
            )

        # Check for ban
        if await is_user_banned(interaction.user.id):
            return await interaction.response.send_message(
                "❌ You are banned from making confessions.", ephemeral=True
            )

        # Open confession modal
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
        """Initialize database and load persistent views"""
        try:
            await init_db()
            await self.load_persistent_views()
            self.initialized = True
            print("[Confessions] ✅ System initialized successfully.")
        except Exception as e:
            print(f"[Confessions] ❌ Initialization failed: {e}")

    async def load_persistent_views(self):
        """Load all persistent confession views on startup"""
        await self.bot.wait_until_ready()
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                # Get the latest confession ID
                cursor = await db.execute("SELECT MAX(id) FROM confessions")
                result = await cursor.fetchone()
                latest_confession_id = result[0] if result[0] else None

                # Load all confessions with views
                async with db.execute(
                    "SELECT id, message_id FROM confessions WHERE message_id IS NOT NULL"
                ) as cursor:
                    async for confession_id, message_id in cursor:
                        try:
                            # Only show confess button on the latest confession
                            show_confess_button = confession_id == latest_confession_id
                            view = PersistentConfessionView(
                                confession_id, show_confess_button=show_confess_button
                            )
                            self.bot.add_view(view)
                        except Exception as e:
                            print(
                                f"Error loading view for confession {confession_id}: {e}"
                            )
            print(f"[Confessions] ✅ Persistent views loaded.")
        except Exception as e:
            print(f"[Confessions] ❌ Error loading views: {e}")

    @app_commands.command(name="confess", description="Submit an anonymous confession")
    async def confess_command(self, interaction: discord.Interaction):
        # Check initialization
        if not self.initialized:
            return await interaction.response.send_message(
                "❌ Confession system is initializing, please try again in a moment.",
                ephemeral=True,
            )

        # Check for ban
        if await is_user_banned(interaction.user.id):
            return await interaction.response.send_message(
                "❌ You are banned from making confessions.", ephemeral=True
            )

        # Check correct channel
        if interaction.channel.id != CONFESSION_CHANNEL_ID:
            return await interaction.response.send_message(
                f"❌ Please use this command in <#{CONFESSION_CHANNEL_ID}>.",
                ephemeral=True,
            )

        # Check allowed roles
        if not any(role.id in ALLOWED_ROLE_IDS for role in interaction.user.roles):
            return await interaction.response.send_message(
                "❌ You do not have at least LVL 5 required to make confessions.",
                ephemeral=True,
            )

        modal = ConfessionModal(self.bot)
        await interaction.response.send_modal(modal)

    @app_commands.command(
        name="confess_stats", description="Show confession statistics"
    )
    @app_commands.default_permissions(manage_messages=True)
    async def confess_stats(self, interaction: discord.Interaction):
        """Show statistics about confessions"""
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                # Total confessions
                cursor = await db.execute("SELECT COUNT(*) FROM confessions")
                total_confessions = (await cursor.fetchone())[0]

                # Today's confessions
                cursor = await db.execute(
                    "SELECT COUNT(*) FROM confessions WHERE DATE(created_at) = DATE('now')"
                )
                today_confessions = (await cursor.fetchone())[0]

                # Total bans
                cursor = await db.execute("SELECT COUNT(*) FROM confessban")
                total_bans = (await cursor.fetchone())[0]

                # Total reports
                cursor = await db.execute("SELECT COUNT(*) FROM reports")
                total_reports = (await cursor.fetchone())[0]

            embed = discord.Embed(
                title="📊 Confession Statistics",
                color=discord.Color.blue(),
                timestamp=datetime.utcnow(),
            )
            embed.add_field(
                name="Total Confessions", value=total_confessions, inline=True
            )
            embed.add_field(
                name="Today's Confessions", value=today_confessions, inline=True
            )
            embed.add_field(name="Total Bans", value=total_bans, inline=True)
            embed.add_field(name="Total Reports", value=total_reports, inline=True)

            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception as e:
            await interaction.response.send_message(
                f"❌ Error fetching statistics: {e}", ephemeral=True
            )


async def setup(bot):
    await bot.add_cog(Confessions(bot))
