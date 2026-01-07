import discord
from discord.ext import commands
import aiosqlite
import random
import re
import asyncio
import logging
from collections import deque
from datetime import datetime, timedelta

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("HighTrafficMarkovBot")

class SingleServerMarkovCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_path = "./server_brain.db"
        
        self.MAX_RAW_MESSAGES = 2_000_000      # Keep ~4 months of raw messages
        self.MAX_TRIPLETS = 5_000_000          # Max unique triplets (~2-3 years)
        self.RAW_MESSAGE_RETENTION_DAYS = 120  # Keep raw messages for 120 days
        self.PRUNE_OLD_TRIPLETS_AFTER_DAYS = 365  # Archive triplets older than 1 year
        
        # Add cleanup task
        self.bot.loop.create_task(self.periodic_cleanup())
        
        # --- OPTIMIZED PARAMETERS FOR 17K DAILY MESSAGES ---
        # Based on: 17k/day = ~708/hour = ~12/minute
        self.MIN_TRIPLETS_FOR_REPLY = 1000      # Need at least 1000 learned triplets
        self.REPLY_CHANCE = 0.015                # 1.5% chance to reply randomly
        self.REPLY_COOLDOWN = 10                 # 10 seconds between replies per channel
        self.TYPING_MIN_SECONDS = 0.3            # Minimum typing delay
        self.TYPING_MAX_SECONDS = 0.8            # Maximum typing delay
        self.SENTENCE_MAX_WORDS = 30             # Maximum words per generated sentence
        self.SEED_WORD_MIN_LENGTH = 3            # Minimum seed word length
        self.MIN_MESSAGE_WORDS = 3               # Minimum words to train on
        
        # Memory buffer for recent messages (helps with seeding)
        self.recent_messages = deque(maxlen=1000)  # Keep last 1000 messages
        
        # Reply cooldown tracking
        self.reply_cooldowns = {}
        self.message_buffer = []
        self.buffer_lock = asyncio.Lock()
        self.FLUSH_THRESHOLD = 100  # Flush every 100 messages
        
        # Performance tracking
        self.stats = {
            'messages_processed': 0,
            'triplets_stored': 0,
            'replies_generated': 0,
            'last_flush': datetime.utcnow()
        }
        
        # Start background tasks
        self.bot.loop.create_task(self.initialize_database())
        self.bot.loop.create_task(self.buffer_flusher())


    async def periodic_cleanup(self):
        """Run database cleanup every 24 hours"""
        while True:
            await asyncio.sleep(24 * 3600)  # 24 hours
            
            try:
                await self.cleanup_old_data()
                logger.info("Performed daily database cleanup")
            except Exception as e:
                logger.error(f"Cleanup failed: {e}")

    async def cleanup_old_data(self):
        """Remove old data to prevent unbounded growth"""
        async with aiosqlite.connect(self.db_path) as db:
            # 1. Remove old raw messages (older than retention period)
            await db.execute(f"""
                DELETE FROM message_log 
                WHERE timestamp < datetime('now', '-{self.RAW_MESSAGE_RETENTION_DAYS} days')
            """)
            
            # 2. Check if we have too many raw messages (safety limit)
            async with db.execute("SELECT COUNT(*) FROM message_log") as cursor:
                count = (await cursor.fetchone())[0]
                
            if count > self.MAX_RAW_MESSAGES:
                excess = count - self.MAX_RAW_MESSAGES
                await db.execute(f"""
                    DELETE FROM message_log 
                    WHERE id IN (
                        SELECT id FROM message_log 
                        ORDER BY timestamp ASC 
                        LIMIT {excess}
                    )
                """)
            
            # 3. Prune very old, rarely used triplets
            await db.execute(f"""
                DELETE FROM markov_chain 
                WHERE last_used < datetime('now', '-{self.PRUNE_OLD_TRIPLETS_AFTER_DAYS} days')
                AND count < 3  -- Remove triplets used less than 3 times
            """)
            
            # 4. Check triplet count limit
            async with db.execute("SELECT COUNT(*) FROM markov_chain") as cursor:
                triplet_count = (await cursor.fetchone())[0]
                
            if triplet_count > self.MAX_TRIPLETS:
                # Remove least used triplets
                excess_triplets = triplet_count - self.MAX_TRIPLETS
                await db.execute(f"""
                    DELETE FROM markov_chain 
                    WHERE (word_a, word_b, next_word) IN (
                        SELECT word_a, word_b, next_word 
                        FROM markov_chain 
                        ORDER BY count ASC, last_used ASC 
                        LIMIT {excess_triplets}
                    )
                """)
            
            # 5. Vacuum to reclaim space
            await db.execute("VACUUM")
            await db.commit()


    # --- DATABASE INITIALIZATION ---
    async def initialize_database(self):
        """Initialize SQLite database optimized for single-server high traffic"""
        await self.bot.wait_until_ready()
        
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # Enable WAL mode for concurrent reads/writes
                await db.execute('PRAGMA journal_mode=WAL;')
                await db.execute('PRAGMA synchronous=NORMAL;')
                await db.execute('PRAGMA cache_size=10000;')  # 10MB cache
                await db.execute('PRAGMA temp_store=MEMORY;')
                
                # Create Markov chain table (word triplets)
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS markov_chain (
                        word_a TEXT NOT NULL,
                        word_b TEXT NOT NULL,
                        next_word TEXT NOT NULL,
                        count INTEGER DEFAULT 1,
                        last_used TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (word_a, word_b, next_word)
                    )
                ''')
                
                # Create indexes for fast lookups
                await db.execute('''
                    CREATE INDEX IF NOT EXISTS idx_markov_start 
                    ON markov_chain(word_a, word_b)
                ''')
                
                # Create message log table (optional, for stats)
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS message_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        content TEXT NOT NULL,
                        author_id INTEGER,
                        channel_id INTEGER,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                await db.commit()
                logger.info("Database initialized with WAL mode and indexes")
                
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")

    # --- MESSAGE PROCESSING ---
    def sanitize_message(self, content):
        """Clean message for training"""
        if not content or len(content.strip()) < 5:
            return ""
            
        # Remove bot commands and common prefixes
        if content.startswith(('!', '/', '.', '?', '~', '$', '#')):
            return ""
            
        # Remove URLs
        content = re.sub(r'https?://\S+|www\.\S+', '', content)
        
        # Remove mentions and emojis
        content = re.sub(r'<[@#][^>]+>|:[^:\s]+:', '', content)
        
        # Remove excessive whitespace and normalize
        content = ' '.join(content.split())
        
        # Convert to lowercase for better matching (optional)
        content = content.lower()
        
        return content if len(content.split()) >= self.MIN_MESSAGE_WORDS else ""

    def extract_triplets(self, content):
        """Extract word triplets from message content"""
        words = content.split()
        triplets = []
        
        if len(words) < 3:
            return triplets
            
        for i in range(len(words) - 2):
            # Ensure all words are non-empty
            if words[i] and words[i+1] and words[i+2]:
                triplets.append((words[i], words[i+1], words[i+2]))
                
        return triplets

    async def train_on_message(self, content):
        """Train Markov chain on a single message"""
        clean_content = self.sanitize_message(content)
        if not clean_content:
            return 0
            
        triplets = self.extract_triplets(clean_content)
        if not triplets:
            return 0
            
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # Batch insert/update triplets
                await db.executemany('''
                    INSERT INTO markov_chain (word_a, word_b, next_word, count, last_used)
                    VALUES (?, ?, ?, 1, CURRENT_TIMESTAMP)
                    ON CONFLICT(word_a, word_b, next_word) 
                    DO UPDATE SET 
                        count = count + 1,
                        last_used = CURRENT_TIMESTAMP
                ''', triplets)
                await db.commit()
                
                self.stats['triplets_stored'] += len(triplets)
                return len(triplets)
                
        except Exception as e:
            logger.error(f"Failed to train on message: {e}")
            return 0

    async def buffer_message(self, message):
        """Buffer message for async processing"""
        async with self.buffer_lock:
            self.message_buffer.append({
                'content': message.content,
                'author_id': message.author.id,
                'channel_id': message.channel.id,
                'timestamp': message.created_at.isoformat()
            })
            
            # Store in recent messages for seeding
            clean = self.sanitize_message(message.content)
            if clean:
                self.recent_messages.append(clean)
                
            self.stats['messages_processed'] += 1
            
            # Trigger flush if threshold reached
            if len(self.message_buffer) >= self.FLUSH_THRESHOLD:
                asyncio.create_task(self.flush_buffer())

    async def buffer_flusher(self):
        """Periodic buffer flusher (every 30 seconds)"""
        while True:
            await asyncio.sleep(30)
            await self.flush_buffer()

    async def flush_buffer(self):
        """Flush buffered messages to database"""
        async with self.buffer_lock:
            if not self.message_buffer:
                return
                
            messages_to_save = self.message_buffer.copy()
            self.message_buffer.clear()
            
        if not messages_to_save:
            return
            
        try:
            # Save to message log
            async with aiosqlite.connect(self.db_path) as db:
                await db.executemany('''
                    INSERT INTO message_log (content, author_id, channel_id, timestamp)
                    VALUES (?, ?, ?, ?)
                ''', [
                    (m['content'], m['author_id'], m['channel_id'], m['timestamp'])
                    for m in messages_to_save
                ])
                
                # Train on all buffered messages
                for msg in messages_to_save:
                    await self.train_on_message(msg['content'])
                    
                await db.commit()
                
            self.stats['last_flush'] = datetime.utcnow()
            logger.debug(f"Flushed {len(messages_to_save)} messages to database")
            
        except Exception as e:
            logger.error(f"Failed to flush buffer: {e}")

    # --- REPLY GENERATION ---
    async def can_generate_reply(self):
        """Check if we have enough data to generate replies"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute("SELECT COUNT(*) FROM markov_chain") as cursor:
                    result = await cursor.fetchone()
                    count = result[0] if result else 0
                    
                return count >= self.MIN_TRIPLETS_FOR_REPLY
        except Exception as e:
            logger.error(f"Error checking data: {e}")
            return False

    async def generate_sentence(self, seed_word=None):
        """Generate a sentence using Markov chain"""
        if not await self.can_generate_reply():
            return None
            
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                
                # Step 1: Get starting word pair
                start_pair = None
                
                if seed_word and len(seed_word) >= self.SEED_WORD_MIN_LENGTH:
                    # Try to find a pair containing the seed word
                    async with db.execute('''
                        SELECT word_a, word_b FROM markov_chain 
                        WHERE word_a LIKE ? OR word_b LIKE ?
                        ORDER BY count DESC, RANDOM()
                        LIMIT 1
                    ''', (f'%{seed_word}%', f'%{seed_word}%')) as cursor:
                        start_pair = await cursor.fetchone()
                
                if not start_pair:
                    # Get a random starting pair weighted by frequency
                    async with db.execute('''
                        SELECT word_a, word_b FROM markov_chain 
                        ORDER BY count * RANDOM() DESC 
                        LIMIT 1
                    ''') as cursor:
                        start_pair = await cursor.fetchone()
                
                if not start_pair:
                    return None
                
                # Step 2: Build sentence
                sentence = [start_pair['word_a'], start_pair['word_b']]
                
                for _ in range(self.SENTENCE_MAX_WORDS - 2):
                    w1, w2 = sentence[-2], sentence[-1]
                    
                    # Get possible next words with their frequencies
                    async with db.execute('''
                        SELECT next_word, count FROM markov_chain 
                        WHERE word_a = ? AND word_b = ?
                        ORDER BY count DESC
                    ''', (w1, w2)) as cursor:
                        options = await cursor.fetchall()
                    
                    if not options:
                        break
                    
                    # Weighted random choice based on frequency
                    words = [row['next_word'] for row in options]
                    weights = [row['count'] for row in options]
                    
                    # Slightly favor more common words but allow randomness
                    next_word = random.choices(words, weights=weights, k=1)[0]
                    sentence.append(next_word)
                    
                    # Natural stopping point (end with punctuation)
                    if next_word.endswith(('.', '!', '?')) and len(sentence) > 5:
                        break
                
                return ' '.join(sentence).capitalize()
                
        except Exception as e:
            logger.error(f"Error generating sentence: {e}")
            return None

    async def generate_reply_to_message(self, message):
        """Generate a reply to a specific message"""
        # Extract potential seed words from message
        words = message.content.split()
        seed_word = None
        
        # Find a good seed word (longer words are better)
        for word in words:
            if len(word) >= self.SEED_WORD_MIN_LENGTH and word.isalpha():
                seed_word = word.lower()
                break
        
        # Also check recent messages for context
        if not seed_word and self.recent_messages:
            recent_text = ' '.join(self.recent_messages)
            recent_words = recent_text.split()
            if recent_words:
                seed_word = random.choice(recent_words)
        
        return await self.generate_sentence(seed_word)

    # --- DISCORD EVENT HANDLERS ---
    @commands.Cog.listener()
    async def on_message(self, message):
        # Ignore bots and DMs
        if message.author.bot:
            return
        if not message.guild:
            return
            
        # Process message asynchronously
        asyncio.create_task(self.buffer_message(message))
        
        # Check if we should reply
        is_mentioned = self.bot.user in message.mentions
        is_reply_to_me = False
        
        if message.reference:
            ref_msg = message.reference.cached_message or message.reference.resolved
            if ref_msg and ref_msg.author.id == self.bot.user.id:
                is_reply_to_me = True
        
        should_reply = is_mentioned or is_reply_to_me
        chance = 1.0 if should_reply else self.REPLY_CHANCE
        
        # Apply cooldown for non-mention replies
        if not should_reply:
            now = datetime.utcnow()
            last_reply = self.reply_cooldowns.get(message.channel.id)
            if last_reply and (now - last_reply).seconds < self.REPLY_COOLDOWN:
                return
        
        # Roll the dice
        if random.random() < chance or should_reply:
            # Check if we have enough data
            if not await self.can_generate_reply():
                if should_reply:  # Only respond to mentions if no data
                    async with message.channel.typing():
                        await asyncio.sleep(0.5)
                        await message.channel.send(
                            "🧠 I'm still learning from chat! Give me a bit more time to read messages.",
                            reference=message if should_reply else None,
                            mention_author=False
                        )
                return
            
            # Generate reply
            reply = await self.generate_reply_to_message(message)
            
            if not reply:
                return
            
            # Send with typing indicator
            async with message.channel.typing():
                thinking_time = random.uniform(
                    self.TYPING_MIN_SECONDS, 
                    self.TYPING_MAX_SECONDS
                )
                await asyncio.sleep(thinking_time)
                
                await message.channel.send(
                    reply,
                    reference=message if should_reply else None,
                    mention_author=False
                )
                
                self.reply_cooldowns[message.channel.id] = datetime.utcnow()
                self.stats['replies_generated'] += 1

    # --- COMMANDS ---
    @commands.command(name="brainstats")
    async def brain_stats(self, ctx):
        """Show bot statistics"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # Get triplet count - FIXED
                async with db.execute("SELECT COUNT(*) FROM markov_chain") as cursor:
                    row = await cursor.fetchone()
                    triplets = row[0] if row else 0
                
                # Get unique word pairs - FIXED
                async with db.execute("SELECT COUNT(*) FROM (SELECT DISTINCT word_a, word_b FROM markov_chain)") as cursor:
                    row = await cursor.fetchone()
                    word_pairs = row[0] if row else 0
                
                # Get message count - FIXED
                async with db.execute("SELECT COUNT(*) FROM message_log") as cursor:
                    row = await cursor.fetchone()
                    messages = row[0] if row else 0
                
                # Get average chain length - FIXED
                async with db.execute("""
                    SELECT AVG(cnt) as avg_connections 
                    FROM (
                        SELECT COUNT(*) as cnt 
                        FROM markov_chain 
                        GROUP BY word_a, word_b
                    )
                """) as cursor:
                    row = await cursor.fetchone()
                    avg_conn = float(row[0]) if row and row[0] else 0.0
                
                # Get most common word pair
                async with db.execute("""
                    SELECT word_a, word_b, SUM(count) as total 
                    FROM markov_chain 
                    GROUP BY word_a, word_b 
                    ORDER BY total DESC 
                    LIMIT 1
                """) as cursor:
                    row = await cursor.fetchone()
                    common_pair = f"{row[0]} {row[1]}" if row else "Not enough data"
                    common_count = row[2] if row else 0
                
                embed = discord.Embed(
                    title="🧠 Brain Statistics",
                    color=discord.Color.blue(),
                    timestamp=datetime.utcnow()
                )
                
                embed.add_field(
                    name="Knowledge Base",
                    value=f"**{triplets:,}** word connections\n"
                          f"**{word_pairs:,}** unique word pairs\n"
                          f"**{avg_conn:.1f}** avg options per pair",
                    inline=False
                )
                
                embed.add_field(
                    name="Most Common Phrase",
                    value=f"**{common_pair}**\nUsed **{common_count:,}** times",
                    inline=False
                )
                
                embed.add_field(
                    name="Activity",
                    value=f"**{messages:,}** messages learned\n"
                          f"**{self.stats['replies_generated']}** replies sent\n"
                          f"**{len(self.message_buffer)}** in buffer\n"
                          f"**{self.stats['triplets_stored']:,}** triplets stored today",
                    inline=False
                )
                
                embed.add_field(
                    name="Performance",
                    value=f"**{self.stats['messages_processed']:,}** processed\n"
                          f"**{self.REPLY_CHANCE*100:.1f}%** reply chance\n"
                          f"**{self.REPLY_COOLDOWN}s** cooldown",
                    inline=False
                )
                
                if triplets < self.MIN_TRIPLETS_FOR_REPLY:
                    embed.add_field(
                        name="⚠️ Status",
                        value=f"Learning... ({triplets}/{self.MIN_TRIPLETS_FOR_REPLY} triplets needed)",
                        inline=False
                    )
                    embed.color = discord.Color.orange()
                else:
                    embed.add_field(
                        name="✅ Status",
                        value="Ready to generate!",
                        inline=False
                    )
                    embed.color = discord.Color.green()
                
                # Reset daily counters at midnight
                now = datetime.utcnow()
                if now.hour == 0 and now.minute == 0:
                    self.stats['triplets_stored'] = 0
                
                await ctx.send(embed=embed)
                
        except Exception as e:
            logger.error(f"Error in brain_stats: {e}")
            await ctx.send(f"❌ Error getting stats: {str(e)}")

    @commands.command(name="generate")
    async def force_generate(self, ctx, *, seed_word=None):
        """Force generate a sentence (optional seed word)"""
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
                await ctx.send("❌ Not enough data yet! Keep chatting so I can learn.")

    @commands.command(name="clearcooldown")
    @commands.has_permissions(manage_messages=True)
    async def clear_cooldown(self, ctx):
        """Clear reply cooldown for this channel (Admin only)"""
        self.reply_cooldowns.pop(ctx.channel.id, None)
        await ctx.send("✅ Cooldown cleared for this channel.")

    @commands.command(name="exportbrain")
    @commands.has_permissions(administrator=True)
    async def export_brain(self, ctx):
        """Export brain data as text file (Admin only)"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute("""
                    SELECT word_a, word_b, next_word, count 
                    FROM markov_chain 
                    ORDER BY count DESC 
                    LIMIT 1000
                """) as cursor:
                    rows = await cursor.fetchall()
                
                if not rows:
                    await ctx.send("No data to export yet.")
                    return
                
                # Create formatted export
                lines = []
                lines.append(f"# Markov Brain Export - {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}")
                lines.append(f"# Total triplets: {len(rows)}")
                lines.append("")
                
                for word_a, word_b, next_word, count in rows:
                    lines.append(f"{word_a} {word_b} -> {next_word} (x{count})")
                
                # Send as file
                content = "\n".join(lines)
                await ctx.send(
                    "Here's a snapshot of my brain:",
                    file=discord.File(
                        bytes(content, 'utf-8'),
                        filename=f"brain_export_{datetime.utcnow().strftime('%Y%m%d')}.txt"
                    )
                )
                
        except Exception as e:
            await ctx.send(f"Error exporting: {str(e)}")
            
    
    
    
    @commands.command(name="genai")
    @commands.has_permissions(manage_messages=True)
    async def genai_help(self, ctx):
        """🤖 Admin-only help command with bot information and usage"""
        
        embed = discord.Embed(
            title="🤖 Markov Bot - Admin Commands",
            description="Advanced commands for server administrators",
            color=discord.Color.purple(),
            timestamp=datetime.utcnow()
        )
        
        embed.add_field(
            name="📊 **Statistics & Info**",
            value="• `.brainstats` - View detailed bot statistics and learning progress\n"
                  "• `.storage` - Check database storage usage and limits\n"
                  "• `.genai` - This help menu (Admin only)",
            inline=False
        )
        
        embed.add_field(
            name="⚙️ **Administration**",
            value="• `.generate [word]` - Force generate a sentence (optional seed word)\n"
                  "• `.clearcooldown` - Clear reply cooldown for this channel (Admin only)\n"
                  "• `.exportbrain` - Export brain data as text file (Admin only)\n"
                  "• `.optimize` - Optimize database performance (Admin only)",
            inline=False
        )
        
        embed.add_field(
            name="🔧 **Bot Configuration**",
            value=f"• **Reply Chance:** {self.REPLY_CHANCE*100:.1f}% random replies\n"
                  f"• **Cooldown:** {self.REPLY_COOLDOWN}s between replies per channel\n"
                  f"• **Learning:** Active - learns from all non-bot messages\n"
                  f"• **Typing Delay:** {self.TYPING_MIN_SECONDS}-{self.TYPING_MAX_SECONDS}s",
            inline=False
        )
        



        
        # Add footer with current status
        try:
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute("SELECT COUNT(*) FROM markov_chain") as cursor:
                    triplets = (await cursor.fetchone())[0]
                
                status = "✅ **Ready to generate!**" if triplets >= self.MIN_TRIPLETS_FOR_REPLY else "⏳ **Learning from chat...**"
                
                embed.set_footer(
                    text=f"{status} | {triplets:,} word patterns learned | Admin: {ctx.author.display_name}",
                    icon_url=ctx.author.avatar.url if ctx.author.avatar else None
                )
        except:
            embed.set_footer(text=f"Admin: {ctx.author.display_name}")
        
        await ctx.send(embed=embed)
    
    @commands.command(name="storage")
    async def storage_info(self, ctx):
        """💾 Show storage usage and limits"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # Get raw message count and size - FIXED
                async with db.execute("""
                    SELECT 
                        COUNT(*) as count,
                        SUM(LENGTH(content)) as total_chars
                    FROM message_log
                """) as cursor:
                    row = await cursor.fetchone()
                    msg_count = row[0] if row else 0
                    total_chars = row[1] if row and row[1] else 0
                
                # Get triplet count - FIXED
                async with db.execute("SELECT COUNT(*) FROM markov_chain") as cursor:
                    row = await cursor.fetchone()
                    triplet_count = row[0] if row else 0
                
                # Estimate sizes (rough)
                raw_msg_size_mb = (total_chars or 0) / 1024 / 1024
                triplet_size_mb = triplet_count * 100 / 1024 / 1024
                
                # Calculate oldest data
                async with db.execute("SELECT MIN(timestamp) as oldest_msg FROM message_log") as cursor:
                    row = await cursor.fetchone()
                    oldest_msg = row[0] if row and row[0] else "No data"
                
                embed = discord.Embed(
                    title="💾 Storage Information",
                    color=discord.Color.purple()
                )
                
                embed.add_field(
                    name="Raw Messages",
                    value=f"**{msg_count:,}** messages\n"
                          f"**{raw_msg_size_mb:.1f} MB** total\n"
                          f"Oldest: {oldest_msg[:10] if oldest_msg != 'No data' else 'N/A'}",
                    inline=False
                )
                
                embed.add_field(
                    name="Markov Chain",
                    value=f"**{triplet_count:,}** unique triplets\n"
                          f"**{triplet_size_mb:.1f} MB** estimated\n"
                          f"Limit: {self.MAX_TRIPLETS:,} triplets",
                    inline=False
                )
                
                embed.add_field(
                    name="System Health",
                    value=f"✅ **Normal**\n"
                          f"Buffer: {len(self.message_buffer)}/{self.FLUSH_THRESHOLD}\n"
                          f"Last flush: {self.stats['last_flush'].strftime('%H:%M')} UTC",
                    inline=False
                )
                
                await ctx.send(embed=embed)
                
        except Exception as e:
            logger.error(f"Error in storage_info: {e}")
            await ctx.send(f"❌ Error getting storage info: {str(e)}")

    @commands.command(name="optimize")
    @commands.has_permissions(administrator=True)
    async def optimize_database(self, ctx):
        """Optimize database performance (Admin only)"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # Vacuum to defragment
                await db.execute("VACUUM")
                
                # Analyze for better query planning
                await db.execute("ANALYZE")
                
                # Reindex
                await db.execute("REINDEX")
                
                await db.commit()
                
            await ctx.send("✅ Database optimized!")
            
        except Exception as e:
            await ctx.send(f"Error optimizing: {str(e)}")

async def setup(bot):
    await bot.add_cog(SingleServerMarkovCog(bot))