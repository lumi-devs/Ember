import discord
from discord.ext import commands, tasks
import yt_dlp
import asyncio
import os

STREAM_URL = os.environ.get("YT_STREAM_URL", "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
VC_CHANNEL_ID = int(os.environ.get("RADIO_CHANNEL_ID", 0))


class MusicStream(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.voice_client: discord.VoiceClient | None = None
        self.channel_id = VC_CHANNEL_ID
        self.current_source = None
        self.stream_task.start()

    @tasks.loop(seconds=5)
    async def stream_task(self):
        if not self.bot.is_ready():
            return

        channel = self.bot.get_channel(self.channel_id)
        if not channel:
            print(f"[DEBUG] Channel {self.channel_id} not found.")
            return

        # Connect to VC if not connected
        if not self.voice_client or not self.voice_client.is_connected():
            try:
                print(f"[DEBUG] Connecting to VC: {channel.name} ({channel.id})")
                self.voice_client = await channel.connect()
                # Deafens the bot
                await self.voice_client.guild.change_voice_state(channel=channel, self_deaf=True)
                print("[DEBUG] Connected and deafened.")
            except Exception as e:
                print(f"[DEBUG] Failed to connect: {e}")
                return

        # If not playing or source finished, fetch new audio
        if not self.voice_client.is_playing():
            print("[DEBUG] Bot not playing. Fetching audio...")
            source = await self.get_audio_source(STREAM_URL)
            if source:
                self.current_source = source
                print("[DEBUG] Playing audio...")
                self.voice_client.play(
                    source,
                    after=lambda e: print(f"[DEBUG] Player finished or error: {e}" if e else "[DEBUG] Playback finished.")
                )
            else:
                print("[DEBUG] Failed to get audio source.")

    async def get_audio_source(self, url: str):
        """Fetch the direct audio URL from YouTube via yt-dlp and return FFmpegPCMAudio"""
        ytdl_opts = {
            "format": "bestaudio/best",
            "quiet": True,
            "extractaudio": True,
            "noplaylist": True,
            "geo_bypass": True,
        }
        ffmpeg_opts = {
            "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
            "options": "-vn -b:a 320k"
        }

        loop = asyncio.get_event_loop()
        try:
            info = await loop.run_in_executor(None, lambda: yt_dlp.YoutubeDL(ytdl_opts).extract_info(url, download=False))
            audio_url = info['url']
            print(f"[DEBUG] Extracted audio URL: {audio_url[:80]}...")
            return discord.FFmpegPCMAudio(audio_url, **ffmpeg_opts)
        except Exception as e:
            print(f"[DEBUG] Error fetching audio: {e}")
            return None

    @commands.command()
    async def stop_stream(self, ctx):
        """Stop the stream and disconnect"""
        if self.voice_client and self.voice_client.is_connected():
            self.voice_client.stop()
            await self.voice_client.disconnect()
            self.voice_client = None
            await ctx.send("🛑 Stream stopped and disconnected.")
            print("[DEBUG] Stream stopped.")


async def setup(bot: commands.Bot):
    await bot.add_cog(MusicStream(bot))
