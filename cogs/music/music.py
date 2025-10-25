import os
import discord
from discord.ext import commands, tasks
from discord import app_commands
import yt_dlp
from collections import deque
import asyncio
import random
from typing import Optional

# Optional Spotify support
try:
    import spotipy
    from spotipy.oauth2 import SpotifyClientCredentials
    SPOTIFY_AVAILABLE = True
except ImportError:
    SPOTIFY_AVAILABLE = False

# HD Quality Options
YTDL_OPTIONS = {
        "format": "bestaudio[abr<=96]/bestaudio",
        "noplaylist": True,
        "youtube_include_dash_manifest": False,
        "youtube_include_hls_manifest": False,
        "cookiefile": "cookies.txt",
}

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -nostdin",
    "options": "-vn -c:a libopus -b:a 128k -application voip -filter:a loudnorm",
}

class MusicPanelView(discord.ui.View):
    def __init__(self, cog, current_track):
        super().__init__(timeout=300)
        self.cog = cog
        self.current_track = current_track
        self.afk_channel_id = 1393175609638912010
        
    def create_progress_bar(self, duration, position=0):
        """Create a beautiful progress bar"""
        if duration == 0:
            return "▬▬▬▬▬▬▬▬▬▬▬▬▬▬"
        
        progress = min(position / duration, 1.0)
        bars = 20
        filled = int(bars * progress)
        bar = "▬" * filled + "🔘" + "▬" * (bars - filled - 1)
        return bar

    @discord.ui.button(emoji="⏮️", style=discord.ButtonStyle.gray, custom_id="previous")
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("🔄 Previous track feature coming soon!", ephemeral=True)

    @discord.ui.button(emoji="⏯️", style=discord.ButtonStyle.blurple, custom_id="play_pause")
    async def play_pause(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if not vc or not vc.is_connected():
            return await interaction.response.send_message("❌ Not connected to a voice channel.", ephemeral=True)

        if vc.is_playing():
            vc.pause()
            button.emoji = "▶️"
            await interaction.response.edit_message(view=self)
            embed = await self.create_music_panel("⏸️ Playback Paused")
            await interaction.message.edit(embed=embed, view=self)
        elif vc.is_paused():
            vc.resume()
            button.emoji = "⏸️"
            await interaction.response.edit_message(view=self)
            embed = await self.create_music_panel("▶️ Playback Resumed")
            await interaction.message.edit(embed=embed, view=self)
        else:
            await interaction.response.send_message("❌ Nothing is playing.", ephemeral=True)

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.gray, custom_id="next")
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()
            await interaction.response.send_message("⏭️ Skipping to next track...", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Nothing to skip.", ephemeral=True)

    @discord.ui.button(emoji="⏹️", style=discord.ButtonStyle.red, custom_id="stop")
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if vc and vc.is_connected():
            gid = str(interaction.guild_id)
            self.cog.song_queues.pop(gid, None)
            self.cog.current_track.pop(gid, None)
            self.cog.auto_play_status.pop(gid, False)
            vc.stop()
            
            # Move to AFK channel instead of disconnecting
            try:
                guild = interaction.guild
                afk_channel = guild.afk_channel
                
                if afk_channel:
                    await vc.move_to(afk_channel)
                    await guild.me.edit(mute=True, deafen=True)
                    
                    embed = discord.Embed(
                        title="🎵 Music Panel - Stopped",
                        description=f"**Playback stopped and moved to {afk_channel.mention}**",
                        color=discord.Color.red()
                    )
                else:
                    await vc.disconnect()
                    embed = discord.Embed(
                        title="🎵 Music Panel - Stopped",
                        description="**Playback stopped and bot disconnected**",
                        color=discord.Color.red()
                    )
                    
            except Exception as e:
                await vc.disconnect()
                embed = discord.Embed(
                    title="🎵 Music Panel - Stopped",
                    description="**Playback stopped and bot disconnected**",
                    color=discord.Color.red()
                )
            
            embed.set_footer(text="Use /play to start music again")
            await interaction.response.edit_message(embed=embed, view=None)
        else:
            await interaction.response.send_message("❌ Not connected to voice channel.", ephemeral=True)


    @discord.ui.button(emoji="🔀", style=discord.ButtonStyle.green, custom_id="shuffle")
    async def shuffle(self, interaction: discord.Interaction, button: discord.ui.Button):
        gid = str(interaction.guild_id)
        queue = self.cog.song_queues.get(gid)
        if queue and len(queue) > 1:
            random.shuffle(queue)
            self.cog.song_queues[gid] = deque(queue)
            await interaction.response.send_message("🔀 Queue shuffled!", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Not enough songs in queue to shuffle.", ephemeral=True)

    @discord.ui.button(emoji="🔁", style=discord.ButtonStyle.green, custom_id="loop")
    async def loop(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("🔄 Loop feature coming soon!", ephemeral=True)

    @discord.ui.button(emoji="♾️", style=discord.ButtonStyle.blurple, custom_id="autoplay")
    async def autoplay(self, interaction: discord.Interaction, button: discord.ui.Button):
        gid = str(interaction.guild_id)
        current_status = self.cog.auto_play_status.get(gid, False)
        
        if current_status:
            # Turn off autoplay
            self.cog.auto_play_status[gid] = False
            button.style = discord.ButtonStyle.gray
            await interaction.response.edit_message(view=self)
            await interaction.followup.send("🔴 **AutoPlay Disabled**\nMusic will stop when queue ends.", ephemeral=True)
        else:
            # Turn on autoplay
            self.cog.auto_play_status[gid] = True
            button.style = discord.ButtonStyle.blurple
            await interaction.response.edit_message(view=self)
            await interaction.followup.send("🟢 **AutoPlay Enabled**\nMusic will continue playing similar songs!", ephemeral=True)
        
        # Update panel to show new status
        embed = await self.create_music_panel()
        await interaction.message.edit(embed=embed, view=self)

    async def create_music_panel(self, status="Now Playing"):
        """Create a beautiful music panel embed"""
        embed = discord.Embed(
            title="🎵 MUSIC PANEL",
            color=discord.Color.from_rgb(114, 137, 218)
        )
        
        # Main track info
        if self.current_track:
            title = self.current_track.get('title', 'Unknown Title')
            duration = self.current_track.get('duration', 0)
            requester = self.current_track.get('requester', 'Unknown')
            gid = str(self.current_track.get('guild_id', ''))
            
            embed.add_field(
                name="🎶 Now Playing",
                value=f"**{title}**",
                inline=False
            )
            
            # Progress bar
            if duration > 0:
                progress_bar = self.create_progress_bar(duration)
                time_str = f"`{self.cog.format_duration(0)} / {self.cog.format_duration(duration)}`"
                embed.add_field(
                    name="Progress",
                    value=f"{progress_bar}\n{time_str}",
                    inline=False
                )
            
            # Metadata
            embed.add_field(
                name="Requested By",
                value=f"🎤 {requester.mention if hasattr(requester, 'mention') else requester}",
                inline=True
            )
            
            embed.add_field(
                name="Music Duration",
                value=f"⏱️ `{self.cog.format_duration(duration)}`",
                inline=True
            )
            
            # Quality info
            embed.add_field(
                name="Quality",
                value="🎧 `HD 128kbps`",
                inline=True
            )
            
            # AutoPlay status
            autoplay_status = self.cog.auto_play_status.get(gid, False)
            autoplay_text = "🟢 **Enabled**" if autoplay_status else "🔴 **Disabled**"
            embed.add_field(
                name="AutoPlay",
                value=autoplay_text,
                inline=True
            )
            
            # VC members count
            vc = self.cog.bot.get_guild(int(gid)).voice_client
            if vc and vc.channel:
                members_in_vc = len([m for m in vc.channel.members if not m.bot])
                embed.add_field(
                    name="Listeners",
                    value=f"👥 `{members_in_vc} user{'s' if members_in_vc != 1 else ''}`",
                    inline=True
                )
            
            # Set thumbnail if available
            if self.current_track.get("thumbnail"):
                embed.set_thumbnail(url=self.current_track["thumbnail"])
            
            # Queue info
            queue_len = len(self.cog.song_queues.get(gid, []))
            if queue_len > 0:
                embed.add_field(
                    name="Up Next",
                    value=f"📋 `{queue_len} track{'s' if queue_len != 1 else ''} in queue`",
                    inline=False
                )
        
        embed.set_footer(text=f"Status: {status} | Use buttons to control playback")
        return embed

class AdvancedMusicControls(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=180)
        self.cog = cog

    @discord.ui.button(emoji="📋", style=discord.ButtonStyle.blurple, custom_id="queue")
    async def show_queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        gid = str(interaction.guild_id)
        queue = self.cog.song_queues.get(gid, deque())
        
        embed = discord.Embed(
            title="📋 Playlist Queue",
            color=discord.Color.green()
        )
        
        if queue:
            queue_text = ""
            for i, (_, track) in enumerate(queue[:10], 1):
                duration = self.cog.format_duration(track.get('duration', 0))
                queue_text += f"`{i:2d}.` **{track['title']}** - `{duration}`\n"
            
            if len(queue) > 10:
                queue_text += f"\n*... and {len(queue) - 10} more tracks*"
            
            embed.description = queue_text
            embed.set_footer(text=f"Total: {len(queue)} tracks in queue")
        else:
            embed.description = "🎵 No tracks in queue. Add some with `/play`!"
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(emoji="🎛️", style=discord.ButtonStyle.gray, custom_id="settings")
    async def settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        gid = str(interaction.guild_id)
        autoplay_status = self.cog.auto_play_status.get(gid, False)
        
        embed = discord.Embed(
            title="🎛️ Audio Settings",
            color=discord.Color.blue()
        )
        
        embed.add_field(name="Quality", value="`HD 128kbps`", inline=True)
        embed.add_field(name="Format", value="`MP3/Opus`", inline=True)
        embed.add_field(name="Bitrate", value="`128kbps`", inline=True)
        embed.add_field(name="AutoPlay", value=f"`{'Enabled' if autoplay_status else 'Disabled'}`", inline=True)
        embed.add_field(name="Shuffle", value="`Available`", inline=True)
        embed.add_field(name="Loop", value="`Coming Soon`", inline=True)
        embed.add_field(name="VC Timeout", value="`2 minutes`", inline=True)
        embed.add_field(name="Max Quality", value="`128kbps`", inline=True)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.song_queues = {}
        self.current_track = {}
        self.music_panels = {}
        self.auto_play_status = {}  # Guild ID -> bool
        self.empty_vc_check.start()

    def cog_unload(self):
        self.empty_vc_check.cancel()

    @tasks.loop(seconds=30)
    async def empty_vc_check(self):
        """Check for empty voice channels and move to AFK channel after 2 minutes"""
        for guild in self.bot.guilds:
            gid = str(guild.id)
            vc = guild.voice_client
            
            if vc and vc.is_connected():
                # Count non-bot members in VC
                members_in_vc = len([m for m in vc.channel.members if not m.bot])
                
                if members_in_vc == 0:
                    # Start or increment empty timer
                    if gid not in self.empty_vc_timers:
                        self.empty_vc_timers[gid] = 0
                    else:
                        self.empty_vc_timers[gid] += 30
                    
                    # If empty for 2 minutes (120 seconds), move to AFK channel
                    if self.empty_vc_timers[gid] >= 120:
                        try:
                            # Stop playback but keep queue
                            if vc.is_playing() or vc.is_paused():
                                vc.stop()
                            
                            # Find AFK channel (use guild's AFK channel or a specified one)
                            afk_channel = None
                            
                            # Option 1: Use guild's AFK channel
                            if guild.afk_channel:
                                afk_channel = guild.afk_channel
                            
                            # Option 2: Use a specified channel ID
                            elif self.afk_channel_id:
                                afk_channel = guild.get_channel(self.afk_channel_id)
                            
                            # Option 3: Find any channel named "AFK" or "afk"
                            if not afk_channel:
                                for channel in guild.voice_channels:
                                    if 'afk' in channel.name.lower():
                                        afk_channel = channel
                                        break
                            
                            if afk_channel:
                                # Move to AFK channel
                                await vc.move_to(afk_channel)
                                
                                # Mute and deafen the bot
                                await guild.me.edit(mute=True, deafen=True)
                                
                                # Send notification
                                embed = await self.create_beautiful_embed(
                                    "🔇 Moved to AFK",
                                    f"Bot moved to {afk_channel.mention} and muted due to no users in voice channel.\n\n"
                                    f"*Use `/play` in a voice channel to resume music.*",
                                    discord.Color.orange()
                                )
                                
                                # Try to send to system channel or last known channel
                                try:
                                    if guild.system_channel:
                                        await guild.system_channel.send(embed=embed)
                                except:
                                    pass
                                    
                            else:
                                # If no AFK channel found, just disconnect
                                await vc.disconnect()
                                embed = await self.create_beautiful_embed(
                                    "🔌 Disconnected",
                                    "Left voice channel due to no users listening for 2 minutes.",
                                    discord.Color.orange()
                                )
                                if guild.system_channel:
                                    await guild.system_channel.send(embed=embed)
                            
                            # Reset timer
                            self.empty_vc_timers.pop(gid, None)
                                
                        except Exception as e:
                            print(f"Error handling empty VC: {e}")
                else:
                    # Reset timer if users are present
                    self.empty_vc_timers.pop(gid, None)
                    
                    # Ensure bot is not muted/deafened when users are present
                    if vc and vc.channel and vc.channel != guild.afk_channel:
                        try:
                            await guild.me.edit(mute=False, deafen=False)
                        except:
                            pass

    async def search_ytdlp_async(self, query):
        """Search for music with enhanced quality"""
        return await asyncio.to_thread(self._extract_info, query)

    def _extract_info(self, query):
        with yt_dlp.YoutubeDL(YTDL_OPTIONS) as ydl:
            return ydl.extract_info(query, download=False)

    async def get_related_track(self, current_track_title):
        """Get a related track for autoplay"""
        try:
            # Search for similar songs based on current track
            search_query = f"similar to {current_track_title}"
            results = await self.search_ytdlp_async(search_query)
            track = results["entries"][0] if "entries" in results else results
            
            if track:
                return {
                    "title": track.get("title", "Unknown Track"),
                    "url": track["url"],
                    "webpage_url": track.get("webpage_url", ""),
                    "thumbnail": track.get("thumbnail", ""),
                    "duration": track.get("duration", 0),
                    "requester": self.bot.user,  # Bot is the requester for autoplay
                    "autoplay": True  # Mark as autoplay track
                }
        except Exception as e:
            print(f"Error getting related track: {e}")
        return None

    def is_spotify_url(self, url: str):
        return url.startswith(("https://open.spotify.com/track/", "https://spotify.com/track/"))

    def extract_spotify_track_info(self, url):
        if not self.spotify:
            return None
        try:
            track_id = url.split("track/")[1].split("?")[0]
            track = self.spotify.track(track_id)
            artists = ", ".join([a["name"] for a in track["artists"]])
            return f"{artists} - {track['name']}"
        except Exception as e:
            print(f"Spotify error: {e}")
            return None

    def format_duration(self, seconds):
        """Format duration in seconds to MM:SS or HH:MM:SS"""
        if seconds == 0:
            return "Live"
        
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        seconds = seconds % 60
        
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        else:
            return f"{minutes:02d}:{seconds:02d}"

    async def create_beautiful_embed(self, title, description, color=discord.Color.blue()):
        """Create a consistently styled embed"""
        embed = discord.Embed(
            title=title,
            description=description,
            color=color,
            timestamp=discord.utils.utcnow()
        )
        embed.set_footer(text="Music Bot • Premium Quality • AutoPlay Available")
        return embed

    async def play_next_song(self, vc, gid, channel):
        gid_str = str(gid)
        
        # Check if queue is empty but autoplay is enabled
        if not self.song_queues.get(gid_str) and self.auto_play_status.get(gid_str, False):
            current_track = self.current_track.get(gid_str)
            if current_track:
                # Get a related track for autoplay
                related_track = await self.get_related_track(current_track['title'])
                if related_track:
                    self.song_queues.setdefault(gid_str, deque()).append((related_track["url"], related_track))
                    
                    # Send autoplay notification
                    embed = await self.create_beautiful_embed(
                        "♾️ AutoPlay",
                        f"**Added similar track:**\n**{related_track['title']}**",
                        discord.Color.purple()
                    )
                    await channel.send(embed=embed)

        # Check if queue is empty
        if not self.song_queues.get(gid_str):
            # Start empty VC check
            self.empty_vc_timers = getattr(self, 'empty_vc_timers', {})
            self.empty_vc_timers[gid_str] = 0
            
            await asyncio.sleep(300)  # 5 minutes for queue empty
            if vc.is_connected() and not vc.is_playing():
                await vc.disconnect()
                embed = await self.create_beautiful_embed(
                    "🔌 Disconnected", 
                    "Left voice channel due to inactivity",
                    discord.Color.orange()
                )
                await channel.send(embed=embed)
            return

        # Get next track
        audio_url, track_info = self.song_queues[gid_str].popleft()
        track_info['guild_id'] = gid_str
        self.current_track[gid_str] = track_info

        try:
            # Create HD audio source
            source = discord.FFmpegOpusAudio(audio_url, **FFMPEG_OPTIONS)

            def after_play(error):
                if error:
                    print(f"Playback error: {error}")
                # Schedule next song
                asyncio.run_coroutine_threadsafe(
                    self.play_next_song(vc, gid, channel), 
                    self.bot.loop
                )

            vc.play(source, after=after_play)

            # Send beautiful music panel
            panel_view = MusicPanelView(self, track_info)
            embed = await panel_view.create_music_panel("🎵 Now Playing")
            
            # Add autoplay indicator if it's an autoplay track
            if track_info.get('autoplay'):
                embed.add_field(
                    name="AutoPlay",
                    value="🔮 *This track was added automatically*",
                    inline=False
                )
            
            # Store panel message for updates
            panel_msg = await channel.send(embed=embed, view=panel_view)
            self.music_panels[gid_str] = panel_msg

        except Exception as e:
            error_embed = await self.create_beautiful_embed(
                "❌ Playback Error",
                f"Could not play **{track_info['title']}**: {str(e)}",
                discord.Color.red()
            )
            await channel.send(embed=error_embed)
            # Try next song
            await self.play_next_song(vc, gid, channel)

    # Initialize empty VC timers
    @empty_vc_check.before_loop
    async def before_empty_vc_check(self):
        await self.bot.wait_until_ready()
        self.empty_vc_timers = {}

    # ────────────────────────────── Commands ──────────────────────────────

    @app_commands.command(name="play", description="Play a song with HD quality")
    @app_commands.describe(query="Song name, YouTube URL, or Spotify URL")
    async def play(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer()
        
        if not interaction.user.voice:
            embed = await self.create_beautiful_embed(
                "❌ Voice Channel Required",
                "You must be in a voice channel to play music!",
                discord.Color.red()
            )
            return await interaction.followup.send(embed=embed, ephemeral=True)

        vc = interaction.guild.voice_client
        channel = interaction.user.voice.channel

        # Connect to voice channel
        if not vc:
            vc = await channel.connect()
            
            # Set bot to open mic but deafened
            try:
                await interaction.guild.me.edit(mute=False, deafen=True)
            except Exception as e:
                print(f"Could not set voice state: {e}")
                
        elif vc.channel != channel:
            await vc.move_to(channel)
            
            # Ensure proper voice state when moving
            try:
                await interaction.guild.me.edit(mute=False, deafen=True)
            except Exception as e:
                print(f"Could not set voice state: {e}")

        # Reset empty VC timer when someone joins
        gid = str(interaction.guild_id)
        self.empty_vc_timers.pop(gid, None)

        # Ensure bot is not muted when users are present
        try:
            await interaction.guild.me.edit(mute=False, deafen=True)
        except:
            pass
        # Reset empty VC timer when someone joins
        gid = str(interaction.guild_id)
        self.empty_vc_timers.pop(gid, None)

        # Handle Spotify URLs
        search_query = query
        if self.is_spotify_url(query):
            spotify_query = self.extract_spotify_track_info(query)
            if spotify_query:
                search_query = spotify_query
                spotify_embed = await self.create_beautiful_embed(
                    "🎵 Spotify Track Detected",
                    f"Searching for: **{spotify_query}**",
                    discord.Color.green()
                )
                await interaction.followup.send(embed=spotify_embed)
            else:
                embed = await self.create_beautiful_embed(
                    "❌ Spotify Error",
                    "Could not extract track information from Spotify URL.",
                    discord.Color.red()
                )
                return await interaction.followup.send(embed=embed)

        try:
            # Search for track
            loading_embed = await self.create_beautiful_embed(
                "🔍 Searching...",
                f"Looking for: **{search_query}**\n\n*🔄 Processing HD audio...*",
                discord.Color.blue()
            )
            await interaction.followup.send(embed=loading_embed)

            results = await self.search_ytdlp_async(search_query)
            track = results["entries"][0] if "entries" in results else results
            
            if not track:
                embed = await self.create_beautiful_embed(
                    "❌ No Results",
                    "Could not find any tracks matching your search.",
                    discord.Color.red()
                )
                return await interaction.followup.send(embed=embed)

            # Create track info
            track_info = {
                "title": track.get("title", "Unknown Track"),
                "url": track["url"],
                "webpage_url": track.get("webpage_url", ""),
                "thumbnail": track.get("thumbnail", ""),
                "duration": track.get("duration", 0),
                "requester": interaction.user,
            }

            # Add to queue
            self.song_queues.setdefault(gid, deque()).append((track_info["url"], track_info))

            # Send response based on current state
            if vc.is_playing() or vc.is_paused():
                queue_pos = len(self.song_queues[gid])
                embed = await self.create_beautiful_embed(
                    "📥 Added to Queue",
                    f"**{track_info['title']}**\n\n"
                    f"**Position in queue:** `{queue_pos}`\n"
                    f"**Duration:** `{self.format_duration(track_info['duration'])}`\n"
                    f"**Quality:** `HD 128kbps`\n"
                    f"**AutoPlay:** `{'Enabled' if self.auto_play_status.get(gid, False) else 'Disabled'}`",
                    discord.Color.green()
                )
                if track_info.get("thumbnail"):
                    embed.set_thumbnail(url=track_info["thumbnail"])
                await interaction.followup.send(embed=embed)
            else:
                embed = await self.create_beautiful_embed(
                    "🎶 Starting Playback",
                    f"**{track_info['title']}**\n\n"
                    f"*🔄 Preparing HD audio stream...*",
                    discord.Color.blue()
                )
                await interaction.followup.send(embed=embed)
                await self.play_next_song(vc, interaction.guild_id, interaction.channel)

        except Exception as e:
            embed = await self.create_beautiful_embed(
                "❌ Error",
                f"An error occurred while processing your request:\n`{str(e)}`",
                discord.Color.red()
            )
            await interaction.followup.send(embed=embed)

    @app_commands.command(name="autoplay", description="Toggle autoplay mode")
    async def autoplay(self, interaction: discord.Interaction):
        gid = str(interaction.guild_id)
        current_status = self.auto_play_status.get(gid, False)
        
        if current_status:
            # Turn off autoplay
            self.auto_play_status[gid] = False
            embed = await self.create_beautiful_embed(
                "🔴 AutoPlay Disabled",
                "Music will stop when the queue ends.\n\n*Use `/autoplay` to enable it again.*",
                discord.Color.orange()
            )
        else:
            # Turn on autoplay
            self.auto_play_status[gid] = True
            embed = await self.create_beautiful_embed(
                "🟢 AutoPlay Enabled",
                "Music will continue playing similar songs when the queue ends!\n\n"
                "*The bot will automatically add related tracks to keep the music going.*",
                discord.Color.green()
            )
        
        await interaction.response.send_message(embed=embed)

    # ... (keep the existing skip, queue, nowplaying, stop, shuffle, panel commands as they are)

async def setup(bot):
    await bot.add_cog(Music(bot))