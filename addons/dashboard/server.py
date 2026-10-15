from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import discord
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeTimedSerializer

from . import oauth

_HERE = Path(__file__).parent
app = FastAPI(docs_url=None, redoc_url=None, title="Ember Dashboard")
app.mount("/static", StaticFiles(directory=_HERE / "static"), name="static")
_templates = Jinja2Templates(directory=_HERE / "templates")

# Populated by init_app() before uvicorn starts
_bot: discord.Client | None = None
_secret: str = ""
_client_id: str = ""
_client_secret: str = ""
_redirect_uri: str = ""

_SESSION_MAX_AGE = 7 * 24 * 3600  # 7 days
_SESSION_COOKIE = "ember_session"


def init_app(
    bot: discord.Client,
    secret: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> None:
    global _bot, _secret, _client_id, _client_secret, _redirect_uri
    _bot = bot
    _secret = secret
    _client_id = client_id
    _client_secret = client_secret
    _redirect_uri = redirect_uri


# ── session helpers ───────────────────────────────────────────────────────────

def _signer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_secret, salt="ember-dashboard")


def _write_session(response: Response, data: dict) -> None:
    token = _signer().dumps(data)
    response.set_cookie(
        _SESSION_COOKIE, token,
        max_age=_SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
    )


def _read_session(request: Request) -> dict | None:
    raw = request.cookies.get(_SESSION_COOKIE)
    if not raw:
        return None
    try:
        return _signer().loads(raw, max_age=_SESSION_MAX_AGE)
    except BadSignature:
        return None


def _require_session(request: Request) -> dict:
    session = _read_session(request)
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return session


# ── feature registry helpers ──────────────────────────────────────────────────

def _feature_list_json() -> list[dict]:
    try:
        from utils.feature_registry import all_features
    except ImportError:
        return []
    result = []
    for f in all_features():
        result.append({
            "cog_name": f.cog_name,
            "display_name": f.display_name,
            "emoji": f.emoji,
            "description": f.description,
            "fields": [
                {
                    "key": cf.key,
                    "label": cf.label,
                    "type": cf.type.name,
                    "description": cf.description,
                    "required": cf.required,
                    "default": cf.default,
                    "placeholder": cf.placeholder,
                    "max_length": cf.max_length,
                    "min_value": cf.min_value,
                    "max_value": cf.max_value,
                }
                for cf in f.fields
            ],
        })
    return result


async def _read_cog_config(cog_name: str, guild_id: int) -> dict:
    try:
        from core.store import get_cog_store
    except ImportError:
        return {}
    store = get_cog_store(cog_name)
    if store is None:
        return {}
    return await store.guild(guild_id).all() or {}


async def _write_cog_config(cog_name: str, guild_id: int, patch: dict) -> None:
    try:
        from core.store import get_cog_store
    except ImportError:
        return
    store = get_cog_store(cog_name)
    if store is None:
        raise HTTPException(404, f"Cog '{cog_name}' not loaded")
    await store.guild(guild_id).update(**patch)


# ── mutual guild helpers ──────────────────────────────────────────────────────

async def _mutual_guilds(access_token: str) -> list[dict]:
    """Guilds where the user has MANAGE_GUILD and the bot is present."""
    assert _bot is not None
    user_guilds = await oauth.get_user_guilds(access_token)
    bot_guild_ids = {g.id for g in _bot.guilds}
    result = []
    for g in user_guilds:
        if not oauth.can_manage(g):
            continue
        gid = int(g["id"])
        if gid not in bot_guild_ids:
            continue
        bot_guild = _bot.get_guild(gid)
        result.append({
            "id": str(gid),
            "name": g["name"],
            "icon": oauth.guild_icon_url(g),
            "member_count": getattr(bot_guild, "member_count", None),
        })
    return result


# ── page routes ───────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    session = _read_session(request)
    if session:
        return RedirectResponse("/guilds")  # type: ignore[return-value]
    return _templates.TemplateResponse("login.html", {"request": request})


@app.get("/login")
async def login() -> RedirectResponse:
    params = urlencode({
        "client_id": _client_id,
        "redirect_uri": _redirect_uri,
        "response_type": "code",
        "scope": "identify guilds",
    })
    return RedirectResponse(f"https://discord.com/oauth2/authorize?{params}")


