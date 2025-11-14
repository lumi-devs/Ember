import discord
from discord.ext import commands
import aiosqlite
from .config import *

class ShamerEvents:
    def __init__(self, cog):
        self.cog = cog

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Track messages and award points"""
        if not message.guild or message.author.bot:
            return

        # Check blacklists
        if message.author.id in self.cog.blacklisted_users:
            return
        
        user_roles = [role.id for role in getattr(message.author, 'roles', [])]
        if any(role_id in self.cog.blacklisted_roles for role_id in user_roles):
            return

        user_id = message.author.id
        
        # Check hourly cap
        current_hourly = self.cog.hourly_gain.get(user_id, 0)
        if current_hourly >= MAX_HOURLY_GAIN:
            return

        # Award point
        async with self.cog.db_lock:
            self.cog.scores[user_id] = self.cog.scores.get(user_id, 0) + 1
            self.cog.hourly_gain[user_id] = current_hourly + 1

            # Save to database
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    """INSERT INTO shame_scores (user_id, score, hourly_gain) 
                       VALUES (?, ?, ?) 
                       ON CONFLICT(user_id) 
                       DO UPDATE SET score = excluded.score, hourly_gain = excluded.hourly_gain""",
                    (user_id, self.cog.scores[user_id], self.cog.hourly_gain[user_id])
                )
                await db.commit()