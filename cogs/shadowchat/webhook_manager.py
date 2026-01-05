"""
SHADOW CHAT - WEBHOOK MANAGER (FINAL FIX - CORRECT AVATAR LOGIC)
WITH OPTIMIZED CACHING
"""

import asyncio
import secrets
import discord
import io
import aiohttp
from typing import Optional, Dict, Tuple, List
from . import constants
import os
import logging
from .models import Database
import time
from datetime import datetime

# Set up logging
logger = logging.getLogger(__name__)

class ManagedWebhook:
    """Represents a managed webhook with session state."""
    
    def __init__(self, url: str, webhook: discord.Webhook, manager=None):
        self.url = url
        self.webhook = webhook
        self.manager = manager  # Reference to WebhookManager for cache updates
        self.is_anonymous = True
        self.current_avatar_bytes = None
        self.assigned_session_id: Optional[str] = None
        self.revealed_user: Optional[discord.User] = None
        self.is_valid = True  # Track if webhook is still valid
        self.last_used: Optional[datetime] = None
        
    async def reset_to_anonymous(self, avatar_bytes: Optional[bytes] = None):
        """
        Reset webhook to anonymous state.
        OPTIMIZED: Only calls API if state actually needs to change.
        """
        # If already anonymous and avatar hasn't changed, skip API call
        if self.is_anonymous and self.current_avatar_bytes == avatar_bytes:
            return

        try:
            self.webhook = await self.webhook.edit(
                name="Shadow",
                avatar=avatar_bytes,
                reason="Reset to anonymous for new session"
            )
            self.is_anonymous = True
            self.revealed_user = None
            self.current_avatar_bytes = avatar_bytes
            self.is_valid = True
            self.last_used = datetime.now()
            
            # Update fast cache if manager is available
            if self.manager:
                avatar_url = str(self.webhook.avatar.url) if self.webhook.avatar else None
                self.manager._update_fast_webhook_cache(self.url, "Shadow", avatar_url)
            
            logger.debug(f"Webhook reset to anonymous: {self.url}")
        except discord.NotFound:
            logger.error(f"Webhook not found, marking as invalid: {self.url}")
            self.is_valid = False
            raise
        except discord.HTTPException as e:
            logger.error(f"Failed to reset webhook: {e}")
            self.is_valid = False
            raise
        except Exception as e:
            logger.error(f"Unexpected error resetting webhook: {e}")
            self.is_valid = False
            raise

    async def update_identity(self, user: discord.User, avatar_bytes: Optional[bytes]):
        """Update webhook to match a specific user (Reveal)."""
        try:
            display_name = user.display_name[:80] if user.display_name else user.name[:80]
            self.webhook = await self.webhook.edit(
                name=display_name,
                avatar=avatar_bytes,
                reason=f"Identity reveal: {user.id}"
            )
            self.is_anonymous = False
            self.revealed_user = user
            self.current_avatar_bytes = avatar_bytes
            self.is_valid = True
            self.last_used = datetime.now()
            
            # Update fast cache if manager is available
            if self.manager:
                avatar_url = str(user.avatar.url) if user.avatar else None
                self.manager._update_fast_webhook_cache(self.url, display_name, avatar_url)
            
            logger.debug(f"Webhook identity updated to user: {user.id}")
        except discord.NotFound:
            logger.error(f"Webhook not found during reveal: {self.url}")
            self.is_valid = False
            raise
        except discord.HTTPException as e:
            logger.error(f"Discord HTTP error updating identity: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error updating identity: {e}")
            raise
    
    async def get_avatar_url(self) -> str:
        """Get avatar URL without API calls where possible."""
        if not self.is_anonymous and self.revealed_user and self.revealed_user.avatar:
            return str(self.revealed_user.avatar.url)
        
        # For anonymous, use default or cached webhook avatar
        if self.webhook and self.webhook.avatar:
            return str(self.webhook.avatar.url)
            
        # Fallback URL so Discord doesn't throw a 400 Bad Request
        return "https://cdn.discordapp.com/embed/avatars/0.png"

