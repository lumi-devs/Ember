import discord
from discord.ext import commands
import re
import os
import asyncio
import shutil
from pathlib import Path
from typing import List, Tuple, Optional

# Your downloader functions (must return either str, list[str], or (list[str], caption))
from helpers.downloaders import download_youtube, download_instagram, download_reddit

MAX_FILE_SIZE = 8 * 1024 * 1024  # 8 MB
TARGET_CHANNEL_ID = 1393171137017413712  # Replace with your channel ID if needed
DOWNLOADS_DIR = Path("downloads")


class MediaFetcher(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        # Reset downloads folder on cog init
        if DOWNLOADS_DIR.exists():
            try:
                shutil.rmtree(DOWNLOADS_DIR)
            except Exception as e:
                print(f"⚠️ Could not remove downloads folder: {e}")
        DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
        print("✅ MediaFetcher cog initialized (downloads folder cleared)")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Ignore bots and other channels
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
        pattern = (
            r"(https?://(?:www\.)?"
            r"(?:youtube\.com/watch\?v=[\w-]+|youtu\.be/[\w-]+|youtube\.com/shorts/[\w-]+"
            r"|instagram\.com/(?:p|reel)/[\w-]+/?"
            r"|reddit\.com/r/[\w-]+/comments/[\w-]+[^\s]*"
            r"|v\.redd\.it/[\w-]+))"
        )
        return re.findall(pattern, text)

    async def process_url(self, url: str, channel: discord.TextChannel, message: discord.Message):
        msg_og = await channel.send(f"⏳ Fetching content from {url}...")
        loop = asyncio.get_running_loop()

        try:
            if "youtube.com" in url or "youtu.be" in url:
                result = await loop.run_in_executor(None, download_youtube, url)
            elif "instagram.com" in url:
                result = await loop.run_in_executor(None, download_instagram, url)
            elif "reddit.com" in url or "v.redd.it" in url:
                result = await loop.run_in_executor(None, download_reddit, url)
            else:
                await msg_og.edit(content="❌ Unsupported URL.")
                await msg_og.delete()
                return
        except Exception as e:
            await msg_og.edit(content=f"❌ Failed to download: {e}")
            await msg_og.delete()
            return

        # Normalize result
        files: List[str] = []
        caption: Optional[str] = None

        if isinstance(result, tuple) and len(result) == 2:
            maybe_files, maybe_caption = result
            caption = maybe_caption
            if isinstance(maybe_files, (list, tuple)):
                files = [str(x) for x in maybe_files if x]
            elif maybe_files:
                files = [str(maybe_files)]
        elif isinstance(result, (list, tuple)):
            files = [str(x) for x in result if x]
        elif isinstance(result, str):
            files = [result] if result else []

        # Ensure paths exist
        existing_paths: List[str] = []
        for p in files:
            try:
                p_path = Path(p)
                if not p_path.exists():
                    alt = DOWNLOADS_DIR / p
                    if alt.exists():
                        p_path = alt
                if p_path.exists():
                    existing_paths.append(str(p_path))
                else:
                    print(f"⚠️ Downloaded file not found: {p}")
            except Exception as e:
                print(f"⚠️ Error checking file path {p}: {e}")

        await msg_og.delete()

        # Partition by size
        in_limit: List[str] = []
        too_large: List[Tuple[str, int]] = []
        for p in existing_paths:
            try:
                size = Path(p).stat().st_size
                if size <= MAX_FILE_SIZE:
                    in_limit.append(p)
                else:
                    too_large.append((p, size))
            except Exception as e:
                print(f"⚠️ Could not stat file {p}: {e}")

        if not in_limit:
            if existing_paths:
                text = (
                    f"⚠️ Media downloaded but files are too large to send "
                    f"(limit {MAX_FILE_SIZE} bytes).\nSource: {url}"
                )
                if caption:
                    text += f"\n\n{caption[:1900]}"
                await channel.send(text)
            else:
                await channel.send("❌ Could not retrieve media.")
            for p in existing_paths:
                try:
                    Path(p).unlink()
                except Exception as e:
                    print(f"⚠️ Could not delete {p}: {e}")
            return

        # Send files
        discord_files = []
        for p in in_limit:
            try:
                discord_files.append(discord.File(p))
            except Exception as e:
                print(f"⚠️ Could not create discord.File for {p}: {e}")

        if not discord_files:
            await channel.send("❌ Could not prepare media to send.")
            for p in existing_paths:
                try:
                    Path(p).unlink()
                except Exception:
                    pass
            return

        send_content = caption if caption else None
        try:
            msg = await channel.send(content=send_content, files=discord_files)
        except discord.HTTPException as e:
            await channel.send(f"❌ Failed to upload files: {e}. Source: {url}")
            for p in existing_paths:
                try:
                    Path(p).unlink()
                except Exception:
                    pass
            return

        try:
            thread = await msg.create_thread(
                name=f"Media by {message.author.display_name}", auto_archive_duration=60
            )
            if caption and not send_content:
                await thread.send(caption)
        except Exception as e:
            print(f"⚠️ Could not create thread: {e}")

        try:
            await message.delete()
        except Exception:
            pass

        for p in existing_paths:
            try:
                Path(p).unlink()
            except Exception as e:
                print(f"⚠️ Could not delete {p}: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(MediaFetcher(bot))
    print("✅ MediaFetcher cog setup complete")
