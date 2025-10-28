import discord
from discord import ui
import asyncio
from datetime import datetime
from cogs.confessions.helpers import DB_PATH, process_media
import aiosqlite

CONFESSION_CHANNEL_ID = 1399658407178272798
LOG_CHANNEL_ID = 1424792373837172899
MEDIA_CHANNEL_ID = 1424139009931743382


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

        # ✅ Send simple ephemeral message (no buttons)
        await interaction.response.send_message(
            "✅ Reply submitted! You have **30 seconds** to send an image/URL in chat or type `skip` to continue without media.",
            ephemeral=True
        )

        # Allow user to send media via message
        cleaner_cog = self.bot.get_cog("ConfessionCleaner")
        if cleaner_cog:
            await cleaner_cog.allow_user_once(self.user_id)

        def check(m):
            return m.author.id == self.user_id and m.channel.id == interaction.channel.id

        image_url = None
        try:
            msg_received = await self.bot.wait_for("message", timeout=30.0, check=check)

            # Process media if user didn’t type skip
            if msg_received.content.lower() != 'skip':
                image_url = await process_media(
                    self.bot, msg_received.content, msg_received.attachments, MEDIA_CHANNEL_ID
                )

            try:
                await msg_received.delete()
            except:
                pass

        except asyncio.TimeoutError:
            pass  # No response

        # Finalize reply
        await self.finalize_reply_directly(image_url)

    async def finalize_reply_directly(self, image_url):
        """Finalize reply after waiting for possible media."""
        channel = self.bot.get_channel(CONFESSION_CHANNEL_ID)

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

            # Send reply to thread
            await thread.send(embed=embed)

            # Log reply
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

            # Save reply in DB
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "INSERT INTO replies (confession_id, user_id, description, image_url) VALUES (?, ?, ?, ?)",
                    (self.confession_id, self.user_id, self.reply_input.value, image_url)
                )
                await db.commit()

            # Notify confession author
            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute("SELECT user_id FROM confessions WHERE id=?", (self.confession_id,))
                row = await cursor.fetchone()

            if row:
                author_id = row[0]
                try:
                    confession_author = await self.bot.fetch_user(author_id)
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
                    pass  # DMs off

        except Exception as e:
            print(f"Error finalizing reply: {e}")
