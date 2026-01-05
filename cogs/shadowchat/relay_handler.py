"""
SHADOW CHAT - RELAY HANDLER
Handles message relaying between session threads.
"""

import discord
import re
from typing import Optional, Tuple, List
from .models import Database, ShadowSession
from .session_manager import SessionManager
from .webhook_manager import WebhookManager
from .constants import GUILD_ID 
from .utils import get_thread_by_id  # Import this utility

class RelayHandler:
    """Handles message relaying between threads."""
    
    def __init__(self, bot, db: Database, session_manager: SessionManager, webhook_manager: WebhookManager):
        self.bot = bot
        self.db = db
        self.session_manager = session_manager
        self.webhook_manager = webhook_manager
        
        # Words to censor (case-insensitive)
        self.censored_words = {
            'aad', 'aand', 'bahenchod', 'behenchod', 'bhenchod', 'bhenchodd', 
            'bakchod', 'bakchodd', 'bakchodi'
            'bevdey', 'bewday', 'bevakoof', 'bevkoof', 'bevkuf', 'bewakoof', 
            'bewkoof', 'bewkuf', 'bhadua', 'bhaduaa', 'bhadva', 'bhadvaa', 
            'bhadwa', 'bhadwaa', 'bhosada', 'bhosda', 'bhosdaa', 'bhosdike', 
            'bhonsdike', 'bsdk', 'b.s.d.k', 'bhosdiki', 'bhosdiwala', 'bhosdiwale', 
            'bhosadchodal', 'bhosadchod', 'babbe', 'babbey', 'bube', 'bubey', 
            'bur', 'burr', 'buurr', 'buur', 'charsi', 'chooche', 'choochi', 
            'chuchi', 'chhod', 'chod', 'chodd', 'chudne', 'chudney', 'chudwa', 
            'chudwaa', 'chudwane', 'chudwaane', 'choot', 'chut', 'chute', 
            'chutia', 'chutiya', 'chutiye',  'gadhalund', 
            'gaand', 'gand', 'gandu', 'gandfat', 'gandfut', 'gandiya', 'gandiye', 
            'gote', 'gotey', 'gotte', 'hag', 'haggu', 'hagne', 
            'hagney', 'harami', 'haramjada', 'haraamjaada', 'haramzyada', 
            'haraamzyaada', 'haraamjaade', 'haraamzaade', 'haraamkhor', 
            'haramkhor', 
            'landi', 'landy', 
            'laude', 'laudey', 'laura', 'lora', 'lauda', 'ling', 'loda', 'lode', 
            'lund', 'launda', 'lounde', 'laundey', 'laundi', 'loundi', 'laundiya', 
            'loundiya', 'lulli', 'maar', 'maro', 'marunga', 'madarchod', 
            'madarchodd', 'madarchood', 'madarchoot', 'madarchut', 
            'mamme', 'mammey', 'moot', 'mut', 'mootne', 'mutne', 'mooth', 'muth', 
            'nunni', 'nunnu', 'paaji', 'paji', 'pesaab', 'pesab', 'peshaab', 
            'peshab', 'pilla', 'pillay', 'pille', 'pilley', 'pisaab', 'pisab', 
            'pkmkb', 'porkistan', 'raand', 'rand', 'randi', 'randy', 
            'tatte',
            
            # Original censored words
            'asshole', 'bitch', 'dick', 'pussy', 'cunt',
            'retard', ' faggot', 'whore', 'slut',
            'kill', 'suicide', 'rape', 'pedo', 'incest', 'madarchod'
        }
        
        # Create regex pattern for word boundaries
        self.censor_pattern = re.compile(
            r'\b(' + '|'.join(re.escape(word) for word in self.censored_words) + r')\b',
            re.IGNORECASE
        )
    
    def censor_message(self, content: str) -> str:
        """
        Censor inappropriate words in message content.
        Returns censored message.
        """
        if not content:
            return content
            
        # Replace censored words with "xxxxxx" (same length as "censored")
        def replace_word(match):
            matched_word = match.group(0)
            # Replace with x's of same length
            return 'x' * len(matched_word)
        
        censored_content = self.censor_pattern.sub(replace_word, content)
        
        # Also check for variations with symbols (e.g., f*ck, sh1t, etc.)
        variations = [
            (r's[!@#$%^&*]*h[!@#$%^&*]*i[!@#$%^&*]*t', 'shit'),
            (r'a[!@#$%^&*]*s[!@#$%^&*]*s', 'ass'),
        ]
        
        for pattern, original_word in variations:
            # Replace variations with x's of original word length
            def replace_variation(match):
                return 'x' * len(original_word)
            
            censored_content = re.sub(pattern, replace_variation, censored_content, flags=re.IGNORECASE)
        
        # If message was censored, add a note
        if censored_content != content:
            # You could optionally add something like:
            # censored_content += "\n\n⚠️ *Some words were censored for safety*"
            pass
            
        return censored_content
    
    async def handle_message(self, message: discord.Message) -> Tuple[bool, Optional[str]]:
        """Handle and relay a message from a session thread."""
        try:
            #print(f"[DEBUG] Handling message in thread {message.channel.id}")
            
            # Get session for this thread
            session = await self.session_manager.get_session_by_thread(message.channel.id)
            if not session:
                print(f"[DEBUG] No session found for thread {message.channel.id}")
                return False, "Not in an active session."
            

            
            # Check if session has valid webhooks
            if not session.webhook1_url or not session.webhook2_url:
                print(f"[DEBUG] ERROR: Session missing webhooks!")
                return False, "Session configuration error (missing webhooks)."
            
            # Update session activity
            await self.session_manager.update_session_activity(session.session_id)
            
            # Determine which user sent the message
            is_user1 = message.author.id == session.user1_id
            is_user2 = message.author.id == session.user2_id
            
            if not (is_user1 or is_user2):
                return False, "You're not in this session."
            
            
            # Determine target webhook (OPPOSITE user)
            if is_user1:
                # User 1 sent message → should go to User 2's thread
                target_webhook_url = session.webhook2_url  # Webhook in User 2's thread
                from_thread_id = session.thread1_id
                to_thread_id = session.thread2_id
                #print(f"[DEBUG] User1 → User2: Using webhook2 in thread2")
            else:
                # User 2 sent message → should go to User 1's thread
                target_webhook_url = session.webhook1_url  # Webhook in User 1's thread
                from_thread_id = session.thread2_id
                to_thread_id = session.thread1_id
                #print(f"[DEBUG] User2 → User1: Using webhook1 in thread1")
            
            # Get thread objects using the utility function
            guild = self.bot.get_guild(GUILD_ID)
            if not guild:
                return False, "Guild not found."
            
            from_thread = await get_thread_by_id(guild, from_thread_id)
            to_thread = await get_thread_by_id(guild, to_thread_id)
            
            if not from_thread or not to_thread:
                return False, "Thread not found."
            
            #print(f"[DEBUG] Message routing:")
            #print(f"  From thread: {from_thread.name} ({from_thread.id}) - User's own thread")
            #print(f"  To thread: {to_thread.name} ({to_thread.id}) - Partner's thread")
            #print(f"  Using webhook: {target_webhook_url[:50]}...")
            
            # Check for reply
            reply_to_mirrored_id = None
            # In the handle_message method, update the reply detection section:
            if message.reference and message.reference.message_id:
                # Get original message ID that was replied to
                original_message_id = message.reference.message_id
                #print(f"[RELAY DEBUG] Reply detected to message ID: {original_message_id}")
                
                # Try to find the mapping in both directions
                # First, try to find where this message is the mirrored message (looking for original)
                mapping = await self.db.get_message_mapping_by_mirrored(
                    session.session_id,
                    from_thread_id,  # The thread where the reply was made
                    original_message_id  # The mirrored message being replied to
                )
                
                # If not found, try the other direction (where this message is the original)
                if not mapping:
                    mapping = await self.db.get_message_mapping_by_original(
                        session.session_id,
                        from_thread_id,  # The thread where the reply was made
                        original_message_id  # The original message being replied to
                    )
                
                if mapping and isinstance(mapping, tuple):
                    # Determine which direction we found
                    # If we found by mirrored, we need to get the original_message_id from the mapping
                    # If we found by original, we need to get the mirrored_id from the mapping
                    
                    # Check if the mapping's from_thread_id matches our from_thread_id
                    if mapping[2] == from_thread_id:  # from_thread_id at index 2
                        # This means we found a mapping where this thread is the source
                        # So the reply target is the mirrored_id in the other thread
                        reply_to_mirrored_id = mapping[5]  # mirrored_id is at index 5
                        #print(f"[RELAY DEBUG] Found mapping (original → mirrored): {mapping[4]} → {reply_to_mirrored_id}")
                    else:
                        # This means we found a mapping where this thread is the destination
                        # So the reply target is the original_message_id in the other thread
                        reply_to_mirrored_id = mapping[4]  # original_message_id is at index 4
                        #print(f"[RELAY DEBUG] Found mapping (mirrored → original): {original_message_id} → {reply_to_mirrored_id}")
                else:
                    print(f"[RELAY DEBUG] No mapping found for reply message {original_message_id} in session {session.session_id}")
                    # Log more details for debugging
                    #print(f"[RELAY DEBUG] Session: {session.session_id}, Thread: {from_thread_id}")
            
            # Create a modified message object with censored content
            class CensoredMessage:
                def __init__(self, original_message, censored_content):
                    self.content = censored_content
                    self.author = original_message.author
                    self.attachments = original_message.attachments
                    self.embeds = original_message.embeds
                    self.reference = original_message.reference
                    self.stickers = original_message.stickers
                    self.id = original_message.id
            
            # Censor the message content
            censored_content = self.censor_message(message.content)
            
            # Only create censored message if content actually changed
            if censored_content != message.content:
                print(f"[CENSORSHIP] Original: {message.content[:50]}...")
                print(f"[CENSORSHIP] Censored: {censored_content[:50]}...")
                message_to_send = CensoredMessage(message, censored_content)
            else:
                message_to_send = message
            
            #print(f"[DEBUG] Calling webhook_manager.relay_message()...")
            
            # Relay the message
            result = await self.webhook_manager.relay_message(
                thread=to_thread,  # This should be the thread to send to (partner's thread)
                webhook_url=target_webhook_url,
                message=message_to_send,
                reply_to_mirrored_id=reply_to_mirrored_id
                # Note: We're NOT passing files parameter - relay_message will create them from attachments
            )
            
            #print(f"[DEBUG] relay_message() returned: {result}")
            
            # Check if result is a tuple
            if isinstance(result, tuple) and len(result) == 2:
                mirrored_id, error = result
            else:
                print(f"[ERROR] relay_message() did not return a tuple. Got: {result}")
                return False, "Internal relay error"
            
            if mirrored_id:
                # Store mapping for future replies
                await self.db.store_message_mapping(
                    session.session_id,
                    from_thread_id,
                    to_thread_id,
                    message.id,
                    mirrored_id
                )
                #print(f"[DEBUG] Stored mapping: {message.id} → {mirrored_id}")
                return True, None
            else:
                print(f"[DEBUG] Relay failed with error: {error}")
                return False, error or "Failed to send message"
                
        except Exception as e:
            print(f"[Shadow Chat] Message handling error: {e}")
            import traceback
            traceback.print_exc()
            return False, "Internal error"