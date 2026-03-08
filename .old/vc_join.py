import os
import asyncio
import logging
import random
import time
from datetime import datetime, timedelta

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

        # Cooldown tracking
        self.failure_count = 0
        self.cooldown_until = None
        self.cooldown_hours = 1  # 1 hour cooldown
        self.max_failures_before_cooldown = 3  # Enter cooldown after 3 failures

    async def cog_load(self):
        """Called when cog is loaded"""
        if self.voice_channel_id:
            logger.info(
                "🎵 AutoJoinVoice cog loaded - will attempt to join voice on ready"
            )
        else:
            logger.warning(
                "⚠️ VOICE_CHANNEL_ID_ON_START not set - voice auto-join disabled"
            )

    def is_in_cooldown(self):
        """Check if we're in cooldown period"""
        if self.cooldown_until and datetime.now() < self.cooldown_until:
            remaining = (self.cooldown_until - datetime.now()).total_seconds()
            logger.info(f"⏰ In cooldown for another {remaining/60:.1f} minutes")
            return True
        return False

    async def enter_cooldown(self):
        """Enter cooldown period after too many failures"""
        self.cooldown_until = datetime.now() + timedelta(hours=self.cooldown_hours)
        logger.warning(
            f"⚠️ Too many voice failures ({self.failure_count}). Entering {self.cooldown_hours}h cooldown until {self.cooldown_until.strftime('%H:%M:%S')}"
        )

        # Schedule exit from cooldown
        if self.reconnect_task and not self.reconnect_task.done():
            self.reconnect_task.cancel()

        # Schedule reconnection after cooldown
        self.reconnect_task = asyncio.create_task(self.cooldown_reconnect())

    async def cooldown_reconnect(self):
        """Wait for cooldown to end then try reconnecting"""
        try:
            if self.cooldown_until:
                wait_seconds = (self.cooldown_until - datetime.now()).total_seconds()
                if wait_seconds > 0:
                    await asyncio.sleep(wait_seconds)

                logger.info("⏰ Cooldown ended - attempting to reconnect")
                self.failure_count = 0
                self.cooldown_until = None
                await self.ensure_voice_connection()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"❌ Error in cooldown reconnect: {e}")

    @commands.Cog.listener()
    async def on_ready(self):
        """Join voice channel when bot is ready"""
        if self.voice_channel_id and not self.is_in_cooldown():
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

            if not self.is_connecting and not self.is_in_cooldown():
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
        if not self.is_in_cooldown():
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
        """Ensure bot is connected to voice with cooldown protection"""
        # Check cooldown first
        if self.is_in_cooldown():
            logger.info("⏳ Skipping voice connection - in cooldown period")
            return

        if self.is_connecting:
            logger.info("⏳ Voice connection already in progress")
            return

        self.is_connecting = True

        try:
            channel = self.bot.get_channel(self.voice_channel_id)
            if not channel:
                logger.error(f"❌ Voice channel {self.voice_channel_id} not found")
                self.failure_count += 1
                return

            guild = channel.guild
            voice = guild.voice_client

            # Check existing connection
            if voice and voice.is_connected():
                if voice.channel.id == self.voice_channel_id:
                    logger.info(f"✅ Already connected to {channel.name}")
                    self.failure_count = 0  # Reset failures on successful connection
                    return
                else:
                    # Move to correct channel
                    logger.info(f"🔄 Moving to {channel.name}")
                    await voice.move_to(channel)
                    self.failure_count = 0
                    return

            # Clean up any stale connection
            if voice:
                await self.cleanup_voice(guild)
                await asyncio.sleep(1)

            # Connect with retries
            for attempt in range(self.max_attempts):
                try:
                    timeout = min(20 + (attempt * 10), 45)
                    logger.info(
                        f"🔌 Connecting to {channel.name} (attempt {attempt + 1}/{self.max_attempts})"
                    )

                    await channel.connect(
                        timeout=timeout, reconnect=True, self_deaf=True, self_mute=True
                    )

                    logger.info(f"✅ Connected to {channel.name}")
                    self.connection_attempts = 0
                    self.failure_count = 0  # Reset failures on success
                    return

                except Exception as e:
                    self.connection_attempts += 1
                    self.failure_count += 1
                    logger.error(f"❌ Attempt {attempt + 1} failed: {type(e).__name__}")

                    # Check if we need to enter cooldown
                    if self.failure_count >= self.max_failures_before_cooldown:
                        logger.warning(
                            f"⚠️ Failure count: {self.failure_count} - threshold reached"
                        )
                        await self.enter_cooldown()
                        return

                    if attempt < self.max_attempts - 1:
                        wait = (2**attempt) + random.uniform(1, 2)
                        logger.info(
                            f"⏳ Retrying in {wait:.1f}s (failures: {self.failure_count})"
                        )
                        await asyncio.sleep(wait)
                    else:
                        logger.error(
                            f"❌ Max attempts reached. Total failures: {self.failure_count}"
                        )

                        # Enter cooldown if we've failed too many times
                        if self.failure_count >= self.max_failures_before_cooldown:
                            await self.enter_cooldown()

        except Exception as e:
            logger.error(f"❌ Voice error: {e}")
            self.failure_count += 1

            # Check cooldown on unexpected errors
            if self.failure_count >= self.max_failures_before_cooldown:
                await self.enter_cooldown()
        finally:
            self.is_connecting = False


async def setup(bot):
    await bot.add_cog(AutoJoinVoice(bot))
