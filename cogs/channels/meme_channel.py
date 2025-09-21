import discord
from discord.ext import commands
import re
import os
import asyncio
from yt_dlp import YoutubeDL

MAX_FILE_SIZE = 8 * 1024 * 1024  # 8MB per file
TARGET_CHANNEL_ID = 1393171137017413712  # Replace with your channel ID

class MediaFetcher(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.ydl_opts = {
            'outtmpl': 'downloads/%(title)s.%(ext)s',
            'format': 'bestvideo+bestaudio/best',
            'merge_output_format': 'mp4',
            'noplaylist': True,
        }
        os.makedirs("downloads", exist_ok=True)
        print("✅ MediaFetcher cog initialized")

    @commands.Cog.listener()
    async def on_message(self, message):
        # Only process messages in the target channel
        if message.channel.id != TARGET_CHANNEL_ID:
            return  # Ignore messages from all other channels

        print(f"🔍 Received message from {message.author} in target channel: {message.content}")
        if message.author.bot:
            print("⏩ Ignoring bot message")
            return

        urls = self.extract_urls(message.content)
        if not urls:
            print("⏩ No URLs found in message")
            return

        for url in urls:
            print(f"➡ Processing URL: {url}")
            await self.process_url(url, message.channel, message)

    def extract_urls(self, text):
        pattern = r"(https?://(?:www\.)?(?:youtube\.com/watch\?v=[\w-]+|youtu\.be/[\w-]+|youtube\.com/shorts/[\w-]+(?:\?[\w=&-]+)?|instagram\.com/(?:p|reel)/[\w-]+/?))"
        urls = re.findall(pattern, text)
        print(f"🔗 Extracted URLs: {urls}")
        return urls

    async def process_url(self, url, channel, message):
        print(f"⏳ Fetching content from {url}")
        msg_og=await channel.send(f"⏳ Fetching content from {url}...")

        try:
            loop = asyncio.get_event_loop()
            info_list = await loop.run_in_executor(None, self.download_media, url)
            print(f"✅ Download complete: {info_list}")
        except Exception as e:
            print(f"❌ Download failed: {e}")
            await channel.send(f"❌ Failed to download: {e}")
            return

        if not info_list:
            print("❌ No media info found")
            await channel.send("❌ Could not retrieve media.")
            return

        files_to_send = []
        for info in info_list:
            filepath = info.get("filepath")
            if not filepath or not os.path.exists(filepath):
                print(f"⚠ Filepath not found: {filepath}")
                continue

            file_size = os.path.getsize(filepath)
            print(f"📂 File: {filepath}, Size: {file_size / (1024*1024):.2f}MB")
            if file_size > MAX_FILE_SIZE:
                print(f"⚠ File too large to upload: {filepath}")
                await channel.send(f"⚠️ File too large to upload ({file_size / (1024*1024):.2f}MB). Skipping.")
                continue

            files_to_send.append(discord.File(filepath))

        if files_to_send:
            print(f"📤 Sending {len(files_to_send)} files")
            x=await channel.send(files=files_to_send)
            await msg_og.delete()
            await x.create_thread(
                name=f"Meme by {message.author.name}",
                 auto_archive_duration=60
            )
            try:
                await message.delete()
                print("🗑 Deleted original message containing URLs")
            except Exception as e:
                print(f"⚠ Could not delete original message: {e}")

            # Clean up downloaded files
            for f in files_to_send:
                os.remove(f.fp.name)
                print(f"🗑 Deleted file: {f.fp.name}")

    def download_media(self, url):
        print(f"⬇ Starting download for URL: {url}")
        with YoutubeDL(self.ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

            if isinstance(info, dict):
                info_list = [info]
            else:
                info_list = info

            for item in info_list:
                item['filepath'] = ydl.prepare_filename(item)
                print(f"✅ Prepared filepath: {item['filepath']}")

            return info_list

async def setup(bot: commands.Bot):
    await bot.add_cog(MediaFetcher(bot))
    print("✅ MediaFetcher cog setup complete")
