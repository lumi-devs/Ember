"""Confessions database layer."""

import logging

from core.database import DatabaseManager

logger = logging.getLogger("ember.confessions")


class ConfessionsDB(DatabaseManager):
    def __init__(self):
        super().__init__("confessions")

    async def initialize_tables(self) -> None:
        await self.execute("PRAGMA foreign_keys = ON")
        await self.execute("""
            CREATE TABLE IF NOT EXISTS confessions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                title       TEXT,
                description TEXT    NOT NULL,
                image_url   TEXT,
                message_id  INTEGER,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await self.execute("""
            CREATE TABLE IF NOT EXISTS replies (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                confession_id INTEGER NOT NULL,
                author_id     INTEGER NOT NULL,
                content       TEXT    NOT NULL,
                message_id    INTEGER,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (confession_id) REFERENCES confessions(id) ON DELETE CASCADE
            )
        """)
        await self.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                target_type TEXT    NOT NULL,
                target_id   INTEGER NOT NULL,
                reporter_id INTEGER NOT NULL,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await self.execute("""
            CREATE TABLE IF NOT EXISTS confessban (
                user_id    INTEGER PRIMARY KEY,
                reason     TEXT,
                banned_by  INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        for ddl in [
            "CREATE INDEX IF NOT EXISTS idx_confession_user    ON confessions(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_confession_message ON confessions(message_id)",
            "CREATE INDEX IF NOT EXISTS idx_reply_confession   ON replies(confession_id)",
            "CREATE INDEX IF NOT EXISTS idx_reply_message      ON replies(message_id)",
            "CREATE INDEX IF NOT EXISTS idx_report_target      ON reports(target_id)",
            "CREATE INDEX IF NOT EXISTS idx_ban_user           ON confessban(user_id)",
        ]:
            await self.execute(ddl)
        logger.info("Confessions database initialized")

    # ─── Confessions ──────────────────────────────────────────────────────────

    async def create_confession(
        self,
        user_id: int,
        description: str,
        title: str | None = None,
        image_url: str | None = None,
    ) -> int:
        await self.save(
            "confessions",
            {
                "user_id": user_id,
                "description": description,
                "title": title,
                "image_url": image_url,
            },
        )
        row = await self.fetch_one("SELECT last_insert_rowid() AS id")
        return row["id"] if row else -1

    async def get_confession(self, confession_id: int) -> dict | None:
        return await self.get("confessions", "id = ?", (confession_id,))

    async def get_confession_by_message(self, message_id: int) -> dict | None:
        return await self.get("confessions", "message_id = ?", (message_id,))

    async def set_confession_message(self, confession_id: int, message_id: int) -> None:
        await self.update("confessions", {"message_id": message_id}, "id = ?", (confession_id,))

    async def get_user_confessions(self, user_id: int) -> list[dict]:
        return await self.get_all(
            "confessions", "user_id = ?", (user_id,), order_by="created_at DESC"
        )

    async def delete_confession(self, confession_id: int) -> bool:
        return await self.delete("confessions", "id = ?", (confession_id,)) > 0

    # ─── Replies ──────────────────────────────────────────────────────────────

    async def add_reply(
        self,
        confession_id: int,
        author_id: int,
        content: str,
        message_id: int | None = None,
    ) -> None:
        await self.save(
            "replies",
            {
                "confession_id": confession_id,
                "author_id": author_id,
                "content": content,
                "message_id": message_id,
            },
        )

    async def get_replies(self, confession_id: int) -> list[dict]:
        return await self.get_all(
            "replies", "confession_id = ?", (confession_id,), order_by="created_at ASC"
        )

    async def get_reply_by_message(self, message_id: int) -> dict | None:
        return await self.get("replies", "message_id = ?", (message_id,))

    async def delete_reply(self, reply_id: int) -> bool:
        return await self.delete("replies", "id = ?", (reply_id,)) > 0

    # ─── Reports ──────────────────────────────────────────────────────────────

    async def add_report(self, target_type: str, target_id: int, reporter_id: int) -> None:
        await self.save(
            "reports",
            {
                "target_type": target_type,
                "target_id": target_id,
                "reporter_id": reporter_id,
            },
        )

    async def get_reports(self, target_id: int) -> list[dict]:
        return await self.get_all("reports", "target_id = ?", (target_id,))

    async def has_reported(self, target_id: int, reporter_id: int) -> bool:
        return await self.exists(
            "reports", "target_id = ? AND reporter_id = ?", (target_id, reporter_id)
        )

    # ─── Bans ─────────────────────────────────────────────────────────────────

    async def ban_user(
        self, user_id: int, reason: str | None = None, banned_by: int | None = None
    ) -> None:
        await self.save(
            "confessban", {"user_id": user_id, "reason": reason, "banned_by": banned_by}
        )

    async def unban_user(self, user_id: int) -> bool:
        return await self.delete("confessban", "user_id = ?", (user_id,)) > 0

    async def is_banned(self, user_id: int) -> bool:
        return await self.exists("confessban", "user_id = ?", (user_id,))

    async def get_ban(self, user_id: int) -> dict | None:
        return await self.get("confessban", "user_id = ?", (user_id,))

    async def get_all_bans(self) -> list[dict]:
        return await self.get_all("confessban", order_by="created_at DESC")


# Module-level singleton
_db: ConfessionsDB | None = None


async def get_db() -> ConfessionsDB:
    global _db
    if _db is None:
        _db = ConfessionsDB()
        await _db.initialize_tables()
    return _db


# Backward compat for old init_db() calls
async def init_db() -> None:
    await get_db()
