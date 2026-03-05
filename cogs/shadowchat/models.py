"""
SHADOW CHAT - DATA MODELS (FIXED)
Added ON DELETE CASCADE to foreign keys to prevent constraint errors.
"""

import discord
import datetime
from dataclasses import dataclass, field
from typing import Optional, Tuple, Dict, Any, List
import aiosqlite

# ==================== DATACLASSES ====================


@dataclass
class ShadowUser:
    """Represents a user in the Shadow Chat system."""

    user_id: int
    username: str
    discriminator: str
    avatar_url: Optional[str]
    joined_at: datetime.datetime
    is_banned: bool = False
    ban_reason: Optional[str] = None

    @property
    def display_name(self) -> str:
        return f"{self.username}#{self.discriminator}"


@dataclass
class ShadowSession:
    """Active chat session between two users."""

    session_id: str
    user1_id: int
    user2_id: int
    thread1_id: int
    thread2_id: int
    webhook1_url: Optional[str] = None
    webhook2_url: Optional[str] = None
    webhook1_name: Optional[str] = None
    webhook2_name: Optional[str] = None
    created_at: datetime.datetime = field(default_factory=datetime.datetime.utcnow)
    last_activity: datetime.datetime = field(default_factory=datetime.datetime.utcnow)
    ended_at: Optional[datetime.datetime] = None
    is_active: bool = True
    user1_consent: bool = False
    user2_consent: bool = False
    identities_revealed: bool = False
    user1_left: bool = False
    user2_left: bool = False

    def get_other_user(self, user_id: int) -> int:
        """Get the partner's user ID."""
        if user_id == self.user1_id:
            return self.user2_id
        elif user_id == self.user2_id:
            return self.user1_id
        raise ValueError(f"User {user_id} not in session {self.session_id}")

    def get_user_thread(self, user_id: int) -> int:
        """Get thread ID for a specific user."""
        if user_id == self.user1_id:
            return self.thread1_id
        elif user_id == self.user2_id:
            return self.thread2_id
        raise ValueError(f"User {user_id} not in session {self.session_id}")

    def get_partner_thread(self, user_id: int) -> int:
        """Get partner's thread ID."""
        if user_id == self.user1_id:
            return self.thread2_id
        elif user_id == self.user2_id:
            return self.thread1_id
        raise ValueError(f"User {user_id} not in session {self.session_id}")


@dataclass
class QueueEntry:
    """User waiting in the queue."""

    user_id: int
    position: int
    joined_at: datetime.datetime = field(default_factory=datetime.datetime.utcnow)


@dataclass
class MessageMapping:
    """Tracks original -> mirrored message IDs for reply continuity."""

    original_message_id: int
    mirrored_message_id: int
    session_id: str
    direction: str  # 'user1_to_user2' or 'user2_to_user1'
    created_at: datetime.datetime = field(default_factory=datetime.datetime.utcnow)


@dataclass
class SessionHistory:
    """Historical session data for wait time estimation."""

    session_id: str
    user1_id: int
    user2_id: int
    created_at: datetime.datetime
    ended_at: datetime.datetime
    duration_minutes: int


# ==================== DATABASE SCHEMA ====================


