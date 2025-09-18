import discord
from discord.ext import commands

WATCH_CHANNEL_ID = 1417923188083654916  # Replace with your channel ID

async def setup(bot: commands.Bot):    
    @bot.event
    async def on_message(message: discord.Message):
        if message.author.bot:
            return
        if message.channel.id != WATCH_CHANNEL_ID:
            return

        if isinstance(message.channel, discord.Thread):
            return

        if message.attachments:
            try:
                thread_name = f"{message.author.display_name}'s thread"
                thread = await message.channel.create_thread(
                    name=thread_name,
                    message=message,
                    type=discord.ChannelType.public_thread,
                    auto_archive_duration=60
                )
            except Exception as e:
                print("[AttachmentThreads] Failed to create thread:", e)
        else:
            try:
                await message.delete()
            except Exception as e:
                print(e)
                pass
