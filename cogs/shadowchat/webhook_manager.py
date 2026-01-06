"""
SHADOW CHAT - WEBHOOK MANAGER (OPTIMIZED - NO EDIT METHODS)
USES AVATAR/NAME PARAMETERS IN SEND METHOD
HOSTS AVATARS ON DISCORD'S CDN
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
from datetime import datetime
import hashlib

# Set up logging
logger = logging.getLogger(__name__)

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
        self._cached_default_avatar_url: Optional[str] = None
        
    def get_avatar_url(self) -> Optional[str]:
        """Get avatar URL from identity state."""
        if not self.identity.is_anonymous and self.identity.user and self.identity.user.avatar:
            # Use user's avatar URL
            return str(self.identity.user.avatar.url)
        elif self.identity.avatar_url:
            # Use cached anonymous avatar URL
            return self.identity.avatar_url
        elif self._cached_default_avatar_url:
            # Use previously cached default avatar
            return self._cached_default_avatar_url
        else:
            # Fallback to Discord's default avatar
            return "https://cdn.discordapp.com/embed/avatars/0.png"
    
    def get_display_name(self) -> str:
        """Get display name from identity state."""
        return self.identity.display_name

class WebhookManager:
    """Manages webhooks using provided static URLs with caching."""
    
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
        
        # Caching system - store Discord CDN URLs for your custom avatars
        self._avatar_cache: Dict[str, str] = {}  # filename_hash -> Discord CDN URL
        self._avatar_file_cache: Dict[str, bytes] = {}  # filename -> file bytes
        self._avatar_files: List[str] = []  # List of available avatar filenames
        self._user_avatar_cache: Dict[str, str] = {}  # user_id -> avatar_url
        self._default_avatar_cache: Dict[str, str] = {}  # webhook_url -> default_avatar_url
        self._reply_cache: Dict[int, dict] = {}  # message_id -> reply info
        
        # Random display names for anonymous mode
        self._anonymous_names = [
            "Shadow", "Ghost", "Echo", "Spectre", "Phantom", "Wraith",
            "Mystery", "Unknown", "Stranger", "Incognito", "Anon", "Mask"
        ]
        
        # Channel for uploading avatar images
        self._avatar_upload_channel_id = None  # Set this to a private channel ID
        
        self._lock = asyncio.Lock()
        
    async def initialize(self):
        """Initialize webhook manager and load/upload local avatars to Discord CDN."""
        logger.info("Initializing Webhook Manager...")
        
        # Create aiohttp session with connection pooling
        if not self.session or self.session.closed:
            connector = aiohttp.TCPConnector(limit_per_host=20, ttl_dns_cache=300)
            self.session = aiohttp.ClientSession(connector=connector)
        
        # Load your custom avatars and upload to Discord CDN
        await self._load_and_upload_avatars()
        
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
                
                # Cache the webhook's current avatar URL
                if webhook.avatar:
                    managed._cached_default_avatar_url = str(webhook.avatar.url)
                    self._default_avatar_cache[url] = str(webhook.avatar.url)
                
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
        
        logger.info(f"Webhook Manager initialized with {loaded_count}/{len(self.provided_webhook_urls)} webhooks")
    
    async def _load_and_upload_avatars(self):
        """Load avatar images from assets folder and upload to Discord CDN."""
        base_dir = os.path.dirname(os.path.abspath(__file__))
        avatar_dir = os.path.join(base_dir, "assets")
        
        if not os.path.exists(avatar_dir):
            logger.warning(f"Avatar directory not found: {avatar_dir}")
            return
        
        supported_extensions = ('.png', '.jpg', '.jpeg', '.gif', '.webp')
        loaded_count = 0
        
        # Create or get avatar upload channel
        await self._ensure_avatar_channel()
        
        # Load all avatar files
        for filename in os.listdir(avatar_dir):
            if filename.lower().endswith(supported_extensions):
                try:
                    with open(os.path.join(avatar_dir, filename), 'rb') as f:
                        avatar_bytes = f.read()
                        self._avatar_file_cache[filename] = avatar_bytes
                        self._avatar_files.append(filename)
                        loaded_count += 1
                except Exception as e:
                    logger.error(f"Failed to load avatar {filename}: {e}")
        
        if loaded_count == 0:
            logger.warning("No avatars loaded! Using fallback Discord avatars.")
            # Fallback to Discord default avatars
            for i in range(6):
                filename = f"discord_default_{i}"
                self._avatar_cache[filename] = f"https://cdn.discordapp.com/embed/avatars/{i}.png"
                self._avatar_files.append(filename)
        
        logger.info(f"Loaded {loaded_count} custom avatars from {avatar_dir}")
        
        # Upload avatars to Discord CDN in background (lazy loading)
        asyncio.create_task(self._upload_avatars_to_cdn())
    
    async def _ensure_avatar_channel(self):
        """Ensure we have a private channel for uploading avatars."""
        if self._avatar_upload_channel_id:
            return
            
        # Try to get from environment or config
        if hasattr(constants, 'AVATAR_UPLOAD_CHANNEL_ID'):
            self._avatar_upload_channel_id = constants.AVATAR_UPLOAD_CHANNEL_ID
            return
            
        # Find or create a private channel
        try:
            # Look for existing channel named "avatar-storage"
            for guild in self.bot.guilds:
                for channel in guild.text_channels:
                    if channel.name == "avatar-storage" and channel.permissions_for(guild.me).send_messages:
                        self._avatar_upload_channel_id = channel.id
                        logger.info(f"Found existing avatar upload channel: {channel.id}")
                        return
            
            # Create a new channel in the first available guild
            for guild in self.bot.guilds:
                if guild.me.guild_permissions.manage_channels:
                    try:
                        overwrites = {
                            guild.default_role: discord.PermissionOverwrite(read_messages=False),
                            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
                        }
                        
                        channel = await guild.create_text_channel(
                            "avatar-storage",
                            overwrites=overwrites,
                            reason="Channel for storing anonymous chat avatars"
                        )
                        self._avatar_upload_channel_id = channel.id
                        logger.info(f"Created avatar upload channel: {channel.id}")
                        return
                    except Exception as e:
                        logger.error(f"Failed to create avatar channel: {e}")
                        continue
            
            logger.warning("Could not create or find avatar upload channel. Using Discord default avatars.")
            
        except Exception as e:
            logger.error(f"Error setting up avatar channel: {e}")
    
    async def _upload_avatars_to_cdn(self):
        """Upload avatars to Discord CDN and cache the URLs."""
        if not self._avatar_upload_channel_id or not self._avatar_file_cache:
            return
            
        try:
            channel = self.bot.get_channel(self._avatar_upload_channel_id)
            if not channel:
                logger.warning(f"Avatar upload channel {self._avatar_upload_channel_id} not found")
                return
            
            uploaded_count = 0
            for filename, avatar_bytes in self._avatar_file_cache.items():
                try:
                    # Create hash for cache key
                    file_hash = hashlib.md5(avatar_bytes).hexdigest()[:8]
                    cache_key = f"{filename}_{file_hash}"
                    
                    # Skip if already uploaded
                    if cache_key in self._avatar_cache:
                        continue
                    
                    # Create discord.File
                    file = discord.File(io.BytesIO(avatar_bytes), filename=filename)
                    
                    # Upload to Discord
                    message = await channel.send(file=file)
                    
                    if message.attachments:
                        # Get the Discord CDN URL
                        cdn_url = message.attachments[0].url
                        self._avatar_cache[cache_key] = cdn_url
                        uploaded_count += 1
                        
                        # Also map filename to this URL for random selection
                        self._avatar_cache[filename] = cdn_url
                        
                        logger.debug(f"Uploaded avatar to Discord CDN: {filename} -> {cdn_url}")
                    
                    # Rate limiting prevention
                    await asyncio.sleep(0.5)
                    
                except discord.HTTPException as e:
                    logger.error(f"Discord error uploading avatar {filename}: {e}")
                    # Rate limit backoff
                    await asyncio.sleep(2)
                except Exception as e:
                    logger.error(f"Error uploading avatar {filename}: {e}")
                    continue
            
            logger.info(f"Uploaded {uploaded_count} avatars to Discord CDN")
            
            # Clean up old messages (keep only the most recent 50)
            if uploaded_count > 0:
                await self._cleanup_old_avatar_messages(channel)
                
        except Exception as e:
            logger.error(f"Error in avatar upload process: {e}")
    
    async def _cleanup_old_avatar_messages(self, channel):
        """Clean up old avatar messages to keep channel clean."""
        try:
            # Keep only the most recent 50 messages
            messages_to_delete = []
            async for message in channel.history(limit=100):
                if len(message.attachments) > 0:
                    messages_to_delete.append(message)
            
            if len(messages_to_delete) > 50:
                messages_to_delete = messages_to_delete[50:]
                
                for message in messages_to_delete:
                    try:
                        await message.delete()
                        await asyncio.sleep(0.1)  # Rate limiting
                    except Exception as e:
                        logger.error(f"Failed to delete old avatar message: {e}")
                        
        except Exception as e:
            logger.error(f"Error cleaning up avatar messages: {e}")
    
    def _get_random_anonymous_state(self) -> Tuple[str, str]:
        """Get random anonymous identity state using YOUR custom avatars."""
        name = secrets.choice(self._anonymous_names)
        
        if self._avatar_files and self._avatar_cache:
            # Try to use custom avatars
            for _ in range(10):  # Try multiple times
                random_avatar_file = secrets.choice(self._avatar_files)
                if random_avatar_file in self._avatar_cache:
                    avatar_url = self._avatar_cache[random_avatar_file]
                    return name, avatar_url
        
        # Fallback to Discord default
        avatar_url = f"https://cdn.discordapp.com/embed/avatars/{secrets.randbelow(6)}.png"
        return name, avatar_url
    
    def _get_random_avatar_url(self) -> str:
        """Get a random avatar URL from your custom avatars."""
        if self._avatar_files and self._avatar_cache:
            for _ in range(10):  # Try multiple times
                random_avatar_file = secrets.choice(self._avatar_files)
                if random_avatar_file in self._avatar_cache:
                    return self._avatar_cache[random_avatar_file]
        
        # Fallback to Discord default
        return f"https://cdn.discordapp.com/embed/avatars/{secrets.randbelow(6)}.png"
    
    async def associate_thread_with_session(self, thread: discord.Thread, session_id: str):
        """Associate a thread with a session for reply tracking."""
        async with self._lock:
            self.thread_sessions[thread.id] = session_id
            logger.debug(f"Associated thread {thread.id} with session {session_id}")
    
    async def assign_webhooks_for_session(self, session_id: str, thread: discord.Thread = None) -> Tuple[Optional[str], Optional[str]]:
        """Assign webhooks for a new session with anonymous state."""
        async with self._lock:
            if len(self.available_webhooks) < 2:
                return None, None
            
            # Get two random webhooks
            webhook1 = self.available_webhooks.pop(secrets.randbelow(len(self.available_webhooks)))
            webhook2 = self.available_webhooks.pop(secrets.randbelow(len(self.available_webhooks)))
            
            # Set anonymous identity for both webhooks using YOUR avatars
            name1, avatar1 = self._get_random_anonymous_state()
            name2, avatar2 = self._get_random_anonymous_state()
            
            # Ensure avatars are different
            while avatar1 == avatar2:
                name2, avatar2 = self._get_random_anonymous_state()
            
            webhook1.identity.is_anonymous = True
            webhook1.identity.display_name = name1
            webhook1.identity.avatar_url = avatar1
            
            webhook2.identity.is_anonymous = True
            webhook2.identity.display_name = name2
            webhook2.identity.avatar_url = avatar2
            
            webhook1.assigned_session_id = session_id
            webhook2.assigned_session_id = session_id
            self.session_webhooks[session_id] = (webhook1, webhook2)
            
            if thread and hasattr(thread, 'id'):
                self.thread_sessions[thread.id] = session_id
            
            logger.debug(f"Assigned webhooks for session {session_id}: {webhook1.url}, {webhook2.url}")
            return webhook1.url, webhook2.url
    
    async def reveal_identities(self, session_id: str, user1: discord.User, user2: discord.User) -> bool:
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
                discriminator = int(user2.discriminator) if user2.discriminator != "0" else user2.id
                avatar_index = discriminator % 6
                webhook1.identity.avatar_url = f"https://cdn.discordapp.com/embed/avatars/{avatar_index}.png"
            
            webhook2.identity.is_anonymous = False
            webhook2.identity.display_name = user1.display_name[:80]
            webhook2.identity.user = user1
            if user1.avatar:
                webhook2.identity.avatar_url = str(user1.avatar.url)
            else:
                discriminator = int(user1.discriminator) if user1.discriminator != "0" else user1.id
                avatar_index = discriminator % 6
                webhook2.identity.avatar_url = f"https://cdn.discordapp.com/embed/avatars/{avatar_index}.png"
            
            logger.debug(f"Revealed identities for session {session_id}: {user1.id} <-> {user2.id}")
            return True
    
    async def update_webhook_identity(self, webhook_url: str, user: discord.User, use_default_avatar: bool = False):
        """
        Update webhook identity state without API calls.
        """
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
                discriminator = int(user.discriminator) if user.discriminator != "0" else user.id
                avatar_index = discriminator % 6
                managed.identity.avatar_url = f"https://cdn.discordapp.com/embed/avatars/{avatar_index}.png"
            else:
                managed.identity.avatar_url = str(user.avatar.url)
            
            managed.identity.last_updated = time.time()
            logger.debug(f"Updated identity state for webhook {webhook_url} to user {user.id}")
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
            # Get new random anonymous identity
            name, avatar_url = self._get_random_anonymous_state()
            
            managed.identity.is_anonymous = True
            managed.identity.display_name = name
            managed.identity.avatar_url = avatar_url
            managed.identity.user = None
            
            logger.debug(f"Reset webhook {webhook_url} to anonymous: {name}")
            return True
        except Exception as e:
            logger.error(f"Failed to reset webhook to anonymous: {e}")
            return False
    
    async def relay_message(self, thread, webhook_url, message, reply_to_mirrored_id=None):
        """Relay message with identity from state."""
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
                                    file = discord.File(io.BytesIO(data), filename=att.filename)
                                    files.append(file)
                                else:
                                    logger.error(f"Failed to download attachment: {att.url}")
                    except Exception as e:
                        logger.error(f"Error downloading attachment {att.url}: {e}")
            
            # Get identity from state
            username = managed.get_display_name()
            avatar_url = managed.get_avatar_url()
            
            # IMPORTANT: Ensure avatar_url is a valid Discord CDN URL
            if avatar_url and (avatar_url.startswith('data:') or ';base64,' in avatar_url):
                # Invalid URL format, use default
                logger.warning(f"Invalid avatar URL format for webhook {webhook_url}, using default")
                avatar_url = "https://cdn.discordapp.com/embed/avatars/0.png"
            
            # Prepare webhook send parameters
            send_kwargs = {
                'content': content if content.strip() else None,
                'username': username,
                'avatar_url': avatar_url,
                'thread': thread,
                'wait': True,
                'allowed_mentions': discord.AllowedMentions.none(),
                'files': files if files else []
            }
            
            # Create a list for all embeds
            all_embeds = []
            
            # Handle reply embeds
            if reply_to_mirrored_id:
                reply_embed = await self._create_reply_embed(
                    thread, reply_to_mirrored_id, webhook_url
                )
                if reply_embed:
                    all_embeds.append(reply_embed)
            
            # Handle stickers
            if message.stickers:
                for sticker in message.stickers:
                    try:
                        sticker_embed = discord.Embed(color=discord.Color.pink())
                        
                        if hasattr(sticker, 'name') and sticker.name:
                            sticker_embed.title = f"🩷 {sticker.name}"
                        
                        if hasattr(sticker, 'url') and sticker.url:
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
                        if embed.type == 'rich':
                            embed_copy = discord.Embed.from_dict(embed.to_dict())
                            
                            # Sanitize embed
                            if hasattr(embed_copy, 'author') and embed_copy.author and embed_copy.author.url:
                                if '/webhooks/' in embed_copy.author.url:
                                    embed_copy.set_author(
                                        name=embed_copy.author.name,
                                        icon_url=embed_copy.author.icon_url
                                    )
                            
                            # Add reply note if needed
                            if reply_to_mirrored_id and not content.strip() and not message.stickers:
                                if embed_copy.description:
                                    embed_copy.description = f"**Replying**\n{embed_copy.description}"
                                else:
                                    embed_copy.description = "**Replying**"
                            
                            all_embeds.append(embed_copy)
                    except Exception as e:
                        logger.error(f"Failed to process embed: {e}")
            
            # Add embeds to send_kwargs if we have any
            if all_embeds:
                send_kwargs['embeds'] = all_embeds[:10]
            
            # Send the message
            sent = await managed.webhook.send(**send_kwargs)

            # Track which webhook sent this message
            if sent and hasattr(sent, 'id'):
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
                        session_id=session_id
                    )
                    
                    # Cache the message info for replies
                    self._reply_cache[sent.id] = {
                        'webhook_url': webhook_url,
                        'username': username,
                        'avatar_url': avatar_url,
                        'timestamp': time.time()
                    }
                    # Clean old cache entries
                    self._clean_old_cache_entries()

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
    
    async def _create_reply_embed(self, thread, reply_to_mirrored_id: int, current_webhook_url: str) -> Optional[discord.Embed]:
        """Create a reply embed using cached data."""
        # Check cache first
        if reply_to_mirrored_id in self._reply_cache:
            cache_entry = self._reply_cache[reply_to_mirrored_id]
            if time.time() - cache_entry['timestamp'] < 300:  # 5 minute cache
                return self._build_reply_embed(
                    thread, reply_to_mirrored_id,
                    cache_entry['username'],
                    cache_entry['avatar_url']
                )
        
        # Try database lookup
        try:
            replied_message = await thread.fetch_message(reply_to_mirrored_id)
            
            # Look up webhook from database
            webhook_info = await self.db.get_webhook_for_message(replied_message.id)
            
            if webhook_info:
                # Get partner webhook from session
                session_id = None
                partner_webhook = None
                
                for sess_id, (wh1, wh2) in self.session_webhooks.items():
                    if wh1.url == current_webhook_url:
                        partner_webhook = wh2
                        session_id = sess_id
                        break
                    elif wh2.url == current_webhook_url:
                        partner_webhook = wh1
                        session_id = sess_id
                        break
                
                if partner_webhook:
                    return self._build_reply_embed(
                        thread, reply_to_mirrored_id,
                        partner_webhook.get_display_name(),
                        partner_webhook.get_avatar_url()
                    )
            
            # Fallback: generic reply
            return self._build_reply_embed(
                thread, reply_to_mirrored_id,
                "Shadow",
                "https://cdn.discordapp.com/embed/avatars/0.png"
            )
            
        except Exception as e:
            logger.error(f"Failed to create reply embed: {e}")
            return None
    
    def _build_reply_embed(self, thread, message_id: int, username: str, avatar_url: str) -> discord.Embed:
        """Build a reply embed with given information."""
        message_link = f"https://discord.com/channels/{thread.guild.id}/{thread.id}/{message_id}"
        
        embed = discord.Embed(color=0x87CEEB)
        embed.set_author(
            name=f"Reply to {username}",
            url=message_link,
            icon_url=avatar_url
        )
        embed.description = f"[Jump to message]({message_link})"
        
        return embed
    
    def _clean_old_cache_entries(self, max_age: int = 300, max_size: int = 1000):
        """Clean old cache entries."""
        current_time = time.time()
        
        # Remove old entries
        to_remove = []
        for key, entry in self._reply_cache.items():
            if current_time - entry['timestamp'] > max_age:
                to_remove.append(key)
        
        for key in to_remove:
            del self._reply_cache[key]
        
        # Limit cache size
        if len(self._reply_cache) > max_size:
            # Remove oldest entries
            sorted_keys = sorted(self._reply_cache.keys(), 
                               key=lambda k: self._reply_cache[k]['timestamp'])
            for key in sorted_keys[:len(sorted_keys) - max_size]:
                del self._reply_cache[key]
    
    async def delete_webhook(self, webhook_url: str):
        """
        Dummy method for compatibility.
        Since we use static webhooks, we don't actually delete them.
        """
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
            
            # Reset identity states with NEW random avatars
            name1, avatar1 = self._get_random_anonymous_state()
            name2, avatar2 = self._get_random_anonymous_state()
            
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
            
            # Return to available pool
            if webhook1.is_valid and webhook1 not in self.available_webhooks:
                self.available_webhooks.append(webhook1)
            if webhook2.is_valid and webhook2 not in self.available_webhooks:
                self.available_webhooks.append(webhook2)
            
            del self.session_webhooks[session_id]
            logger.info(f"Session {session_id} ended, webhooks returned to pool")

    async def close(self):
        """Cleanup resources."""
        if self.session and not self.session.closed:
            await self.session.close()
        logger.info("Webhook Manager closed")

    def get_stats(self) -> Dict:
        """Get statistics about webhook manager."""
        return {
            "total_webhooks": len(self.managed_webhooks),
            "available_webhooks": len(self.available_webhooks),
            "active_sessions": len(self.session_webhooks),
            "valid_webhooks": sum(1 for w in self.managed_webhooks.values() if w.is_valid),
            "reply_cache_size": len(self._reply_cache),
            "local_avatar_count": len(self._avatar_files),
            "uploaded_avatar_count": len(self._avatar_cache),
            "user_avatar_cache_size": len(self._user_avatar_cache)
        }
    
    def clear_cache(self):
        """Clear all caches (for debugging)."""
        self._reply_cache.clear()
        self._user_avatar_cache.clear()
        logger.info("All caches cleared")