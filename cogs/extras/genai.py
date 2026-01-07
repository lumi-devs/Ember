import discord
from discord.ext import commands
import markovify
import sqlite3
import random
import re
import asyncio
from datetime import datetime, timedelta
import logging
import aiosqlite
from collections import deque
import os
from contextlib import asynccontextmanager

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class GenAICog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_path = "./messages.db"
        
        # In-memory caches for performance
        self.models = {}                    # Cached markov models
        self.model_cache_time = {}          # Last model generation time
        self.recent_messages_cache = {}     # LRU cache for recent messages (1000 per channel)
        self.reply_cooldowns = {}           # Reply cooldown tracking
        self.conversation_context = {}      # Recent conversation topics
        
        # Configuration
        self.MAX_MESSAGES_PER_CHANNEL = 2000000  # 2 million messages
        self.MIN_MESSAGES_FOR_MODEL = 50
        self.RECENT_MESSAGES_CACHE_SIZE = 1000   # Keep recent messages in RAM
        self.REPLY_CHANCE = 0.02
        self.MODEL_CACHE_TTL = 300               # 5 minutes
        self.REPLY_COOLDOWN = 30
        self.MAX_MESSAGE_LENGTH = 2000
        self.MESSAGES_PER_SAVE = 50              # Batch saves to DB
        self.REPLY_ON_PING = True                # ALWAYS reply when pinged (your requested feature)
        
        # Keyword triggers
        self.KEYWORD_TRIGGERS = {
            'what do you think': 0.8,
            'lol': 0.3,
            'haha': 0.3,
            '?': 0.4,
            '!': 0.2
        }
        
        # Message buffer for batch saving
        self.message_buffer = []
        self.buffer_lock = asyncio.Lock()
        
        # Initialize database
        self.bot.loop.create_task(self.initialize_database())
        self.bot.loop.create_task(self.buffer_flusher())
    
    @asynccontextmanager
    async def get_db(self):
        """Async context manager for database connections"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            yield db
    
    async def initialize_database(self):
        """Initialize SQLite database with proper indexes"""
        await self.bot.wait_until_ready()
        
        async with self.get_db() as db:
            # Create tables
            await db.execute('''
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    cleaned INTEGER DEFAULT 0
                )
            ''')
            
            await db.execute('''
                CREATE TABLE IF NOT EXISTS channels (
                    channel_id INTEGER PRIMARY KEY,
                    message_count INTEGER DEFAULT 0,
                    last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Create indexes for performance
            await db.execute('CREATE INDEX IF NOT EXISTS idx_channel_id ON messages(channel_id)')
            await db.execute('CREATE INDEX IF NOT EXISTS idx_timestamp ON messages(timestamp)')
            await db.execute('CREATE INDEX IF NOT EXISTS idx_channel_timestamp ON messages(channel_id, timestamp)')
            
            await db.commit()
            
            # Pre-cache recent messages for active channels
            await self.warmup_caches()
    
    async def warmup_caches(self):
        """Warm up caches on startup"""
        async with self.get_db() as db:
            # Get all channels with messages
            async with db.execute('''
                SELECT channel_id, COUNT(*) as count 
                FROM messages 
                GROUP BY channel_id 
                HAVING count > 0
            ''') as cursor:
                channels = await cursor.fetchall()
                
                for channel in channels:
                    channel_id = channel['channel_id']
                    # Load recent messages into cache
                    await self.load_recent_messages(channel_id)
    
    async def load_recent_messages(self, channel_id):
        """Load recent messages from DB into memory cache"""
        async with self.get_db() as db:
            async with db.execute('''
                SELECT content 
                FROM messages 
                WHERE channel_id = ? 
                ORDER BY timestamp DESC 
                LIMIT ?
            ''', (channel_id, self.RECENT_MESSAGES_CACHE_SIZE)) as cursor:
                messages = await cursor.fetchall()
                
                if messages:
                    # Store in reverse order (newest first) but as deque for easy appends
                    self.recent_messages_cache[channel_id] = deque(
                        [msg['content'] for msg in reversed(messages)], 
                        maxlen=self.RECENT_MESSAGES_CACHE_SIZE
                    )
    
    async def buffer_flusher(self):
        """Periodically flush message buffer to database"""
        while True:
            await asyncio.sleep(30)  # Flush every 30 seconds
            await self.flush_buffer()
    
    async def flush_buffer(self):
        """Flush buffered messages to database"""
        if not self.message_buffer:
            return
        
        async with self.buffer_lock:
            if not self.message_buffer:
                return
            
            buffer_copy = self.message_buffer.copy()
            self.message_buffer.clear()
            
            try:
                async with self.get_db() as db:
                    # Insert in batch
                    await db.executemany('''
                        INSERT INTO messages (channel_id, content, timestamp)
                        VALUES (?, ?, ?)
                    ''', [(msg['channel_id'], msg['content'], msg['timestamp']) for msg in buffer_copy])
                    
                    # Update channel counts
                    for msg in buffer_copy:
                        await db.execute('''
                            INSERT OR REPLACE INTO channels (channel_id, message_count, last_updated)
                            VALUES (
                                ?,
                                COALESCE((SELECT message_count FROM channels WHERE channel_id = ?), 0) + 1,
                                CURRENT_TIMESTAMP
                            )
                        ''', (msg['channel_id'], msg['channel_id']))
                    
                    await db.commit()
                    
                    logger.debug(f"Flushed {len(buffer_copy)} messages to database")
                    
                    # Update recent messages cache
                    for msg in buffer_copy:
                        channel_id = msg['channel_id']
                        if channel_id not in self.recent_messages_cache:
                            self.recent_messages_cache[channel_id] = deque(maxlen=self.RECENT_MESSAGES_CACHE_SIZE)
                        self.recent_messages_cache[channel_id].append(msg['content'])
                        
                        # Keep cache size in check
                        if len(self.recent_messages_cache[channel_id]) > self.RECENT_MESSAGES_CACHE_SIZE:
                            self.recent_messages_cache[channel_id].popleft()
                    
                    # Enforce per-channel message limit
                    await self.enforce_message_limits()
                    
            except Exception as e:
                logger.error(f"Error flushing buffer: {e}")
                # Re-add messages to buffer if failed
                self.message_buffer.extend(buffer_copy)
    
    async def enforce_message_limits(self):
        """Enforce 2 million messages per channel limit"""
        async with self.get_db() as db:
            # Get channels exceeding limit
            async with db.execute('''
                SELECT channel_id, COUNT(*) as count 
                FROM messages 
                GROUP BY channel_id 
                HAVING count > ?
            ''', (self.MAX_MESSAGES_PER_CHANNEL,)) as cursor:
                channels = await cursor.fetchall()
                
                for channel in channels:
                    channel_id = channel['channel_id']
                    excess = channel['count'] - self.MAX_MESSAGES_PER_CHANNEL
                    
                    if excess > 0:
                        # Delete oldest messages
                        await db.execute('''
                            DELETE FROM messages 
                            WHERE id IN (
                                SELECT id FROM messages 
                                WHERE channel_id = ? 
                                ORDER BY timestamp ASC 
                                LIMIT ?
                            )
                        ''', (channel_id, excess))
                        
                        await db.execute('''
                            UPDATE channels 
                            SET message_count = message_count - ? 
                            WHERE channel_id = ?
                        ''', (excess, channel_id))
                        
                        logger.info(f"Removed {excess} old messages from channel {channel_id}")
            
            await db.commit()
    
    def sanitize_message(self, content):
        """Clean message content"""
        if not content:
            return ""
        
        # Remove command invocations
        if content.startswith(self.bot.command_prefix):
            return ""
        
        # Remove URLs
        content = re.sub(r'https?://\S+', '', content)
        
        # Remove excessive whitespace
        content = re.sub(r'\s+', ' ', content)
        
        # Remove markdown formatting
        content = re.sub(r'[`*_~|]', '', content)
        
        return content.strip()
    
    async def add_message(self, channel_id, content):
        """Add message to buffer for batch saving"""
        clean_content = self.sanitize_message(content)
        if not clean_content or len(clean_content) < 2:
            return
        
        message_data = {
            'channel_id': channel_id,
            'content': clean_content,
            'timestamp': datetime.utcnow().isoformat()
        }
        
        async with self.buffer_lock:
            self.message_buffer.append(message_data)
            
            # Update recent cache immediately
            if channel_id not in self.recent_messages_cache:
                self.recent_messages_cache[channel_id] = deque(maxlen=self.RECENT_MESSAGES_CACHE_SIZE)
            self.recent_messages_cache[channel_id].append(clean_content)
            
            # Update conversation context
            words = [w.lower() for w in clean_content.split() if len(w) > 3]
            if words:
                self.conversation_context[channel_id] = words[-5:]
        
        # Auto-flush if buffer gets large
        if len(self.message_buffer) >= self.MESSAGES_PER_SAVE * 2:
            await self.flush_buffer()
    
    async def get_messages_for_model(self, channel_id, limit=50000):
        """Get messages for model building with intelligent sampling"""
        messages = []
        
        # 1. Always include recent messages from cache
        if channel_id in self.recent_messages_cache:
            messages.extend(list(self.recent_messages_cache[channel_id]))
        
        # 2. If we need more, get from database with intelligent sampling
        if len(messages) < 500:  # Minimum for decent model
            async with self.get_db() as db:
                # Get total count first
                async with db.execute('SELECT COUNT(*) as count FROM messages WHERE channel_id = ?', (channel_id,)) as cursor:
                    total_count = (await cursor.fetchone())['count']
                
                if total_count > 10000:
                    # For large datasets, sample intelligently
                    # Get recent messages (last 10000)
                    async with db.execute('''
                        SELECT content FROM messages 
                        WHERE channel_id = ? 
                        ORDER BY timestamp DESC 
                        LIMIT 10000
                    ''', (channel_id,)) as cursor:
                        recent = await cursor.fetchall()
                        messages.extend([msg['content'] for msg in recent])
                    
                    # Get random sample from older messages
                    sample_size = min(10000, total_count - 10000)
                    if sample_size > 0:
                        async with db.execute(f'''
                            SELECT content FROM messages 
                            WHERE channel_id = ? 
                            AND id IN (
                                SELECT id FROM messages 
                                WHERE channel_id = ? 
                                ORDER BY RANDOM() 
                                LIMIT ?
                            )
                        ''', (channel_id, channel_id, sample_size)) as cursor:
                            random_sample = await cursor.fetchall()
                            messages.extend([msg['content'] for msg in random_sample])
                else:
                    # For small datasets, get everything
                    async with db.execute('SELECT content FROM messages WHERE channel_id = ?', (channel_id,)) as cursor:
                        all_messages = await cursor.fetchall()
                        messages.extend([msg['content'] for msg in all_messages])
        
        return messages
    
    async def get_model(self, channel_id, force_refresh=False):
        """Get or create markov model with intelligent caching"""
        now = datetime.utcnow()
        
        # Check cache first
        if (not force_refresh and 
            channel_id in self.models and 
            channel_id in self.model_cache_time and
            (now - self.model_cache_time[channel_id]).seconds < self.MODEL_CACHE_TTL):
            return self.models[channel_id]
        
        # Get messages for model
        messages = await self.get_messages_for_model(channel_id)
        
        if len(messages) < self.MIN_MESSAGES_FOR_MODEL:
            return None
        
        try:
            # Weight recent messages more heavily
            recent_weight = 3
            weighted_messages = []
            
            if len(messages) > 1000:
                # Recent messages get repeated for weight
                recent = messages[-1000:] * recent_weight
                older = messages[:-1000]
                weighted_messages = older + recent
            else:
                weighted_messages = messages * recent_weight
            
            # Create model
            text = "\n".join(weighted_messages)
            message_count = len(weighted_messages)
            state_size = 2 if message_count < 1000 else (3 if message_count < 10000 else 2)
            
            model = markovify.NewlineText(text, state_size=state_size)
            
            # Cache the model
            self.models[channel_id] = model
            self.model_cache_time[channel_id] = now
            
            # Prune old models
            if len(self.models) > 50:
                oldest_channel = min(self.model_cache_time.items(), key=lambda x: x[1])[0]
                del self.models[oldest_channel]
                del self.model_cache_time[oldest_channel]
            
            return model
            
        except Exception as e:
            logger.error(f"Error creating model for {channel_id}: {e}")
            return None
    
    def calculate_reply_probability(self, message, is_mentioned):
        """Calculate reply probability"""
        if is_mentioned:
            return 1.0  # Always reply when pinged
        
        base_chance = self.REPLY_CHANCE
        
        # Check keyword triggers
        content_lower = message.content.lower()
        for keyword, boost in self.KEYWORD_TRIGGERS.items():
            if keyword in content_lower:
                base_chance += boost
        
        # Conversation activity boost
        channel_id = message.channel.id
        if channel_id in self.conversation_context:
            base_chance *= 1.5
        
        # Cooldown reduction
        if channel_id in self.reply_cooldowns:
            elapsed = datetime.utcnow() - self.reply_cooldowns[channel_id]
            if elapsed.seconds < self.REPLY_COOLDOWN:
                base_chance *= 0.1
        
        # Message length boost
        if len(message.content) > 20:
            base_chance *= 1.2
        
        return min(base_chance, 0.8)
    
    async def generate_response(self, model, context_message, channel_id):
        """Generate a response using multiple strategies"""
        response = None
        
        # Strategy 1: Try to continue from user's message
        if context_message:
            user_words = [w for w in context_message.split() if len(w) > 3]
            random.shuffle(user_words)
            
            for word in user_words[:3]:
                try:
                    response = model.make_sentence_with_start(
                        word, 
                        strict=False, 
                        tries=50,
                        max_words=random.randint(5, 30)
                    )
                    if response:
                        break
                except:
                    continue
        
        # Strategy 2: Use conversation context
        if not response and channel_id in self.conversation_context:
            context_words = self.conversation_context[channel_id]
            for word in random.sample(context_words, min(3, len(context_words))):
                try:
                    response = model.make_sentence_with_start(word, strict=False, tries=30)
                    if response:
                        break
                except:
                    continue
        
        # Strategy 3: Random generation
        if not response:
            for _ in range(3):
                response = model.make_sentence(
                    tries=100,
                    max_words=random.randint(4, 25),
                    min_words=random.randint(3, 8)
                )
                if response:
                    break
        
        # Post-process
        if response:
            response = response.strip()
            if response and not response.endswith(('.', '!', '?')):
                response += random.choice(['.', '!', '?'])
            
            if len(response) > self.MAX_MESSAGE_LENGTH:
                response = response[:self.MAX_MESSAGE_LENGTH - 3] + "..."
            
            # Update context
            words = [w.lower() for w in response.split() if len(w) > 3]
            if words:
                self.conversation_context[channel_id] = words[-5:]
        
        return response
    
    @commands.Cog.listener()
    async def on_message(self, message):
        """Main message handler"""
        # Ignore bots
        if message.author.bot:
            return
        
        channel_id = message.channel.id
        
        # --- TRAINING PHASE ---
        await self.add_message(channel_id, message.content)
        
        # --- GENERATION PHASE ---
        # Check if pinged (YOUR REQUESTED FEATURE)
        is_mentioned = self.bot.user in message.mentions
        
        # Calculate reply probability
        reply_prob = self.calculate_reply_probability(message, is_mentioned)
        
        # ALWAYS reply when pinged (if we have data)
        should_reply = is_mentioned or random.random() < reply_prob
        
        if should_reply and message.content:
            # Check cooldown
            if channel_id in self.reply_cooldowns:
                elapsed = datetime.utcnow() - self.reply_cooldowns[channel_id]
                if elapsed.seconds < self.REPLY_COOLDOWN:
                    return
            
            async with message.channel.typing():
                # Natural typing delay
                await asyncio.sleep(random.uniform(0.5, 2.0))
                
                model = await self.get_model(channel_id)
                
                if not model:
                    if is_mentioned:
                        await message.channel.send(
                            random.choice([
                                "I need more chat data to generate responses!",
                                "Keep chatting and I'll learn from you!",
                                "Not enough messages to work with yet!"
                            ])
                        )
                    return
                
                # Generate response
                clean_text = self.sanitize_message(message.content)
                context = clean_text if clean_text else None
                response = await self.generate_response(model, context, channel_id)
                
                if response:
                    # Add emoji occasionally
                    if random.random() < 0.1:
                        emojis = ['🤖', '💭', '🤔', '😊', '⚡']
                        response += f" {random.choice(emojis)}"
                    
                    try:
                        await message.channel.send(response)
                        self.reply_cooldowns[channel_id] = datetime.utcnow()
                        
                        # Small chance for follow-up
                        if random.random() < 0.05:
                            await asyncio.sleep(random.uniform(2, 5))
                            follow_up = model.make_sentence(tries=50)
                            if follow_up and len(follow_up) < 100:
                                await message.channel.send(follow_up)
                                
                    except discord.HTTPException as e:
                        logger.error(f"Error sending message: {e}")
    
    @commands.command(name="force", aliases=["f"])
    async def force_gen(self, ctx, seed_word: str = None):
        """Force generate a message"""
        async with ctx.channel.typing():
            model = await self.get_model(ctx.channel.id)
            
            if not model:
                await ctx.send("❌ Not enough data to generate yet!")
                return
            
            response = None
            if seed_word:
                try:
                    response = model.make_sentence_with_start(seed_word, strict=False, tries=100)
                except:
                    pass
            
            if not response:
                response = model.make_sentence(tries=150)
            
            if response:
                await ctx.send(response)
            else:
                await ctx.send("🤔 Couldn't generate a coherent message.")
    
    @commands.command(name="stats")
    async def stats(self, ctx):
        """Show statistics"""
        channel_id = ctx.channel.id
        
        async with self.get_db() as db:
            # Get message count
            async with db.execute('SELECT COUNT(*) as count FROM messages WHERE channel_id = ?', (channel_id,)) as cursor:
                message_count = (await cursor.fetchone())['count']
            
            # Get channel info
            async with db.execute('SELECT * FROM channels WHERE channel_id = ?', (channel_id,)) as cursor:
                channel_info = await cursor.fetchone()
        
        embed = discord.Embed(
            title="🤖 Markov Bot Statistics",
            color=discord.Color.blue(),
            timestamp=datetime.utcnow()
        )
        
        embed.add_field(name="Messages Stored", value=f"{message_count:,}", inline=True)
        
        if channel_info:
            last_updated = datetime.fromisoformat(channel_info['last_updated'])
            embed.add_field(
                name="Last Updated", 
                value=f"<t:{int(last_updated.timestamp())}:R>", 
                inline=True
            )
        
        if channel_id in self.models:
            embed.add_field(name="Model Status", value="✅ Cached", inline=True)
            cache_age = (datetime.utcnow() - self.model_cache_time[channel_id]).seconds
            embed.add_field(name="Cache Age", value=f"{cache_age}s", inline=True)
        
        if channel_id in self.recent_messages_cache:
            embed.add_field(name="Memory Cache", value=f"{len(self.recent_messages_cache[channel_id])} messages", inline=True)
        
        await ctx.send(embed=embed)
    
    @commands.command(name="clearmodel", aliases=["reload"])
    @commands.has_permissions(manage_messages=True)
    async def clear_model(self, ctx):
        """Clear cached model"""
        if ctx.channel.id in self.models:
            del self.models[ctx.channel.id]
            del self.model_cache_time[ctx.channel.id]
            await ctx.send("✅ Model cache cleared!")
        else:
            await ctx.send("No cached model found.")
    
    @commands.command(name="flush")
    @commands.has_permissions(manage_messages=True)
    async def flush_now(self, ctx):
        """Manually flush buffer to database"""
        count = len(self.message_buffer)
        await self.flush_buffer()
        await ctx.send(f"💾 Flushed {count} messages to database!")
    
    @commands.command(name="pingme")
    async def ping_test(self, ctx):
        """Test ping response"""
        await ctx.send(f"Hey {ctx.author.mention}, I'm listening! Try mentioning me in a message.")

async def setup(bot):
    await bot.add_cog(GenAICog(bot))