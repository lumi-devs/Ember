"""Webhook-based message relay between session channels.

Handles: text, attachments (files/images/video), stickers, and reply embeds.
Reply embeds mimic Discord's native "Replying to X" look using an embed as the
first embed in the webhook message.
"""

import logging
import random
import re
import time
from collections import OrderedDict, defaultdict, deque
from pathlib import Path

import aiohttp
import discord

from .constants import (
    # GUILD_ID removed — guild_id derived from message context
    MESSAGE_RATE_LIMIT,
    MESSAGE_RATE_WINDOW,
    PING_COOLDOWN_SECONDS,
    PING_MAX_PER_WINDOW,
    PROFANITY_WORDS,
    PROMO_TIPS,
    STAT_DROP_TEMPLATES,
)

log = logging.getLogger("ember.shadowchat.relay")

# Light-blue used for reply embeds — same as Discord's native reply bar colour.
REPLY_EMBED_COLOR = 0x87CEEB
STICKER_EMBED_COLOR = 0xFF85C0

# Matches a standalone `@` — at-sign not surrounded by non-whitespace on either side.
# Examples that match: "@ hi", "hey @", "@ "  (standalone ping)
# Examples that don't: "@user", "@everyone"  (not standalone)
_STANDALONE_AT_RE = re.compile(r"(?<!\S)@(?!\S)")

# Regex that matches a message whose *entire* content is one or more media URLs
# (Tenor, Giphy, direct image/gif/video, or generic CDN links that end in a
# known media extension).  When a message matches this we relay the URL(s) as
# plain content first so Discord can render the preview, then send the reply
# embed in a separate follow-up webhook call.
_MEDIA_URL_RE = re.compile(r"^https?://\S+$")  # single token starting with http(s)://
_MEDIA_HOSTNAME_RE = re.compile(
    r"^https?://(tenor\.com|media\.tenor\.com|giphy\.com|media\.giphy\.com|"
    r"media\d*\.giphy\.com|i\.imgur\.com|imgur\.com|"
    r"cdn\.discordapp\.com|media\.discordapp\.net|"
    r".*\.(gif|gifv|mp4|webm|mov|jpg|jpeg|png|webp))(/|$)",
    re.IGNORECASE,
)
_MEDIA_EXT_RE = re.compile(
    r"https?://\S+\.(gif|gifv|mp4|webm|mov|jpg|jpeg|png|webp)(\?[^\s]*)?$",
    re.IGNORECASE,
)


def _is_media_only(content: str) -> bool:
    """Return True if the message content is nothing but one media URL."""
    stripped = content.strip()
    if not _MEDIA_URL_RE.match(stripped):
        return False
    return bool(_MEDIA_HOSTNAME_RE.search(stripped) or _MEDIA_EXT_RE.search(stripped))


# Allowed attachment extensions (block executables, scripts, etc.)
ALLOWED_ATTACHMENT_EXTS = {
    # Images
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".bmp",
    ".svg",
    # Videos (Discord supported)
    ".mp4",
    ".mov",
    ".webm",
    ".avi",
    # Documents
    ".pdf",
    ".txt",
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
}