class Database:
    """Database handler for Shadow Chat - Fixed Foreign Keys."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn: Optional[aiosqlite.Connection] = None

    async def ensure_user_exists(self, user_id: int):
        """Ensure a user exists in the database, creating if necessary."""
        cursor = await self.conn.execute(
            "SELECT user_id FROM shadow_users WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()

        if not row:
            # Create a minimal user entry
            await self.conn.execute(
                """INSERT INTO shadow_users (user_id, username, discriminator) 
                   VALUES (?, ?, ?)""",
                (user_id, "Unknown", "0000"),
            )
            await self.conn.commit()

    async def get_message_mapping_by_mirrored(
        self, session_id: str, thread_id: int, mirrored_id: int
    ) -> Optional[Tuple]:
        """Find message mapping by the mirrored message ID."""
        cursor = await self.conn.execute(
            """
            SELECT * FROM shadow_message_mappings 
            WHERE session_id = ? AND (from_thread_id = ? OR to_thread_id = ?) AND mirrored_id = ?
        """,
            (session_id, thread_id, thread_id, mirrored_id),
        )
        return await cursor.fetchone()

    async def get_message_mapping_by_original(
        self, session_id: str, thread_id: int, original_message_id: int
    ) -> Optional[Tuple]:
        """Find message mapping by the original message ID."""
        cursor = await self.conn.execute(
            """
            SELECT * FROM shadow_message_mappings 
            WHERE session_id = ? AND (from_thread_id = ? OR to_thread_id = ?) AND original_message_id = ?
        """,
            (session_id, thread_id, thread_id, original_message_id),
        )
        return await cursor.fetchone()

    async def cleanup_message_mappings(self, hours: int = 24):
        """Clean up message mappings older than X hours."""
        try:
            # Calculate cutoff time string for SQLite
            # SQLite uses 'YYYY-MM-DD HH:MM:SS' format
            await self.conn.execute(
                """
                DELETE FROM shadow_message_mappings 
                WHERE created_at < datetime('now', ?)
            """,
                (f"-{hours} hours",),
            )

            await self.conn.execute(
                """
                DELETE FROM message_mappings 
                WHERE created_at < datetime('now', ?)
            """,
                (f"-{hours} hours",),
            )

            await self.conn.commit()
            # print("[Database] Cleaned up old message mappings")
        except Exception as e:
            print(f"[Database] Error cleaning mappings: {e}")

    async def initialize(self):
        """Create tables if they don't exist - With ON DELETE CASCADE."""
        self.conn = await aiosqlite.connect(self.db_path)
        await self.conn.execute("PRAGMA foreign_keys = ON")

        # Users table (bans, stats)
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS shadow_users (
                user_id INTEGER PRIMARY KEY,
                username TEXT NOT NULL,
                discriminator TEXT NOT NULL,
                avatar_url TEXT,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_banned BOOLEAN DEFAULT 0,
                ban_reason TEXT,
                total_sessions INTEGER DEFAULT 0,
                last_active TIMESTAMP
            )
        """)

        # Sessions table
        # Added ON DELETE CASCADE to user references
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS shadow_sessions (
                session_id TEXT PRIMARY KEY,
                user1_id INTEGER NOT NULL,
                user2_id INTEGER NOT NULL,
                thread1_id INTEGER NOT NULL UNIQUE,
                thread2_id INTEGER NOT NULL UNIQUE,
                webhook1_url TEXT,
                webhook2_url TEXT,
                webhook1_name TEXT,
                webhook2_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                ended_at TIMESTAMP,
                is_active BOOLEAN DEFAULT 1,
                user1_consent BOOLEAN DEFAULT 0,
                user2_consent BOOLEAN DEFAULT 0,
                identities_revealed BOOLEAN DEFAULT 0,
                user1_left BOOLEAN DEFAULT 0,
                user2_left BOOLEAN DEFAULT 0,
                FOREIGN KEY (user1_id) REFERENCES shadow_users(user_id) ON DELETE CASCADE,
                FOREIGN KEY (user2_id) REFERENCES shadow_users(user_id) ON DELETE CASCADE
            )
        """)

        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS webhook_message_mappings (
                message_id INTEGER PRIMARY KEY,
                webhook_id INTEGER NOT NULL,
                webhook_url TEXT NOT NULL,
                thread_id INTEGER NOT NULL,
                session_id TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES shadow_sessions(session_id) ON DELETE CASCADE
            )
        """)
        await self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_webhook_messages_message ON webhook_message_mappings(message_id)
        """)
        await self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_webhook_messages_webhook ON webhook_message_mappings(webhook_id)
        """)

        # Queue table
        # Added ON DELETE CASCADE
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS shadow_queue (
                queue_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER UNIQUE NOT NULL,
                position INTEGER NOT NULL,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES shadow_users(user_id) ON DELETE CASCADE
            )
        """)

        # Message mappings table
        # Added ON DELETE CASCADE to session_id - This prevents the crash when cleaning up old sessions
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS message_mappings (
                original_message_id INTEGER PRIMARY KEY,
                mirrored_message_id INTEGER NOT NULL,
                session_id TEXT NOT NULL,
                direction TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES shadow_sessions(session_id) ON DELETE CASCADE
            )
        """)

        # Shadow message mappings table (Alternate version)
        # Added ON DELETE CASCADE to session_id
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS shadow_message_mappings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                from_thread_id INTEGER NOT NULL,
                to_thread_id INTEGER NOT NULL,
                original_message_id INTEGER NOT NULL,
                mirrored_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES shadow_sessions(session_id) ON DELETE CASCADE,
                UNIQUE(session_id, from_thread_id, original_message_id)
            )
        """)

        # Session history
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS session_history (
                session_id TEXT PRIMARY KEY,
                user1_id INTEGER NOT NULL,
                user2_id INTEGER NOT NULL,
                created_at TIMESTAMP NOT NULL,
                ended_at TIMESTAMP NOT NULL,
                duration_minutes INTEGER NOT NULL,
                FOREIGN KEY (user1_id) REFERENCES shadow_users(user_id) ON DELETE CASCADE,
                FOREIGN KEY (user2_id) REFERENCES shadow_users(user_id) ON DELETE CASCADE
            )
        """)

        # Create indexes for performance
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_active ON shadow_sessions(is_active)"
        )
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_user1 ON shadow_sessions(user1_id)"
        )
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_user2 ON shadow_sessions(user2_id)"
        )
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_queue_position ON shadow_queue(position)"
        )
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_queue_joined ON shadow_queue(joined_at)"
        )
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mappings_session ON message_mappings(session_id)"
        )
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_history_duration ON session_history(duration_minutes)"
        )

        await self.conn.commit()
        print("[Shadow Chat] Database initialized (Schema Fixed)")

    async def close(self):
        """Close database connection."""
        if self.conn:
            await self.conn.close()

    # ========== USER METHODS ==========

    async def get_or_create_user(
        self,
        user_id: int,
        username: str,
        discriminator: str,
        avatar_url: Optional[str] = None,
    ) -> ShadowUser:
        """Get existing user or create new one."""
        cursor = await self.conn.execute(
            "SELECT * FROM shadow_users WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()

        if row:
            return ShadowUser(
                user_id=row[0],
                username=row[1],
                discriminator=row[2],
                avatar_url=row[3],
                joined_at=datetime.datetime.fromisoformat(row[4]),
                is_banned=bool(row[5]),
                ban_reason=row[6],
            )
        else:
            await self.conn.execute(
                """INSERT INTO shadow_users 
                   (user_id, username, discriminator, avatar_url) 
                   VALUES (?, ?, ?, ?)""",
                (user_id, username, discriminator, avatar_url),
            )
            await self.conn.commit()
            return ShadowUser(
                user_id=user_id,
                username=username,
                discriminator=discriminator,
                avatar_url=avatar_url,
                joined_at=discord.utils.utcnow(),
            )

    async def ban_user(self, user_id: int, reason: str = "No reason provided"):
        """Ban a user from using Shadow Chat."""
        await self.conn.execute(
            "UPDATE shadow_users SET is_banned = 1, ban_reason = ? WHERE user_id = ?",
            (reason, user_id),
        )
        await self.conn.commit()

    async def unban_user(self, user_id: int):
        """Unban a user."""
        await self.conn.execute(
            "UPDATE shadow_users SET is_banned = 0, ban_reason = NULL WHERE user_id = ?",
            (user_id,),
        )
        await self.conn.commit()

    async def is_user_banned(self, user_id: int) -> bool:
        """Check if user is banned."""
        cursor = await self.conn.execute(
            "SELECT is_banned FROM shadow_users WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        return bool(row[0]) if row else False

    # ========== QUEUE METHODS ==========

    async def add_user_to_queue(
        self,
        user_id: int,
        username: str = None,
        join_time: Optional[datetime.datetime] = None,
    ) -> Optional[int]:
        """Add user to queue, return their position."""
        if join_time is None:
            join_time = discord.utils.utcnow()

        try:
            if username:
                await self.get_or_create_user(user_id, username, "0000", None)

            cursor = await self.conn.execute(
                "SELECT queue_id FROM shadow_queue WHERE user_id = ?", (user_id,)
            )
            if await cursor.fetchone():
                return None

            cursor = await self.conn.execute(
                "SELECT COALESCE(MAX(position), 0) FROM shadow_queue"
            )
            row = await cursor.fetchone()
            new_position = row[0] + 1 if row else 1

            await self.conn.execute(
                """INSERT INTO shadow_queue (user_id, position, joined_at) 
                   VALUES (?, ?, ?)""",
                (user_id, new_position, join_time.isoformat()),
            )
            await self.conn.commit()
            return new_position

        except Exception as e:
            print(f"[Database] Error adding user to queue: {e}")
            return None

    async def _reorder_queue(self):
        """Reorder queue after removal."""
        try:
            await self.conn.execute("""
                UPDATE shadow_queue 
                SET position = row_num
                FROM (
                    SELECT queue_id, ROW_NUMBER() OVER (ORDER BY joined_at) as row_num
                    FROM shadow_queue
                ) AS reordered
                WHERE shadow_queue.queue_id = reordered.queue_id
            """)
            await self.conn.commit()
        except Exception as e:
            print(f"[Database] Error reordering queue: {e}")

    async def get_queue_entries_by_position(
        self, start: int = 1, count: int = 2
    ) -> List[QueueEntry]:
        """Get queue entries by position."""
        cursor = await self.conn.execute(
            """
            SELECT user_id, position, joined_at 
            FROM shadow_queue 
            ORDER BY position 
            LIMIT ? OFFSET ?
        """,
            (count, start - 1),
        )

        rows = await cursor.fetchall()
        return [
            QueueEntry(
                user_id=row[0],
                position=row[1],
                joined_at=datetime.datetime.fromisoformat(row[2]),
            )
            for row in rows
        ]

    async def remove_queue_users(self, user_ids: List[int]) -> int:
        """Remove multiple users from queue."""
        if not user_ids:
            return 0

        placeholders = ",".join(["?"] * len(user_ids))
        cursor = await self.conn.execute(
            f"DELETE FROM shadow_queue WHERE user_id IN ({placeholders})", user_ids
        )
        await self.conn.commit()

        # Reorder the queue
        if cursor.rowcount > 0:
            await self._reorder_queue()

        return cursor.rowcount

    async def remove_from_queue(self, user_id: int) -> bool:
        """Remove user from queue."""
        cursor = await self.conn.execute(
            "DELETE FROM shadow_queue WHERE user_id = ?", (user_id,)
        )
        await self.conn.commit()

        if cursor.rowcount > 0:
            await self._reorder_queue()
            return True
        return False

    async def get_queue_position(self, user_id: int) -> Optional[int]:
        """Get user's queue position."""
        cursor = await self.conn.execute(
            "SELECT position FROM shadow_queue WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    async def get_queue_users(self) -> List[QueueEntry]:
        """Get all users in queue."""
        cursor = await self.conn.execute(
            "SELECT user_id, position, joined_at FROM shadow_queue ORDER BY position"
        )
        rows = await cursor.fetchall()
        return [
            QueueEntry(
                user_id=row[0],
                position=row[1],
                joined_at=datetime.datetime.fromisoformat(row[2]),
            )
            for row in rows
        ]

    async def get_queue_size(self) -> int:
        """Get current queue size."""
        cursor = await self.conn.execute("SELECT COUNT(*) FROM shadow_queue")
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def get_average_session_duration(self, limit: int = 20) -> float:
        """Get average session duration from history."""
        cursor = await self.conn.execute(
            """
            SELECT AVG(duration_minutes) FROM (
                SELECT duration_minutes FROM session_history 
                ORDER BY ended_at DESC LIMIT ?
            )
        """,
            (limit,),
        )
        row = await cursor.fetchone()
        return (
            float(row[0]) if row and row[0] is not None else 15.0
        )  # Default 15 minutes

    async def add_session_to_history(self, session: ShadowSession):
        """Add completed session to history for wait time estimation."""
        if not session.ended_at:
            return

        duration = (session.ended_at - session.created_at).seconds // 60

        await self.conn.execute(
            """
            INSERT OR REPLACE INTO session_history 
            (session_id, user1_id, user2_id, created_at, ended_at, duration_minutes)
            VALUES (?, ?, ?, ?, ?, ?)
        """,
            (
                session.session_id,
                session.user1_id,
                session.user2_id,
                session.created_at.isoformat(),
                session.ended_at.isoformat(),
                duration,
            ),
        )
        await self.conn.commit()

    # ========== SESSION METHODS ==========

    async def create_session(self, session: ShadowSession):
        """Store a new session."""
        await self.conn.execute(
            """
            INSERT INTO shadow_sessions 
            (session_id, user1_id, user2_id, thread1_id, thread2_id, 
             webhook1_url, webhook2_url, webhook1_name, webhook2_name,
             created_at, last_activity, 
             is_active, user1_consent, user2_consent, identities_revealed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                session.session_id,
                session.user1_id,
                session.user2_id,
                session.thread1_id,
                session.thread2_id,
                session.webhook1_url,
                session.webhook2_url,
                session.webhook1_name,
                session.webhook2_name,
                session.created_at.isoformat(),
                session.last_activity.isoformat(),
                session.is_active,
                session.user1_consent,
                session.user2_consent,
                session.identities_revealed,
            ),
        )

        # Increment session counters for users
        await self.conn.execute(
            "UPDATE shadow_users SET total_sessions = total_sessions + 1 WHERE user_id IN (?, ?)",
            (session.user1_id, session.user2_id),
        )

        await self.conn.commit()

    async def get_session(self, session_id: str) -> Optional[ShadowSession]:
        """Retrieve a session by ID."""
        cursor = await self.conn.execute(
            "SELECT * FROM shadow_sessions WHERE session_id = ?", (session_id,)
        )
        row = await cursor.fetchone()

        if not row:
            return None

        ended_at = datetime.datetime.fromisoformat(row[11]) if row[11] else None

        return ShadowSession(
            session_id=row[0],
            user1_id=row[1],
            user2_id=row[2],
            thread1_id=row[3],
            thread2_id=row[4],
            webhook1_url=row[5],
            webhook2_url=row[6],
            webhook1_name=row[7],
            webhook2_name=row[8],
            created_at=datetime.datetime.fromisoformat(row[9]),
            last_activity=datetime.datetime.fromisoformat(row[10]),
            ended_at=ended_at,
            is_active=bool(row[12]),
            user1_consent=bool(row[13]),
            user2_consent=bool(row[14]),
            identities_revealed=bool(row[15]),
        )

    async def get_session_by_thread(self, thread_id: int) -> Optional[ShadowSession]:
        """Find session containing a specific thread."""
        cursor = await self.conn.execute(
            """
            SELECT * FROM shadow_sessions 
            WHERE (thread1_id = ? OR thread2_id = ?) AND is_active = 1
        """,
            (thread_id, thread_id),
        )
        row = await cursor.fetchone()

        if not row:
            return None

        ended_at = datetime.datetime.fromisoformat(row[11]) if row[11] else None

        return ShadowSession(
            session_id=row[0],
            user1_id=row[1],
            user2_id=row[2],
            thread1_id=row[3],
            thread2_id=row[4],
            webhook1_url=row[5],
            webhook2_url=row[6],
            webhook1_name=row[7],
            webhook2_name=row[8],
            created_at=datetime.datetime.fromisoformat(row[9]),
            last_activity=datetime.datetime.fromisoformat(row[10]),
            ended_at=ended_at,
            is_active=bool(row[12]),
            user1_consent=bool(row[13]),
            user2_consent=bool(row[14]),
            identities_revealed=bool(row[15]),
        )

    async def get_session_by_user(self, user_id: int) -> Optional[ShadowSession]:
        """Find active session for a user."""
        cursor = await self.conn.execute(
            """
            SELECT * FROM shadow_sessions 
            WHERE (user1_id = ? OR user2_id = ?) AND is_active = 1
            LIMIT 1
        """,
            (user_id, user_id),
        )
        row = await cursor.fetchone()

        if not row:
            return None

        ended_at = datetime.datetime.fromisoformat(row[11]) if row[11] else None

        return ShadowSession(
            session_id=row[0],
            user1_id=row[1],
            user2_id=row[2],
            thread1_id=row[3],
            thread2_id=row[4],
            webhook1_url=row[5],
            webhook2_url=row[6],
            webhook1_name=row[7],
            webhook2_name=row[8],
            created_at=datetime.datetime.fromisoformat(row[9]),
            last_activity=datetime.datetime.fromisoformat(row[10]),
            ended_at=ended_at,
            is_active=bool(row[12]),
            user1_consent=bool(row[13]),
            user2_consent=bool(row[14]),
            identities_revealed=bool(row[15]),
        )

    async def update_session_activity(self, session_id: str):
        """Update last_activity timestamp for a session."""
        await self.conn.execute(
            """
            UPDATE shadow_sessions 
            SET last_activity = ? 
            WHERE session_id = ?
        """,
            (discord.utils.utcnow().isoformat(), session_id),
        )
        await self.conn.commit()

    async def update_reveal_consent(self, session_id: str, user_id: int, consent: bool):
        """Update reveal consent for a user."""
        session = await self.get_session(session_id)
        if not session:
            return

        field = "user1_consent" if user_id == session.user1_id else "user2_consent"
        await self.conn.execute(
            f"""
            UPDATE shadow_sessions 
            SET {field} = ?, last_activity = ?
            WHERE session_id = ?
        """,
            (consent, discord.utils.utcnow().isoformat(), session_id),
        )
        await self.conn.commit()

    async def set_identities_revealed(self, session_id: str):
        """Mark session as having revealed identities."""
        await self.conn.execute(
            """
            UPDATE shadow_sessions 
            SET identities_revealed = 1, last_activity = ?
            WHERE session_id = ?
        """,
            (discord.utils.utcnow().isoformat(), session_id),
        )
        await self.conn.commit()

    async def end_session(
        self, session_id: str, ended_at: Optional[datetime.datetime] = None
    ):
        """Mark session as inactive and record end time."""
        if ended_at is None:
            ended_at = discord.utils.utcnow()

        await self.conn.execute(
            """
            UPDATE shadow_sessions 
            SET is_active = 0, ended_at = ?, last_activity = ?
            WHERE session_id = ?
        """,
            (ended_at.isoformat(), ended_at.isoformat(), session_id),
        )
        await self.conn.commit()

        # Add to history
        session = await self.get_session(session_id)
        if session:
            session.ended_at = ended_at
            await self.add_session_to_history(session)

    async def get_active_sessions(self) -> List[ShadowSession]:
        """Get all active sessions."""
        cursor = await self.conn.execute(
            "SELECT * FROM shadow_sessions WHERE is_active = 1 ORDER BY created_at"
        )
        rows = await cursor.fetchall()

        sessions = []
        for row in rows:
            ended_at = datetime.datetime.fromisoformat(row[11]) if row[11] else None

            sessions.append(
                ShadowSession(
                    session_id=row[0],
                    user1_id=row[1],
                    user2_id=row[2],
                    thread1_id=row[3],
                    thread2_id=row[4],
                    webhook1_url=row[5],
                    webhook2_url=row[6],
                    webhook1_name=row[7],
                    webhook2_name=row[8],
                    created_at=datetime.datetime.fromisoformat(row[9]),
                    last_activity=datetime.datetime.fromisoformat(row[10]),
                    ended_at=ended_at,
                    is_active=bool(row[12]),
                    user1_consent=bool(row[13]),
                    user2_consent=bool(row[14]),
                    identities_revealed=bool(row[15]),
                )
            )
        return sessions

    async def get_active_session_count(self) -> int:
        """Count active sessions."""
        cursor = await self.conn.execute(
            "SELECT COUNT(*) FROM shadow_sessions WHERE is_active = 1"
        )
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def cleanup_old_sessions(self, cutoff_minutes: int = 120):
        """Clean up sessions older than X minutes."""
        cutoff = discord.utils.utcnow() - datetime.timedelta(minutes=cutoff_minutes)
        await self.conn.execute(
            "DELETE FROM shadow_sessions WHERE last_activity < ? AND is_active = 0",
            (cutoff.isoformat(),),
        )
        await self.conn.commit()

    # ========== MESSAGE MAPPING METHODS ==========

    async def store_webhook_message(
        self,
        message_id: int,
        webhook_id: int,
        webhook_url: str,
        thread_id: int,
        session_id: str,
    ):
        """Store which webhook sent which message."""
        await self.conn.execute(
            """
            INSERT OR REPLACE INTO webhook_message_mappings 
            (message_id, webhook_id, webhook_url, thread_id, session_id)
            VALUES (?, ?, ?, ?, ?)
        """,
            (message_id, webhook_id, webhook_url, thread_id, session_id),
        )
        await self.conn.commit()

    async def get_webhook_for_message(self, message_id: int) -> Optional[dict]:
        """Get webhook info for a message."""
        cursor = await self.conn.execute(
            """
            SELECT webhook_id, webhook_url, thread_id, session_id 
            FROM webhook_message_mappings 
            WHERE message_id = ?
        """,
            (message_id,),
        )
        row = await cursor.fetchone()
        if row:
            return {
                "webhook_id": row[0],
                "webhook_url": row[1],
                "thread_id": row[2],
                "session_id": row[3],
            }
        return None

    async def delete_session_webhook_mappings(self, session_id: str):
        """Delete all webhook message mappings for a session."""
        await self.conn.execute(
            "DELETE FROM webhook_message_mappings WHERE session_id = ?", (session_id,)
        )
        await self.conn.commit()

    async def add_message_mapping(self, mapping: MessageMapping):
        """Store a message mapping."""
        await self.conn.execute(
            """
            INSERT OR REPLACE INTO message_mappings 
            (original_message_id, mirrored_message_id, session_id, direction, created_at)
            VALUES (?, ?, ?, ?, ?)
        """,
            (
                mapping.original_message_id,
                mapping.mirrored_message_id,
                mapping.session_id,
                mapping.direction,
                mapping.created_at.isoformat(),
            ),
        )
        await self.conn.commit()

    async def get_mirrored_message_id(self, original_message_id: int) -> Optional[int]:
        """Get mirrored message ID for an original."""
        cursor = await self.conn.execute(
            "SELECT mirrored_message_id FROM message_mappings WHERE original_message_id = ?",
            (original_message_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    async def get_original_message_id(self, mirrored_message_id: int) -> Optional[int]:
        """Get original message ID for a mirrored message."""
        cursor = await self.conn.execute(
            "SELECT original_message_id FROM message_mappings WHERE mirrored_message_id = ?",
            (mirrored_message_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    async def delete_session_mappings(self, session_id: str):
        """Delete all message mappings for a session."""
        # Delete regular message mappings
        await self.conn.execute(
            "DELETE FROM message_mappings WHERE session_id = ?", (session_id,)
        )
        # Delete shadow message mappings
        await self.conn.execute(
            "DELETE FROM shadow_message_mappings WHERE session_id = ?", (session_id,)
        )
        # Delete webhook message mappings
        await self.conn.execute(
            "DELETE FROM webhook_message_mappings WHERE session_id = ?", (session_id,)
        )
        await self.conn.commit()

    async def get_message_mapping(
        self, session_id: str, from_thread_id: int, original_message_id: int
    ):
        """Get message mapping for replying."""
        cursor = await self.conn.execute(
            """
            SELECT * FROM shadow_message_mappings 
            WHERE session_id = ? AND from_thread_id = ? AND original_message_id = ?
        """,
            (session_id, from_thread_id, original_message_id),
        )
        row = await cursor.fetchone()
        return row

    async def store_message_mapping(
        self,
        session_id: str,
        from_thread_id: int,
        to_thread_id: int,
        original_message_id: int,
        mirrored_id: int,
    ):
        """Store message mapping for replying."""
        await self.conn.execute(
            """
            INSERT INTO shadow_message_mappings 
            (session_id, from_thread_id, to_thread_id, original_message_id, mirrored_id, created_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
        """,
            (
                session_id,
                from_thread_id,
                to_thread_id,
                original_message_id,
                mirrored_id,
            ),
        )
        await self.conn.commit()

    async def mark_user_left_session(self, session_id: str, user_id: int):
        """Mark a user as having left the session."""
        session = await self.get_session(session_id)
        if not session:
            return

        if user_id == session.user1_id:
            await self.conn.execute(
                """
                UPDATE shadow_sessions 
                SET user1_left = 1, last_activity = ?
                WHERE session_id = ?
            """,
                (discord.utils.utcnow().isoformat(), session_id),
            )
        elif user_id == session.user2_id:
            await self.conn.execute(
                """
                UPDATE shadow_sessions 
                SET user2_left = 1, last_activity = ?
                WHERE session_id = ?
            """,
                (discord.utils.utcnow().isoformat(), session_id),
            )

        await self.conn.commit()

    async def has_both_users_left(self, session_id: str) -> bool:
        """Check if both users have left the session."""
        cursor = await self.conn.execute(
            "SELECT user1_left, user2_left FROM shadow_sessions WHERE session_id = ?",
            (session_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return True

    async def cleanup_inactive_users(self, days: int = 30):
        """Clean up users who haven't been active for X days."""
        cutoff = discord.utils.utcnow() - datetime.timedelta(days=days)

        # First, delete users who have no sessions, no queue entries, and are old
        await self.conn.execute(
            """
            DELETE FROM shadow_users 
            WHERE user_id NOT IN (
                SELECT DISTINCT user1_id FROM shadow_sessions 
                UNION 
                SELECT DISTINCT user2_id FROM shadow_sessions
                UNION
                SELECT DISTINCT user_id FROM shadow_queue
            )
            AND last_active < ?
        """,
            (cutoff.isoformat(),),
        )
        await self.conn.commit()

    async def cleanup_old_session_history(self, days: int = 90):
        """Clean up old session history entries."""
        cutoff = discord.utils.utcnow() - datetime.timedelta(days=days)
        await self.conn.execute(
            "DELETE FROM session_history WHERE ended_at < ?", (cutoff.isoformat(),)
        )
        await self.conn.commit()

    async def cleanup_abandoned_sessions(self, hours: int = 6):
        """Clean up sessions where both users have left but session is still active."""
        await self.conn.execute(
            """
            DELETE FROM shadow_sessions 
            WHERE is_active = 1 
            AND user1_left = 1 
            AND user2_left = 1
            AND last_activity < datetime('now', ?)
        """,
            (f"-{hours} hours",),
        )
        await self.conn.commit()
