# In cogs/confessions/reply.py
import discord
from discord import ui
import asyncio
from cogs.confessions.helpers import process_media

CONFESSION_CHANNEL_ID = 1399658407178272798
LOG_CHANNEL_ID = 1424792373837172899
MEDIA_CHANNEL_ID = 1424139009931743382

from datetime import datetime
import zoneinfo

IST = zoneinfo.ZoneInfo("Asia/Kolkata")

timestamp=datetime.now(IST)

class ReplyModal(ui.Modal, title="Anonymous Reply"):
    reply_input = ui.TextInput(label="Reply", style=discord.TextStyle.paragraph, max_length=500)

    def __init__(self, bot, confession_id, confession_message_id, confession_author_id):
        super().__init__()
        self.bot = bot
        self.confession_id = confession_id
        self.confession_message_id = confession_message_id
        self.confession_author_id = confession_author_id
        self.user_id = None

    async def on_submit(self, interaction: discord.Interaction):
        self.user_id = interaction.user.id

        # 👑 THE FIX → always defer first!!
        await interaction.response.defer(ephemeral=True, thinking=False)

        # Inform user (using follow-up)
        await interaction.followup.send(
            "✅ Reply submitted! You have **30 seconds** to send an image/URL here or type `skip`.",
            ephemeral=True
        )

        cleaner_cog = self.bot.get_cog("ConfessionCleaner")
        if cleaner_cog:
            await cleaner_cog.allow_user_once(self.user_id)

        def check(m):
            return m.author.id == self.user_id and m.channel.id == interaction.channel.id

        image_url = None

        try:
            msg_received = await self.bot.wait_for("message", timeout=30.0, check=check)

            if msg_received.content.lower() != "skip":
                image_url = await process_media(
                    self.bot, msg_received.content, msg_received.attachments, MEDIA_CHANNEL_ID
                )

            # delete user's message silently
            try:
                await msg_received.delete()
            except:
                pass

        except asyncio.TimeoutError:
            pass

        await self.finalize_reply_directly(image_url)

    async def finalize_reply_directly(self, image_url):
        """Finalize reply after waiting for possible media."""
        channel = self.bot.get_channel(CONFESSION_CHANNEL_ID)

        is_owner_reply = self.user_id == self.confession_author_id
        color = discord.Color.gold() if is_owner_reply else discord.Color.green()

        embed = discord.Embed(
            title=f"↩️ Reply to Confession #{self.confession_id}",
            description=self.reply_input.value,
            color=color,
            timestamp=timestamp  # ← fixed timestamp
        )

        if image_url:
            embed.set_image(url=image_url)

        if is_owner_reply:
            embed.set_footer(text="👑 OP")

        try:
            confession_msg = await channel.fetch_message(self.confession_message_id)

            thread = confession_msg.thread
            if not thread:
                thread = await confession_msg.create_thread(
                    name=f"Confession #{self.confession_id} Replies",
                    auto_archive_duration=60
                )

            await thread.send(embed=embed)

            # LOGGING
            log_channel = self.bot.get_channel(LOG_CHANNEL_ID)
            if log_channel:
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

            # Notify OP
            if not is_owner_reply:
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
                    pass
                except Exception as e:
                    print(f"Error notifying confession author: {e}")

        except Exception as e:
            print(f"Error finalizing reply: {e}")
