import discord
from discord.ext import commands, tasks
import aiosqlite
import random
import re
import asyncio
import logging
from collections import deque
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import emoji
from typing import List, Tuple, Optional
import nltk
from nltk.tokenize import word_tokenize, sent_tokenize
from nltk import pos_tag
from nltk.corpus import stopwords
import string

# Try to download NLTK data
try:
    nltk.data.find('tokenizers/punkt')
    nltk.data.find('taggers/averaged_perceptron_tagger')
    nltk.data.find('corpora/stopwords')
except LookupError:
    nltk.download('punkt', quiet=True)
    nltk.download('averaged_perceptron_tagger', quiet=True)
    nltk.download('stopwords', quiet=True)

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('./genai/markov_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("EnhancedMarkovBot")

class EnhancedMarkovCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_path = "./genai/server_brain.db"
        self.state_file = "./genai/bot_state.json"
        self.stop_words = set(stopwords.words('english'))
        
        # INITIALIZATION FLAGS
        self.db_initialized = False
        self.user_cache = {}
        self.user_id_map = {}
        
        # Initialize stats FIRST
        self.stats = {
            'messages_processed': 0,
            'triplets_stored_today': 0,
            'replies_generated': 0,
            'last_flush': datetime.utcnow().isoformat(),
            'total_triplets': 0,
            'total_messages': 0,
            'startup_time': datetime.utcnow().isoformat(),
            'unique_users': 0,
            'emojis_learned': 0,
            'rate_limited': 0,
            'spam_blocked': 0
        }
        
        # Initialize other tracking dictionaries
        self.message_timestamps = {}
        self.last_bot_message = {}
        self.reply_cooldowns = {}
        self.message_buffer = deque(maxlen=1000)
        self.recent_messages = deque(maxlen=5000)
        
        # NOW load saved state (which will update stats if they exist)
        self.load_state()
        
        # Enhanced limits with emoji control
        self.MAX_EMOJIS_PER_MESSAGE = 3  # Limit emojis learned per message
        self.MAX_EMOJI_DENSITY = 0.3     # Max 30% of message can be emojis
        
        # ANTI-SPAM PROTECTION
        self.MIN_MESSAGE_INTERVAL = 0.5  # Minimum 0.5 seconds between bot messages in same channel
        self.MAX_MESSAGES_PER_MINUTE = 80  # Max 80 messages per minute per channel
        self.SPAM_BURST_WINDOW = 10       # Time window for burst detection (seconds)
        self.SPAM_BURST_LIMIT = 5         # Max messages in burst window (increased from 2)
        
        # Limits optimized for high traffic (17k/day)
        self.MAX_RAW_MESSAGES = 2_000_000
        self.MAX_TRIPLETS = 5_000_000
        self.RAW_MESSAGE_RETENTION_DAYS = 120
        self.PRUNE_OLD_TRIPLETS_AFTER_DAYS = 365
        
        # --- OPTIMIZED PARAMETERS ---
        self.MIN_TRIPLETS_FOR_REPLY = 500
        self.REPLY_CHANCE = 0.015
        self.REPLY_COOLDOWN = 10
        self.TYPING_MIN_SECONDS = 0.3
        self.TYPING_MAX_SECONDS = 0.8
        self.SENTENCE_MAX_WORDS = 30
        self.SEED_WORD_MIN_LENGTH = 3
        self.MIN_MESSAGE_WORDS = 3
        
        # Emoji reply control
        self.MAX_REPLY_EMOJIS = 2
        self.EMOJI_REPLY_CHANCE = 0.1  # 10% chance to include emoji in reply
        
        self.FLUSH_THRESHOLD = 50
        
        # Start background tasks
        self.bot.loop.create_task(self.initialize_database())
        self.periodic_tasks.start()
        self.cleanup_rate_limits.start()

    def load_state(self):
        """Load bot state from file to survive restarts"""
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r') as f:
                    state = json.load(f)
                
                # Safely update stats if they exist in state
                if 'stats' in state:
                    for key, value in state['stats'].items():
                        if key in self.stats:
                            # Handle different types properly
                            if isinstance(self.stats[key], int):
                                self.stats[key] = int(value)
                            elif isinstance(self.stats[key], str):
                                self.stats[key] = str(value)
                            else:
                                self.stats[key] = value
                
                # Load cooldowns with type safety
                if 'reply_cooldowns' in state:
                    for k, v in state['reply_cooldowns'].items():
                        try:
                            channel_id = int(k)
                            if isinstance(v, str):
                                self.reply_cooldowns[channel_id] = datetime.fromisoformat(v)
                        except (ValueError, TypeError) as e:
                            logger.warning(f"Failed to load cooldown for channel {k}: {e}")
                
                logger.info(f"Loaded state with {len(self.reply_cooldowns)} cooldowns")
                
        except Exception as e:
            logger.error(f"Failed to load state: {e}")
            # Don't fail completely, just log the error

    def save_state(self):
        """Save bot state to file"""
        try:
            state = {
                'stats': self.stats,
                'reply_cooldowns': {
                    str(k): v.isoformat() if isinstance(v, datetime) else v
                    for k, v in self.reply_cooldowns.items()
                },
                'last_saved': datetime.utcnow().isoformat()
            }
            
            # Ensure directory exists
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            
            with open(self.state_file, 'w') as f:
                json.dump(state, f, indent=2)
                
            logger.debug("State saved successfully")
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    async def initialize_database(self):
        """Initialize database with connection pool"""
        await self.bot.wait_until_ready()
        
        try:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            
            async with aiosqlite.connect(self.db_path) as db:
                # Optimize SQLite settings
                await db.execute('PRAGMA journal_mode=WAL;')
                await db.execute('PRAGMA synchronous=NORMAL;')
                await db.execute('PRAGMA cache_size=-10000;')
                await db.execute('PRAGMA temp_store=MEMORY;')
                await db.execute('PRAGMA mmap_size=268435456;')
                await db.execute('PRAGMA optimize;')
                
                # Create tables
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS markov_chain (
                        word_a TEXT NOT NULL,
                        word_b TEXT NOT NULL,
                        next_word TEXT NOT NULL,
                        count INTEGER DEFAULT 1,
                        last_used TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        word_type TEXT DEFAULT 'normal',
                        PRIMARY KEY (word_a, word_b, next_word)
                    ) WITHOUT ROWID
                ''')
                
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS message_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        content TEXT NOT NULL,
                        author_id INTEGER,
                        channel_id INTEGER,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                        processed BOOLEAN DEFAULT 0,
                        emoji_count INTEGER DEFAULT 0,
                        is_bot_reply BOOLEAN DEFAULT 0
                    )
                ''')
                
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS user_stats (
                        user_id INTEGER PRIMARY KEY,
                        username TEXT,
                        message_count INTEGER DEFAULT 0,
                        last_seen TIMESTAMP,
                        emoji_usage INTEGER DEFAULT 0
                    )
                ''')
                
                # Create indexes
                await db.execute('''
                    CREATE INDEX IF NOT EXISTS idx_markov_lookup 
                    ON markov_chain(word_a, word_b, count DESC)
                ''')
                
                await db.execute('''
                    CREATE INDEX IF NOT EXISTS idx_markov_type 
                    ON markov_chain(word_type)
                ''')
                
                await db.execute('''
                    CREATE INDEX IF NOT EXISTS idx_message_log_timestamp 
                    ON message_log(timestamp)
                ''')
                
                await db.execute('''
                    CREATE INDEX IF NOT EXISTS idx_message_log_processed 
                    ON message_log(processed)
                ''')
                
                await db.commit()
                
                # Get initial counts
                async with db.execute("SELECT COUNT(*) FROM markov_chain") as cursor:
                    result = await cursor.fetchone()
                    self.stats['total_triplets'] = result[0] if result else 0
                
                async with db.execute("SELECT COUNT(*) FROM message_log") as cursor:
                    result = await cursor.fetchone()
                    self.stats['total_messages'] = result[0] if result else 0
                
                async with db.execute("SELECT COUNT(DISTINCT author_id) FROM message_log") as cursor:
                    result = await cursor.fetchone()
                    self.stats['unique_users'] = result[0] if result else 0
                
                logger.info(f"Database initialized. Existing: {self.stats['total_triplets']} triplets, {self.stats['total_messages']} messages from {self.stats['unique_users']} users")
                self.db_initialized = True
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            raise

    # --- ANTI-SPAM FUNCTIONS ---
    def check_rate_limit(self, channel_id: int) -> bool:
        """Check if bot should send a message based on rate limits"""
        now = datetime.utcnow()
        
        # Initialize channel tracking if not exists
        if channel_id not in self.message_timestamps:
            self.message_timestamps[channel_id] = deque(maxlen=self.MAX_MESSAGES_PER_MINUTE * 2)
        
        if channel_id not in self.last_bot_message:
            self.last_bot_message[channel_id] = now - timedelta(seconds=self.MIN_MESSAGE_INTERVAL + 1)
        
        # Check minimum interval
        time_since_last = (now - self.last_bot_message[channel_id]).total_seconds()
        if time_since_last < self.MIN_MESSAGE_INTERVAL:
            logger.debug(f"Rate limited: {time_since_last:.2f}s since last message in channel {channel_id}")
            self.stats['rate_limited'] += 1
            return False
        
        # Check burst limit
        timestamps = self.message_timestamps[channel_id]
        recent_messages = sum(1 for ts in timestamps 
                            if (now - ts).total_seconds() < self.SPAM_BURST_WINDOW)
        
        if recent_messages >= self.SPAM_BURST_LIMIT:
            logger.debug(f"Spam burst detected: {recent_messages} messages in {self.SPAM_BURST_WINDOW}s")
            self.stats['spam_blocked'] += 1
            return False
        
        # Check messages per minute
        minute_ago = now - timedelta(minutes=1)
        messages_last_minute = sum(1 for ts in timestamps if ts > minute_ago)
        
        if messages_last_minute >= self.MAX_MESSAGES_PER_MINUTE:
            logger.debug(f"Max messages per minute reached: {messages_last_minute}")
            self.stats['rate_limited'] += 1
            return False
        
        return True

    def record_bot_message(self, channel_id: int):
        """Record that bot sent a message"""
        now = datetime.utcnow()
        self.last_bot_message[channel_id] = now
        if channel_id not in self.message_timestamps:
            self.message_timestamps[channel_id] = deque(maxlen=self.MAX_MESSAGES_PER_MINUTE * 2)
        self.message_timestamps[channel_id].append(now)

    @tasks.loop(minutes=5)
    async def cleanup_rate_limits(self):
        """Clean up old rate limiting data"""
        try:
            now = datetime.utcnow()
            cutoff = now - timedelta(minutes=10)
            
            # Clean message timestamps
            channels_to_remove = []
            for channel_id in list(self.message_timestamps.keys()):
                # Remove old timestamps
                self.message_timestamps[channel_id] = deque(
                    (ts for ts in self.message_timestamps[channel_id] 
                     if ts > cutoff),
                    maxlen=self.MAX_MESSAGES_PER_MINUTE * 2
                )
                
                # Remove empty deques
                if not self.message_timestamps[channel_id]:
                    channels_to_remove.append(channel_id)
            
            for channel_id in channels_to_remove:
                self.message_timestamps.pop(channel_id, None)
            
            # Clean last bot message if too old
            channels_to_remove = []
            for channel_id in list(self.last_bot_message.keys()):
                if self.last_bot_message[channel_id] < cutoff:
                    channels_to_remove.append(channel_id)
            
            for channel_id in channels_to_remove:
                self.last_bot_message.pop(channel_id, None)
            
            logger.debug("Cleaned up rate limit data")
        except Exception as e:
            logger.error(f"Error in cleanup_rate_limits: {e}")

    # --- ENHANCED MESSAGE PROCESSING WITH NLP ---
    def sanitize_message(self, content: str, message=None) -> str:
        """Clean message with proper username handling"""
        if not content or len(content.strip()) < 3:
            return ""
        
        # Remove bot commands
        if content.startswith(('.', '!', '/', '?', '~')):
            return ""
        
        # Store original for emoji extraction
        original_content = content
        
        # Remove URLs
        content = re.sub(r'https?://\S+|www\.\S+', '', content)
        
        # Replace user mentions with readable names and store mapping
        def replace_mention(match):
            user_id = int(match.group(1))
            
            # Try to get username from cache
            if user_id in self.user_cache:
                username = self.user_cache[user_id]
            elif message and message.guild:
                # Try to get from guild
                member = message.guild.get_member(user_id)
                if member:
                    username = member.display_name
                    self.user_cache[user_id] = username
                else:
                    # Fallback to user ID placeholder
                    username = f"user_{user_id % 1000}"
            else:
                username = f"user_{user_id % 1000}"
            
            # Create placeholder that can be converted back
            placeholder = f"@USER_{user_id}"
            self.user_id_map[placeholder] = user_id
            return placeholder
        
        content = re.sub(r'<@!?(\d+)>', replace_mention, content)
        
        # Preserve channel mentions as readable names
        def replace_channel(match):
            channel_id = int(match.group(1))
            return "#channel_mention"
        
        content = re.sub(r'<#(\d+)>', replace_channel, content)
        
        # Preserve role mentions
        content = re.sub(r'<@&(\d+)>', '@role_mention', content)
        
        # Remove @everyone and @here
        content = re.sub(r'@everyone|@here', '', content, flags=re.IGNORECASE)
        
        # Count and limit emojis
        emoji_list = list(emoji.emoji_list(content))
        
        # Extract Discord emojis (both <:name:id> and <a:name:id>)
        custom_emoji_matches = re.findall(r'<a?:(\w+):(\d+)>', content)
        custom_emojis = [name for name, _ in custom_emoji_matches]
        all_emojis = emoji_list + [{'emoji': f':{e}:'} for e in custom_emojis]
        
        # Calculate emoji density
        total_chars = len(original_content)
        emoji_chars = sum(len(e['emoji']) for e in emoji_list) + len(''.join(custom_emojis))
        emoji_density = emoji_chars / total_chars if total_chars > 0 else 0
        
        if emoji_density > self.MAX_EMOJI_DENSITY:
            # Keep only first few emojis
            kept_emojis = all_emojis[:self.MAX_EMOJIS_PER_MESSAGE]
            
            # Remove all Unicode emojis
            for e in emoji_list:
                content = content.replace(e['emoji'], ' ')
            
            # Remove all Discord emojis
            content = re.sub(r'<a?:\w+:\d+>', ' ', content)
            
            # Add back limited emojis as special tokens
            for i, e in enumerate(kept_emojis):
                if i < self.MAX_EMOJIS_PER_MESSAGE:
                    if isinstance(e, dict):  # Unicode emoji
                        emoji_name = emoji.demojize(e['emoji'])[1:-1]
                        content += f" :emoji_{emoji_name}:"
                    else:  # Custom emoji name
                        content += f" :custom_emoji_{e}:"
        else:
            # Convert Unicode emojis to tokens for learning
            for e in emoji_list:
                emoji_name = emoji.demojize(e['emoji'])[1:-1]
                content = content.replace(e['emoji'], f' :emoji_{emoji_name}: ')
            
            # Convert Discord emojis to tokens
            for emoji_name, emoji_id in custom_emoji_matches:
                # Replace this specific emoji with token
                pattern = fr'<a?:{re.escape(emoji_name)}:{emoji_id}>'
                content = re.sub(
                    pattern,
                    f' :custom_emoji_{emoji_name}: ',
                    content
                )
        
        # Clean up punctuation
        content = re.sub(r'([!?.]){2,}', r'\1', content)  # Reduce multiple punctuation
        
        # Remove excessive whitespace
        content = ' '.join(content.split()).strip()
        
        # Only return if we have enough words
        word_count = len(content.split())
        return content if word_count >= self.MIN_MESSAGE_WORDS else ""

    def extract_triplets(self, content: str) -> List[Tuple[str, str, str]]:
        """Extract word triplets from content"""
        words = content.split()
        triplets = []
        
        if len(words) < 3:
            return triplets
        
        # Use sliding window with step 1 for more coverage
        for i in range(len(words) - 2):
            word_a = words[i].strip()
            word_b = words[i+1].strip()
            word_c = words[i+2].strip()
            
            if word_a and word_b and word_c:
                # Skip if too many emojis in sequence
                emoji_count = sum(1 for w in [word_a, word_b, word_c] 
                                if w.startswith(':emoji_') or w.startswith(':custom_emoji_'))
                if emoji_count >= 2:
                    continue
                
                triplets.append((word_a, word_b, word_c))
        
        return triplets

    def classify_word_type(self, word: str) -> str:
        """Classify word type for better generation"""
        if word.startswith(':emoji_') or word.startswith(':custom_emoji_'):
            return 'emoji'
        elif word.startswith('@USER_') or word.startswith('@user_') or word.startswith('@role_') or word.startswith('#channel_'):
            return 'mention'
        elif re.match(r'^[A-Z][a-z]*$', word) and len(word) > 1:
            return 'proper_noun'
        elif word.endswith(('ing', 'ed', 'ly')):
            return 'verb_or_adverb'
        else:
            return 'normal'

    async def train_on_message(self, content: str, author_id: int, emoji_count: int = 0, message=None):
        """Train Markov chain with batch processing"""
        clean_content = self.sanitize_message(content, message)
        if not clean_content:
            return 0
        
        triplets = self.extract_triplets(clean_content)
        if not triplets:
            return 0
        
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # Batch insert with word type classification
                for word_a, word_b, word_c in triplets:
                    word_type_a = self.classify_word_type(word_a)
                    word_type_b = self.classify_word_type(word_b)
                    word_type_c = self.classify_word_type(word_c)
                    
                    # Use most specific type
                    word_type = 'emoji' if 'emoji' in [word_type_a, word_type_b, word_type_c] \
                              else 'mention' if 'mention' in [word_type_a, word_type_b, word_type_c] \
                              else 'normal'
                    
                    await db.execute('''
                        INSERT INTO markov_chain (word_a, word_b, next_word, count, last_used, word_type)
                        VALUES (?, ?, ?, 1, CURRENT_TIMESTAMP, ?)
                        ON CONFLICT(word_a, word_b, next_word) 
                        DO UPDATE SET 
                            count = count + 1,
                            last_used = CURRENT_TIMESTAMP
                    ''', (word_a, word_b, word_c, word_type))
                
                # Update user stats
                await db.execute('''
                    INSERT INTO user_stats (user_id, message_count, last_seen, emoji_usage)
                    VALUES (?, 1, CURRENT_TIMESTAMP, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        message_count = message_count + 1,
                        last_seen = CURRENT_TIMESTAMP,
                        emoji_usage = emoji_usage + ?
                ''', (author_id, emoji_count, emoji_count))
                
                # Update username if available
                if message and message.author:
                    await db.execute('''
                        UPDATE user_stats 
                        SET username = ? 
                        WHERE user_id = ? AND (username IS NULL OR username != ?)
                    ''', (str(message.author), author_id, str(message.author)))
                
                await db.commit()
                
                self.stats['triplets_stored_today'] += len(triplets)
                self.stats['emojis_learned'] += emoji_count
                return len(triplets)
                
        except Exception as e:
            logger.error(f"Training error: {e}")
            return 0

    # --- REPLY GENERATION WITH ANTI-SPAM ---
    async def generate_sentence(self, seed_word: Optional[str] = None, context_message: Optional[str] = None) -> Optional[str]:
        """Generate sentence with enhanced NLP and controlled emojis"""
        if not await self.can_generate_reply():
            return None
        
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                
                # Get starting pair with context consideration
                start_pair = None
                
                if seed_word and len(seed_word) >= self.SEED_WORD_MIN_LENGTH:
                    # Try to find a starting pair with the seed word
                    async with db.execute('''
                        SELECT word_a, word_b, count FROM markov_chain 
                        WHERE (LOWER(word_a) = LOWER(?) OR LOWER(word_b) = LOWER(?))
                        AND word_type != 'emoji'
                        ORDER BY count DESC
                        LIMIT 10
                    ''', (seed_word, seed_word)) as cursor:
                        candidates = await cursor.fetchall()
                        if candidates:
                            # Weighted random selection
                            total_weight = sum(row['count'] for row in candidates)
                            if total_weight > 0:
                                rand = random.random() * total_weight
                                cumulative = 0
                                for row in candidates:
                                    cumulative += row['count']
                                    if cumulative >= rand:
                                        start_pair = row
                                        break
                
                if not start_pair and context_message:
                    # Extract words from context
                    clean_context = re.sub(r'[^\w\s@#:]', ' ', context_message)
                    words = clean_context.lower().split()
                    
                    # Filter for meaningful words
                    context_words = [w for w in words 
                                   if len(w) >= 4 
                                   and not w.startswith(('@', '#', ':'))
                                   and not re.match(r'^\d+$', w)]
                    
                    # Try context words
                    for context_word in context_words[:5]:
                        async with db.execute('''
                            SELECT word_a, word_b, count FROM markov_chain 
                            WHERE (LOWER(word_a) = LOWER(?) OR LOWER(word_b) = LOWER(?))
                            AND word_type != 'emoji'
                            ORDER BY count DESC
                            LIMIT 5
                        ''', (context_word, context_word)) as cursor:
                            candidates = await cursor.fetchall()
                            if candidates:
                                # Pick random from candidates
                                start_pair = random.choice(candidates)
                                break
                
                if not start_pair:
                    # Get random starting pair
                    async with db.execute('''
                        SELECT word_a, word_b, count FROM markov_chain 
                        WHERE word_type != 'emoji'
                        AND LENGTH(word_a) > 1 AND LENGTH(word_b) > 1
                        ORDER BY RANDOM()
                        LIMIT 10
                    ''') as cursor:
                        candidates = await cursor.fetchall()
                        if candidates:
                            # Weighted selection
                            total_weight = sum(row['count'] for row in candidates)
                            if total_weight > 0:
                                rand = random.random() * total_weight
                                cumulative = 0
                                for row in candidates:
                                    cumulative += row['count']
                                    if cumulative >= rand:
                                        start_pair = row
                                        break
                            else:
                                start_pair = candidates[0]
                
                if not start_pair:
                    return None
                
                # Build sentence with emoji control
                sentence = [start_pair['word_a'], start_pair['word_b']]
                emoji_count = 0
                max_attempts = self.SENTENCE_MAX_WORDS * 3
                
                for attempt in range(max_attempts):
                    if len(sentence) >= self.SENTENCE_MAX_WORDS:
                        break
                    
                    w1, w2 = sentence[-2], sentence[-1]
                    
                    # Get next word options
                    async with db.execute('''
                        SELECT next_word, count, word_type FROM markov_chain 
                        WHERE word_a = ? AND word_b = ?
                        ORDER BY count DESC 
                        LIMIT 15
                    ''', (w1, w2)) as cursor:
                        options = await cursor.fetchall()
                    
                    if not options:
                        # Try with just word_b as fallback
                        async with db.execute('''
                            SELECT next_word, count, word_type FROM markov_chain 
                            WHERE word_b = ?
                            ORDER BY count DESC 
                            LIMIT 10
                        ''', (w2,)) as cursor:
                            options = await cursor.fetchall()
                    
                    if not options:
                        break
                    
                    # Filter options
                    filtered_options = []
                    for option in options:
                        word_type = option['word_type']
                        next_word = option['next_word']
                        
                        if not next_word or len(next_word.strip()) == 0:
                            continue
                        
                        # Skip emojis if we have too many
                        if word_type == 'emoji' and emoji_count >= self.MAX_REPLY_EMOJIS:
                            continue
                        
                        # Skip duplicate consecutive words
                        if len(sentence) >= 1 and next_word == sentence[-1]:
                            continue
                        
                        filtered_options.append(option)
                    
                    if not filtered_options:
                        filtered_options = options[:3]
                    
                    # Weighted random selection
                    total_weight = sum(opt['count'] for opt in filtered_options)
                    if total_weight == 0:
                        next_word = random.choice(filtered_options)['next_word']
                    else:
                        rand = random.random() * total_weight
                        cumulative = 0
                        for opt in filtered_options:
                            cumulative += opt['count']
                            if cumulative >= rand:
                                next_word = opt['next_word']
                                break
                        else:
                            next_word = filtered_options[0]['next_word']
                    
                    # Track emojis
                    if self.classify_word_type(next_word) == 'emoji':
                        emoji_count += 1
                    
                    sentence.append(next_word)
                    
                    # Check for natural ending
                    if next_word.endswith(('.', '!', '?')):
                        if len(sentence) >= 5 and random.random() < 0.6:
                            break
                    
                    # Length-based stopping
                    if len(sentence) >= 8 and random.random() < 0.3:
                        break
                    
                    if len(sentence) >= self.SENTENCE_MAX_WORDS:
                        break
                
                # Convert tokens to readable text
                readable_sentence = []
                used_mentions = set()
                
                for i, word in enumerate(sentence):
                    # Handle Unicode emojis
                    if word.startswith(':emoji_'):
                        emoji_name = word[7:-1] if word.endswith(':') else word[7:]
                        try:
                            emoji_char = emoji.emojize(f":{emoji_name}:", language='alias')
                            readable_sentence.append(emoji_char)
                        except:
                            readable_sentence.append(f":{emoji_name}:")
                    
                    # Handle custom Discord emojis
                    elif word.startswith(':custom_emoji_'):
                        emoji_name = word[14:-1] if word.endswith(':') else word[14:]
                        readable_sentence.append(f":{emoji_name}:")
                    
                    # Handle username mentions
                    elif word.startswith('@USER_'):
                        user_id = word[6:]
                        if user_id.isdigit() and user_id not in used_mentions:
                            readable_sentence.append(f"<@{user_id}>")
                            used_mentions.add(user_id)
                        else:
                            readable_sentence.append(f"@{user_id}")
                    
                    # Handle other mentions
                    elif word in ['#channel_mention', '@role_mention']:
                        readable_sentence.append(word)
                    
                    # Regular words - ensure proper case
                    else:
                        # Capitalize first word of sentence
                        if i == 0 and word[0].islower():
                            word = word[0].upper() + word[1:]
                        readable_sentence.append(word)
                
                # Assemble final sentence
                result = ''
                for i, token in enumerate(readable_sentence):
                    if i == 0:
                        result = token
                    else:
                        prev_token = readable_sentence[i-1]
                        # Don't add space between mention/emoji and punctuation
                        if (prev_token.endswith(('>', ':')) and 
                            token in ['.', ',', '!', '?', ':', ';']):
                            result += token
                        # Don't add space before some punctuation
                        elif token in ['.', ',', '!', '?', ':', ';']:
                            result += token
                        else:
                            result += ' ' + token
                
                # Clean up multiple spaces
                result = re.sub(r'\s+', ' ', result).strip()
                
                # Ensure proper spacing after mentions/emojis
                result = re.sub(r'(<@\d+>|:[a-zA-Z0-9_]+:)([a-zA-Z])', r'\1 \2', result)
                
                # Capitalize first letter if not already
                if result and result[0].islower():
                    result = result[0].upper() + result[1:]
                
                # Add punctuation if missing
                if result and not result[-1] in '.!?;':
                    last_words = result.lower().split()[-2:]
                    
                    # Question detection
                    question_words = ['what', 'why', 'how', 'when', 'who', 'where', 'which', 'whose']
                    if any(word in last_words for word in question_words):
                        result += '?'
                    # Exclamation detection
                    elif any(word in last_words for word in ['wow', 'awesome', 'amazing', 'great', 'yes', 'no']):
                        result += '!'
                    else:
                        result += '.'
                
                # Final validation
                words_in_result = result.split()
                if len(words_in_result) < 3:
                    return None
                
                # Remove any trailing spaces before punctuation
                result = re.sub(r'\s+([.!?,;])', r'\1', result)
                
                return result
                
        except Exception as e:
            logger.error(f"Generation error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None

    async def can_generate_reply(self):
        """Check if we have enough data"""
        return self.stats['total_triplets'] >= self.MIN_TRIPLETS_FOR_REPLY

    # --- DISCORD EVENT HANDLERS WITH ANTI-SPAM ---
    @commands.Cog.listener()
    async def on_message(self, message):
        # Skip bots, DMs, and commands
        if message.author.bot or not message.guild:
            return
        
        content = message.content.strip()
        if not content or content.startswith(('.', '!', '/', '?', '~')):
            return
        
        # Process message asynchronously
        asyncio.create_task(self.buffer_message(message))
        
        # Check for mentions or replies
        is_mentioned = self.bot.user in message.mentions
        is_reply_to_me = False
        
        if message.reference:
            try:
                ref_msg = await message.channel.fetch_message(message.reference.message_id)
                if ref_msg.author.id == self.bot.user.id:
                    is_reply_to_me = True
            except:
                pass
        
        should_reply = is_mentioned or is_reply_to_me
        chance = 1.0 if should_reply else self.REPLY_CHANCE
        
        # Check rate limits BEFORE generating reply
        if not self.check_rate_limit(message.channel.id):
            if should_reply:
                # If mentioned but rate limited, send a polite message
                async with message.channel.typing():
                    await asyncio.sleep(0.5)
                    await message.channel.send(
                        "🤖 I'm typing too fast! Let me catch my breath...",
                        reference=message,
                        mention_author=False
                    )
                    self.record_bot_message(message.channel.id)
            return
        
        # Cooldown check
        if not should_reply:
            now = datetime.utcnow()
            last_reply = self.reply_cooldowns.get(message.channel.id)
            if last_reply and (now - last_reply).seconds < self.REPLY_COOLDOWN:
                return
        
        # Determine if we should reply
        if random.random() < chance or should_reply:
            if not await self.can_generate_reply():
                if should_reply:
                    async with message.channel.typing():
                        await asyncio.sleep(0.5)
                        await message.channel.send(
                            f"🧠 I'm learning! So far I know {self.stats['total_triplets']:,} word patterns. Keep chatting!",
                            reference=message,
                            mention_author=False
                        )
                        self.record_bot_message(message.channel.id)
                return
            
            # Generate and send reply
            reply = await self.generate_sentence(context_message=message.content)
            
            if reply:
                async with message.channel.typing():
                    thinking_time = random.uniform(self.TYPING_MIN_SECONDS, self.TYPING_MAX_SECONDS)
                    await asyncio.sleep(thinking_time)
                    
                    try:
                        await message.channel.send(
                            reply,
                            reference=message if should_reply else None,
                            mention_author=False
                        )
                        
                        # Record successful message
                        self.record_bot_message(message.channel.id)
                        self.reply_cooldowns[message.channel.id] = datetime.utcnow()
                        self.stats['replies_generated'] += 1
                        self.save_state()
                        
                        # Log bot reply
                        async with aiosqlite.connect(self.db_path) as db:
                            await db.execute('''
                                INSERT INTO message_log (content, author_id, channel_id, timestamp, is_bot_reply)
                                VALUES (?, ?, ?, ?, 1)
                            ''', (reply, self.bot.user.id, message.channel.id, datetime.utcnow().isoformat()))
                            await db.commit()
                            
                    except discord.HTTPException as e:
                        if e.status == 429:  # Rate limited by Discord
                            logger.warning(f"Discord rate limit hit: {e}")
                            self.stats['rate_limited'] += 1
                        else:
                            logger.error(f"Failed to send message: {e}")
            
            elif should_reply:
                # If we should reply but couldn't generate, send fallback
                fallbacks = [
                    "Hmm, I'm not sure what to say to that!",
                    "Let me think about that...",
                    "Interesting! I need to learn more about this.",
                    "🤔 Processing...",
                    "I'm still learning how to respond to that!"
                ]
                async with message.channel.typing():
                    await asyncio.sleep(0.5)
                    await message.channel.send(
                        random.choice(fallbacks),
                        reference=message,
                        mention_author=False
                    )
                    self.record_bot_message(message.channel.id)

    async def buffer_message(self, message):
        """Buffer message for processing"""
        try:
            # Count Unicode emojis
            emoji_list = list(emoji.emoji_list(message.content))
            
            # Count Discord emojis
            custom_emojis = re.findall(r'<a?:(\w+):\d+>', message.content)
            emoji_count = len(emoji_list) + len(custom_emojis)
            
            # Limit emojis learned
            if emoji_count > self.MAX_EMOJIS_PER_MESSAGE:
                emoji_count = self.MAX_EMOJIS_PER_MESSAGE
            
            # Save to database
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute('''
                    INSERT INTO message_log (content, author_id, channel_id, timestamp, emoji_count)
                    VALUES (?, ?, ?, ?, ?)
                ''', (message.content, message.author.id, message.channel.id, 
                    message.created_at.isoformat(), emoji_count))
                await db.commit()
            
            # Add to in-memory buffers
            clean = self.sanitize_message(message.content, message)
            if clean:
                self.recent_messages.append(clean)
            
            self.stats['messages_processed'] += 1
            self.stats['total_messages'] += 1
            
            # Train immediately
            trained = await self.train_on_message(message.content, message.author.id, emoji_count, message)
            if trained > 0:
                self.stats['total_triplets'] += trained
            
            # Periodic save
            if self.stats['messages_processed'] % self.FLUSH_THRESHOLD == 0:
                self.save_state()
            
        except Exception as e:
            logger.error(f"Buffering error: {e}")

    # --- PERIODIC TASKS ---
    @tasks.loop(minutes=5)
    async def periodic_tasks(self):
        """Run periodic maintenance tasks"""
        if not self.db_initialized:
            return
        
        try:
            await self.cleanup_old_data()
            await self.update_stats()
            self.save_state()
            logger.debug("Periodic tasks completed")
        except Exception as e:
            logger.error(f"Periodic task error: {e}")

    async def cleanup_old_data(self):
        """Clean up old data periodically"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # Archive old raw messages
                cutoff_date = (datetime.utcnow() - timedelta(days=self.RAW_MESSAGE_RETENTION_DAYS)).isoformat()
                await db.execute('DELETE FROM message_log WHERE timestamp < ?', (cutoff_date,))
                
                # Archive old triplets
                old_cutoff = (datetime.utcnow() - timedelta(days=self.PRUNE_OLD_TRIPLETS_AFTER_DAYS)).isoformat()
                await db.execute('DELETE FROM markov_chain WHERE last_used < ? AND count < 2', (old_cutoff,))
                
                # Vacuum database
                await db.execute('VACUUM')
                
                await db.commit()
                logger.debug("Cleanup completed")
                
        except Exception as e:
            logger.error(f"Cleanup error: {e}")

    async def update_stats(self):
        """Update statistics from database"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute("SELECT COUNT(*) FROM markov_chain") as cursor:
                    result = await cursor.fetchone()
                    self.stats['total_triplets'] = result[0] if result else 0
                
                async with db.execute("SELECT COUNT(*) FROM message_log") as cursor:
                    result = await cursor.fetchone()
                    self.stats['total_messages'] = result[0] if result else 0
                
                async with db.execute("SELECT COUNT(DISTINCT author_id) FROM message_log") as cursor:
                    result = await cursor.fetchone()
                    self.stats['unique_users'] = result[0] if result else 0
                
                # Update today's messages count
                today = datetime.utcnow().date().isoformat()
                async with db.execute("SELECT COUNT(*) FROM message_log WHERE DATE(timestamp) = DATE('now')") as cursor:
                    result = await cursor.fetchone()
                    self.stats['today_messages'] = result[0] if result else 0
                
        except Exception as e:
            logger.error(f"Stats update error: {e}")

    # --- COMMANDS WITH ENHANCED STATS ---
    @commands.command(name="brainstats")
    async def brain_stats(self, ctx):
        """Show enhanced statistics"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                
                # Get comprehensive stats
                async with db.execute("""
                    SELECT 
                        (SELECT COUNT(*) FROM markov_chain) as triplets,
                        (SELECT COUNT(*) FROM markov_chain WHERE word_type = 'emoji') as emoji_triplets,
                        (SELECT COUNT(*) FROM markov_chain WHERE word_type = 'mention') as mention_triplets,
                        (SELECT COUNT(*) FROM message_log) as messages,
                        (SELECT COUNT(*) FROM message_log WHERE DATE(timestamp) = DATE('now')) as today_messages,
                        (SELECT COUNT(DISTINCT author_id) FROM message_log) as unique_users
                """) as cursor:
                    row = await cursor.fetchone()
                
                # Get top users
                async with db.execute("""
                    SELECT username, message_count, emoji_usage 
                    FROM user_stats 
                    ORDER BY message_count DESC 
                    LIMIT 5
                """) as cursor:
                    top_users = await cursor.fetchall()
                
                # Update instance stats
                if row:
                    self.stats.update({
                        'total_triplets': row['triplets'],
                        'total_messages': row['messages'],
                        'unique_users': row['unique_users']
                    })
                
                embed = discord.Embed(
                    title="🧠 Enhanced Brain Statistics",
                    color=discord.Color.blue(),
                    timestamp=datetime.utcnow()
                )
                
                if row:
                    embed.add_field(
                        name="Knowledge Base",
                        value=f"**{row['triplets']:,}** total triplets\n"
                              f"**{row['emoji_triplets']:,}** emoji patterns\n"
                              f"**{row['mention_triplets']:,}** mention patterns",
                        inline=False
                    )
                    
                    embed.add_field(
                        name="Activity",
                        value=f"**{row['messages']:,}** total messages\n"
                              f"**{row['today_messages']}** messages today\n"
                              f"**{row['unique_users']}** unique users\n"
                              f"**{self.stats['replies_generated']}** replies sent",
                        inline=False
                    )
                else:
                    embed.add_field(name="Error", value="Could not fetch statistics", inline=False)
                
                # Top users
                if top_users:
                    top_users_text = "\n".join(
                        f"{user['username'] or 'Unknown'}: {user['message_count']:,} msgs ({user['emoji_usage']} emojis)"
                        for user in top_users
                    )
                    embed.add_field(name="Top Contributors", value=top_users_text, inline=False)
                
                # Anti-spam stats
                embed.add_field(
                    name="Rate Limiting",
                    value=f"**{self.stats['rate_limited']}** times rate-limited\n"
                          f"**{self.stats['spam_blocked']}** spam bursts blocked\n"
                          f"**{len(self.message_timestamps)}** channels tracked",
                    inline=False
                )
                
                # Status
                if self.stats['total_triplets'] < self.MIN_TRIPLETS_FOR_REPLY:
                    status = f"Learning... ({self.stats['total_triplets']}/{self.MIN_TRIPLETS_FOR_REPLY} triplets)"
                    embed.color = discord.Color.orange()
                else:
                    status = "✅ Ready to generate!"
                    embed.color = discord.Color.green()
                
                embed.set_footer(text=f"{status} | Uptime: {self.get_uptime()}")
                
                await ctx.send(embed=embed)
                
        except Exception as e:
            logger.error(f"Brainstats error: {e}")
            await ctx.send(f"❌ Error: {str(e)[:100]}")

    def get_uptime(self):
        """Get formatted uptime"""
        try:
            startup = datetime.fromisoformat(self.stats['startup_time'])
            uptime = datetime.utcnow() - startup
            days = uptime.days
            hours, remainder = divmod(uptime.seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            
            if days > 0:
                return f"{days}d {hours}h {minutes}m"
            else:
                return f"{hours}h {minutes}m {seconds}s"
        except:
            return "Unknown"

    @commands.command(name="generate")
    async def force_generate(self, ctx, *, seed_word=None):
        """Force generate a sentence with rate limit check"""
        # Check rate limit
        if not self.check_rate_limit(ctx.channel.id):
            await ctx.send("⏳ Please wait a moment before generating again!")
            return
        
        async with ctx.channel.typing():
            sentence = await self.generate_sentence(seed_word, ctx.message.content)
            
            if sentence:
                embed = discord.Embed(
                    description=f"*{sentence}*",
                    color=discord.Color.green()
                )
                if seed_word:
                    embed.set_footer(text=f"Seeded with: {seed_word}")
                msg = await ctx.send(embed=embed)
                self.record_bot_message(ctx.channel.id)
            else:
                await ctx.send("❌ Not enough data yet! I need to learn more from chat.")

    @commands.command(name="antispam")
    @commands.has_permissions(manage_messages=True)
    async def anti_spam_info(self, ctx):
        """Show anti-spam settings and stats"""
        embed = discord.Embed(
            title="🛡️ Anti-Spam Protection",
            color=discord.Color.orange(),
            timestamp=datetime.utcnow()
        )
        
        embed.add_field(
            name="Current Settings",
            value=f"**Min Interval:** {self.MIN_MESSAGE_INTERVAL}s\n"
                  f"**Max/Minute:** {self.MAX_MESSAGES_PER_MINUTE}\n"
                  f"**Burst Window:** {self.SPAM_BURST_WINDOW}s\n"
                  f"**Burst Limit:** {self.SPAM_BURST_LIMIT}\n"
                  f"**Reply Chance:** {self.REPLY_CHANCE*100:.1f}%\n"
                  f"**Reply Cooldown:** {self.REPLY_COOLDOWN}s",
            inline=False
        )
        
        # Current channel stats
        now = datetime.utcnow()
        channel_id = ctx.channel.id
        
        if channel_id in self.last_bot_message:
            last_msg_time = self.last_bot_message[channel_id]
            seconds_since = (now - last_msg_time).total_seconds()
            embed.add_field(
                name="This Channel",
                value=f"Last bot message: {seconds_since:.1f}s ago\n"
                      f"Can send now: {'✅' if seconds_since >= self.MIN_MESSAGE_INTERVAL else '❌'}",
                inline=False
            )
        
        embed.add_field(
            name="Global Stats",
            value=f"Rate limited: {self.stats['rate_limited']} times\n"
                  f"Spam blocked: {self.stats['spam_blocked']} times\n"
                  f"Active channels: {len(self.message_timestamps)}",
            inline=False
        )
        
        await ctx.send(embed=embed)

    @commands.command(name="resetlimits")
    @commands.has_permissions(manage_messages=True)
    async def reset_rate_limits(self, ctx):
        """Reset rate limits for current channel"""
        channel_id = ctx.channel.id
        self.message_timestamps.pop(channel_id, None)
        self.last_bot_message.pop(channel_id, None)
        self.reply_cooldowns.pop(channel_id, None)
        self.save_state()
        await ctx.send("✅ Rate limits reset for this channel.")

    @commands.command(name="emojistats")
    async def emoji_stats(self, ctx):
        """Show emoji usage statistics"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                
                # Get emoji stats
                async with db.execute("""
                    SELECT 
                        COUNT(*) as total_emojis,
                        COUNT(DISTINCT next_word) as unique_emojis
                    FROM markov_chain 
                    WHERE word_type = 'emoji'
                """) as cursor:
                    emoji_stats = await cursor.fetchone()
                
                # Get average emojis per message
                async with db.execute("""
                    SELECT AVG(emoji_count) as avg_per_message
                    FROM message_log
                    WHERE emoji_count > 0
                """) as cursor:
                    avg_result = await cursor.fetchone()
                    avg_per_message = avg_result['avg_per_message'] if avg_result and avg_result['avg_per_message'] else 0
                
                # Get most common emojis
                async with db.execute("""
                    SELECT next_word, COUNT(*) as usage_count 
                    FROM markov_chain 
                    WHERE word_type = 'emoji' 
                    GROUP BY next_word 
                    ORDER BY usage_count DESC 
                    LIMIT 10
                """) as cursor:
                    top_emojis = await cursor.fetchall()
                
                embed = discord.Embed(
                    title="😀 Emoji Statistics",
                    color=discord.Color.gold(),
                    timestamp=datetime.utcnow()
                )
                
                if emoji_stats:
                    embed.add_field(
                        name="Overview",
                        value=f"**{emoji_stats['total_emojis']:,}** total emoji usages\n"
                              f"**{emoji_stats['unique_emojis']}** unique emojis\n"
                              f"**{avg_per_message:.1f}** avg emojis per message\n"
                              f"**{self.MAX_EMOJIS_PER_MESSAGE}** max learned per message\n"
                              f"**{self.MAX_REPLY_EMOJIS}** max in replies",
                        inline=False
                    )
                
                # Top emojis
                if top_emojis:
                    emoji_list = []
                    for emoji_row in top_emojis:
                        emoji_name = emoji_row['next_word'].replace(':emoji_', '').replace(':custom_emoji_', '').rstrip(':')
                        try:
                            # Try to show actual emoji
                            emoji_char = emoji.emojize(f":{emoji_name}:", language='alias')
                            emoji_list.append(f"{emoji_char}: {emoji_row['usage_count']:,}")
                        except:
                            emoji_list.append(f":{emoji_name}: {emoji_row['usage_count']:,}")
                    
                    embed.add_field(
                        name="Most Used Emojis",
                        value="\n".join(emoji_list),
                        inline=False
                    )
                
                embed.set_footer(text=f"Emoji density limit: {self.MAX_EMOJI_DENSITY*100:.0f}%")
                await ctx.send(embed=embed)
                
        except Exception as e:
            logger.error(f"Emoji stats error: {e}")
            await ctx.send(f"❌ Error: {str(e)[:100]}")

    @commands.command(name="clearcooldown")
    @commands.has_permissions(manage_messages=True)
    async def clear_cooldown(self, ctx):
        """Clear reply cooldown"""
        self.reply_cooldowns.pop(ctx.channel.id, None)
        self.save_state()
        await ctx.send("✅ Cooldown cleared for this channel.")

    @commands.command(name="exportbrain")
    @commands.has_permissions(administrator=True)
    async def export_brain(self, ctx):
        """Export brain data"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute("""
                    SELECT word_a, word_b, next_word, count 
                    FROM markov_chain 
                    ORDER BY count DESC 
                    LIMIT 5000
                """) as cursor:
                    rows = await cursor.fetchall()
                
                if not rows:
                    await ctx.send("No data to export yet.")
                    return
                
                # Create export
                lines = [f"# Markov Brain Export - {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}"]
                lines.append(f"# Total triplets: {len(rows):,}")
                lines.append("")
                
                for word_a, word_b, next_word, count in rows:
                    lines.append(f"{word_a} {word_b} -> {next_word} (x{count})")
                
                content = "\n".join(lines)
                filename = f"brain_export_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.txt"
                
                await ctx.send(
                    f"Exported {len(rows):,} patterns:",
                    file=discord.File(
                        bytes(content, 'utf-8'),
                        filename=filename
                    )
                )
                
        except Exception as e:
            logger.error(f"Export error: {e}")
            await ctx.send(f"Error exporting: {str(e)}")

    @commands.command(name="resetdaily")
    @commands.has_permissions(administrator=True)
    async def reset_daily_stats(self, ctx):
        """Reset daily statistics"""
        self.stats['triplets_stored_today'] = 0
        self.save_state()
        await ctx.send("✅ Daily statistics reset.")

    @commands.command(name="health")
    async def health_check(self, ctx):
        """Check bot health"""
        try:
            embed = discord.Embed(
                title="🤖 Bot Health Check",
                color=discord.Color.green(),
                timestamp=datetime.utcnow()
            )
            
            uptime = self.get_uptime()
            
            embed.add_field(
                name="System Status",
                value=f"✅ **Online**\n"
                      f"Uptime: {uptime}\n"
                      f"Database: {'✅ Healthy' if self.db_initialized else '❌ Not ready'}\n"
                      f"State: ✅ Saved",
                inline=False
            )
            
            embed.add_field(
                name="Memory",
                value=f"Recent messages: {len(self.recent_messages)}/5000\n"
                      f"Cooldowns: {len(self.reply_cooldowns)} active\n"
                      f"User cache: {len(self.user_cache)} users",
                inline=False
            )
            
            embed.add_field(
                name="Performance",
                value=f"Triplets: {self.stats['total_triplets']:,}\n"
                      f"Messages processed: {self.stats['messages_processed']:,}\n"
                      f"Replies generated: {self.stats['replies_generated']:,}",
                inline=False
            )
            
            await ctx.send(embed=embed)
                
        except Exception as e:
            await ctx.send(f"❌ Health check failed: {e}")

    @commands.command(name="forcelearn")
    @commands.has_permissions(administrator=True)
    async def force_learn(self, ctx, limit: int = 100):
        """Force learning from recent messages"""
        if limit > 1000:
            limit = 1000
            
        await ctx.send(f"⏳ Learning from last {limit} messages...")
        
        try:
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute('''
                    SELECT content, author_id FROM message_log 
                    WHERE processed = 0 
                    ORDER BY timestamp DESC 
                    LIMIT ?
                ''', (limit,)) as cursor:
                    rows = await cursor.fetchall()
                    
                learned = 0
                for content, author_id in rows:
                    # Count emojis
                    emoji_list = list(emoji.emoji_list(content))
                    custom_emojis = re.findall(r'<a?:(\w+):\d+>', content)
                    emoji_count = len(emoji_list) + len(custom_emojis)
                    
                    learned += await self.train_on_message(content, author_id, emoji_count)
                
                await ctx.send(f"✅ Learned {learned} new triplets from {len(rows)} messages!")
                
        except Exception as e:
            logger.error(f"Force learn error: {e}")
            await ctx.send(f"❌ Error: {e}")

async def setup(bot):
    await bot.add_cog(EnhancedMarkovCog(bot))