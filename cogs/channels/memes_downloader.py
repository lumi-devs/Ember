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
TARGET_CHANNEL_ID = 1393171137017413712
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
            print(f"➡️ Processing URL: `{url}`")
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
        # Send initial status message
        status_msg = await channel.send(f"⏳ Fetching content from `{url}`...")

        files: List[str] = []
        caption = ""

        try:
            # YouTube
            if "youtube.com" in url or "youtu.be" in url:
                files, title, caption_text = await self.bot.loop.run_in_executor(None, download_youtube, url)
                caption = f"{title}\n\n{caption_text}" if caption_text else title

            # Instagram
            elif "instagram.com" in url:
                files, title, caption_text = await self.bot.loop.run_in_executor(None, download_instagram, url)
                caption = f"{title}\n\n{caption_text}" if caption_text else title

            # Reddit
            elif "reddit.com" in url or "v.redd.it" in url or "i.redd.it" in url:
                files, title, caption_text = await self.bot.loop.run_in_executor(None, download_reddit, url)
                caption = f"{title}\n\n{caption_text}" if caption_text else title

            else:
                await status_msg.edit(content=f"❌ Unsupported URL: `{url}`")
                await asyncio.sleep(3)
                await status_msg.delete()
                return

        except Exception as e:
            await status_msg.edit(content=f"{url})
            await asyncio.sleep(3)
            await status_msg.delete()
            return

        await status_msg.delete()

        # Remove invalid or empty files
        files = [str(f) for f in files if f and os.path.exists(str(f))]
        if not files:
            await channel.send(f"❌ Failed to download media from `{url}`")
            return

        valid_files = []
        oversized_files = []

        for file_path in files:
            size = os.path.getsize(file_path)
            if size <= MAX_FILE_SIZE:
                valid_files.append((file_path, size))
            else:
                oversized_files.append((file_path, size))

        # Notify about oversized files
        if oversized_files:
            embed = discord.Embed(
                description=f"File(s) too large for Discord (<8MB):\n" +
                            "\n".join([f"`{os.path.basename(f)}` ({s//1024} KB)" for f, s in oversized_files]),
                color=discord.Color.orange()
            )
            await channel.send(f"⚠️ Media from {url} skipped:", embed=embed)

        if not valid_files:
            # Clean up
            for f, _ in files:
                try:
                    os.remove(f)
                except:
                    pass
            return

        # Send valid files
        discord_files = [discord.File(f) for f, _ in valid_files]
        try:
            sent_msg = await channel.send(files=discord_files)
            if caption:
                embed = discord.Embed(description=caption, color=discord.Color.gold())
                thread = await sent_msg.create_thread(
                    name=f"Media by {message.author.display_name}"[:100],
                    auto_archive_duration=60
                )
                await thread.send(embed=embed)
        except Exception as e:
            await channel.send(f"❌ Failed to upload files from `{url}`: {e}")

        # Clean up
        for f, _ in valid_files:
            try:
                os.remove(f)
            except:
                pass

async def setup(bot: commands.Bot):
    await bot.add_cog(MediaFetcher(bot))
