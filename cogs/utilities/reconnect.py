# cogs/utilities/autorestart.py
import discord
from discord.ext import commands
import asyncio
import subprocess
import logging

log = logging.getLogger("BotLogger")

class AutoRestart(commands.Cog):
    """Automatically restarts the bot service on fatal disconnection."""

    def __init__(self, bot):
        self.bot = bot
        self.service_name = "discord-bot.service"

    @commands.Cog.listener()
    async def on_disconnect(self):
        log.warning("⚠️  Bot disconnected from Discord. Waiting to see if it reconnects...")

    @commands.Cog.listener()
    async def on_resumed(self):
        log.info("✅ Session resumed — no restart needed.")

    @commands.Cog.listener()
    async def on_connect(self):
        log.info("✅ Connected to Discord.")

    async def cog_load(self):
        self.bot.loop.set_exception_handler(self._handle_exception)

    def _handle_exception(self, loop, context):
        exception = context.get("exception")
        msg = context.get("message", "")

        if exception:
            log.error(f"💥 Unhandled exception: {exception}")
        else:
            log.error(f"💥 Loop error: {msg}")

        if self._is_fatal_connection_error(exception or msg):
            log.warning("🔄 Fatal connection error detected — triggering restart...")
            asyncio.ensure_future(self._restart())

    def _is_fatal_connection_error(self, exc):
        fatal_indicators = [
            "Cannot connect to host",
            "name resolution",
            "ClientConnectorError",
            "ConnectionResetError",
        ]
        exc_str = str(exc)
        return any(indicator.lower() in exc_str.lower() for indicator in fatal_indicators)

    async def _restart(self):
        """Waits briefly then restarts the systemd service."""
        await asyncio.sleep(3)
        log.info(f"🔄 Restarting service: {self.service_name}")
        try:
            subprocess.Popen(
                ["sudo", "/usr/bin/systemctl", "restart", self.service_name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            log.error(f"❌ Failed to trigger restart: {e}")

    # --- Manual restart command (owner only) ---
    @commands.command(name="restart", hidden=True)
    @commands.is_owner()
    async def restart_cmd(self, ctx):
        """Manually restarts the bot service."""
        await ctx.send("🔄 Restarting bot service...")
        log.info(f"Manual restart triggered by {ctx.author}")
        await asyncio.sleep(1)
        await self._restart()


async def setup(bot):
    await bot.add_cog(AutoRestart(bot))
