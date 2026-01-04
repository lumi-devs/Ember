"""
SHADOW CHAT - WEBHOOK MANAGER (STATIC URLS VERSION)
Uses pre-provided webhook URLs instead of creating new ones.
"""

import asyncio
import discord
import io
import aiohttp
import random
from typing import Optional, Dict, Tuple, List
from . import constants
import os

class ManagedWebhook:
    """Represents a managed webhook with session state."""
    
    def __init__(self, url: str, webhook: discord.Webhook):
        self.url = url
        self.webhook = webhook
        self.original_name = "Shadow"  # Default anonymous name
        self.original_avatar_bytes = None
        self.current_avatar_bytes = None
        self.assigned_session_id: Optional[str] = None
        self.is_anonymous = True
        self.revealed_user: Optional[discord.User] = None
        self.assigned_avatar_key: Optional[str] = None
        
    async def reset_to_anonymous(self, avatar_bytes: Optional[bytes] = None):
        """Reset webhook to anonymous state."""
        try:
            await self.webhook.edit(
                name="Shadow",
                avatar=avatar_bytes,
                reason="Reset to anonymous for new session"
            )
            self.is_anonymous = True
            self.revealed_user = None
            self.current_avatar_bytes = avatar_bytes
            #print(f"[Webhook Manager] Reset webhook {self.url[:50]}... to anonymous")
        except Exception as e:
            print(f"[Webhook Manager] Failed to reset webhook {self.url[:50]}...: {e}")
            # If webhook is dead, mark it as unusable
            raise

