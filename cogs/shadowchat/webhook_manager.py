"""
SHADOW CHAT - WEBHOOK MANAGER (OPTIMIZED - NO EDIT METHODS)
USES AVATAR/NAME PARAMETERS IN SEND METHOD
SMART AVATAR CACHING WITH DISCORD CDN
FIXED REPLY EMBEDS
"""

import asyncio
import secrets
import discord
import io
import aiohttp
from typing import Optional, Dict, Tuple, List, Any
from . import constants
import os
import logging
from .models import Database
import time
from datetime import datetime, timedelta
import hashlib
import json

# Set up logging
logger = logging.getLogger(__name__)


class AvatarCacheEntry:
    """Represents a cached avatar with expiration."""

    def __init__(self, filename: str, cdn_url: str, message_id: int, channel_id: int):
        self.filename = filename
        self.cdn_url = cdn_url
        self.message_id = message_id
        self.channel_id = channel_id
        self.uploaded_at = datetime.now()
        self.last_used = datetime.now()
        self.use_count = 0

    def is_expired(self, max_age_hours: int = 6) -> bool:
        """Check if cache entry is expired."""
        age = datetime.now() - self.uploaded_at
        return age.total_seconds() > (max_age_hours * 3600)

    def should_refresh(self, max_age_hours: int = 4) -> bool:
        """Check if cache entry should be refreshed soon."""
        age = datetime.now() - self.uploaded_at
        return age.total_seconds() > (max_age_hours * 3600)

    def mark_used(self):
        """Mark this avatar as used."""
        self.last_used = datetime.now()
        self.use_count += 1


class IdentityState:
    """Represents a webhook's identity state."""

    def __init__(self):
        self.is_anonymous = True
        self.display_name = "Shadow"
        self.avatar_url = None
        self.user: Optional[discord.User] = None
        self.last_updated = time.time()


class ManagedWebhook:
    """Represents a managed webhook with identity state."""

    def __init__(self, url: str, webhook: discord.Webhook, manager=None):
        self.url = url
        self.webhook = webhook
        self.manager = manager  # Reference to WebhookManager for cache updates
        self.identity = IdentityState()
        self.assigned_session_id: Optional[str] = None
        self.is_valid = True  # Track if webhook is still valid
        self.last_used: Optional[datetime] = None

    def get_avatar_url(self) -> Optional[str]:
        """Get avatar URL from identity state."""
        if (
            not self.identity.is_anonymous
            and self.identity.user
            and self.identity.user.avatar
        ):
            # Use user's avatar URL
            return str(self.identity.user.avatar.url)
        elif self.identity.avatar_url:
            # Use cached anonymous avatar URL
            return self.identity.avatar_url
        else:
            # Fallback to Discord's default avatar
            return "https://cdn.discordapp.com/embed/avatars/0.png"

    def get_display_name(self) -> str:
        """Get display name from identity state."""
        return self.identity.display_name


