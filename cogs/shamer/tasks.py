import discord
from discord.ext import tasks
from datetime import datetime, timedelta
from .database import ShameDatabase
from .utils import ShameUtils
from .config import ShameConfig
import aiosqlite
import time

class ShameTasks:
    def __init__(self, bot, database: ShameDatabase):
        self.bot = bot
        self.db = database
        self.utils = ShameUtils(database, self.bot)
    
    @tasks.loop(hours=1)
    async def hourly_decay(self):
        """Apply hourly point decay to all users"""
        try:
            await self.bot.wait_until_ready()
            print("[Shamer] Applying hourly decay...")
            
            # Get all users with points > 0
            users = await self.db.get_leaderboard(10000)  # Large limit to get all
            if not users:
                print("[Shamer] No users to decay")
                return
            
            updates = []
            deletions = []
            
            for user_data in users:
                user_id = user_data['user_id']
                points = user_data['points']
                
                decay_amount = await self.utils.calculate_decay(points)
                new_points = max(0, points - decay_amount)
                
                if new_points == 0:
                    deletions.append(user_id)
                    print(f"[Shamer] Removing user {user_id} (reached 0 points)")
                else:
                    updates.append((new_points, 0, user_id))  # points, hourly_points, user_id
                    print(f"[Shamer] Decayed {decay_amount} points from user {user_id}")
            
            # Batch operations
            if updates:
                await self.db.batch_update_users(updates)
            if deletions:
                await self.db.batch_delete_users(deletions)
                
            print(f"[Shamer] Hourly decay completed: {len(updates)} updated, {len(deletions)} removed")
            
        except Exception as e:
            print(f"[Shamer] Critical error in hourly_decay: {e}")
    
    @tasks.loop(hours=24)
    async def daily_leaderboard_check(self):
        """Check if it's time to post leaderboard and assign roles"""
        try:
            await self.bot.wait_until_ready()
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
                
        except Exception as e:
            print(f"[Shamer] Critical error in daily_leaderboard_check: {e}")
    
    async def post_leaderboard_and_assign_roles(self):
        """Post leaderboard and assign shame role to top 5"""
        try:
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
            
            # Build leaderboard description with string concatenation
            symbols = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
            description = ""
            
            for i, user_data in enumerate(leaderboard):
                user_id = user_data['user_id']
                symbol = symbols[i] if i < len(symbols) else "•"
                
                line = f"{symbol} <@{user_id}>\n**{user_data['points']}** points • {user_data['total_messages']} messages\n\n"
                description += line
            
            # Create single embed with all mentions in description
            embed = discord.Embed(
                title="🏆 Hall of Shame - Leaderboard Update",
                description=description,
                color=discord.Color.gold(),
                timestamp=discord.utils.utcnow()
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
            
            # Announce role winners
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
                
        except Exception as e:
            print(f"[Shamer] Critical error in post_leaderboard_and_assign_roles: {e}")

    def start_tasks(self):
        """Start all background tasks"""
        try:
            self.hourly_decay.start()
            self.daily_leaderboard_check.start()
            print("[Shamer] Background tasks started!")
        except Exception as e:
            print(f"[Shamer] Error starting tasks: {e}")