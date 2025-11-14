import aiosqlite
import json
from typing import List, Dict, Any, Optional

class ShameDatabase:
    def __init__(self, db_path: str):
        self.db_path = db_path
    
    async def init_db(self):
        async with aiosqlite.connect(self.db_path) as db:
            # Users table
            await db.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    points INTEGER DEFAULT 0,
                    hourly_points INTEGER DEFAULT 0,
                    last_message_time REAL,
                    total_messages INTEGER DEFAULT 0,
                    updated_at REAL
                )
            ''')
            
            # Blacklist table
            await db.execute('''
                CREATE TABLE IF NOT EXISTS blacklist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_id INTEGER NOT NULL,
                    entity_type TEXT NOT NULL, -- 'user' or 'role'
                    created_at REAL DEFAULT (julianday('now'))
                )
            ''')
            
            # Leaderboard history table
            await db.execute('''
                CREATE TABLE IF NOT EXISTS leaderboard_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    period_start REAL,
                    period_end REAL,
                    leaderboard_data TEXT, -- JSON stored leaderboard
                    created_at REAL DEFAULT (julianday('now'))
                )
            ''')
            
            # System state table
            await db.execute('''
                CREATE TABLE IF NOT EXISTS system_state (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at REAL DEFAULT (julianday('now'))
                )
            ''')
            
            await db.commit()
    
    async def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                'SELECT * FROM users WHERE user_id = ?', 
                (user_id,)
            )
            row = await cursor.fetchone()
            return dict(row) if row else None
    
    async def update_user(self, user_id: int, **kwargs):
        async with aiosqlite.connect(self.db_path) as db:
            if await self.get_user(user_id):
                # Update existing user
                set_clause = ', '.join([f"{key} = ?" for key in kwargs.keys()])
                values = list(kwargs.values()) + [user_id]
                await db.execute(
                    f'UPDATE users SET {set_clause} WHERE user_id = ?',
                    values
                )
            else:
                # Insert new user
                columns = ['user_id'] + list(kwargs.keys())
                placeholders = ', '.join(['?'] * len(columns))
                values = [user_id] + list(kwargs.values())
                await db.execute(
                    f'INSERT INTO users ({", ".join(columns)}) VALUES ({placeholders})',
                    values
                )
            await db.commit()
    
    async def get_leaderboard(self, limit: int = 10) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                'SELECT * FROM users ORDER BY points DESC LIMIT ?',
                (limit,)
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    
    async def get_user_rank(self, user_id: int) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute('''
                SELECT COUNT(*) + 1 as rank FROM users 
                WHERE points > (SELECT points FROM users WHERE user_id = ?)
            ''', (user_id,))
            row = await cursor.fetchone()
            return row[0] if row else 0
    
    # Blacklist methods
    async def add_to_blacklist(self, entity_id: int, entity_type: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                'INSERT OR IGNORE INTO blacklist (entity_id, entity_type) VALUES (?, ?)',
                (entity_id, entity_type)
            )
            await db.commit()
    
    async def remove_from_blacklist(self, entity_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                'DELETE FROM blacklist WHERE entity_id = ?',
                (entity_id,)
            )
            await db.commit()
    
    async def get_blacklist(self) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute('SELECT * FROM blacklist')
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    
    async def is_blacklisted(self, user_id: int, user_roles: List[int]) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            # Check if user is blacklisted
            cursor = await db.execute(
                'SELECT 1 FROM blacklist WHERE entity_id = ? AND entity_type = "user"',
                (user_id,)
            )
            if await cursor.fetchone():
                return True
            
            # Check if any of user's roles are blacklisted
            for role_id in user_roles:
                cursor = await db.execute(
                    'SELECT 1 FROM blacklist WHERE entity_id = ? AND entity_type = "role"',
                    (role_id,)
                )
                if await cursor.fetchone():
                    return True
            
            return False
    
    # System state methods
    async def set_system_state(self, key: str, value: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                'INSERT OR REPLACE INTO system_state (key, value) VALUES (?, ?)',
                (key, value)
            )
            await db.commit()
    
    async def get_system_state(self, key: str) -> Optional[str]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                'SELECT value FROM system_state WHERE key = ?',
                (key,)
            )
            row = await cursor.fetchone()
            return row[0] if row else None