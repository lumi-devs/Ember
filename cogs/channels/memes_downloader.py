import discord
from discord.ext import commands
import re
import asyncio
import shutil
from pathlib import Path
from typing import List, Optional
import os
from helpers.downloaders import download_youtube, download_instagram, download_reddit

MAX_FILE_SIZE = 8 * 1024 * 1024  # 8 MB
TARGET_CHANNEL_ID = 1419396753907646696
DOWNLOADS_DIR = Path("downloads")

class MediaFetcher(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        
        # Clear downloads folder
        if DOWNLOADS_DIR.exists():
            shutil.rmtree(DOWNLOADS_DIR, ignore_errors=True)
        DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
        print("✅ MediaFetcher cog initialized")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.channel.id != TARGET_CHANNEL_ID:
            return

        print(f"🟢 Saw message: {message.content}")
        urls = self.extract_urls(message.content)
        print(f"🔗 Extracted URLs: {urls}")

        if not urls:
            return

        for url in urls:
            print(f"➡️ Processing URL: {url}")
            await self.process_url(url, message.channel, message)

    def extract_urls(self, text: str) -> List[str]:
            KNOWN_DOMAINS = [
            "youtube.com",
            "youtu.be",
            "instagram.com",
            "reddit.com",
            "v.redd.it",
            "i.redd.it",
            ]
            urls = re.findall(r"https?://[^\s]+", text)
            return [url for url in urls if any(domain in url for domain in KNOWN_DOMAINS)]

    async def process_url(self, url: str, channel: discord.TextChannel, message: discord.Message):
        # Send initial message
        status_msg = await channel.send(f"⏳ Fetching content from {url}...")
        
        try:
            # Determine downloader based on URL
            if "youtube.com" in url or "youtu.be" in url:
                files = [await self.bot.loop.run_in_executor(None, download_youtube, url)]
                caption = None
            elif "instagram.com" in url:
                files = await self.bot.loop.run_in_executor(None, download_instagram, url)
                caption = None
            elif "reddit.com" in url or "v.redd.it" in url:
                files, title, caption_text = await self.bot.loop.run_in_executor(None, download_reddit, url)
                caption = f"{title}\n\n{caption_text}" if caption_text else title
            else:
                await status_msg.edit(content="❌ Unsupported URL.")
                await asyncio.sleep(3)
                await status_msg.delete()
                return
                
        except Exception as e:
            await status_msg.edit(content=f"❌ Failed to download\nMaybe NSFW?")
            await asyncio.sleep(3)
            await status_msg.delete()
            return
            
        await status_msg.delete()
        files = [f for f in files]
        
        if not files:
            await channel.send(f"❌ Failed to download media from {url}")
            return
        print(1)
            
        valid_files = []
        oversized_files = []
        
        for file_path in files:
            if isinstance(file_path, list):
                return file_path[0] if file_path else ""
            size = Path(file_path).stat().st_size
            (valid_files if size <= MAX_FILE_SIZE else oversized_files).append((file_path, size))
        print(1)
        # Notify about oversized files
        if oversized_files:
            oversized_info = "\n".join([f"{f} ({s} bytes)" for f, s in oversized_files])
            embed=discord.Embed(description=f"File was too Large.", color=discord.Color.orange())
            await channel.send(url, embed=embed)
            
        if not valid_files:
            for f in files:
                os.remove(f)
            return
        discord_files = [discord.File(str(f)) for f, _ in valid_files]
        print(1)        
        # Send message with files
        try:
            # Truncate caption if needed
            if caption and len(caption) > 2000:
                caption = caption[:1997] + "..."
            sent_msg = await channel.send(files=discord_files)
            print(1)
            
            # Create thread for media
            try:
                embed=discord.Embed(description=caption, colour=discord.Color.gold())
                thread = await sent_msg.create_thread(
                    name=f"Media by {message.author.display_name}"[:100],
                    auto_archive_duration=60
                )
                if caption:
                    await thread.send(embed=embed)
            except Exception:
                pass
                
            # Try to delete original message
            try:
                await message.delete()
            except Exception:
                pass
                
        except Exception as e:
            await channel.send(f"❌ Failed to upload files: {e}")
            
        # Clean up files
        for f in files:
            os.remove(f)

async def setup(bot: commands.Bot):
    await bot.add_cog(MediaFetcher(bot))