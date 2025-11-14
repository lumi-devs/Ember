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
                    
                    if new_points == 0:
                        # Remove user from database when they hit 0 points
                        await db.execute('DELETE FROM users WHERE user_id = ?', (user_id,))
                        print(f"[Shamer] Removed user {user_id} (reached 0 points)")
                    else:
                        await self.db.update_user(
                            user_id, 
                            points=new_points,
                            hourly_points=0  # Reset hourly counter
                        )
                        print(f"[Shamer] Decayed {decay_amount} points from user {user_id}")
            
            await db.commit()
    
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
        
        # Check if we already sent a message today
        last_sent = await self.db.get_system_state('last_leaderboard_sent')
        if last_sent:
            last_sent_date = datetime.fromisoformat(last_sent).date()
            if last_sent_date == datetime.now().date():
                print("[Shamer] Already sent leaderboard today, skipping...")
                return
        
        # Get top 5 for leaderboard (only users with points > 0)
        leaderboard = await self.db.get_leaderboard(5)
        
        if not leaderboard:
            print("[Shamer] No users with points found for leaderboard")
            return
        
        # Post in leaderboard channel
        channel = self.bot.get_channel(ShameConfig.LEADERBOARD_CHANNEL_ID)
        if not channel:
            print("[Shamer] ERROR: Channel not found")
            return
        
        message_sent = False
        role_message_sent = False
        
        # Create leaderboard embed
        embed = discord.Embed(
            title="🏆 Hall of Shame - Leaderboard Update",
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow()
        )
        
        symbols = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
        
        for i, user_data in enumerate(leaderboard):
            user = self.bot.get_user(user_data['user_id'])
            username = user.mention if user else f"<@{user_data['user_id']}>"
            
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
        
        # Send leaderboard embed
        try:
            leaderboard_message = await channel.send(embed=embed)
            message_sent = True
            print(f"[Shamer] ✅ Leaderboard sent at {datetime.now()} | Message ID: {leaderboard_message.id}")
        except Exception as e:
            print(f"[Shamer] ❌ Failed to send leaderboard: {e}")
        
        # Announce role winners - FIXED: Use direct mention syntax
        top_5_ids = [user['user_id'] for user in leaderboard[:5]]
        winners_mentions = [f"<@{user_id}>" for user_id in top_5_ids]
        
        role_embed = discord.Embed(
            title="🎉 New Shamers!",
            description=f"Congratulations to {', '.join(winners_mentions)} for earning the shame role!",
            color=discord.Color.orange()
        )
        
        # Send role announcement
        try:
            role_message = await channel.send(f"<@&{ShameConfig.SHAME_ROLE_ID}>", embed=role_embed)
            role_message_sent = True
            print(f"[Shamer] ✅ Role announcement sent at {datetime.now()} | Message ID: {role_message.id}")
        except Exception as e:
            print(f"[Shamer] ❌ Failed to send role announcement: {e}")
        
        # Assign roles to top 5
        role_assignment_success = True
        for guild in self.bot.guilds:
            top_5_ids = [user['user_id'] for user in leaderboard[:5]]
            try:
                await self.utils.assign_shame_role(guild, top_5_ids)
                print(f"[Shamer] ✅ Roles assigned successfully in guild: {guild.name}")
            except Exception as e:
                print(f"[Shamer] ❌ Failed to assign roles in guild {guild.name}: {e}")
                role_assignment_success = False
        
        # Update last leaderboard time only if messages were sent
        if message_sent or role_message_sent:
            await self.db.set_system_state('last_leaderboard', datetime.now().isoformat())
            await self.db.set_system_state('last_leaderboard_sent', datetime.now().isoformat())
            print("[Shamer] ✅ Leaderboard rotation completed successfully!")
        else:
            print("[Shamer] ❌ Leaderboard rotation failed - no messages sent!")
    
    def start_tasks(self):
        """Start all background tasks"""
        self.hourly_decay.start()
        self.daily_leaderboard_check.start()
        print("[Shamer] Background tasks started!")