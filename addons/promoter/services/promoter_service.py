import logging

from core.store import Store

logger = logging.getLogger("ember.promoter.service")


class PromoterService:
    def __init__(self):
        self.db = Store.for_cog("promoter_role")

    async def init_db(self) -> None:
        self.db.register_table("""
            CREATE TABLE IF NOT EXISTS promoter_logs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id    INTEGER,
                member_id   INTEGER,
                member_name TEXT,
                action      TEXT,
                reason      TEXT,
                content     TEXT,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await self.db.initialize()
        logger.info("Promoter database initialized")

    async def log_action(
        self,
        guild_id: int,
        member_id: int,
        member_name: str,
        action: str,
        reason: str,
        content: str = "",
    ) -> None:
        try:
            await self.db.store(
                "promoter_logs",
                {
                    "guild_id": guild_id,
                    "member_id": member_id,
                    "member_name": member_name,
                    "action": action,
                    "reason": reason,
                    "content": content,
                },
            )
        except Exception as exc:
            logger.error("DB log error: %s", exc)

    async def delete_user_data(self, user_id: int) -> None:
        """Delete all logs associated with a user (GDPR compliance)."""
        await self.db.remove("promoter_logs", member_id=user_id)
