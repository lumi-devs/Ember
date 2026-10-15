from __future__ import annotations

import asyncio
import logging
import os

import discord
import uvicorn

from core import EmberCog

log = logging.getLogger("ember.dashboard")

setup = None  # set at module bottom


class DashboardCog(EmberCog, name="Dashboard"):
    """Web-based admin dashboard with Discord OAuth2."""

    default_config: dict = {}  # no per-guild config needed for the cog itself

    def __init__(self, bot: discord.Client) -> None:
        super().__init__(bot)
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task | None = None

    async def cog_load(self) -> None:
        secret = os.environ.get("EMBER_DASHBOARD_SECRET", "")
        client_id = os.environ.get("DISCORD_OAUTH2_CLIENT_ID", "")
        client_secret = os.environ.get("DISCORD_OAUTH2_CLIENT_SECRET", "")
        redirect_uri = os.environ.get("DISCORD_OAUTH2_REDIRECT_URI", "")
        host = os.environ.get("EMBER_DASHBOARD_HOST", "0.0.0.0")
        port = int(os.environ.get("EMBER_DASHBOARD_PORT", "8080"))

        missing = [k for k, v in {
            "EMBER_DASHBOARD_SECRET": secret,
            "DISCORD_OAUTH2_CLIENT_ID": client_id,
            "DISCORD_OAUTH2_CLIENT_SECRET": client_secret,
            "DISCORD_OAUTH2_REDIRECT_URI": redirect_uri,
        }.items() if not v]
        if missing:
            log.error("Dashboard disabled — missing env vars: %s", ", ".join(missing))
            return

        from .server import app, init_app
        init_app(self.bot, secret, client_id, client_secret, redirect_uri)

        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level="warning",
            access_log=False,
        )
        self._server = uvicorn.Server(config)
        self._task = asyncio.get_event_loop().create_task(
            self._server.serve(), name="ember-dashboard"
        )
        log.info("Dashboard started on http://%s:%d", host, port)

    async def cog_unload(self) -> None:
        if self._server:
            self._server.should_exit = True
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("Dashboard stopped")


setup = DashboardCog.make_setup()
