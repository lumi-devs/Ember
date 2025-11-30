import aiosqlite
import json
from typing import List, Dict, Any, Optional
import time

class ShameDatabase:
    def __init__(self, db_path: str):
        self.db_path = db_path
    
    async def init_db(self):
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # Users table
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS users (
                        user_id INTEGER PRIMARY KEY,
                        points INTEGER DEFAULT 0,
                        hourly_points INTEGER DEFAULT 0,
                        last_message_time REAL,
                        total_messages INTEGER DEFAULT 0,
                        updated_at REAL DEFAULT (julianday('now'))
                    )
                ''')
                
                # Blacklist table
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS blacklist (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        entity_id INTEGER NOT NULL,
                        entity_type TEXT NOT NULL,
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
                
                # Leaderboard history table (ADDED)
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS leaderboard_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        rotation_date REAL,
                        top_users TEXT,
                        created_at REAL DEFAULT (julianday('now'))
                    )
                ''')
                
                # Create indexes
                await db.execute('CREATE INDEX IF NOT EXISTS idx_users_points ON users(points DESC)')
                await db.execute('CREATE INDEX IF NOT EXISTS idx_blacklist_entity ON blacklist(entity_id, entity_type)')
                await db.execute('CREATE INDEX IF NOT EXISTS idx_users_updated ON users(updated_at)')
                
                await db.commit()
                print("[ShameDatabase] Database initialized with all tables ✅")
        except Exception as e:
            print(f"[ShameDatabase] Error initializing database: {e}")
            raise
        
    async def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    'SELECT * FROM users WHERE user_id = ?', 
                    (user_id,)
                )
                row = await cursor.fetchone()
                return dict(row) if row else None
        except Exception as e:
            print(f"[ShameDatabase] Error in get_user: {e}")
            return None
    
    async def update_user(self, user_id: int, **kwargs):
        try:
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
        except Exception as e:
            print(f"[ShameDatabase] Error in update_user: {e}")
    
    async def batch_update_users(self, updates: List[tuple]):
        """Batch update users: (points, hourly_points, user_id)"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.executemany(
                    'UPDATE users SET points = ?, hourly_points = ? WHERE user_id = ?',
                    updates
                )
                await db.commit()
        except Exception as e:
            print(f"[ShameDatabase] Error in batch_update_users: {e}")
    
    async def batch_delete_users(self, user_ids: List[int]):
        """Batch delete users"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.executemany(
                    'DELETE FROM users WHERE user_id = ?',
                    [(uid,) for uid in user_ids]
                )
                await db.commit()
        except Exception as e:
            print(f"[ShameDatabase] Error in batch_delete_users: {e}")
    
    async def get_leaderboard(self, limit: int = 10) -> List[Dict[str, Any]]:
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    'SELECT * FROM users WHERE points > 0 ORDER BY points DESC LIMIT ?',
                    (limit,)
                )
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            print(f"[ShameDatabase] Error in get_leaderboard: {e}")
            return []
    
    async def get_user_rank(self, user_id: int) -> int:
        try:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute('''
                    SELECT COUNT(*) + 1 as rank FROM users 
                    WHERE points > (SELECT COALESCE(points, 0) FROM users WHERE user_id = ?)
                ''', (user_id,))
                row = await cursor.fetchone()
                return row[0] if row else 0
        except Exception as e:
            print(f"[ShameDatabase] Error in get_user_rank: {e}")
            return 0
    
    # Blacklist methods
    async def add_to_blacklist(self, entity_id: int, entity_type: str):
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    'INSERT OR IGNORE INTO blacklist (entity_id, entity_type) VALUES (?, ?)',
                    (entity_id, entity_type)
                )
                await db.commit()
        except Exception as e:
            print(f"[ShameDatabase] Error in add_to_blacklist: {e}")
    
    async def remove_from_blacklist(self, entity_id: int):
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    'DELETE FROM blacklist WHERE entity_id = ?',
                    (entity_id,)
                )
                await db.commit()
        except Exception as e:
            print(f"[ShameDatabase] Error in remove_from_blacklist: {e}")
    
    async def get_blacklist(self) -> List[Dict[str, Any]]:
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute('SELECT * FROM blacklist')
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            print(f"[ShameDatabase] Error in get_blacklist: {e}")
            return []
    
    async def is_blacklisted(self, user_id: int, user_roles: List[int]) -> bool:
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # Check if user is blacklisted
                cursor = await db.execute(
                    'SELECT 1 FROM blacklist WHERE entity_id = ? AND entity_type = "user"',
                    (user_id,)
                )
                if await cursor.fetchone():
                    return True
                
                # Check if any of user's roles are blacklisted
                if user_roles:
                    placeholders = ','.join('?' * len(user_roles))
                    cursor = await db.execute(
                        f'SELECT 1 FROM blacklist WHERE entity_id IN ({placeholders}) AND entity_type = "role"',
                        user_roles
                    )
                    if await cursor.fetchone():
                        return True
                
                return False
        except Exception as e:
            print(f"[ShameDatabase] Error in is_blacklisted: {e}")
            return False
    
    # System state methods
    async def set_system_state(self, key: str, value: str):
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    'INSERT OR REPLACE INTO system_state (key, value) VALUES (?, ?)',
                    (key, value)
                )
                await db.commit()
        except Exception as e:
            print(f"[ShameDatabase] Error in set_system_state: {e}")

    async def hard_reset(self, reset_type: str):
        """Perform hard reset operations"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                if reset_type == "full":
                    await db.execute('DELETE FROM users')
                    await db.execute('DELETE FROM blacklist')
                    await db.execute('DELETE FROM system_state')
                    await db.execute('DELETE FROM leaderboard_history')
                elif reset_type == "points":
                    await db.execute('UPDATE users SET points = 0, hourly_points = 0')
                    await db.execute('DELETE FROM system_state WHERE key IN ("last_leaderboard", "last_leaderboard_sent")')
                else:  # leaderboard
                    await db.execute('DELETE FROM system_state WHERE key IN ("last_leaderboard", "last_leaderboard_sent")')
                
                await db.commit()
                return True
        except Exception as e:
            print(f"[ShameDatabase] Error in hard_reset: {e}")
            return False
    
    async def get_system_state(self, key: str) -> Optional[str]:
        try:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(
                    'SELECT value FROM system_state WHERE key = ?',
                    (key,)
                )
                row = await cursor.fetchone()
                return row[0] if row else None
        except Exception as e:
            print(f"[ShameDatabase] Error in get_system_state: {e}")
            return None