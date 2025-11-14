import discord
from discord.ext import commands
from .database import ShameDatabase
from .commands import ShameCommands
from .tasks import ShameTasks
from .config import ShameConfig

class ShameSystem(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = ShameDatabase(ShameConfig.DB_PATH)
        self.tasks = ShameTasks(bot, self.db)
    
    async def cog_load(self):
        await self.db.init_db()
        self.tasks.start_tasks()
    
    @commands.Cog.listener()
    async def on_message(self, message):
        # Ignore bots and DMs
        if message.author.bot or not message.guild:
            return
        
        # Check if user can earn points
        can_earn = await self.db.is_blacklisted(
            message.author.id,
            [role.id for role in message.author.roles]
        )
        
        if can_earn:
            return
        
        user_data = await self.db.get_user(message.author.id)
        current_points = user_data['points'] if user_data else 0
        hourly_points = user_data['hourly_points'] if user_data else 0
        
        # Check hourly limit
        if hourly_points >= ShameConfig.MAX_POINTS_PER_HOUR:
            return
        
        # Update points
        new_points = current_points + 1
        new_hourly_points = hourly_points + 1
        
        await self.db.update_user(
            message.author.id,
            points=new_points,
            hourly_points=new_hourly_points,
            total_messages=(user_data['total_messages'] + 1) if user_data else 1,
            last_message_time=message.created_at.timestamp()
        )
