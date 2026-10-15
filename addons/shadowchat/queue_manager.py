"""Queue state (in-memory) + persistent DB backing + matchmaking loop."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from .constants import MATCH_PAIRS, MATCHMAKING_INTERVAL

log = logging.getLogger("ember.shadowchat.queue")

# Type alias: async callback called when a match is found
MatchCallback = Callable[[int, str, int, str], Awaitable[None]]


def _can_match(gender_a: str, gender_b: str) -> bool:
    """Check whether two genders form a valid match pair (bidirectional)."""
    return (gender_a, gender_b) in MATCH_PAIRS or (gender_b, gender_a) in MATCH_PAIRS


class QueueManager:
    def __init__(self, db, on_match: MatchCallback):
        self._db = db
        self._on_match = on_match
        # In-memory ordered list: [(user_id, gender), ...]  — ordered by arrival
        self._queue: list[tuple[int, str]] = []
        self._user_set: set[int] = set()
        self._task: asyncio.Task | None = None

    # ── Public API ────────────────────────────────────────────────────────────

    async def start(self):
        """Load queue from DB (restart recovery) then start the matchmaking loop."""
        rows = await self._db.queue_get_all()
        for row in rows:
            uid = row["user_id"]
            if uid not in self._user_set:
                self._queue.append((uid, row["gender"]))
                self._user_set.add(uid)
        log.info(f"Queue restored: {len(self._queue)} users waiting")
        self._task = asyncio.create_task(self._loop(), name="sc_matchmaking")

    def stop(self):
        if self._task and not self._task.done():
            self._task.cancel()

    async def add(self, user_id: int, gender: str) -> bool:
        """Add user to queue. Returns False if already queued."""
        if user_id in self._user_set:
            return False
        self._queue.append((user_id, gender))
        self._user_set.add(user_id)
        await self._db.queue_add(user_id, gender)
        log.debug(f"Queued user {user_id} ({gender}), queue size={len(self._queue)}")
        return True

    async def remove(self, user_id: int) -> bool:
        """Remove user from queue. Returns False if not in queue."""
        if user_id not in self._user_set:
            return False
        self._queue = [(uid, g) for uid, g in self._queue if uid != user_id]
        self._user_set.discard(user_id)
        await self._db.queue_remove(user_id)
        return True

    def in_queue(self, user_id: int) -> bool:
        return user_id in self._user_set

    def size(self) -> int:
        return len(self._queue)

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _loop(self):
        while True:
            try:
                await asyncio.sleep(MATCHMAKING_INTERVAL)
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Matchmaking loop error: {e}", exc_info=True)

    @staticmethod
    def _rating_tier(avg: float | None) -> int:
        """Map avg rating to a tier score (0–2) for soft matchmaking preference."""
        if avg is None:
            return 1  # unrated newcomers treated as mid-tier
        if avg >= 4.0:
            return 2  # Tier A: high
        if avg >= 2.5:
            return 1  # Tier B: mid
        return 0  # Tier C: low

    async def _tick(self):
        if len(self._queue) < 2:
            return

        # Fetch ratings for all queued users (used for soft preference sort)
        ratings: dict[int, float | None] = {}
        for uid, _ in self._queue:
            ratings[uid] = await self._db.rating_get_user_avg(uid)

        matched_users: set[int] = set()
        pairs: list[tuple[tuple[int, str], tuple[int, str]]] = []

        # Greedy O(n²) scan — queue is small in practice
        q = [e for e in self._queue if e[0] not in matched_users]
        for i in range(len(q)):
            if q[i][0] in matched_users:
                continue
            for j in range(i + 1, len(q)):
                if q[j][0] in matched_users:
                    continue
                uid_a, g_a = q[i]
                uid_b, g_b = q[j]
                if _can_match(g_a, g_b):
                    pairs.append((q[i], q[j]))
                    matched_users.add(uid_a)
                    matched_users.add(uid_b)
                    break

        # Sort pairs so higher combined tier score fires first.
        # Tier A+A (score 4) > A+B (3) > B+B (2) > A+C (2) > B+C (1) > C+C (0)
        # Tie-break is FIFO (already encoded in queue order).
        pairs.sort(
            key=lambda p: self._rating_tier(ratings.get(p[0][0]))
            + self._rating_tier(ratings.get(p[1][0])),
            reverse=True,
        )

        for (uid_a, g_a), (uid_b, g_b) in pairs:
            # Remove from in-memory queue first (avoid double-match on re-entry)
            self._queue = [(uid, g) for uid, g in self._queue if uid not in {uid_a, uid_b}]
            self._user_set.discard(uid_a)
            self._user_set.discard(uid_b)
            await self._db.queue_remove(uid_a)
            await self._db.queue_remove(uid_b)
            log.info(f"Matched {uid_a}({g_a}) ↔ {uid_b}({g_b})")
            asyncio.create_task(
                self._fire_match(uid_a, g_a, uid_b, g_b),
                name=f"sc_match_{uid_a}_{uid_b}",
            )

    async def _fire_match(self, uid_a: int, g_a: str, uid_b: int, g_b: str):
        try:
            await self._on_match(uid_a, g_a, uid_b, g_b)
        except Exception as e:
            log.error(f"Match callback failed for {uid_a}↔{uid_b}: {e}", exc_info=True)
            # Re-queue both users on failure
            await self.add(uid_a, g_a)
            await self.add(uid_b, g_b)