class WebhookManager:
    """Manages webhooks using provided static URLs with smart avatar caching."""

    def __init__(self, bot, db: Database):
        self.bot = bot
        self.db: Optional[Database] = db
        self.session: Optional[aiohttp.ClientSession] = None

        # Configuration: Add your webhook URLs here
        self.provided_webhook_urls = constants.WEBHOOK_URLS

        self.managed_webhooks: Dict[str, ManagedWebhook] = {}
        self.available_webhooks: List[ManagedWebhook] = []
        self.session_webhooks: Dict[str, Tuple[ManagedWebhook, ManagedWebhook]] = {}
        self.thread_sessions: Dict[int, str] = {}

        # Smart avatar caching system
        self._avatar_cache: Dict[str, AvatarCacheEntry] = (
            {}
        )  # filename_hash -> cache entry
        self._avatar_file_cache: Dict[str, bytes] = {}  # filename -> file bytes
        self._avatar_files: List[str] = []  # List of available avatar filenames
        self._user_avatar_cache: Dict[str, str] = {}  # user_id -> avatar_url

        # Reply cache - IMPORTANT: Cache the full reply embed data
        self._reply_message_cache: Dict[int, dict] = (
            {}
        )  # message_id -> webhook info cache

        # Cache persistence
        self._cache_file = "avatar_cache.json"
        self._cache_save_task = None

        self._avatar_cache_max_age_hours = 48  # Keep entries for 48h if verified
        self._max_cached_avatars = 200

        # New task tracker
        self._maintenance_task = None

        # Random display names for anonymous mode
        self._anonymous_names = [
            "Shadow",
            "Ghost",
            "Echo",
            "Spectre",
            "Phantom",
            "Wraith",
            "Mystery",
            "Unknown",
            "Stranger",
            "Incognito",
            "Anon",
            "Mask",
            "Shade",
            "Void",
            "Abyss",
            "Umbra",
            "Gloom",
            "Obsidian",
            "Midnight",
            "Eclipse",
            "Silhouette",
            "Noir",
            "Vanta",
            "Enigma",
            "Cipher",
            "Riddle",
            "Paradox",
            "Secret",
            "Whisper",
            "Veil",
            "Cloak",
            "Shroud",
            "Mirage",
            "Illusion",
            "Camouflage",
            "Mist",
            "Fog",
            "Haze",
            "Smoke",
            "Vapor",
            "Ash",
            "Dust",
            "Storm",
            "Frost",
            "Winter",
            "Rain",
            "Thunder",
            "Nomad",
            "Drifter",
            "Rogue",
            "Ronin",
            "Exile",
            "Outcast",
            "Traveler",
            "Watcher",
            "Observer",
            "Scout",
            "Agent",
            "Proxy",
            "Alias",
            "Glitch",
            "Signal",
            "Static",
            "Null",
            "Blank",
            "Zero",
            "Nobody",
            "System",
            "Chaos",
            "Entropy",
            "Raven",
            "Crow",
            "Wolf",
            "Viper",
            "Cobra",
            "Hawk",
            "Spirit",
            "Soul",
            "Wisp",
            "Banshee",
            "Ghoul",
            "Revenant",
        ]

        # Channel for uploading avatar images
        self._avatar_upload_channel_id = None

        # Fast cache for webhook info (no API calls needed)
        self._webhook_info_fast_cache: Dict[str, dict] = (
            {}
        )  # webhook_url -> {name, avatar_url, timestamp}

        self._lock = asyncio.Lock()

    async def initialize(self):
        """Initialize webhook manager and load/setup avatar caching."""
        logger.info("Initializing Webhook Manager...")

        # Create aiohttp session with connection pooling
        if not self.session or self.session.closed:
            connector = aiohttp.TCPConnector(limit_per_host=20, ttl_dns_cache=300)
            self.session = aiohttp.ClientSession(connector=connector)

        # Load existing cache
        await self._load_cache()

        # Load your custom avatars
        await self._load_local_avatars()

        # Setup avatar upload channel
        await self._ensure_avatar_channel()

        # Start cache maintenance in background
        self._cache_save_task = asyncio.create_task(self._periodic_cache_save())

        # NEW: Start the Avatar Maintenance Loop
        if self._maintenance_task is None:
            self._maintenance_task = asyncio.create_task(
                self._avatar_maintenance_loop()
            )
            logger.info("Started Avatar Maintenance Loop (10m check / 10h refresh)")

        if not self.provided_webhook_urls:
            logger.warning("No webhook URLs provided in constants.WEBHOOK_URLS")
            return

        loaded_count = 0
        for url in self.provided_webhook_urls:
            try:
                webhook = discord.Webhook.from_url(
                    url, session=self.session, bot_token=self.bot.http.token
                )
                # Verify webhook exists
                await webhook.fetch()

                managed = ManagedWebhook(url, webhook, self)
                self.managed_webhooks[url] = managed
                self.available_webhooks.append(managed)
                loaded_count += 1

                logger.debug(f"Loaded webhook: {url}")

            except discord.NotFound:
                logger.error(f"Webhook not found, skipping: {url}")
                continue
            except discord.HTTPException as e:
                logger.error(f"HTTP error loading webhook {url}: {e}")
                continue
            except Exception as e:
                logger.error(f"Failed to load webhook {url}: {e}")
                continue

        logger.info(
            f"Webhook Manager initialized with {loaded_count}/{len(self.provided_webhook_urls)} webhooks"
        )

    async def _verify_avatar_url(self, url: str) -> bool:
        """
        Checks if an image exists at URL using the existing bot session.
        Returns True only for 200 OK with image content-type.
        """
        if not url:
            return False

        if not self.session or self.session.closed:
            # If session is dead, we can't verify, so assume false to trigger re-upload safe-guard
            return False

        try:
            # HEAD request is faster - just check headers
            # Added timeout to prevent hanging
            async with self.session.head(url, timeout=5) as resp:
                if resp.status != 200:
                    return False

                content_type = resp.headers.get("Content-Type", "").lower()
                return content_type.startswith("image/")

        except Exception as e:
            logger.debug(f"Failed to verify URL {url}: {e}")
            return False

    async def run_maintenance(self) -> dict:
        """Manually run the avatar maintenance cycle using cached data."""
        summary = {"checked": 0, "missing": 0, "expired": 0, "uploaded": 0, "failed": 0}

        if not self._avatar_upload_channel_id:
            logger.warning("No avatar upload channel found, skipping maintenance.")
            return summary

        for filename in self._avatar_files:
            summary["checked"] += 1
            if filename not in self._avatar_file_cache:
                continue

            # SIMPLIFY: Use just filename as cache key
            cache_key = filename  # CHANGED FROM: f"{filename}_{file_hash}"

            should_upload = False

            # Case 1: TRULY missing from dictionary (not loaded from JSON)
            if cache_key not in self._avatar_cache:
                summary["missing"] += 1
                should_upload = True

            # Case 2: In cache, but actually expired (use a longer window, e.g., 24h)
            else:
                entry = self._avatar_cache[cache_key]
                # Only re-upload if it's older than 24 hours to save API calls
                if entry.is_expired(24):
                    summary["expired"] += 1
                    should_upload = True

            if should_upload:
                try:
                    avatar_bytes = self._avatar_file_cache[filename]
                    new_url = await self._upload_avatar_to_cdn(
                        filename, avatar_bytes, cache_key
                    )
                    if new_url:
                        summary["uploaded"] += 1
                        await asyncio.sleep(2)  # Protect against rate limits
                    else:
                        summary["failed"] += 1
                except Exception as e:
                    logger.error(f"Maintenance upload error: {e}")
                    summary["failed"] += 1

        await self._save_cache()
        return summary

    async def _avatar_maintenance_loop(self):
        await self.bot.wait_until_ready()

        while not self.bot.is_closed():
            try:
                logger.info(
                    f"Running avatar validity check on {len(self._avatar_files)} local assets..."
                )

                if not self._avatar_upload_channel_id:
                    logger.warning("No upload channel found, skipping maintenance.")
                    await asyncio.sleep(600)
                    continue

                for filename in self._avatar_files:
                    # 1. Check if we have the file data loaded
                    if filename not in self._avatar_file_cache:
                        continue

                    cache_key = filename

                    # 2. Check if it is completely missing from cache
                    if cache_key not in self._avatar_cache:
                        logger.info(
                            f"Maintenance: {filename} missing from cache. Uploading."
                        )
                        avatar_bytes = self._avatar_file_cache[filename]
                        await self._upload_avatar_to_cdn(
                            filename, avatar_bytes, cache_key
                        )
                        await asyncio.sleep(2.5)  # Rate limit protection

                    # 3. If in cache, VERIFY THE URL (The new logic)
                    else:
                        entry = self._avatar_cache[cache_key]

                        # Check if the link works
                        is_valid = await self._verify_avatar_url(entry.cdn_url)

                        if is_valid:
                            # It's good! Update last_used to keep it fresh in stats
                            entry.last_used = datetime.now()
                        else:
                            # It's dead (403/404). Refresh it.
                            logger.info(
                                f"Maintenance: {filename} link is dead (Status != 200). Refreshing."
                            )
                            await self._refresh_avatar(cache_key, entry)
                            await asyncio.sleep(2.5)  # Rate limit protection

                logger.info("Avatar maintenance complete.")

            except Exception as e:
                logger.error(f"Error in avatar maintenance loop: {e}")

            # Save the updated cache to disk
            await self._save_cache()

            # Run this every 60 minutes.
            # Since we are checking status codes, we don't need to run this constantly.
            await asyncio.sleep(3600)

    def _get_fast_webhook_info(self, webhook_url: str) -> Optional[dict]:
        """Get webhook info from fast cache (no API calls)."""
        if webhook_url in self._webhook_info_fast_cache:
            cache_entry = self._webhook_info_fast_cache[webhook_url]
            # Cache valid for 1 minute (frequent updates possible)
            if time.time() - cache_entry["timestamp"] < 60:
                return cache_entry
        return None

    def _update_fast_webhook_cache(
        self, webhook_url: str, name: str, avatar_url: Optional[str]
    ):
        """Update fast cache for webhook information."""
        self._webhook_info_fast_cache[webhook_url] = {
            "name": name,
            "avatar_url": avatar_url,
            "timestamp": time.time(),
        }

        # Limit cache size
        if len(self._webhook_info_fast_cache) > 100:
            # Remove oldest entry
            oldest_key = min(
                self._webhook_info_fast_cache.keys(),
                key=lambda k: self._webhook_info_fast_cache[k]["timestamp"],
            )
            del self._webhook_info_fast_cache[oldest_key]

    async def _load_cache(self):
        """Load avatar cache from disk."""
        if not os.path.exists(self._cache_file):
            logger.info("No existing avatar cache found")
            return

        try:
            with open(self._cache_file, "r") as f:
                cache_data = json.load(f)

            loaded = 0
            for key, data in cache_data.items():
                # --- CRITICAL FIX START ---
                # We load EVERYTHING. Even if it's 5 years old.
                # Do NOT put an 'if expired: continue' check here.
                entry = AvatarCacheEntry(
                    filename=data["filename"],
                    cdn_url=data["cdn_url"],
                    message_id=data["message_id"],
                    channel_id=data["channel_id"],
                )
                entry.uploaded_at = datetime.fromisoformat(data["uploaded_at"])
                entry.last_used = datetime.fromisoformat(data["last_used"])
                entry.use_count = data["use_count"]

                self._avatar_cache[key] = entry
                loaded += 1
                # --- CRITICAL FIX END ---

            logger.info(f"Loaded {loaded} cached avatars from disk")

        except Exception as e:
            logger.error(f"Error loading avatar cache: {e}")

    async def _save_cache(self):
        """Save avatar cache to disk."""
        try:
            cache_data = {}
            for key, entry in self._avatar_cache.items():
                cache_data[key] = {
                    "filename": entry.filename,
                    "cdn_url": entry.cdn_url,
                    "message_id": entry.message_id,
                    "channel_id": entry.channel_id,
                    "uploaded_at": entry.uploaded_at.isoformat(),
                    "last_used": entry.last_used.isoformat(),
                    "use_count": entry.use_count,
                }

            with open(self._cache_file, "w") as f:
                json.dump(cache_data, f, indent=2)

            logger.debug(f"Saved {len(cache_data)} avatar cache entries to disk")

        except Exception as e:
            logger.error(f"Error saving avatar cache: {e}")

    async def _periodic_cache_save(self):
        """Periodically save cache to disk."""
        while True:
            try:
                await asyncio.sleep(300)  # Save every 5 minutes
                await self._save_cache()
            except asyncio.CancelledError:
                await self._save_cache()  # Final save on cancellation
                break
            except Exception as e:
                logger.error(f"Error in periodic cache save: {e}")

    async def _load_local_avatars(self):
        """Load avatar images from assets folder."""
        base_dir = os.path.dirname(os.path.abspath(__file__))
        avatar_dir = os.path.join(base_dir, "assets")

        if not os.path.exists(avatar_dir):
            logger.warning(f"Avatar directory not found: {avatar_dir}")
            return

        supported_extensions = (".png", ".jpg", ".jpeg", ".gif", ".webp")
        loaded_count = 0

        for filename in os.listdir(avatar_dir):
            if filename.lower().endswith(supported_extensions):
                try:
                    with open(os.path.join(avatar_dir, filename), "rb") as f:
                        avatar_bytes = f.read()
                        self._avatar_file_cache[filename] = avatar_bytes
                        self._avatar_files.append(filename)
                        loaded_count += 1
                except Exception as e:
                    logger.error(f"Failed to load avatar {filename}: {e}")

        if loaded_count == 0:
            logger.warning("No avatars loaded! Using fallback Discord avatars.")

        logger.info(f"Loaded {loaded_count} custom avatars from {avatar_dir}")

    async def _ensure_avatar_channel(self):
        """Ensure we have a private channel for uploading avatars."""
        if self._avatar_upload_channel_id:
            return

        # Try to get from environment or config
        if (
            hasattr(constants, "AVATAR_UPLOAD_CHANNEL_ID")
            and constants.AVATAR_UPLOAD_CHANNEL_ID
        ):
            self._avatar_upload_channel_id = constants.AVATAR_UPLOAD_CHANNEL_ID
            return

        # Find or create a private channel
        try:
            # Look for existing channel named "avatar-storage"
            for guild in self.bot.guilds:
                for channel in guild.text_channels:
                    if (
                        channel.name == "avatar-storage"
                        and channel.permissions_for(guild.me).send_messages
                    ):
                        self._avatar_upload_channel_id = channel.id
                        logger.info(
                            f"Found existing avatar upload channel: {channel.id}"
                        )
                        return

            # Create a new channel in the first available guild
            for guild in self.bot.guilds:
                if guild.me.guild_permissions.manage_channels:
                    try:
                        overwrites = {
                            guild.default_role: discord.PermissionOverwrite(
                                read_messages=False
                            ),
                            guild.me: discord.PermissionOverwrite(
                                read_messages=True, send_messages=True
                            ),
                        }

                        channel = await guild.create_text_channel(
                            "avatar-storage",
                            overwrites=overwrites,
                            reason="Channel for storing anonymous chat avatars",
                        )
                        self._avatar_upload_channel_id = channel.id
                        logger.info(f"Created avatar upload channel: {channel.id}")
                        return
                    except Exception as e:
                        logger.error(f"Failed to create avatar channel: {e}")
                        continue

            logger.warning(
                "Could not create or find avatar upload channel. Using Discord default avatars."
            )

        except Exception as e:
            logger.error(f"Error setting up avatar channel: {e}")

    async def _get_or_upload_avatar(self, filename: str) -> Optional[str]:
        """Get cached avatar URL or upload to Discord CDN - async version."""
        if filename not in self._avatar_file_cache:
            return None

        avatar_bytes = self._avatar_file_cache[filename]

        # SIMPLIFY: Use just filename as cache key (no hash)
        cache_key = filename  # CHANGED FROM: f"{filename}_{file_hash}"

        # Check cache first
        if cache_key in self._avatar_cache:
            entry = self._avatar_cache[cache_key]

            # Check if expired
            if entry.is_expired(self._avatar_cache_max_age_hours):
                logger.debug(f"Cache entry expired for {filename}, removing")
                del self._avatar_cache[cache_key]
            else:
                # Check if needs refresh
                if entry.should_refresh(self._avatar_refresh_hours):
                    # Refresh in background
                    asyncio.create_task(self._refresh_avatar(cache_key, entry))

                entry.mark_used()
                return entry.cdn_url

        # Not in cache or expired, upload to Discord CDN
        return await self._upload_avatar_to_cdn(filename, avatar_bytes, cache_key)

    async def _upload_avatar_to_cdn(
        self, filename: str, avatar_bytes: bytes, cache_key: str
    ) -> Optional[str]:
        """Upload avatar to Discord CDN and cache the URL."""
        if not self._avatar_upload_channel_id:
            logger.warning("No avatar upload channel available")
            return None

        try:
            channel = self.bot.get_channel(self._avatar_upload_channel_id)
            if not channel:
                logger.warning(
                    f"Avatar upload channel {self._avatar_upload_channel_id} not found"
                )
                return None

            # Create discord.File
            file = discord.File(io.BytesIO(avatar_bytes), filename=filename)

            # Upload to Discord
            message = await channel.send(file=file)

            if message.attachments:
                # Get the Discord CDN URL
                cdn_url = message.attachments[0].url

                # Create cache entry
                entry = AvatarCacheEntry(
                    filename=filename,
                    cdn_url=cdn_url,
                    message_id=message.id,
                    channel_id=channel.id,
                )
                entry.mark_used()

                self._avatar_cache[cache_key] = entry

                # Clean up old cache entries if needed
                await self._cleanup_old_cache_entries()

                logger.debug(f"Uploaded avatar to Discord CDN: {filename} -> {cdn_url}")
                return cdn_url

        except discord.HTTPException as e:
            logger.error(f"Discord error uploading avatar {filename}: {e}")
            # Rate limit backoff
            await asyncio.sleep(2)
        except Exception as e:
            logger.error(f"Error uploading avatar {filename}: {e}")

        return None

    async def _refresh_avatar(self, cache_key: str, old_entry: AvatarCacheEntry):
        """Refresh an avatar by re-uploading it."""
        try:
            filename = old_entry.filename
            if filename not in self._avatar_file_cache:
                return

            avatar_bytes = self._avatar_file_cache[filename]

            # Upload new version
            new_cdn_url = await self._upload_avatar_to_cdn(
                filename, avatar_bytes, cache_key
            )

            if new_cdn_url:
                # Delete old message
                logger.debug(f"Refreshed avatar: {filename}")

        except Exception as e:
            logger.error(f"Error refreshing avatar: {e}")

    async def _cleanup_old_cache_entries(self):
        """Clean up old cache entries to stay within limits."""
        if len(self._avatar_cache) <= self._max_cached_avatars:
            return

        # Sort by last used (oldest first)
        sorted_entries = sorted(
            self._avatar_cache.items(), key=lambda x: x[1].last_used
        )

        # Delete oldest entries
        for cache_key, entry in sorted_entries[
            : len(self._avatar_cache) - self._max_cached_avatars
        ]:
            try:
                del self._avatar_cache[cache_key]
            except Exception as e:
                logger.error(f"Error cleaning up cache entry {cache_key}: {e}")

    async def get_random_anonymous_state(self) -> Tuple[str, str]:
        """Get random anonymous identity state using YOUR custom avatars - ASYNC."""
        name = secrets.choice(self._anonymous_names)

        if not self._avatar_files:
            # No custom avatars, use Discord default
            avatar_url = (
                f"https://cdn.discordapp.com/embed/avatars/{secrets.randbelow(6)}.png"
            )
            return name, avatar_url

        # FIX: Pick a random file FIRST
        filename = secrets.choice(self._avatar_files)

        # SIMPLIFY: Check cache with just filename
        cache_key = filename  # CHANGED FROM: f"{filename}_{file_hash}"

        if cache_key in self._avatar_cache:
            entry = self._avatar_cache[cache_key]
            if not entry.is_expired(self._avatar_cache_max_age_hours):
                entry.mark_used()
                return name, entry.cdn_url

        # If the chosen random file wasn't cached, upload it now
        cdn_url = await self._get_or_upload_avatar(filename)

        if cdn_url:
            return name, cdn_url

        # Fallback
        avatar_url = (
            f"https://cdn.discordapp.com/embed/avatars/{secrets.randbelow(6)}.png"
        )
        return name, avatar_url

    async def assign_webhooks_for_session(
        self, session_id: str, thread: discord.Thread = None
    ) -> Tuple[Optional[str], Optional[str]]:
        """Assign webhooks for a new session with anonymous state."""
        print(
            f"[DEBUG] Available webhooks: {len(self.available_webhooks)}"
        )  # Check pool size
        async with self._lock:
            if len(self.available_webhooks) < 2:
                return None, None

            # Get two random webhooks
            webhook1 = self.available_webhooks.pop(
                secrets.randbelow(len(self.available_webhooks))
            )
            webhook2 = self.available_webhooks.pop(
                secrets.randbelow(len(self.available_webhooks))
            )
            print(f"Selected webhooks: {webhook1.url}, {webhook2.url}")

            # Set anonymous identity for both webhooks using YOUR avatars
            name1, avatar1 = await self.get_random_anonymous_state()
            name2, avatar2 = await self.get_random_anonymous_state()

            # Ensure avatars are different
            attempts = 0
            while avatar1 == avatar2 and attempts < 5:
                name2, avatar2 = await self.get_random_anonymous_state()
                attempts += 1

            webhook1.identity.is_anonymous = True
            webhook1.identity.display_name = name1
            webhook1.identity.avatar_url = avatar1

            webhook2.identity.is_anonymous = True
            webhook2.identity.display_name = name2
            webhook2.identity.avatar_url = avatar2

            # Update fast cache
            self._update_fast_webhook_cache(webhook1.url, name1, avatar1)
            self._update_fast_webhook_cache(webhook2.url, name2, avatar2)

            webhook1.assigned_session_id = session_id
            webhook2.assigned_session_id = session_id
            self.session_webhooks[session_id] = (webhook1, webhook2)

            if thread and hasattr(thread, "id"):
                self.thread_sessions[thread.id] = session_id

            logger.debug(
                f"Assigned webhooks for session {session_id}: {webhook1.url}, {webhook2.url}"
            )
            return webhook1.url, webhook2.url

    async def associate_thread_with_session(
        self, thread: discord.Thread, session_id: str
    ):
        """Associate a thread with a session for reply tracking."""
        async with self._lock:
            self.thread_sessions[thread.id] = session_id
            logger.debug(f"Associated thread {thread.id} with session {session_id}")

    async def reveal_identities(
        self, session_id: str, user1: discord.User, user2: discord.User
    ) -> bool:
        """Reveal identities by swapping identity states."""
        async with self._lock:
            if session_id not in self.session_webhooks:
                return False

            webhook1, webhook2 = self.session_webhooks[session_id]

            # Update identity states (no API calls)
            webhook1.identity.is_anonymous = False
            webhook1.identity.display_name = user2.display_name[:80]
            webhook1.identity.user = user2
            if user2.avatar:
                webhook1.identity.avatar_url = str(user2.avatar.url)
            else:
                # Use Discord's default avatar for user without custom avatar
                discriminator = (
                    int(user2.discriminator) if user2.discriminator != "0" else user2.id
                )
                avatar_index = discriminator % 6
                webhook1.identity.avatar_url = (
                    f"https://cdn.discordapp.com/embed/avatars/{avatar_index}.png"
                )

            webhook2.identity.is_anonymous = False
            webhook2.identity.display_name = user1.display_name[:80]
            webhook2.identity.user = user1
            if user1.avatar:
                webhook2.identity.avatar_url = str(user1.avatar.url)
            else:
                discriminator = (
                    int(user1.discriminator) if user1.discriminator != "0" else user1.id
                )
                avatar_index = discriminator % 6
                webhook2.identity.avatar_url = (
                    f"https://cdn.discordapp.com/embed/avatars/{avatar_index}.png"
                )

            # Update fast cache
            self._update_fast_webhook_cache(
                webhook1.url,
                webhook1.identity.display_name,
                webhook1.identity.avatar_url,
            )
            self._update_fast_webhook_cache(
                webhook2.url,
                webhook2.identity.display_name,
                webhook2.identity.avatar_url,
            )

            logger.debug(
                f"Revealed identities for session {session_id}: {user1.id} <-> {user2.id}"
            )
            return True

    async def update_webhook_identity(
        self, webhook_url: str, user: discord.User, use_default_avatar: bool = False
    ):
        """Update webhook identity state without API calls."""
        managed = self.managed_webhooks.get(webhook_url)
        if not managed:
            logger.error(f"Webhook not found for URL: {webhook_url}")
            return False

        try:
            managed.identity.is_anonymous = False
            managed.identity.display_name = user.display_name[:80]
            managed.identity.user = user

            if use_default_avatar or not user.avatar:
                # Use Discord's default avatar based on discriminator
                discriminator = (
                    int(user.discriminator) if user.discriminator != "0" else user.id
                )
                avatar_index = discriminator % 6
                managed.identity.avatar_url = (
                    f"https://cdn.discordapp.com/embed/avatars/{avatar_index}.png"
                )
            else:
                managed.identity.avatar_url = str(user.avatar.url)

            # Update fast cache
            self._update_fast_webhook_cache(
                webhook_url, managed.identity.display_name, managed.identity.avatar_url
            )

            managed.identity.last_updated = time.time()
            logger.debug(
                f"Updated identity state for webhook {webhook_url} to user {user.id}"
            )
            return True
        except Exception as e:
            logger.error(f"Update identity state error for {webhook_url}: {e}")
            return False

    async def reset_to_anonymous(self, webhook_url: str) -> bool:
        """Reset a webhook to anonymous state with a new random avatar."""
        managed = self.managed_webhooks.get(webhook_url)
        if not managed:
            return False

        try:
            # Get new random anonymous identity - FIX: Use async version!
            name, avatar_url = (
                await self.get_random_anonymous_state()
            )  # CHANGED THIS LINE

            managed.identity.is_anonymous = True
            managed.identity.display_name = name
            managed.identity.avatar_url = avatar_url
            managed.identity.user = None

            # Update fast cache
            self._update_fast_webhook_cache(webhook_url, name, avatar_url)

            logger.debug(f"Reset webhook {webhook_url} to anonymous: {name}")
            return True
        except Exception as e:
            logger.error(f"Failed to reset webhook to anonymous: {e}")
            return False

    async def relay_message(
        self, thread, webhook_url, message, reply_to_mirrored_id=None
    ):
        """Relay message with identity from state - WITH FIXED REPLY EMBEDS."""
        managed = self.managed_webhooks.get(webhook_url)
        if not managed:
            logger.error(f"Webhook not found for URL: {webhook_url}")
            return None, "Webhook not found"

        # Check if webhook is valid
        if not managed.is_valid:
            logger.error(f"Webhook is no longer valid: {webhook_url}")
            return None, "Webhook is no longer valid"

        try:
            # Prepare content
            content = message.content[:2000] if message.content else ""

            # Download attachments if any
            files = []
            if message.attachments:
                for att in message.attachments:
                    try:
                        async with aiohttp.ClientSession() as session:
                            async with session.get(att.url) as resp:
                                if resp.status == 200:
                                    data = await resp.read()
                                    file = discord.File(
                                        io.BytesIO(data), filename=att.filename
                                    )
                                    files.append(file)
                                else:
                                    logger.error(
                                        f"Failed to download attachment: {att.url}"
                                    )
                    except Exception as e:
                        logger.error(f"Error downloading attachment {att.url}: {e}")

            # Get identity from state
            username = managed.get_display_name()
            avatar_url = managed.get_avatar_url()

            # IMPORTANT: Ensure avatar_url is a valid Discord CDN URL
            if avatar_url and (
                avatar_url.startswith("data:") or ";base64," in avatar_url
            ):
                # Invalid URL format, use default
                logger.warning(
                    f"Invalid avatar URL format for webhook {webhook_url}, using default"
                )
                avatar_url = "https://cdn.discordapp.com/embed/avatars/0.png"

            # Prepare webhook send parameters
            send_kwargs = {
                "content": content if content.strip() else None,
                "username": username,
                "avatar_url": avatar_url,
                "thread": thread,
                "wait": True,
                "allowed_mentions": discord.AllowedMentions.none(),
                "files": files if files else [],
            }

            # Create a list for all embeds
            all_embeds = []
            if reply_to_mirrored_id:
                reply_embed = None

                # 1. Try to get from Cache
                if reply_to_mirrored_id in self._reply_message_cache:
                    cache_entry = self._reply_message_cache[reply_to_mirrored_id]
                    if time.time() - cache_entry["timestamp"] < 300:
                        # CRITICAL FIX: Ensure 'embed' is not None before using it
                        if cache_entry.get("embed"):
                            reply_embed = cache_entry["embed"]

                # 2. If Cache failed or was None, Fetch from Discord
                if not reply_embed:
                    try:
                        replied_message = await thread.fetch_message(
                            reply_to_mirrored_id
                        )

                        # [Existing logic to find webhook info/author name...]
                        # METHOD 1: Look up webhook from database
                        webhook_info = await self.db.get_webhook_for_message(
                            replied_message.id
                        )

                        reply_author_name = "Shadow"
                        reply_author_avatar = None

                        if webhook_info:
                            managed_webhook = self.managed_webhooks.get(
                                webhook_info["webhook_url"]
                            )
                            if managed_webhook:
                                cache_info = self._get_fast_webhook_info(
                                    webhook_info["webhook_url"]
                                )
                                if cache_info:
                                    reply_author_name = cache_info["name"]
                                    reply_author_avatar = cache_info["avatar_url"]
                                else:
                                    if managed_webhook.is_anonymous:
                                        reply_author_name = "Shadow"
                                        reply_author_avatar = (
                                            managed_webhook.get_avatar_url()
                                        )
                                    else:
                                        reply_author_name = (
                                            managed_webhook.get_display_name()
                                        )
                                        reply_author_avatar = (
                                            managed_webhook.get_avatar_url()
                                        )
                        else:
                            # METHOD 2: Try to determine from session context
                            session_id = None
                            for sess_id, (wh1, wh2) in self.session_webhooks.items():
                                if wh1.url == webhook_url:
                                    partner_webhook = wh2
                                    session_id = sess_id
                                    break
                                elif wh2.url == webhook_url:
                                    partner_webhook = wh1
                                    session_id = sess_id
                                    break

                            if session_id:
                                cache_info = self._get_fast_webhook_info(
                                    partner_webhook.url
                                )
                                if cache_info:
                                    reply_author_name = cache_info["name"]
                                    reply_author_avatar = cache_info["avatar_url"]
                                else:
                                    reply_author_name = (
                                        partner_webhook.get_display_name()
                                    )
                                    reply_author_avatar = (
                                        partner_webhook.get_avatar_url()
                                    )

                        # Create message link
                        message_link = f"https://discord.com/channels/{thread.guild.id}/{thread.id}/{reply_to_mirrored_id}"

                        # Create reply embed
                        reply_embed = discord.Embed(color=0x87CEEB)

                        if replied_message.content:
                            content_preview = (
                                replied_message.content[:100] + "..."
                                if len(replied_message.content) > 100
                                else replied_message.content
                            )
                            reply_embed.description = f"{content_preview}"
                        elif replied_message.attachments:
                            attachment_texts = []
                            for att in replied_message.attachments[:3]:
                                attachment_texts.append(
                                    f"📎 [{att.filename}]({att.url})"
                                )

                            if attachment_texts:
                                reply_embed.description = f"\n".join(attachment_texts)
                            else:
                                reply_embed.description = (
                                    f"📎 [Attachment]({message_link})"
                                )
                        elif replied_message.stickers:
                            sticker = replied_message.stickers[0]
                            sticker_name = getattr(sticker, "name", "Sticker")
                            reply_embed.description = (
                                f"🧩 [{sticker_name}]({message_link})"
                            )
                        elif replied_message.embeds:
                            reply_embed.description = (
                                f"🖼️ [Embedded Content]({message_link})"
                            )
                        else:
                            reply_embed.description = f"[Message]({message_link})"

                        # Set author
                        reply_embed.set_author(
                            name=f"Reply to {reply_author_name}",
                            url=message_link,
                            icon_url=reply_author_avatar,
                        )

                        # Cache this new valid embed
                        self._reply_message_cache[reply_to_mirrored_id] = {
                            "embed": reply_embed,
                            "timestamp": time.time(),
                        }

                    except discord.NotFound:
                        reply_embed = discord.Embed(
                            description="**Replying to a deleted message**",
                            color=0x87CEEB,
                        )
                    except Exception as e:
                        logger.error(f"Failed to create reply embed: {e}")
                        reply_embed = discord.Embed(
                            description="**Replying to a message**", color=0x87CEEB
                        )

                if reply_embed:
                    all_embeds.append(reply_embed)

            # Handle stickers - KEEPING YOUR CODE
            if message.stickers:
                for sticker in message.stickers:
                    try:
                        sticker_embed = discord.Embed(color=discord.Color.pink())

                        if hasattr(sticker, "name") and sticker.name:
                            sticker_embed.title = f"🩷 {sticker.name}"

                        if hasattr(sticker, "url") and sticker.url:
                            sticker_embed.set_image(url=str(sticker.url))

                        if reply_to_mirrored_id and not content.strip():
                            sticker_embed.description = "**Replying**"

                        all_embeds.append(sticker_embed)
                    except Exception as e:
                        logger.error(f"Failed to create sticker embed: {e}")

            # Handle user-created embeds
            if message.embeds:
                for embed in message.embeds:
                    try:
                        if embed.type == "rich":
                            embed_copy = discord.Embed.from_dict(embed.to_dict())

                            # Sanitize embed
                            if (
                                hasattr(embed_copy, "author")
                                and embed_copy.author
                                and embed_copy.author.url
                            ):
                                if "/webhooks/" in embed_copy.author.url:
                                    embed_copy.set_author(
                                        name=embed_copy.author.name,
                                        icon_url=embed_copy.author.icon_url,
                                    )

                            # Add reply note if needed
                            if (
                                reply_to_mirrored_id
                                and not content.strip()
                                and not message.stickers
                            ):
                                if embed_copy.description:
                                    embed_copy.description = (
                                        f"**Replying**\n{embed_copy.description}"
                                    )
                                else:
                                    embed_copy.description = "**Replying**"

                            all_embeds.append(embed_copy)
                    except Exception as e:
                        logger.error(f"Failed to process embed: {e}")

            # Add embeds to send_kwargs if we have any
            if all_embeds:
                valid_embeds = [e for e in all_embeds if e is not None]
                if valid_embeds:
                    send_kwargs["embeds"] = valid_embeds[:10]

            # Send the message
            sent = await managed.webhook.send(**send_kwargs)

            # Track which webhook sent this message
            if sent and hasattr(sent, "id"):
                # Get session ID
                session_id = None
                for sess_id, (wh1, wh2) in self.session_webhooks.items():
                    if wh1.url == webhook_url or wh2.url == webhook_url:
                        session_id = sess_id
                        break

                if session_id:
                    # Store in database
                    await self.db.store_webhook_message(
                        message_id=sent.id,
                        webhook_id=managed.webhook.id,
                        webhook_url=webhook_url,
                        thread_id=thread.id,
                        session_id=session_id,
                    )

                    # Cache the message info for replies
                    self._reply_message_cache[sent.id] = {
                        "embed": sent.embeds[0] if sent.embeds else None,
                        "timestamp": time.time(),
                        "username": username,
                        "avatar_url": avatar_url,
                    }
                    # Clean old cache entries
                    self._clean_old_reply_cache_entries()

            return sent.id, None

        except discord.NotFound:
            logger.error(f"Webhook not found, marking as invalid: {webhook_url}")
            managed.is_valid = False
            return None, "Webhook no longer exists"
        except discord.HTTPException as e:
            logger.error(f"Discord HTTP error sending message: {e}")
            return None, str(e)
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            return None, str(e)

    def _clean_old_reply_cache_entries(self, max_age: int = 300, max_size: int = 1000):
        """Clean old reply cache entries."""
        current_time = time.time()

        # Remove old entries
        to_remove = []
        for key, entry in self._reply_message_cache.items():
            if current_time - entry["timestamp"] > max_age:
                to_remove.append(key)

        for key in to_remove:
            del self._reply_message_cache[key]

        # Limit cache size
        if len(self._reply_message_cache) > max_size:
            # Remove oldest entries
            sorted_keys = sorted(
                self._reply_message_cache.keys(),
                key=lambda k: self._reply_message_cache[k]["timestamp"],
            )
            for key in sorted_keys[: len(sorted_keys) - max_size]:
                del self._reply_message_cache[key]

    async def delete_webhook(self, webhook_url: str):
        """Dummy method for compatibility."""
        logger.debug(f"Delete webhook called (no-op for static webhook): {webhook_url}")
        pass

    async def end_session(self, session_id: str):
        """Return webhooks to pool and reset identity states."""
        async with self._lock:
            # Clear thread associations
            thread_ids_to_remove = []
            for thread_id, sess_id in self.thread_sessions.items():
                if sess_id == session_id:
                    thread_ids_to_remove.append(thread_id)

            for thread_id in thread_ids_to_remove:
                del self.thread_sessions[thread_id]

            if session_id not in self.session_webhooks:
                logger.warning(f"Session {session_id} not found for cleanup")
                return

            webhook1, webhook2 = self.session_webhooks[session_id]

            # Reset identity states with NEW random avatars - FIX: Use async versions!
            name1, avatar1 = await self.get_random_anonymous_state()  # CHANGED
            name2, avatar2 = await self.get_random_anonymous_state()  # CHANGED

            webhook1.identity.is_anonymous = True
            webhook1.identity.display_name = name1
            webhook1.identity.avatar_url = avatar1
            webhook1.identity.user = None
            webhook1.assigned_session_id = None

            webhook2.identity.is_anonymous = True
            webhook2.identity.display_name = name2
            webhook2.identity.avatar_url = avatar2
            webhook2.identity.user = None
            webhook2.assigned_session_id = None

            # Update fast cache
            self._update_fast_webhook_cache(webhook1.url, name1, avatar1)
            self._update_fast_webhook_cache(webhook2.url, name2, avatar2)

            # Return to available pool
            if webhook1.is_valid and webhook1 not in self.available_webhooks:
                self.available_webhooks.append(webhook1)
            if webhook2.is_valid and webhook2 not in self.available_webhooks:
                self.available_webhooks.append(webhook2)

            del self.session_webhooks[session_id]
            logger.info(f"Session {session_id} ended, webhooks returned to pool")

    async def close(self):
        """Cleanup resources."""
        # Cancel cache save task
        if self._cache_save_task:
            self._cache_save_task.cancel()
            try:
                await self._cache_save_task
            except asyncio.CancelledError:
                pass

        # Save cache one final time
        await self._save_cache()

        if self.session and not self.session.closed:
            await self.session.close()
        logger.info("Webhook Manager closed")

    def get_stats(self) -> Dict:
        """Get statistics about webhook manager."""
        # Count active vs expired cache entries
        active_cache = 0
        expired_cache = 0
        for entry in self._avatar_cache.values():
            if entry.is_expired(self._avatar_cache_max_age_hours):
                expired_cache += 1
            else:
                active_cache += 1

        return {
            "total_webhooks": len(self.managed_webhooks),
            "available_webhooks": len(self.available_webhooks),
            "active_sessions": len(self.session_webhooks),
            "valid_webhooks": sum(
                1 for w in self.managed_webhooks.values() if w.is_valid
            ),
            "reply_cache_size": len(self._reply_message_cache),
            "local_avatar_count": len(self._avatar_files),
            "cached_avatars_active": active_cache,
            "cached_avatars_expired": expired_cache,
            "user_avatar_cache_size": len(self._user_avatar_cache),
            "avatar_upload_channel": self._avatar_upload_channel_id,
            "fast_webhook_cache_size": len(self._webhook_info_fast_cache),
        }

    def clear_cache(self):
        """Clear all caches (for debugging)."""
        self._reply_message_cache.clear()
        self._user_avatar_cache.clear()
        self._avatar_cache.clear()
        self._webhook_info_fast_cache.clear()
        logger.info("All caches cleared")

    async def cleanup_expired_avatars(self):
        """Manually clean up expired avatar cache entries."""
        logger.info("Starting manual avatar cache cleanup...")

        expired_keys = []
        for cache_key, entry in self._avatar_cache.items():
            if entry.is_expired(self._avatar_cache_max_age_hours):
                expired_keys.append(cache_key)

        cleaned = 0
        for cache_key in expired_keys:
            try:
                entry = self._avatar_cache[cache_key]
                del self._avatar_cache[cache_key]
                cleaned += 1
            except Exception as e:
                logger.error(f"Error cleaning up expired avatar {cache_key}: {e}")

        await self._save_cache()
        logger.info(f"Cleaned up {cleaned} expired avatars")
