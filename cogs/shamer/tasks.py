import discord
from discord.ext import tasks
from datetime import datetime, timedelta
from .database import ShameDatabase
from .utils import ShameUtils
from .config import ShameConfig
import aiosqlite

class ShameTasks:
    def __init__(self, bot, database: ShameDatabase):
        self.bot = bot
        self.db = database
        self.utils = ShameUtils(database, self.bot)
    
    @tasks.loop(hours=1)
    async def hourly_decay(self):
        """Apply hourly point decay to all users"""
        print("[Shamer] Applying hourly decay...")
        async with aiosqlite.connect(self.db.db_path) as db:
            cursor = await db.execute('SELECT user_id, points FROM users')
            users = await cursor.fetchall()
            
            for user_id, points in users:
                if points > 0:
                    decay_amount = await self.utils.calculate_decay(points)
                    new_points = max(0, points - decay_amount)
                    
                    await self.db.update_user(
                        user_id, 
                        points=new_points,
                        hourly_points=0  # Reset hourly counter
                    )
                    print(f"[Shamer] Decayed {decay_amount} points from user {user_id}")
    
    @tasks.loop(hours=24)
    async def daily_leaderboard_check(self):
        """Check if it's time to post leaderboard and assign roles"""
        print("[Shamer] Checking for leaderboard rotation...")
        last_leaderboard = await self.db.get_system_state('last_leaderboard')
        
        if last_leaderboard:
            last_date = datetime.fromisoformat(last_leaderboard)
            next_leaderboard = last_date + timedelta(days=ShameConfig.LEADERBOARD_INTERVAL_DAYS)
            
            if datetime.now() >= next_leaderboard:
                await self.post_leaderboard_and_assign_roles()
        else:
            # First time running, set initial state
            await self.db.set_system_state('last_leaderboard', datetime.now().isoformat())
    
    async def post_leaderboard_and_assign_roles(self):
        """Post leaderboard and assign shame role to top 5"""
        print("[Shamer] Posting leaderboard and assigning roles...")
        
        # Get top 10 for leaderboard
        leaderboard = await self.db.get_leaderboard(10)
        
        # Post in leaderboard channel
        channel = self.bot.get_channel(ShameConfig.LEADERBOARD_CHANNEL_ID)
        if channel:
            # Create leaderboard embed
            embed = discord.Embed(
                title="🏆 Hall of Shame - Leaderboard Update",
                color=discord.Color.gold(),
                timestamp=discord.utils.utcnow()
            )
            
            symbols = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
            
            for i, user_data in enumerate(leaderboard):
                user = self.bot.get_user(user_data['user_id'])
                username = user.mention if user else f"Unknown User ({user_data['user_id']})"
                
                symbol = symbols[i] if i < len(symbols) else "•"
                
                embed.add_field(
                    name=f"{symbol} {username}",
                    value=f"**{user_data['points']}** points • {user_data['total_messages']} messages",
                    inline=False
                )
            
            # Calculate next rotation time
            next_reroll = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=4)
            time_until_reroll = next_reroll - datetime.now()
            days = time_until_reroll.days
            hours = time_until_reroll.seconds // 3600
            
            embed.set_footer(text=f"You can do better gng, rerolling in {days}d {hours}h")
            
            await channel.send(embed=embed)
            
            # Announce role winners
            top_5_ids = [user['user_id'] for user in leaderboard[:5]]
            winners_mentions = [f"<@{user_id}>" for user_id in top_5_ids]
            
            role_embed = discord.Embed(
                title="🎉 New Shame Role Holders!",
                description=f"Congratulations to {', '.join(winners_mentions)} for earning the shame role!",
                color=discord.Color.blue()
            )
            await channel.send(embed=role_embed)
        
        # Assign roles to top 5
        for guild in self.bot.guilds:
            top_5_ids = [user['user_id'] for user in leaderboard[:5]]
            await self.utils.assign_shame_role(guild, top_5_ids)
        
        # Update last leaderboard time
        await self.db.set_system_state('last_leaderboard', datetime.now().isoformat())
        print("[Shamer] Leaderboard rotation completed!")
    
    def start_tasks(self):
        """Start all background tasks"""
        self.hourly_decay.start()
        self.daily_leaderboard_check.start()
        print("[Shamer] Background tasks started!")