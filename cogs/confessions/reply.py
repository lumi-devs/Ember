import discord
from discord import ui
import asyncio
from datetime import datetime
import aiosqlite
from cogs.confessions.helpers import process_media, DB_PATH

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

        # Send initial response
        await interaction.response.send_message(
            "✅ Reply submitted! You have **30 seconds** to send an image/URL here or type `skip`.",
            ephemeral=True
        )

        # Allow user to send one message in confession channel
        cleaner_cog = self.bot.get_cog("ConfessionCleaner")
        if cleaner_cog:
            await cleaner_cog.allow_user_once(self.user_id)

        # Wait for media
        image_url = await self.wait_for_media(interaction)
        
        # Finalize reply
        await self.finalize_reply_directly(image_url)

        # Send completion message
        await interaction.followup.send(
            "✅ Your reply has been posted!",
            ephemeral=True
        )

    async def wait_for_media(self, interaction):
        """Wait for media response"""
        def check(m):
            return m.author.id == self.user_id and m.channel.id == interaction.channel.id

        try:
            msg_received = await self.bot.wait_for("message", timeout=30.0, check=check)

            # Check if user wants to skip
            if msg_received.content.lower() != "skip":
                # Process media FIRST
                image_url = await process_media(
                    self.bot, msg_received.content, msg_received.attachments, MEDIA_CHANNEL_ID
                )
            else:
                image_url = None

            # Delete user's message AFTER processing
            try:
                await msg_received.delete()
            except:
                pass
                
            return image_url

        except asyncio.TimeoutError:
            return None

    async def finalize_reply_directly(self, image_url):
        """Finalize reply after waiting for possible media."""
        try:
            channel = self.bot.get_channel(CONFESSION_CHANNEL_ID)
            if not channel:
                print("Confession channel not found")
                return

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
                return
            except discord.Forbidden:
                print(f"No permission to fetch message {self.confession_message_id}")
                return

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
                    return

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

        except Exception as e:
            print(f"Error finalizing reply: {e}")
            import traceback
            traceback.print_exc()

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