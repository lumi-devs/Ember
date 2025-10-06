import discord
from discord import ui
import asyncio
from datetime import datetime
from cogs.confessions.helpers import DB_PATH, extract_url, init_db, process_media
import aiosqlite

CONFESSION_CHANNEL_ID = 1399658407178272798
LOG_CHANNEL_ID = 1424792373837172899
MEDIA_CHANNEL_ID=1424139009931743382

class ReplyModal(ui.Modal, title="Anonymous Reply"):
    reply_input = ui.TextInput(label="Reply", style=discord.TextStyle.paragraph, max_length=500)

    def __init__(self, bot, confession_id, message_id):
        super().__init__()
        self.bot = bot
        self.confession_id = confession_id
        self.message_id = message_id  # store original confession message
        self.user_id = None

    async def on_submit(self, interaction: discord.Interaction):
        self.user_id = interaction.user.id
        channel = self.bot.get_channel(CONFESSION_CHANNEL_ID)

        user = await self.bot.fetch_user(self.user_id)
        await interaction.response.send_message(
            "✅ Reply submitted! You have 20s to send an image/URL or type `skip`.", ephemeral=True
        )
        cleaner_cog = self.bot.get_cog("ConfessionCleaner")
        if cleaner_cog:
           self.bot.loop.create_task(cleaner_cog.temporarily_allow_user(user.id, 20))

        def check(m):
            return m.author.id == user.id and m.channel.id == CONFESSION_CHANNEL_ID

        try:
            msg_received = await self.bot.wait_for("message", timeout=10.0, check=check)
            image_url = await process_media(self.bot, msg_received.content, msg_received.attachments, MEDIA_CHANNEL_ID)
        except asyncio.TimeoutError:
            image_url = None
        finally:
            # delete user’s message to keep channel clean
            if 'msg_received' in locals():
                try:
                    await msg_received.delete()
                except:
                    pass
                # Lock channel again

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
            confession_msg = await channel.fetch_message(self.message_id)
            thread = confession_msg.thread
            if not thread:
                thread = await confession_msg.create_thread(
                    name=f"Confession #{self.confession_id} Replies",
                    auto_archive_duration=60
                )
            await thread.send(embed=embed)

            # Log channel
            log_channel = self.bot.get_channel(LOG_CHANNEL_ID)
            if log_channel:
                log_embed = discord.Embed(
                    title=f"Reply to Confession #{self.confession_id}",
                    description=self.reply_input.value,
                    color=discord.Color.purple(),
                    timestamp=datetime.utcnow()
                )
                user = await self.bot.fetch_user(self.user_id)
                log_embed.add_field(name="User", value=user.mention, inline=False)
                if image_url:
                    log_embed.set_image(url=image_url)
                await log_channel.send(f'Reply #{self.confession_id}', embed=log_embed)

            # DM the confession author
            confession_author = confession_msg.author
            try:
                await confession_author.send(embed=embed)
            except discord.Forbidden:
                # User has DMs closed
                pass

        except Exception as e:
            await interaction.followup.send(f"❌ Failed to post reply: {e}", ephemeral=True)

