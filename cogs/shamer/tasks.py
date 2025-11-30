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
        self.utils = ShameUtils(database, self.bot)  # Initialize utils here
        self.last_rotation_check = None
    
    async def initialize_tasks(self):
        """Initialize tasks after bot is ready"""
        await self.bot.wait_until_ready()
        self.start_tasks()
    
    @tasks.loop(hours=1)
    async def hourly_decay(self):
        """Apply hourly point decay to all users"""
        try:
            await self.bot.wait_until_ready()
            print("[Shamer] Applying hourly decay...")
            
            # Get all users with points > 0
            users = await self.db.get_leaderboard(10000)
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
                    updates.append((new_points, 0, user_id))
                    print(f"[Shamer] Decayed {decay_amount} points from user {user_id}")
            
            if updates:
                await self.db.batch_update_users(updates)
            if deletions:
                await self.db.batch_delete_users(deletions)
                
            print(f"[Shamer] Hourly decay completed: {len(updates)} updated, {len(deletions)} removed")
            
        except Exception as e:
            print(f"[Shamer] Critical error in hourly_decay: {e}")

    @tasks.loop(minutes=5)
    async def rotation_checker(self):
        """Check if it's time for leaderboard rotation"""
        try:
            await self.bot.wait_until_ready()
            
            # Get last rotation time
            last_rotation_str = await self.db.get_system_state('last_rotation_time')
            now = datetime.now()
            
            if not last_rotation_str:
                # First time - set initial rotation time to now
                await self.db.set_system_state('last_rotation_time', now.isoformat())
                print("[Shamer] Initialized rotation timer")
                return
            
            last_rotation = datetime.fromisoformat(last_rotation_str)
            time_since_rotation = now - last_rotation
            
            # Check if 4 days have passed
            if time_since_rotation >= timedelta(days=ShameConfig.LEADERBOARD_INTERVAL_DAYS):
                print(f"[Shamer] 🚨 Rotation time reached! Last rotation: {last_rotation}")
                await self.execute_leaderboard_rotation()
                
        except Exception as e:
            print(f"[Shamer] Error in rotation_checker: {e}")

    async def execute_leaderboard_rotation(self):
        """Execute the full leaderboard rotation process"""
        try:
            print("[Shamer] 🎯 Starting leaderboard rotation...")
            
            # Step 1: Post leaderboard and assign roles
            success = await self.post_leaderboard_and_assign_roles()
            
            if success:
                # Step 2: Reset all stats
                await self.hard_reset_all_stats()
                
                # Step 3: Update rotation time
                await self.db.set_system_state('last_rotation_time', datetime.now().isoformat())
                
                print("[Shamer] ✅ Leaderboard rotation completed successfully!")
            else:
                print("[Shamer] ❌ Leaderboard rotation failed - will retry next check")
            
        except Exception as e:
            print(f"[Shamer] ❌ Error in leaderboard rotation: {e}")

    async def hard_reset_all_stats(self):
        """Reset all user points AND message counts to 0"""
        try:
            print("[Shamer] 🚨 Performing 4-day hard reset of ALL stats...")
            
            # Get user count before reset for logging
            users_before = await self.db.get_leaderboard(10000)
            user_count = len(users_before)
            
            if user_count == 0:
                print("[Shamer] No users to reset")
                return
            
            # Reset ALL stats to 0 but keep users in database
            async with aiosqlite.connect(self.db.db_path) as db:
                await db.execute('UPDATE users SET points = 0, hourly_points = 0, total_messages = 0')
                await db.commit()
            
            print(f"[Shamer] ✅ Hard reset completed: {user_count} users reset to 0 points & 0 messages")
            
        except Exception as e:
            print(f"[Shamer] ❌ Error in hard_reset_all_stats: {e}")

    async def post_leaderboard_and_assign_roles(self):
        """Post leaderboard and assign shame role to top 5"""
        try:
            print("[Shamer] Posting leaderboard and assigning roles...")
            
            # Get top 5 for leaderboard
            leaderboard = await self.db.get_leaderboard(5)
            
            if not leaderboard:
                print("[Shamer] No users with points found for leaderboard")
                return False
            
            # Post in leaderboard channel
            channel = self.bot.get_channel(ShameConfig.LEADERBOARD_CHANNEL_ID)
            if not channel:
                print("[Shamer] ERROR: Channel not found")
                return False
            
            # Build leaderboard description
            symbols = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
            description = ""
            
            for i, user_data in enumerate(leaderboard):
                user_id = user_data['user_id']
                user = self.bot.get_user(user_id)
                username = user.display_name if user else f"Unknown User ({user_id})"
                symbol = symbols[i] if i < len(symbols) else "•"
                
                line = f"{symbol} {username} **{user_data['points']}** points • {user_data['total_messages']} messages\n\n"
                description += line
            
            # Create embed
            embed = discord.Embed(
                title="🏆 Hall of Shame - Leaderboard Update",
                description=description,
                color=discord.Color.gold(),
                timestamp=discord.utils.utcnow()
            )
            
            # Calculate next rotation time
            next_reroll = datetime.now() + timedelta(days=ShameConfig.LEADERBOARD_INTERVAL_DAYS)
            time_until_reroll = next_reroll - datetime.now()
            days = time_until_reroll.days
            hours = time_until_reroll.seconds // 3600
            
            embed.set_footer(text=f"You can do better gng, rerolling in {days}d {hours}h")
            
            # Send leaderboard embed
            try:
                leaderboard_message = await channel.send(embed=embed)
                print(f"[Shamer] ✅ Leaderboard sent | Message ID: {leaderboard_message.id}")
            except Exception as e:
                print(f"[Shamer] ❌ Failed to send leaderboard: {e}")
                return False
            
            # Assign roles to top 5
            top_5_ids = [user['user_id'] for user in leaderboard[:5]]
            role_assignment_success = True
            
            for guild in self.bot.guilds:
                try:
                    await self.utils.assign_shame_role(guild, top_5_ids)
                    print(f"[Shamer] ✅ Roles assigned successfully in guild: {guild.name}")
                except Exception as e:
                    print(f"[Shamer] ❌ Failed to assign roles in guild {guild.name}: {e}")
                    role_assignment_success = False
            
            # Log successful execution
            await self.db.set_system_state('last_leaderboard_sent', datetime.now().isoformat())
            print("[Shamer] ✅ Leaderboard rotation completed!")
            return True
                
        except Exception as e:
            print(f"[Shamer] Critical error in post_leaderboard_and_assign_roles: {e}")
            return False

    def start_tasks(self):
        """Start all background tasks"""
        try:
            self.hourly_decay.start()
            self.rotation_checker.start()
            print("[Shamer] Background tasks started!")
        except Exception as e:
            print(f"[Shamer] Error starting tasks: {e}")

    async def manually_trigger_leaderboard(self):
        """Manually trigger leaderboard rotation"""
        try:
            print("[Shamer] 🚨 Manual leaderboard trigger requested...")
            await self.execute_leaderboard_rotation()
        except Exception as e:
            print(f"[Shamer] Error in manual trigger: {e}")