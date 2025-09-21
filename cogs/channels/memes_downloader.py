import discord
from discord.ext import commands
import re
import os
import asyncio
from helpers.downloaders import download_youtube, download_instagram, download_reddit
import shutil

MAX_FILE_SIZE = 8 * 1024 * 1024  # 8MB
TARGET_CHANNEL_ID = 1393171137017413712  # Replace with your channel ID

class MediaFetcher(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        downloads_path = "downloads"

        # Delete the folder if it exists
        if os.path.exists(downloads_path):
            shutil.rmtree(downloads_path)

        # Recreate the folder
        os.makedirs(downloads_path, exist_ok=True)
        print("✅ MediaFetcher cog initialized (downloads folder cleared)")

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.channel.id != TARGET_CHANNEL_ID or message.author.bot:
            return

        urls = self.extract_urls(message.content)
        if not urls:
            return

        for url in urls:
            await self.process_url(url, message.channel, message)

    def extract_urls(self, text):
        pattern = r"(https?://(?:www\.)?(?:youtube\.com/watch\?v=[\w-]+|youtu\.be/[\w-]+|youtube\.com/shorts/[\w-]+|instagram\.com/(?:p|reel)/[\w-]+/?|reddit\.com/r/[\w-]+/comments/[\w-]+))"
        return re.findall(pattern, text)

    async def process_url(self, url, channel, message):
        msg_og = await channel.send(f"⏳ Fetching content from {url}...")

        loop = asyncio.get_event_loop()
        try:
            if "youtube.com" in url or "youtu.be" in url:
                files = await loop.run_in_executor(None, lambda: [download_youtube(url)])
                caption = None
            elif "instagram.com" in url:
                files = await loop.run_in_executor(None, download_instagram, url)
                caption = None
            elif "reddit.com" in url:
                files, caption = await loop.run_in_executor(None, download_reddit, url)
            else:
                await channel.send("❌ Unsupported URL.")
                await msg_og.delete()
                return
        except Exception as e:
            await channel.send(f"❌ Failed to download: {e}")
            await msg_og.delete()
            return
        await msg_og.delete()

        files_to_send = []
        file_paths = []  # keep track of actual file paths

        for f in files:
          if f and os.path.exists(f) and os.path.getsize(f) <= MAX_FILE_SIZE:
            files_to_send.append(discord.File(f))
            file_paths.append(f)  # store path separately

        if not files_to_send:
         await channel.send("❌ Could not retrieve media.")
         return

        msg = await channel.send(files=files_to_send)
        thread = await msg.create_thread(
             name=f"Media by {message.author.display_name}", auto_archive_duration=60
        )
        if caption:
         await thread.send(caption)

        try:
          await message.delete()
        except:
         pass

        for f in file_paths:
         try:
            os.remove(f)
         except Exception as e:
             print(f"⚠️ Could not delete {f}: {e}")



async def setup(bot: commands.Bot):
    await bot.add_cog(MediaFetcher(bot))
    print("✅ MediaFetcher cog setup complete")
