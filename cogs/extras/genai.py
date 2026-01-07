import discord
from discord.ext import commands
import markovify
import aiosqlite
import random
import re
import asyncio
from datetime import datetime
import logging
from collections import deque
import functools

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("GlobalGenAI")

class GlobalGenAICog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_path = "./global_messages.db"
        
        # Global Model State
        self.global_model = None
        self.model_timestamp = None
        self.is_building_model = False
        
        # Caches
        self.recent_messages = deque(maxlen=2000) 
        self.reply_cooldowns = {}
        
        # --- HIGH TRAFFIC CONFIGURATION ---
        self.FLUSH_THRESHOLD = 200     # Increased for high traffic (flush less often, bigger batches)
        self.MIN_MESSAGES_FOR_MODEL = 500
        self.MAX_TRAINING_SAMPLE = 100000  # Increased to remember ~4 days of chat (requires ~1-2GB RAM)
        self.REPLY_CHANCE = 0.02
        self.MODEL_TTL = 1800          # Rebuild every 30 mins (10 mins is too frequent for 25k/day)
        self.REPLY_COOLDOWN = 5
        
        # Buffers
        self.message_buffer = []
        self.buffer_lock = asyncio.Lock()
        
        # Start background tasks
        self.bot.loop.create_task(self.initialize_database())
        self.bot.loop.create_task(self.buffer_flusher())

    async def initialize_database(self):
        """Initialize SQLite database with WAL mode for concurrency"""
        await self.bot.wait_until_ready()
        async with aiosqlite.connect(self.db_path) as db:
            # OPTIMIZATION: Enable Write-Ahead Logging (WAL)
            # This allows reading and writing at the same time (Crucial for active servers)
            await db.execute('PRAGMA journal_mode=WAL;')
            await db.execute('PRAGMA synchronous=NORMAL;')
            
            await db.execute('''
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT NOT NULL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            await db.execute('CREATE INDEX IF NOT EXISTS idx_timestamp ON messages(timestamp)')
            await db.commit()
            
        logger.info("Database initialized (WAL Mode ON). Warming up model...")
        await self.rebuild_global_model()

    # --- DATA INGESTION ---

    def sanitize_message(self, content):
        if not content: return ""
        if content.startswith(self.bot.command_prefix): return ""
        
        # Filter typical bot command spam
        if content.startswith(('!', '/', '?', '.', ';')): return ""

        # Remove URLs, Mentions, Custom Emojis
        content = re.sub(r'https?://\S+', '', content)
        content = re.sub(r'<a?:\w+:\d+>', '', content)
        content = re.sub(r'<@&?\d+>', '', content)
        
        return " ".join(content.split())

    async def add_message(self, content):
        """Add message to buffer"""
        clean_content = self.sanitize_message(content)
        if not clean_content or len(clean_content.split()) < 2:
            return

        should_flush = False
        async with self.buffer_lock:
            self.message_buffer.append({
                'content': clean_content,
                'timestamp': discord.utils.utcnow().isoformat()
            })
            
            if len(self.message_buffer) >= self.FLUSH_THRESHOLD:
                should_flush = True
            
        self.recent_messages.append(clean_content)
        
        # Non-blocking flush trigger
        if should_flush:
            asyncio.create_task(self.flush_buffer())

    async def buffer_flusher(self):
        """Backup timer flush (every 30s)"""
        while True:
            await asyncio.sleep(30)
            await self.flush_buffer()

    async def flush_buffer(self):
        """Flush buffer to DB safely"""
        # Check if empty first to avoid locking
        if not self.message_buffer:
            return

        async with self.buffer_lock:
            if not self.message_buffer:
                return
            to_save = self.message_buffer.copy()
            self.message_buffer.clear()

        if to_save:
            try:
                async with aiosqlite.connect(self.db_path) as db:
                    await db.executemany(
                        'INSERT INTO messages (content, timestamp) VALUES (?, ?)',
                        [(m['content'], m['timestamp']) for m in to_save]
                    )
                    await db.commit()
                # Reduce log noise - only log errors or large flushes
                if len(to_save) > 50:
                    logger.debug(f"Saved {len(to_save)} messages.")
            except Exception as e:
                logger.error(f"Failed to save messages: {e}")

    # --- MODEL HANDLING ---

    def _build_markov_process(self, text_data):
        try:
            # state_size=2 is faster and uses less RAM than 3
            return markovify.NewlineText(text_data, state_size=2, well_formed=False)
        except Exception as e:
            return None

    async def get_training_data(self):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            
            # Fast count estimate (exact count is slow on large tables)
            async with db.execute("SELECT MAX(id) FROM messages") as cursor:
                row = await cursor.fetchone()
                total = row[0] if row and row[0] else 0

            if total < self.MIN_MESSAGES_FOR_MODEL:
                return []

            limit_recent = int(self.MAX_TRAINING_SAMPLE * 0.8) # 80% recent for relevance
            limit_random = int(self.MAX_TRAINING_SAMPLE * 0.2)

            async with db.execute(f"SELECT content FROM messages ORDER BY id DESC LIMIT {limit_recent}") as cursor:
                recents = [row['content'] for row in await cursor.fetchall()]

            randoms = []
            if total > limit_recent:
                # Optimized random selection for SQLite
                async with db.execute(f"SELECT content FROM messages WHERE id IN (SELECT id FROM messages ORDER BY RANDOM() LIMIT {limit_random})") as cursor:
                    randoms = [row['content'] for row in await cursor.fetchall()]

            return recents + randoms

    async def rebuild_global_model(self):
            if self.is_building_model: return
            self.is_building_model = True
            logger.info("Rebuilding global model...")

            try:
                data = await self.get_training_data()
                
                # --- FIX: Handle low data gracefully ---
                if not data:
                    logger.warning(f"Not enough data to build model yet. (Need {self.MIN_MESSAGES_FOR_MODEL} messages)")
                    # Set timestamp anyway so we don't try again for another 10-30 mins
                    self.model_timestamp = discord.utils.utcnow()
                    return
                # ---------------------------------------

                text_block = "\n".join(data)
                
                new_model = await self.bot.loop.run_in_executor(
                    None, 
                    functools.partial(self._build_markov_process, text_block)
                )

                if new_model:
                    self.global_model = new_model
                    self.model_timestamp = discord.utils.utcnow()
                    logger.info(f"Model updated. Lines: {len(data)}")
            except Exception as e:
                logger.error(f"Error rebuilding model: {e}")
                # Prevent retry spam on error too
                self.model_timestamp = discord.utils.utcnow()
            finally:
                self.is_building_model = False

    async def ensure_model_freshness(self):
        if not self.model_timestamp:
            asyncio.create_task(self.rebuild_global_model())
            return
            
        age = (discord.utils.utcnow() - self.model_timestamp).seconds
        if age > self.MODEL_TTL and not self.is_building_model:
            asyncio.create_task(self.rebuild_global_model())

    async def generate_reply(self, message):
        # Fire and forget model check
        asyncio.create_task(self.ensure_model_freshness())
        
        if not self.global_model: return None

        response = None
        user_words = [w for w in message.content.split() if len(w) > 4]
        
        if user_words:
            try:
                seed = random.choice(user_words)
                response = self.global_model.make_sentence_with_start(seed, tries=10, strict=False)
            except: pass

        if not response:
            response = self.global_model.make_sentence(tries=30, max_overlap_ratio=0.7)

        return response

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot: return
        if message.guild is None: return
        
        # 1. Save data (Non-blocking)
        asyncio.create_task(self.add_message(message.content))

        # 2. Check Triggers
        is_mentioned = self.bot.user in message.mentions
        is_reply_to_me = False

        # Check if this is a reply to the bot
        if message.reference:
            # Try to get the message object from cache/reference
            ref_msg = message.reference.cached_message or message.reference.resolved
            if ref_msg and ref_msg.author.id == self.bot.user.id:
                is_reply_to_me = True

        # 3. Decision Logic
        # Reply if mentioned OR if it's a direct reply to the bot
        should_reply = is_mentioned or is_reply_to_me
        
        # Set Probability
        chance = 1.0 if should_reply else self.REPLY_CHANCE

        # 4. Execution
        if random.random() < chance or should_reply:
            # Cooldown Check (Skip if mentioned/replied to)
            if not should_reply and message.channel.id in self.reply_cooldowns:
                if (discord.utils.utcnow() - self.reply_cooldowns[message.channel.id]).seconds < self.REPLY_COOLDOWN:
                    return

            async with message.channel.typing():
                # Random "thinking" delay
                await asyncio.sleep(random.uniform(0.5, 1.0))
                
                reply = await self.generate_reply(message)
                
                if reply:
                    # If the user replied to us, let's reply back to them specifically
                    await message.channel.send(reply, reference=message if should_reply else None, mention_author=False)
                    self.reply_cooldowns[message.channel.id] = discord.utils.utcnow()
                
                # --- NEW FALLBACK LOGIC ---
                elif should_reply:
                    # If user pinged/replied but we have no response (Empty Model), tell them why.
                    try:
                        async with aiosqlite.connect(self.db_path) as db:
                            async with db.execute("SELECT COUNT(*) as count FROM messages") as cursor:
                                result = await cursor.fetchone()
                                # FIX 2: Use integer index [0] instead of string key ['count']
                                # because we didn't set a row_factory for this specific connection
                                count = result[0] if result else 0
                        
                        needed = self.MIN_MESSAGES_FOR_MODEL - count
                        
                        if needed > 0:
                            await message.channel.send(f"🧠 I need to read **{needed}** more messages before I can start talking!")
                        else:
                            await message.channel.send("😴 I'm currently rebuilding my brain... try again in a minute!")
                    except Exception as e:
                        logger.error(f"Error sending fallback message: {e}")

    @commands.command(name="stats")
    async def stats(self, ctx):
        db_count = 0
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT MAX(id) FROM messages") as cursor:
                row = await cursor.fetchone()
                db_count = row[0] if row and row[0] else 0

        buffer_count = len(self.message_buffer)
        
        embed = discord.Embed(title="📊 High-Load Stats", color=discord.Color.gold())
        embed.add_field(name="Total Messages", value=f"**{db_count + buffer_count:,}**", inline=False)
        embed.add_field(name="Buffer", value=f"{buffer_count} / {self.FLUSH_THRESHOLD}", inline=True)
        embed.add_field(name="Model Size", value=f"{self.MAX_TRAINING_SAMPLE:,} lines", inline=True)
        
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(GlobalGenAICog(bot))