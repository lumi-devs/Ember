import asyncio
import aiosqlite
import discord
from .config import * 
from discord.ext import commands, tasks
from discord import app_commands, Embed, Color
from datetime import datetime, timedelta
from typing import Dict, Set

class Shamer(commands.Cog):
    """Hall of Shame system with points, decay, and role rotations"""
    
    def __init__(self, bot):
        self.bot = bot
        self.scores: Dict[int, float] = {}
        self.hourly_gain: Dict[int, int] = {}
        self.blacklisted_users: Set[int] = set()
        self.blacklisted_roles: Set[int] = set()
        self.db_lock = asyncio.Lock()
        self.next_rotation: datetime = None
        self.current_top5: Set[int] = set()
        
        # Start background tasks
        self.hourly_decay.start()
        self.rotation_check.start()

    async def cog_load(self):
        """Load data when cog starts"""
        await self.setup_db()
        await self.load_all_data()
        await self.calculate_next_rotation()

    async def setup_db(self):
        """Initialize database tables"""
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS shame_scores (
                    user_id INTEGER PRIMARY KEY,
                    score REAL DEFAULT 0,
                    hourly_gain INTEGER DEFAULT 0,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS shame_blacklist_users (
                    user_id INTEGER PRIMARY KEY
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS shame_blacklist_roles (
                    role_id INTEGER PRIMARY KEY
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS shame_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            await db.commit()

    async def load_all_data(self):
        """Load all data from database"""
        async with aiosqlite.connect(DB_PATH) as db:
            # Load scores
            async with db.execute("SELECT user_id, score, hourly_gain FROM shame_scores") as cursor:
                async for row in cursor:
                    self.scores[row[0]] = row[1]
                    self.hourly_gain[row[0]] = row[2]

            # Load blacklists
            async with db.execute("SELECT user_id FROM shame_blacklist_users") as cursor:
                self.blacklisted_users = {row[0] async for row in cursor}
            
            async with db.execute("SELECT role_id FROM shame_blacklist_roles") as cursor:
                self.blacklisted_roles = {row[0] async for row in cursor}

            # Load next rotation time
            async with db.execute("SELECT value FROM shame_meta WHERE key = 'next_rotation'") as cursor:
                result = await cursor.fetchone()
                if result:
                    self.next_rotation = datetime.fromisoformat(result[0])
            
            # Load current top5
            async with db.execute("SELECT value FROM shame_meta WHERE key = 'current_top5'") as cursor:
                result = await cursor.fetchone()
                if result:
                    self.current_top5 = set(map(int, result[0].split(','))) if result[0] else set()

        print(f"[Shamer] Loaded {len(self.scores)} users, {len(self.blacklisted_users)} blacklisted users")

    async def save_meta(self, key: str, value: str):
        """Save metadata to database"""
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT OR REPLACE INTO shame_meta (key, value) VALUES (?, ?)",
                (key, value)
            )
            await db.commit()

    async def calculate_next_rotation(self):
        """Calculate next rotation time if not set"""
        if not self.next_rotation:
            # Set first rotation to next midnight + 4 days
            now = datetime.now().astimezone()
            next_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=ROTATION_INTERVAL_DAYS)
            self.next_rotation = next_midnight
            await self.save_meta('next_rotation', self.next_rotation.isoformat())