class WebhookManager:
    """Manages webhooks using provided static URLs."""
    
    def __init__(self, bot, db: Database):
        self.bot = bot
        self.db: Optional[Database] = db
        self.session: Optional[aiohttp.ClientSession] = None
        
        # Configuration: Add your webhook URLs here
        self.provided_webhook_urls = constants.WEBHOOK_URLS
        
        self.managed_webhooks: Dict[str, ManagedWebhook] = {}
        self.available_webhooks: List[ManagedWebhook] = []
        self.session_webhooks: Dict[str, Tuple[ManagedWebhook, ManagedWebhook]] = {}
        
        self.avatar_cache: Dict[str, bytes] = {}
        self.avatar_keys: List[str] = []
        self.user_avatar_cache: Dict[str, bytes] = {}  # user_id -> avatar_bytes cache
        self._reply_message_cache: Dict[int, dict] = {}  # message_id -> webhook info cache
        
        # Fast cache for webhook info (no API calls needed)
        self._webhook_info_fast_cache: Dict[str, dict] = {}  # webhook_url -> {name, avatar_url, timestamp}
        
        self._lock = asyncio.Lock()
        self.thread_sessions: Dict[int, str] = {}
        
    async def initialize(self):
        """Initialize webhook manager."""
        logger.info("Initializing Webhook Manager...")
        
        # Create aiohttp session with connection pooling
        if not self.session or self.session.closed:
            connector = aiohttp.TCPConnector(limit_per_host=20, ttl_dns_cache=300)
            self.session = aiohttp.ClientSession(connector=connector)
        
        await self._load_avatars()
        
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
                
                # Initialize fast cache
                avatar_url = str(webhook.avatar.url) if webhook.avatar else None
                self._update_fast_webhook_cache(url, "Shadow", avatar_url)
                
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

    def _get_fast_webhook_info(self, webhook_url: str) -> Optional[dict]:
        """Get webhook info from fast cache (no API calls)."""
        if webhook_url in self._webhook_info_fast_cache:
            cache_entry = self._webhook_info_fast_cache[webhook_url]
            # Cache valid for 1 minute (frequent updates possible)
            if time.time() - cache_entry['timestamp'] < 60:
                return cache_entry
        return None

    def _update_fast_webhook_cache(self, webhook_url: str, name: str, avatar_url: Optional[str]):
        """Update fast cache for webhook information."""
        self._webhook_info_fast_cache[webhook_url] = {
            'name': name,
            'avatar_url': avatar_url,
            'timestamp': time.time()
        }
        
        # Limit cache size
        if len(self._webhook_info_fast_cache) > 100:
            # Remove oldest entry
            oldest_key = min(self._webhook_info_fast_cache.keys(), 
                           key=lambda k: self._webhook_info_fast_cache[k]['timestamp'])
            del self._webhook_info_fast_cache[oldest_key]

    async def _load_avatars(self):
        """Load avatar images from assets folder."""
        base_dir = os.path.dirname(os.path.abspath(__file__))
        avatar_dir = os.path.join(base_dir, "assets")
        
        if not os.path.exists(avatar_dir):
            logger.warning(f"Avatar directory not found: {avatar_dir}")
            return
        
        supported_extensions = ('.png', '.jpg', '.jpeg', '.gif', '.webp')
        loaded_count = 0
        
        for filename in os.listdir(avatar_dir):
            if filename.lower().endswith(supported_extensions):
                try:
                    with open(os.path.join(avatar_dir, filename), 'rb') as f:
                        self.avatar_cache[filename] = f.read()
                        self.avatar_keys.append(filename)
                        loaded_count += 1
                    print("Loaded {} avatars.".format(loaded_count))
                except Exception as e:
                    logger.error(f"Failed to load avatar {filename}: {e}")
        
        logger.debug(f"Loaded {loaded_count} avatars from {avatar_dir}")
    
    def _get_random_avatar_bytes(self) -> Optional[bytes]:
        """Get random avatar bytes from cache using cryptographically secure random selection."""
        if not self.avatar_keys:
            return None
        
        # Use secrets.choice() for cryptographically secure random selection
        random_key = secrets.choice(self.avatar_keys)
        return self.avatar_cache.get(random_key)
    
    async def associate_thread_with_session(self, thread: discord.Thread, session_id: str):
        """Associate a thread with a session for reply tracking."""
        async with self._lock:
            self.thread_sessions[thread.id] = session_id
            logger.debug(f"Associated thread {thread.id} with session {session_id}")
    
    async def assign_webhooks_for_session(self, session_id: str, thread: discord.Thread = None) -> Tuple[Optional[str], Optional[str]]:
        """Assign webhooks and RESET them here (Lazy Reset)."""
        async with self._lock:
            if len(self.available_webhooks) < 2:
                return None, None
            
            webhook1 = self.available_webhooks.pop(secrets.randbelow(len(self.available_webhooks)))
            webhook2 = self.available_webhooks.pop(secrets.randbelow(len(self.available_webhooks)))
            try:
                # Get random avatar bytes from cache
                avatar_bytes1 = self._get_random_avatar_bytes()
                avatar_bytes2 = self._get_random_avatar_bytes()
                
                # Use asyncio.gather for parallel execution
                await asyncio.gather(
                    webhook1.reset_to_anonymous(avatar_bytes1),
                    webhook2.reset_to_anonymous(avatar_bytes2)
                )
                
            except Exception:
                self.available_webhooks.extend([webhook1, webhook2])
                return None, None
            
            webhook1.assigned_session_id = session_id
            webhook2.assigned_session_id = session_id
            self.session_webhooks[session_id] = (webhook1, webhook2)
            if thread and hasattr(thread, 'id'):
                self.thread_sessions[thread.id] = session_id
            return webhook1.url, webhook2.url
    
    async def reveal_identities(self, session_id: str, user1: discord.User, user2: discord.User) -> bool:
        """Reveal identities by swapping profiles."""
        async with self._lock:
            if session_id not in self.session_webhooks:
                return False
            
            webhook1, webhook2 = self.session_webhooks[session_id]
            
            try:
                async def get_avatar(u):
                    if not u.avatar: 
                        return None
                    
                    # Check cache first
                    cache_key = f"{u.id}_{u.avatar.key if u.avatar.key else 'default'}"
                    if cache_key in self.user_avatar_cache:
                        return self.user_avatar_cache[cache_key]
                    
                    # Download and cache
                    async with aiohttp.ClientSession() as s:
                        async with s.get(str(u.avatar.url)) as r:
                            if r.status == 200:
                                avatar_bytes = await r.read()
                                # Cache the result
                                self.user_avatar_cache[cache_key] = avatar_bytes
                                # Limit cache size
                                if len(self.user_avatar_cache) > 50:
                                    # Remove oldest entry
                                    del self.user_avatar_cache[next(iter(self.user_avatar_cache))]
                                return avatar_bytes
                    return None

                # Download avatars in parallel
                av1, av2 = await asyncio.gather(
                    get_avatar(user1), 
                    get_avatar(user2)
                )
                
                # Update webhooks in parallel
                await asyncio.gather(
                    webhook1.update_identity(user2, av2),
                    webhook2.update_identity(user1, av1)
                )
                
                return True
            except Exception as e:
                print(f"[Webhook Manager] Reveal failed: {e}")
                return False

    async def update_webhook_identity(self, webhook_url: str, user: discord.User, use_default_avatar: bool = False):
        """
        Public method for IdentityManager to call.
        Finds the managed webhook and updates its identity.
        """
        managed = self.managed_webhooks.get(webhook_url)
        if not managed:
            logger.error(f"Webhook not found for URL: {webhook_url}")
            return False
            
        try:
            avatar_bytes = None
            if not use_default_avatar and user.avatar:
                # Check cache first
                cache_key = f"{user.id}_{user.avatar.key if user.avatar.key else 'default'}"
                if cache_key in self.user_avatar_cache:
                    avatar_bytes = self.user_avatar_cache[cache_key]
                else:
                    async with self.session.get(str(user.avatar.url)) as response:
                        if response.status == 200:
                            avatar_bytes = await response.read()
                            # Cache the result
                            self.user_avatar_cache[cache_key] = avatar_bytes
                            # Limit cache size
                            if len(self.user_avatar_cache) > 50:
                                # Remove oldest entry
                                del self.user_avatar_cache[next(iter(self.user_avatar_cache))]
            
            await managed.update_identity(user, avatar_bytes)
            logger.debug(f"Updated webhook {webhook_url} to user {user.id}")
            return True
        except Exception as e:
            logger.error(f"Update identity error for {webhook_url}: {e}")
            return False

    async def relay_message(self, thread, webhook_url, message, reply_to_mirrored_id=None):
        """Relay message with support for stickers, files, embeds, and reply embeds."""
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
            
            # Append attachment URLs to content if any
            files = []
            if message.attachments:
                for att in message.attachments:
                    # Download the attachment
                    try:
                        async with aiohttp.ClientSession() as session:
                            async with session.get(att.url) as resp:
                                if resp.status == 200:
                                    data = await resp.read()
                                    # Create a discord.File object
                                    file = discord.File(io.BytesIO(data), filename=att.filename)
                                    files.append(file)
                                else:
                                    logger.error(f"Failed to download attachment: {att.url}")
                    except Exception as e:
                        logger.error(f"Error downloading attachment {att.url}: {e}")
            
            # Truncate content to 2000 characters if needed
            if len(content) > 2000:
                content = content[:2000]
            
            username = "Shadow"
            avatar_url = None
            
            if not managed.is_anonymous and managed.revealed_user:
                username = managed.revealed_user.display_name
                if managed.revealed_user.avatar:
                    avatar_url = str(managed.revealed_user.avatar.url)
            else:
                if managed.webhook.avatar:
                    avatar_url = str(managed.webhook.avatar.url)

            
            # Prepare webhook send parameters - USE WEBHOOK'S CURRENT AVATAR
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
                # Check cache first
                if reply_to_mirrored_id in self._reply_message_cache:
                    cache_entry = self._reply_message_cache[reply_to_mirrored_id]
                    if time.time() - cache_entry['timestamp'] < 300:  # 5 minute cache
                        reply_embed = cache_entry['embed']
                        all_embeds.append(reply_embed)
                else:
                    try:
                        replied_message = await thread.fetch_message(reply_to_mirrored_id)
                        
                        # METHOD 1: Look up webhook from our database tracking
                        webhook_info = await self.db.get_webhook_for_message(replied_message.id)
                        
                        reply_author_name = "Shadow"
                        reply_author_avatar = None
                        
                        if webhook_info:
                            managed_webhook = self.managed_webhooks.get(webhook_info['webhook_url'])
                            if managed_webhook:
                                # USE FAST CACHE INSTEAD OF API CALL
                                cache_info = self._get_fast_webhook_info(webhook_info['webhook_url'])
                                if cache_info:
                                    # Use cached info
                                    reply_author_name = cache_info['name']
                                    reply_author_avatar = cache_info['avatar_url']
                                else:
                                    # Fallback to managed webhook state (no API call)
                                    if managed_webhook.is_anonymous:
                                        reply_author_name = "Shadow"
                                        reply_author_avatar = managed_webhook.webhook.avatar.url if managed_webhook.webhook.avatar else None
                                    else:
                                        reply_author_name = managed_webhook.revealed_user.display_name if managed_webhook.revealed_user else "Shadow"
                                        reply_author_avatar = str(managed_webhook.revealed_user.avatar.url) if managed_webhook.revealed_user and managed_webhook.revealed_user.avatar else None
                        else:
                            # METHOD 2: Try to determine from session context
                            # Get session ID from thread tracking
                            session_id = None
                            for sess_id, (wh1, wh2) in self.session_webhooks.items():
                                if wh1.url == webhook_url:
                                    # Current webhook is wh1, so replied message was from wh2 (partner)
                                    partner_webhook = wh2
                                    session_id = sess_id
                                    break
                                elif wh2.url == webhook_url:
                                    # Current webhook is wh2, so replied message was from wh1 (partner)
                                    partner_webhook = wh1
                                    session_id = sess_id
                                    break
                            
                            if session_id:
                                # USE FAST CACHE INSTEAD OF API CALL
                                cache_info = self._get_fast_webhook_info(partner_webhook.url)
                                if cache_info:
                                    # Use cached info
                                    reply_author_name = cache_info['name']
                                    reply_author_avatar = cache_info['avatar_url']
                                else:
                                    # Fallback to partner webhook state (no API call)
                                    if partner_webhook.is_anonymous:
                                        reply_author_name = "Shadow"
                                        reply_author_avatar = None
                                    else:
                                        reply_author_name = partner_webhook.revealed_user.display_name if partner_webhook.revealed_user else "Shadow"
                                        reply_author_avatar = str(partner_webhook.revealed_user.avatar.url) if partner_webhook.revealed_user and partner_webhook.revealed_user.avatar else None
                                
                        # Create message link
                        message_link = f"https://discord.com/channels/{thread.guild.id}/{thread.id}/{reply_to_mirrored_id}"
                        
                        # Create reply embed
                        reply_embed = discord.Embed(color=0x87CEEB)
                        
                        if replied_message.content:
                            content_preview = replied_message.content[:100] + "..." if len(replied_message.content) > 100 else replied_message.content
                            reply_embed.description = f"{content_preview}"
                        elif replied_message.attachments:
                            attachment_texts = []
                            for att in replied_message.attachments[:3]:
                                attachment_texts.append(f"📎 [{att.filename}]({att.url})")
                            
                            if attachment_texts:
                                reply_embed.description = f"\n".join(attachment_texts)
                            else:
                                reply_embed.description = f"📎 [Attachment]({message_link})"
                            
                            if len(replied_message.attachments) > 3:
                                reply_embed.description += f"\n... and {len(replied_message.attachments) - 3} more"
                        elif replied_message.stickers:
                            sticker = replied_message.stickers[0]
                            sticker_name = getattr(sticker, 'name', 'Sticker')
                            reply_embed.description = f"🩷 [{sticker_name}]({message_link})"
                        elif replied_message.embeds:
                            reply_embed.description = f"📄 [Embedded Content]({message_link})"
                        else:
                            reply_embed.description = f"[Message]({message_link})"
                        
                        # Set author with message link - USE WEBHOOK'S CURRENT AVATAR
                        reply_embed.set_author(
                            name=f"Reply to {reply_author_name}",
                            url=message_link,
                            icon_url=reply_author_avatar  # Use webhook's current avatar
                        )
                        
                        reply_embed.timestamp = replied_message.created_at
                        all_embeds.append(reply_embed)
                        
                        # Cache the reply embed
                        self._reply_message_cache[reply_to_mirrored_id] = {
                            'embed': reply_embed,
                            'timestamp': time.time()
                        }
                        # Limit cache size
                        if len(self._reply_message_cache) > 100:
                            # Remove oldest entry
                            del self._reply_message_cache[next(iter(self._reply_message_cache))]
                        
                    except discord.NotFound:
                        simple_reply_embed = discord.Embed(
                            description="**Replying to a message**",
                            color=0x87CEEB
                        )
                        all_embeds.append(simple_reply_embed)
                    except Exception as e:
                        logger.error(f"Failed to create reply embed: {e}")
                        simple_reply_embed = discord.Embed(
                            description="**Replying to a message**",
                            color=0x87CEEB
                        )
                        all_embeds.append(simple_reply_embed)
            
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
                send_kwargs['embeds'] = all_embeds[:10]  # Discord limit
            
            # Send the message
            sent = await managed.webhook.send(**send_kwargs)

            # TRACK WHICH WEBHOOK SENT THIS MESSAGE
            if sent and hasattr(sent, 'id'):
                # Get session ID from thread tracking or find it
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

    async def delete_webhook(self, webhook_url: str):
        """
        Dummy method for compatibility with SessionManager.
        Since we use static webhooks, we don't actually delete them from Discord,
        we just ensure they are returned to the pool (handled by end_session).
        """
        logger.debug(f"Delete webhook called (no-op for static webhook): {webhook_url}")
        pass
        
    async def end_session(self, session_id: str):
        """Return webhooks to pool WITHOUT calling API."""
        async with self._lock:
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
            
            webhook1.assigned_session_id = None
            webhook2.assigned_session_id = None
            
            # Only return valid webhooks to pool
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
            "avatar_cache_size": len(self.avatar_cache),
            "valid_webhooks": sum(1 for w in self.managed_webhooks.values() if w.is_valid),
            "user_avatar_cache_size": len(self.user_avatar_cache),
            "reply_cache_size": len(self._reply_message_cache),
            "webhook_fast_cache_size": len(self._webhook_info_fast_cache)
        }