@app.get("/callback")
async def callback(request: Request, code: str | None = None, error: str | None = None) -> Response:
    if error or not code:
        return RedirectResponse("/?error=denied")
    try:
        tokens = await oauth.exchange_code(code, _client_id, _client_secret, _redirect_uri)
        user = await oauth.get_user(tokens["access_token"])
    except Exception:
        return RedirectResponse("/?error=oauth_failed")

    session_data = {
        "user_id": user["id"],
        "username": user["username"],
        "avatar": oauth.user_avatar_url(user),
        "access_token": tokens["access_token"],
    }
    response = RedirectResponse("/guilds", status_code=302)
    _write_session(response, session_data)
    return response


@app.get("/logout")
async def logout() -> Response:
    response = RedirectResponse("/")
    response.delete_cookie(_SESSION_COOKIE)
    return response


@app.get("/guilds", response_class=HTMLResponse)
async def guilds_page(request: Request) -> HTMLResponse:
    session = _read_session(request)
    if not session:
        return RedirectResponse("/")  # type: ignore[return-value]
    return _templates.TemplateResponse("guilds.html", {
        "request": request,
        "user": session,
    })


@app.get("/guilds/{guild_id}", response_class=HTMLResponse)
async def guild_page(request: Request, guild_id: str) -> HTMLResponse:
    session = _read_session(request)
    if not session:
        return RedirectResponse("/")  # type: ignore[return-value]
    assert _bot is not None
    guild = _bot.get_guild(int(guild_id))
    if guild is None:
        raise HTTPException(404, "Bot is not in that server")
    return _templates.TemplateResponse("guild.html", {
        "request": request,
        "user": session,
        "guild_id": guild_id,
        "guild_name": guild.name,
        "guild_icon": (
            str(guild.icon.url) if guild.icon else None
        ),
    })


# ── API routes ────────────────────────────────────────────────────────────────

@app.get("/api/me")
async def api_me(request: Request) -> JSONResponse:
    session = _require_session(request)
    return JSONResponse({"user_id": session["user_id"], "username": session["username"], "avatar": session["avatar"]})


@app.get("/api/guilds")
async def api_guilds(request: Request) -> JSONResponse:
    session = _require_session(request)
    try:
        guilds = await _mutual_guilds(session["access_token"])
    except Exception as e:
        raise HTTPException(502, f"Discord API error: {e}") from e
    return JSONResponse(guilds)


@app.get("/api/guilds/{guild_id}/channels")
async def api_channels(request: Request, guild_id: str) -> JSONResponse:
    _require_session(request)
    assert _bot is not None
    guild = _bot.get_guild(int(guild_id))
    if guild is None:
        raise HTTPException(404, "Guild not found")
    channels = [
        {"id": str(c.id), "name": c.name, "type": c.type.name}
        for c in sorted(guild.channels, key=lambda c: c.position)
        if isinstance(c, (discord.TextChannel, discord.ForumChannel, discord.VoiceChannel, discord.CategoryChannel))
    ]
    return JSONResponse(channels)


@app.get("/api/guilds/{guild_id}/roles")
async def api_roles(request: Request, guild_id: str) -> JSONResponse:
    _require_session(request)
    assert _bot is not None
    guild = _bot.get_guild(int(guild_id))
    if guild is None:
        raise HTTPException(404, "Guild not found")
    roles = [
        {"id": str(r.id), "name": r.name, "color": str(r.color)}
        for r in sorted(guild.roles, key=lambda r: -r.position)
        if not r.is_default()
    ]
    return JSONResponse(roles)


@app.get("/api/features")
async def api_features(request: Request) -> JSONResponse:
    _require_session(request)
    return JSONResponse(_feature_list_json())


@app.get("/api/guilds/{guild_id}/config/{cog_name}")
async def api_get_config(request: Request, guild_id: str, cog_name: str) -> JSONResponse:
    _require_session(request)
    config = await _read_cog_config(cog_name, int(guild_id))
    return JSONResponse(config)


@app.patch("/api/guilds/{guild_id}/config/{cog_name}")
async def api_patch_config(request: Request, guild_id: str, cog_name: str) -> JSONResponse:
    _require_session(request)
    body: dict[str, Any] = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "Body must be a JSON object")
    await _write_cog_config(cog_name, int(guild_id), body)
    return JSONResponse({"ok": True})
