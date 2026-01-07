import discord
from discord.ext import commands
import markovify
import os
import random
import re
import json
import asyncio
from collections import deque
import heapq
from datetime import datetime, timedelta
import logging
import aiofiles

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class GenAICog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.data_path = "./messages"
        
        # Improved memory management with multiple caches
        self.models = {}           # Cached markov models
        self.message_cache = {}    # Channel_id -> deque of messages (in-memory)
        self.model_cache_time = {} # Last model generation time
        
        # Enhanced configuration
        self.MAX_MESSAGES = 500000  # More reasonable
        self.MIN_MESSAGES_FOR_MODEL = 50
        self.REPLY_CHANCE = 0.02        # 2% base chance
        self.MODEL_CACHE_TTL = 300      # 5 minutes cache TTL
        self.REPLY_COOLDOWN = 30        # Seconds between replies in same channel
        self.MAX_MESSAGE_LENGTH = 2000  # Discord limit
        self.CONTEXT_WINDOW = 5         # Look at last 5 messages for context
        
        # Active conversation tracking
        self.reply_cooldowns = {}
        self.conversation_context = {}  # Track recent conversation topics
        
        # Advanced reply triggers
        self.KEYWORD_TRIGGERS = {
            'what do you think': 0.8,
            'lol': 0.3,
            'haha': 0.3,
            '?': 0.4,
            '!': 0.2
        }
        
        # Ensure data directory exists
        os.makedirs(self.data_path, exist_ok=True)
        
        # Load existing data on startup
        self.bot.loop.create_task(self.initialize_data())

    async def initialize_data(self):
        """Load existing data from files on startup"""
        await self.bot.wait_until_ready()
        for filename in os.listdir(self.data_path):
            if filename.endswith('.json'):
                channel_id = int(filename[:-5])
                file_path = os.path.join(self.data_path, filename)
                try:
                    async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                        data = json.loads(await f.read())
                        self.message_cache[channel_id] = deque(data['messages'], maxlen=self.MAX_MESSAGES)
                    logger.info(f"Loaded {len(self.message_cache[channel_id])} messages for channel {channel_id}")
                except Exception as e:
                    logger.error(f"Error loading {filename}: {e}")

    def get_file_path(self, channel_id):
        return os.path.join(self.data_path, f"{channel_id}.json")

    def sanitize_message(self, content):
        """Advanced message cleaning"""
        if not content:
            return ""
        
        # Remove command invocations
        if content.startswith(self.bot.command_prefix):
            return ""
            
        # Remove URLs
        content = re.sub(r'https?://\S+', '', content)
        
        # Remove excessive whitespace
        content = re.sub(r'\s+', ' ', content)
        
        # Remove special characters that break markov chains
        content = re.sub(r'[`´]', "'", content)
        
        return content.strip()

    async def save_messages(self, channel_id):
        """Asynchronously save messages to disk"""
        if channel_id not in self.message_cache:
            return
            
        file_path = self.get_file_path(channel_id)
        data = {
            'last_updated': datetime.utcnow().isoformat(),
            'message_count': len(self.message_cache[channel_id]),
            'messages': list(self.message_cache[channel_id])
        }
        
        try:
            async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(data, ensure_ascii=False, indent=2))
        except Exception as e:
            logger.error(f"Error saving messages for {channel_id}: {e}")

    async def add_message(self, channel_id, message):
        """Add message to memory with auto-saving"""
        if channel_id not in self.message_cache:
            self.message_cache[channel_id] = deque(maxlen=self.MAX_MESSAGES)
        
        self.message_cache[channel_id].append(message)
        
        # Auto-save every 100 messages
        if len(self.message_cache[channel_id]) % 100 == 0:
            self.bot.loop.create_task(self.save_messages(channel_id))

    async def get_model(self, channel_id, force_refresh=False):
        """Get or create markov model with intelligent caching"""
        now = datetime.utcnow()
        
        # Check cache first
        if (not force_refresh and 
            channel_id in self.models and 
            channel_id in self.model_cache_time and
            (now - self.model_cache_time[channel_id]).seconds < self.MODEL_CACHE_TTL):
            return self.models[channel_id]
        
        # Check if we have enough data
        if (channel_id not in self.message_cache or 
            len(self.message_cache[channel_id]) < self.MIN_MESSAGES_FOR_MODEL):
            return None
        
        try:
            # Use aiofiles for async file-like operations
            messages = list(self.message_cache[channel_id])
            
            # Add recent messages multiple times for better weighting
            recent_messages = messages[-1000:] * 3
            all_messages = messages + recent_messages
            
            # Create model with adaptive state size
            message_count = len(all_messages)
            state_size = 2 if message_count < 1000 else (3 if message_count < 10000 else 2)
            
            text = "\n".join(all_messages)
            model = markovify.NewlineText(text, state_size=state_size)
            
            # Cache the model
            self.models[channel_id] = model
            self.model_cache_time[channel_id] = now
            
            # Prune old models if cache gets too big
            if len(self.models) > 50:
                oldest = min(self.model_cache_time, key=self.model_cache_time.get)
                del self.models[oldest]
                del self.model_cache_time[oldest]
            
            return model
            
        except Exception as e:
            logger.error(f"Error creating model for {channel_id}: {e}")
            return None

    def calculate_reply_probability(self, message, is_mentioned):
        """Advanced probability calculation based on context"""
        if is_mentioned:
            return 1.0  # Always reply to mentions
            
        base_chance = self.REPLY_CHANCE
        
        # Check for keyword triggers
        content_lower = message.content.lower()
        for keyword, boost in self.KEYWORD_TRIGGERS.items():
            if keyword in content_lower:
                base_chance += boost
        
        # Increase chance if conversation is active
        channel_id = message.channel.id
        if channel_id in self.conversation_context:
            base_chance *= 1.5
        
        # Decrease chance if in cooldown
        if channel_id in self.reply_cooldowns:
            elapsed = datetime.utcnow() - self.reply_cooldowns[channel_id]
            if elapsed.seconds < self.REPLY_COOLDOWN:
                base_chance *= 0.1
        
        # Adjust based on message length (reply more to substantial messages)
        if len(message.content) > 20:
            base_chance *= 1.2
        
        # Ensure probability is within bounds
        return min(base_chance, 0.8)  # Cap at 80%

    async def generate_response(self, model, context_message, channel_id):
        """Generate contextual response with multiple strategies"""
        response = None
        
        # Strategy 1: Try to continue from user's message
        if context_message:
            user_words = [w for w in context_message.split() if len(w) > 3]
            if user_words:
                random.shuffle(user_words)
                for word in user_words[:3]:  # Try 3 different words
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
        
        # Strategy 2: Try to generate based on conversation context
        if not response and channel_id in self.conversation_context:
            context_words = self.conversation_context[channel_id]
            if context_words:
                for word in random.sample(context_words, min(3, len(context_words))):
                    try:
                        response = model.make_sentence_with_start(
                            word,
                            strict=False,
                            tries=30
                        )
                        if response:
                            break
                    except:
                        continue
        
        # Strategy 3: Generate random sentence with varied attempts
        if not response:
            for _ in range(3):
                response = model.make_sentence(
                    tries=100,
                    max_words=random.randint(4, 25),
                    min_words=random.randint(3, 8)
                )
                if response:
                    break
        
        # Post-process the response
        if response:
            # Clean up any odd punctuation
            response = response.strip()
            if response and not response.endswith(('.', '!', '?')):
                response += random.choice(['.', '!', '?'])
            
            # Ensure it's not too long
            if len(response) > self.MAX_MESSAGE_LENGTH:
                response = response[:self.MAX_MESSAGE_LENGTH - 3] + "..."
            
            # Update conversation context
            words = [w.lower() for w in response.split() if len(w) > 3]
            if words:
                self.conversation_context[channel_id] = words[-5:]  # Keep last 5 words
        
        return response

    @commands.Cog.listener()
    async def on_message(self, message):
        """Main message handler with improved logic"""
        # Ignore bots and empty messages
        if message.author.bot or not message.content:
            return
        
        channel_id = message.channel.id
        
        # --- TRAINING PHASE ---
        clean_text = self.sanitize_message(message.content)
        if clean_text and len(clean_text) > 1:
            await self.add_message(channel_id, clean_text)
            
            # Update conversation context
            words = [w.lower() for w in clean_text.split() if len(w) > 3]
            if words:
                self.conversation_context[channel_id] = words[-5:]
        
        # --- GENERATION PHASE ---
        # Check triggers
        is_mentioned = self.bot.user in message.mentions
        reply_prob = self.calculate_reply_probability(message, is_mentioned)
        
        if (is_mentioned or random.random() < reply_prob) and message.content != "":
            # Check cooldown
            if channel_id in self.reply_cooldowns:
                elapsed = datetime.utcnow() - self.reply_cooldowns[channel_id]
                if elapsed.seconds < self.REPLY_COOLDOWN:
                    return
            
            async with message.channel.typing():
                # Small delay to seem more natural
                await asyncio.sleep(random.uniform(0.5, 2.0))
                
                model = await self.get_model(channel_id)
                
                if not model:
                    if is_mentioned:
                        await message.channel.send(
                            random.choice([
                                "I'm still learning from this chat!",
                                "Need more conversation to join in.",
                                "Say a bit more and I'll join the conversation!"
                            ])
                        )
                    return
                
                # Use the last message as context
                context = clean_text if clean_text else None
                response = await self.generate_response(model, context, channel_id)
                
                if response:
                    # Add random emoji occasionally (10% chance)
                    if random.random() < 0.1:
                        emojis = ['😊', '🤔', '😄', '🤖', '💭']
                        response += f" {random.choice(emojis)}"
                    
                    try:
                        sent = await message.channel.send(response)
                        
                        # Update cooldown
                        self.reply_cooldowns[channel_id] = datetime.utcnow()
                        
                        # Small chance to generate a follow-up
                        if random.random() < 0.05:
                            await asyncio.sleep(random.uniform(2, 5))
                            follow_up = model.make_sentence(tries=50)
                            if follow_up and len(follow_up) < 100:
                                await message.channel.send(follow_up)
                                
                    except discord.HTTPException as e:
                        logger.error(f"Error sending message: {e}")

    @commands.command(name="force", aliases=["f"])
    async def force_gen(self, ctx, seed_word: str = None):
        """Force the bot to generate a message with optional seed"""
        async with ctx.channel.typing():
            model = await self.get_model(ctx.channel.id)
            
            if not model:
                await ctx.send("❌ Not enough data to generate yet!")
                return
            
            response = None
            if seed_word:
                try:
                    response = model.make_sentence_with_start(
                        seed_word,
                        strict=False,
                        tries=100
                    )
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
        """Show statistics about the model"""
        channel_id = ctx.channel.id
        message_count = len(self.message_cache.get(channel_id, []))
        
        embed = discord.Embed(
            title="🤖 Model Statistics",
            color=discord.Color.blue(),
            timestamp=datetime.utcnow()
        )
        
        embed.add_field(name="Messages Stored", value=f"{message_count:,}", inline=True)
        
        if channel_id in self.models:
            embed.add_field(name="Model Cached", value="✅", inline=True)
        
        if channel_id in self.model_cache_time:
            last_update = self.model_cache_time[channel_id]
            embed.add_field(
                name="Last Model Update", 
                value=f"<t:{int(last_update.timestamp())}:R>",
                inline=False
            )
        
        await ctx.send(embed=embed)

    @commands.command(name="clearmodel", aliases=["reload"])
    @commands.has_permissions(manage_messages=True)
    async def clear_model(self, ctx):
        """Clear the cached model (forces regeneration)"""
        if ctx.channel.id in self.models:
            del self.models[ctx.channel.id]
            del self.model_cache_time[ctx.channel.id]
            await ctx.send("✅ Model cache cleared! It will regenerate on next reply.")
        else:
            await ctx.send("No cached model found.")

    @commands.command(name="savememory")
    @commands.has_permissions(manage_messages=True)
    async def save_memory_cmd(self, ctx):
        """Manually save memory to disk"""
        await self.save_messages(ctx.channel.id)
        await ctx.send("💾 Memory saved to disk!")

async def setup(bot):
    await bot.add_cog(GenAICog(bot))