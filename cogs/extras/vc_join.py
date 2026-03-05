import os
import asyncio
import logging
import random

import discord
from discord.ext import commands

logger = logging.getLogger("BotLogger")


class AutoJoinVoice(commands.Cog):
    """Automatically joins a voice channel on startup and maintains connection"""
    
    def __init__(self, bot):
        self.bot = bot
        self.voice_channel_id = int(os.environ.get("VOICE_CHANNEL_ID_ON_START", 0))
        self.is_connecting = False
        self.connection_attempts = 0
        self.max_attempts = 5
        self.reconnect_task = None
        
    async def cog_load(self):
        """Called when cog is loaded"""
        if self.voice_channel_id:
            logger.info("🎵 AutoJoinVoice cog loaded - will attempt to join voice on ready")
        else:
            logger.warning("⚠️ VOICE_CHANNEL_ID_ON_START not set - voice auto-join disabled")
    
    @commands.Cog.listener()
    async def on_ready(self):
        """Join voice channel when bot is ready"""
        if self.voice_channel_id:
            # Wait a bit for everything to initialize
            await asyncio.sleep(5)
            await self.ensure_voice_connection()
    
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        """Monitor voice state changes"""
        if member != self.bot.user:
            return
        
        if before.channel and not after.channel:
            # Bot was disconnected
            logger.warning("⚠️ Bot disconnected from voice channel")
            if not self.is_connecting:
                # Try to reconnect after a delay
                await asyncio.sleep(3)
                await self.ensure_voice_connection()
        
        elif after.channel and (before.channel != after.channel):
            # Bot moved channels
            logger.info(f"🔄 Bot moved to {after.channel.name}")
    
    @commands.Cog.listener()
    async def on_resumed(self):
        """Handle session resume"""
        logger.info("🔄 Session resumed - checking voice connection")
        await asyncio.sleep(2)
        await self.ensure_voice_connection()
    
    async def cleanup_voice(self, guild):
        """Clean up voice connection"""
        try:
            if guild.voice_client:
                logger.info(f"🧹 Cleaning up voice in {guild.name}")
                await guild.voice_client.disconnect(force=True)
                await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"❌ Cleanup error: {e}")
    
    async def ensure_voice_connection(self):
        """Ensure bot is connected to voice"""
        if self.is_connecting:
            logger.info("⏳ Voice connection already in progress")
            return
        
        self.is_connecting = True
        
        try:
            channel = self.bot.get_channel(self.voice_channel_id)
            if not channel:
                logger.error(f"❌ Voice channel {self.voice_channel_id} not found")
                return
            
            guild = channel.guild
            voice = guild.voice_client
            
            # Check existing connection
            if voice and voice.is_connected():
                if voice.channel.id == self.voice_channel_id:
                    logger.info(f"✅ Already connected to {channel.name}")
                    return
                else:
                    # Move to correct channel
                    logger.info(f"🔄 Moving to {channel.name}")
                    await voice.move_to(channel)
                    return
            
            # Clean up any stale connection
            if voice:
                await self.cleanup_voice(guild)
                await asyncio.sleep(1)
            
            # Connect with retries
            for attempt in range(self.max_attempts):
                try:
                    timeout = min(20 + (attempt * 10), 45)
                    logger.info(f"🔌 Connecting to {channel.name} (attempt {attempt + 1}/{self.max_attempts})")
                    
                    await channel.connect(
                        timeout=timeout,
                        reconnect=True,
                        self_deaf=True,
                        self_mute=True
                    )
                    
                    logger.info(f"✅ Connected to {channel.name}")
                    self.connection_attempts = 0
                    return
                    
                except Exception as e:
                    self.connection_attempts += 1
                    logger.error(f"❌ Attempt {attempt + 1} failed: {type(e).__name__}")
                    
                    if attempt < self.max_attempts - 1:
                        wait = (2 ** attempt) + random.uniform(1, 2)
                        logger.info(f"⏳ Retrying in {wait:.1f}s")
                        await asyncio.sleep(wait)
                    else:
                        logger.error("❌ Max attempts reached")
            
        except Exception as e:
            logger.error(f"❌ Voice error: {e}")
        finally:
            self.is_connecting = False
    
    @discord.app_commands.command(name="joinvc", description="Make bot join the voice channel")
    async def join_vc(self, interaction: discord.Interaction):
        """Manual command to join voice"""
        await interaction.response.defer(ephemeral=True)
        await self.ensure_voice_connection()
        await interaction.followup.send("🔄 Attempting to join voice...")
    
    @discord.app_commands.command(name="leavevc", description="Make bot leave voice channel")
    async def leave_vc(self, interaction: discord.Interaction):
        """Manual command to leave voice"""
        if interaction.guild.voice_client:
            await interaction.guild.voice_client.disconnect()
            await interaction.response.send_message("👋 Left voice channel")
        else:
            await interaction.response.send_message("❌ Not in a voice channel")


async def setup(bot):
    await bot.add_cog(AutoJoinVoice(bot))