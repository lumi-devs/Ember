import discord
from discord.ext import commands
from .database import ShameDatabase
from .utils import ShameUtils
from .config import ShameConfig
import time

class ShameSystem(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = ShameDatabase(ShameConfig.DB_PATH)
        self.utils = ShameUtils(self.db, self.bot)
        self._blacklist_cache = {}
        self._cache_timeout = 300
        self.tasks = None  # Will be set after initialization
    
    async def cog_load(self):
        try:
            await self.db.init_db()
            print("[ShameSystem] Cog loaded successfully ✅")
            
            # Import tasks here to avoid circular imports
            from .tasks import ShameTasks
            self.tasks = ShameTasks(self.bot, self.db)
            self.tasks.start_tasks()
            
        except Exception as e:
            print(f"[ShameSystem] Error loading cog: {e}")
    
    def _clean_old_cache(self):
        """Remove cache entries older than timeout"""
        current_time = time.time()
        to_remove = []
        for key, (value, timestamp) in self._blacklist_cache.items():
            if current_time - timestamp > self._cache_timeout:
                to_remove.append(key)
        for key in to_remove:
            del self._blacklist_cache[key]
    
    @commands.Cog.listener()
    async def on_message(self, message):
        # Ignore bots and DMs
        if message.author.bot or not message.guild:
            return
        
        # More lenient rate limiting - 1 message per 0.5 seconds
        if hasattr(self, '_last_message_time'):
            time_since_last = time.time() - self._last_message_time
            if time_since_last < 0.5:
                return
        self._last_message_time = time.time()
        
        # Check if channel/category is blacklisted from tracking
        if not await self.utils.is_channel_tracked(message.channel):
            return
        
        # Clean old cache entries periodically
        if len(self._blacklist_cache) > 1000:
            self._clean_old_cache()
        
        # Check if user is blacklisted (with caching)
        cache_key = f"blacklist_{message.author.id}"
        current_time = time.time()
        
        if cache_key in self._blacklist_cache:
            is_blacklisted, cache_time = self._blacklist_cache[cache_key]
            if current_time - cache_time < self._cache_timeout:
                if is_blacklisted:
                    return
            else:
                del self._blacklist_cache[cache_key]
        
        # Fresh check
        is_blacklisted = await self.db.is_blacklisted(
            message.author.id,
            [role.id for role in message.author.roles]
        )
        
        # Cache the result
        self._blacklist_cache[cache_key] = (is_blacklisted, current_time)
        
        if is_blacklisted:
            return
        
        # Get user data
        user_data = await self.db.get_user(message.author.id)
        current_points = user_data['points'] if user_data else 0
        hourly_points = user_data['hourly_points'] if user_data else 0
        total_messages = user_data['total_messages'] if user_data else 0
        
        # Check hourly limit
        if hourly_points >= ShameConfig.MAX_POINTS_PER_HOUR:
            return
        
        # Update points
        new_points = current_points + 1
        new_hourly_points = hourly_points + 1
        new_total_messages = total_messages + 1
        
        await self.db.update_user(
            message.author.id,
            points=new_points,
            hourly_points=new_hourly_points,
            total_messages=new_total_messages,
            last_message_time=message.created_at.timestamp()
        )
        
        # Debug logging (optional)
        if new_points % 50 == 0:  # Log every 50 points
            print(f"[ShameSystem] {message.author.display_name}: {new_points} points, {new_total_messages} messages")   