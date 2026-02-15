import discord
from discord.ext import tasks
from datetime import datetime, timedelta
import pytz
from collections import defaultdict

class RoleMentionTracker:
    def __init__(self, cog):
        self.cog = cog
        self.bot = cog.bot
        self.ist = pytz.timezone('Asia/Kolkata')
        self.data_manager = cog.data_manager
        
        # Load role mention data
        self.role_mentions = self.data_manager.load_role_mentions()
        
        # Start daily reset task
        self.daily_reset.start()
    
    def cog_unload(self):
        """Cleanup when tracker is unloaded"""
        self.daily_reset.cancel()
        self.data_manager.save_role_mentions(self.role_mentions)
    
    @tasks.loop(minutes=1)
    async def daily_reset(self):
        """Reset counters daily at 4 AM IST"""
        now = datetime.now(self.ist)
        
        if now.hour == 4 and now.minute == 0:
            # Log the reset
            log_channel = self.cog.get_log_channel()
            if log_channel:
                embed = discord.Embed(
                    title="📊 Daily Role Mention Reset",
                    description="All role mention counters have been reset for the new day.",
                    color=discord.Color.green()
                )
                embed.add_field(
                    name="Reset Time", 
                    value=f"{now.strftime('%Y-%m-%d %H:%M:%S')} IST"
                )
                embed.set_footer(text="Counters reset daily at 4:00 AM IST")
                await log_channel.send(embed=embed)
            
            # Reset all counters
            self.role_mentions.clear()
            self.data_manager.save_role_mentions(self.role_mentions)
            
            print(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] Role mention counters reset at 4 AM IST")
    
    @daily_reset.before_loop
    async def before_daily_reset(self):
        """Wait until bot is ready and sync to 4 AM IST"""
        await self.bot.wait_until_ready()
        
        now = datetime.now(self.ist)
        target = now.replace(hour=4, minute=0, second=0, microsecond=0)
        
        if now >= target:
            target += timedelta(days=1)
        
        await discord.utils.sleep_until(target.astimezone(pytz.UTC))
    
    def increment_mentions(self, guild_id: str, role_id: str):
        """Increment mention count for a role"""
        self.role_mentions[guild_id][role_id] += 1
        self.data_manager.save_role_mentions(self.role_mentions)
    
    def get_role_count(self, guild_id: str, role_id: str) -> int:
        """Get mention count for a specific role"""
        return self.role_mentions.get(guild_id, {}).get(role_id, 0)
    
    def get_guild_stats(self, guild_id: str) -> dict:
        """Get all role mention stats for a guild"""
        if guild_id not in self.role_mentions:
            return {"total": 0, "unique": 0, "roles": {}}
        
        roles_data = dict(self.role_mentions[guild_id])
        total = sum(roles_data.values())
        unique = len(roles_data)
        
        return {
            "total": total,
            "unique": unique,
            "roles": roles_data
        }
    
    def reset_guild(self, guild_id: str):
        """Reset all counters for a specific guild"""
        if guild_id in self.role_mentions:
            self.role_mentions[guild_id].clear()
            self.data_manager.save_role_mentions(self.role_mentions)