# cogs/omegle/omegle_matchmaker.py
import discord
from discord.ext import commands
from typing import Optional, Tuple, Dict, List
from helpers.github_data import load_db, save_db

class Matchmaker:
    def __init__(
        self,
        bot: commands.Bot,
        male_role_id: int,
        female_role_id: int,
        anyone_role_ids: list,
        hub_channel_id: int,
        db_key: str = "omegle"
    ):
        self.bot = bot
        self.male_role_id = male_role_id
        self.female_role_id = female_role_id
        self.anyone_role_ids = anyone_role_ids or []
        self.hub_channel_id = hub_channel_id
        self.db_key = db_key

        # in-memory queue synced with DB
        # entries: {"user_id": int, "serial": int, "gender": "male"|"female", "preference":"male"|"female"|"any"}
        self.queue: List[Dict] = []
        self._load_from_db()

    # ---------- DB helpers ----------
    def _load_from_db(self):
        db, _ = load_db()
        data = db.get(self.db_key, {})
        self.queue = data.get("queue", []) or []
        # ensure last_serial exists
        if "last_serial" not in data:
            data["last_serial"] = 0
            db[self.db_key] = data
            save_db(db, None)

    def _save_to_db(self):
        db, file = load_db()
        data = db.get(self.db_key, {})
        data["queue"] = self.queue
        data["last_serial"] = max((entry.get("serial", 0) for entry in self.queue), default=0)
        db[self.db_key] = data
        save_db(db, file)

    def _next_serial(self) -> int:
        db, file = load_db()
        data = db.get(self.db_key, {})
        last = data.get("last_serial", 0)
        last += 1
        data["last_serial"] = last
        db[self.db_key] = data
        save_db(db, file)
        return last

    # ---------- queue operations ----------
    def get_queue_entry(self, user_id: int) -> Optional[Dict]:
        for e in self.queue:
            if e["user_id"] == user_id:
                return e
        return None

    async def add_user(self, member: discord.Member, preference: str) -> Tuple[bool, Optional[int]]:
        """
        Add a user to the queue. Returns (added, serial_or_None).
        If user already in queue returns (False, serial).
        If user missing gender role returns (False, None).
        """
        existing = self.get_queue_entry(member.id)
        if existing:
            return False, existing["serial"]

        gender = self._detect_gender(member)
        if gender is None:
            # user doesn't have male/female role
            return False, None

        serial = self._next_serial()
        entry = {
            "user_id": member.id,
            "serial": serial,
            "gender": gender,          # 'male' | 'female'
            "preference": preference   # 'male' | 'female' | 'any'
        }
        self.queue.append(entry)
        self.queue.sort(key=lambda x: x["serial"])
        self._save_to_db()
        return True, serial

    async def remove_user_by_id(self, user_id: int) -> bool:
        before = len(self.queue)
        self.queue = [e for e in self.queue if e["user_id"] != user_id]
        if len(self.queue) < before:
            self._reassign_serials()
            self._save_to_db()
            return True
        return False

    def _reassign_serials(self):
        # reassign serials ascending while preserving current order
        for idx, e in enumerate(self.queue, start=1):
            e["serial"] = idx
        # update last_serial in DB
        db, file = load_db()
        data = db.get(self.db_key, {})
        data["last_serial"] = len(self.queue)
        db[self.db_key] = data
        save_db(db, file)

    # ---------- matching ----------
    def _detect_gender(self, member: discord.Member) -> Optional[str]:
        role_ids = [r.id for r in member.roles]
        if self.male_role_id in role_ids:
            return "male"
        if self.female_role_id in role_ids:
            return "female"
        # no gender role present
        return None

    def _compatible(self, a: Dict, b: Dict) -> bool:
        """
        Returns True if entry a and entry b are compatible.
        - if either preference == 'any' -> match
        - otherwise require a['preference'] == b['gender'] and b['preference'] == a['gender']
        - if both prefer 'any' -> match
        """
        pa = a["preference"]; pb = b["preference"]
        ga = a["gender"]; gb = b["gender"]

        if not ga or not gb:
            return False

        if pa == "any" and pb == "any":
            return True
        if pa == "any" or pb == "any":
            return True
        return (pa == gb) and (pb == ga)

    async def find_match(self, guild: discord.Guild) -> Tuple[Optional[discord.Member], Optional[discord.Member]]:
        """
        Scan queue in serial order and return first compatible pair of discord.Member objects.
        Removes matched entries from queue and persists DB.
        """
        if len(self.queue) < 2:
            return None, None

        # build list of (entry, member) for members present in guild
        entries = []
        for e in sorted(self.queue, key=lambda x: x["serial"]):
            m = guild.get_member(e["user_id"])
            if m:
                entries.append((e, m))
            else:
                # cleanup absent members
                await self.remove_user_by_id(e["user_id"])

        n = len(entries)
        for i in range(n):
            e1, m1 = entries[i]
            for j in range(i+1, n):
                e2, m2 = entries[j]
                if self._compatible(e1, e2):
                    # remove the matched entries from queue by id
                    await self.remove_user_by_id(e1["user_id"])
                    await self.remove_user_by_id(e2["user_id"])
                    return m1, m2
        return None, None

    # ---------- chat creation ----------
    async def create_chat_thread(self, guild: discord.Guild, member1: discord.Member, member2: discord.Member) -> Optional[discord.Thread]:
        hub = guild.get_channel(self.hub_channel_id)
        if not hub:
            return None
        try:
            thread = await hub.create_thread(
                name=f"chat-{member1.display_name}-x-{member2.display_name}",
                type=discord.ChannelType.private_thread,
                auto_archive_duration=60
            )
            # add members
            try:
                await thread.add_user(member1)
            except Exception:
                pass
            try:
                await thread.add_user(member2)
            except Exception:
                pass
            return thread
        except Exception:
            return None
