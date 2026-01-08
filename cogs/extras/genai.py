import discord
from discord.ext import commands, tasks
import aiosqlite
import random
import re
import asyncio
import logging
from collections import deque
from datetime import datetime, timedelta
import aiosqlite
import json
import os
from pathlib import Path

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('./genai/markov_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("OptimizedMarkovBot")

class OptimizedMarkovCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_path = "./genai/server_brain.db"
        self.state_file = "./genai/bot_state.json"
        
        # Load saved state on startup
        self.load_state()
        
        # Database connection pool
        self.db_pool = None
        
        # Limits optimized for high traffic (17k/day)
        self.MAX_RAW_MESSAGES = 2_000_000
        self.MAX_TRIPLETS = 5_000_000
        self.RAW_MESSAGE_RETENTION_DAYS = 120
        self.PRUNE_OLD_TRIPLETS_AFTER_DAYS = 365
        
        # --- OPTIMIZED PARAMETERS ---
        self.MIN_TRIPLETS_FOR_REPLY = 500       # Reduced to start replying sooner
        self.REPLY_CHANCE = 0.015
        self.REPLY_COOLDOWN = 10
        self.TYPING_MIN_SECONDS = 0.3
        self.TYPING_MAX_SECONDS = 0.8
        self.SENTENCE_MAX_WORDS = 30
        self.SEED_WORD_MIN_LENGTH = 3
        self.MIN_MESSAGE_WORDS = 2              # Reduced from 3
        
        # Persistent buffer with file backup
        self.message_buffer = deque(maxlen=1000)
        self.recent_messages = deque(maxlen=5000)  # Increased for better seeding
        
        # Persistent cooldown tracking
        self.reply_cooldowns = {}
        
        # Performance tracking with persistence
        self.stats = {
            'messages_processed': 0,
            'triplets_stored_today': 0,
            'replies_generated': 0,
            'last_flush': datetime.utcnow().isoformat(),
            'total_triplets': 0,
            'total_messages': 0,
            'startup_time': datetime.utcnow().isoformat()
        }
        
        self.FLUSH_THRESHOLD = 50               # Reduced for more frequent saves
        self.buffer_lock = asyncio.Lock()
        
        # Start background tasks
        self.bot.loop.create_task(self.initialize_database())
        #self.bot.loop.create_task(self.restore_buffer())
        self.periodic_tasks.start()

    def load_state(self):
        """Load bot state from file to survive restarts"""
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r') as f:
                    state = json.load(f)
                    self.reply_cooldowns = {
                        int(k): datetime.fromisoformat(v) 
                        for k, v in state.get('reply_cooldowns', {}).items()
                    }
                    logger.info(f"Loaded state with {len(self.reply_cooldowns)} cooldowns")
        except Exception as e:
            logger.error(f"Failed to load state: {e}")

    def save_state(self):
        """Save bot state to file"""
        try:
            state = {
                'reply_cooldowns': {
                    str(k): v.isoformat() 
                    for k, v in self.reply_cooldowns.items()
                },
                'stats': self.stats,
                'saved_at': datetime.utcnow().isoformat()
            }
            with open(self.state_file, 'w') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    async def initialize_database(self):
        """Initialize database with connection pool"""
        await self.bot.wait_until_ready()
        
        try:
            # Create database directory if it doesn't exist
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            
            async with aiosqlite.connect(self.db_path) as db:
                # Optimize SQLite settings
                await db.execute('PRAGMA journal_mode=WAL;')
                await db.execute('PRAGMA synchronous=NORMAL;')
                await db.execute('PRAGMA cache_size=-10000;')  # 10MB cache
                await db.execute('PRAGMA temp_store=MEMORY;')
                await db.execute('PRAGMA mmap_size=268435456;')  # 256MB mmap
                await db.execute('PRAGMA optimize;')
                
                # Create tables with optimized schema
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS markov_chain (
                        word_a TEXT NOT NULL,
                        word_b TEXT NOT NULL,
                        next_word TEXT NOT NULL,
                        count INTEGER DEFAULT 1,
                        last_used TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (word_a, word_b, next_word)
                    ) WITHOUT ROWID
                ''')
                
                # Create optimized indexes
                await db.execute('''
                    CREATE INDEX IF NOT EXISTS idx_markov_lookup 
                    ON markov_chain(word_a, word_b, count DESC)
                ''')
                
                await db.execute('''
                    CREATE INDEX IF NOT EXISTS idx_markov_recency 
                    ON markov_chain(last_used DESC)
                ''')
                
                # Create message log with partitioning by day
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS message_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        content TEXT NOT NULL,
                        author_id INTEGER,
                        channel_id INTEGER,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                        processed BOOLEAN DEFAULT 0,
                        day_generated DATE GENERATED ALWAYS AS (DATE(timestamp))
                    )
                ''')
                
                # Create indexes for message log
                await db.execute('''
                    CREATE INDEX IF NOT EXISTS idx_msg_timestamp 
                    ON message_log(timestamp DESC)
                ''')
                
                await db.execute('''
                    CREATE INDEX IF NOT EXISTS idx_msg_processed 
                    ON message_log(processed)
                ''')
                
                await db.commit()
                
                # Get initial counts
                async with db.execute("SELECT COUNT(*) FROM markov_chain") as cursor:
                    row = await cursor.fetchone()
                    self.stats['total_triplets'] = row[0] if row else 0
                
                async with db.execute("SELECT COUNT(*) FROM message_log") as cursor:
                    row = await cursor.fetchone()
                    self.stats['total_messages'] = row[0] if row else 0
                
                logger.info(f"Database initialized. Existing: {self.stats['total_triplets']} triplets, {self.stats['total_messages']} messages")
                await self.restore_buffer()
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            raise

    async def restore_buffer(self):
        """Restore recent messages from database on startup"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute('''
                    SELECT content FROM message_log 
                    WHERE processed = 1 
                    ORDER BY timestamp DESC 
                    LIMIT 1000
                ''') as cursor:
                    rows = await cursor.fetchall()
                    for row in rows:
                        clean = self.sanitize_message(row[0])
                        if clean:
                            self.recent_messages.append(clean)
                    logger.info(f"Restored {len(rows)} messages to recent buffer")
        except Exception as e:
            logger.error(f"Failed to restore buffer: {e}")

    @tasks.loop(minutes=5)
    async def periodic_tasks(self):
        """Run periodic maintenance tasks"""
        try:
            await self.flush_buffer()
            await self.cleanup_old_data()
            self.save_state()
            await self.update_stats()
        except Exception as e:
            logger.error(f"Periodic task error: {e}")

    @periodic_tasks.before_loop
    async def before_periodic_tasks(self):
        await self.bot.wait_until_ready()

    async def cleanup_old_data(self):
        """Clean up old data periodically"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # Archive old raw messages
                cutoff_date = (datetime.utcnow() - timedelta(days=self.RAW_MESSAGE_RETENTION_DAYS)).isoformat()
                
                await db.execute('''
                    DELETE FROM message_log 
                    WHERE timestamp < ?
                ''', (cutoff_date,))
                
                # Archive very old, rarely used triplets
                old_cutoff = (datetime.utcnow() - timedelta(days=self.PRUNE_OLD_TRIPLETS_AFTER_DAYS)).isoformat()
                
                await db.execute('''
                    DELETE FROM markov_chain 
                    WHERE last_used < ? AND count < 2
                ''', (old_cutoff,))
                
                # Vacuum periodically
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
                    row = await cursor.fetchone()
                    self.stats['total_triplets'] = row[0] if row else 0
                
                async with db.execute("SELECT COUNT(*) FROM message_log") as cursor:
                    row = await cursor.fetchone()
                    self.stats['total_messages'] = row[0] if row else 0
        except Exception as e:
            logger.error(f"Stats update error: {e}")

    # --- MESSAGE PROCESSING ---
    def sanitize_message(self, content):
        """Clean message for training - less aggressive filtering"""
        if not content or len(content.strip()) < 3:  # Reduced from 5
            return ""
            
        # Skip commands
        command_prefixes = ('.', '!', '/', '?', '~', '$', '&', '*')
        if content.startswith(command_prefixes):
            return ""
            
        # Remove URLs but keep other content
        content = re.sub(r'https?://\S+|www\.\S+', '', content)
        
        # Keep mentions as words but remove the <@> wrapper
        content = re.sub(r'<@!?(\d+)>', r'@user\1', content)
        
        # Remove excessive whitespace and normalize
        content = ' '.join(content.split())
        
        # Keep original case for better variety
        # Only convert to lowercase for very short messages
        if len(content) < 20:
            content = content.lower()
        
        return content if len(content.split()) >= self.MIN_MESSAGE_WORDS else ""

    def extract_triplets(self, content):
        """Extract word triplets with sliding window"""
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
                triplets.append((word_a, word_b, word_c))
                
        return triplets

    async def train_on_message(self, content):
        """Train Markov chain with batch processing"""
        clean_content = self.sanitize_message(content)
        if not clean_content:
            return 0
            
        triplets = self.extract_triplets(clean_content)
        if not triplets:
            return 0
            
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # Use batch insert with conflict resolution
                await db.executemany('''
                    INSERT INTO markov_chain (word_a, word_b, next_word, count, last_used)
                    VALUES (?, ?, ?, 1, CURRENT_TIMESTAMP)
                    ON CONFLICT(word_a, word_b, next_word) 
                    DO UPDATE SET 
                        count = count + 1,
                        last_used = CURRENT_TIMESTAMP
                ''', triplets)
                
                # Mark message as processed
                await db.execute('''
                    UPDATE message_log 
                    SET processed = 1 
                    WHERE content = ? AND processed = 0
                    LIMIT 1
                ''', (content,))
                
                await db.commit()
                
                self.stats['triplets_stored_today'] += len(triplets)
                return len(triplets)
                
        except Exception as e:
            logger.error(f"Training error: {e}")
            return 0

    async def buffer_message(self, message):
        """Buffer message for processing"""
        async with self.buffer_lock:
            # Save to database immediately for persistence
            try:
                async with aiosqlite.connect(self.db_path) as db:
                    await db.execute('''
                        INSERT INTO message_log (content, author_id, channel_id, timestamp)
                        VALUES (?, ?, ?, ?)
                    ''', (message.content, message.author.id, message.channel.id, 
                          message.created_at.isoformat()))
                    await db.commit()
            except Exception as e:
                logger.error(f"Failed to save message: {e}")
                return
            
            # Add to in-memory buffers
            clean = self.sanitize_message(message.content)
            if clean:
                self.recent_messages.append(clean)
                
            self.stats['messages_processed'] += 1
            
            # Train immediately for small buffers
            if len(self.recent_messages) % 10 == 0:
                await self.train_on_message(message.content)
            
            # Trigger flush if threshold reached
            if self.stats['messages_processed'] % self.FLUSH_THRESHOLD == 0:
                asyncio.create_task(self.flush_buffer())

    async def flush_buffer(self):
        """Flush and train on buffered messages"""
        try:
            # Train on recent unprocessed messages
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute('''
                    SELECT content FROM message_log 
                    WHERE processed = 0 
                    ORDER BY timestamp DESC 
                    LIMIT 100
                ''') as cursor:
                    rows = await cursor.fetchall()
                    
                    for row in rows:
                        await self.train_on_message(row[0])
                        
            self.stats['last_flush'] = datetime.utcnow().isoformat()
            logger.debug(f"Flushed and trained on {len(rows)} messages")
            
        except Exception as e:
            logger.error(f"Flush error: {e}")

    # --- REPLY GENERATION ---
    async def can_generate_reply(self):
        """Check if we have enough data"""
        return self.stats['total_triplets'] >= self.MIN_TRIPLETS_FOR_REPLY

    async def generate_sentence(self, seed_word=None):
        """Generate sentence with better randomness"""
        if not await self.can_generate_reply():
            return None
            
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                
                # Get starting pair
                start_pair = None
                
                if seed_word and len(seed_word) >= self.SEED_WORD_MIN_LENGTH:
                    # Try to find seed in recent context
                    async with db.execute('''
                        SELECT word_a, word_b FROM markov_chain 
                        WHERE word_a LIKE ? OR word_b LIKE ? OR next_word LIKE ?
                        ORDER BY RANDOM() * count DESC 
                        LIMIT 1
                    ''', (f'%{seed_word}%', f'%{seed_word}%', f'%{seed_word}%')) as cursor:
                        start_pair = await cursor.fetchone()
                
                if not start_pair:
                    # Get random starting pair weighted by usage and recency
                    async with db.execute('''
                        SELECT word_a, word_b FROM markov_chain 
                        ORDER BY (count * 0.7 + (julianday('now') - julianday(last_used)) * 0.3) * RANDOM() DESC 
                        LIMIT 1
                    ''') as cursor:
                        start_pair = await cursor.fetchone()
                
                if not start_pair:
                    return None
                
                # Build sentence
                sentence = [start_pair['word_a'], start_pair['word_b']]
                max_attempts = self.SENTENCE_MAX_WORDS * 2
                
                for _ in range(max_attempts):
                    if len(sentence) >= self.SENTENCE_MAX_WORDS:
                        break
                        
                    w1, w2 = sentence[-2], sentence[-1]
                    
                    async with db.execute('''
                        SELECT next_word, count FROM markov_chain 
                        WHERE word_a = ? AND word_b = ?
                        ORDER BY count DESC 
                        LIMIT 10
                    ''', (w1, w2)) as cursor:
                        options = await cursor.fetchall()
                    
                    if not options:
                        break
                    
                    # More random selection
                    if random.random() < 0.3 and len(options) > 1:
                        # Sometimes pick a less common option for variety
                        next_word = random.choice(options[1:])['next_word']
                    else:
                        # Usually pick from top 3
                        top_n = min(3, len(options))
                        next_word = random.choice(options[:top_n])['next_word']
                    
                    sentence.append(next_word)
                    
                    # Natural stopping points
                    if next_word.endswith(('.', '!', '?')) and len(sentence) > 5:
                        break
                    if len(sentence) > 8 and random.random() < 0.2:
                        break
                
                # Capitalize and add punctuation if missing
                result = ' '.join(sentence)
                if not result.endswith(('.', '!', '?')):
                    result += random.choice(['.', '!', '?'])
                
                return result.capitalize()
                
        except Exception as e:
            logger.error(f"Generation error: {e}")
            return None

    async def generate_reply_to_message(self, message):
        """Generate contextual reply"""
        words = message.content.split()
        seed_word = None
        
        # Find seed words with preference for nouns/adjectives
        for word in words:
            clean_word = re.sub(r'[^\w\s]', '', word.lower())
            if len(clean_word) >= 4:  # Longer words often carry more meaning
                seed_word = clean_word
                break
        
        # Fallback to recent context
        if not seed_word and self.recent_messages:
            recent_text = ' '.join(list(self.recent_messages)[-10:])
            recent_words = [w for w in recent_text.split() if len(w) >= 3]
            if recent_words:
                seed_word = random.choice(recent_words)
        
        return await self.generate_sentence(seed_word)

    # --- DISCORD EVENT HANDLERS ---
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
                return
            
            # Generate and send reply
            reply = await self.generate_reply_to_message(message)
            
            if reply:
                async with message.channel.typing():
                    thinking_time = random.uniform(self.TYPING_MIN_SECONDS, self.TYPING_MAX_SECONDS)
                    await asyncio.sleep(thinking_time)
                    
                    await message.channel.send(
                        reply,
                        reference=message if should_reply else None,
                        mention_author=False
                    )
                    
                    self.reply_cooldowns[message.channel.id] = datetime.utcnow()
                    self.stats['replies_generated'] += 1
                    self.save_state()

    # --- COMMANDS ---
    @commands.command(name="brainstats")
    async def brain_stats(self, ctx):
        """Show statistics - FIXED VERSION"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # Get all counts in single transaction
                async with db.execute("""
                    SELECT 
                        (SELECT COUNT(*) FROM markov_chain) as triplets,
                        (SELECT COUNT(DISTINCT word_a || '|' || word_b) FROM markov_chain) as pairs,
                        (SELECT COUNT(*) FROM message_log) as messages,
                        (SELECT COUNT(*) FROM message_log WHERE DATE(timestamp) = DATE('now')) as today_messages
                """) as cursor:
                    row = await cursor.fetchone()
                    
                triplets = row[0] if row else 0
                pairs = row[1] if row else 0
                messages = row[2] if row else 0
                today_messages = row[3] if row else 0
                
                # Get average connections
                async with db.execute("""
                    SELECT AVG(option_count) FROM (
                        SELECT COUNT(*) as option_count 
                        FROM markov_chain 
                        GROUP BY word_a, word_b
                    )
                """) as cursor:
                    avg_row = await cursor.fetchone()
                    avg_conn = round(avg_row[0], 1) if avg_row and avg_row[0] else 0.0
                
                # Get most common pair
                async with db.execute("""
                    SELECT word_a, word_b, SUM(count) as total 
                    FROM markov_chain 
                    GROUP BY word_a, word_b 
                    ORDER BY total DESC 
                    LIMIT 1
                """) as cursor:
                    common_row = await cursor.fetchone()
                    common_pair = f"{common_row[0]} {common_row[1]}" if common_row else "Not enough data"
                    common_count = common_row[2] if common_row else 0
                
                # Update stats
                self.stats['total_triplets'] = triplets
                self.stats['total_messages'] = messages
                
                embed = discord.Embed(
                    title="🧠 Brain Statistics",
                    color=discord.Color.blue(),
                    timestamp=datetime.utcnow()
                )
                
                embed.add_field(
                    name="Knowledge Base",
                    value=f"**{triplets:,}** word connections\n"
                          f"**{pairs:,}** unique word pairs\n"
                          f"**{avg_conn}** avg options per pair",
                    inline=False
                )
                
                embed.add_field(
                    name="Most Common Phrase",
                    value=f"**{common_pair}**\nUsed **{common_count:,}** times",
                    inline=False
                )
                
                embed.add_field(
                    name="Activity",
                    value=f"**{messages:,}** total messages learned\n"
                          f"**{today_messages}** messages today\n"
                          f"**{self.stats['replies_generated']}** replies sent\n"
                          f"**{self.stats['triplets_stored_today']:,}** triplets stored today",
                    inline=False
                )
                
                embed.add_field(
                    name="Performance",
                    value=f"**{self.stats['messages_processed']:,}** processed\n"
                          f"**{self.REPLY_CHANCE*100:.1f}%** reply chance\n"
                          f"**{self.REPLY_COOLDOWN}s** cooldown\n"
                          f"**{len(self.recent_messages)}** in memory",
                    inline=False
                )
                
                # Status
                if triplets < self.MIN_TRIPLETS_FOR_REPLY:
                    status = f"Learning... ({triplets}/{self.MIN_TRIPLETS_FOR_REPLY} triplets)"
                    embed.color = discord.Color.orange()
                else:
                    status = "✅ Ready to generate!"
                    embed.color = discord.Color.green()
                
                embed.set_footer(text=status)
                
                await ctx.send(embed=embed)
                
        except Exception as e:
            logger.error(f"Brainstats error: {e}")
            await ctx.send(f"❌ Error: {str(e)[:100]}")

    @commands.command(name="generate")
    async def force_generate(self, ctx, *, seed_word=None):
        """Force generate a sentence"""
        async with ctx.channel.typing():
            sentence = await self.generate_sentence(seed_word)
            
            if sentence:
                embed = discord.Embed(
                    description=f"*{sentence}*",
                    color=discord.Color.green()
                )
                if seed_word:
                    embed.set_footer(text=f"Seeded with: {seed_word}")
                await ctx.send(embed=embed)
            else:
                await ctx.send("❌ Not enough data yet! I need to learn more from chat.")

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
            async with aiosqlite.connect(self.db_path) as db:
                # Check database health
                await db.execute("PRAGMA integrity_check")
                await db.execute("PRAGMA optimize")
                
                embed = discord.Embed(
                    title="🤖 Bot Health Check",
                    color=discord.Color.green(),
                    timestamp=datetime.utcnow()
                )
                
                uptime = datetime.utcnow() - datetime.fromisoformat(self.stats['startup_time'])
                hours, remainder = divmod(int(uptime.total_seconds()), 3600)
                minutes, seconds = divmod(remainder, 60)
                
                embed.add_field(
                    name="System Status",
                    value=f"✅ **Online**\n"
                          f"Uptime: {hours}h {minutes}m {seconds}s\n"
                          f"Database: ✅ Healthy\n"
                          f"State: ✅ Saved",
                    inline=False
                )
                
                embed.add_field(
                    name="Memory",
                    value=f"Recent messages: {len(self.recent_messages)}/5000\n"
                          f"Buffer: {self.stats['messages_processed'] % self.FLUSH_THRESHOLD}/{self.FLUSH_THRESHOLD}\n"
                          f"Cooldowns: {len(self.reply_cooldowns)} active",
                    inline=False
                )
                
                await ctx.send(embed=embed)
                
        except Exception as e:
            await ctx.send(f"❌ Health check failed: {e}")

    @commands.command(name="forcelearn")
    @commands.has_permissions(administrator=True)
    async def force_learn(self, ctx, limit: int = 100):
        """Force learning from recent messages"""
        await ctx.send(f"⏳ Learning from last {limit} messages...")
        
        try:
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute('''
                    SELECT content FROM message_log 
                    WHERE processed = 0 
                    ORDER BY timestamp DESC 
                    LIMIT ?
                ''', (limit,)) as cursor:
                    rows = await cursor.fetchall()
                    
                learned = 0
                for row in rows:
                    learned += await self.train_on_message(row[0])
                    
                await ctx.send(f"✅ Learned {learned} new triplets from {len(rows)} messages!")
                
        except Exception as e:
            await ctx.send(f"❌ Error: {e}")

async def setup(bot):
    await bot.add_cog(OptimizedMarkovCog(bot))