class RelayHandler:
    def __init__(self, db):
        self.db = db
        # user_id → deque of message timestamps within the current window
        self._rate_buckets: dict[int, deque[float]] = defaultdict(deque)
        self._http: aiohttp.ClientSession | None = None
        # Bounded LRU cache for reply embeds: msg_id → {"codename", "content"}
        # Capped at 2000 entries; oldest evicted on overflow.
        self._reply_cache: OrderedDict[int, dict] = OrderedDict()
        self._reply_cache_max = 2000

        # Mid-session promo tip: session_ids that have already received a tip
        self._prompted_sessions: set[str] = set()
        # Mid-session stat drop: session_ids that have already received a stat drop
        self._stats_dropped: set[str] = set()
        # @-ping rate limiting: user_id → deque of ping timestamps
        self._ping_buckets: dict[int, deque[float]] = defaultdict(deque)

    def _cache_put(self, key: int, value: dict):
        """Insert into reply cache, evicting the oldest entry if over cap."""
        if key in self._reply_cache:
            self._reply_cache.move_to_end(key)
        self._reply_cache[key] = value
        while len(self._reply_cache) > self._reply_cache_max:
            self._reply_cache.popitem(last=False)

    async def start(self):
        self._http = aiohttp.ClientSession()

    async def stop(self):
        if self._http and not self._http.closed:
            await self._http.close()

    # ── Content filtering ─────────────────────────────────────────────────────

    @staticmethod
    def _check_content(content: str) -> str | None:
        """
        Check message content for profanity/violations.
        Returns the violation word if found, None if content is OK.
        """
        lower_content = f" {content.lower()} "
        for word in PROFANITY_WORDS:
            if f" {word} " in lower_content:
                return word
        return None

    # ── Public entry point ────────────────────────────────────────────────────

    async def relay(self, message: discord.Message, session: dict) -> bool:
        """
        Relay *message* to the partner's channel via webhook.
        Returns True on success, False if rate-limited or error.
        """
        sender_id = message.author.id

        if not self._check_rate(sender_id):
            try:
                await message.channel.send(
                    f"⚠️ Slow down! You can send at most {MESSAGE_RATE_LIMIT} messages "
                    f"per {MESSAGE_RATE_WINDOW} seconds.",
                    delete_after=5,
                )
            except discord.HTTPException:
                pass
            return False

        # Check for profanity/content violations
        if message.content:
            violation = self._check_content(message.content)
            if violation:
                try:
                    await message.channel.send(
                        "⚠️ Your message contained inappropriate content and was not sent.",
                        delete_after=10,
                    )
                    # Log the violation
                    log.warning(f"Profanity violation by user {sender_id}: {violation}")
                except discord.HTTPException:
                    pass
                # Don't relay the message
                return False

        is_ch1 = message.channel.id == session["channel1_id"]
        if is_ch1:
            webhook_url = session["webhook2_url"]
            codename = session["codename1"]
            partner_channel_id = session["channel2_id"]
            partner_user_id = session["user2_id"]
        else:
            webhook_url = session["webhook1_url"]
            codename = session["codename2"]
            partner_channel_id = session["channel1_id"]
            partner_user_id = session["user1_id"]

        # ── Reply component ───────────────────────────────────────────────────────
        reply_component: discord.ui.Container | None = None
        if message.reference and message.reference.message_id:
            ref_msg_id = message.reference.message_id
            jump_url = f"https://discord.com/channels/{message.guild.id if message.guild else 0}/{message.channel.id}/{ref_msg_id}"
            reply_component = await self._build_reply_component(
                ref_msg_id,
                message.channel.id,
                session,
                jump_url=jump_url,
            )

        # ── Collect files with validation ─────────────────────────────────────
        files: list[discord.File] = []
        att_urls: list[str] = []
        for att in message.attachments:
            # Block disallowed file types
            ext = Path(att.filename).suffix.lower() if "." in att.filename else ""
            if ext and ext not in ALLOWED_ATTACHMENT_EXTS:
                try:
                    await message.channel.send(
                        f"⚠️ File type `. {ext}` is not allowed.",
                        delete_after=5,
                    )
                except discord.HTTPException:
                    pass
                continue
            att_urls.append(att.url)
            try:
                files.append(await att.to_file())
            except Exception:
                log.warning(f"Could not download attachment {att.url}")

        # ── Sticker components ────────────────────────────────────────────────────
        sticker_components: list[discord.ui.Container] = []
        for sticker in message.stickers:
            comp = self._make_sticker_component(sticker)
            sticker_components.append(comp)

        # ── Build view components: reply first, then stickers ──────────────────────
        view = discord.ui.LayoutView(timeout=None)
        if reply_component:
            view.add_item(reply_component)
        for comp in sticker_components:
            view.add_item(comp)

        # If no components, view will have no children; treat as None
        view_to_send = view if view.children else None

        # ── @ ping injection ──────────────────────────────────────────────────
        # Standalone `@` (e.g. "@ hi", "hey @") → real Discord mention of partner.
        raw_content = message.content or ""
        if _STANDALONE_AT_RE.search(raw_content):
            if self._check_ping_rate(sender_id):
                # Replace every standalone @ with the partner mention
                raw_content = _STANDALONE_AT_RE.sub(f"<@{partner_user_id}>", raw_content)
            else:
                # Rate limited — strip the @ silently, message still relays
                raw_content = _STANDALONE_AT_RE.sub("", raw_content).strip()

        # Guard: webhook requires at least one of content/file/component
        content = raw_content or None
        has_payload = bool(content or files or view_to_send)
        if not has_payload:
            # Pure sticker with no image URL (animated/Lottie) — send a text fallback
            if message.stickers:
                content = f"*sent a sticker: {message.stickers[0].name}*"
            else:
                return False  # nothing to relay

        # ── Split media-URL-only messages ─────────────────────────────────────
        # If the content is purely a media URL (GIF, Tenor, Giphy, image …) and
        # there are also components (e.g. a reply component), Discord won't render the
        # media preview when components are present.  Send the URL as a standalone
        # message first so the preview renders, then send the components separately.
        if content and _is_media_only(content) and view_to_send and not files:
            await self._send_webhook(
                webhook_url=webhook_url,
                username=codename,
                content=content,
                files=[],
                # No components in this first message
            )
            content = None  # components go in the second call below

        # Allow user mention to fire if content contains an injected @-ping
        has_ping = content and f"<@{partner_user_id}>" in content
        allowed_mentions = (
            discord.AllowedMentions(users=True) if has_ping else discord.AllowedMentions.none()
        )

        sent_id = await self._send_webhook(
            webhook_url=webhook_url,
            username=codename,
            content=content,
            files=files,
            view=view_to_send,
            allowed_mentions=allowed_mentions,
        )

        # ── Store message mapping for future reply embeds ─────────────────────
        if sent_id:
            # Enrich preview so reply embeds work for pure-image / pure-sticker messages
            preview = message.content or ""
            if not preview:
                if message.attachments:
                    preview = f"*{len(message.attachments)} attachment(s)*"
                elif message.stickers:
                    preview = f"*sticker: {message.stickers[0].name}*"
            preview = preview[:300]
            await self.db.msg_map_store(
                session["session_id"],
                src_channel_id=message.channel.id,
                src_msg_id=message.id,
                dst_channel_id=partner_channel_id,
                dst_msg_id=sent_id,
                content=preview,
                codename=codename,
            )
            # Warm the bounded cache for both directions.
            self._cache_put(message.id, {"codename": codename, "content": preview})
            self._cache_put(sent_id, {"codename": codename, "content": preview})

        # ── Log to DB ─────────────────────────────────────────────────────────
        await self.db.message_log(
            session["session_id"],
            sender_id,
            message.content,
            att_urls,
        )
        await self.db.stats_add_message(sender_id)

        # ── Mid-session promo tip (10% chance, once per session) ──────────────
        session_id = session["session_id"]
        if session_id not in self._prompted_sessions and random.random() < 0.10:
            self._prompted_sessions.add(session_id)
            tip = random.choice(PROMO_TIPS)
            from .views import _text_view

            try:
                await message.channel.send(
                    view=_text_view(f"-# {tip}", accent="#5865F2"),
                )
            except discord.HTTPException:
                pass

        # ── Random live stats drop (8% chance, once per session, after msg 5) ─
        if session_id not in self._stats_dropped:
            msg_count = await self.db.messages_count_session(sender_id, session_id)
            if msg_count >= 5 and random.random() < 0.08:
                self._stats_dropped.add(session_id)
                stat_text = await self._build_stat_drop()
                if stat_text:
                    from .views import _text_view

                    try:
                        await message.channel.send(
                            view=_text_view(f"-# {stat_text}", accent="#4f545c"),
                        )
                    except discord.HTTPException:
                        pass

        return sent_id is not None

    # ── Reply embed builder ───────────────────────────────────────────────────

    async def _build_reply_component(
        self,
        ref_msg_id: int,
        current_channel_id: int,
        session: dict,
        jump_url: str | None = None,
    ) -> discord.ui.Container | None:
        """
        Build a "↩ Replying to {codename}" component for a message reference.
        Looks up the mapping DB to find whose codename + what content to show.
        """
        # In-memory cache hit
        cached = self._reply_cache.get(ref_msg_id)
        if cached:
            return self._make_reply_component(cached["codename"], cached["content"], jump_url)

        # DB lookup
        row = await self.db.msg_map_lookup(
            session["session_id"],
            current_channel_id,
            ref_msg_id,
        )
        if row:
            self._cache_put(ref_msg_id, row)
            return self._make_reply_component(row["codename"], row["content"], jump_url)

        # Fallback: fetch the message from Discord directly (e.g. very first reply
        # before any mapping exists, or after a bot restart)
        channel = None
        if hasattr(self, "_bot"):
            channel = self._bot.get_channel(current_channel_id)
        if channel:
            try:
                ref_msg = await channel.fetch_message(ref_msg_id)
                codename = ref_msg.author.display_name if ref_msg.author else "?"
                if ref_msg.content:
                    content = ref_msg.content
                elif ref_msg.attachments:
                    exts = {
                        a.filename.rsplit(".", 1)[-1].lower()
                        for a in ref_msg.attachments
                        if "." in a.filename
                    }
                    img_exts = {"jpg", "jpeg", "png", "gif", "webp", "mp4", "mov", "webm"}
                    if exts & img_exts:
                        content = f"*{len(ref_msg.attachments)} image/video attachment(s)*"
                    else:
                        content = f"*{len(ref_msg.attachments)} attachment(s)*"
                elif ref_msg.stickers:
                    content = f"*sticker: {ref_msg.stickers[0].name}*"
                else:
                    content = "*no text*"
                return self._make_reply_component(codename, content, jump_url)
            except discord.HTTPException:
                pass

        return self._make_reply_component(None, "*Replying to a message*", jump_url)

    @staticmethod
    def _make_reply_component(
        codename: str | None, content: str, jump_url: str | None = None
    ) -> discord.ui.Container:
        preview = content[:150] + ("…" if len(content) > 150 else "")
        children = []
        # Title line
        if codename:
            if jump_url:
                children.append(discord.ui.TextDisplay(f"[↩ Replying to {codename}]({jump_url})"))
            else:
                children.append(discord.ui.TextDisplay(f"**↩ Replying to {codename}**"))
        else:
            children.append(discord.ui.TextDisplay("**↩ Replying to a message**"))
        # Preview
        if preview and preview != "*no text*":
            children.append(discord.ui.TextDisplay(preview))
        else:
            children.append(discord.ui.TextDisplay("*no text*"))
        # If there's a jump_url and we didn't use it in title, add a small link
        if jump_url and not codename:
            children.append(discord.ui.TextDisplay(f"-[Jump]({jump_url})"))
        return discord.ui.Container(*children, accent_color=discord.Color(REPLY_EMBED_COLOR))

    def _make_sticker_component(self, sticker) -> discord.ui.Container:
        """Create a component for a sticker using MediaGallery and footer."""
        try:
            media = discord.Media(url=str(sticker.url))
            gallery = discord.ui.MediaGallery(media)
        except Exception:
            # Fallback: just use text with link
            children = [
                discord.ui.TextDisplay(f"🩷 Sticker: {sticker.name or 'unknown'}\n{sticker.url}")
            ]
            return discord.ui.Container(*children, accent_color=discord.Color(STICKER_EMBED_COLOR))
        children = [gallery]  # type: ignore
        if sticker.name:
            children.append(discord.ui.TextDisplay(f"🩷 {sticker.name}"))
        return discord.ui.Container(*children, accent_color=discord.Color(STICKER_EMBED_COLOR))  # type: ignore

    # ── Reaction relay ─────────────────────────────────────────────────────────

    async def send_reaction(
        self,
        webhook_url: str,
        codename: str,
        emoji_str: str,
        msg_url: str | None = None,
        msg_content: str | None = None,
    ):
        """Send a reaction notification to the partner's channel via webhook."""
        children = []
        # Title line
        if msg_url:
            children.append(discord.ui.TextDisplay(f"[reacted {emoji_str}]({msg_url})"))
        else:
            children.append(discord.ui.TextDisplay(f"**reacted {emoji_str}**"))
        if msg_content:
            preview = msg_content[:150] + ("…" if len(msg_content) > 150 else "")
            children.append(discord.ui.TextDisplay(preview))
        container = discord.ui.Container(*children, accent_color=discord.Color(REPLY_EMBED_COLOR))
        view = discord.ui.LayoutView(timeout=None)
        view.add_item(container)

        await self._send_webhook(
            webhook_url=webhook_url,
            username=codename,
            content="",
            files=[],
            view=view,
        )

    # ── Ping rate limiting ────────────────────────────────────────────────────

    def _check_ping_rate(self, user_id: int) -> bool:
        """Returns True if user may fire an @-ping (within PING_MAX_PER_WINDOW)."""
        now = time.monotonic()
        bucket = self._ping_buckets[user_id]
        cutoff = now - PING_COOLDOWN_SECONDS
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= PING_MAX_PER_WINDOW:
            return False
        bucket.append(now)
        return True

    # ── Stat drop builder ─────────────────────────────────────────────────────

    async def _build_stat_drop(self) -> str | None:
        """Pick a random stat template and fill it with live data."""
        try:
            key, template = random.choice(STAT_DROP_TEMPLATES)
            stats = await self.db.global_stats()

            if key == "recent_hour":
                x = await self.db.recent_sessions_count(3600)
                return template.format(x=x)
            elif key == "active_now":
                return template.format(x=stats.get("users_chatting", 0))
            elif key == "total_sessions":
                return template.format(total=stats.get("total_sessions", 0))
            elif key == "total_hours":
                # total_seconds is stored in sc_stats — sum it up
                total_secs = await self._total_chat_seconds()
                return template.format(hours=max(1, total_secs // 3600))
            elif key == "today":
                x = await self.db.recent_sessions_count(86400)
                return template.format(x=x)
            elif key == "total_users":
                return template.format(users=stats.get("total_users", 0))
            elif key == "queue_now":
                return template.format(queue=stats.get("queue_size", 0))
        except Exception as e:
            log.debug(f"Stat drop build error: {e}")
        return None

    async def _total_chat_seconds(self) -> int:
        """Sum total_seconds across all users in sc_stats."""
        try:
            # Use the database manager to get the sum
            result = await self.db._execute_fetchone_with_lock(
                "SELECT SUM(total_seconds) FROM sc_stats"
            )
            return int(result[0]) if result and result[0] else 0
        except Exception:
            return 0

    # ── Rate limiting ─────────────────────────────────────────────────────────

    def _check_rate(self, user_id: int) -> bool:
        now = time.monotonic()
        bucket = self._rate_buckets[user_id]
        cutoff = now - MESSAGE_RATE_WINDOW

        while bucket and bucket[0] < cutoff:
            bucket.popleft()

        if len(bucket) >= MESSAGE_RATE_LIMIT:
            return False

        bucket.append(now)
        return True

    # ── Webhook dispatch ──────────────────────────────────────────────────────

    async def _send_webhook(
        self,
        webhook_url: str,
        username: str,
        content: str | None,
        files: list[discord.File],
        *,
        view: discord.ui.LayoutView | None = None,
        avatar_url: str | None = None,
        allowed_mentions: discord.AllowedMentions | None = None,
    ) -> int | None:
        """Send via webhook and return the sent message ID, or None on failure."""
        if not self._http or self._http.closed:
            self._http = aiohttp.ClientSession()

        try:
            webhook = discord.Webhook.from_url(webhook_url, session=self._http)

            kwargs: dict = {
                "username": username,
                "allowed_mentions": allowed_mentions or discord.AllowedMentions.none(),
                "wait": True,
            }
            if content and content.strip():
                kwargs["content"] = content
            if files:
                kwargs["files"] = files
            if view:
                kwargs["view"] = view
            if avatar_url:
                kwargs["avatar_url"] = avatar_url

            sent = await webhook.send(**kwargs)
            return sent.id

        except discord.NotFound:
            log.warning(f"Webhook not found: {webhook_url[:40]}...")
        except discord.HTTPException as e:
            log.error(f"Webhook send failed: {e}")
        except Exception as e:
            log.error(f"Unexpected relay error: {e}", exc_info=True)

        return None
