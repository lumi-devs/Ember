import discord
from discord import ui
import asyncio
from datetime import datetime
from cogs.confessions.helpers import DB_PATH, extract_url, init_db, process_media
import aiosqlite

CONFESSION_CHANNEL_ID = 1399658407178272798
LOG_CHANNEL_ID = 1424792373837172899
MEDIA_CHANNEL_ID = 1424139009931743382


class ReplyMediaView(ui.View):
    def __init__(self, bot, confession_id, message_id, user_id, reply_content):
        super().__init__(timeout=30.0)
        self.bot = bot
        self.confession_id = confession_id
        self.message_id = message_id
        self.user_id = user_id
        self.reply_content = reply_content

    @ui.button(label="Add Image URL", style=discord.ButtonStyle.secondary, emoji="🖼️")
    async def add_image_url(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ This is not for you!", ephemeral=True)
        await interaction.response.send_message(
            "Please send your image URL in the chat within 30 seconds. Type `skip` to cancel.", 
            ephemeral=True
        )

    @ui.button(label="Skip Media", style=discord.ButtonStyle.danger, emoji="⏭️")
    async def skip_media(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ This is not for you!", ephemeral=True)
        await interaction.response.send_message("Skipped media attachment.", ephemeral=True)
        await self.finalize_reply(interaction, None)

    async def finalize_reply(self, interaction, image_url):
        """Finalize the reply with or without media"""
        channel = self.bot.get_channel(CONFESSION_CHANNEL_ID)
        
        # Embed for reply
        embed = discord.Embed(
            title=f"↩️ Reply to Confession #{self.confession_id}",
            description=self.reply_content,
            color=discord.Color.green(),
            timestamp=datetime.utcnow()
        )
        if image_url:
            embed.set_image(url=image_url)
            
        try:
            # Get confession message and thread
            confession_msg = await channel.fetch_message(self.message_id)
            thread = confession_msg.thread
            if not thread:
                thread = await confession_msg.create_thread(
                    name=f"Confession #{self.confession_id} Replies",
                    auto_archive_duration=60  # 24 hours instead of 60 minutes
                )
            
            # Send reply in thread
            await thread.send(embed=embed)

            # Log to log channel
            log_channel = self.bot.get_channel(LOG_CHANNEL_ID)
            if log_channel:
                log_embed = discord.Embed(
                    title=f"Reply to Confession #{self.confession_id}",
                    description=self.reply_content,
                    color=discord.Color.green(),
                    timestamp=datetime.utcnow()
                )
                user = await self.bot.fetch_user(self.user_id)
                log_embed.add_field(name="User", value=f"{user} ({user.id})", inline=False)
                if image_url:
                    log_embed.set_image(url=image_url)
                await log_channel.send(embed=log_embed)

            # Save reply to database
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "INSERT INTO replies (confession_id, user_id, description, image_url) VALUES (?, ?, ?, ?)",
                    (self.confession_id, self.user_id, self.reply_content, image_url)
                )
                await db.commit()

            # Notify confession author via DM
            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute("SELECT user_id FROM confessions WHERE id=?", (self.confession_id,))
                row = await cursor.fetchone()

            if row:
                author_id = row[0]
                try:
                    confession_author = await self.bot.fetch_user(author_id)
                    notify_embed = discord.Embed(
                        title=f"💌 New Reply to Your Confession #{self.confession_id}",
                        description=f"Someone replied to your confession: \"{self.reply_content[:100]}{'...' if len(self.reply_content) > 100 else ''}\"",
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

            await interaction.followup.send("✅ Reply submitted successfully!", ephemeral=True)

        except Exception as e:
            await interaction.followup.send(f"❌ Failed to post reply: {e}", ephemeral=True)


class ReplyModal(ui.Modal, title="Anonymous Reply"):
    reply_input = ui.TextInput(label="Reply", style=discord.TextStyle.paragraph, max_length=500)

    def __init__(self, bot, confession_id, message_id):
        super().__init__()
        self.bot = bot
        self.confession_id = confession_id
        self.message_id = message_id
        self.user_id = None

    async def on_submit(self, interaction: discord.Interaction):
        self.user_id = interaction.user.id
        
        # Send initial response with media options
        await interaction.response.send_message(
            "✅ Reply submitted! You have 30s to send an image/URL or use the buttons below. Type `skip` to cancel.", 
            ephemeral=True,
            view=ReplyMediaView(self.bot, self.confession_id, self.message_id, self.user_id, self.reply_input.value)
        )
        
        # Allow user to send media via message
        cleaner_cog = self.bot.get_cog("ConfessionCleaner")
        if cleaner_cog:
            await cleaner_cog.allow_user_once(self.user_id)

        def check(m):
            return m.author.id == self.user_id and m.channel.id == interaction.channel.id

        try:
            msg_received = await self.bot.wait_for("message", timeout=30.0, check=check)
            
            # Process media from message if not skip
            if msg_received.content.lower() != 'skip':
                image_url = await process_media(self.bot, msg_received.content, msg_received.attachments, MEDIA_CHANNEL_ID)
                
                if image_url:
                    # Finalize reply with media
                    await self.finalize_reply_directly(image_url)
            
            # Delete user's message after processing
            try:
                await msg_received.delete()
            except:
                pass
                
        except asyncio.TimeoutError:
            # Timeout - no media added, finalize without media
            await self.finalize_reply_directly(None)

    async def finalize_reply_directly(self, image_url):
        """Finalize reply without interaction context"""
        channel = self.bot.get_channel(CONFESSION_CHANNEL_ID)
        
        # Embed for reply
        embed = discord.Embed(
            title=f"↩️ Reply to Confession #{self.confession_id}",
            description=self.reply_input.value,
            color=discord.Color.green(),
            timestamp=datetime.utcnow()
        )
        if image_url:
            embed.set_image(url=image_url)
            
        try:
            # Get confession message and thread
            confession_msg = await channel.fetch_message(self.message_id)
            thread = confession_msg.thread
            if not thread:
                thread = await confession_msg.create_thread(
                    name=f"Confession #{self.confession_id} Replies",
                    auto_archive_duration=60
                )
            
            # Send reply in thread
            await thread.send(embed=embed)

            # Log to log channel
            log_channel = self.bot.get_channel(LOG_CHANNEL_ID)
            if log_channel:
                log_embed = discord.Embed(
                    title=f"Reply to Confession #{self.confession_id}",
                    description=self.reply_input.value,
                    color=discord.Color.green(),
                    timestamp=datetime.utcnow()
                )
                user = await self.bot.fetch_user(self.user_id)
                log_embed.add_field(name="User", value=f"{user.mention} ({user.id})", inline=False)
                if image_url:
                    log_embed.set_image(url=image_url)
                await log_channel.send(embed=log_embed)

            # Save reply to database
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "INSERT INTO replies (confession_id, user_id, description, image_url) VALUES (?, ?, ?, ?)",
                    (self.confession_id, self.user_id, self.reply_input.value, image_url)
                )
                await db.commit()

            # Notify confession author via DM
            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute("SELECT user_id FROM confessions WHERE id=?", (self.confession_id,))
                row = await cursor.fetchone()

            if row:
                author_id = row[0]
                try:
                    confession_author = await self.bot.fetch_user(author_id)
                    notify_embed = discord.Embed(
                        title=f"💌 New Reply to Your Confession #{self.confession_id}",
                        description=f"Someone replied to your confession: \"{self.reply_input.value[:100]}{'...' if len(self.reply_input.value) > 100 else ''}\"",
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
            print(f"Error finalizing reply: {e}")