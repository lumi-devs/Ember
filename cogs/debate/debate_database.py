import sqlite3
import aiosqlite
from datetime import datetime, timedelta
from .debate_config import DB_PATH


class DebateDatabase:
    def __init__(self):
        self.db_path = DB_PATH

    async def init_db(self):
        """Initialize database tables"""
        async with aiosqlite.connect(self.db_path) as db:
            # Debates table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS debates (
                    thread_id TEXT PRIMARY KEY,
                    topic TEXT NOT NULL,
                    proposer_id TEXT NOT NULL,
                    approver_id TEXT NOT NULL,
                    tags TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    closed_at TIMESTAMP,
                    last_activity TIMESTAMP,
                    message_count INTEGER DEFAULT 0,
                    participant_count INTEGER DEFAULT 0
                )
            """)

            # Participants table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS participants (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    messages_sent INTEGER DEFAULT 0,
                    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_active TIMESTAMP,
                    UNIQUE(user_id, thread_id)
                )
            """)

            # Weekly leaderboard - NOW INCLUDES last_updated
            await db.execute("""
                CREATE TABLE IF NOT EXISTS weekly_scores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    week_start DATE NOT NULL,
                    score INTEGER DEFAULT 0,
                    debates_created INTEGER DEFAULT 0,
                    messages_sent INTEGER DEFAULT 0,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, week_start)
                )
            """)

            # Reactions tracking
            await db.execute("""
                CREATE TABLE IF NOT EXISTS reactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    reactor_id TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Muted users table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS muted_users (
                    user_id TEXT PRIMARY KEY,
                    expiry TIMESTAMP,
                    reason TEXT,
                    mod_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Pending submissions table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS pending_submissions (
                    user_id TEXT PRIMARY KEY,
                    topic TEXT NOT NULL,
                    opening TEXT NOT NULL,
                    why TEXT,
                    mod_message_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # NEW: Config table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS config (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)

            # ALSO: Add last_updated to existing weekly_scores if missing
            try:
                await db.execute(
                    "ALTER TABLE weekly_scores ADD COLUMN last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
                )
            except:
                pass  # Column already exists

            await db.commit()

    async def get_config(self, key: str) -> str | None:
        """Retrieve a config value by key."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT value FROM config WHERE key = ?", (key,))
            row = await cursor.fetchone()
            return row[0] if row else None

    async def set_config(self, key: str, value: str) -> None:
        """Set a config key-value pair."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, value)
            )
            await db.commit()

    # Helper method to execute queries without connection reuse issues
    async def execute(self, query, *params):
        """Execute a query and return cursor"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(query, params)
            await db.commit()
            return cursor

    async def fetchone(self, query, *params):
        """Fetch one row"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(query, params)
            return await cursor.fetchone()

    async def fetchall(self, query, *params):
        """Fetch all rows"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(query, params)
            return await cursor.fetchall()

    # Debate operations
    async def create_debate(self, thread_id, topic, proposer_id, approver_id, tags):
        await self.execute(
            """
            INSERT INTO debates (thread_id, topic, proposer_id, approver_id, tags, last_activity)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
            thread_id,
            topic,
            proposer_id,
            approver_id,
            tags,
        )

    async def update_debate_activity(self, thread_id):
        await self.execute(
            """
            UPDATE debates 
            SET last_activity = CURRENT_TIMESTAMP,
                message_count = message_count + 1
            WHERE thread_id = ?
        """,
            thread_id,
        )

    async def close_debate(self, thread_id):
        await self.execute(
            """
            UPDATE debates 
            SET closed_at = CURRENT_TIMESTAMP
            WHERE thread_id = ?
        """,
            thread_id,
        )

    # Get debate duration
    async def get_debate_duration(self, thread_id):
        """Get debate duration in days"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT julianday('now') - julianday(created_at) 
                FROM debates WHERE thread_id = ?
            """,
                (thread_id,),
            )
            result = await cursor.fetchone()
            return result[0] if result else 0

    # Participant operations
    async def add_participant(self, user_id, thread_id):
        await self.execute(
            """
            INSERT OR IGNORE INTO participants (user_id, thread_id, last_active)
            VALUES (?, ?, CURRENT_TIMESTAMP)
        """,
            user_id,
            thread_id,
        )

    async def update_participant_activity(self, user_id, thread_id):
        await self.execute(
            """
            UPDATE participants 
            SET messages_sent = messages_sent + 1,
                last_active = CURRENT_TIMESTAMP
            WHERE user_id = ? AND thread_id = ?
        """,
            user_id,
            thread_id,
        )

    # Mute operations
    async def add_mute(self, user_id, expiry, reason, mod_id):
        """Add a muted user to database"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO muted_users (user_id, expiry, reason, mod_id)
                VALUES (?, ?, ?, ?)
            """,
                (user_id, expiry, reason, mod_id),
            )
            await db.commit()

    async def remove_mute(self, user_id):
        """Remove a muted user"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM muted_users WHERE user_id = ?", (user_id,))
            await db.commit()

    async def get_all_mutes(self):
        """Get all active mutes"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                SELECT user_id, expiry, reason, mod_id, created_at
                FROM muted_users 
                WHERE expiry IS NULL OR expiry > CURRENT_TIMESTAMP
            """)
            rows = await cursor.fetchall()
            return [
                {
                    "user_id": r[0],
                    "expiry": datetime.fromisoformat(r[1]) if r[1] else None,
                    "reason": r[2],
                    "mod_id": r[3],
                    "created_at": datetime.fromisoformat(r[4]),
                }
                for r in rows
            ]

    async def clean_expired_mutes(self):
        """Remove expired mutes"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM muted_users WHERE expiry < CURRENT_TIMESTAMP")
            await db.commit()

    # Pending submissions
    async def save_submission(self, user_id, topic, opening, why, mod_message_id=None):
        """Save a pending submission"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO pending_submissions (user_id, topic, opening, why, mod_message_id)
                VALUES (?, ?, ?, ?, ?)
            """,
                (user_id, topic, opening, why, mod_message_id),
            )
            await db.commit()

    async def get_submission(self, user_id):
        """Get a pending submission"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT user_id, topic, opening, why, mod_message_id
                FROM pending_submissions WHERE user_id = ?
            """,
                (user_id,),
            )
            row = await cursor.fetchone()
            if row:
                return {
                    "user_id": row[0],
                    "topic": row[1],
                    "opening": row[2],
                    "why": row[3],
                    "mod_message_id": row[4],
                }
            return None

    async def delete_submission(self, user_id):
        """Delete a pending submission"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM pending_submissions WHERE user_id = ?", (user_id,)
            )
            await db.commit()

    # Analytics queries
    async def get_dashboard_stats(self):
        async with aiosqlite.connect(self.db_path) as db:
            # Total debates
            cursor = await db.execute("SELECT COUNT(*) FROM debates")
            total = await cursor.fetchone()

            # Active debates (not closed, active in last 48h)
            cursor = await db.execute("""
                SELECT COUNT(*) FROM debates 
                WHERE closed_at IS NULL 
                AND last_activity > datetime('now', '-48 hours')
            """)
            active = await cursor.fetchone()

            # Total messages
            cursor = await db.execute("SELECT SUM(message_count) FROM debates")
            messages = await cursor.fetchone()

            # Unique participants
            cursor = await db.execute(
                "SELECT COUNT(DISTINCT user_id) FROM participants"
            )
            participants = await cursor.fetchone()

            # Avg debate length
            cursor = await db.execute("""
                SELECT AVG(julianday(closed_at) - julianday(created_at)) * 24
                FROM debates WHERE closed_at IS NOT NULL
            """)
            avg_length = await cursor.fetchone()

            return {
                "total_debates": total[0] or 0,
                "active_debates": active[0] or 0,
                "total_messages": messages[0] or 0,
                "unique_participants": participants[0] or 0,
                "avg_length": round(avg_length[0] or 0, 1),
            }

    async def get_weekly_leaderboard(self, limit=5):
        week_start = datetime.now().strftime("%Y-%W")
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT user_id, score, messages_sent, debates_created
                FROM weekly_scores
                WHERE week_start = ?
                ORDER BY score DESC
                LIMIT ?
            """,
                (week_start, limit),
            )
            rows = await cursor.fetchall()
            return [
                {"user_id": r[0], "score": r[1], "messages": r[2], "debates": r[3]}
                for r in rows
            ]

    async def get_trending_debates(self, limit=5):
        """
        Get trending debates based on recent activity (last 24h)
        Returns list of debates with message count and thread IDs
        """
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT 
                    d.thread_id,
                    d.topic,
                    d.message_count,
                    COUNT(DISTINCT p.user_id) as participant_count,
                    MAX(p.last_active) as last_activity
                FROM debates d
                LEFT JOIN participants p ON d.thread_id = p.thread_id
                WHERE d.closed_at IS NULL 
                    AND d.last_activity > datetime('now', '-24 hours')
                GROUP BY d.thread_id
                ORDER BY d.message_count DESC, last_activity DESC
                LIMIT ?
            """,
                (limit,),
            )

            rows = await cursor.fetchall()
            return [
                {
                    "thread_id": r[0],
                    "topic": r[1],
                    "message_count": r[2] or 0,
                    "participant_count": r[3] or 0,
                    "last_activity": r[4],
                }
                for r in rows
            ]

    async def get_debate_info(self, thread_id):
        """Get debate info for a thread"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT last_activity, message_count, closed_at 
                FROM debates WHERE thread_id = ?
            """,
                (thread_id,),
            )
            return await cursor.fetchone()

    async def get_participant_count(self, thread_id):
        """Get participant count for a thread"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT COUNT(*) FROM participants WHERE thread_id = ?
            """,
                (thread_id,),
            )
            result = await cursor.fetchone()
            return result[0] if result else 0

    async def get_top_participant(self, thread_id):
        """Get most active participant in a thread"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT user_id, messages_sent FROM participants 
                WHERE thread_id = ? ORDER BY messages_sent DESC LIMIT 1
            """,
                (thread_id,),
            )
            return await cursor.fetchone()

    async def get_user_stats(self, user_id):
        """Get stats for a specific user"""
        async with aiosqlite.connect(self.db_path) as db:
            # Debates created
            cursor = await db.execute(
                "SELECT COUNT(*) FROM debates WHERE proposer_id = ?", (user_id,)
            )
            created = await cursor.fetchone()

            # Messages sent
            cursor = await db.execute(
                "SELECT SUM(messages_sent) FROM participants WHERE user_id = ?",
                (user_id,),
            )
            messages = await cursor.fetchone()

            # Weekly score
            week_start = datetime.now().strftime("%Y-%W")
            cursor = await db.execute(
                "SELECT score FROM weekly_scores WHERE user_id = ? AND week_start = ?",
                (user_id, week_start),
            )
            weekly = await cursor.fetchone()

            return {
                "debates_created": created[0] or 0,
                "messages_sent": messages[0] or 0,
                "weekly_score": weekly[0] or 0,
            }

    async def reset_weekly_scores(self):
        """Call this every Monday"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM weekly_scores WHERE week_start < ?",
                (datetime.now().strftime("%Y-%W"),),
            )
            await db.commit()

    async def can_add_points(self, user_id, points_per_hour_limit=10):
        """
        Check if user can earn more points (anti-spam)
        Limits points per hour to prevent spam
        """
        hour_ago = (datetime.now() - timedelta(hours=1)).isoformat()

        async with aiosqlite.connect(self.db_path) as db:
            # Check points earned in last hour
            cursor = await db.execute(
                """
                SELECT COALESCE(SUM(score), 0) FROM weekly_scores 
                WHERE user_id = ? AND week_start = ? 
                AND last_updated > ?
            """,
                (user_id, datetime.now().strftime("%Y-%W"), hour_ago),
            )

            result = await cursor.fetchone()
            points_last_hour = result[0] if result else 0

            return points_last_hour < points_per_hour_limit

    async def add_weekly_score(
        self, user_id, points, message_sent=False, debate_created=False
    ):
        """Add weekly score with anti-spam protection"""
        week_start = datetime.now().strftime("%Y-%W")

        # Anti-spam: Check if user can earn points
        if (
            not debate_created and points > 0
        ):  # Skip check for debate creation (one-time)
            can_add = await self.can_add_points(user_id)
            if not can_add:
                return False  # User has hit rate limit

        async with aiosqlite.connect(self.db_path) as db:
            # Add last_updated column if it doesn't exist (run once)
            try:
                await db.execute(
                    "ALTER TABLE weekly_scores ADD COLUMN last_updated TIMESTAMP"
                )
            except Exception:
                pass  # Column already exists

            await db.execute(
                """
                INSERT INTO weekly_scores (user_id, week_start, score, messages_sent, debates_created, last_updated)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id, week_start) DO UPDATE SET
                    score = score + ?,
                    messages_sent = messages_sent + ?,
                    debates_created = debates_created + ?,
                    last_updated = CURRENT_TIMESTAMP
            """,
                (
                    user_id,
                    week_start,
                    points,
                    1 if message_sent else 0,
                    1 if debate_created else 0,
                    points,
                    1 if message_sent else 0,
                    1 if debate_created else 0,
                ),
            )
            await db.commit()
            return True

    async def get_trending_debaters(self, limit=5):
        """
        Get trending debaters based on recent activity (last 24h)
        """
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT 
                    p.user_id,
                    COUNT(DISTINCT p.thread_id) as active_debates,
                    SUM(p.messages_sent) as recent_msgs,
                    COALESCE(w.score, 0) as weekly_score
                FROM participants p
                LEFT JOIN weekly_scores w ON p.user_id = w.user_id 
                    AND w.week_start = ?
                WHERE p.last_active > datetime('now', '-24 hours')
                GROUP BY p.user_id
                ORDER BY recent_msgs DESC, weekly_score DESC
                LIMIT ?
            """,
                (datetime.now().strftime("%Y-%W"), limit),
            )

            rows = await cursor.fetchall()
            return [
                {
                    "user_id": r[0],
                    "active_debates": r[1],
                    "recent_messages": r[2],
                    "weekly_score": r[3],
                }
                for r in rows
            ]

    async def check_reaction_exists(self, message_id, reactor_id):
        """Check if a reaction has already been recorded"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(
                    """
                    SELECT 1 FROM reactions 
                    WHERE message_id = ? AND reactor_id = ?
                """,
                    (message_id, reactor_id),
                )
                result = await cursor.fetchone()
                return result is not None
        except sqlite3.Error as e:
            print(f"Database error in check_reaction_exists: {e}")
            return False

    async def record_reaction(self, message_id, user_id, reactor_id):
        """Record a reaction to prevent duplicates"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    """
                    INSERT INTO reactions (message_id, user_id, reactor_id)
                    VALUES (?, ?, ?)
                """,
                    (message_id, user_id, reactor_id),
                )
                await db.commit()
        except sqlite3.Error as e:
            print(f"Database error in record_reaction: {e}")

    async def get_user_pending_submissions_count(self, user_id):
        """Get count of pending submissions for a user"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM pending_submissions WHERE user_id = ?", (user_id,)
            )
            result = await cursor.fetchone()
            return result[0] if result else 0

    async def get_last_submission_time(self, user_id):
        """Get timestamp of user's last submission"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT created_at FROM pending_submissions WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
                (user_id,),
            )
            result = await cursor.fetchone()
            return datetime.fromisoformat(result[0]) if result else None

    async def get_all_pending_submissions(self):
        """Get all pending submissions with their timestamps"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                SELECT user_id, topic, opening, why, mod_message_id, created_at 
                FROM pending_submissions
            """)
            rows = await cursor.fetchall()
            return [
                {
                    "user_id": r[0],
                    "topic": r[1],
                    "opening": r[2],
                    "why": r[3],
                    "mod_message_id": r[4],
                    "created_at": datetime.fromisoformat(r[5]),
                }
                for r in rows
            ]

    async def get_old_pending_submissions(self, hours=24):
        """Get pending submissions older than specified hours"""
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT user_id, mod_message_id FROM pending_submissions WHERE created_at < ?",
                (cutoff,),
            )
            return await cursor.fetchall()

    async def cleanup_old_data(self, days=30):
        """Clean up old data from all tables"""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()

        async with aiosqlite.connect(self.db_path) as db:
            # Clean old reactions
            await db.execute("DELETE FROM reactions WHERE created_at < ?", (cutoff,))

            # Clean old participant records from closed debates
            await db.execute(
                """
                DELETE FROM participants 
                WHERE thread_id IN (
                    SELECT thread_id FROM debates WHERE closed_at < ?
                )
            """,
                (cutoff,),
            )

            # Archive or delete old closed debates (keep summary data but remove from main tables)
            # You might want to move these to an archive table instead of deleting
            await db.execute("DELETE FROM debates WHERE closed_at < ?", (cutoff,))

            await db.commit()
            return True

    async def auto_reject_expired_submissions(self, hours=24):
        """Auto-reject submissions older than specified hours"""
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT user_id, mod_message_id FROM pending_submissions WHERE created_at < ?",
                (cutoff,),
            )
            expired = await cursor.fetchall()

            for user_id, mod_message_id in expired:
                # Delete from database
                await db.execute(
                    "DELETE FROM pending_submissions WHERE user_id = ?", (user_id,)
                )

            await db.commit()
            return expired
