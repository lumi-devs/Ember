import json
from datetime import datetime, timedelta

from core.database import DatabaseManager

from .constants import (
    RATING_TTL_DAYS,
    REPORT_RATE_LIMIT,
    REPORT_RATE_WINDOW,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS sc_queue (
user_id INTEGER PRIMARY KEY,
gender TEXT NOT NULL,
joined_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sc_sessions (
session_id TEXT PRIMARY KEY,
user1_id INTEGER NOT NULL,
user2_id INTEGER NOT NULL,
channel1_id INTEGER NOT NULL,
channel2_id INTEGER NOT NULL,
webhook1_url TEXT NOT NULL,
webhook2_url TEXT NOT NULL,
codename1 TEXT NOT NULL,
codename2 TEXT NOT NULL,
started_at REAL NOT NULL,
ended_at REAL,
end_reason TEXT,
reveal_done INTEGER DEFAULT 0,
welcome_msg1_id INTEGER,
welcome_msg2_id INTEGER
);

CREATE TABLE IF NOT EXISTS sc_messages (
id INTEGER PRIMARY KEY AUTOINCREMENT,
session_id TEXT NOT NULL,
sender_id INTEGER NOT NULL,
content TEXT,
attachments TEXT,
sent_at REAL NOT NULL,
FOREIGN KEY(session_id) REFERENCES sc_sessions(session_id)
);

CREATE TABLE IF NOT EXISTS sc_stats (
user_id INTEGER PRIMARY KEY,
total_chats INTEGER DEFAULT 0,
total_seconds INTEGER DEFAULT 0,
total_messages INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sc_reports (
id INTEGER PRIMARY KEY AUTOINCREMENT,
session_id TEXT,
reporter_id INTEGER NOT NULL,
reported_id INTEGER NOT NULL,
reason TEXT,
reported_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sc_reveal_requests (
session_id TEXT PRIMARY KEY,
requester_id INTEGER NOT NULL,
requested_at REAL NOT NULL,
accepted INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sc_cooldowns (
user_id INTEGER PRIMARY KEY,
expires_at REAL NOT NULL
);

-- one row per relayed message (src = sender's channel, dst = partner's channel)
CREATE TABLE IF NOT EXISTS sc_msg_map (
id INTEGER PRIMARY KEY AUTOINCREMENT,
session_id TEXT NOT NULL,
src_channel_id INTEGER NOT NULL,
src_msg_id INTEGER NOT NULL,
dst_channel_id INTEGER NOT NULL,
dst_msg_id INTEGER NOT NULL,
content TEXT,
codename TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sc_dm_mute (
user_id INTEGER PRIMARY KEY,
muted_until REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sc_report_rate (
user_id INTEGER NOT NULL,
reported_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sc_ratings (
id INTEGER PRIMARY KEY AUTOINCREMENT,
session_id TEXT NOT NULL,
user_id INTEGER NOT NULL,
rated_user_id INTEGER NOT NULL DEFAULT 0,
rating INTEGER NOT NULL,
given_at REAL NOT NULL,
expires_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sc_rating_skip (
user_id INTEGER PRIMARY KEY,
skips_remaining INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_sc_sess_users ON sc_sessions(user1_id, user2_id);
CREATE INDEX IF NOT EXISTS idx_sc_ratings_expires ON sc_ratings(expires_at);
CREATE INDEX IF NOT EXISTS idx_sc_sess_channels ON sc_sessions(channel1_id, channel2_id);
CREATE INDEX IF NOT EXISTS idx_sc_msgs_session ON sc_messages(session_id);
CREATE INDEX IF NOT EXISTS idx_sc_msgmap_src ON sc_msg_map(session_id, src_channel_id, src_msg_id);
CREATE INDEX IF NOT EXISTS idx_sc_msgmap_dst ON sc_msg_map(session_id, dst_channel_id, dst_msg_id);
CREATE INDEX IF NOT EXISTS idx_sc_report_rate_user ON sc_report_rate(user_id);
"""

_MIGRATIONS = [
    "ALTER TABLE sc_sessions ADD COLUMN welcome_msg1_id INTEGER",
    "ALTER TABLE sc_sessions ADD COLUMN welcome_msg2_id INTEGER",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_sc_ratings_unique ON sc_ratings(session_id, user_id)",
    "ALTER TABLE sc_ratings ADD COLUMN rated_user_id INTEGER NOT NULL DEFAULT 0",
    "CREATE INDEX IF NOT EXISTS idx_sc_ratings_rated ON sc_ratings(rated_user_id)",
    "CREATE TABLE IF NOT EXISTS sc_rating_skip (user_id INTEGER PRIMARY KEY, skips_remaining INTEGER NOT NULL DEFAULT 0)",
]


class ShadowDB(DatabaseManager):
    def __init__(self):
        super().__init__("shadowchat_v2")

    async def init_db(self):
        await self.execute_script(SCHEMA)
        for sql in _MIGRATIONS:
            try:
                await self.execute(sql)
            except Exception:
                pass

    # ── Queue ─────────────────────────────────────────────────────────────────

    async def queue_add(self, user_id: int, gender: str):
        await self.execute(
            "INSERT OR REPLACE INTO sc_queue (user_id, gender, joined_at) VALUES (?, ?, ?)",
            (user_id, gender, datetime.utcnow().timestamp()),
        )

    async def queue_remove(self, user_id: int):
        await self.execute("DELETE FROM sc_queue WHERE user_id = ?", (user_id,))

    async def queue_get_all(self) -> list[dict]:
        return await self.fetch("SELECT * FROM sc_queue ORDER BY joined_at ASC")

    async def queue_get_user(self, user_id: int) -> dict | None:
        return await self.fetch_one("SELECT * FROM sc_queue WHERE user_id = ?", (user_id,))

    # ── Sessions ──────────────────────────────────────────────────────────────

    async def session_create(
        self,
        session_id: str,
        user1_id: int,
        user2_id: int,
        channel1_id: int,
        channel2_id: int,
        webhook1_url: str,
        webhook2_url: str,
        codename1: str,
        codename2: str,
    ):
        await self.execute(
            """INSERT INTO sc_sessions
            (session_id, user1_id, user2_id, channel1_id, channel2_id,
            webhook1_url, webhook2_url, codename1, codename2, started_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                user1_id,
                user2_id,
                channel1_id,
                channel2_id,
                webhook1_url,
                webhook2_url,
                codename1,
                codename2,
                datetime.utcnow().timestamp(),
            ),
        )

    async def session_end(self, session_id: str, reason: str):
        await self.execute(
            "UPDATE sc_sessions SET ended_at = ?, end_reason = ? WHERE session_id = ?",
            (datetime.utcnow().timestamp(), reason, session_id),
        )

    async def session_get(self, session_id: str) -> dict | None:
        return await self.fetch_one("SELECT * FROM sc_sessions WHERE session_id = ?", (session_id,))

    async def session_get_by_channel(self, channel_id: int) -> dict | None:
        return await self.fetch_one(
            "SELECT * FROM sc_sessions WHERE (channel1_id = ? OR channel2_id = ?) AND ended_at IS NULL",
            (channel_id, channel_id),
        )

    async def session_get_active_all(self) -> list[dict]:
        return await self.fetch("SELECT * FROM sc_sessions WHERE ended_at IS NULL")

    async def session_set_reveal_done(self, session_id: str):
        await self.execute(
            "UPDATE sc_sessions SET reveal_done = 1 WHERE session_id = ?", (session_id,)
        )

    async def session_set_welcome_msgs(self, session_id: str, msg1_id: int, msg2_id: int):
        await self.execute(
            "UPDATE sc_sessions SET welcome_msg1_id = ?, welcome_msg2_id = ? WHERE session_id = ?",
            (msg1_id, msg2_id, session_id),
        )

    async def session_update_codenames(self, session_id: str, codename1: str, codename2: str):
        await self.execute(
            "UPDATE sc_sessions SET codename1 = ?, codename2 = ? WHERE session_id = ?",
            (codename1, codename2, session_id),
        )

    # ── Messages ──────────────────────────────────────────────────────────────

    async def message_log(
        self, session_id: str, sender_id: int, content: str, attachments: list[str]
    ):
        await self.execute(
            "INSERT INTO sc_messages (session_id, sender_id, content, attachments, sent_at) VALUES (?, ?, ?, ?, ?)",
            (
                session_id,
                sender_id,
                content or "",
                json.dumps(attachments),
                datetime.utcnow().timestamp(),
            ),
        )

    async def messages_get_session(self, session_id: str) -> list[dict]:
        return await self.fetch(
            "SELECT * FROM sc_messages WHERE session_id = ? ORDER BY sent_at ASC", (session_id,)
        )

    async def messages_count_session(self, user_id: int, session_id: str) -> int:
        row = await self.fetch_one(
            "SELECT COUNT(*) as cnt FROM sc_messages WHERE session_id = ? AND sender_id = ?",
            (session_id, user_id),
        )
        return row["cnt"] if row else 0

    # ── Stats ─────────────────────────────────────────────────────────────────

    async def stats_end_session(self, user_id: int, seconds: int, messages: int):
        await self.execute(
            """INSERT INTO sc_stats (user_id, total_chats, total_seconds, total_messages)
            VALUES (?, 1, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
            total_chats = total_chats + 1,
            total_seconds = total_seconds + ?,
            total_messages = total_messages + ?""",
            (user_id, seconds, messages, seconds, messages),
        )

    async def stats_add_message(self, user_id: int):
        await self.execute(
            """INSERT INTO sc_stats (user_id, total_chats, total_seconds, total_messages)
            VALUES (?, 0, 0, 1)
            ON CONFLICT(user_id) DO UPDATE SET total_messages = total_messages + 1""",
            (user_id,),
        )

    async def stats_get(self, user_id: int) -> dict | None:
        return await self.fetch_one("SELECT * FROM sc_stats WHERE user_id = ?", (user_id,))

    # ── Reports ───────────────────────────────────────────────────────────────

    async def report_add(self, session_id: str, reporter_id: int, reported_id: int, reason: str):
        await self.execute(
            "INSERT INTO sc_reports (session_id, reporter_id, reported_id, reason, reported_at) VALUES (?, ?, ?, ?, ?)",
            (session_id, reporter_id, reported_id, reason, datetime.utcnow().timestamp()),
        )

    async def report_rate_check(self, user_id: int) -> bool:
        row = await self.fetch_one(
            "SELECT COUNT(*) as cnt FROM sc_report_rate WHERE user_id = ? AND reported_at > ?",
            (user_id, datetime.utcnow().timestamp() - REPORT_RATE_WINDOW),
        )
        return (row["cnt"] if row else 0) < REPORT_RATE_LIMIT

    async def report_rate_record(self, user_id: int):
        await self.execute(
            "INSERT INTO sc_report_rate (user_id, reported_at) VALUES (?, ?)",
            (user_id, datetime.utcnow().timestamp()),
        )

    # ── Reveal ────────────────────────────────────────────────────────────────

    async def reveal_set(self, session_id: str, requester_id: int):
        await self.execute(
            "INSERT OR REPLACE INTO sc_reveal_requests (session_id, requester_id, requested_at, accepted) VALUES (?, ?, ?, 0)",
            (session_id, requester_id, datetime.utcnow().timestamp()),
        )

    async def reveal_get(self, session_id: str) -> dict | None:
        return await self.fetch_one(
            "SELECT * FROM sc_reveal_requests WHERE session_id = ?", (session_id,)
        )

    async def reveal_accept(self, session_id: str):
        await self.execute(
            "UPDATE sc_reveal_requests SET accepted = 1 WHERE session_id = ?", (session_id,)
        )

    async def reveal_clear(self, session_id: str):
        await self.execute("DELETE FROM sc_reveal_requests WHERE session_id = ?", (session_id,))

    # ── Cooldowns ─────────────────────────────────────────────────────────────

    async def cooldown_set(self, user_id: int, seconds: int):
        expires = (datetime.utcnow() + timedelta(seconds=seconds)).timestamp()
        await self.execute(
            "INSERT OR REPLACE INTO sc_cooldowns (user_id, expires_at) VALUES (?, ?)",
            (user_id, expires),
        )

    async def cooldown_check(self, user_id: int) -> float | None:
        row = await self.fetch_one(
            "SELECT expires_at FROM sc_cooldowns WHERE user_id = ?", (user_id,)
        )
        if not row:
            return None
        remaining = row["expires_at"] - datetime.utcnow().timestamp()
        if remaining <= 0:
            await self.execute("DELETE FROM sc_cooldowns WHERE user_id = ?", (user_id,))
            return None
        return remaining

    async def cooldown_extend(self, user_id: int, seconds: int):
        row = await self.fetch_one(
            "SELECT expires_at FROM sc_cooldowns WHERE user_id = ?", (user_id,)
        )
        if row:
            new_expires = max(row["expires_at"], datetime.utcnow().timestamp()) + seconds
        else:
            new_expires = datetime.utcnow().timestamp() + seconds
        await self.execute(
            "INSERT OR REPLACE INTO sc_cooldowns (user_id, expires_at) VALUES (?, ?)",
            (user_id, new_expires),
        )

    # ── Message mapping (for reply embeds) ───────────────────────────────────

    async def msg_map_store(
        self,
        session_id: str,
        src_channel_id: int,
        src_msg_id: int,
        dst_channel_id: int,
        dst_msg_id: int,
        content: str | None,
        codename: str,
    ):
        await self.execute(
            """INSERT INTO sc_msg_map
            (session_id, src_channel_id, src_msg_id, dst_channel_id, dst_msg_id, content, codename)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                src_channel_id,
                src_msg_id,
                dst_channel_id,
                dst_msg_id,
                content or "",
                codename,
            ),
        )

    async def msg_map_lookup(self, session_id: str, channel_id: int, msg_id: int) -> dict | None:
        row = await self.fetch_one(
            """SELECT codename, content, src_channel_id, src_msg_id FROM sc_msg_map
            WHERE session_id = ? AND dst_channel_id = ? AND dst_msg_id = ?""",
            (session_id, channel_id, msg_id),
        )
        if row:
            return {
                "codename": row["codename"],
                "content": row["content"],
                "src_channel_id": row["src_channel_id"],
                "src_msg_id": row["src_msg_id"],
            }

        row = await self.fetch_one(
            """SELECT codename, content, dst_channel_id, dst_msg_id FROM sc_msg_map
            WHERE session_id = ? AND src_channel_id = ? AND src_msg_id = ?""",
            (session_id, channel_id, msg_id),
        )
        if row:
            return {
                "codename": row["codename"],
                "content": row["content"],
                "src_channel_id": channel_id,
                "src_msg_id": msg_id,
            }
        return None

    # ── DM mute ───────────────────────────────────────────────────────────────

    async def dm_mute_set(self, user_id: int, hours: int = 12):
        expires = (datetime.utcnow() + timedelta(hours=hours)).timestamp()
        await self.execute(
            "INSERT OR REPLACE INTO sc_dm_mute (user_id, muted_until) VALUES (?, ?)",
            (user_id, expires),
        )

    async def dm_mute_check(self, user_id: int) -> bool:
        row = await self.fetch_one(
            "SELECT muted_until FROM sc_dm_mute WHERE user_id = ?", (user_id,)
        )
        if not row:
            return False
        if row["muted_until"] <= datetime.utcnow().timestamp():
            await self.execute("DELETE FROM sc_dm_mute WHERE user_id = ?", (user_id,))
            return False
        return True

    # ── Ratings ───────────────────────────────────────────────────────────────

    async def rating_add(self, session_id: str, rater_id: int, rated_user_id: int, rating: int):
        now = datetime.utcnow().timestamp()
        expires = now + (RATING_TTL_DAYS * 86400)
        await self.execute(
            "INSERT OR IGNORE INTO sc_ratings "
            "(session_id, user_id, rated_user_id, rating, given_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, rater_id, rated_user_id, rating, now, expires),
        )

    async def rating_get_user_avg(self, user_id: int) -> float | None:
        row = await self.fetch_one(
            "SELECT AVG(rating) as avg_rating, COUNT(*) as cnt FROM sc_ratings WHERE rated_user_id = ?",
            (user_id,),
        )
        if row and row["cnt"] > 0:
            return round(row["avg_rating"], 2)
        return None

    async def rating_get_user_count(self, user_id: int) -> int:
        row = await self.fetch_one(
            "SELECT COUNT(*) as cnt FROM sc_ratings WHERE rated_user_id = ?", (user_id,)
        )
        return row["cnt"] if row else 0

    async def ratings_cleanup_expired(self):
        await self.execute(
            "DELETE FROM sc_ratings WHERE expires_at <= ?", (datetime.utcnow().timestamp(),)
        )

    # ── Rating skip ───────────────────────────────────────────────────────────

    async def rating_skip_check(self, user_id: int) -> bool:
        row = await self.fetch_one(
            "SELECT skips_remaining FROM sc_rating_skip WHERE user_id = ?", (user_id,)
        )
        return bool(row and row["skips_remaining"] > 0)

    async def rating_skip_set(self, user_id: int, n: int = 10):
        await self.execute(
            "INSERT OR REPLACE INTO sc_rating_skip (user_id, skips_remaining) VALUES (?, ?)",
            (user_id, n),
        )

    async def rating_skip_decrement(self, user_id: int):
        await self.execute(
            "UPDATE sc_rating_skip SET skips_remaining = skips_remaining - 1 WHERE user_id = ?",
            (user_id,),
        )
        await self.execute(
            "DELETE FROM sc_rating_skip WHERE user_id = ? AND skips_remaining <= 0", (user_id,)
        )

    # ── Global stats ─────────────────────────────────────────────────────────

    async def recent_sessions_count(self, seconds: int) -> int:
        cutoff = datetime.utcnow().timestamp() - seconds
        row = await self.fetch_one(
            "SELECT COUNT(*) as cnt FROM sc_sessions WHERE started_at >= ?", (cutoff,)
        )
        return row["cnt"] if row else 0

    async def global_stats(self) -> dict:
        row = await self.fetch_one("SELECT COUNT(*) as cnt FROM sc_sessions WHERE ended_at IS NULL")
        active_sessions = row["cnt"] if row else 0

        row = await self.fetch_one("SELECT COUNT(*) as cnt FROM sc_queue")
        queue_size = row["cnt"] if row else 0

        row = await self.fetch_one("SELECT COUNT(*) as cnt FROM sc_sessions")
        total_sessions = row["cnt"] if row else 0

        row = await self.fetch_one("SELECT COUNT(DISTINCT user_id) as cnt FROM sc_stats")
        total_users = row["cnt"] if row else 0

        return {
            "active_sessions": active_sessions,
            "queue_size": queue_size,
            "users_chatting": active_sessions * 2,
            "total_sessions": total_sessions,
            "total_users": total_users,
        }

    async def delete_user_data(self, user_id: int) -> None:
        """Delete all user data for GDPR compliance."""
        # Remove from queue
        await self.execute("DELETE FROM sc_queue WHERE user_id = ?", (user_id,))
        # Delete sessions where user is participant (will cascade messages)
        await self.execute(
            "DELETE FROM sc_sessions WHERE user1_id = ? OR user2_id = ?", (user_id, user_id)
        )
        # Delete user stats
        await self.execute("DELETE FROM sc_stats WHERE user_id = ?", (user_id,))
        # Delete reports made by user
        await self.execute("DELETE FROM sc_reports WHERE reporter_id = ?", (user_id,))
        # Delete reveal requests by user
        await self.execute("DELETE FROM sc_reveal_requests WHERE requester_id = ?", (user_id,))
        # Delete cooldowns
        await self.execute("DELETE FROM sc_cooldowns WHERE user_id = ?", (user_id,))
        # Delete rating skips
        await self.execute("DELETE FROM sc_rating_skip WHERE user_id = ?", (user_id,))
        # Delete ratings given by user
        await self.execute("DELETE FROM sc_ratings WHERE user_id = ?", (user_id,))
        # For ratings where user was rated (clean up rated_user_id)
        await self.execute(
            "UPDATE sc_ratings SET rated_user_id = 0 WHERE rated_user_id = ?", (user_id,)
        )
        # Delete DM mutes
        await self.execute("DELETE FROM sc_dm_mute WHERE user_id = ?", (user_id,))
        # Delete message mappings
        await self.execute(
            "DELETE FROM sc_msg_map WHERE session_id IN ("
            "SELECT session_id FROM sc_sessions WHERE user1_id = ? OR user2_id = ?)",
            (user_id, user_id),
        )
