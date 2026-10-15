from __future__ import annotations

import aiohttp

DISCORD_API = "https://discord.com/api/v10"
DISCORD_CDN = "https://cdn.discordapp.com"
_MANAGE_GUILD = 0x20


async def exchange_code(
    code: str, client_id: str, client_secret: str, redirect_uri: str
) -> dict:
    """Exchange an OAuth2 authorization code for tokens."""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{DISCORD_API}/oauth2/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "client_secret": client_secret,
            },
        ) as resp:
            resp.raise_for_status()
            return await resp.json()


async def get_user(access_token: str) -> dict:
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{DISCORD_API}/users/@me",
            headers={"Authorization": f"Bearer {access_token}"},
        ) as resp:
            resp.raise_for_status()
            return await resp.json()


async def get_user_guilds(access_token: str) -> list[dict]:
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{DISCORD_API}/users/@me/guilds",
            headers={"Authorization": f"Bearer {access_token}"},
        ) as resp:
            resp.raise_for_status()
            return await resp.json()


def user_avatar_url(user: dict) -> str:
    uid = user["id"]
    avatar = user.get("avatar")
    if avatar:
        fmt = "gif" if avatar.startswith("a_") else "png"
        return f"{DISCORD_CDN}/avatars/{uid}/{avatar}.{fmt}?size=64"
    discriminator = int(user.get("discriminator", 0) or 0)
    index = (int(uid) >> 22) % 6 if discriminator == 0 else discriminator % 5
    return f"{DISCORD_CDN}/embed/avatars/{index}.png"


def guild_icon_url(guild: dict) -> str | None:
    gid = guild["id"]
    icon = guild.get("icon")
    if not icon:
        return None
    fmt = "gif" if icon.startswith("a_") else "png"
    return f"{DISCORD_CDN}/icons/{gid}/{icon}.{fmt}?size=64"


def can_manage(guild: dict) -> bool:
    """True if the OAuth2 guild entry shows MANAGE_GUILD permission."""
    return bool(int(guild.get("permissions", 0)) & _MANAGE_GUILD)
