"""DuckDNS IPv6 auto-update cog — runs once on bot ready."""

from __future__ import annotations

import logging
import os

import aiohttp
from discord.ext import commands

from core import EmberCog, Translator, cog_i18n

_ = Translator("duckdns", __file__)

log = logging.getLogger("ember.duckdns")

DOMAIN = os.getenv("DUCKDNS_DOMAIN", "")
TOKEN = os.getenv("DUCKDNS_TOKEN", "")
_DUCKDNS_URL = "https://www.duckdns.org/update"
_IPV6_URL = "https://api6.ipify.org"


@cog_i18n(_)
class DuckDNSCog(EmberCog, name="DuckDNS"):
    """Auto-updates DuckDNS with the current IPv6 address on startup."""

    @commands.Cog.listener()
    async def on_ready(self):
        if not DOMAIN or not TOKEN:
            log.warning("DUCKDNS_DOMAIN or DUCKDNS_TOKEN not set — skipping update")
            return
        await self._update()

    async def _update(self):
        try:
            async with aiohttp.ClientSession() as session:
                # Get current IPv6
                async with session.get(_IPV6_URL, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    ipv6 = (await r.text()).strip()

                # Push to DuckDNS
                params = {"domains": DOMAIN, "token": TOKEN, "ipv6": ipv6, "verbose": "true"}
                async with session.get(
                    _DUCKDNS_URL, params=params, timeout=aiohttp.ClientTimeout(total=10)
                ) as r:
                    body = (await r.text()).strip()

            if body.startswith("OK"):
                changed = "UPDATED" in body
                if changed:
                    log.info(f"DuckDNS updated: {DOMAIN}.duckdns.org → {ipv6}")
                else:
                    log.info(f"DuckDNS unchanged: {DOMAIN}.duckdns.org ({ipv6})")
            else:
                log.error(f"DuckDNS update failed — response: {body}")

        except Exception as e:
            log.error(f"DuckDNS update error: {e}")


setup = DuckDNSCog.make_setup()
