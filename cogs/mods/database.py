"""Database management for Staff Analytics cog."""

import json
import aiosqlite
import asyncio
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple
from collections import defaultdict
import logging
import discord

from .models import (
    ConsistencyStats, StaffMessage, Interaction, ConversationSession,
    ModerationAction, VCSession, Report, Bump, RolePing, HelpEvent, StaffStats,
    ReactionEvent, ThreadEvent, ResponseTime, StaffNote,
    QualityEvent, FirstResponseEvent, EngagementEvent, ReactionReceived,
)
from .constants import DB_BATCH_SIZE, DB_BATCH_TIMEOUT, DB_RETENTION_DAYS, SCORE_WEIGHTS, MODERATION_SEVERITY
from .utils import truncate_string

logger = logging.getLogger(__name__)



class DatabaseManager:
    def __init__(self, db_path: str = "staff_analytics.db"):
        self.db_path = db_path
        self.pool: Optional[aiosqlite.Connection] = None
        self.batch_queue: Dict[str, List[Dict]] = defaultdict(list)
        self._lock = asyncio.Lock()

        # BUG FIX: consistency_stats was written on EVERY staff message — at 24k
        # msg/day with ~20% being staff that's ~4 800 individual DB transactions.
        # We now coalesce within a write window: track the pending update and flush
        # it at most once per CONSISTENCY_COALESCE_SECS per staff member.
        self._consistency_pending: Dict[int, datetime] = {}   # staff_id -> latest message_time
        self._consistency_flush_lock = asyncio.Lock()
        self._CONSISTENCY_COALESCE_SECS = 60   # flush at most once per minute per staff

        # BUG FIX: _batch_processor ran forever with no shutdown signal.
        # Adding a flag so close() can drain cleanly.
        self._running = True

        # Dashboard stats cache: guild_id -> (timestamp, List[(member_id, StaffStats)])
        # Avoids re-querying all staff on every dashboard refresh tick
        self._dashboard_cache: Dict[int, tuple] = {}
        self._DASHBOARD_CACHE_TTL = 600   # seconds — refresh at most every 10 min

    # ─── Lifecycle ────────────────────────────────────────────────────────────

    async def initialize(self):
        self.pool = await aiosqlite.connect(self.db_path)
        self.pool.row_factory = aiosqlite.Row
        await self._create_tables()
        await self._migrate_tables()
        await self._create_indexes()
        asyncio.create_task(self._batch_processor())
        asyncio.create_task(self._consistency_flush_loop())
        asyncio.create_task(self._cleanup_old_data())
        logger.info("Database initialized")

    async def close(self):
        self._running = False
        # Flush all pending batches before closing
        async with self._lock:
            for table, items in list(self.batch_queue.items()):
                if items:
                    await self._flush_batch(table, list(items))
                    self.batch_queue[table] = []
        # Flush pending consistency updates
        async with self._consistency_flush_lock:
            for staff_id, msg_time in list(self._consistency_pending.items()):
                await self._do_update_consistency(staff_id, msg_time)
            self._consistency_pending.clear()
        if self.pool:
            await self.pool.close()

    # ─── Schema ───────────────────────────────────────────────────────────────

    async def _create_tables(self):
        stmts = [
            """CREATE TABLE IF NOT EXISTS staff_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                staff_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER UNIQUE NOT NULL,
                timestamp TIMESTAMP NOT NULL,
                content TEXT,
                word_count INTEGER DEFAULT 0
            )""",
            """CREATE TABLE IF NOT EXISTS interactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                staff_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                interaction_type TEXT NOT NULL,
                timestamp TIMESTAMP NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS conversation_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                staff_id INTEGER NOT NULL,
                start_time TIMESTAMP NOT NULL,
                end_time TIMESTAMP,
                channel_ids TEXT NOT NULL,
                user_ids TEXT NOT NULL,
                message_count INTEGER NOT NULL,
                duration_minutes REAL
            )""",
            """CREATE TABLE IF NOT EXISTS moderation_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                moderator_id INTEGER NOT NULL,
                target_id INTEGER NOT NULL,
                action_type TEXT NOT NULL,
                timestamp TIMESTAMP NOT NULL,
                reason TEXT,
                session_id TEXT,
                severity REAL DEFAULT 1.0
            )""",
            """CREATE TABLE IF NOT EXISTS vc_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                staff_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                join_time TIMESTAMP NOT NULL,
                leave_time TIMESTAMP,
                duration_minutes REAL
            )""",
            """CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                staff_id INTEGER NOT NULL,
                timestamp TIMESTAMP NOT NULL,
                message_link TEXT,
                report_type TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS bumps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                staff_id INTEGER NOT NULL,
                timestamp TIMESTAMP NOT NULL,
                bump_type TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS role_pings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                staff_id INTEGER NOT NULL,
                role_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                timestamp TIMESTAMP NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS help_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                helper_id INTEGER NOT NULL,
                helped_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                is_new_member INTEGER NOT NULL DEFAULT 0,
                timestamp TIMESTAMP NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS consistency_stats (
                staff_id INTEGER PRIMARY KEY,
                active_days INTEGER NOT NULL DEFAULT 0,
                active_hours TEXT,
                active_days_of_week TEXT,
                longest_streak INTEGER NOT NULL DEFAULT 0,
                current_streak INTEGER NOT NULL DEFAULT 0,
                last_active_date TIMESTAMP,
                peak_hours_activity INTEGER NOT NULL DEFAULT 0,
                off_peak_activity INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE IF NOT EXISTS reactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                staff_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                emoji TEXT NOT NULL,
                timestamp TIMESTAMP NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS thread_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                staff_id INTEGER NOT NULL,
                thread_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                timestamp TIMESTAMP NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS response_times (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                staff_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                response_seconds REAL NOT NULL,
                timestamp TIMESTAMP NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS staff_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                staff_id INTEGER NOT NULL,
                author_id INTEGER NOT NULL,
                note TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL,
                is_positive INTEGER NOT NULL DEFAULT 1
            )""",
            """CREATE TABLE IF NOT EXISTS quality_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                staff_id INTEGER NOT NULL,
                message_id INTEGER UNIQUE NOT NULL,
                channel_id INTEGER NOT NULL,
                quality_score REAL NOT NULL,
                signals TEXT,
                word_count INTEGER DEFAULT 0,
                timestamp TIMESTAMP NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS first_responses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                staff_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                response_seconds REAL NOT NULL,
                timestamp TIMESTAMP NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS custom_score_weights (
                guild_id INTEGER NOT NULL,
                weight_key TEXT NOT NULL,
                weight_value REAL NOT NULL,
                updated_by INTEGER NOT NULL,
                updated_at TIMESTAMP NOT NULL,
                PRIMARY KEY (guild_id, weight_key)
            )""",
            """CREATE TABLE IF NOT EXISTS engagement_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                staff_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                user_id INTEGER,
                timestamp TIMESTAMP NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS reactions_received (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                staff_id INTEGER NOT NULL,
                reactor_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                emoji TEXT NOT NULL,
                timestamp TIMESTAMP NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS notified_achievements (
                staff_id    INTEGER NOT NULL,
                achievement TEXT    NOT NULL,
                notified_at TIMESTAMP NOT NULL,
                PRIMARY KEY (staff_id, achievement)
            )""",
            """CREATE TABLE IF NOT EXISTS pending_mod_actions (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                moderator_id   INTEGER NOT NULL,
                target_id      INTEGER NOT NULL,
                action_type    TEXT NOT NULL,
                reason         TEXT,
                severity       REAL DEFAULT 1.0,
                detected_at    TIMESTAMP NOT NULL,
                guild_id       INTEGER NOT NULL,
                channel_id     INTEGER NOT NULL,
                message_id     INTEGER NOT NULL,
                review_msg_id  INTEGER,
                status         TEXT NOT NULL DEFAULT 'pending'
            )""",
            """CREATE TABLE IF NOT EXISTS daily_challenge_progress (
                staff_id    INTEGER NOT NULL,
                challenge   TEXT    NOT NULL,
                date        TEXT    NOT NULL,
                value       REAL    NOT NULL DEFAULT 0,
                completed   INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (staff_id, challenge, date)
            )""",
        ]
        for sql in stmts:
            await self.pool.execute(sql)
        await self.pool.commit()

    async def _migrate_tables(self):
        """Safe migrations: add columns that may not exist yet."""
        migrations = [
            ("moderation_actions", "severity REAL DEFAULT 1.0"),
            ("consistency_stats",  "off_peak_activity INTEGER NOT NULL DEFAULT 0"),
            ("vc_sessions",        "peak_occupancy INTEGER DEFAULT 1"),
        ]
        for table, col_def in migrations:
            try:
                await self.pool.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")
                await self.pool.commit()
                logger.info("Migrated: added %s to %s", col_def.split()[0], table)
            except Exception:
                pass  # Column already exists

        # Ensure new tables exist even on databases created before this version.
        # _create_tables uses CREATE TABLE IF NOT EXISTS so re-running is safe.
        await self._create_tables()

    async def _create_indexes(self):
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_sm_staff   ON staff_messages(staff_id)",
            "CREATE INDEX IF NOT EXISTS idx_sm_ts      ON staff_messages(timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_int_staff  ON interactions(staff_id)",
            "CREATE INDEX IF NOT EXISTS idx_int_user   ON interactions(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_int_ts     ON interactions(timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_cs_staff   ON conversation_sessions(staff_id)",
            "CREATE INDEX IF NOT EXISTS idx_ma_mod     ON moderation_actions(moderator_id)",
            "CREATE INDEX IF NOT EXISTS idx_ma_ts      ON moderation_actions(timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_vc_staff   ON vc_sessions(staff_id)",
            "CREATE INDEX IF NOT EXISTS idx_vc_join    ON vc_sessions(join_time)",
            "CREATE INDEX IF NOT EXISTS idx_rpt_staff  ON reports(staff_id)",
            "CREATE INDEX IF NOT EXISTS idx_bump_staff ON bumps(staff_id)",
            "CREATE INDEX IF NOT EXISTS idx_rp_staff   ON role_pings(staff_id)",
            "CREATE INDEX IF NOT EXISTS idx_he_helper  ON help_events(helper_id)",
            "CREATE INDEX IF NOT EXISTS idx_he_ts      ON help_events(timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_rx_staff   ON reactions(staff_id)",
            "CREATE INDEX IF NOT EXISTS idx_rx_ts      ON reactions(timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_te_staff   ON thread_events(staff_id)",
            "CREATE INDEX IF NOT EXISTS idx_rt_staff   ON response_times(staff_id)",
            "CREATE INDEX IF NOT EXISTS idx_sn_staff   ON staff_notes(staff_id)",
            "CREATE INDEX IF NOT EXISTS idx_qe_staff   ON quality_events(staff_id)",
            "CREATE INDEX IF NOT EXISTS idx_qe_ts      ON quality_events(timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_fr_staff   ON first_responses(staff_id)",
            "CREATE INDEX IF NOT EXISTS idx_fr_ts      ON first_responses(timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_ee_staff   ON engagement_events(staff_id)",
            "CREATE INDEX IF NOT EXISTS idx_ee_type    ON engagement_events(event_type)",
            "CREATE INDEX IF NOT EXISTS idx_ee_ts      ON engagement_events(timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_rr_staff   ON reactions_received(staff_id)",
            "CREATE INDEX IF NOT EXISTS idx_rr_ts      ON reactions_received(timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_ma_mod_ts ON moderation_actions(moderator_id, timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_pma_mod  ON pending_mod_actions(moderator_id)",
            "CREATE INDEX IF NOT EXISTS idx_pma_stat ON pending_mod_actions(status)",
            "CREATE INDEX IF NOT EXISTS idx_dcp_date ON daily_challenge_progress(date)",
            "CREATE INDEX IF NOT EXISTS idx_dcp_staff ON daily_challenge_progress(staff_id, date)",
        ]
        for sql in indexes:
            await self.pool.execute(sql)
        await self.pool.commit()

    # ─── Batch processing ─────────────────────────────────────────────────────

    async def _batch_processor(self):
        while self._running:
            await asyncio.sleep(DB_BATCH_TIMEOUT)
            async with self._lock:
                for table, items in list(self.batch_queue.items()):
                    if items:
                        await self._flush_batch(table, list(items))
                        self.batch_queue[table] = []

    async def _flush_batch(self, table: str, items: List[Dict]):
        if not items:
            return
        try:
            handler = {
                "staff_messages": self._batch_insert_staff_messages,
                "interactions":   self._batch_insert_interactions,
                "help_events":    self._batch_insert_help_events,
                "reactions":      self._batch_insert_reactions,
                "quality_events": self._batch_insert_quality_events,
            }.get(table)
            if handler:
                await handler(items)
        except Exception as e:
            logger.error("Batch flush error (%s): %s", table, e)

    async def _batch_insert_staff_messages(self, items: List[Dict]):
        await self.pool.executemany(
            "INSERT OR IGNORE INTO staff_messages "
            "(staff_id, channel_id, message_id, timestamp, content, word_count) "
            "VALUES (?,?,?,?,?,?)",
            [(i["staff_id"], i["channel_id"], i["message_id"],
              i["timestamp"], i.get("content"), i.get("word_count", 0)) for i in items],
        )
        await self.pool.commit()

    async def _batch_insert_interactions(self, items: List[Dict]):
        await self.pool.executemany(
            "INSERT INTO interactions "
            "(staff_id, user_id, channel_id, message_id, interaction_type, timestamp) "
            "VALUES (?,?,?,?,?,?)",
            [(i["staff_id"], i["user_id"], i["channel_id"],
              i["message_id"], i["interaction_type"], i["timestamp"]) for i in items],
        )
        await self.pool.commit()

    async def _batch_insert_help_events(self, items: List[Dict]):
        await self.pool.executemany(
            "INSERT INTO help_events "
            "(helper_id, helped_id, channel_id, message_id, event_type, is_new_member, timestamp) "
            "VALUES (?,?,?,?,?,?,?)",
            [(i["helper_id"], i["helped_id"], i["channel_id"], i["message_id"],
              i["event_type"], int(i.get("is_new_member", False)), i["timestamp"]) for i in items],
        )
        await self.pool.commit()

    async def _batch_insert_reactions(self, items: List[Dict]):
        await self.pool.executemany(
            "INSERT INTO reactions "
            "(staff_id, user_id, channel_id, message_id, emoji, timestamp) "
            "VALUES (?,?,?,?,?,?)",
            [(i["staff_id"], i["user_id"], i["channel_id"],
              i["message_id"], i["emoji"], i["timestamp"]) for i in items],
        )
        await self.pool.commit()

    async def _batch_insert_quality_events(self, items: List[Dict]):
        await self.pool.executemany(
            "INSERT OR IGNORE INTO quality_events "
            "(staff_id, message_id, channel_id, quality_score, signals, word_count, timestamp) "
            "VALUES (?,?,?,?,?,?,?)",
            [(i["staff_id"], i["message_id"], i["channel_id"], i["quality_score"],
              i.get("signals", ""), i.get("word_count", 0), i["timestamp"]) for i in items],
        )
        await self.pool.commit()

    # ─── Insert helpers ───────────────────────────────────────────────────────

    async def insert_staff_message(self, msg: StaffMessage):
        self.batch_queue["staff_messages"].append(msg.to_dict())
        if len(self.batch_queue["staff_messages"]) >= DB_BATCH_SIZE:
            async with self._lock:
                await self._flush_batch("staff_messages", list(self.batch_queue["staff_messages"]))
                self.batch_queue["staff_messages"] = []

    async def insert_interaction(self, interaction: Interaction):
        self.batch_queue["interactions"].append(interaction.to_dict())

    async def insert_help_event(self, event: HelpEvent):
        self.batch_queue["help_events"].append(event.to_dict())

    async def insert_reaction(self, event: ReactionEvent):
        self.batch_queue["reactions"].append(event.to_dict())

    async def insert_quality_event(self, event: QualityEvent):
        self.batch_queue["quality_events"].append(event.to_dict())

    async def insert_first_response(self, event: FirstResponseEvent):
        d = event.to_dict()
        await self.pool.execute(
            "INSERT INTO first_responses "
            "(staff_id, user_id, channel_id, message_id, response_seconds, timestamp) "
            "VALUES (?,?,?,?,?,?)",
            (d["staff_id"], d["user_id"], d["channel_id"],
             d["message_id"], d["response_seconds"], d["timestamp"]),
        )
        await self.pool.commit()

    async def insert_engagement_event(self, event: EngagementEvent):
        d = event.to_dict()
        await self.pool.execute(
            "INSERT INTO engagement_events "
            "(staff_id, channel_id, message_id, event_type, user_id, timestamp) "
            "VALUES (?,?,?,?,?,?)",
            (d["staff_id"], d["channel_id"], d["message_id"],
             d["event_type"], d["user_id"], d["timestamp"]),
        )
        await self.pool.commit()

    async def insert_reaction_received(self, event: ReactionReceived):
        d = event.to_dict()
        await self.pool.execute(
            "INSERT INTO reactions_received "
            "(staff_id, reactor_id, channel_id, message_id, emoji, timestamp) "
            "VALUES (?,?,?,?,?,?)",
            (d["staff_id"], d["reactor_id"], d["channel_id"],
             d["message_id"], d["emoji"], d["timestamp"]),
        )
        await self.pool.commit()

    async def insert_conversation_session(self, session: ConversationSession):
        d = session.to_dict()
        await self.pool.execute(
            "INSERT INTO conversation_sessions "
            "(staff_id, start_time, end_time, channel_ids, user_ids, message_count, duration_minutes) "
            "VALUES (?,?,?,?,?,?,?)",
            (d["staff_id"], d["start_time"], d["end_time"],
             ",".join(str(x) for x in d["channel_ids"]),
             ",".join(str(x) for x in d["user_ids"]),
             d["message_count"], d["duration_minutes"]),
        )
        await self.pool.commit()

    async def insert_moderation_action(self, action: ModerationAction):
        d = action.to_dict()
        await self.pool.execute(
            "INSERT INTO moderation_actions "
            "(moderator_id, target_id, action_type, timestamp, reason, session_id, severity) "
            "VALUES (?,?,?,?,?,?,?)",
            (d["moderator_id"], d["target_id"], d["action_type"], d["timestamp"],
             d["reason"], d["session_id"], d.get("severity", 1.0)),
        )
        await self.pool.commit()

    async def start_vc_session(self, session: VCSession) -> int:
        cursor = await self.pool.execute(
            "INSERT INTO vc_sessions (staff_id, channel_id, join_time) VALUES (?,?,?)",
            (session.staff_id, session.channel_id, session.join_time.isoformat()),
        )
        await self.pool.commit()
        return cursor.lastrowid

    async def end_vc_session(self, session_id: int, leave_time: datetime, peak_occupancy: int = 1):
        join_row = await (await self.pool.execute(
            "SELECT join_time FROM vc_sessions WHERE id = ?", (session_id,)
        )).fetchone()
        if join_row:
            join_dt = datetime.fromisoformat(join_row["join_time"])
            dur = (leave_time - join_dt).total_seconds() / 60
            await self.pool.execute(
                "UPDATE vc_sessions SET leave_time = ?, duration_minutes = ?, peak_occupancy = ? WHERE id = ?",
                (leave_time.isoformat(), dur, max(1, peak_occupancy), session_id),
            )
            await self.pool.commit()

    async def insert_report(self, report: Report):
        d = report.to_dict()
        await self.pool.execute(
            "INSERT INTO reports (staff_id, timestamp, message_link, report_type) VALUES (?,?,?,?)",
            (d["staff_id"], d["timestamp"], d["message_link"], d["report_type"]),
        )
        await self.pool.commit()

    async def insert_bump(self, bump: Bump):
        d = bump.to_dict()
        await self.pool.execute(
            "INSERT INTO bumps (staff_id, timestamp, bump_type) VALUES (?,?,?)",
            (d["staff_id"], d["timestamp"], d["bump_type"]),
        )
        await self.pool.commit()

    async def insert_role_ping(self, ping: RolePing):
        d = ping.to_dict()
        await self.pool.execute(
            "INSERT INTO role_pings "
            "(staff_id, role_id, channel_id, message_id, timestamp) VALUES (?,?,?,?,?)",
            (d["staff_id"], d["role_id"], d["channel_id"], d["message_id"], d["timestamp"]),
        )
        await self.pool.commit()

    async def insert_thread_event(self, event: ThreadEvent):
        d = event.to_dict()
        await self.pool.execute(
            "INSERT INTO thread_events "
            "(staff_id, thread_id, channel_id, event_type, timestamp) VALUES (?,?,?,?,?)",
            (d["staff_id"], d["thread_id"], d["channel_id"], d["event_type"], d["timestamp"]),
        )
        await self.pool.commit()

    async def insert_response_time(self, rt: ResponseTime):
        d = rt.to_dict()
        await self.pool.execute(
            "INSERT INTO response_times "
            "(staff_id, user_id, channel_id, response_seconds, timestamp) VALUES (?,?,?,?,?)",
            (d["staff_id"], d["user_id"], d["channel_id"], d["response_seconds"], d["timestamp"]),
        )
        await self.pool.commit()

    # ─── Consistency stats — coalesced writes ─────────────────────────────────

    async def update_consistency_stats(self, staff_id: int, message_time: datetime):
        """
        Enqueue a consistency-stats update.

        BUG FIX: original performed a full read-modify-write DB transaction on
        EVERY staff message.  At 24 k msg/day with 20 % staff that's ~4 800
        individual transactions.  We now coalesce: each staff member's update is
        held in memory for up to CONSISTENCY_COALESCE_SECS seconds, then flushed
        once per window by _consistency_flush_loop.

        The in-memory value is always the latest message_time so streak and
        active-hours logic (which cares about "did they send something today?")
        remains correct — we just avoid writing it 100 × per day when it changes
        trivially.
        """
        # Update the pending timestamp for this staff member.
        # If there's already a pending update, we keep the latest message_time.
        async with self._consistency_flush_lock:
            existing = self._consistency_pending.get(staff_id)
            if existing is None or message_time > existing:
                self._consistency_pending[staff_id] = message_time

    async def _consistency_flush_loop(self):
        """Flush coalesced consistency updates every CONSISTENCY_COALESCE_SECS."""
        while self._running:
            await asyncio.sleep(self._CONSISTENCY_COALESCE_SECS)
            async with self._consistency_flush_lock:
                pending = dict(self._consistency_pending)
                self._consistency_pending.clear()
            for staff_id, msg_time in pending.items():
                try:
                    await self._do_update_consistency(staff_id, msg_time)
                except Exception as e:
                    logger.error("Consistency flush error for %d: %s", staff_id, e)

    async def _do_update_consistency(self, staff_id: int, message_time: datetime):
        """Actual DB read-modify-write for consistency stats (called by flush loop)."""
        row = await (await self.pool.execute(
            "SELECT * FROM consistency_stats WHERE staff_id = ?", (staff_id,)
        )).fetchone()
        hour = message_time.hour
        is_off_peak = hour in list(range(0, 8)) + list(range(22, 24))

        if not row:
            stats = ConsistencyStats(
                staff_id=staff_id, active_days=1,
                current_streak=1, longest_streak=1,
                last_active_date=message_time,
                peak_hours_activity=1 if hour in range(14, 23) else 0,
                off_peak_activity=1 if is_off_peak else 0,
            )
            stats.active_hours[hour] = 1
            stats.active_days_of_week[message_time.weekday()] = 1
            await self._save_consistency_stats(stats)
            return

        stats = ConsistencyStats(
            staff_id=staff_id,
            active_days=row["active_days"],
            longest_streak=row["longest_streak"],
            current_streak=row["current_streak"],
            peak_hours_activity=row["peak_hours_activity"],
            off_peak_activity=row["off_peak_activity"] if "off_peak_activity" in row.keys() else 0,
            last_active_date=datetime.fromisoformat(row["last_active_date"]) if row["last_active_date"] else None,
        )
        from collections import defaultdict as _dd
        stats.active_hours        = _dd(int, {int(k): v for k, v in json.loads(row["active_hours"] or "{}").items()})
        stats.active_days_of_week = _dd(int, {int(k): v for k, v in json.loads(row["active_days_of_week"] or "{}").items()})
        if stats.last_active_date:
            diff = (message_time.date() - stats.last_active_date.date()).days
            if diff == 1:
                stats.current_streak += 1
                stats.longest_streak  = max(stats.longest_streak, stats.current_streak)
            elif diff > 1:
                stats.current_streak = 1
            if diff >= 1:
                stats.active_days += 1
        else:
            stats.current_streak = 1
            stats.active_days   += 1

        stats.active_hours[hour] += 1
        stats.active_days_of_week[message_time.weekday()] += 1
        if hour in range(14, 23):
            stats.peak_hours_activity += 1
        if is_off_peak:
            stats.off_peak_activity += 1
        stats.last_active_date = message_time
        await self._save_consistency_stats(stats)

    async def _save_consistency_stats(self, stats: ConsistencyStats):
        await self.pool.execute(
            """INSERT OR REPLACE INTO consistency_stats
               (staff_id, active_days, active_hours, active_days_of_week,
                longest_streak, current_streak, last_active_date,
                peak_hours_activity, off_peak_activity)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                stats.staff_id, stats.active_days,
                json.dumps(dict(stats.active_hours)),
                json.dumps(dict(stats.active_days_of_week)),
                stats.longest_streak, stats.current_streak,
                stats.last_active_date.isoformat() if stats.last_active_date else None,
                stats.peak_hours_activity, stats.off_peak_activity,
            ),
        )
        await self.pool.commit()

    # ─── Consistency stats read ───────────────────────────────────────────────

    async def get_consistency_stats(self, staff_id: int) -> Optional[ConsistencyStats]:
        row = await (await self.pool.execute(
            "SELECT * FROM consistency_stats WHERE staff_id = ?", (staff_id,)
        )).fetchone()
        if not row:
            return None
        from collections import defaultdict as _dd
        stats = ConsistencyStats(
            staff_id=staff_id,
            active_days=row["active_days"],
            longest_streak=row["longest_streak"],
            current_streak=row["current_streak"],
            peak_hours_activity=row["peak_hours_activity"],
            off_peak_activity=row["off_peak_activity"] if "off_peak_activity" in row.keys() else 0,
            last_active_date=datetime.fromisoformat(row["last_active_date"]) if row["last_active_date"] else None,
        )
        stats.active_hours        = _dd(int, json.loads(row["active_hours"] or "{}"))
        stats.active_days_of_week = _dd(int, json.loads(row["active_days_of_week"] or "{}"))
        # Calculate 28-day activity pattern for weekly consistency — single bulk query
        now_dt  = discord.utils.utcnow()
        cutoff_28 = (now_dt - timedelta(days=28)).isoformat()
        active_days_rows = await (await self.pool.execute(
            "SELECT DATE(timestamp) as day FROM staff_messages "
            "WHERE staff_id=? AND timestamp>? GROUP BY DATE(timestamp)",
            (staff_id, cutoff_28)
        )).fetchall()
        active_day_set = {r["day"] for r in active_days_rows}
        weekly_data = []
        for day_offset in range(27, -1, -1):
            day_str = (now_dt - timedelta(days=day_offset)).strftime("%Y-%m-%d")
            weekly_data.append(1 if day_str in active_day_set else 0)
        stats.weekly_active_days = weekly_data
        return stats

    # ─── Staff notes ──────────────────────────────────────────────────────────

    async def add_staff_note(self, note: StaffNote) -> int:
        cursor = await self.pool.execute(
            "INSERT INTO staff_notes (staff_id, author_id, note, created_at, is_positive) VALUES (?,?,?,?,?)",
            (note.staff_id, note.author_id, note.note, note.created_at.isoformat(), int(note.is_positive)),
        )
        await self.pool.commit()
        return cursor.lastrowid

    async def get_staff_notes(self, staff_id: int, limit: int = 10) -> List[StaffNote]:
        rows = await (await self.pool.execute(
            "SELECT * FROM staff_notes WHERE staff_id = ? ORDER BY created_at DESC LIMIT ?",
            (staff_id, limit),
        )).fetchall()
        return [
            StaffNote(
                id=row["id"], staff_id=row["staff_id"], author_id=row["author_id"],
                note=row["note"], created_at=datetime.fromisoformat(row["created_at"]),
                is_positive=bool(row["is_positive"]),
            )
            for row in rows
        ]

    async def delete_staff_note(self, note_id: int, requester_id: int) -> bool:
        row = await (await self.pool.execute(
            "SELECT id FROM staff_notes WHERE id = ?", (note_id,)
        )).fetchone()
        if not row:
            return False
        await self.pool.execute("DELETE FROM staff_notes WHERE id = ?", (note_id,))
        await self.pool.commit()
        return True

    # ─── Score weights ────────────────────────────────────────────────────────

    async def get_score_weights(self, guild_id: int) -> Dict[str, float]:
        rows = await (await self.pool.execute(
            "SELECT weight_key, weight_value FROM custom_score_weights WHERE guild_id = ?", (guild_id,)
        )).fetchall()
        weights = dict(SCORE_WEIGHTS)
        for row in rows:
            weights[row["weight_key"]] = row["weight_value"]
        return weights

    async def set_score_weight(self, guild_id: int, key: str, value: float, updated_by: int):
        await self.pool.execute(
            """INSERT INTO custom_score_weights (guild_id, weight_key, weight_value, updated_by, updated_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(guild_id, weight_key) DO UPDATE SET weight_value=excluded.weight_value,
               updated_by=excluded.updated_by, updated_at=excluded.updated_at""",
            (guild_id, key, value, updated_by, discord.utils.utcnow().isoformat()),
        )
        await self.pool.commit()

    async def reset_score_weights(self, guild_id: int):
        await self.pool.execute("DELETE FROM custom_score_weights WHERE guild_id = ?", (guild_id,))
        await self.pool.commit()

    # ─── Staff stats query ────────────────────────────────────────────────────

    async def get_staff_stats(
        self,
        staff_id: int,
        days: int = 30,
        weights: Optional[Dict[str, float]] = None,
    ) -> StaffStats:
        cutoff = (discord.utils.utcnow() - timedelta(days=days)).isoformat()
        stats = StaffStats(staff_id=staff_id, _weights=weights)

        row = await (await self.pool.execute(
            "SELECT COUNT(*) as cnt, COALESCE(AVG(word_count), 0) as avg_wc "
            "FROM staff_messages WHERE staff_id=? AND timestamp>?",
            (staff_id, cutoff),
        )).fetchone()
        stats.total_messages   = row["cnt"] or 0
        stats.avg_message_words = round(row["avg_wc"] or 0.0, 1)

        # help_events / unique_members_helped no longer produced; kept as 0
        # for backward-compat with old DB rows and export

        row = await (await self.pool.execute(
            "SELECT COUNT(*) as c, COALESCE(SUM(severity),0) as sev "
            "FROM moderation_actions WHERE moderator_id=? AND timestamp>?",
            (staff_id, cutoff),
        )).fetchone()
        stats.moderation_actions      = row["c"] or 0
        stats.moderation_severity_sum = round(row["sev"] or 0.0, 2)

        # Mod action breakdown by type
        breakdown_rows = await (await self.pool.execute(
            "SELECT action_type, COUNT(*) as c FROM moderation_actions "
            "WHERE moderator_id=? AND timestamp>? GROUP BY action_type",
            (staff_id, cutoff),
        )).fetchall()
        stats.mod_action_breakdown = {row["action_type"]: row["c"] for row in breakdown_rows}

        row = await (await self.pool.execute(
            "SELECT COALESCE(SUM(duration_minutes),0)/60.0 as hrs "
            "FROM vc_sessions WHERE staff_id=? AND join_time>?",
            (staff_id, cutoff),
        )).fetchone()
        stats.vc_hours = round(row["hrs"] or 0.0, 2)

        row = await (await self.pool.execute(
            "SELECT COUNT(*) as c FROM reports WHERE staff_id=? AND timestamp>?",
            (staff_id, cutoff),
        )).fetchone()
        stats.reports_filed = row["c"] or 0

        row = await (await self.pool.execute(
            "SELECT COUNT(*) as c FROM bumps WHERE staff_id=? AND timestamp>?",
            (staff_id, cutoff),
        )).fetchone()
        stats.bumps_performed = row["c"] or 0

        # conversation_sessions removed from scoring (surfaces as 0; old data preserved)

        row = await (await self.pool.execute(
            "SELECT COUNT(*) as c FROM reactions WHERE staff_id=? AND timestamp>?",
            (staff_id, cutoff),
        )).fetchone()
        stats.reactions_given = row["c"] or 0

        row = await (await self.pool.execute(
            "SELECT COUNT(*) as c FROM thread_events WHERE staff_id=? AND event_type='created' AND timestamp>?",
            (staff_id, cutoff),
        )).fetchone()
        stats.threads_created = row["c"] or 0

        row = await (await self.pool.execute(
            "SELECT COUNT(*) as c FROM response_times WHERE staff_id=? AND response_seconds<=? AND timestamp>?",
            (staff_id, 300, cutoff),
        )).fetchone()
        stats.fast_responses = row["c"] or 0

        row = await (await self.pool.execute(
            "SELECT COALESCE(AVG(response_seconds),0) as avg FROM response_times WHERE staff_id=? AND timestamp>?",
            (staff_id, cutoff),
        )).fetchone()
        stats.avg_response_seconds = round(row["avg"] or 0.0, 1)

        row = await (await self.pool.execute(
            "SELECT COUNT(*) as c FROM first_responses WHERE staff_id=? AND timestamp>?",
            (staff_id, cutoff),
        )).fetchone()
        stats.first_responses = row["c"] or 0

        row = await (await self.pool.execute(
            "SELECT COUNT(*) as c, COALESCE(AVG(quality_score),0) as avg_qs "
            "FROM quality_events WHERE staff_id=? AND timestamp>?",
            (staff_id, cutoff),
        )).fetchone()
        stats.quality_events    = row["c"] or 0
        stats.avg_quality_score = round(row["avg_qs"] or 0.0, 2)

        row = await (await self.pool.execute(
            "SELECT COUNT(DISTINCT DATE(timestamp)) as c "
            "FROM staff_messages WHERE staff_id=? AND timestamp>?",
            (staff_id, cutoff),
        )).fetchone()
        stats.active_days = row["c"] or 0

        row = await (await self.pool.execute(
            "SELECT COUNT(DISTINCT channel_id) as c "
            "FROM staff_messages WHERE staff_id=? AND timestamp>?",
            (staff_id, cutoff),
        )).fetchone()
        stats.channels_participated = row["c"] or 0

        # ── Engagement / community-building ──────────────────────────────────
        row = await (await self.pool.execute(
            "SELECT COUNT(*) as c FROM engagement_events "
            "WHERE staff_id=? AND event_type='dead_chat_revival' AND timestamp>?",
            (staff_id, cutoff),
        )).fetchone()
        stats.dead_chat_revivals = row["c"] or 0

        row = await (await self.pool.execute(
            "SELECT COUNT(*) as c FROM engagement_events "
            "WHERE staff_id=? AND event_type='conversation_starter' AND timestamp>?",
            (staff_id, cutoff),
        )).fetchone()
        stats.conversation_starters = row["c"] or 0

        row = await (await self.pool.execute(
            "SELECT COUNT(*) as c FROM engagement_events "
            "WHERE staff_id=? AND event_type='new_member_greeting' AND timestamp>?",
            (staff_id, cutoff),
        )).fetchone()
        stats.new_member_greetings = row["c"] or 0

        row = await (await self.pool.execute(
            "SELECT COUNT(*) as c FROM reactions_received "
            "WHERE staff_id=? AND timestamp>?",
            (staff_id, cutoff),
        )).fetchone()
        stats.reactions_received = row["c"] or 0
        row = await (await self.pool.execute(
            "SELECT COUNT(*) as c FROM engagement_events "
            "WHERE staff_id=? AND event_type='new_member_chat' AND timestamp>?",
            (staff_id, cutoff),
        )).fetchone()
        stats.new_member_chats = row["c"] or 0

        # Unique people talked with (interactions, non-staff)
        row = await (await self.pool.execute(
            "SELECT COUNT(DISTINCT user_id) as c FROM interactions WHERE staff_id=? AND timestamp>?",
            (staff_id, cutoff),
        )).fetchone()
        stats.unique_talked_with = row["c"] or 0

        # Ping responses
        row = await (await self.pool.execute(
            "SELECT COUNT(*) as c FROM engagement_events "
            "WHERE staff_id=? AND event_type='ping_response' AND timestamp>?",
            (staff_id, cutoff),
        )).fetchone()
        stats.ping_responses = row["c"] or 0

        # VC people bonus: sum of (peak_occupancy - 1) across sessions with others present
        row = await (await self.pool.execute(
            "SELECT COALESCE(SUM(CASE WHEN peak_occupancy > 1 THEN peak_occupancy - 1 ELSE 0 END), 0) as bonus, "
            "COUNT(CASE WHEN peak_occupancy > 1 THEN 1 END) as sessions_with_others "
            "FROM vc_sessions WHERE staff_id=? AND join_time>?",
            (staff_id, cutoff),
        )).fetchone()
        stats.vc_people_bonus    = round((row["bonus"] or 0) * 0.5, 2)
        stats.vc_people_sessions = row["sessions_with_others"] or 0

        # Unique new members talked to
        row = await (await self.pool.execute(
            "SELECT COUNT(DISTINCT user_id) as c FROM engagement_events "
            "WHERE staff_id=? AND event_type='new_member_chat' AND timestamp>?",
            (staff_id, cutoff),
        )).fetchone()
        stats.unique_newbies_chatted = row["c"] or 0

        return stats

    # ─── Leaderboard / rank helpers ───────────────────────────────────────────

    async def get_cached_staff_stats(
        self,
        guild_id: int,
        staff_ids: List[int],
        days: int,
        weights: Dict,
    ) -> List[tuple]:
        """
        Return [(staff_id, StaffStats), ...] using an in-process cache.
        Dashboard and weekly report call this instead of querying every member
        individually on each refresh — drops ~20 DB queries to 1 cache hit.
        Cache TTL is _DASHBOARD_CACHE_TTL seconds.
        """
        cache_key = (guild_id, days)
        cached = self._dashboard_cache.get(cache_key)
        if cached:
            ts, data = cached
            age = (discord.utils.utcnow() - ts).total_seconds()
            if age < self._DASHBOARD_CACHE_TTL:
                return data

        result = []
        for sid in staff_ids:
            s = await self.get_staff_stats(sid, days, weights)
            result.append((sid, s))
        self._dashboard_cache[cache_key] = (discord.utils.utcnow(), result)
        return result

    def invalidate_dashboard_cache(self, guild_id: int = None):
        """Call after any write that should immediately reflect on the dashboard."""
        if guild_id:
            self._dashboard_cache = {k: v for k, v in self._dashboard_cache.items() if k[0] != guild_id}
        else:
            self._dashboard_cache.clear()

    async def get_staff_rank_position(
        self, staff_id: int, all_staff_ids: List[int], days: int = 30
    ) -> Tuple[int, int]:
        if not all_staff_ids:
            return 1, 0
        weights = {}
        scores = []
        for sid in all_staff_ids:
            s = await self.get_staff_stats(sid, days, weights)
            scores.append((sid, s.engagement_score))
        scores.sort(key=lambda x: x[1], reverse=True)
        for rank, (sid, _) in enumerate(scores, 1):
            if sid == staff_id:
                return rank, len(scores)
        return len(scores), len(scores)

    async def get_leaderboard(
        self,
        metric: str,
        days: int = 30,
        limit: int = 15,
    ) -> List[Tuple[int, float]]:
        """
        Return [(staff_id, value), ...] sorted descending for the given metric.

        Supported metric values (matching the choices in staffleaderboard):
          help, new_members, moderation, reports, messages,
          vc_hours, reactions, threads, first_response, quality,
          dead_chat_revivals, conversation_starters, new_member_greetings,
          reactions_received
        """
        cutoff = (discord.utils.utcnow() - timedelta(days=days)).isoformat()

        _Q: Dict[str, Tuple[str, tuple]] = {
            "help": (
                "SELECT helper_id AS staff_id, COUNT(*) AS val "
                "FROM help_events WHERE timestamp>? "
                "GROUP BY helper_id ORDER BY val DESC LIMIT ?",
                (cutoff, limit),
            ),
            "new_members": (
                "SELECT helper_id AS staff_id, COUNT(*) AS val "
                "FROM help_events WHERE is_new_member=1 AND timestamp>? "
                "GROUP BY helper_id ORDER BY val DESC LIMIT ?",
                (cutoff, limit),
            ),
            "moderation": (
                "SELECT moderator_id AS staff_id, COALESCE(SUM(severity), 0) AS val "
                "FROM moderation_actions WHERE timestamp>? "
                "GROUP BY moderator_id ORDER BY val DESC LIMIT ?",
                (cutoff, limit),
            ),
            "reports": (
                "SELECT staff_id, COUNT(*) AS val "
                "FROM reports WHERE timestamp>? "
                "GROUP BY staff_id ORDER BY val DESC LIMIT ?",
                (cutoff, limit),
            ),
            "messages": (
                "SELECT staff_id, COUNT(*) AS val "
                "FROM staff_messages WHERE timestamp>? "
                "GROUP BY staff_id ORDER BY val DESC LIMIT ?",
                (cutoff, limit),
            ),
            "vc_hours": (
                "SELECT staff_id, COALESCE(SUM(duration_minutes), 0) / 60.0 AS val "
                "FROM vc_sessions WHERE join_time>? "
                "GROUP BY staff_id ORDER BY val DESC LIMIT ?",
                (cutoff, limit),
            ),
            "reactions": (
                "SELECT staff_id, COUNT(*) AS val "
                "FROM reactions WHERE timestamp>? "
                "GROUP BY staff_id ORDER BY val DESC LIMIT ?",
                (cutoff, limit),
            ),
            "threads": (
                "SELECT staff_id, COUNT(*) AS val "
                "FROM thread_events WHERE event_type='created' AND timestamp>? "
                "GROUP BY staff_id ORDER BY val DESC LIMIT ?",
                (cutoff, limit),
            ),
            "first_response": (
                "SELECT staff_id, COUNT(*) AS val "
                "FROM first_responses WHERE timestamp>? "
                "GROUP BY staff_id ORDER BY val DESC LIMIT ?",
                (cutoff, limit),
            ),
            "quality": (
                "SELECT staff_id, COALESCE(AVG(quality_score), 0) AS val "
                "FROM quality_events WHERE timestamp>? "
                "GROUP BY staff_id ORDER BY val DESC LIMIT ?",
                (cutoff, limit),
            ),
            "dead_chat_revivals": (
                "SELECT staff_id, COUNT(*) AS val "
                "FROM engagement_events WHERE event_type='dead_chat_revival' AND timestamp>? "
                "GROUP BY staff_id ORDER BY val DESC LIMIT ?",
                (cutoff, limit),
            ),
            "conversation_starters": (
                "SELECT staff_id, COUNT(*) AS val "
                "FROM engagement_events WHERE event_type='conversation_starter' AND timestamp>? "
                "GROUP BY staff_id ORDER BY val DESC LIMIT ?",
                (cutoff, limit),
            ),
            "new_member_greetings": (
                "SELECT staff_id, COUNT(*) AS val "
                "FROM engagement_events WHERE event_type='new_member_greeting' AND timestamp>? "
                "GROUP BY staff_id ORDER BY val DESC LIMIT ?",
                (cutoff, limit),
            ),
            "reactions_received": (
                "SELECT staff_id, COUNT(*) AS val "
                "FROM reactions_received WHERE timestamp>? "
                "GROUP BY staff_id ORDER BY val DESC LIMIT ?",
                (cutoff, limit),
            ),
            "bumps": (
                "SELECT staff_id, COUNT(*) AS val "
                "FROM bumps WHERE timestamp>? "
                "GROUP BY staff_id ORDER BY val DESC LIMIT ?",
                (cutoff, limit),
            ),
            "unique_talked_with": (
                "SELECT staff_id, COUNT(DISTINCT user_id) AS val "
                "FROM interactions WHERE timestamp>? "
                "GROUP BY staff_id ORDER BY val DESC LIMIT ?",
                (cutoff, limit),
            ),
            "ping_responses": (
                "SELECT staff_id, COUNT(*) AS val "
                "FROM engagement_events WHERE event_type='ping_response' AND timestamp>? "
                "GROUP BY staff_id ORDER BY val DESC LIMIT ?",
                (cutoff, limit),
            ),
        }

        entry = _Q.get(metric)
        if not entry:
            logger.warning("get_leaderboard: unknown metric %r", metric)
            return []

        sql, params = entry
        rows = await (await self.pool.execute(sql, params)).fetchall()
        return [(row["staff_id"], round(float(row["val"]), 2)) for row in rows]

    async def get_inactive_staff(self, staff_ids: List[int], days: int) -> List[int]:
        if not staff_ids:
            return []
        cutoff = (discord.utils.utcnow() - timedelta(days=days)).isoformat()
        ph = ",".join("?" * len(staff_ids))
        rows = await (await self.pool.execute(
            f"SELECT DISTINCT staff_id FROM staff_messages WHERE staff_id IN ({ph}) AND timestamp>?",
            (*staff_ids, cutoff),
        )).fetchall()
        active = {row["staff_id"] for row in rows}
        return [sid for sid in staff_ids if sid not in active]

    async def get_trend_comparison(self, staff_id: int) -> Dict[str, Any]:
        """Get trend comparison between current 30d and previous 30d for all metrics."""
        now = discord.utils.utcnow()
        cur_start = (now - timedelta(days=30)).isoformat()
        prev_start = (now - timedelta(days=60)).isoformat()
        prev_end = cur_start
        
        async def _count(table: str, col: str, cutoff_start: str, cutoff_end: str, 
                        timestamp_col: str = "timestamp", extra_condition: str = "") -> int:
            """Base count helper with configurable timestamp column."""
            condition = f" AND {extra_condition}" if extra_condition else ""
            query = f"SELECT COUNT(*) as c FROM {table} WHERE {col}=? AND {timestamp_col}>? AND {timestamp_col}<=?{condition}"
            row = await (await self.pool.execute(
                query,
                (staff_id, cutoff_start, cutoff_end),
            )).fetchone()
            return row["c"] or 0
        
        async def _count_with_event_type(table: str, col: str, event_type: str, 
                                        cutoff_start: str, cutoff_end: str,
                                        timestamp_col: str = "timestamp") -> int:
            """Count records with specific event_type."""
            query = f"SELECT COUNT(*) as c FROM {table} WHERE {col}=? AND event_type=? AND {timestamp_col}>? AND {timestamp_col}<=?"
            row = await (await self.pool.execute(
                query,
                (staff_id, event_type, cutoff_start, cutoff_end),
            )).fetchone()
            return row["c"] or 0
        
        async def _count_with_flag(table: str, col: str, flag_col: str, flag_value: int,
                                    cutoff_start: str, cutoff_end: str,
                                    timestamp_col: str = "timestamp") -> int:
            """Count records with a boolean flag."""
            query = f"SELECT COUNT(*) as c FROM {table} WHERE {col}=? AND {flag_col}=? AND {timestamp_col}>? AND {timestamp_col}<=?"
            row = await (await self.pool.execute(
                query,
                (staff_id, flag_value, cutoff_start, cutoff_end),
            )).fetchone()
            return row["c"] or 0
        
        async def _sum_duration(table: str, col: str, cutoff_start: str, cutoff_end: str) -> float:
            query = f"SELECT COALESCE(SUM(duration_minutes),0)/60.0 as hrs FROM {table} WHERE {col}=? AND join_time>? AND join_time<=?"
            row = await (await self.pool.execute(
                query,
                (staff_id, cutoff_start, cutoff_end),
            )).fetchone()
            return round(row["hrs"] or 0.0, 2)
        
        async def _avg_response(table: str, col: str, cutoff_start: str, cutoff_end: str) -> float:
            query = f"SELECT COALESCE(AVG(response_seconds),0) as avg FROM {table} WHERE {col}=? AND timestamp>? AND timestamp<=?"
            row = await (await self.pool.execute(
                query,
                (staff_id, cutoff_start, cutoff_end),
            )).fetchone()
            return round(row["avg"] or 0.0, 1)
        
        async def _avg_quality(table: str, col: str, cutoff_start: str, cutoff_end: str) -> float:
            query = f"SELECT COALESCE(AVG(quality_score),0) as avg FROM {table} WHERE {col}=? AND timestamp>? AND timestamp<=?"
            row = await (await self.pool.execute(
                query,
                (staff_id, cutoff_start, cutoff_end),
            )).fetchone()
            return round(row["avg"] or 0.0, 2)
        
        async def _count_fast_responses(table: str, col: str, cutoff_start: str, cutoff_end: str) -> int:
            """Count responses ≤5 minutes."""
            query = f"SELECT COUNT(*) as c FROM {table} WHERE {col}=? AND response_seconds<=300 AND timestamp>? AND timestamp<=?"
            row = await (await self.pool.execute(
                query,
                (staff_id, cutoff_start, cutoff_end),
            )).fetchone()
            return row["c"] or 0
        
        async def _count_threads_created(table: str, col: str, cutoff_start: str, cutoff_end: str) -> int:
            """Count threads created (event_type='created')."""
            query = f"SELECT COUNT(*) as c FROM {table} WHERE {col}=? AND event_type='created' AND timestamp>? AND timestamp<=?"
            row = await (await self.pool.execute(
                query,
                (staff_id, cutoff_start, cutoff_end),
            )).fetchone()
            return row["c"] or 0
        
        
        return {
            # Messages
            "messages": {
                "current": await _count("staff_messages", "staff_id", cur_start, now.isoformat()),
                "previous": await _count("staff_messages", "staff_id", prev_start, prev_end),
            },
            # help_events removed — no longer produced; kept as zeroes
            # Moderation actions (count)
            "moderation": {
                "current": await _count("moderation_actions", "moderator_id", cur_start, now.isoformat()),
                "previous": await _count("moderation_actions", "moderator_id", prev_start, prev_end),
            },
            # Moderation severity (weighted sum)
            "moderation_severity": {
                "current": await self._get_mod_severity_sum(staff_id, cur_start, now.isoformat()),
                "previous": await self._get_mod_severity_sum(staff_id, prev_start, prev_end),
            },
            # Reports
            "reports": {
                "current": await _count("reports", "staff_id", cur_start, now.isoformat()),
                "previous": await _count("reports", "staff_id", prev_start, prev_end),
            },
            # Bumps
            "bumps": {
                "current": await _count("bumps", "staff_id", cur_start, now.isoformat()),
                "previous": await _count("bumps", "staff_id", prev_start, prev_end),
            },
            # VC hours
            "vc_hours": {
                "current": await _sum_duration("vc_sessions", "staff_id", cur_start, now.isoformat()),
                "previous": await _sum_duration("vc_sessions", "staff_id", prev_start, prev_end),
            },
            # conversation_sessions removed — no longer produced; kept as zeroes
            # Reactions
            "reactions": {
                "current": await _count("reactions", "staff_id", cur_start, now.isoformat()),
                "previous": await _count("reactions", "staff_id", prev_start, prev_end),
            },
            # Threads created
            "threads": {
                "current": await _count_threads_created("thread_events", "staff_id", cur_start, now.isoformat()),
                "previous": await _count_threads_created("thread_events", "staff_id", prev_start, prev_end),
            },
            # Fast responses (≤5 min)
            "fast_responses": {
                "current": await _count_fast_responses("response_times", "staff_id", cur_start, now.isoformat()),
                "previous": await _count_fast_responses("response_times", "staff_id", prev_start, prev_end),
            },
            # First responses
            "first_response": {
                "current": await _count("first_responses", "staff_id", cur_start, now.isoformat()),
                "previous": await _count("first_responses", "staff_id", prev_start, prev_end),
            },
            # Quality events
            "quality": {
                "current": await _count("quality_events", "staff_id", cur_start, now.isoformat()),
                "previous": await _count("quality_events", "staff_id", prev_start, prev_end),
            },
            # Average quality score
            "quality_score": {
                "current": await _avg_quality("quality_events", "staff_id", cur_start, now.isoformat()),
                "previous": await _avg_quality("quality_events", "staff_id", prev_start, prev_end),
            },
            # Average response time
            "response_time": {
                "current": await _avg_response("response_times", "staff_id", cur_start, now.isoformat()),
                "previous": await _avg_response("response_times", "staff_id", prev_start, prev_end),
            },
            # Unique members helped - FIXED: using the regular method
            "unique_members": {
                "current": await self._count_unique_members(staff_id, cur_start, now.isoformat()),
                "previous": await self._count_unique_members(staff_id, prev_start, prev_end),
            },
            # Dead chat revivals
            "dead_chat_revivals": {
                "current": await _count_with_event_type("engagement_events", "staff_id", "dead_chat_revival", cur_start, now.isoformat()),
                "previous": await _count_with_event_type("engagement_events", "staff_id", "dead_chat_revival", prev_start, prev_end),
            },
            # Conversation starters
            "conversation_starters": {
                "current": await _count_with_event_type("engagement_events", "staff_id", "conversation_starter", cur_start, now.isoformat()),
                "previous": await _count_with_event_type("engagement_events", "staff_id", "conversation_starter", prev_start, prev_end),
            },
            # New member greetings
            "new_member_greetings": {
                "current": await _count_with_event_type("engagement_events", "staff_id", "new_member_greeting", cur_start, now.isoformat()),
                "previous": await _count_with_event_type("engagement_events", "staff_id", "new_member_greeting", prev_start, prev_end),
            },
            # Reactions received
            "reactions_received": {
                "current": await _count("reactions_received", "staff_id", cur_start, now.isoformat()),
                "previous": await _count("reactions_received", "staff_id", prev_start, prev_end),
            },
            # Ping responses
            "ping_responses": {
                "current": await _count_with_event_type("engagement_events", "staff_id", "ping_response", cur_start, now.isoformat()),
                "previous": await _count_with_event_type("engagement_events", "staff_id", "ping_response", prev_start, prev_end),
            },
            # Unique talked with
            "unique_talked_with": {
                "current": await self._count_unique_members(staff_id, cur_start, now.isoformat()),
                "previous": await self._count_unique_members(staff_id, prev_start, prev_end),
            },
            # VC hours trend
            "vc_hours": {
                "current": await _sum_duration("vc_sessions", "staff_id", cur_start, now.isoformat()),
                "previous": await _sum_duration("vc_sessions", "staff_id", prev_start, prev_end),
            },
        }

    async def _get_mod_severity_sum(self, staff_id: int, cutoff_start: str, cutoff_end: str) -> float:
        """Helper to get weighted moderation severity sum."""
        row = await (await self.pool.execute(
            "SELECT COALESCE(SUM(severity), 0) as sev "
            "FROM moderation_actions WHERE moderator_id=? AND timestamp>? AND timestamp<=?",
            (staff_id, cutoff_start, cutoff_end),
        )).fetchone()
        return round(row["sev"] or 0.0, 2)

    async def _count_unique_members(self, staff_id: int, cutoff_start: str, cutoff_end: str) -> int:
        """Helper to count unique members helped."""
        row = await (await self.pool.execute(
            "SELECT COUNT(DISTINCT helped_id) as c "
            "FROM help_events WHERE helper_id=? AND timestamp>? AND timestamp<=?",
            (staff_id, cutoff_start, cutoff_end),
        )).fetchone()
        return row["c"] or 0

    async def get_server_health(self, staff_ids: List[int], days: int) -> Dict[str, Any]:
        if not staff_ids:
            return {
                "total_staff": 0, "active_staff": 0, "total_messages": 0,
                "total_help": 0, "total_moderation": 0, "new_members_helped": 0,
                "avg_response_secs": 0.0, "first_responses": 0, "vc_hours": 0.0,
            }
        cutoff = (discord.utils.utcnow() - timedelta(days=days)).isoformat()
        ph = ",".join("?" * len(staff_ids))

        total_msg  = await (await self.pool.execute(
            f"SELECT COUNT(*) as c FROM staff_messages WHERE staff_id IN ({ph}) AND timestamp>?",
            (*staff_ids, cutoff),
        )).fetchone()
        total_help_c = 0  # help_events no longer produced
        total_mod  = await (await self.pool.execute(
            f"SELECT COUNT(*) as c FROM moderation_actions WHERE moderator_id IN ({ph}) AND timestamp>?",
            (*staff_ids, cutoff),
        )).fetchone()
        total_new_c  = 0  # help_events no longer produced
        avg_rt     = await (await self.pool.execute(
            f"SELECT COALESCE(AVG(response_seconds),0) as avg FROM response_times WHERE staff_id IN ({ph}) AND timestamp>?",
            (*staff_ids, cutoff),
        )).fetchone()
        total_fr   = await (await self.pool.execute(
            f"SELECT COUNT(*) as c FROM first_responses WHERE staff_id IN ({ph}) AND timestamp>?",
            (*staff_ids, cutoff),
        )).fetchone()
        total_vc   = await (await self.pool.execute(
            f"SELECT COALESCE(SUM(duration_minutes),0)/60.0 as hrs FROM vc_sessions WHERE staff_id IN ({ph}) AND join_time>?",
            (*staff_ids, cutoff),
        )).fetchone()
        active_rows = await (await self.pool.execute(
            f"SELECT COUNT(DISTINCT staff_id) as c FROM staff_messages WHERE staff_id IN ({ph}) AND timestamp>?",
            (*staff_ids, cutoff),
        )).fetchall()

        total_bumps = await (await self.pool.execute(
            f"SELECT COUNT(*) as c FROM bumps WHERE staff_id IN ({ph}) AND timestamp>?",
            (*staff_ids, cutoff),
        )).fetchone()
        ping_resp = await (await self.pool.execute(
            f"SELECT COUNT(*) as c FROM engagement_events WHERE staff_id IN ({ph}) AND event_type='ping_response' AND timestamp>?",
            (*staff_ids, cutoff),
        )).fetchone()
        unique_talked = await (await self.pool.execute(
            f"SELECT COUNT(DISTINCT user_id) as c FROM interactions WHERE staff_id IN ({ph}) AND timestamp>?",
            (*staff_ids, cutoff),
        )).fetchone()

        return {
            "total_staff":        len(staff_ids),
            "active_staff":       active_rows[0]["c"] if active_rows else 0,
            "total_messages":     total_msg["c"]   or 0,
            "total_help":         total_help_c,
            "total_moderation":   total_mod["c"]   or 0,
            "new_members_helped": total_new_c,
            "avg_response_secs":  round(avg_rt["avg"] or 0.0, 1),
            "first_responses":    total_fr["c"]    or 0,
            "vc_hours":           round(total_vc["hrs"] or 0.0, 1),
            "total_bumps":        total_bumps["c"] or 0,
            "ping_responses":     ping_resp["c"]   or 0,
            "unique_talked_with": unique_talked["c"] or 0,
        }

    # ─── Drop-off detection ──────────────────────────────────────────────────────

    async def get_dropoff_staff(
        self, staff_ids: List[int], lookback_weeks: int = 4
    ) -> List[Dict]:
        """
        Return staff whose message count this week is <50% of their N-week
        rolling average, only flagging when the prior baseline is meaningful (>5).
        Uses lightweight message-count queries — does NOT call get_staff_stats.
        """
        if not staff_ids:
            return []
        now    = discord.utils.utcnow()
        result = []
        ph     = ",".join("?" * len(staff_ids))

        # Fetch all week slices in one query per week instead of per-staff
        week_data: Dict[int, List[int]] = {sid: [] for sid in staff_ids}
        for w in range(lookback_weeks):
            w_end   = now - timedelta(weeks=w)
            w_start = w_end - timedelta(weeks=1)
            rows = await (await self.pool.execute(
                f"SELECT staff_id, COUNT(*) as c FROM staff_messages "
                f"WHERE staff_id IN ({ph}) AND timestamp>? AND timestamp<=? "
                f"GROUP BY staff_id",
                (*staff_ids, w_start.isoformat(), w_end.isoformat())
            )).fetchall()
            counts = {row["staff_id"]: row["c"] for row in rows}
            for sid in staff_ids:
                week_data[sid].append(counts.get(sid, 0))

        for sid, weeks in week_data.items():
            if not weeks:
                continue
            this_week = weeks[0]
            prior     = weeks[1:]
            avg_prior = sum(prior) / max(len(prior), 1)
            # Only flag if: had meaningful activity before AND dropped significantly
            if avg_prior > 5 and this_week < avg_prior * 0.5:
                result.append({
                    "staff_id": sid,
                    "this_week": this_week,
                    "avg_prev":  round(avg_prior, 1),
                    "drop_pct":  round((1 - this_week / avg_prior) * 100),
                })

        result.sort(key=lambda x: x["drop_pct"], reverse=True)
        return result

    async def get_mod_action_breakdown_server(
        self, staff_ids: List[int], days: int
    ) -> Dict[str, int]:
        """Server-wide mod action breakdown by type for the overview embed."""
        if not staff_ids:
            return {}
        cutoff = (discord.utils.utcnow() - timedelta(days=days)).isoformat()
        ph = ",".join("?" * len(staff_ids))
        rows = await (await self.pool.execute(
            f"SELECT action_type, COUNT(*) as c FROM moderation_actions "
            f"WHERE moderator_id IN ({ph}) AND timestamp>? GROUP BY action_type",
            (*staff_ids, cutoff)
        )).fetchall()
        return {row["action_type"]: row["c"] for row in rows}

    # ─── Export ───────────────────────────────────────────────────────────────

    async def export_staff_data(self, staff_id: int, days: int) -> Dict[str, Any]:
        cutoff = (discord.utils.utcnow() - timedelta(days=days)).isoformat()
        data: Dict[str, Any] = {
            "staff_id": staff_id,
            "exported_at": discord.utils.utcnow().isoformat(),
            "days": days,
        }

        async def fetch(table: str, col: str, ts_col: str = "timestamp"):
            rows = await (await self.pool.execute(
                f"SELECT * FROM {table} WHERE {col}=? AND {ts_col}>?", (staff_id, cutoff)
            )).fetchall()
            return [dict(r) for r in rows]

        data["messages"]       = await fetch("staff_messages",      "staff_id")
        data["interactions"]   = await fetch("interactions",        "staff_id")
        data["moderation"]     = await fetch("moderation_actions",  "moderator_id")
        data["voice"]          = await fetch("vc_sessions",         "staff_id", "join_time")
        data["reports"]        = await fetch("reports",             "staff_id")
        data["bumps"]          = await fetch("bumps",               "staff_id")
        data["role_pings"]     = await fetch("role_pings",          "staff_id")
        data["help_events"]    = [dict(r) for r in await (await self.pool.execute(
            "SELECT * FROM help_events WHERE helper_id=? AND timestamp>?", (staff_id, cutoff)
        )).fetchall()]
        data["reactions"]      = await fetch("reactions",           "staff_id")
        data["thread_events"]  = await fetch("thread_events",       "staff_id")
        data["response_times"] = await fetch("response_times",      "staff_id")
        data["quality_events"] = await fetch("quality_events",      "staff_id")
        data["first_responses"]= await fetch("first_responses",     "staff_id")
        data["engagement_events"]    = await fetch("engagement_events",  "staff_id")
        data["reactions_received"]   = await fetch("reactions_received", "staff_id")
        data["notes"]          = await fetch("staff_notes",         "staff_id", "created_at")
        return data

    # ─── Cleanup ──────────────────────────────────────────────────────────────

    async def _cleanup_old_data(self):
        while self._running:
            await asyncio.sleep(86400)
            cutoff = (discord.utils.utcnow() - timedelta(days=DB_RETENTION_DAYS)).isoformat()
            tables = [
                ("staff_messages",       "timestamp"),
                ("interactions",         "timestamp"),
                ("conversation_sessions","start_time"),
                ("moderation_actions",   "timestamp"),
                ("vc_sessions",          "join_time"),
                ("reports",              "timestamp"),
                ("bumps",                "timestamp"),
                ("role_pings",           "timestamp"),
                ("help_events",          "timestamp"),
                ("reactions",            "timestamp"),
                ("thread_events",        "timestamp"),
                ("response_times",       "timestamp"),
                ("quality_events",       "timestamp"),
                ("first_responses",      "timestamp"),
                ("engagement_events",    "timestamp"),
                ("reactions_received",   "timestamp"),
            ]
            for table, ts_col in tables:
                try:
                    await self.pool.execute(
                        f"DELETE FROM {table} WHERE {ts_col} < ?", (cutoff,)
                    )
                except Exception as e:
                    logger.error("Cleanup error (%s): %s", table, e)
            await self.pool.commit()
            logger.info("Cleaned data older than %d days", DB_RETENTION_DAYS)



    async def get_recent_activity(self, staff_ids: List[int], hours: int) -> Dict[int, List[Dict]]:
        """
        Get recent activity for a list of staff members.
        
        Args:
            staff_ids: List of staff member IDs
            hours: Number of hours to look back
        
        Returns:
            Dict mapping staff_id -> list of activity records
        """
        if not staff_ids:
            return {}
        
        cutoff = (discord.utils.utcnow() - timedelta(hours=hours)).isoformat()
        placeholders = ",".join("?" * len(staff_ids))
        
        activity: Dict[int, List[Dict]] = {}
        
        # Messages
        rows = await (await self.pool.execute(
            f"""
            SELECT staff_id, channel_id, timestamp, content, 'message' as type
            FROM staff_messages 
            WHERE staff_id IN ({placeholders}) AND timestamp > ?
            ORDER BY timestamp DESC
            """,
            (*staff_ids, cutoff)
        )).fetchall()
        
        for row in rows:
            sid = row["staff_id"]
            if sid not in activity:
                activity[sid] = []
            activity[sid].append({
                "type": "message",
                "channel_id": row["channel_id"],
                "timestamp": row["timestamp"],
                "details": truncate_string(row["content"] or "", 50)
            })
        
        # Moderation actions
        rows = await (await self.pool.execute(
            f"""
            SELECT moderator_id as staff_id, action_type, timestamp, target_id, 'moderation' as type
            FROM moderation_actions 
            WHERE moderator_id IN ({placeholders}) AND timestamp > ?
            ORDER BY timestamp DESC
            """,
            (*staff_ids, cutoff)
        )).fetchall()
        
        for row in rows:
            sid = row["staff_id"]
            if sid not in activity:
                activity[sid] = []
            severity = MODERATION_SEVERITY.get(row["action_type"], 1.0)
            activity[sid].append({
                "type": "moderation",
                "action_type": row["action_type"],
                "timestamp": row["timestamp"],
                "target_id": row["target_id"],
                "severity": severity,
                "details": f"{row['action_type']} (×{severity:.1f})"
            })
        
        # Voice sessions (recently started)
        rows = await (await self.pool.execute(
            f"""
            SELECT staff_id, channel_id, join_time as timestamp, 'voice' as type
            FROM vc_sessions 
            WHERE staff_id IN ({placeholders}) AND join_time > ?
            ORDER BY join_time DESC
            """,
            (*staff_ids, cutoff)
        )).fetchall()
        
        for row in rows:
            sid = row["staff_id"]
            if sid not in activity:
                activity[sid] = []
            activity[sid].append({
                "type": "voice",
                "channel_id": row["channel_id"],
                "timestamp": row["timestamp"],
            })
        
        # First responses
        rows = await (await self.pool.execute(
            f"""
            SELECT staff_id, user_id, channel_id, timestamp, 'first_response' as type
            FROM first_responses 
            WHERE staff_id IN ({placeholders}) AND timestamp > ?
            ORDER BY timestamp DESC
            """,
            (*staff_ids, cutoff)
        )).fetchall()
        
        for row in rows:
            sid = row["staff_id"]
            if sid not in activity:
                activity[sid] = []
            activity[sid].append({
                "type": "first_response",
                "channel_id": row["channel_id"],
                "timestamp": row["timestamp"],
                "user_id": row["user_id"],
            })
        
        # Sort each staff member's activities by timestamp (newest first)
        for sid in activity:
            activity[sid].sort(key=lambda x: x["timestamp"], reverse=True)
            # Limit to most recent 10 per staff member
            activity[sid] = activity[sid][:10]
        
        return activity

    async def has_notified_achievement(self, staff_id: int, key: str) -> bool:
        row = await (await self.pool.execute(
            "SELECT 1 FROM notified_achievements WHERE staff_id=? AND achievement=?",
            (staff_id, key)
        )).fetchone()
        return row is not None

    async def mark_achievement_notified(self, staff_id: int, key: str):
        await self.pool.execute(
            "INSERT OR IGNORE INTO notified_achievements (staff_id, achievement, notified_at) VALUES (?,?,?)",
            (staff_id, key, discord.utils.utcnow().isoformat())
        )
        await self.pool.commit()

    async def get_achievement_notified_at(self, staff_id: int, key: str) -> Optional[datetime]:
        """Return when a staff member was first notified of an achievement, or None."""
        row = await (await self.pool.execute(
            "SELECT notified_at FROM notified_achievements WHERE staff_id=? AND achievement=?",
            (staff_id, key)
        )).fetchone()
        if row and row["notified_at"]:
            return datetime.fromisoformat(row["notified_at"])
        return None

    # ─── Rank-up notification guard ───────────────────────────────────────────

    async def has_notified_rankup(self, staff_id: int, from_tier: str, to_tier: str) -> bool:
        """True if we already DM'd this exact transition today (UTC)."""
        today = discord.utils.utcnow().date().isoformat()
        key   = f"rankup_{from_tier}_{to_tier}_{today}"
        return await self.has_notified_achievement(staff_id, key)

    async def mark_rankup_notified(self, staff_id: int, from_tier: str, to_tier: str):
        today = discord.utils.utcnow().date().isoformat()
        key   = f"rankup_{from_tier}_{to_tier}_{today}"
        await self.mark_achievement_notified(staff_id, key)

    # ─── Pending moderation actions ──────────────────────────────────────────

    async def insert_pending_mod_action(
        self,
        moderator_id: int,
        target_id: int,
        action_type: str,
        reason: Optional[str],
        severity: float,
        guild_id: int,
        channel_id: int,
        message_id: int,
    ) -> int:
        cursor = await self.pool.execute(
            """INSERT INTO pending_mod_actions
               (moderator_id, target_id, action_type, reason, severity,
                detected_at, guild_id, channel_id, message_id, status)
               VALUES (?,?,?,?,?,?,?,?,?,'pending')""",
            (moderator_id, target_id, action_type, reason, severity,
             discord.utils.utcnow().isoformat(), guild_id, channel_id, message_id),
        )
        await self.pool.commit()
        return cursor.lastrowid

    async def set_pending_mod_review_msg(self, pending_id: int, review_msg_id: int):
        await self.pool.execute(
            "UPDATE pending_mod_actions SET review_msg_id=? WHERE id=?",
            (review_msg_id, pending_id),
        )
        await self.pool.commit()

    async def approve_pending_mod_action(self, pending_id: int) -> Optional[Dict]:
        row = await (await self.pool.execute(
            "SELECT * FROM pending_mod_actions WHERE id=? AND status='pending'", (pending_id,)
        )).fetchone()
        if not row:
            return None
        await self.pool.execute(
            "UPDATE pending_mod_actions SET status='approved' WHERE id=?", (pending_id,)
        )
        await self.pool.commit()
        return dict(row)

    async def reject_pending_mod_action(self, pending_id: int):
        await self.pool.execute(
            "UPDATE pending_mod_actions SET status='rejected' WHERE id=?", (pending_id,)
        )
        await self.pool.commit()

    async def get_pending_mod_actions(self, guild_id: int) -> List[Dict]:
        rows = await (await self.pool.execute(
            "SELECT * FROM pending_mod_actions WHERE guild_id=? AND status='pending' ORDER BY detected_at DESC",
            (guild_id,),
        )).fetchall()
        return [dict(r) for r in rows]

    # ─── Daily challenges ─────────────────────────────────────────────────────

    async def get_daily_challenge_progress(self, staff_id: int, date_str: str) -> Dict[str, Dict]:
        rows = await (await self.pool.execute(
            "SELECT challenge, value, completed FROM daily_challenge_progress WHERE staff_id=? AND date=?",
            (staff_id, date_str),
        )).fetchall()
        return {r["challenge"]: {"value": r["value"], "completed": bool(r["completed"])} for r in rows}

    async def upsert_daily_challenge_progress(
        self, staff_id: int, date_str: str, challenge: str, value: float, completed: bool
    ):
        await self.pool.execute(
            """INSERT INTO daily_challenge_progress (staff_id, challenge, date, value, completed)
               VALUES (?,?,?,?,?)
               ON CONFLICT(staff_id, challenge, date) DO UPDATE SET
                 value=excluded.value, completed=excluded.completed""",
            (staff_id, challenge, date_str, value, int(completed)),
        )
        await self.pool.commit()

    async def get_daily_challenge_completions(self, staff_id: int, date_str: str) -> int:
        row = await (await self.pool.execute(
            "SELECT COUNT(*) as c FROM daily_challenge_progress WHERE staff_id=? AND date=? AND completed=1",
            (staff_id, date_str),
        )).fetchone()
        return row["c"] or 0

    async def was_active_today(self, staff_id: int) -> bool:
        """Return True if the staff member has sent at least one message today (UTC)."""
        now     = discord.utils.utcnow()
        cutoff  = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        row = await (await self.pool.execute(
            "SELECT COUNT(*) as c FROM staff_messages WHERE staff_id=? AND timestamp>?",
            (staff_id, cutoff),
        )).fetchone()
        return (row["c"] or 0) > 0

    async def get_first_staff_message(self, staff_id: int) -> Optional[datetime]:
        """Get the timestamp of a staff member's first message."""
        row = await (await self.pool.execute(
            "SELECT MIN(timestamp) as first_msg FROM staff_messages WHERE staff_id = ?",
            (staff_id,)
        )).fetchone()
        if row and row["first_msg"]:
            return datetime.fromisoformat(row["first_msg"])
        return None

    async def get_daily_message_counts(self, staff_id: int, days: int) -> Dict[str, int]:
        """Return {'YYYY-MM-DD': count} of staff messages for the last N days."""
        cutoff = (discord.utils.utcnow() - timedelta(days=days)).isoformat()
        rows = await (await self.pool.execute(
            "SELECT DATE(timestamp) as day, COUNT(*) as c "
            "FROM staff_messages WHERE staff_id=? AND timestamp>? "
            "GROUP BY DATE(timestamp)",
            (staff_id, cutoff),
        )).fetchall()
        return {row["day"]: row["c"] for row in rows}

    async def get_daily_mod_counts(self, staff_id: int, days: int) -> Dict[str, int]:
        """Return {'YYYY-MM-DD': count} of moderation actions for the last N days."""
        cutoff = (discord.utils.utcnow() - timedelta(days=days)).isoformat()
        rows = await (await self.pool.execute(
            "SELECT DATE(timestamp) as day, COUNT(*) as c "
            "FROM moderation_actions WHERE moderator_id=? AND timestamp>? "
            "GROUP BY DATE(timestamp)",
            (staff_id, cutoff),
        )).fetchall()
        return {row["day"]: row["c"] for row in rows}

    async def get_weekly_score_history(
        self, staff_id: int, weeks: int = 4, weights: Optional[Dict] = None
    ) -> List[Dict]:
        """Return per-week message + mod counts for trend analysis."""
        now    = discord.utils.utcnow()
        result = []
        for w in range(weeks - 1, -1, -1):
            w_end   = now - timedelta(weeks=w)
            w_start = w_end - timedelta(weeks=1)
            msg_row = await (await self.pool.execute(
                "SELECT COUNT(*) as c FROM staff_messages "
                "WHERE staff_id=? AND timestamp>? AND timestamp<=?",
                (staff_id, w_start.isoformat(), w_end.isoformat())
            )).fetchone()
            mod_row = await (await self.pool.execute(
                "SELECT COUNT(*) as c FROM moderation_actions "
                "WHERE moderator_id=? AND timestamp>? AND timestamp<=?",
                (staff_id, w_start.isoformat(), w_end.isoformat())
            )).fetchone()
            result.append({
                "week_start": w_start.strftime("%b %d"),
                "messages":   msg_row["c"] or 0,
                "mod_actions": mod_row["c"] or 0,
            })
        return result