import discord
from discord import ui
import asyncio
from datetime import datetime
import aiosqlite
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
            "✅ Reply submitted! You have **30 seconds** to reply with an image URL or attachment. "
            "Send `skip` to continue without media.",
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
            msg_received = await self.bot.wait_for("message", timeout=30.0, check=check)
            
            # Check if user wants to skip
            if msg_received.content.lower() != 'skip':
                # Process media with fallback
                success, result = await self.process_media_with_fallback(
                    msg_received.content, 
                    msg_received.attachments
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

    async def process_media_with_fallback(self, content, attachments):
        """Process media with fallback to fetching from media channel"""
        # Try to process media normally first
        image_url = await process_media(
            self.bot, 
            content, 
            attachments, 
            MEDIA_CHANNEL_ID
        )
        
        if image_url:
            return True, image_url
        
        # If normal processing fails, try to extract and fetch from media channel
        try:
            # Extract URL from message content
            url = extract_url(content) if content else None
            
            if not url and attachments:
                # Use first attachment if no URL in content
                url = attachments[0].url
            
            if not url:
                return False, "No media found in message"
            
            # Send to media channel
            media_channel = self.bot.get_channel(MEDIA_CHANNEL_ID)
            if not media_channel:
                return False, "Media channel not found"
            
            # Send the URL to media channel
            media_msg = await media_channel.send(f"Media for reply to confession #{self.confession_id}: {url}")
            
            # Wait a moment for Discord to process
            await asyncio.sleep(2)
            
            # Try to fetch the message again to get proper embed
            try:
                media_msg = await media_channel.fetch_message(media_msg.id)
                
                # Check for embed
                if media_msg.embeds:
                    embed = media_msg.embeds[0]
                    if embed.image:
                        return True, embed.image.url
                    elif embed.thumbnail:
                        return True, embed.thumbnail.url
                
                # Check for attachments
                if media_msg.attachments:
                    return True, media_msg.attachments[0].url
                
                # Fallback to content URL
                return True, url
                
            except discord.NotFound:
                return False, "Media message not found in media channel"
            
        except Exception as e:
            print(f"Error in media fallback processing: {e}")
            return False, f"Error: {str(e)}"

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
                    return await interaction.followup.send(
                        "❌ Could not create discussion thread.",
                        ephemeral=True
                    )

            # Send reply to thread
            await thread.send(embed=embed)

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
                "✅ Your reply has been posted!",
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