class WebhookManager:
    """Manages webhooks using provided static URLs."""
    
    def __init__(self, bot):
        self.bot = bot
        self.session = aiohttp.ClientSession()
        
        # Configuration: Add your webhook URLs here
        self.provided_webhook_urls = [
            "***WEBHOOK_REMOVED***",
            "***WEBHOOK_REMOVED***",
            "***WEBHOOK_REMOVED***",
            "***WEBHOOK_REMOVED***",
            "***WEBHOOK_REMOVED***",
            "***WEBHOOK_REMOVED***",
            "***WEBHOOK_REMOVED***",
            "***WEBHOOK_REMOVED***",
            "***WEBHOOK_REMOVED***",
            "***WEBHOOK_REMOVED***",
            "***WEBHOOK_REMOVED***",
            "***WEBHOOK_REMOVED***",
            "***WEBHOOK_REMOVED***",
            "***WEBHOOK_REMOVED***",
            "***WEBHOOK_REMOVED***"

        ]
        
        # Active webhooks
        self.managed_webhooks: Dict[str, ManagedWebhook] = {}  # url -> ManagedWebhook
        self.available_webhooks: List[ManagedWebhook] = []  # Webhooks not in use
        self.session_webhooks: Dict[str, Tuple[ManagedWebhook, ManagedWebhook]] = {}  # session_id -> (webhook1, webhook2)
        
        # Avatar management
        self.avatar_cache: Dict[str, bytes] = {}
        self.avatar_keys: List[str] = []
        
        # Locks
        self._lock = asyncio.Lock()
        
        print(f"[Webhook Manager] Initialized with {len(self.provided_webhook_urls)} provided webhook URLs")
    
    async def initialize(self):
        """Initialize webhook manager with provided URLs."""
        print("[Webhook Manager] Initializing with provided webhook URLs...")
        
        # Load avatars
        await self._load_avatars()
        
        # Initialize all provided webhooks
        for url in self.provided_webhook_urls:
            try:
                webhook = discord.Webhook.from_url(
                    url,
                    session=self.session,
                    bot_token=self.bot.http.token
                )
                
                # Test if webhook is valid
                try:
                    await webhook.fetch()
                except discord.NotFound:
                    print(f"[Webhook Manager] Webhook not found or invalid: {url[:50]}...")
                    continue
                
                managed = ManagedWebhook(url, webhook)
                self.managed_webhooks[url] = managed
                self.available_webhooks.append(managed)
                
                print(f"[Webhook Manager] ✓ Loaded webhook: {url[:50]}...")
                
            except Exception as e:
                print(f"[Webhook Manager] ✗ Failed to load webhook {url[:50]}...: {e}")
        
        print(f"[Webhook Manager] Successfully loaded {len(self.available_webhooks)}/{len(self.provided_webhook_urls)} webhooks")
        
        if len(self.available_webhooks) < 2:
            print("[Webhook Manager] WARNING: Need at least 2 webhooks to function properly!")
    
    async def _load_avatars(self):
        """Load avatar images from assets folder."""
        base_dir = os.path.dirname(os.path.abspath(__file__))
        avatar_dir = os.path.join(base_dir, "assets")
        
        if not os.path.exists(avatar_dir):
            print(f"[Webhook Manager] WARNING: 'assets' folder not found: {avatar_dir}")
            # Create a default anonymous avatar
            default_avatar = self._create_default_avatar()
            if default_avatar:
                self.avatar_cache["default"] = default_avatar
                self.avatar_keys.append("default")
            return
        
        valid_extensions = ('.png', '.jpg', '.jpeg', '.gif', '.webp')
        
        for filename in os.listdir(avatar_dir):
            if filename.lower().endswith(valid_extensions):
                file_path = os.path.join(avatar_dir, filename)
                try:
                    with open(file_path, 'rb') as f:
                        avatar_bytes = f.read()
                    self.avatar_cache[filename] = avatar_bytes
                    self.avatar_keys.append(filename)
                    print(f"[Webhook Manager] ✓ Loaded avatar: {filename}")
                except Exception as e:
                    print(f"[Webhook Manager] ✗ Failed to load avatar {filename}: {e}")
        
        print(f"[Webhook Manager] Loaded {len(self.avatar_keys)} avatars")
    
    def _create_default_avatar(self) -> bytes:
        """Create a simple default avatar in memory."""
        # You could use PIL to create a simple colored circle
        # For now, return None and we'll handle it in the code
        return None
    
    def _get_random_avatar_bytes(self) -> Optional[bytes]:
        """Get random avatar bytes."""
        if not self.avatar_keys:
            return None
        
        key = random.choice(self.avatar_keys)
        return self.avatar_cache.get(key)
    
    async def assign_webhooks_for_session(self, session_id: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Assign two webhooks to a new session.
        Returns: (webhook1_url, webhook2_url) or (None, None) if not enough webhooks
        """
        async with self._lock:
            if len(self.available_webhooks) < 2:
                print(f"[Webhook Manager] Not enough available webhooks for session {session_id}")
                return None, None
            
            # Get two random available webhooks
            webhook1 = self.available_webhooks.pop(random.randrange(len(self.available_webhooks)))
            webhook2 = self.available_webhooks.pop(random.randrange(len(self.available_webhooks)))
            
            # Assign random avatars
            avatar1_bytes = self._get_random_avatar_bytes()
            avatar2_bytes = self._get_random_avatar_bytes()
            
            # Reset webhooks to anonymous state with random avatars
            await webhook1.reset_to_anonymous(avatar1_bytes)
            await webhook2.reset_to_anonymous(avatar2_bytes)
            
            # Store session assignment
            webhook1.assigned_session_id = session_id
            webhook2.assigned_session_id = session_id
            self.session_webhooks[session_id] = (webhook1, webhook2)
            
            print(f"[Webhook Manager] Assigned webhooks to session {session_id}")
            return webhook1.url, webhook2.url
    
    async def get_webhook_for_message(self, webhook_url: str, thread: discord.Thread) -> Optional[ManagedWebhook]:
        """Get a webhook for sending a message to a specific thread."""
        managed = self.managed_webhooks.get(webhook_url)
        if not managed:
            print(f"[Webhook Manager] Webhook not found: {webhook_url[:50]}...")
            return None
        
        # The webhook object is already loaded, we just need to use it
        return managed
    
    async def relay_message(
        self,
        thread: discord.Thread,
        webhook_url: str,
        message: discord.Message,
        reply_to_mirrored_id: Optional[int] = None
    ) -> Tuple[Optional[int], Optional[str]]:
        """
        Relay a message using the assigned webhook.
        """
        try:
            # Get managed webhook
            managed = self.managed_webhooks.get(webhook_url)
            if not managed:
                return None, "Webhook not found"
            
            # Prepare message content
            content = message.content
            if not content and not message.attachments and not message.embeds and not message.stickers:
                return None, "Empty message"
            
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
            
            # Prepare embeds
            embeds = []
            if hasattr(message, 'embeds') and message.embeds:
                embeds.extend(message.embeds)
            
            # Handle stickers
            if message.stickers:
                for sticker in message.stickers:
                    try:
                        if sticker.url:
                            embed = discord.Embed()
                            embed.set_image(url=sticker.url)
                            embeds.append(embed)
                    except Exception:
                        pass
            
            # Determine webhook name and avatar
            username = "Shadow"  # Default anonymous name
            avatar_url = None
            
            # If webhook is revealed, use real user info
            if not managed.is_anonymous and managed.revealed_user:
                username = managed.revealed_user.display_name
                avatar_url = str(managed.revealed_user.avatar.url) if managed.revealed_user.avatar else None
            
            # Send message
            try:
                sent_msg = await managed.webhook.send(
                    content=content[:2000] if content else None,
                    username=username[:80],
                    avatar_url=avatar_url,
                    files=files,
                    embeds=embeds[:10],
                    thread=thread,
                    wait=True
                )
                
                return sent_msg.id, None
                
            except discord.NotFound:
                print(f"[Webhook Manager] Webhook deleted or invalid: {webhook_url[:50]}...")
                # Mark this webhook as unusable
                await self._mark_webhook_unusable(webhook_url)
                return None, "Webhook no longer exists"
                
        except Exception as e:
            print(f"[Webhook Manager] Relay error: {e}")
            return None, str(e)
    
    async def _mark_webhook_unusable(self, webhook_url: str):
        """Mark a webhook as unusable and clean up."""
        async with self._lock:
            if webhook_url in self.managed_webhooks:
                managed = self.managed_webhooks[webhook_url]
                
                # Remove from available list if present
                if managed in self.available_webhooks:
                    self.available_webhooks.remove(managed)
                
                # Clean up session assignments
                if managed.assigned_session_id:
                    session_id = managed.assigned_session_id
                    if session_id in self.session_webhooks:
                        webhook1, webhook2 = self.session_webhooks[session_id]
                        if webhook1 == managed or webhook2 == managed:
                            del self.session_webhooks[session_id]
                
                del self.managed_webhooks[webhook_url]
    
    async def reveal_identities(self, session_id: str, user1: discord.User, user2: discord.User) -> bool:
        """
        Reveal identities in a session - swap webhook appearances.
        """
        async with self._lock:
            if session_id not in self.session_webhooks:
                print(f"[Webhook Manager] Session {session_id} not found for reveal")
                return False
            
            webhook1, webhook2 = self.session_webhooks[session_id]
            
            try:
                # Download avatars
                avatar1_bytes = None
                avatar2_bytes = None
                
                if user1.avatar:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(str(user1.avatar.url)) as response:
                            if response.status == 200:
                                avatar1_bytes = await response.read()
                
                if user2.avatar:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(str(user2.avatar.url)) as response:
                            if response.status == 200:
                                avatar2_bytes = await response.read()
                
                # SWAP identities:
                # Webhook 1 (shows in user1's thread) should display user2's identity
                await webhook1.webhook.edit(
                    name=user2.display_name[:80],
                    avatar=avatar2_bytes,
                    reason="Identity reveal - showing partner"
                )
                webhook1.is_anonymous = False
                webhook1.revealed_user = user2
                
                # Webhook 2 (shows in user2's thread) should display user1's identity
                await webhook2.webhook.edit(
                    name=user1.display_name[:80],
                    avatar=avatar1_bytes,
                    reason="Identity reveal - showing partner"
                )
                webhook2.is_anonymous = False
                webhook2.revealed_user = user1
                
                print(f"[Webhook Manager] Identities revealed for session {session_id}:")
                print(f"  Webhook 1 now shows: {user2.display_name}")
                print(f"  Webhook 2 now shows: {user1.display_name}")
                
                return True
                
            except Exception as e:
                print(f"[Webhook Manager] Failed to reveal identities: {e}")
                return False
    
    async def end_session(self, session_id: str):
        """
        Clean up session and reset webhooks for reuse.
        """
        async with self._lock:
            if session_id not in self.session_webhooks:
                return
            
            webhook1, webhook2 = self.session_webhooks[session_id]
            
            # Reset both webhooks to anonymous
            avatar1_bytes = self._get_random_avatar_bytes()
            avatar2_bytes = self._get_random_avatar_bytes()
            
            await webhook1.reset_to_anonymous(avatar1_bytes)
            await webhook2.reset_to_anonymous(avatar2_bytes)
            
            # Clear session assignment
            webhook1.assigned_session_id = None
            webhook2.assigned_session_id = None
            
            # Return webhooks to available pool
            if webhook1 not in self.available_webhooks:
                self.available_webhooks.append(webhook1)
            if webhook2 not in self.available_webhooks:
                self.available_webhooks.append(webhook2)
            
            # Remove session mapping
            del self.session_webhooks[session_id]
            
            print(f"[Webhook Manager] Session {session_id} ended, webhooks reset and returned to pool")
    
    async def close(self):
        """Clean up resources."""
        await self.session.close()