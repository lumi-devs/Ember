import discord
from discord import ui
import asyncio
from datetime import datetime
import aiosqlite
import aiohttp
import io
from cogs.confessions.helpers import DB_PATH, extract_url

CONFESSION_CHANNEL_ID = 1399658407178272798
LOG_CHANNEL_ID = 1453409054314467339
MEDIA_CHANNEL_ID = 1424139009931743382


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
        self.reply_text = None  # Store the reply text

    async def on_submit(self, interaction: discord.Interaction):
        self.user_id = interaction.user.id
        self.reply_text = self.reply_input.value  # Store the reply text

        # Defer immediately to avoid timeout
        await interaction.response.defer(ephemeral=True, thinking=True)

        # Send media prompt FIRST
        await interaction.followup.send(
            "✅ Reply submitted! You have **60 seconds** to reply with an image URL or attachment.\n"
            "Send `skip` to continue without media.\n\n"
            "**Accepted formats:**\n"
            "• Discord image upload (drag & drop)\n"
            "• Direct image URL (.png/.jpg/.gif/.webp)",
            ephemeral=True,
        )

        # Allow user to send one message in confession channel
        cleaner_cog = self.bot.get_cog("ConfessionCleaner")
        if cleaner_cog:
            await cleaner_cog.allow_user_once(self.user_id)

        # Wait for optional media with timeout
        image_url = await self.wait_for_media(interaction)

        # Finalize reply with or without media
        await self.finalize_reply_directly(image_url, interaction)

    async def wait_for_media(self, interaction):
        """Wait for media response with proper timeout handling"""

        def check(m):
            return (
                m.author.id == self.user_id and m.channel.id == interaction.channel.id
            )

        try:
            msg_received = await self.bot.wait_for("message", timeout=60.0, check=check)

            # Check if user wants to skip
            content_lower = msg_received.content.lower().strip()
            if content_lower == "skip":
                # User typed 'skip' - delete the skip message
                try:
                    await msg_received.delete()
                except Exception:
                    pass
                await interaction.followup.send(
                    "✅ Skipped media upload.", ephemeral=True
                )
                # Return None for no image
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
                except Exception:
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
                # Return None for no image
                return None

            # Process valid media
            success, result = await self.download_and_reupload_media(
                msg_received.content, msg_received.attachments
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
                await interaction.followup.send(
                    "✅ Media added to reply!", ephemeral=True
                )
                # Return the image URL
                return result
            else:
                # Media processing failed
                error_msg = (
                    result if isinstance(result, str) else "Unknown error occurred"
                )
                await interaction.followup.send(
                    f"❌ Failed to process media: {error_msg}\nContinuing without media.",
                    ephemeral=True,
                )
                # Return None for no image
                return None

        except asyncio.TimeoutError:
            await interaction.followup.send(
                "⏰ Media upload timed out. Continuing without media.", ephemeral=True
            )
            return None
        except Exception as e:
            print(f"Error in wait_for_media: {e}")
            import traceback

            traceback.print_exc()
            await interaction.followup.send(
                "❌ An error occurred while processing media.", ephemeral=True
            )
            return None

    async def download_and_reupload_media(self, content, attachments):
        """Download media and re-upload to media channel, then return new URL"""
        try:
            print(
                f"[DEBUG] Starting media processing for confession #{self.confession_id}"
            )

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
                            filename = f"gif_{self.confession_id}.gif"
                        else:
                            filename = f"image_{self.confession_id}.jpg"
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
                                filename = f"image_{self.confession_id}.jpg"
                            elif "image/png" in content_type:
                                filename = f"image_{self.confession_id}.png"
                            elif "image/gif" in content_type:
                                filename = f"image_{self.confession_id}.gif"
                            elif "image/webp" in content_type:
                                filename = f"image_{self.confession_id}.webp"
                            else:
                                # Default to jpg
                                filename = f"image_{self.confession_id}.jpg"

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
                    f"**Media for reply to confession #{self.confession_id}**",
                    file=file,
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

    async def finalize_reply_directly(self, image_url, interaction):
        """Finalize reply after waiting for possible media."""
        try:
            print(f"[DEBUG] Finalizing reply for confession #{self.confession_id}")
            print(f"[DEBUG] Image URL: {image_url}")

            channel = self.bot.get_channel(CONFESSION_CHANNEL_ID)
            if not channel:
                print("[DEBUG] Confession channel not found")
                return await interaction.followup.send(
                    "❌ Confession channel not found.", ephemeral=True
                )

            is_owner_reply = self.user_id == self.confession_author_id
            color = discord.Color.gold() if is_owner_reply else discord.Color.green()

            embed = discord.Embed(
                title=f"↩️ Reply to Confession #{self.confession_id}",
                description=self.reply_text,  # Use stored reply text
                color=color,
                timestamp=datetime.utcnow(),
            )

            if image_url:
                embed.set_image(url=image_url)
                print(f"[DEBUG] Set image URL in embed: {image_url}")

            if is_owner_reply:
                embed.set_footer(text="👑 OP")

            # Get confession message and thread
            try:
                print(
                    f"[DEBUG] Fetching confession message: {self.confession_message_id}"
                )
                confession_msg = await channel.fetch_message(self.confession_message_id)
                print("[DEBUG] Found confession message")
            except discord.NotFound:
                print(
                    f"[DEBUG] Confession message {self.confession_message_id} not found"
                )
                return await interaction.followup.send(
                    "❌ Original confession not found.", ephemeral=True
                )
            except discord.Forbidden:
                print(
                    f"[DEBUG] No permission to fetch message {self.confession_message_id}"
                )
                return await interaction.followup.send(
                    "❌ No permission to access confession.", ephemeral=True
                )

            # Get or create thread
            thread = confession_msg.thread
            if not thread:
                try:
                    print(
                        f"[DEBUG] Creating thread for confession #{self.confession_id}"
                    )
                    thread = await confession_msg.create_thread(
                        name=f"Confession #{self.confession_id} Replies",
                        auto_archive_duration=60,
                    )
                    print(f"[DEBUG] Thread created: {thread.id}")
                except discord.HTTPException as e:
                    print(f"[DEBUG] Error creating thread: {e}")
                    # Try with a simpler name if the first fails
                    try:
                        thread = await confession_msg.create_thread(
                            name=f"Replies #{self.confession_id}",
                            auto_archive_duration=60,
                        )
                        print("[DEBUG] Thread created with simple name")
                    except Exception as e2:
                        print(f"[DEBUG] Failed to create thread: {e2}")
                        return await interaction.followup.send(
                            "❌ Could not create discussion thread.", ephemeral=True
                        )

            # Send reply to thread
            print(f"[DEBUG] Sending reply to thread: {thread.id}")
            sent_msg = await thread.send(embed=embed)
            print(f"[DEBUG] Reply sent, message ID: {sent_msg.id}")

            # Save reply to database
            print("[DEBUG] Saving to database")
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "INSERT INTO replies (confession_id, user_id, description, image_url) VALUES (?, ?, ?, ?)",
                    (self.confession_id, self.user_id, self.reply_text, image_url),
                )
                await db.commit()
                print("[DEBUG] Database saved")

            # Logging
            await self.log_reply(image_url, color)

            # Notify OP if not own reply
            if not is_owner_reply:
                await self.notify_author(confession_msg)

            # Send completion message
            print("[DEBUG] Sending completion message")
            await interaction.followup.send(
                f"✅ Your reply has been posted!\n"
                f"📝 View it here: {sent_msg.jump_url}",
                ephemeral=True,
            )
            print("[DEBUG] Reply process completed successfully")

        except Exception as e:
            print(f"[DEBUG] Error finalizing reply: {e}")
            import traceback

            traceback.print_exc()
            await interaction.followup.send(
                f"❌ An error occurred while posting your reply: {str(e)}",
                ephemeral=True,
            )

    async def log_reply(self, image_url, color):
        """Log reply to log channel"""
        log_channel = self.bot.get_channel(LOG_CHANNEL_ID)
        if not log_channel:
            return

        try:
            log_embed = discord.Embed(
                title=f"Reply to Confession #{self.confession_id}",
                description=self.reply_text,
                color=color,
                timestamp=datetime.utcnow(),
            )
            user = await self.bot.fetch_user(self.user_id)
            log_embed.add_field(
                name="Replier", value=f"{user.mention} ({user.id})", inline=False
            )
            log_embed.add_field(
                name="Confession Author",
                value=f"<@{self.confession_author_id}> ({self.confession_author_id})",
                inline=False,
            )
            if image_url:
                log_embed.set_image(url=image_url)

            await log_channel.send(embed=log_embed)
        except Exception as e:
            print(f"[DEBUG] Error logging reply: {e}")

    async def notify_author(self, confession_msg):
        """Notify confession author about new reply"""
        try:
            confession_author = await self.bot.fetch_user(self.confession_author_id)
            notify_embed = discord.Embed(
                title=f"💌 New Reply to Your Confession #{self.confession_id}",
                description=f"Someone replied: \"{self.reply_text[:100]}{'...' if len(self.reply_text) > 100 else ''}\"",
                color=discord.Color.blue(),
                timestamp=datetime.utcnow(),
            )
            notify_embed.add_field(
                name="View Thread",
                value=f"[Click here to view the conversation]({confession_msg.jump_url})",
                inline=False,
            )
            await confession_author.send(embed=notify_embed)
        except discord.Forbidden:
            pass  # User has DMs disabled
        except Exception as e:
            print(f"[DEBUG] Error notifying confession author: {e}")
