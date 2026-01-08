from contextlib import asynccontextmanager
from functools import partial
import secrets
import time
import concurrent
import discord
from discord.ext import commands, tasks
import aiosqlite
import re
import random
import asyncio
import logging
from collections import deque
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import emoji
from typing import List, Tuple, Optional, Dict
import nltk
from nltk.tokenize import word_tokenize
from nltk import pos_tag
from nltk.sentiment import SentimentIntensityAnalyzer
from collections import Counter
import math
from langdetect import DetectorFactory

# Ensure consistent language detection
DetectorFactory.seed = 0

# Try to download NLTK data
try:
    nltk.data.find('tokenizers/punkt')
    nltk.data.find('tokenizers/punkt_tab') # <--- NEW REQUIREMENT
    nltk.data.find('taggers/averaged_perceptron_tagger')
    nltk.data.find('taggers/universal_tagset')
    nltk.data.find('corpora/stopwords')
    nltk.data.find('sentiment/vader_lexicon')
    nltk.data.find('averaged_perceptron_tagger_eng')
except LookupError:
    nltk.download('punkt', quiet=True)
    nltk.download('punkt_tab', quiet=True) # <--- NEW REQUIREMENT
    nltk.download('averaged_perceptron_tagger', quiet=True)
    nltk.download('universal_tagset', quiet=True)
    nltk.download('stopwords', quiet=True)
    nltk.download('vader_lexicon', quiet=True)
    nltk.download('averaged_perceptron_tagger_eng')

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
        self._db_pool = []  # List of available connections
        self._db_pool_lock = asyncio.Lock()
        self._max_pool_size = 5
        self.global_topics = {}
        try:
            from nltk.corpus import stopwords
            self.stop_words = set(stopwords.words('english'))
            
            # EXPANDED HINGLISH STOPWORDS (Grammar & Fillers)
            hinglish_grammar = {
                # Pronouns & Address
                'mai', 'main', 'hum', 'tu', 'tum', 'aap', 'woh', 'yeh', 'ye', 'wo', 'uss', 'iss', 
                'bhai', 'bro', 'yaar', 'dost', 'log', 'sab', 'kuch', 'koi', 
                
                # Verbs (Auxiliary)
                'hai', 'hain', 'ho', 'hoga', 'hogi', 'honge', 'hu', 'hoon', 'tha', 'thi', 'the', 
                'kar', 'kara', 'karne', 'karna', 'karo', 'raha', 'rahi', 'rahe',
                
                # Prepositions/Conjunctions/Particles
                'ka', 'ki', 'ke', 'ko', 'ne', 'se', 'par', 'pe', 'me', 'mein', 'aur', 
                'lekin', 'magar', 'kyunki', 'agar', 'to', 'toh', 'bhi', 'hi', 'mat', 'wala', 'wale',
                
                # Conversational Fillers / Yes/No
                'na', 'nahi', 'nhi', 'haan', 'han', 'ha', 'ok', 'okay', 'accha', 'acha', 'sahi', 
                'thik', 'theek', 'arey', 'abe', 'lol', 'lmao', 'ded', 'xd', 'nice', 'good', 'bad',
                'pata', 'baat', 'msg', 'message', 'kya', 'kyu', 'kyun', 'kab', 'kaha', 'kaise'
            }
            
            self.stop_words = self.stop_words.union(hinglish_grammar)
            logger.info(f"Loaded {len(self.stop_words)} stopwords (English + Extended Hinglish)")
        except:
            self.stop_words = set()
        
        # INITIALIZATION FLAGS
        self.db_initialized = False
        self.user_cache = {}
        self.user_id_map = {}
        
        self.nltk_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        self.nltk_lock = asyncio.Lock()
        self.recent_topic_boost = {}  # {word: boost_factor}
        self.recent_topic_decay = 0.8  # Decay factor per generation
        self.query_cache = {}
        self.cache_ttl = 300  # 5 minutes
        # Question detection map for dynamic temperature
        self.qa_map = {
            # English question words
            'what', 'why', 'how', 'when', 'who', 'where', 'which', 'whose', 'whom',
            # Hinglish question words
            'kya', 'kaise', 'kyun', 'kab', 'kahan', 'kaun', 'kisne', 'kisko', 'kiski',
            'kitna', 'kitni', 'kitne', 'kisliye', 'kyon'
        }
        
        # Time-aware greetings (IST)
        self.time_greetings = {
            'morning': ['good morning', 'subh prabhat', 'shubhodaya', 'gm', 'goodmorning', 'uth'],
            'afternoon': ['good afternoon', 'namaste', 'hello', 'hi'],
            'evening': ['good evening', 'shubh sandhya', 'namaste', 'hello'],
            'night': ['good night', 'shubh ratri', 'gn', 'goodnight', 'soja', 'so jao']
        }
        # --- MODIFIED: Initialize NLP Models for multilingual support ---
        self.nlp = None  # We'll disable spaCy for now since it's English-only
        logger.info("SpaCy disabled for Hinglish support (English-only model)")
        
        
        # Initialize sentiment analyzer (VADER works well with English in Hinglish)
        self.sentiment_analyzer = SentimentIntensityAnalyzer()
        
        # --- NEW: Hinglish-specific parameters ---
        self.HINGLISH_MODE = True  # Enable Hinglish-specific processing
        self.MIN_HINDI_WORD_LENGTH = 2  # Hindi words can be shorter
        
        # --- NEW: Satellite View Buffer ---
        self.conversation_context = deque(maxlen=200)  # Last 50 messages for topic modeling
        self.current_topic_keywords = []
        self.conversation_sentiment = 0.0  # Running average sentiment
        
        # Initialize stats with Hinglish-specific metrics
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
            'spam_blocked': 0,
            'sentiment_analyzed': 0,
            'topics_detected': 0,
            'lemmas_stored': 0,
            'hinglish_messages': 0,  # NEW: Track Hinglish messages
            'hindi_words_learned': 0,  # NEW: Track Hindi vocabulary
        }
        
        # Initialize other tracking dictionaries
        self.message_timestamps = {}
        self.last_bot_message = {}
        self.reply_cooldowns = {}
        self.server_emoji_cache = {}
        self.message_buffer = deque(maxlen=1000)
        self.recent_messages = deque(maxlen=5000)
        
        # NOW load saved state (which will update stats if they exist)
        self.load_state()
        
        # Enhanced limits with emoji control
        self.MAX_EMOJIS_PER_MESSAGE = 3
        self.MAX_EMOJI_DENSITY = 0.3
        
        # ANTI-SPAM PROTECTION
        self.MIN_MESSAGE_INTERVAL = 0.5
        self.MAX_MESSAGES_PER_MINUTE = 45
        self.SPAM_BURST_WINDOW = 10
        self.SPAM_BURST_LIMIT = 5
        
        # Limits optimized for high traffic (17k/day)
        self.MAX_RAW_MESSAGES = 2_000_000
        self.MAX_TRIPLETS = 5_000_000
        self.RAW_MESSAGE_RETENTION_DAYS = 120
        self.PRUNE_OLD_TRIPLETS_AFTER_DAYS = 365
        
        # --- OPTIMIZED PARAMETERS for Hinglish ---
        self.MIN_TRIPLETS_FOR_REPLY = 500
        self.REPLY_CHANCE = 0.02
        self.REPLY_COOLDOWN = 10
        self.TYPING_MIN_SECONDS = 0.3
        self.TYPING_MAX_SECONDS = 0.8
        self.SENTENCE_MAX_WORDS = 30
        self.SEED_WORD_MIN_LENGTH = 2  # Reduced for Hindi words
        self.MIN_MESSAGE_WORDS = 2  # Reduced for Hinglish
        
        # --- MODIFIED: NLP ENHANCEMENT PARAMETERS for Hinglish ---
        self.GRAMMAR_ENFORCEMENT = True  # Disable for Hinglish (mixed grammar rules)
        self.SENTIMENT_MATCHING = True   # Keep sentiment matching
        self.TOPIC_COHERENCE = True      # Keep topic coherence
        self.LEMMATIZATION_ENABLED = True  # Enable Hinglish lemmatization
        
        # Grammar rules simplified for Hinglish
        self.POS_TRANSITION_RULES = {
            'DET': ['ADJ', 'NOUN', 'PROPN'],
            'ADJ': ['NOUN', 'ADJ'],
            'NOUN': ['VERB', 'ADP', 'AUX', 'PUNCT'],
            'VERB': ['ADV', 'ADP', 'NOUN', 'PUNCT'],
            'ADV': ['VERB', 'ADJ'],
        }
        
        # Emoji reply control
        self.MAX_REPLY_EMOJIS = 2
        self.EMOJI_REPLY_CHANCE = 0.1
        
        self.FLUSH_THRESHOLD = 50
        
        # Start background tasks
        self.bot.loop.create_task(self.initialize_database())
        self.periodic_tasks.start()
        self.cleanup_rate_limits.start()
        self.update_topic_model.start()
    # --- MODIFIED: Enhanced Hinglish Lemmatizer ---

    @asynccontextmanager
    async def get_db(self):
        """Get database connection from pool with proper function registration"""
        db = None
        try:
            async with self._db_pool_lock:
                if self._db_pool:
                    db = self._db_pool.pop()
            
            # FIX: If pool is empty, we MUST create a new connection
            if not db:
                db = await aiosqlite.connect(self.db_path)
                # Register the REGEXP function only on brand new connections
                try:
                    await db.create_function("REGEXP", 2, self.regexp)
                except:
                    pass 
            
            if db is None:
                raise Exception("Database connection could not be established")
                
            yield db
            
        except Exception as e:
            logger.error(f"Database connection error: {e}")
            if db:
                await db.close()
            raise
        finally:
            if db:
                async with self._db_pool_lock:
                    if len(self._db_pool) < self._max_pool_size:
                        self._db_pool.append(db)
                    else:
                        await db.close()

    
    async def get_cached_query(self, query_key: str, query_func, *args):
        """Cache database query results"""
        now = time.time()
        
        if query_key in self.query_cache:
            result, timestamp = self.query_cache[query_key]
            if now - timestamp < self.cache_ttl:
                return result
        
        # Execute and cache
        result = await query_func(*args)
        self.query_cache[query_key] = (result, now)
        
        # Clean old cache entries
        if len(self.query_cache) > 1000:
            self.clean_cache()
        
        return result

    def clean_cache(self):
        """Remove old cache entries"""
        now = time.time()
        to_remove = []
        
        for key, (_, timestamp) in self.query_cache.items():
            if now - timestamp > self.cache_ttl:
                to_remove.append(key)
        
        for key in to_remove:
            del self.query_cache[key]
    
    def lemmatize_text(self, text: str) -> str:
        """Reduce words to their root form for Hinglish (Hindi-English)"""
        if not text:
            return text
        
        # Skip lemmatization for mentions, emojis, and URLs
        if any(prefix in text for prefix in ['@', '#', ':emoji_', ':custom_emoji_', 'http']):
            return text
        
        words = text.split()
        lemmatized_words = []
        
        # Comprehensive Hinglish lemmatization rules
        hindi_verb_map = {
            # Common Hindi verb conjugations (Romanized)
            'bolta': 'bol', 'bolti': 'bol', 'bolte': 'bol', 'bolna': 'bol', 'bolne': 'bol',
            'boli': 'bol', 'bolkar': 'bol', 'bolke': 'bol', 'bolo': 'bol', 'boltaa': 'bol',
            
            'karta': 'kar', 'karti': 'kar', 'karte': 'kar', 'karna': 'kar', 'karne': 'kar',
            'kari': 'kar', 'karke': 'kar', 'karo': 'kar', 'kartaa': 'kar', 'karle': 'kar',
            
            'hota': 'ho', 'hoti': 'ho', 'hote': 'ho', 'hona': 'ho', 'hone': 'ho',
            'hoon': 'ho', 'hai': 'ho', 'hain': 'ho', 'hoa': 'ho', 'hoi': 'ho',
            
            'dekhta': 'dekh', 'dekhti': 'dekh', 'dekhte': 'dekh', 'dekhna': 'dekh',
            'dekhne': 'dekh', 'dekhi': 'dekh', 'dekho': 'dekh', 'dekhkar': 'dekh',
            
            'sochta': 'soch', 'sochti': 'soch', 'sochte': 'soch', 'sochna': 'soch',
            'sochne': 'soch', 'sochi': 'soch', 'socho': 'soch', 'sochkar': 'soch',
            
            'jaata': 'ja', 'jaati': 'ja', 'jaate': 'ja', 'jaana': 'ja', 'jaane': 'ja',
            'gaya': 'ja', 'gayi': 'ja', 'gaye': 'ja', 'jao': 'ja', 'jaakar': 'ja',
            
            'aata': 'aa', 'aati': 'aa', 'aate': 'aa', 'aana': 'aa', 'aane': 'aa',
            'aaya': 'aa', 'aayi': 'aa', 'aaye': 'aa', 'aao': 'aa', 'aakar': 'aa',
            
            'likhta': 'likh', 'likhti': 'likh', 'likhte': 'likh', 'likhna': 'likh',
            'likhne': 'likh', 'likhi': 'likh', 'likho': 'likh', 'likhkar': 'likh',
            
            'padhta': 'padh', 'padhti': 'padh', 'padhte': 'padh', 'padhna': 'padh',
            'padhne': 'padh', 'padhi': 'padh', 'padho': 'padh', 'padhkar': 'padh',
            
            # Additional common verbs
            'marta': 'mar', 'marti': 'mar', 'marte': 'mar', 'marna': 'mar',
            'rota': 'ro', 'roti': 'ro', 'rote': 'ro', 'rona': 'ro',
            'hasata': 'has', 'hasati': 'has', 'hasate': 'has', 'hasna': 'has',
            'samajhta': 'samajh', 'samajhti': 'samajh', 'samajhte': 'samajh', 'samajhna': 'samajh',
            'laata': 'la', 'laati': 'la', 'laate': 'la', 'laana': 'la',
            'leata': 'le', 'leati': 'le', 'leate': 'le', 'leana': 'le',
            'deta': 'de', 'deti': 'de', 'dete': 'de', 'dena': 'de',
            'leta': 'le', 'leti': 'le', 'lete': 'le', 'lena': 'le',
        }
        
        # English verb lemmatization (basic)
        english_verb_suffixes = {
            'ing': '',  # running -> run
            'ed': '',   # walked -> walk
            'es': '',   # goes -> go
            's': '',    # runs -> run
        }
        
        # Common English irregular verbs
        english_irregular_verbs = {
            'am': 'be', 'is': 'be', 'are': 'be', 'was': 'be', 'were': 'be', 'been': 'be',
            'has': 'have', 'have': 'have', 'had': 'have',
            'does': 'do', 'did': 'do', 'done': 'do',
            'goes': 'go', 'went': 'go', 'gone': 'go',
            'gets': 'get', 'got': 'get', 'gotten': 'get',
            'makes': 'make', 'made': 'make',
            'knows': 'know', 'knew': 'know', 'known': 'know',
            'thinks': 'think', 'thought': 'think',
            'says': 'say', 'said': 'say',
            'comes': 'come', 'came': 'come', 'come': 'come',
            'looks': 'look', 'looked': 'look',
            'wants': 'want', 'wanted': 'want',
            'finds': 'find', 'found': 'find',
            'gives': 'give', 'gave': 'give', 'given': 'give',
            'tells': 'tell', 'told': 'tell',
            'works': 'work', 'worked': 'work',
            'calls': 'call', 'called': 'call',
            'tries': 'try', 'tried': 'try',
            'asks': 'ask', 'asked': 'ask',
            'needs': 'need', 'needed': 'need',
            'feels': 'feel', 'felt': 'feel',
            'becomes': 'become', 'became': 'become', 'become': 'become',
            'leaves': 'leave', 'left': 'leave',
            'puts': 'put', 'put': 'put',
            'means': 'mean', 'meant': 'mean',
            'keeps': 'keep', 'kept': 'keep',
            'lets': 'let', 'let': 'let',
            'begins': 'begin', 'began': 'begin', 'begun': 'begin',
            'seems': 'seem', 'seemed': 'seem',
            'helps': 'help', 'helped': 'help',
            'talks': 'talk', 'talked': 'talk',
            'turns': 'turn', 'turned': 'turn',
            'starts': 'start', 'started': 'start',
            'shows': 'show', 'showed': 'show', 'shown': 'show',
            'hears': 'hear', 'heard': 'hear',
            'plays': 'play', 'played': 'play',
            'runs': 'run', 'ran': 'run', 'run': 'run',
            'moves': 'move', 'moved': 'move',
            'lives': 'live', 'lived': 'live',
            'believes': 'believe', 'believed': 'believe',
            'brings': 'bring', 'brought': 'bring',
            'happens': 'happen', 'happened': 'happen',
            'writes': 'write', 'wrote': 'write', 'written': 'write',
            'provides': 'provide', 'provided': 'provide',
            'sits': 'sit', 'sat': 'sit',
            'stands': 'stand', 'stood': 'stand',
            'loses': 'lose', 'lost': 'lose',
            'pays': 'pay', 'paid': 'pay',
            'meets': 'meet', 'met': 'meet',
            'includes': 'include', 'included': 'include',
            'continues': 'continue', 'continued': 'continue',
        }
        
        # Hindi adjective/noun variations
        hindi_adjective_map = {
            'achaa': 'acha', 'ache': 'acha', 'achi': 'acha',  # good
            'buraa': 'bura', 'bure': 'bura', 'buri': 'bura',  # bad
            'chotaa': 'chota', 'chote': 'chota', 'choti': 'chota',  # small
            'lambaa': 'lamba', 'lambe': 'lamba', 'lambi': 'lamba',  # long/tall
            'badaa': 'bada', 'bade': 'bada', 'badi': 'bada',  # big
            'pyaraa': 'pyara', 'pyare': 'pyara', 'pyari': 'pyara',  # lovely
            'sundar': 'sundar', 'sundari': 'sundar',  # beautiful
            'garib': 'garib', 'garibo': 'garib',  # poor
            'amir': 'amir', 'amiro': 'amir',  # rich
        }
        
        # Common Hinglish nouns (plural forms)
        hinglish_noun_map = {
            'bhaiyo': 'bhai', 'bhaiyon': 'bhai',  # brothers
            'beheno': 'behen', 'behenon': 'behen',  # sisters
            'dosto': 'dost', 'doston': 'dost',  # friends
            'yaaro': 'yaar', 'yaaron': 'yaar',  # friends
            'ladko': 'ladka', 'ladkon': 'ladka',  # boys
            'ladkiyo': 'ladki', 'ladkiyon': 'ladki',  # girls
            'log': 'aadmi', 'logo': 'aadmi', 'logon': 'aadmi',  # people
            'baccho': 'bacha', 'bachon': 'bacha',  # children
            'gharo': 'ghar', 'gharon': 'ghar',  # houses
            'gaadiyo': 'gaadi', 'gaadiyon': 'gaadi',  # vehicles
            'kitabo': 'kitab', 'kitabon': 'kitab',  # books
            'mobile': 'phone', 'mobiles': 'phone',  # phones
        }
        
        # Common Hinglish word normalizations
        hinglish_normalizations = {
            'nahi': 'nahi', 'nahin': 'nahi', 'naheen': 'nahi', 'na': 'nahi', 'nah': 'nahi', 'nhi': 'nahi',
            'haan': 'haan', 'han': 'haan', 'ha': 'haan', 'hmm': 'haan', 'hm': 'haan', 'hmmm': 'haan', 'yes': 'haan', 'yep': 'haan', 'yeah': 'haan',
            'acha': 'acha', 'accha': 'acha', 'achha': 'acha', 'acchha': 'acha',
            'theek': 'theek', 'thik': 'theek', 'thik hai': 'theek', 'thikh': 'theek',
            'kya': 'kya', 'kaise': 'kaise', 'kyun': 'kyun', 'kab': 'kab', 'kahan': 'kahan', 'kaun': 'kaun',
            'bhai': 'bhai', 'bhaiya': 'bhai', 'bro': 'bhai', 'brother': 'bhai',
            'yaar': 'yaar', 'dost': 'dost', 'friend': 'dost', 'mitr': 'dost',
            'hai': 'hai', 'hain': 'hai', 'ho': 'hai',
            'main': 'main', 'mein': 'main', 'mai': 'main',
            'tum': 'tum', 'tu': 'tum', 'aap': 'aap', 'you': 'tum',
            'mera': 'mera', 'meri': 'mera', 'mere': 'mera', 'my': 'mera',
            'tera': 'tera', 'teri': 'tera', 'tere': 'tera', 'your': 'tera',
            'hum': 'hum', 'ham': 'hum', 'we': 'hum',
            'woh': 'woh', 'vo': 'woh', 'us': 'woh', 'he': 'woh', 'she': 'woh', 'they': 'woh',
            'koi': 'koi', 'kuch': 'kuch', 'some': 'kuch',
            'yahan': 'yahan', 'yahaan': 'yahan', 'idhar': 'yahan', 'here': 'yahan',
            'wahan': 'wahan', 'wahaan': 'wahan', 'udhar': 'wahan', 'there': 'wahan',
            'abhi': 'abhi', 'now': 'abhi',
            'phir': 'phir', 'fir': 'phir', 'then': 'phir',
            'lekin': 'lekin', 'magar': 'lekin', 'but': 'lekin',
            'aur': 'aur', 'and': 'aur',
            'ya': 'ya', 'or': 'ya',
            'par': 'par', 'on': 'par',
            'se': 'se', 'from': 'se', 'with': 'se',
            'ke': 'ke', 'of': 'ke', 'ki': 'ke', 'ka': 'ke',
            'me': 'me', 'in': 'me',
            'pe': 'pe', 'on': 'pe',
            'tak': 'tak', 'until': 'tak',
            'bhi': 'bhi', 'too': 'bhi', 'also': 'bhi',
            'toh': 'toh', 'so': 'toh',
            'kyunki': 'kyunki', 'because': 'kyunki',
            'agar': 'agar', 'if': 'agar',
            'to': 'to', 'then': 'to',
            'jo': 'jo', 'that': 'jo',
            'ki': 'ki', 'that': 'ki',
            'jab': 'jab', 'when': 'jab',
            'jahan': 'jahan', 'where': 'jahan',
            'jitna': 'jitna', 'as much': 'jitna',
            'waisa': 'waisa', 'like that': 'waisa',
            'aisa': 'aisa', 'like this': 'aisa',
            'waise': 'waise', 'that way': 'waise',
            'aise': 'aise', 'this way': 'aise',
        }
        
        # Common Hinglish filler words to keep as-is
        common_hinglish_fillers = [
            'wow', 'awesome', 'mast', 'badhiya', 'zabardast', 'super', 'great', 'nice',
            'arey', 'oye', 'hello', 'hi', 'hey', 'bye', 'goodbye',
            'ok', 'okay', 'fine', 'alright', 'cool',
            'sorry', 'excuse', 'please', 'thank', 'thanks', 'welcome',
            'haha', 'lol', 'lmao', 'rofl', 'hehe', 'hihi',
            'umm', 'uhh', 'ahem', 'well',
        ]
        
        for word in words:
            lower_word = word.lower()
            original_word = word
            
            # Check if word is already in Devanagari (Hindi script)
            # We typically don't lemmatize Devanagari without proper tools
            if re.search(r'[\u0900-\u097F]', word):
                lemmatized_words.append(word)
                continue
            
            # 1. Check Hinglish normalizations first (most common)
            if lower_word in hinglish_normalizations:
                lemmatized_words.append(hinglish_normalizations[lower_word])
                continue
            
            # 2. Check Hindi verb map
            if lower_word in hindi_verb_map:
                lemmatized_words.append(hindi_verb_map[lower_word])
                continue
            
            # 3. Check English irregular verbs
            if lower_word in english_irregular_verbs:
                lemmatized_words.append(english_irregular_verbs[lower_word])
                continue
            
            # 4. Check Hindi adjective variations
            if lower_word in hindi_adjective_map:
                lemmatized_words.append(hindi_adjective_map[lower_word])
                continue
            
            # 5. Check Hinglish noun variations
            if lower_word in hinglish_noun_map:
                lemmatized_words.append(hinglish_noun_map[lower_word])
                continue
            
            # 6. Apply English suffix rules (if word looks English)
            if re.match(r'^[a-z]+$', lower_word):  # Only English letters
                matched = False
                for suffix, replacement in english_verb_suffixes.items():
                    if lower_word.endswith(suffix):
                        # Check if removing suffix leaves at least 2 characters
                        stem = lower_word[:-len(suffix)]
                        if len(stem) >= 2:
                            # Check if the stem is itself a word (avoid over-stemming)
                            lemmatized_words.append(stem + replacement)
                            matched = True
                            break
                if matched:
                    continue
            
            # 7. Check if it's a common filler word
            if lower_word in common_hinglish_fillers:
                lemmatized_words.append(lower_word)
                continue
            
            # 8. Default: keep the original word (preserve case for proper nouns)
            if word[0].isupper() and len(word) > 1:  # Likely a proper noun
                lemmatized_words.append(word)
            else:
                lemmatized_words.append(lower_word)
        
        return ' '.join(lemmatized_words)
    
    def is_question(self, text: str) -> bool:
        """Check if text is a question for dynamic temperature adjustment."""
        if not text:
            return False
        
        # Check for question marks
        if '?' in text:
            return True
        
        # Check for question words
        words = text.lower().split()
        for word in words:
            if word in self.qa_map:
                return True
        
        # Check for question patterns in Hinglish
        hinglish_patterns = [
            r'kya.*hai', r'kaise.*ho', r'kyun.*hai', r'kab.*aayega',
            r'kaun.*hai', r'kahan.*hai', r'kitna.*hai'
        ]
        
        for pattern in hinglish_patterns:
            if re.search(pattern, text.lower()):
                return True
        
        return False
    
    
    
    def humanize_response(self, text: str, sentiment: float) -> str:
        """
        Apply human-like quirks to the generated response.
        
        Features:
        1. Typing quirks: 15% chance to lowercase everything (casual vibe)
        2. Lazy punctuation: 30% chance to remove final punctuation
        3. Vocal fry: 5% chance to elongate last vowel for positive sentiment
        4. Hinglish flair: Add common Hinglish fillers
        """
        if not text:
            return text
        
        result = text
        
        # 1. Typing quirks (15% chance)
        if secrets.SystemRandom().random() < 0.15:
            result = result.lower()
        
        # 2. Lazy punctuation (30% chance)
        if secrets.SystemRandom().random() < 0.30 and result and result[-1] in '.!?':
            result = result[:-1]
        
        # 3. Vocal fry - elongate last vowel for positive sentiment (5% chance)
        if secrets.SystemRandom().random() < 0.05 and sentiment > 0.1:
            words = result.split()
            if words:
                last_word = words[-1].lower()
                
                # Check for vowel at end
                vowels = {'a', 'e', 'i', 'o', 'u'}
                if last_word and last_word[-1] in vowels:
                    # Elongate based on sentiment intensity
                    elongation = min(int(sentiment * 5) + 1, 4)
                    words[-1] = last_word + last_word[-1] * elongation
                    result = ' '.join(words)
        
        # 4. Hinglish flair - add common fillers (10% chance)
        if secrets.SystemRandom().random() < 0.10:
            hinglish_fillers = [
                'yaar', 'bhai', 'dost', 'arey', 'oye', 'hmm', 'achaa',
                'wah', 'wow', 'seriously', 'literally', 'bas'
            ]
            
            # Add filler at beginning or end
            if secrets.choice([True, False]):
                result = f"{secrets.choice(hinglish_fillers)}... {result}"
            else:
                result = f"{result} {secrets.choice(hinglish_fillers)}"
        
        return result
    
    
    def update_topic_boost(self, topics: List[str]):
        """Update topic boost weights with decay."""
        # Apply decay to existing boosts
        for word in list(self.recent_topic_boost.keys()):
            self.recent_topic_boost[word] *= self.recent_topic_decay
            if self.recent_topic_boost[word] < 1.1:  # Remove insignificant boosts
                del self.recent_topic_boost[word]
        
        # Add new topics
        for topic in topics:
            if topic in self.recent_topic_boost:
                self.recent_topic_boost[topic] = min(2.5, self.recent_topic_boost[topic] + 0.5)
            else:
                self.recent_topic_boost[topic] = 2.0  # Initial boost
    
    def get_topic_boost(self, word: str) -> float:
        """Get boost factor for a word based on recent topics."""
        return self.recent_topic_boost.get(word.lower(), 1.0)
    
    def apply_dynamic_temperature(self, weights: List[float], temperature: float) -> List[float]:
        """Apply temperature scaling to weights."""
        if temperature <= 0:
            return weights
        
        # Apply temperature scaling: weight^(1/temperature)
        scaled_weights = []
        for w in weights:
            if w > 0:
                # Add epsilon to avoid zero
                scaled = w ** (1.0 / temperature)
                scaled_weights.append(scaled)
            else:
                scaled_weights.append(0.0)
        
        # Normalize
        total = sum(scaled_weights)
        if total > 0:
            return [w / total for w in scaled_weights]
        return scaled_weights
    
    def extract_recent_topics(self, text: str) -> List[str]:
        """Extract nouns and proper nouns from recent context for stickiness."""
        if not text:
            return []
        
        try:
            # Get POS tags
            pos_tags = self.get_pos_tags(text)
            
            # Extract nouns and proper nouns
            topics = []
            for word, tag in pos_tags:
                if tag in ['NOUN', 'PROPN', 'NNP', 'NN']:
                    # Clean the word
                    clean_word = re.sub(r'[^\w\s]', '', word.lower())
                    if len(clean_word) > 2 and clean_word not in self.stop_words:
                        topics.append(clean_word)
            
            return topics[:5]  # Limit to top 5 topics
        except:
            return []
    
    def is_hindi_word(self, word: str) -> bool:
        """Check if a word contains Devanagari script characters"""
        # Hindi Unicode range: \u0900-\u097F
        return bool(re.search(r'[\u0900-\u097F]', word))
    
    def is_english_word(self, word: str) -> bool:
        """Check if a word contains only English characters"""
        return bool(re.match(r'^[a-zA-Z]+$', word))
    
    def is_hinglish_word(self, word: str) -> bool:
        """Check if word is Hinglish (mix of scripts or Romanized Hindi)"""
        # Remove mentions, emojis, etc.
        if word.startswith(('@', '#', ':')):
            return False
        
        # Check for Romanized Hindi patterns (common in Hinglish)
        hinglish_patterns = [
            r'^[a-z]+na$',  # Hindi verbs ending with 'na'
            r'^[a-z]+i$',   # Hindi feminine forms
            r'^[a-z]+e$',   # Hindi plural forms
            r'^[a-z]+o$',   # Hindi vocative forms
            r'^acha$', '^thik$', '^kya$', '^kaise$', '^kyun$',  # Common Romanized Hindi
            r'^bhai$', '^yaar$', '^dost$', '^sahi$', '^theek$',
        ]
        
        word_lower = word.lower()
        for pattern in hinglish_patterns:
            if re.match(pattern, word_lower):
                return True
        
        return False
    
    def regexp(self, expr, item):
        reg = re.compile(expr, re.IGNORECASE)
        return reg.search(item) is not None
    
    def detect_language_mix(self, text: str) -> Dict[str, float]:
        """Detect language mix in text (English vs Hindi vs Hinglish)"""
        words = text.split()
        if not words:
            return {'english': 0, 'hindi': 0, 'hinglish': 0}
        
        english_count = 0
        hindi_count = 0
        hinglish_count = 0
        other_count = 0
        
        for word in words:
            if self.is_hindi_word(word):
                hindi_count += 1
            elif self.is_english_word(word):
                english_count += 1
            elif self.is_hinglish_word(word):
                hinglish_count += 1
            else:
                other_count += 1
        
        total = len(words)
        return {
            'english': english_count / total,
            'hindi': hindi_count / total,
            'hinglish': hinglish_count / total,
            'other': other_count / total
        }

    def get_time_based_seed(self, seed_word: str) -> Optional[str]:
        """Adjust seed word based on time of day (IST)."""
        # Convert UTC to IST (UTC+5:30)
        utc_now = datetime.utcnow()
        ist_now = utc_now + timedelta(hours=5, minutes=30)
        
        # Get time of day
        hour = ist_now.hour
        
        # Check if seed word relates to greetings
        greeting_words = {'hi', 'hello', 'hey', 'namaste', 'gm', 'gn', 'good morning', 
                         'good night', 'uth', 'soja', 'subah', 'raat'}
        
        if seed_word and seed_word.lower() in greeting_words:
            if 5 <= hour < 12:
                # Morning
                return secrets.choice(self.time_greetings['morning'])
            elif 12 <= hour < 17:
                # Afternoon
                return secrets.choice(self.time_greetings['afternoon'])
            elif 17 <= hour < 21:
                # Evening
                return secrets.choice(self.time_greetings['evening'])
            else:
                # Night
                return secrets.choice(self.time_greetings['night'])
        
        return seed_word
    
    def get_pos_tags(self, text: str) -> List[Tuple[str, str]]:
        """
        Hybrid POS tagging: Uses NLTK for English structure + Custom overrides for Hinglish.
        """
        if not text:
            return []

        # 1. Use NLTK for smart tagging (handles English & basic morphology)
        # We use tagset='universal' to get simple tags like VERB, NOUN, ADJ
        try:
            tokens = word_tokenize(text)
            nltk_tags = pos_tag(tokens, tagset='universal')
        except Exception as e:
            logger.error(f"NLTK tagging error: {e}")
            # Fallback to simple split if NLTK fails
            return [(w, 'UNK') for w in text.split()]

        final_tags = []
        
        # 2. Apply Hinglish Patch (Override NLTK errors for Hindi words)
        for word, tag in nltk_tags:
            word_lower = word.lower()
            
            # Manual overrides for common Hinglish words
            if word_lower in ['mein', 'main', 'mujhe', 'tumhe', 'usko', 'unko', 'hum', 'tum']:
                final_tags.append((word, 'PRON'))
            
            elif word_lower in ['nahi', 'na', 'mat', 'nhi', 'haan', 'han']:
                final_tags.append((word, 'PART')) # Particle (Negation/Affirmation)
                
            elif word_lower in ['hai', 'hain', 'tha', 'thi', 'the', 'hoga', 'raha', 'rahi']:
                final_tags.append((word, 'VERB'))
                
            elif word_lower.endswith(('ka', 'ki', 'ke', 'ko', 'se', 'par', 'pe', 'me')):
                final_tags.append((word, 'ADP')) # Adposition (Prepositions)
                
            elif word_lower in ['acha', 'bura', 'sahi', 'galat', 'thik', 'badhiya']:
                final_tags.append((word, 'ADJ'))
                
            elif word_lower in ['kyu', 'kyun', 'kab', 'kahan', 'kaise']:
                final_tags.append((word, 'ADV'))
                
            else:
                # Trust NLTK for everything else (English words, Proper Nouns, etc.)
                final_tags.append((word, tag))

        return final_tags
    
    def analyze_sentiment(self, text: str) -> float:
        """Get sentiment score for Hinglish text"""
        # VADER works with English, so we'll focus on English sentiment
        # For Hindi sentiment, we'd need a different model
        scores = self.sentiment_analyzer.polarity_scores(text)
        
        # Adjust for common Hinglish sentiment words
        positive_hindi = ['acha', 'sahi', 'theek', 'badhiya', 'mast', 'awesome', 'good', 'nice', 'khoob', 'badiya']
        negative_hindi = ['bura', 'kharab', 'bekar', 'ganda', 'bad', 'worst', 'buraa', 'ganda']
        
        text_lower = text.lower()
        words = text_lower.split()
        for word in positive_hindi:
            if word in words: # Exact word match
                scores['compound'] = min(1.0, scores['compound'] + 0.3)
        
        for word in negative_hindi:
            if word in text_lower:
                scores['compound'] = max(-1.0, scores['compound'] - 0.3)
        
        return scores['compound']
    
    def extract_tfidf_keywords(self, messages: List[str], top_n: int = 10) -> List[Tuple[str, float]]:
        """Extract important keywords for Hinglish (Fixing Emojis & Noise)"""
        if not messages:
            return []
        
        # Build vocabulary
        all_words = []
        for msg in messages:
            # 1. Clean out Discord Emojis FIRST (<:name:id> or <a:name:id>)
            # We replace them with space so they don't glue words together
            clean_msg = re.sub(r'<a?:\w+:\d+>', ' ', msg)
            
            words = []
            for w in clean_msg.split():
                w_lower = w.lower()
                
                # 2. Strict Filtering
                # - Must be longer than min length
                # - Must NOT be in stop words
                # - Must NOT start with special chars (@, #, :)
                # - Must NOT be purely numeric
                # - Must NOT be a single repeated char (e.g. "aaaa")
                if (len(w) >= self.MIN_HINDI_WORD_LENGTH 
                    and w_lower not in self.stop_words
                    and not w.startswith(('@', '#', ':'))
                    and not re.match(r'^\d+$', w)
                    and not re.match(r'^(.)\1*$', w_lower)): # Skips 'hhhh', 'aaaa'
                    
                    words.append(w_lower)
            
            all_words.extend(words)
        
        if not all_words:
            return []
        
        # Term Frequency (TF)
        word_counts = Counter(all_words)
        total_words = len(all_words)
        
        # Document Frequency (DF)
        doc_freq = Counter()
        for msg in messages:
            # Clean emoji here too for consistency
            clean_msg = re.sub(r'<a?:\w+:\d+>', ' ', msg)
            
            words_in_msg = set()
            for w in clean_msg.split():
                w_lower = w.lower()
                if (len(w) >= self.MIN_HINDI_WORD_LENGTH 
                    and w_lower not in self.stop_words
                    and not w.startswith(('@', '#', ':'))):
                    words_in_msg.add(w_lower)
            
            for word in words_in_msg:
                doc_freq[word] += 1
        
        # Calculate TF-IDF
        tfidf_scores = {}
        for word, count in word_counts.items():
            tf = count / total_words
            df = doc_freq.get(word, 1)
            # Add 1 to DF to avoid division by zero (smoothing)
            idf = math.log(len(messages) / (df + 1)) + 1
            
            # Boost logic
            score = tf * idf
            
            # Boost specific meaningful categories if needed
            # (Optional: Boost Proper Nouns slightly if you had POS tagging here)
            
            tfidf_scores[word] = score
        
        return sorted(tfidf_scores.items(), key=lambda x: x[1], reverse=True)[:top_n]



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


    def is_valid_pos_transition(self, pos1: str, pos2: str) -> bool:
            """
            Enforce stricter grammar to prevent bad Hinglish mixing.
            Example: Prevents "Main aaj khana play" (Noun -> Verb mismatch)
            """
            if not self.GRAMMAR_ENFORCEMENT:
                return True

            # Rule 1: Hindi Postpositions (ka/ke/ki/ko/mein/par) usually NOT followed by raw Verbs
            # Bad: "kamra mein sleep" -> Good: "kamra mein so" or "kamra mein sleeping"
            if pos1 == 'ADP': 
                if pos2 == 'VERB': return False 
                if pos2 == 'PRON': return True 
                if pos2 == 'NOUN': return True
                if pos2 == 'PROPN': return True
                if pos2 == 'ADJ': return True

            # Rule 2: Adjectives should usually precede Nouns
            if pos1 == 'ADJ':
                if pos2 == 'NOUN': return True
                if pos2 == 'PROPN': return True
                if pos2 == 'ADJ': return True # "bohot acha"
                if pos2 == 'VERB': return False # "red run" -> Bad

            # Rule 3: Pronouns (Main/Tum) usually followed by Noun, Adj, or Aux Verb
            if pos1 == 'PRON':
                if pos2 == 'VERB': return True 
                if pos2 == 'NOUN': return True 
                if pos2 == 'ADJ': return True 
                if pos2 == 'ADP': return True # "mujh par"

            return True
    
    
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
        """Initialize database with connection pool and NLP columns"""
        await self.bot.wait_until_ready()
        
        try:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            
            async with self.get_db() as db: 
                
                # Optimize SQLite settings
                await db.execute('PRAGMA journal_mode=WAL;')
                await db.execute('PRAGMA synchronous=NORMAL;')
                await db.execute('PRAGMA cache_size=-10000;')
                await db.execute('PRAGMA temp_store=DEFAULT;')
                await db.execute('PRAGMA mmap_size=33554432;')
                
                # --- UPDATED: Create tables with NLP columns ---
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS markov_chain (
                        word_a TEXT NOT NULL,
                        word_b TEXT NOT NULL,
                        next_word TEXT NOT NULL,
                        lemma_a TEXT,           -- NEW: Lemmatized form
                        lemma_b TEXT,           -- NEW: Lemmatized form
                        lemma_next TEXT,        -- NEW: Lemmatized form
                        pos_a TEXT,             -- NEW: Part of speech
                        pos_b TEXT,             -- NEW: Part of speech
                        pos_next TEXT,          -- NEW: Part of speech
                        sentiment_score REAL DEFAULT 0.0,  -- NEW: Average sentiment
                        topic_weight REAL DEFAULT 1.0,     -- NEW: Topic relevance
                        count INTEGER DEFAULT 1,
                        last_used TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        word_type TEXT DEFAULT 'normal',
                        PRIMARY KEY (word_a, word_b, next_word)
                    ) WITHOUT ROWID
                ''')
                
                # Create message_log table (unchanged)
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS message_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        content TEXT NOT NULL,
                        author_id INTEGER,
                        channel_id INTEGER,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                        processed BOOLEAN DEFAULT 0,
                        emoji_count INTEGER DEFAULT 0,
                        is_bot_reply BOOLEAN DEFAULT 0,
                        sentiment REAL DEFAULT 0.0,  -- NEW: Message sentiment
                        cleaned_content TEXT         -- NEW: Cleaned text for topic modeling
                    )
                ''')
                
                # Create user_stats table (unchanged)
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS user_stats (
                        user_id INTEGER PRIMARY KEY,
                        username TEXT,
                        message_count INTEGER DEFAULT 0,
                        last_seen TIMESTAMP,
                        emoji_usage INTEGER DEFAULT 0,
                        avg_sentiment REAL DEFAULT 0.0  -- NEW: User's typical sentiment
                    )
                ''')
                
                # Create indexes for NLP columns
                await db.execute('''
                    CREATE INDEX IF NOT EXISTS idx_markov_lemmas 
                    ON markov_chain(lemma_a, lemma_b, lemma_next)
                ''')
                
                await db.execute('''
                    CREATE INDEX IF NOT EXISTS idx_markov_pos 
                    ON markov_chain(pos_a, pos_b, pos_next)
                ''')
                
                await db.execute('''
                    CREATE INDEX IF NOT EXISTS idx_markov_sentiment 
                    ON markov_chain(sentiment_score)
                ''')
                
                # Existing indexes...
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
                
                async with db.execute("PRAGMA table_info(markov_chain)") as cursor:
                    columns = [row[1] for row in await cursor.fetchall()]
                    if 'lemma_a' not in columns:
                        logger.info("Migrating database schema...")
                        await db.execute("ALTER TABLE markov_chain ADD COLUMN lemma_a TEXT")
                        await db.execute("ALTER TABLE markov_chain ADD COLUMN lemma_b TEXT")
                        await db.execute("ALTER TABLE markov_chain ADD COLUMN lemma_next TEXT")
                    
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
                
                # Count lemmatized entries
                async with db.execute("SELECT COUNT(*) FROM markov_chain WHERE lemma_a IS NOT NULL") as cursor:
                    result = await cursor.fetchone()
                    self.stats['lemmas_stored'] = result[0] if result else 0
                
                logger.info(f"Database initialized. Existing: {self.stats['total_triplets']} triplets, {self.stats['lemmas_stored']} lemmas from {self.stats['total_messages']} messages")
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
            
    @tasks.loop(minutes=3)
    async def update_topic_model(self):
        """Enhanced Topic Identification using frequency and recency"""
        try:
            if not self.conversation_context:
                return

            # 1. Combine context but weight the last 10 messages 3x more
            weighted_text = []
            context_list = list(self.conversation_context)
            for i, msg in enumerate(context_list):
                multiplier = 3 if i > len(context_list) - 10 else 1
                weighted_text.extend([msg['content']] * multiplier)
            
            # 2. Extract keywords with better filtering
            keywords = self.extract_tfidf_keywords(weighted_text, top_n=12)
            
            # 3. Filter for 'Meaningful' topics (Hinglish + English)
            # Remove common conversational fillers from being topics
            fillers = {'kya', 'hai', 'bhai', 'yaar', 'toh', 'aur', 'the', 'this', 'that'}
            self.current_topic_keywords = [
                k for k in keywords 
                if k[0].lower() not in fillers              # Remove grammar/stop words
                and not k[0].startswith('<')               # Ignore Discord custom emojis
                and not k[0].startswith(':')               # Ignore Unicode emoji tokens
                and len(k[0]) > 1                          # Ignore single characters
            ]

            # 4. Update DB weights for these active topics
            if self.current_topic_keywords and self.TOPIC_COHERENCE:
                async with self.get_db() as db: 
                    # Reset old topic weights first to prevent "topic drift"
                    await db.execute("UPDATE markov_chain SET topic_weight = 1.0 WHERE topic_weight > 1.0")
                    
                    for keyword, weight in self.current_topic_keywords:
                        # Boost patterns that contain current topic keywords
                        await db.execute('''
                            UPDATE markov_chain 
                            SET topic_weight = ? 
                            WHERE (word_a = ? OR word_b = ? OR next_word = ?)
                        ''', (1.5 + weight, keyword, keyword, keyword))
                    await db.commit()
                
            self.stats['topics_detected'] += 1
            logger.info(f"Top Active Topics: {[k[0] for k in self.current_topic_keywords[:5]]}")
                
        except Exception as e:
            logger.error(f"Topic update error: {e}")

    # --- ENHANCED MESSAGE PROCESSING WITH NLP ---
    def sanitize_message(self, content: str, message=None) -> str:
        """Clean message for Hinglish"""
        if not content or len(content.strip()) < 2:
            return ""
        
        # Remove bot commands
        if content.startswith(('.', '!', '/', '?', '~')):
            return ""
        
        # Store original for emoji extraction
        original_content = content
        
        # Remove URLs
        content = re.sub(r'https?://\S+|www\.\S+', '', content)
        
        # Replace user mentions with readable names
        def replace_mention(match):
            user_id = int(match.group(1))
            
            if user_id in self.user_cache:
                username = self.user_cache[user_id]
            elif message and message.guild:
                member = message.guild.get_member(user_id)
                if member:
                    username = member.display_name
                    self.user_cache[user_id] = username
                else:
                    username = f"user_{user_id % 1000}"
            else:
                username = f"user_{user_id % 1000}"
            
            placeholder = f"@USER_{user_id}"
            self.user_id_map[placeholder] = user_id
            return placeholder
        
        content = re.sub(r'<@!?(\d+)>', replace_mention, content)
        
        # Preserve channel mentions
        content = re.sub(r'<#(\d+)>', '#channel_mention', content)
        
        # Preserve role mentions
        content = re.sub(r'<@&(\d+)>', '@role_mention', content)
        
        # Remove @everyone and @here
        content = re.sub(r'@everyone|@here', '', content, flags=re.IGNORECASE)
        
        # Handle emojis - Check server existence FIRST
        emoji_list = list(emoji.emoji_list(content))
        custom_emoji_matches = re.findall(r'<(a?):(\w+):(\d+)>', content)
        
        # Store valid emojis that exist on the server
        valid_custom_emojis = []
        server_emoji_cache = {}
        
        if message and message.guild:
            # Get all server emojis and create a lookup dictionary
            for server_emoji in message.guild.emojis:
                server_emoji_cache[str(server_emoji.id)] = server_emoji
                server_emoji_cache[server_emoji.name] = server_emoji
            
            for anim, name, e_id in custom_emoji_matches:
                # Check if emoji exists on server by ID OR by name
                emoji_exists = False
                
                # First check by ID (more reliable)
                if str(e_id) in server_emoji_cache:
                    emoji_exists = True
                # Also check by name (in case ID lookup fails but emoji is known)
                elif name in server_emoji_cache:
                    e_id = str(server_emoji_cache[name].id)  # Use the correct ID from server
                    emoji_exists = True
                
                if emoji_exists:
                    # Store the full Discord emoji format <:name:id> or <a:name:id>
                    emoji_format = f"<{anim}:{name}:{e_id}>"
                    valid_custom_emojis.append((emoji_format, name, e_id))
                    logger.debug(f"Found valid server emoji: {emoji_format}")
                else:
                    # Remove emojis not in the server
                    pattern = fr'<{anim}:{re.escape(name)}:{e_id}>'
                    content = re.sub(pattern, '', content)
                    logger.debug(f"Removed non-server emoji: <{anim}:{name}:{e_id}>")
        
        # Calculate emoji density
        total_chars = len(original_content)
        emoji_chars = sum(len(e['emoji']) for e in emoji_list) + \
                    sum(len(e[0]) for e in valid_custom_emojis)
        emoji_density = emoji_chars / total_chars if total_chars > 0 else 0
        
        if emoji_density > self.MAX_EMOJI_DENSITY:
            # Combine all emojis
            all_emojis = []
            for e in emoji_list:
                all_emojis.append(('unicode', e['emoji'], e['emoji']))
            for emoji_format, name, e_id in valid_custom_emojis:
                all_emojis.append(('custom', name, emoji_format))
            
            # Keep only limited emojis
            kept_emojis = all_emojis[:self.MAX_EMOJIS_PER_MESSAGE]
            
            # Remove all emojis first
            for e in emoji_list:
                content = content.replace(e['emoji'], ' ')
            
            # Remove all custom emojis from content
            for anim, name, e_id in custom_emoji_matches:
                pattern = fr'<{anim}:{re.escape(name)}:{e_id}>'
                content = re.sub(pattern, ' ', content)
            
            # Add back limited emojis (only valid server emojis)
            for emoji_type, name, emoji_data in kept_emojis:
                if emoji_type == 'unicode':
                    emoji_name = emoji.demojize(emoji_data)[1:-1]
                    content += f" :emoji_{emoji_name}:"
                else:
                    # Only add emojis that exist on server
                    content += f" {emoji_data}"
        else:
            # Convert Unicode emojis to tokens
            for e in emoji_list:
                emoji_name = emoji.demojize(e['emoji'])[1:-1]
                content = content.replace(e['emoji'], f' :emoji_{emoji_name}: ')
            
            # Keep Discord emojis in FULL format - they're already validated
            # No need to convert them, they stay as <:name:id>
            # The invalid ones have already been removed above
        
        # Clean up punctuation (more lenient for Hinglish)
        content = re.sub(r'([!?.]){3,}', r'\1', content)
        
        # Remove excessive whitespace
        content = ' '.join(content.split()).strip()
        
        # Track Hinglish usage
        if content:
            lang_mix = self.detect_language_mix(content)
            if lang_mix['hindi'] > 0.1 or lang_mix['hinglish'] > 0.1:
                self.stats['hinglish_messages'] += 1
        
        # Return if we have enough words
        word_count = len(content.split())
        return content if word_count >= self.MIN_MESSAGE_WORDS else ""


    def classify_word_type(self, word: str, pos: str):
        if word.startswith("<@"):
            return "mention"
        if word.startswith("<:"):
            return "emoji"
        if pos.startswith("NN"):
            return "noun"
        if pos.startswith("VB"):
            return "verb"
        if pos.startswith("JJ"):
            return "adjective"
        if pos.startswith("RB"):
            return "adverb"
        return "other"


    async def train_on_message(self, content: str, author_id: int, emoji_count: int = 0, message=None):
        """Train Markov chain for Hinglish"""
        clean_content = self.sanitize_message(content, message)
        if not clean_content:
            return 0
        
        # Analyze sentiment
        sentiment = self.analyze_sentiment(content)
        self.stats['sentiment_analyzed'] += 1
        
        # Update context
        self.conversation_context.append({
            'content': clean_content,
            'sentiment': sentiment,
            'timestamp': datetime.utcnow()
        })
        self.update_global_topics(clean_content)
        # Update running sentiment
        sentiments = [msg['sentiment'] for msg in self.conversation_context]
        self.conversation_sentiment = sum(sentiments) / len(sentiments) if sentiments else 0.0
        
        words = clean_content.split()
        
        # --- WIRE IN ASYNC NLP HERE ---
        # Use the async wrapper you created to prevent blocking the event loop
        pos_tags_list = await self.get_pos_tags_async(clean_content)
        
        # Fallback if alignment fails
        if len(pos_tags_list) != len(words):
            pos_tags_list = [(w, 'UNK') for w in words]

        triplets = []
        
        # Count Hindi words for stats
        hindi_word_count = sum(1 for word in words if self.is_hindi_word(word))
        self.stats['hindi_words_learned'] += hindi_word_count
        
        for i in range(len(words) - 2):
            word_a, word_b, word_c = words[i], words[i+1], words[i+2]
            
            # Skip if too many emojis in sequence
            emoji_count_seq = sum(1 for w in [word_a, word_b, word_c] 
                                if w.startswith(':emoji_') or w.startswith(':custom_emoji_'))
            if emoji_count_seq >= 2:
                continue
            
            # Simple POS approximation for Hinglish
            pos_a = self.get_pos_tags(word_a)[0][1] if self.get_pos_tags(word_a) else 'UNK'
            pos_b = self.get_pos_tags(word_b)[0][1] if self.get_pos_tags(word_b) else 'UNK'
            pos_c = self.get_pos_tags(word_c)[0][1] if self.get_pos_tags(word_c) else 'UNK'
            
            lemma_a = self.lemmatize_text(word_a)
            lemma_b = self.lemmatize_text(word_b)
            lemma_c = self.lemmatize_text(word_c)

            triplets.append((
                word_a, word_b, word_c,
                lemma_a, lemma_b, lemma_c,
                pos_a, pos_b, pos_c,
                sentiment
            ))
        
        if not triplets:
            return 0
        
        try:
            async with self.get_db() as db: 
                
                # Batch insert with Hinglish information
                batch_data = []
                
                for triplet in triplets:
                    (word_a, word_b, word_c, lemma_a, lemma_b, lemma_c, 
                    pos_a, pos_b, pos_c, sent) = triplet
                    
                    # Calculate word type purely in Python first
                    word_type = self.classify_word_type(word_a, pos_a)
                    
                    # Append 12 parameters matching the 12 placeholders below
                    batch_data.append((
                        word_a, word_b, word_c,          # 1, 2, 3
                        lemma_a, lemma_b, lemma_c,       # 4, 5, 6
                        pos_a, pos_b, pos_c,             # 7, 8, 9
                        sent,                            # 10 (Initial sentiment)
                        word_type,                       # 11 (Word type)
                        sent                             # 12 (Update sentiment value)
                    ))
                
                # Execute single bulk operation (massive speedup)
                if batch_data:
                    await db.executemany('''
                        INSERT INTO markov_chain 
                        (word_a, word_b, next_word, lemma_a, lemma_b, lemma_next,
                        pos_a, pos_b, pos_next, sentiment_score, count, word_type)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                        ON CONFLICT(word_a, word_b, next_word) 
                        DO UPDATE SET 
                            count = count + 1,
                            last_used = CURRENT_TIMESTAMP,
                            sentiment_score = ((sentiment_score * count) + ?) / (count + 1)
                    ''', batch_data)
                
                # Update user stats
                await db.execute('''
                    INSERT INTO user_stats (user_id, message_count, last_seen, emoji_usage, avg_sentiment)
                    VALUES (?, 1, CURRENT_TIMESTAMP, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        message_count = message_count + 1,
                        last_seen = CURRENT_TIMESTAMP,
                        emoji_usage = emoji_usage + ?,
                        avg_sentiment = ((avg_sentiment * (message_count - 1)) + ?) / message_count
                ''', (author_id, emoji_count, sentiment, emoji_count, sentiment))
                
                if message and message.author:
                    await db.execute('''
                        UPDATE user_stats 
                        SET username = ? 
                        WHERE user_id = ? AND (username IS NULL OR username != ?)
                    ''', (str(message.author), author_id, str(message.author)))
                
                await db.execute('''
                    INSERT INTO message_log (content, author_id, channel_id, timestamp, 
                                           emoji_count, sentiment, cleaned_content)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (content, author_id, message.channel.id if message else 0,
                     message.created_at.isoformat() if message else datetime.utcnow().isoformat(),
                     emoji_count, sentiment, clean_content))
                
                await db.commit()
                
                self.stats['triplets_stored_today'] += len(triplets)
                self.stats['emojis_learned'] += emoji_count
                return len(triplets)
                
        except Exception as e:
            logger.error(f"Training error: {e}")
            return 0

    from functools import partial



    def weighted_choice(self, items, temperature=1.0):
        """
        items: List of (value, count)
        """
        if temperature <= 0:
            return max(items, key=lambda x: x[1])[0]

        weights = [math.pow(count, 1 / temperature) for _, count in items]
        total = sum(weights)

        secure_gen = secrets.SystemRandom()
        r = secure_gen.uniform(0, total)
        upto = 0

        for (value, _), w in zip(items, weights):
            upto += w
            if upto >= r:
                return value

        return items[-1][0]

    # Update get_pos_tags to use async version
    async def get_pos_tags_async(self, text: str) -> List[Tuple[str, str]]:
        """Async version of POS tagging"""
        if not text:
            return []
        
        try:
            tokens = await asyncio.get_event_loop().run_in_executor(
                self.nltk_executor, word_tokenize, text
            )
            nltk_tags = await asyncio.get_event_loop().run_in_executor(
                self.nltk_executor, partial(pos_tag, tokens, tagset='universal')
            )
            
            # Apply Hinglish overrides (synchronous part is fast)
            final_tags = []
            for word, tag in nltk_tags:
                word_lower = word.lower()
                
                # Your existing Hinglish overrides...
                if word_lower in ['mein', 'main', 'mujhe', 'tumhe']:
                    final_tags.append((word, 'PRON'))
                elif word_lower in ['nahi', 'na', 'mat']:
                    final_tags.append((word, 'PART'))
                elif word_lower in ['hai', 'hain', 'tha', 'thi']:
                    final_tags.append((word, 'VERB'))
                elif word_lower.endswith(('ka', 'ki', 'ke', 'ko')):
                    final_tags.append((word, 'ADP'))
                elif word_lower in ['acha', 'bura', 'sahi']:
                    final_tags.append((word, 'ADJ'))
                elif word_lower in ['kyu', 'kyun', 'kab']:
                    final_tags.append((word, 'ADV'))
                else:
                    final_tags.append((word, tag))
            
            return final_tags
        except Exception as e:
            logger.error(f"Async POS tagging error: {e}")
            return [(w, 'UNK') for w in text.split()]

    def entropy(self, counts):
        total = sum(counts)
        return -sum((c/total) * math.log(c/total + 1e-9) for c in counts)


    def update_global_topics(self, text: str):
            """Update global topics that persist for hours (Long-Term Memory)"""
            # Extract nouns/proper nouns
            try:
                pos_tags = self.get_pos_tags(text)
                for word, tag in pos_tags:
                    if tag in ['NOUN', 'PROPN'] and len(word) > 3:
                        # Clean word
                        w = word.lower()
                        if w in self.stop_words: continue
                        
                        # Add or refresh topic
                        # We use a timestamp to allow decay over time (e.g., 4 hours)
                        self.global_topics[w] = datetime.utcnow().isoformat()
            except:
                pass
                
            # Cleanup old topics (> 4 hours)
            now = datetime.utcnow()
            to_remove = []
            for topic, timestamp_str in self.global_topics.items():
                ts = datetime.fromisoformat(timestamp_str)
                if (now - ts).total_seconds() > 14400:  # 4 hours
                    to_remove.append(topic)
            
            for t in to_remove:
                del self.global_topics[t]
                
            
    async def generate_sentence(self, seed_word: Optional[str] = None, 
                              context_message: Optional[str] = None) -> Optional[str]:
        """Generate sentence with smart backoff, dynamic temperature, context awareness, entropy filtering, and caching."""
        if not await self.can_generate_reply():
            return None
        
        # --- 1. MERGED TEMPERATURE LOGIC ---
        # Base temperature on sentiment
        temperature = 0.8
        if self.conversation_sentiment > 0.4:
            temperature = 1.1   # Energetic
        elif self.conversation_sentiment < -0.3:
            temperature = 0.6   # Safer/Calmer
            
        # Overwrite if context implies a question (needs precision)
        if context_message and self.is_question(context_message):
            temperature = 0.5
        
        try:
            async with self.get_db() as db: 
                
                db.row_factory = aiosqlite.Row
                
                # Get current context
                current_sentiment = self.conversation_sentiment
                topic_keywords = [kw for kw, _ in self.current_topic_keywords]
                
                # === 2. TIME-AWARE CONTEXT ===
                adjusted_seed_word = self.get_time_based_seed(seed_word) if seed_word else None
                
                # === 3. CONTEXT STICKINESS ===
                recent_topics = []
                if context_message:
                    recent_topics = self.extract_recent_topics(context_message)
                    self.update_topic_boost(recent_topics)
                
                # === ENHANCED HELPER FUNCTION WITH CACHING ===
                async def fetch_rows(sql, params):
                    async with db.execute(sql, params) as cursor:
                        return await cursor.fetchall()
                
                # Helper function for smart backoff query with CACHING
                async def query_next_words(w1: Optional[str] = None, 
                                         w2: Optional[str] = None,
                                         w3: Optional[str] = None) -> List[aiosqlite.Row]:
                    """Query for next words using smart backoff strategy with caching."""
                    queries = []
                    
                    # Try tri-gram first (most specific)
                    if w1 and w2 and w3:
                        sql = '''
                            SELECT next_word, lemma_next, pos_next, count, word_type,
                                sentiment_score, topic_weight FROM markov_chain 
                            WHERE word_a = ? AND word_b = ? AND next_word = ?
                        '''
                        queries.append((sql, [w1, w2, w3]))
                    
                    # Try bi-gram (fallback)
                    if w2 and w3:
                        sql = '''
                            SELECT next_word, lemma_next, pos_next, count, word_type,
                                sentiment_score, topic_weight FROM markov_chain 
                            WHERE word_a = ? AND word_b = ?
                        '''
                        queries.append((sql, [w2, w3]))
                    
                    # Try uni-gram with topic weighting (loosest)
                    if w3:
                        sql = '''
                            SELECT next_word, lemma_next, pos_next, count, word_type,
                                sentiment_score, topic_weight FROM markov_chain 
                            WHERE word_b = ?
                            ORDER BY (count * COALESCE(topic_weight, 1)) DESC
                            LIMIT 20
                        '''
                        queries.append((sql, [w3]))
                    
                    # Execute queries in order with caching
                    for sql, params in queries:
                        # Use caching for database queries
                        cache_key = f"query_next_{sql}_{'_'.join(str(p) for p in params)}"
                        results = await self.get_cached_query(cache_key, fetch_rows, sql, params)
                        if results:
                            return results
                    
                    return []
                
                # Get starting sequence
                start_sequence = None
                
                if adjusted_seed_word:
                    # Try to find starting sequence with seed word
                    sql = '''
                        SELECT word_a, word_b, next_word, lemma_a, lemma_b, lemma_next,
                            pos_a, pos_b, pos_next, count, sentiment_score, topic_weight 
                        FROM markov_chain 
                        WHERE (LOWER(word_a) = LOWER(?) OR LOWER(word_b) = LOWER(?))
                        AND word_type != 'emoji'
                        ORDER BY (count * COALESCE(topic_weight, 1)) DESC
                        LIMIT 5
                    '''
                    
                    # Use caching for seed lookup
                    cache_key = f"seed_{adjusted_seed_word}"
                    candidates = await self.get_cached_query(cache_key, fetch_rows, sql, [adjusted_seed_word, adjusted_seed_word])
                    
                    if candidates:
                        weights = []
                        for row in candidates:
                            weight = row['count']
                            
                            # Apply sentiment matching
                            if self.SENTIMENT_MATCHING:
                                sentiment_diff = abs(row['sentiment_score'] - current_sentiment)
                                sentiment_factor = 1.0 - min(sentiment_diff, 1.0)
                                weight *= sentiment_factor
                            
                            # Apply topic coherence
                            if self.TOPIC_COHERENCE and row['topic_weight']:
                                weight *= row['topic_weight']
                            
                            # Apply context stickiness boost
                            for topic in recent_topics:
                                if (topic in row['word_a'].lower() or 
                                    topic in row['word_b'].lower() or
                                    topic in row['next_word'].lower()):
                                    weight *= self.get_topic_boost(topic)
                            
                            weights.append(weight)
                        
                        # Apply temperature to starting selection
                        if sum(weights) > 0:
                            scaled_weights = self.apply_dynamic_temperature(weights, temperature)
                            start_sequence = secrets.choices(candidates, weights=scaled_weights)[0]
                
                if not start_sequence and context_message:
                    # Extract context words for fallback
                    clean_context = re.sub(r'[^\w\s@#:]', ' ', context_message)
                    words = clean_context.lower().split()
                    
                    context_words = [w for w in words 
                                    if len(w) >= 2  # Shorter for Hinglish
                                    and not w.startswith(('@', '#', ':'))
                                    and w not in self.stop_words]
                    
                    for context_word in context_words[:3]:
                        sql = '''
                            SELECT word_a, word_b, next_word, lemma_a, lemma_b, lemma_next,
                                pos_a, pos_b, pos_next, count, sentiment_score, topic_weight 
                            FROM markov_chain 
                            WHERE (LOWER(word_a) = LOWER(?) OR LOWER(word_b) = LOWER(?))
                            AND word_type != 'emoji'
                            ORDER BY count DESC LIMIT 3
                        '''
                        
                        cache_key = f"context_{context_word}"
                        candidates = await self.get_cached_query(cache_key, fetch_rows, sql, [context_word, context_word])
                        if candidates:
                            start_sequence = secrets.choice(candidates)
                            break
                
                if not start_sequence:
                    # Fallback to random weighted by sentiment
                    sql = '''
                        SELECT word_a, word_b, next_word, lemma_a, lemma_b, lemma_next,
                            pos_a, pos_b, pos_next, count, sentiment_score, topic_weight 
                        FROM markov_chain 
                        WHERE word_type != 'emoji'
                        AND LENGTH(word_a) > 1 AND LENGTH(word_b) > 1
                        ORDER BY ABS(sentiment_score - ?) ASC, RANDOM()
                        LIMIT 10
                    '''
                    
                    cache_key = f"random_fallback_{current_sentiment}"
                    candidates = await self.get_cached_query(cache_key, fetch_rows, sql, [current_sentiment])
                    if candidates:
                        weights = [row['count'] for row in candidates]
                        if sum(weights) > 0:
                            start_sequence = random.choices(candidates, weights=weights)[0]
                
                if not start_sequence:
                    return None
                
                # Start building sentence
                sentence_words = [start_sequence['word_a'], start_sequence['word_b'], start_sequence['next_word']]
                sentence_pos = [start_sequence['pos_a'], start_sequence['pos_b'], start_sequence['pos_next']]
                
                # SMART BACKOFF LOOP WITH ENTROPY FILTERING
                emoji_count = 0
                max_attempts = self.SENTENCE_MAX_WORDS * 3
                
                for attempt in range(max_attempts):
                    if len(sentence_words) >= self.SENTENCE_MAX_WORDS:
                        break
                    
                    # Get the last 3 words for backoff
                    w1 = sentence_words[-3] if len(sentence_words) >= 3 else None
                    w2 = sentence_words[-2] if len(sentence_words) >= 2 else None
                    w3 = sentence_words[-1]
                    pos3 = sentence_pos[-1]
                    
                    # Get next word options using smart backoff with caching
                    options = await query_next_words(w1, w2, w3)
                    
                    if not options:
                        break
                    
                    # === ENTROPY-BASED TEMPERATURE ADJUSTMENT ===
                    counts = [row['count'] for row in options]
                    current_entropy = self.entropy(counts)
                    
                    # Adjust temperature based on entropy
                    # Low entropy = one word dominating = risk of loop = INCREASE TEMP
                    if current_entropy < 0.5: 
                        temp_modifier = 1.5
                    # High entropy = confusing options = risk of nonsense = DECREASE TEMP
                    elif current_entropy > 2.5:
                        temp_modifier = 0.7
                    else:
                        temp_modifier = 1.0
                        
                    adjusted_temp = temperature * temp_modifier
                    
                    # Filter and weight options
                    filtered_options = []
                    base_weights = []
                    
                    for option in options:
                        word_type = option['word_type']
                        next_word = option['next_word']
                        next_pos = option['pos_next']
                        
                        if not next_word or len(next_word.strip()) == 0:
                            continue
                        
                        if word_type == 'emoji' and emoji_count >= self.MAX_REPLY_EMOJIS:
                            continue
                        
                        if len(sentence_words) >= 1 and next_word == sentence_words[-1]:
                            continue
                        
                        weight = option['count']
                        
                        # Apply sentiment matching
                        if self.SENTIMENT_MATCHING:
                            sentiment_diff = abs(option['sentiment_score'] - current_sentiment)
                            sentiment_factor = 1.0 - min(sentiment_diff, 1.0)
                            weight *= sentiment_factor
                        
                        # Apply topic coherence
                        if self.TOPIC_COHERENCE and option['topic_weight']:
                            weight *= option['topic_weight']
                        
                        # Apply context stickiness boost
                        for topic in recent_topics:
                            if topic in next_word.lower():
                                weight *= self.get_topic_boost(topic)
                            if next_word.lower() in self.global_topics:
                                weight *= 1.2
                        
                        # Apply grammar rules if enabled
                        if self.GRAMMAR_ENFORCEMENT:
                            if not self.is_valid_pos_transition(pos3, next_pos):
                                weight *= 0.5
                        
                        filtered_options.append(option)
                        base_weights.append(weight)
                    
                    if not filtered_options:
                        break
                    
                    # Apply dynamic temperature to weights (with entropy adjustment)
                    if sum(base_weights) > 0:
                        final_weights = self.apply_dynamic_temperature(base_weights, adjusted_temp)
                        next_choice = secrets.choices(filtered_options, weights=final_weights)[0]
                    else:
                        next_choice = secrets.choice(filtered_options)
                    
                    # Track emojis
                    if next_choice['word_type'] == 'emoji':
                        emoji_count += 1
                    
                    # Add to sentence
                    sentence_words.append(next_choice['next_word'])
                    sentence_pos.append(next_choice['pos_next'])
                    
                    # Natural stopping conditions
                    last_word = next_choice['next_word']
                    
                    # End on punctuation
                    if last_word.endswith(('.', '!', '?')):
                        if len(sentence_words) >= 5 and secrets.SystemRandom().random() < 0.7:
                            break
                    
                    # End on emoji with decent length
                    elif next_choice['word_type'] == 'emoji':
                        if len(sentence_words) >= 4 and secrets.SystemRandom().random() < 0.4:
                            break
                    
                    # Length-based stopping with temperature influence
                    # Higher temperature = longer sentences (more creative)
                    min_length_for_stop = max(5, int(8 * adjusted_temp))
                    if len(sentence_words) >= min_length_for_stop and secrets.SystemRandom().random() < 0.3:
                        break
                
                # Convert to readable text
                readable_sentence = []
                
                for i, word in enumerate(sentence_words):
                    if word.startswith(':emoji_'):
                        emoji_name = word[7:-1] if word.endswith(':') else word[7:]
                        try:
                            emoji_char = emoji.emojize(f":{emoji_name}:", language='alias')
                            readable_sentence.append(emoji_char)
                        except:
                            readable_sentence.append(f":{emoji_name}:")
                    
                    elif word.startswith('<:') and word.endswith('>'):
                        readable_sentence.append(word)
                    
                    elif word.startswith('@USER_'):
                        user_id = word[6:]
                        readable_sentence.append(f"<@{user_id}>")
                    
                    else:
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
                        is_prev_emoji = (
                            (prev_token.startswith('<:') and prev_token.endswith('>')) or
                            any(char in emoji.EMOJI_DATA for char in prev_token)
                        )
                        
                        if is_prev_emoji:
                            result += ' ' + token
                        elif token in ['.', ',', '!', '?', ':', ';']:
                            result += token
                        else:
                            result += ' ' + token
                
                # Clean up
                result = re.sub(r'\s+', ' ', result).strip()
                result = re.sub(r'(<@\d+>|:[a-zA-Z0-9_]+:)([a-zA-Z])', r'\1 \2', result)
                
                # Capitalize first letter
                if result and result[0].islower():
                    result = result[0].upper() + result[1:]
                
                # Add appropriate punctuation
                if result and not result[-1] in '.!?;':
                    # Check for question patterns
                    last_words = result.lower().split()[-2:]
                    question_detected = any(
                        word in last_words for word in 
                        ['what', 'why', 'how', 'when', 'who', 'where', 'which',
                         'kya', 'kaise', 'kyun', 'kab', 'kahan', 'kaun']
                    )
                    
                    if question_detected:
                        result += '?'
                    elif current_sentiment > 0.3 or any(word in last_words for word in 
                                                      ['wow', 'awesome', 'acha', 'badhiya', 'mast']):
                        result += '!'
                    else:
                        result += '.'
                
                # Final validation
                words_in_result = result.split()
                if len(words_in_result) < 2:  # Allow shorter responses with emojis
                    emoji_in_result = any(
                        (word.startswith('<:') and word.endswith('>')) or 
                        (word in emoji.EMOJI_DATA)
                        for word in words_in_result
                    )
                    if not emoji_in_result:
                        return None
                
                # === APPLY HUMANIZATION ===
                result = self.humanize_response(result, current_sentiment)
                
                # Final clean punctuation
                result = re.sub(r'(?<![:>])\s+([.!?,;])', r'\1', result)
                
                return result
                
        except Exception as e:
            logger.error(f"Enhanced generation error: {e}")
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
        if secrets.SystemRandom().random() < chance or should_reply:
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
                    secure_gen = secrets.SystemRandom()
                    thinking_time = secure_gen.uniform(self.TYPING_MIN_SECONDS, self.TYPING_MAX_SECONDS)
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
                        async with self.get_db() as db: 
                            
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
                        secrets.choice(fallbacks),
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
            async with self.get_db() as db: 
                
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
            
            # --- PATCH: MEMORY LEAK PROTECTION ---
            # Clear user caches if they get too large (over 1000 items)
            if len(self.user_cache) > 1000:
                self.user_cache.clear()
                self.server_emoji_cache.clear()
                self.user_id_map.clear()
                logger.info("Cleared user cache to free RAM")
            # -------------------------------------
            
            self.save_state()
            logger.debug("Periodic tasks completed")
        except Exception as e:
            logger.error(f"Periodic task error: {e}")

    async def cleanup_old_data(self):
        """Clean up old data periodically"""
        try:
            async with self.get_db() as db: 
                
                # 1. Perform deletions first
                cutoff_date = (datetime.utcnow() - timedelta(days=self.RAW_MESSAGE_RETENTION_DAYS)).isoformat()
                await db.execute('DELETE FROM message_log WHERE timestamp < ?', (cutoff_date,))
                
                old_cutoff = (datetime.utcnow() - timedelta(days=self.PRUNE_OLD_TRIPLETS_AFTER_DAYS)).isoformat()
                await db.execute('DELETE FROM markov_chain WHERE last_used < ? AND count < 2', (old_cutoff,))
                await db.commit() # End the transaction here

            # 2. VACUUM must happen on a "fresh" connection outside a transaction
            async with self.get_db() as db: 
                
                await db.execute('PRAGMA vacuum;') # Use PRAGMA for explicit control
                logger.info("Database VACUUM completed successfully.")
                
        except Exception as e:
            logger.error(f"Cleanup error: {e}")

    async def update_stats(self):
        """Update statistics from database"""
        try:
            async with self.get_db() as db: 
                
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
    
    
    @commands.command(name="hinglishstats")
    async def hinglish_stats(self, ctx):
        """Show Hinglish-specific statistics with fixed Regex"""
        try:
            async with self.get_db() as db: 
                # Register the regex function for this connection
                
                db.row_factory = aiosqlite.Row
                
                # Hindi Regex: Matches Devanagari characters
                hindi_regex = r'[\u0900-\u097F]'
                
                async with db.execute(f"""
                    SELECT COUNT(DISTINCT next_word) as unique_hindi_words
                    FROM markov_chain 
                    WHERE next_word REGEXP ?
                    OR LOWER(next_word) IN ('acha', 'thik', 'kya', 'kaise', 'kyun', 'bhai', 'yaar')
                """, (hindi_regex,)) as cursor:
                    hindi_words = await cursor.fetchone()
                
                # ... (rest of your embed code)
                total_messages = self.stats['total_messages']
                h_ratio = (self.stats['hinglish_messages'] / total_messages * 100) if total_messages > 0 else 0
                
                embed = discord.Embed(title="🇮🇳 Hinglish Statistics", color=0x2ecc71)
                embed.add_field(name="Language Mix", value=f"Hinglish Ratio: {h_ratio:.1f}%\nHindi Words: {hindi_words['unique_hindi_words']:,}")
                await ctx.send(embed=embed)
        except Exception as e:
            logger.error(f"Hinglish stats error: {e}")
            await ctx.send(f"❌ Error: {e}")
                
        except Exception as e:
            logger.error(f"Hinglish stats error: {e}")
            await ctx.send(f"❌ Error: {str(e)[:100]}")
    
    @commands.command(name="nlpstatus")
    async def nlp_status(self, ctx):
        """Show NLP enhancement status"""
        embed = discord.Embed(
            title="🧠 NLP Enhancement Status",
            color=discord.Color.purple(),
            timestamp=datetime.utcnow()
        )
        
        # Current settings
        embed.add_field(
            name="Active Features",
            value=f"**Lemmatization:** {'✅' if self.LEMMATIZATION_ENABLED else '❌'}\n"
                  f"**Grammar Rules:** {'✅' if self.GRAMMAR_ENFORCEMENT else '❌'}\n"
                  f"**Sentiment Matching:** {'✅' if self.SENTIMENT_MATCHING else '❌'}\n"
                  f"**Topic Coherence:** {'✅' if self.TOPIC_COHERENCE else '❌'}\n"
                  f"**NLP Model:** {'✅ Loaded' if self.nlp else '❌ Not available'}",
            inline=False
        )
        
        # Current context
        embed.add_field(
            name="Current Conversation",
            value=f"**Messages in context:** {len(self.conversation_context)}\n"
                  f"**Sentiment:** {self.conversation_sentiment:+.2f}\n"
                  f"**Current topics:** {', '.join([kw for kw, _ in self.current_topic_keywords[:3]]) or 'None'}",
            inline=False
        )
        
        # Stats
        embed.add_field(
            name="NLP Statistics",
            value=f"**Sentiment analyzed:** {self.stats['sentiment_analyzed']}\n"
                  f"**Topics detected:** {self.stats['topics_detected']}\n"
                  f"**Lemmas stored:** {self.stats['lemmas_stored']:,}\n"
                  f"**Grammar rules:** {len(self.POS_TRANSITION_RULES)}",
            inline=False
        )
        
        await ctx.send(embed=embed)
    
    @commands.command(name="brainstats")
    async def brain_stats(self, ctx):
        """Show enhanced statistics with NLP info"""
        try:
            async with self.get_db() as db: 
                
                db.row_factory = aiosqlite.Row
                
                # Get comprehensive stats
                async with db.execute("""
                    SELECT 
                        (SELECT COUNT(*) FROM markov_chain) as triplets,
                        (SELECT COUNT(*) FROM markov_chain WHERE word_type = 'emoji') as emoji_triplets,
                        (SELECT COUNT(*) FROM markov_chain WHERE word_type = 'mention') as mention_triplets,
                        (SELECT COUNT(*) FROM message_log) as messages,
                        (SELECT COUNT(*) FROM message_log WHERE DATE(timestamp) = DATE('now')) as today_messages,
                        (SELECT COUNT(DISTINCT author_id) FROM message_log) as unique_users,
                        (SELECT COUNT(*) FROM markov_chain WHERE lemma_a IS NOT NULL) as lemmatized_triplets,
                        (SELECT AVG(sentiment) FROM message_log WHERE sentiment != 0) as avg_sentiment
                """) as cursor:
                    row = await cursor.fetchone()
                
                # Get top users
                async with db.execute("""
                    SELECT user_id, username, message_count, emoji_usage, avg_sentiment
                    FROM user_stats 
                    ORDER BY message_count DESC 
                    LIMIT 5
                """) as cursor:
                    top_users = await cursor.fetchall()
                
                # Update instance stats
                if row:
                    # Safe formatting helpers
                    triplets = row['triplets'] or 0
                    lemmas = row['lemmatized_triplets'] or 0
                    emoji_triplets = row['emoji_triplets'] or 0
                    mention_triplets = row['mention_triplets'] or 0
                    messages = row['messages'] or 0
                    today = row['today_messages'] or 0
                    users = row['unique_users'] or 0
                    
                    # Handle float potentially being None
                    avg_sent = row['avg_sentiment']
                    avg_sent_str = f"{avg_sent:+.2f}" if avg_sent is not None else "0.00"
                    
                    embed = discord.Embed(
                        title="🧠 Enhanced Brain Statistics (NLP)",
                        color=discord.Color.blue(),
                        timestamp=datetime.utcnow()
                    )
                    embed.add_field(
                        name="Knowledge Base",
                        value=f"**{triplets:,}** total triplets\n"
                              f"**{lemmas:,}** lemmatized\n"
                              f"**{emoji_triplets:,}** emoji patterns\n"
                              f"**{mention_triplets:,}** mention patterns",
                        inline=False
                    )
                    
                    embed.add_field(
                        name="Activity & Sentiment",
                        value=f"**{messages:,}** total messages\n"
                              f"**{today}** messages today\n"
                              f"**{users}** unique users\n"
                              f"**{avg_sent_str}** avg sentiment\n"
                              f"**{self.conversation_sentiment:+.2f}** current sentiment",
                        inline=False
                    )
                    
                else:
                    embed.add_field(name="Error", value="Could not fetch statistics", inline=False)
                
                # Top users with sentiment
                if top_users:
                    lines = []
                    for user in top_users:
                        u_id = user['user_id']
                        cnt = user['message_count'] or 0
                        # Safe sentiment formatting
                        sent = user['avg_sentiment']
                        sent_str = f"{sent:+.2f}" if sent is not None else "0.00"
                        lines.append(f"<@{u_id}>: {cnt:,} msgs (sentiment: {sent_str})")
                    
                    top_users_text = "\n".join(lines)
                    embed.add_field(name="Top Contributors", value=top_users_text, inline=False)
                
                # Current topics
                if self.current_topic_keywords:
                    topics = ", ".join([f"`{kw}`" for kw, _ in self.current_topic_keywords[:5]])
                    embed.add_field(name="Current Topics", value=topics, inline=False)
                
                # NLP Status
                embed.add_field(
                    name="NLP Features",
                    value=f"**Lemmatization:** {'✅' if self.LEMMATIZATION_ENABLED else '❌'}\n"
                          f"**Grammar:** {'✅' if self.GRAMMAR_ENFORCEMENT else '❌'}\n"
                          f"**Sentiment:** {'✅' if self.SENTIMENT_MATCHING else '❌'}\n"
                          f"**Topics:** {'✅' if self.TOPIC_COHERENCE else '❌'}",
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
            async with self.get_db() as db: 
                
                db.row_factory = aiosqlite.Row
                
                # Get emoji stats - UPDATED QUERY
                async with db.execute("""
                    SELECT 
                        COUNT(*) as total_emojis,
                        COUNT(DISTINCT next_word) as unique_emojis
                    FROM markov_chain 
                    WHERE word_type = 'emoji'
                """) as cursor:
                    emoji_stats = await cursor.fetchone()
                
                # Get most common emojis - UPDATED QUERY
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
                            f"**{emoji_stats['unique_emojis']}** unique emojis",
                        inline=False
                    )
                
                # Top emojis - UPDATED DISPLAY
                if top_emojis:
                    emoji_list = []
                    for emoji_row in top_emojis:
                        emoji_word = emoji_row['next_word']
                        
                        # Check if it's a Discord emoji format
                        discord_match = re.match(r'^<(a?):(\w+):(\d+)>$', emoji_word)
                        if discord_match:
                            # It's a Discord emoji - show as-is
                            emoji_list.append(f"{emoji_word}: {emoji_row['usage_count']:,}")
                        elif emoji_word.startswith(':emoji_'):
                            # It's a Unicode emoji token
                            emoji_name = emoji_word.replace(':emoji_', '').rstrip(':')
                            try:
                                emoji_char = emoji.emojize(f":{emoji_name}:", language='alias')
                                emoji_list.append(f"{emoji_char}: {emoji_row['usage_count']:,}")
                            except:
                                emoji_list.append(f":{emoji_name}: {emoji_row['usage_count']:,}")
                        else:
                            # Fallback
                            emoji_list.append(f"{emoji_word}: {emoji_row['usage_count']:,}")
                    
                    embed.add_field(
                        name="Most Used Emojis",
                        value="\n".join(emoji_list),
                        inline=False
                    )
                
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
            async with self.get_db() as db: 
                
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
            async with self.get_db() as db: 
                
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
            
            
    @commands.command(name="ingest")
    @commands.has_permissions(administrator=True)
    async def ingest_file(self, ctx):
        """Upload a .txt file to train the bot on Hinglish data"""
        if not ctx.message.attachments:
            await ctx.send("❌ Please upload a `.txt` file with the command.")
            return

        attachment = ctx.message.attachments[0]
        if not attachment.filename.endswith('.txt'):
            await ctx.send("❌ File must be a text file.")
            return

        await ctx.send("⏳ Downloading and processing... This may take a moment.")
        
        try:
            content = await attachment.read()
            text_data = content.decode('utf-8', errors='ignore')
            lines = text_data.split('\n')
            
            count = 0
            async with ctx.typing():
                for line in lines:
                    line = line.strip()
                    if len(line) < 5: continue
                    
                    # Train
                    await self.train_on_message(line, self.bot.user.id)
                    count += 1
                    
                    # Yield to event loop every 100 lines
                    if count % 100 == 0:
                        await asyncio.sleep(0)
                        
            self.save_state()
            await ctx.send(f"✅ Successfully ingested **{count}** lines of training data!")
            
        except Exception as e:
            await ctx.send(f"❌ Error processing file: {e}")

async def setup(bot):
    await bot.add_cog(EnhancedMarkovCog(bot))