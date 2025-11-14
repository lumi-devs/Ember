import asyncio
from discord.ext import tasks
from datetime import datetime, timedelta
import aiosqlite
from .events import *

class ShamerTasks:
    def __init__(self, cog):
        self.cog = cog

    @tasks.loop(minutes=60)
    async def hourly_decay(self):
        """Apply hourly decay to all scores"""
        print(f"[Shamer] Applying hourly decay at {datetime.now()}")
        
        async with self.cog.db_lock:
            updates = []
            for user_id, score in list(self.cog.scores.items()):
                if score == 0:
                    continue
                    
                # Calculate decay (10% or 10 points, whichever is lower)
                decay_amount = min(abs(score) * HOURLY_DECAY_PERCENT, HOURLY_DECAY_MIN)
                
                # Apply decay (push toward zero)
                if score > 0:
                    new_score = max(0, score - decay_amount)
                else:
                    new_score = min(0, score + decay_amount)
                
                if new_score != score:
                    self.cog.scores[user_id] = round(new_score, 2)
                    updates.append((new_score, user_id))
            
            # Reset hourly gains
            self.cog.hourly_gain.clear()
            
            # Save to database
            if updates:
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.executemany(
                        "UPDATE shame_scores SET score = ?, hourly_gain = 0 WHERE user_id = ?",
                        updates
                    )
                    await db.commit()
            
            print(f"[Shamer] Decay applied to {len(updates)} users")

    @tasks.loop(minutes=5)
    async def rotation_check(self):
        """Check if it's time for rotation"""
        if not self.cog.next_rotation:
            return
            
        now = datetime.now().astimezone()
        if now >= self.cog.next_rotation:
            await self.cog.do_rotation()