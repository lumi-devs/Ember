import aiosqlite
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
import json

DB_PATH = Path("data/booster_roles.db")
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

class Database:
    """SQLite database handler for booster roles"""
    
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._connection = None
    
    async def get_connection(self) -> aiosqlite.Connection:
        """Get or create database connection"""
        if self._connection is None:
            self._connection = await aiosqlite.connect(self.db_path)
            self._connection.row_factory = aiosqlite.Row  # Return dict-like rows
        return self._connection
    
    async def close(self):
        """Close database connection"""
        if self._connection:
            await self._connection.close()
            self._connection = None
    
    async def init_db(self):
        """Initialize database tables"""
        conn = await self.get_connection()
        
        # Roles table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS roles (
                user_id INTEGER PRIMARY KEY,
                role_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                color TEXT NOT NULL,
                color_type TEXT DEFAULT 'solid',
                secondary_colour TEXT,
                icon_type TEXT,
                icon_set_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        
        # Bestowed table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS bestowed (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                role_id INTEGER NOT NULL,
                invited_at TEXT NOT NULL,
                accepted BOOLEAN DEFAULT 0,
                accepted_at TEXT,
                UNIQUE(owner_id, user_id)
            )
        """)
        
        # Blacklist table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS blacklist (
                user_id INTEGER PRIMARY KEY
            )
        """)
        
        # Indexes for performance
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_roles_role_id ON roles(role_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_bestowed_owner_id ON bestowed(owner_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_bestowed_user_id ON bestowed(user_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_bestowed_accepted ON bestowed(accepted)")
        
        await conn.commit()
    
    # Role operations
    async def save_role(self, user_id: int, role_data: Dict[str, Any]) -> bool:
        """Save or update a role"""
        conn = await self.get_connection()
        
        # Prepare data
        data = {
            'user_id': user_id,
            'role_id': role_data.get('role_id'),
            'name': role_data.get('name', ''),
            'color': role_data.get('color', '#000000'),
            'color_type': role_data.get('color_type', 'solid'),
            'secondary_colour': role_data.get('secondary_colour'),
            'icon_type': role_data.get('icon_type'),
            'icon_set_at': role_data.get('icon_set_at'),
            'created_at': role_data.get('created_at', datetime.now(timezone(timedelta(hours=5, minutes=30))).isoformat()),
            'updated_at': datetime.now(timezone(timedelta(hours=5, minutes=30))).isoformat()
        }
        
        try:
            await conn.execute("""
                INSERT OR REPLACE INTO roles 
                (user_id, role_id, name, color, color_type, secondary_colour, icon_type, icon_set_at, created_at, updated_at)
                VALUES (:user_id, :role_id, :name, :color, :color_type, :secondary_colour, :icon_type, :icon_set_at, :created_at, :updated_at)
            """, data)
            await conn.commit()
            return True
        except Exception as e:
            print(f"[ERROR] Failed to save role: {e}")
            return False
    
    async def get_role(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get a role by user ID"""
        conn = await self.get_connection()
        
        async with conn.execute("SELECT * FROM roles WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)
        return None
    
    async def get_role_by_role_id(self, role_id: int) -> Optional[Dict[str, Any]]:
        """Get a role by role ID"""
        conn = await self.get_connection()
        
        async with conn.execute("SELECT * FROM roles WHERE role_id = ?", (role_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)
        return None
    
    async def delete_role(self, user_id: int) -> bool:
        """Delete a role and related bestowed entries"""
        conn = await self.get_connection()
        
        try:
            # Start transaction
            await conn.execute("BEGIN TRANSACTION")
            
            # Delete role
            await conn.execute("DELETE FROM roles WHERE user_id = ?", (user_id,))
            
            # Delete bestowed entries where this user is the owner
            await conn.execute("DELETE FROM bestowed WHERE owner_id = ?", (user_id,))
            
            # Also delete bestowed entries where this user is the recipient
            await conn.execute("DELETE FROM bestowed WHERE user_id = ?", (user_id,))
            
            await conn.commit()
            return True
        except Exception as e:
            await conn.rollback()
            print(f"[ERROR] Failed to delete role: {e}")
            return False
    
    async def get_all_roles(self) -> List[Dict[str, Any]]:
        """Get all roles"""
        conn = await self.get_connection()
        
        async with conn.execute("SELECT * FROM roles") as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    
    # Bestowed operations
    async def add_bestowed(self, owner_id: int, user_id: int, role_id: int) -> bool:
        """Add a bestowed entry"""
        conn = await self.get_connection()
        
        try:
            await conn.execute("""
                INSERT OR REPLACE INTO bestowed (owner_id, user_id, role_id, invited_at, accepted)
                VALUES (?, ?, ?, ?, ?)
            """, (
                owner_id,
                user_id,
                role_id,
                datetime.now(timezone(timedelta(hours=5, minutes=30))).isoformat(),
                False
            ))
            await conn.commit()
            return True
        except Exception as e:
            print(f"[ERROR] Failed to add bestowed: {e}")
            return False
    
    async def update_bestowed(self, owner_id: int, user_id: int, accepted: bool = True) -> bool:
        """Update bestowed acceptance status"""
        conn = await self.get_connection()
        
        try:
            data = {
                'accepted': accepted,
                'accepted_at': datetime.now(timezone(timedelta(hours=5, minutes=30))).isoformat() if accepted else None,
                'owner_id': owner_id,
                'user_id': user_id
            }
            
            await conn.execute("""
                UPDATE bestowed 
                SET accepted = :accepted, accepted_at = :accepted_at
                WHERE owner_id = :owner_id AND user_id = :user_id
            """, data)
            await conn.commit()
            return True
        except Exception as e:
            print(f"[ERROR] Failed to update bestowed: {e}")
            return False
    
    async def remove_bestowed(self, owner_id: int, user_id: int) -> bool:
        """Remove a bestowed entry"""
        conn = await self.get_connection()
        
        try:
            await conn.execute("DELETE FROM bestowed WHERE owner_id = ? AND user_id = ?", (owner_id, user_id))
            await conn.commit()
            return True
        except Exception as e:
            print(f"[ERROR] Failed to remove bestowed: {e}")
            return False
    
    async def get_bestowed_by_owner(self, owner_id: int) -> List[Dict[str, Any]]:
        """Get all bestowed entries for an owner"""
        conn = await self.get_connection()
        
        async with conn.execute("SELECT * FROM bestowed WHERE owner_id = ?", (owner_id,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    
    async def get_bestowed_by_user(self, user_id: int) -> List[Dict[str, Any]]:
        """Get all bestowed entries for a user"""
        conn = await self.get_connection()
        
        async with conn.execute("SELECT * FROM bestowed WHERE user_id = ?", (user_id,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    
    async def get_bestowed(self, owner_id: int, user_id: int) -> Optional[Dict[str, Any]]:
        """Get a specific bestowed entry"""
        conn = await self.get_connection()
        
        async with conn.execute("SELECT * FROM bestowed WHERE owner_id = ? AND user_id = ?", (owner_id, user_id)) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)
        return None
    
    # Blacklist operations
    async def add_to_blacklist(self, user_id: int) -> bool:
        """Add user to blacklist"""
        conn = await self.get_connection()
        
        try:
            await conn.execute("INSERT OR IGNORE INTO blacklist (user_id) VALUES (?)", (user_id,))
            await conn.commit()
            return True
        except Exception as e:
            print(f"[ERROR] Failed to add to blacklist: {e}")
            return False
    
    async def remove_from_blacklist(self, user_id: int) -> bool:
        """Remove user from blacklist"""
        conn = await self.get_connection()
        
        try:
            await conn.execute("DELETE FROM blacklist WHERE user_id = ?", (user_id,))
            await conn.commit()
            return True
        except Exception as e:
            print(f"[ERROR] Failed to remove from blacklist: {e}")
            return False
    
    async def is_user_banned(self, user_id: int) -> bool:
        """Check if user is banned"""
        conn = await self.get_connection()
        
        async with conn.execute("SELECT 1 FROM blacklist WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row is not None
    
    async def get_blacklist(self) -> List[int]:
        """Get all blacklisted users"""
        conn = await self.get_connection()
        
        async with conn.execute("SELECT user_id FROM blacklist") as cursor:
            rows = await cursor.fetchall()
            return [row['user_id'] for row in rows]
    
    # Statistics
    async def get_stats(self) -> Dict[str, Any]:
        """Get database statistics"""
        conn = await self.get_connection()
        
        stats = {}
        
        # Role count
        async with conn.execute("SELECT COUNT(*) as count FROM roles") as cursor:
            row = await cursor.fetchone()
            stats['total_roles'] = row['count'] if row else 0
        
        # Bestowed count
        async with conn.execute("SELECT COUNT(*) as count FROM bestowed") as cursor:
            row = await cursor.fetchone()
            stats['total_bestowed'] = row['count'] if row else 0
        
        # Accepted bestowed count
        async with conn.execute("SELECT COUNT(*) as count FROM bestowed WHERE accepted = 1") as cursor:
            row = await cursor.fetchone()
            stats['accepted_bestowed'] = row['count'] if row else 0
        
        # Blacklist count
        async with conn.execute("SELECT COUNT(*) as count FROM blacklist") as cursor:
            row = await cursor.fetchone()
            stats['blacklisted_users'] = row['count'] if row else 0
        
        return stats
    
    # Migration from JSON
    async def migrate_from_json(self, json_path: Path) -> bool:
        """Migrate data from JSON file to SQLite"""
        try:
            if not json_path.exists():
                print(f"[INFO] No JSON file found at {json_path}, skipping migration")
                return True
            
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            conn = await self.get_connection()
            
            # Migrate roles
            roles = data.get('roles', {})
            for user_id_str, role_data in roles.items():
                user_id = int(user_id_str)
                await self.save_role(user_id, role_data)
            
            # Migrate bestowed
            bestowed = data.get('bestowed', {})
            for owner_id_str, users in bestowed.items():
                owner_id = int(owner_id_str)
                for user_id_str, entry in users.items():
                    user_id = int(user_id_str)
                    await self.add_bestowed(owner_id, user_id, entry.get('role_id'))
                    if entry.get('accepted'):
                        await self.update_bestowed(owner_id, user_id, True)
            
            # Migrate blacklist
            blacklist = data.get('blacklist', [])
            for user_id_str in blacklist:
                user_id = int(user_id_str)
                await self.add_to_blacklist(user_id)
            
            print(f"[INFO] Successfully migrated data from {json_path}")
            return True
            
        except Exception as e:
            print(f"[ERROR] Failed to migrate from JSON: {e}")
            return False
    
    # Backup functionality
    async def create_backup(self, backup_path: Path = None) -> bool:
        """Create a backup of the database"""
        if backup_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = DB_PATH.parent / f"backups/booster_roles_{timestamp}.db"
        
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            conn = await self.get_connection()
            await conn.execute(f"VACUUM INTO '{backup_path}'")
            
            # Keep only last 7 backups
            backup_dir = backup_path.parent
            backup_files = sorted(backup_dir.glob("booster_roles_*.db"), key=lambda x: x.stat().st_mtime)
            for old_backup in backup_files[:-7]:
                old_backup.unlink()
            
            return True
        except Exception as e:
            print(f"[ERROR] Failed to create backup: {e}")
            return False

# Singleton instance
_db_instance = None

async def get_db() -> Database:
    """Get database instance"""
    global _db_instance
    if _db_instance is None:
        _db_instance = Database()
        await _db_instance.init_db()
    return _db_instance

# Convenience functions for backward compatibility
async def load_db() -> Dict[str, Any]:
    """Load all data (for backward compatibility)"""
    db = await get_db()
    
    # Get all roles
    roles = await db.get_all_roles()
    roles_dict = {str(role['user_id']): dict(role) for role in roles}
    
    # Get all bestowed
    all_bestowed = {}
    conn = await db.get_connection()
    async with conn.execute("SELECT * FROM bestowed") as cursor:
        rows = await cursor.fetchall()
        for row in rows:
            row_dict = dict(row)
            owner_id = str(row_dict['owner_id'])
            user_id = str(row_dict['user_id'])
            
            if owner_id not in all_bestowed:
                all_bestowed[owner_id] = {}
            
            all_bestowed[owner_id][user_id] = {
                'role_id': row_dict['role_id'],
                'invited_at': row_dict['invited_at'],
                'accepted': bool(row_dict['accepted']),
                'accepted_at': row_dict['accepted_at']
            }
    
    # Get blacklist
    blacklist = await db.get_blacklist()
    blacklist_str = [str(user_id) for user_id in blacklist]
    
    return {
        'roles': roles_dict,
        'bestowed': all_bestowed,
        'blacklist': blacklist_str
    }

async def save_db(data: Dict[str, Any]) -> bool:
    """Save all data (for backward compatibility)"""
    # This function doesn't make sense for SQLite since we have individual operations
    # But we'll implement it for compatibility
    print("[WARN] save_db() is deprecated for SQLite. Use individual database methods instead.")
    return True

# Synchronous wrappers (for backward compatibility)
def load_db_sync() -> Dict[str, Any]:
    """Synchronous wrapper for load_db"""
    import asyncio
    return asyncio.run(load_db())

def save_db_sync(data: Dict[str, Any]) -> bool:
    """Synchronous wrapper for save_db"""
    import asyncio
    return asyncio.run(save_db(data))

# Export common functions for backward compatibility
async def update_role(user_id: str, role_data: Dict[str, Any]) -> bool:
    """Update a role (for backward compatibility)"""
    db = await get_db()
    return await db.save_role(int(user_id), role_data)

async def get_role(user_id: str) -> Dict[str, Any]:
    """Get a role (for backward compatibility)"""
    db = await get_db()
    return await db.get_role(int(user_id))

async def delete_role(user_id: str) -> bool:
    """Delete a role (for backward compatibility)"""
    db = await get_db()
    return await db.delete_role(int(user_id))

async def add_bestowed(owner_id: str, user_id: str, role_id: int) -> bool:
    """Add bestowed (for backward compatibility)"""
    db = await get_db()
    return await db.add_bestowed(int(owner_id), int(user_id), role_id)

async def update_bestowed(owner_id: str, user_id: str, accepted: bool = True) -> bool:
    """Update bestowed (for backward compatibility)"""
    db = await get_db()
    return await db.update_bestowed(int(owner_id), int(user_id), accepted)

async def remove_bestowed(owner_id: str, user_id: str) -> bool:
    """Remove bestowed (for backward compatibility)"""
    db = await get_db()
    return await db.remove_bestowed(int(owner_id), int(user_id))

async def is_user_banned(user_id: str) -> bool:
    """Check if user is banned (for backward compatibility)"""
    db = await get_db()
    return await db.is_user_banned(int(user_id))

async def get_user_bestowed_roles(self, user_id: int) -> List[Dict[str, Any]]:
    """Get all bestowed roles a user currently has"""
    conn = await self.get_connection()
    
    async with conn.execute("""
        SELECT r.*, b.owner_id, b.accepted, b.accepted_at
        FROM roles r
        JOIN bestowed b ON r.role_id = b.role_id
        WHERE b.user_id = ? AND b.accepted = 1
    """, (user_id,)) as cursor:
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]