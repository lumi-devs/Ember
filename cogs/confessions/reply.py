import discord
from discord import ui
import asyncio
from datetime import datetime
import aiosqlite
import aiohttp
import io
from cogs.confessions.helpers import process_media, DB_PATH, extract_url

CONFESSION_CHANNEL_ID = 1399658407178272798
LOG_CHANNEL_ID = 1453409054314467339
MEDIA_CHANNEL_ID = 1424139009931743382

class ReplyModal(ui.Modal, title="Anonymous Reply"):
    reply_input = ui.TextInput(
        label="Reply", 
        style=discord.TextStyle.paragraph, 
        max_length=500,
        placeholder="Type your reply here...",
        required=True
    )

    def __init__(self, bot, confession_id, confession_message_id, confession_author_id):
        super().__init__()
        self.bot = bot
        self.confession_id = confession_id
        self.confession_message_id = confession_message_id
        self.confession_author_id = confession_author_id
        self.user_id = None

    async def on_submit(self, interaction: discord.Interaction):
        self.user_id = interaction.user.id

        # Defer immediately to avoid timeout
        await interaction.response.defer(ephemeral=True, thinking=True)

        # Send media prompt FIRST
        await interaction.followup.send(
            "✅ Reply submitted! You have **60 seconds** to reply with an image URL or attachment.\n"
            "Send `skip` to continue without media.\n\n"
            "**Accepted formats:**\n"
            "• Discord image upload (drag & drop)\n"
            "• Direct image URL (.png/.jpg/.gif/.webp)",
            ephemeral=True
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
            return m.author.id == self.user_id and m.channel.id == interaction.channel.id

        try:
            msg_received = await self.bot.wait_for("message", timeout=60.0, check=check)
            
            # Check if user wants to skip
            if msg_received.content.lower().strip() != 'skip':
                # Process media by downloading and re-uploading
                success, result = await self.download_and_reupload_media(
                    msg_received.content, 
                    msg_received.attachments,
                    interaction
                )
                
                # Only delete the message AFTER successful media processing
                try:
                    await msg_received.delete()
                except discord.Forbidden:
                    print(f"No permission to delete message from {msg_received.author}")
                except discord.NotFound:
                    print("Message already deleted")
                except Exception as e:
                    print(f"Error deleting message: {e}")
                
                if success:
                    await interaction.followup.send("✅ Media added to reply!", ephemeral=True)
                    return result  # Returns the media URL
                else:
                    await interaction.followup.send(
                        f"❌ Media processing failed: {result}\nContinuing without media.",
                        ephemeral=True
                    )
                    return None
            else:
                # User typed 'skip' - delete the skip message
                try:
                    await msg_received.delete()
                except:
                    pass
                await interaction.followup.send("✅ Skipped media upload.", ephemeral=True)
                return None
                    
        except asyncio.TimeoutError:
            await interaction.followup.send("⏰ Media upload timed out. Continuing without media.", ephemeral=True)
            return None
        except Exception as e:
            print(f"Error in wait_for_media: {e}")
            await interaction.followup.send("❌ An error occurred while processing media.", ephemeral=True)
            return None

    async def download_and_reupload_media(self, content, attachments, interaction):
        """Download media and re-upload to media channel, then return new URL"""
        try:
            # Step 1: Get the media URL
            media_url = None
            file_data = None
            filename = None
            
            # Check attachments first
            if attachments:
                attachment = attachments[0]
                media_url = attachment.url
                filename = attachment.filename
            # Check content for URL
            elif content:
                media_url = extract_url(content)
                if media_url:
                    # Try to extract filename from URL
                    filename = media_url.split('/')[-1].split('?')[0]
            
            if not media_url:
                return False, "No media found in message"
            
            # Step 2: Download the media
            async with aiohttp.ClientSession() as session:
                try:
                    async with session.get(media_url) as response:
                        if response.status != 200:
                            return False, f"Failed to download media: HTTP {response.status}"
                        
                        # Read the image data
                        file_data = await response.read()
                        
                        # If no filename, try to guess from content-type
                        if not filename:
                            content_type = response.headers.get('Content-Type', '')
                            if 'image/jpeg' in content_type:
                                filename = f"image_{self.confession_id}.jpg"
                            elif 'image/png' in content_type:
                                filename = f"image_{self.confession_id}.png"
                            elif 'image/gif' in content_type:
                                filename = f"image_{self.confession_id}.gif"
                            elif 'image/webp' in content_type:
                                filename = f"image_{self.confession_id}.webp"
                            else:
                                filename = f"media_{self.confession_id}.bin"
                
                except aiohttp.ClientError as e:
                    return False, f"Failed to download media: {str(e)}"
                except Exception as e:
                    return False, f"Error downloading: {str(e)}"
            
            if not file_data:
                return False, "No media data received"
            
            # Step 3: Upload to media channel
            media_channel = self.bot.get_channel(MEDIA_CHANNEL_ID)
            if not media_channel:
                return False, "Media channel not found"
            
            # Clean up any existing messages for this confession reply
            await self.cleanup_existing_media(media_channel)
            
            # Create file object
            file = discord.File(io.BytesIO(file_data), filename=filename)
            
            # Upload to media channel
            try:
                media_msg = await media_channel.send(
                    f"**Media for reply to confession #{self.confession_id}**",
                    file=file
                )
                
                # Wait a moment for Discord to process
                await asyncio.sleep(2)
                
                # Get the URL from the uploaded message
                final_url = None
                
                # Check attachments first (most reliable)
                if media_msg.attachments:
                    final_url = media_msg.attachments[0].url
                # Check embeds as fallback
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
                
                # Verify it's a valid image URL
                if any(final_url.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp']):
                    return True, final_url
                else:
                    # Even if extension doesn't match, if Discord accepted it as an image, use it
                    return True, final_url
                
            except discord.HTTPException as e:
                return False, f"Failed to upload to media channel: {str(e)}"
            
        except Exception as e:
            print(f"Error in download_and_reupload_media: {e}")
            import traceback
            traceback.print_exc()
            return False, f"Error: {str(e)}"
    
    async def cleanup_existing_media(self, media_channel):
        """Clean up any existing media messages for this confession reply"""
        try:
            # Search for existing media messages for this confession reply
            async for message in media_channel.history(limit=50):
                if f"reply to confession #{self.confession_id}" in message.content.lower():
                    try:
                        await message.delete()
                    except:
                        pass
        except Exception as e:
            print(f"Error cleaning up existing media: {e}")

    async def finalize_reply_directly(self, image_url, interaction):
        """Finalize reply after waiting for possible media."""
        try:
            channel = self.bot.get_channel(CONFESSION_CHANNEL_ID)
            if not channel:
                print("Confession channel not found")
                return await interaction.followup.send(
                    "❌ Confession channel not found.",
                    ephemeral=True
                )

            is_owner_reply = self.user_id == self.confession_author_id
            color = discord.Color.gold() if is_owner_reply else discord.Color.green()

            embed = discord.Embed(
                title=f"↩️ Reply to Confession #{self.confession_id}",
                description=self.reply_input.value,
                color=color,
                timestamp=datetime.utcnow()
            )

            if image_url:
                embed.set_image(url=image_url)

            if is_owner_reply:
                embed.set_footer(text="👑 OP")

            # Get confession message and thread
            try:
                confession_msg = await channel.fetch_message(self.confession_message_id)
            except discord.NotFound:
                print(f"Confession message {self.confession_message_id} not found")
                return await interaction.followup.send(
                    "❌ Original confession not found.",
                    ephemeral=True
                )
            except discord.Forbidden:
                print(f"No permission to fetch message {self.confession_message_id}")
                return await interaction.followup.send(
                    "❌ No permission to access confession.",
                    ephemeral=True
                )

            # Get or create thread
            thread = confession_msg.thread
            if not thread:
                try:
                    thread = await confession_msg.create_thread(
                        name=f"Confession #{self.confession_id} Replies",
                        auto_archive_duration=60
                    )
                except discord.HTTPException as e:
                    print(f"Error creating thread: {e}")
                    # Try with a simpler name if the first fails
                    try:
                        thread = await confession_msg.create_thread(
                            name=f"Replies #{self.confession_id}",
                            auto_archive_duration=60
                        )
                    except:
                        return await interaction.followup.send(
                            "❌ Could not create discussion thread.",
                            ephemeral=True
                        )

            # Send reply to thread
            sent_msg = await thread.send(embed=embed)

            # Save reply to database
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "INSERT INTO replies (confession_id, user_id, description, image_url) VALUES (?, ?, ?, ?)",
                    (self.confession_id, self.user_id, self.reply_input.value, image_url)
                )
                await db.commit()

            # Logging
            await self.log_reply(image_url, color)
            
            # Notify OP if not own reply
            if not is_owner_reply:
                await self.notify_author(confession_msg)

            # Send completion message
            await interaction.followup.send(
                f"✅ Your reply has been posted!\n"
                f"📝 View it here: {sent_msg.jump_url}",
                ephemeral=True
            )

        except Exception as e:
            print(f"Error finalizing reply: {e}")
            import traceback
            traceback.print_exc()
            await interaction.followup.send(
                "❌ An error occurred while posting your reply.",
                ephemeral=True
            )

    async def log_reply(self, image_url, color):
        """Log reply to log channel"""
        log_channel = self.bot.get_channel(LOG_CHANNEL_ID)
        if not log_channel:
            return

        try:
            log_embed = discord.Embed(
                title=f"Reply to Confession #{self.confession_id}",
                description=self.reply_input.value,
                color=color,
                timestamp=datetime.utcnow()
            )
            user = await self.bot.fetch_user(self.user_id)
            log_embed.add_field(name="Replier", value=f"{user.mention} ({user.id})", inline=False)
            log_embed.add_field(name="Confession Author", value=f"<@{self.confession_author_id}> ({self.confession_author_id})", inline=False)
            if image_url:
                log_embed.set_image(url=image_url)
                
            await log_channel.send(embed=log_embed)
        except Exception as e:
            print(f"Error logging reply: {e}")

    async def notify_author(self, confession_msg):
        """Notify confession author about new reply"""
        try:
            confession_author = await self.bot.fetch_user(self.confession_author_id)
            notify_embed = discord.Embed(
                title=f"💌 New Reply to Your Confession #{self.confession_id}",
                description=f"Someone replied: \"{self.reply_input.value[:100]}{'...' if len(self.reply_input.value) > 100 else ''}\"",
                color=discord.Color.blue(),
                timestamp=datetime.utcnow()
            )
            notify_embed.add_field(
                name="View Thread",
                value=f"[Click here to view the conversation]({confession_msg.jump_url})",
                inline=False
            )
            await confession_author.send(embed=notify_embed)
        except discord.Forbidden:
            pass  # User has DMs disabled
        except Exception as e:
            print(f"Error notifying confession author: {e}")