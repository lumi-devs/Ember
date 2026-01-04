"""
SHADOW CHAT - WEBHOOK MANAGER
Cached for speed, flushed on session end.
"""

import asyncio
import discord
import io
import aiohttp
import random
from typing import Optional, Dict, Tuple, List
from . import constants
import os

class CachedWebhook:
    """Represents a cached webhook with its data."""
    
    def __init__(self, webhook: discord.Webhook, avatar_key: Optional[str] = None):
        self.webhook = webhook
        self.avatar_key = avatar_key
        self.is_anonymous = True  # Start as anonymous by default
        self.user_id = None  # User ID if revealed
        self.thread_id = None  # Associated thread ID

class WebhookManager:
    """Manages webhooks for message relaying with session-based caching."""
    
    def __init__(self, bot):
        self.bot = bot
        self.session = aiohttp.ClientSession()
        
        # Cache structure: {webhook_url: CachedWebhook}
        self.webhook_cache: Dict[str, CachedWebhook] = {}
        
        # Quick lookup by thread ID: {thread_id: webhook_url}
        self.thread_to_webhook: Dict[int, str] = {}
        
        # Reverse lookup: {webhook_id: webhook_url} for Discord's webhook ID
        self.webhook_id_to_url: Dict[int, str] = {}
        
        # Avatar management (persistent across sessions)
        self.avatar_cache: Dict[str, bytes] = {}  # filename -> avatar bytes
        self.avatar_urls: List[str] = []  # Available avatar filenames
        
        # For faster avatar selection
        self._avatar_index = 0
        self._creation_lock = asyncio.Lock()
    
    async def initialize(self):
        """Initialize webhook manager and load avatars."""
        base_dir = os.path.dirname(os.path.abspath(__file__))
        avatar_dir = os.path.join(base_dir, "assets")
        
        self.avatar_cache = {} 
        self.avatar_urls = []
        
        if not os.path.exists(avatar_dir):
            print(f"[Shadow Chat] WARNING: 'assets' folder not found at: {avatar_dir}")
            return

        print(f"[Shadow Chat] Loading avatars from: {avatar_dir}")
        
        valid_extensions = ('.png', '.jpg', '.jpeg', '.gif', '.webp')
        loaded_count = 0

        try:
            for filename in os.listdir(avatar_dir):
                if filename.lower().endswith(valid_extensions):
                    file_path = os.path.join(avatar_dir, filename)
                    try:
                        with open(file_path, 'rb') as f:
                             avatar_bytes = await asyncio.to_thread(f.read)
                            
                        self.avatar_cache[filename] = avatar_bytes
                        self.avatar_urls.append(filename)
                        loaded_count += 1
                        print(f"[Shadow Chat] ✓ Loaded avatar: {filename} ({len(avatar_bytes)} bytes)")
                        
                    except Exception as e:
                        print(f"[Shadow Chat] ✗ Failed to load {filename}: {e}")
            
            print(f"[Shadow Chat] Successfully loaded {loaded_count} avatars from local folder")
            
            if loaded_count == 0:
                 print(f"[Shadow Chat] WARNING: No valid images found in assets folder!")

        except Exception as e:
            print(f"[Shadow Chat] Error accessing avatar directory: {e}")
    
    def _get_next_avatar_key(self) -> Optional[str]:
        """Get next avatar key in round-robin fashion."""
        if not self.avatar_urls:
            return None
        
        # Round-robin selection
        key = self.avatar_urls[self._avatar_index]
        self._avatar_index = (self._avatar_index + 1) % len(self.avatar_urls)
        return key
    
    def get_random_avatar_bytes(self) -> Optional[bytes]:
        """Get random avatar bytes quickly."""
        key = self._get_next_avatar_key()
        if key:
            return self.avatar_cache.get(key)
        return None
    
    def _cache_webhook(self, webhook_url: str, webhook: discord.Webhook, 
                      avatar_key: Optional[str] = None, is_anonymous: bool = True,
                      thread_id: Optional[int] = None):
        """Cache a webhook for fast access."""
        cached = CachedWebhook(webhook, avatar_key)
        cached.is_anonymous = is_anonymous
        cached.thread_id = thread_id
        self.webhook_cache[webhook_url] = cached
        
        # Store webhook ID for reverse lookup
        if hasattr(webhook, 'id') and webhook.id:
            self.webhook_id_to_url[webhook.id] = webhook_url
        
        # Store thread mapping
        if thread_id:
            self.thread_to_webhook[thread_id] = webhook_url
        
        return cached
    
    def _get_cached_webhook(self, webhook_url: str) -> Optional[CachedWebhook]:
        """Get cached webhook if valid."""
        return self.webhook_cache.get(webhook_url)
    
    async def close(self):
        """Close HTTP session and FLUSH ALL CACHE."""
        await self.session.close()
        
        # FLUSH ALL CACHE - This is what you wanted!
        self._flush_cache()
    
    def _flush_cache(self):
        """Flush all webhook caches (called on session end)."""
        print(f"[Webhook Manager] Flushing cache: {len(self.webhook_cache)} webhooks, {len(self.thread_to_webhook)} thread mappings")
        
        # Clear all caches
        self.webhook_cache.clear()
        self.thread_to_webhook.clear()
        self.webhook_id_to_url.clear()
        
        print(f"[Webhook Manager] Cache flushed successfully")
    
    async def create_webhook(self, thread: discord.Thread, partner_user: discord.Member) -> Tuple[Optional[discord.Webhook], Optional[str]]:
      async with self._creation_lock:
        try:
            parent_channel = thread.parent
            if not parent_channel:
                return None, None
            
            # ALWAYS use "Shadow" as the name
            webhook_name = "Shadow"
            
            # First, check if we already have a cached webhook for this thread
            cached_url = self.thread_to_webhook.get(thread.id)
            if cached_url and cached_url in self.webhook_cache:
                cached = self.webhook_cache[cached_url]
                # Verify it still exists in Discord
                try:
                    webhooks = await parent_channel.webhooks()
                    for wh in webhooks:
                        if wh.id == cached.webhook.id:
                            return cached.webhook, cached_url
                except:
                    pass  # We'll create a new one
            
            # Check Discord for existing webhooks owned by our bot
            webhooks = await parent_channel.webhooks()
            
            # Look for any "Shadow" webhook owned by our bot
            for webhook in webhooks:
                if webhook.name == webhook_name and webhook.user == self.bot.user:
                    # Check if this webhook is already assigned to a thread
                    webhook_url = self.webhook_id_to_url.get(webhook.id)
                    if webhook_url:
                        # This webhook is already in use by another thread
                        continue
                    
                    # Found unused "Shadow" webhook, assign it to this thread
                    self._cache_webhook(webhook.url, webhook, 
                                      thread_id=thread.id)
                    return webhook, webhook.url
            
            # Get avatar
            avatar_key = self._get_next_avatar_key()
            avatar_bytes = self.avatar_cache.get(avatar_key) if avatar_key else None
            
            # Create new webhook with name "Shadow"
            webhook = await parent_channel.create_webhook(
                name=webhook_name,
                avatar=avatar_bytes,
                reason=f"Shadow Chat webhook for thread {thread.id}"
            )
            
            # Cache it
            self._cache_webhook(webhook.url, webhook, avatar_key, True, thread.id)
            
            return webhook, webhook.url
            
        except discord.Forbidden:
            return None, None
        except discord.HTTPException as e:
            print(f"[Webhook Manager] Failed to create webhook: {e}")
            return None, None
        except Exception as e:
            print(f"[Webhook Manager] Unexpected error creating webhook: {e}")
            import traceback
            traceback.print_exc()
            return None, None
    
    async def get_webhook(self, webhook_url: str, *, force_fetch: bool = False) -> Optional[discord.Webhook]:
        """
        Get webhook object from URL with caching.
        
        Args:
            webhook_url: The webhook URL
            force_fetch: If True, fetch fresh from Discord (for identity updates)
        """
        cached = self._get_cached_webhook(webhook_url)
        
        # Use cache unless we need fresh data (identity reveal)
        if cached and not force_fetch:
            return cached.webhook
        
        try:
            # Fetch fresh from Discord
            webhook = discord.Webhook.from_url(
                webhook_url,
                session=self.session,
            )
            
            # Update cache with fresh webhook
            if cached:
                cached.webhook = webhook
            else:
                # Try to find thread association
                thread_id = None
                for tid, url in self.thread_to_webhook.items():
                    if url == webhook_url:
                        thread_id = tid
                        break
                
                # Create new cache entry
                self._cache_webhook(webhook_url, webhook, 
                                  thread_id=thread_id)
            
            return webhook
        except Exception as e:
            print(f"[Webhook Manager] Failed to get webhook from URL: {e}")
            
            # Clean up cache
            self._cleanup_webhook(webhook_url)
            
            return None
    
    def _cleanup_webhook(self, webhook_url: str):
        """Clean up webhook from all caches."""
        if webhook_url in self.webhook_cache:
            cached = self.webhook_cache[webhook_url]
            
            # Remove from webhook ID mapping
            if hasattr(cached.webhook, 'id') and cached.webhook.id in self.webhook_id_to_url:
                del self.webhook_id_to_url[cached.webhook.id]
            
            # Remove from thread mapping
            if cached.thread_id in self.thread_to_webhook:
                del self.thread_to_webhook[cached.thread_id]
            
            # Remove from main cache
            del self.webhook_cache[webhook_url]
    
    async def update_webhook_identity(self, webhook_url: str, user: discord.User) -> bool:
        """
        Update webhook to show user's real identity and refresh cache.
        """
        try:
            print(f"[Webhook Manager] Updating webhook identity for user: {user.display_name}")
            
            # Get fresh webhook (force fetch)
            webhook = await self.get_webhook(webhook_url, force_fetch=True)
            if not webhook:
                return False
            
            # Download user avatar
            avatar_bytes = None
            if user.avatar:
                async with aiohttp.ClientSession() as session:
                    async with session.get(str(user.avatar.url)) as response:
                        if response.status == 200:
                            avatar_bytes = await response.read()
            
            # Update the webhook - change name to user's display name
            await webhook.edit(
                name=user.display_name[:80],
                avatar=avatar_bytes,
                reason="Shadow Chat identity reveal"
            )
            
            # Update cache
            cached = self._get_cached_webhook(webhook_url)
            if cached:
                cached.avatar_key = None  # Not from our avatar pool
                cached.is_anonymous = False
                cached.user_id = user.id
                # Update with the edited webhook
                cached.webhook = webhook
            
            print(f"[Webhook Manager] Updated webhook identity: {user.display_name}")
            return True
            
        except Exception as e:
            print(f"[Webhook Manager] Failed to update webhook identity: {e}")
            return False
    
    async def reset_webhook_to_anonymous(self, webhook_url: str) -> bool:
        """Reset webhook to anonymous state with name "Shadow"."""
        webhook = await self.get_webhook(webhook_url)
        if not webhook:
            return False
        
        try:
            # Get a new random avatar
            avatar_key = self._get_next_avatar_key()
            avatar_bytes = self.avatar_cache.get(avatar_key) if avatar_key else None
            
            # ALWAYS reset to name "Shadow"
            webhook_name = "Shadow"
            
            # Update webhook
            await webhook.edit(
                name=webhook_name,
                avatar=avatar_bytes,
                reason="Shadow Chat reset to anonymous"
            )
            
            # Update cache
            cached = self._get_cached_webhook(webhook_url)
            if cached:
                cached.avatar_key = avatar_key
                cached.is_anonymous = True
                cached.user_id = None
            
            print(f"[Webhook Manager] Reset webhook to anonymous: Shadow")
            return True
        except Exception as e:
            print(f"[Webhook Manager] Failed to reset webhook: {e}")
            return False
    
    async def send_message(
        self,
        webhook_url: str,
        content: str,
        files: Optional[list] = None,
        embeds: Optional[list] = None,
        thread: Optional[discord.Thread] = None,
        wait: bool = True
    ) -> Optional[int]:
        """
        Send a message via webhook using cached webhook for speed.
        """
        # Try to get from cache first
        cached = self._get_cached_webhook(webhook_url)
        
        if not cached:
            # Not in cache, fetch it
            webhook = await self.get_webhook(webhook_url)
            if not webhook:
                return None
        else:
            # Use cached webhook
            webhook = cached.webhook
        
        try:
            # Prepare message
            has_content = bool(content and content.strip())
            has_files = bool(files)
            has_embeds = bool(embeds)
            
            if not has_content and not has_files and not has_embeds:
                return None
            
            # Send message (using cached webhook for speed)
            sent_msg = await webhook.send(
                content=content[:2000] if has_content else None,
                files=files or [],
                embeds=embeds[:10] if embeds else [],
                thread=thread,
                wait=wait
            )
            
            if sent_msg:
                return sent_msg.id
            
        except discord.NotFound:
            # Webhook was deleted, clean up cache
            print(f"[Webhook Manager] Webhook not found, cleaning cache: {webhook_url[:50]}...")
            self._cleanup_webhook(webhook_url)
            return None
        except Exception as e:
            print(f"[Webhook Manager] Webhook send error: {e}")
            # Try to refresh cache
            if cached:
                self._cleanup_webhook(webhook_url)
        
        return None
    
    async def relay_message(
        self,
        from_thread: discord.Thread,
        to_webhook_url: str,
        message: discord.Message,
        *,
        reply_to_mirrored_id: Optional[int] = None
    ) -> Tuple[Optional[int], Optional[str]]:
        """
        Fast message relaying using cached webhooks.
        """
        # Process attachments
        files = []
        if message.attachments:
            for attachment in message.attachments:
                try:
                    file_bytes = await attachment.read()
                    file = discord.File(
                        io.BytesIO(file_bytes),
                        filename=attachment.filename
                    )
                    files.append(file)
                except Exception as e:
                    print(f"[Webhook Manager] Failed to process attachment: {e}")
                    continue
        
        # Get embeds
        embeds = []
        
        # Check if this is a reply
        if message.reference and message.reference.message_id:
            try:
                replied_msg = await message.channel.fetch_message(message.reference.message_id)
                if replied_msg:
                    # Get reply content
                    reply_content = replied_msg.content
                    
                    if not reply_content:
                        if replied_msg.stickers:
                            sticker_name = replied_msg.stickers[0].name if hasattr(replied_msg.stickers[0], 'name') else "Sticker"
                            reply_content = f"📎 {sticker_name}"
                        elif replied_msg.attachments:
                            file_name = replied_msg.attachments[0].filename
                            reply_content = f"📎 {file_name}"
                        else:
                            reply_content = "Click to view message"

                    # Create reply embed
                    reply_embed = discord.Embed(
                        description=f"**Replying to:** {reply_content[:100]}{'...' if len(reply_content) > 100 else ''}",
                        color=0x3498db
                    )
                    
                    # Use the author of the message we are replying to
                    if hasattr(replied_msg, 'author') and replied_msg.author:
                        reply_embed.set_author(
                            name=f"↪️ {replied_msg.author.display_name}",
                            icon_url=replied_msg.author.display_avatar.url
                        )
                    
                    embeds.append(reply_embed)
            except Exception as e:
                print(f"[Webhook Manager] Could not fetch replied message: {e}")
        
        # Add existing embeds
        if hasattr(message, 'embeds') and message.embeds:
            embeds.extend(message.embeds)
        
        # Get content
        content = getattr(message, 'content', '')
        if content is None:
            content = ''
        
        # Handle stickers
        if message.stickers:
            for sticker in message.stickers:
                try:
                    if sticker.url:
                        embed = discord.Embed()
                        embed.set_image(url=sticker.url)
                        embeds.append(embed)
                        if not content:
                            content = f"📎 {sticker.name or 'Sticker'}"
                except Exception:
                    pass
        
        # Send it using cached webhook
        mirrored_id = await self.send_message(
            webhook_url=to_webhook_url,
            content=content,
            files=files,
            embeds=embeds,
            thread=from_thread
        )
        
        if mirrored_id:
            return mirrored_id, None
        else:
            return None, "Failed to send"
    
    async def get_webhook_for_thread(self, thread_id: int) -> Optional[str]:
        """Get webhook URL for a thread."""
        return self.thread_to_webhook.get(thread_id)
    
    async def check_webhook_valid(self, webhook_url: str) -> bool:
        """Check if webhook is valid."""
        cached = self._get_cached_webhook(webhook_url)
        if cached:
            return True
        
        # Try to fetch it
        webhook = await self.get_webhook(webhook_url)
        return webhook is not None
    
    async def delete_webhook(self, webhook_url: str) -> bool:
        """Delete a webhook and clean up cache."""
        webhook = await self.get_webhook(webhook_url)
        if not webhook:
            return False
        
        try:
            await webhook.delete(reason="Shadow Chat session ended")
            
            # Clean up from cache
            self._cleanup_webhook(webhook_url)
            
            print(f"[Webhook Manager] Deleted webhook: {webhook_url[:50]}...")
            return True
        except Exception as e:
            print(f"[Webhook Manager] Failed to delete webhook: {e}")
            # Still clean up cache even if delete fails
            self._cleanup_webhook(webhook_url)
            return False
    
    async def flush_cache(self):
        """Manually flush cache (can be called anytime)."""
        self._flush_cache()