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
    
    @tasks.loop(minutes=15)  # Check more frequently to catch missed dates
    async def leaderboard_scheduler(self):
        """Check if it's time to post leaderboard based on fixed schedule"""
        try:
            await self.bot.wait_until_ready()
            
            # Get the next scheduled leaderboard date from database
            next_leaderboard_str = await self.db.get_system_state('next_leaderboard_date')
            
            if not next_leaderboard_str:
                # First time running - calculate next leaderboard date
                await self.initialize_leaderboard_schedule()
                return
            
            next_leaderboard = datetime.fromisoformat(next_leaderboard_str)
            now = datetime.now()
            
            # If current time is past the scheduled date, trigger leaderboard
            if now >= next_leaderboard:
                print(f"[Shamer] 🚨 Leaderboard scheduled date reached: {next_leaderboard}")
                await self.execute_leaderboard_rotation()
                
        except Exception as e:
            print(f"[Shamer] Critical error in leaderboard_scheduler: {e}")
    
    async def initialize_leaderboard_schedule(self):
        """Initialize the leaderboard schedule based on fixed intervals"""
        try:
            # Calculate next leaderboard date (every 4 days at midnight)
            now = datetime.now()
            
            # Find the next 4-day interval starting from a known date
            # You can set a fixed start date or use the current date
            start_date = datetime(2024, 1, 1)  # Example fixed start date
            days_since_start = (now - start_date).days
            intervals_passed = days_since_start // ShameConfig.LEADERBOARD_INTERVAL_DAYS
            next_interval = intervals_passed + 1
            
            next_leaderboard = start_date + timedelta(days=next_interval * ShameConfig.LEADERBOARD_INTERVAL_DAYS)
            
            # Set to midnight
            next_leaderboard = next_leaderboard.replace(hour=0, minute=0, second=0, microsecond=0)
            
            await self.db.set_system_state('next_leaderboard_date', next_leaderboard.isoformat())
            print(f"[Shamer] Initialized leaderboard schedule. Next leaderboard: {next_leaderboard}")
            
        except Exception as e:
            print(f"[Shamer] Error initializing leaderboard schedule: {e}")
    
    async def execute_leaderboard_rotation(self):
        """Execute the full leaderboard rotation process"""
        try:
            print("[Shamer] 🎯 Executing scheduled leaderboard rotation...")
            
            # Step 1: Post leaderboard and assign roles
            await self.post_leaderboard_and_assign_roles()
            
            # Step 2: Reset all stats
            await self.hard_reset_all_stats()
            
            # Step 3: Schedule next leaderboard
            await self.schedule_next_leaderboard()
            
            print("[Shamer] ✅ Leaderboard rotation completed successfully!")
            
        except Exception as e:
            print(f"[Shamer] ❌ Error in leaderboard rotation: {e}")
    
    async def schedule_next_leaderboard(self):
        """Schedule the next leaderboard date"""
        try:
            now = datetime.now()
            next_leaderboard = now + timedelta(days=ShameConfig.LEADERBOARD_INTERVAL_DAYS)
            
            # Set to midnight
            next_leaderboard = next_leaderboard.replace(hour=0, minute=0, second=0, microsecond=0)
            
            await self.db.set_system_state('next_leaderboard_date', next_leaderboard.isoformat())
            print(f"[Shamer] 📅 Next leaderboard scheduled for: {next_leaderboard}")
            
        except Exception as e:
            print(f"[Shamer] Error scheduling next leaderboard: {e}")
    
    async def hard_reset_all_stats(self):
        """Reset all user points AND message counts to 0 (keeps users in database)"""
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
            
            # Log the reset in leaderboard channel
            channel = self.bot.get_channel(ShameConfig.LEADERBOARD_CHANNEL_ID)
            if channel:
                reset_embed = discord.Embed(
                    title="🔄 Statistics Reset",
                    description=f"All user points and message counts have been reset to zero!\n**{user_count}** users affected.",
                    color=discord.Color.orange(),
                    timestamp=discord.utils.utcnow()
                )
                reset_embed.set_footer(text="Fresh start for the new rotation!")
                
                try:
                    await channel.send(embed=reset_embed)
                    print("[Shamer] ✅ Reset announcement sent")
                except Exception as e:
                    print(f"[Shamer] ❌ Failed to send reset announcement: {e}")
                    
        except Exception as e:
            print(f"[Shamer] ❌ Error in hard_reset_all_stats: {e}")
    
    async def post_leaderboard_and_assign_roles(self):
        """Post leaderboard and assign shame role to top 5"""
        try:
            print("[Shamer] Posting leaderboard and assigning roles...")
            
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
                user = self.bot.get_user(user_id)
                username = user.display_name if user else f"Unknown User ({user_id})"
                symbol = symbols[i] if i < len(symbols) else "•"
                
                line = f"{symbol} {username} **{user_data['points']}** points\n\n"
                description += line
            
            # Create single embed with all mentions in description
            embed = discord.Embed(
                title="🏆 Hall of Shame - Leaderboard Update",
                description=description,
                color=discord.Color.gold(),
                timestamp=discord.utils.utcnow()
            )
            
            # Calculate next rotation time
            next_reroll = datetime.now() + timedelta(days=ShameConfig.LEADERBOARD_INTERVAL_DAYS)
            next_reroll = next_reroll.replace(hour=0, minute=0, second=0, microsecond=0)
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
            
            # Log successful execution
            if message_sent or role_message_sent:
                await self.db.set_system_state('last_leaderboard_executed', datetime.now().isoformat())
                print("[Shamer] ✅ Leaderboard posted successfully!")
            else:
                print("[Shamer] ❌ Leaderboard posting failed - no messages sent!")
                
        except Exception as e:
            print(f"[Shamer] Critical error in post_leaderboard_and_assign_roles: {e}")

    @tasks.loop(hours=24)
    async def maintenance_check(self):
        """Daily maintenance to ensure schedule integrity"""
        try:
            await self.bot.wait_until_ready()
            
            # Verify next leaderboard date exists
            next_leaderboard_str = await self.db.get_system_state('next_leaderboard_date')
            if not next_leaderboard_str:
                print("[Shamer] Maintenance: No leaderboard schedule found, initializing...")
                await self.initialize_leaderboard_schedule()
                return
            
            # Verify the date is in the future
            next_leaderboard = datetime.fromisoformat(next_leaderboard_str)
            now = datetime.now()
            
            if next_leaderboard <= now:
                print("[Shamer] Maintenance: Found overdue leaderboard, executing now...")
                await self.execute_leaderboard_rotation()
                
        except Exception as e:
            print(f"[Shamer] Error in maintenance check: {e}")

    def start_tasks(self):
        """Start all background tasks"""
        try:
            self.hourly_decay.start()
            self.leaderboard_scheduler.start()  # More frequent checking
            self.maintenance_check.start()  # Daily integrity check
            print("[Shamer] Background tasks started!")
        except Exception as e:
            print(f"[Shamer] Error starting tasks: {e}")

    async def manually_trigger_leaderboard(self):
        """Manually trigger leaderboard rotation (for testing or recovery)"""
        try:
            print("[Shamer] 🚨 Manual leaderboard trigger requested...")
            await self.execute_leaderboard_rotation()
        except Exception as e:
            print(f"[Shamer] Error in manual trigger: {e}")