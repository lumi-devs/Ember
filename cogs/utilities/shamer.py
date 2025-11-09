# cogs/hall_of_shame.py
import asyncio
import aiosqlite
import discord
from discord.ext import commands, tasks
from discord import app_commands, Embed, Color
from datetime import datetime, timezone, timedelta
from typing import Dict

DB_PATH = "data/shame.db"

# ----- HARD-CODED SETTINGS -----
SHAME_CHANNEL_ID = 1437084755291279371
SHAMER_ROLE_ID = 1437078973220917455
IGNORED_ROLE_IDS = [1393170995157798983, 1406324000736542911]
# --------------------------------

DECAY_RATE = 0.02
DECAY_INTERVAL_MIN = 10
ROTATION_INTERVAL_HOURS = 96  # every 4 days
TOP_N = 5

IST = timezone(timedelta(hours=5, minutes=30))


class HallOfShame(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db_lock = asyncio.Lock()
        self.cache: Dict[int, float] = {}
        self.blacklist = set()
        self.last_rotation: datetime | None = None

        self.decay_and_flush_task.start()
        self.rotation_task.start()

    # -------------------- DB Setup --------------------
    async def cog_load(self):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    user_id INTEGER PRIMARY KEY,
                    count REAL DEFAULT 0,
                    last_update TIMESTAMP
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS blacklist (
                    user_id INTEGER PRIMARY KEY
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            await db.commit()

        await self._load_db_to_cache()

    async def _load_db_to_cache(self):
        async with self.db_lock:
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute("SELECT user_id, count FROM messages")
                self.cache = {int(uid): float(score) for uid, score in await cur.fetchall()}

                cur = await db.execute("SELECT user_id FROM blacklist")
                self.blacklist = {int(uid) for (uid,) in await cur.fetchall()}

                cur = await db.execute("SELECT value FROM meta WHERE key = 'last_rotation'")
                row = await cur.fetchone()
                if row and row[0]:
                    try:
                        self.last_rotation = datetime.fromisoformat(row[0]).astimezone(IST)
                    except Exception:
                        self.last_rotation = None

                if not self.last_rotation:
                    self.last_rotation = datetime.now(IST)
                    await db.execute(
                        "INSERT OR REPLACE INTO meta (key, value) VALUES ('last_rotation', ?)",
                        (self.last_rotation.isoformat(),)
                    )
                    await db.commit()

    def cog_unload(self):
        self.decay_and_flush_task.cancel()
        self.rotation_task.cancel()

    # -------------------- Message Tracking --------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        try:
            if any(r.id in IGNORED_ROLE_IDS for r in message.author.roles):
                return
        except Exception:
            pass
        if message.author.id in self.blacklist:
            return

        self.cache[message.author.id] = self.cache.get(message.author.id, 0.0) + 1.0

        # Threads only in the shame channel
        if message.channel.id != SHAME_CHANNEL_ID:
            return
        try:
            if message.attachments and not message.stickers:
                me = message.guild.me
                if message.channel.permissions_for(me).create_public_threads:
                    await message.create_thread(name=".", auto_archive_duration=60)
        except Exception:
            pass

    # -------------------- Decay + Flush --------------------
    @tasks.loop(minutes=DECAY_INTERVAL_MIN)
    async def decay_and_flush_task(self):
        async with self.db_lock:
            for uid in list(self.cache.keys()):
                self.cache[uid] *= (1 - DECAY_RATE)
                if self.cache[uid] < 0.001:
                    del self.cache[uid]
            await self._flush_cache_to_db()

    @decay_and_flush_task.before_loop
    async def before_decay_flush(self):
        await self.bot.wait_until_ready()
        await self._load_db_to_cache()

    async def _flush_cache_to_db(self):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("BEGIN")
            try:
                for uid, score in self.cache.items():
                    await db.execute("""
                        INSERT INTO messages (user_id, count, last_update)
                        VALUES (?, ?, ?)
                        ON CONFLICT(user_id) DO UPDATE SET count = excluded.count, last_update = excluded.last_update
                    """, (int(uid), float(score), datetime.now(IST).isoformat()))
                if self.last_rotation:
                    await db.execute(
                        "INSERT OR REPLACE INTO meta (key, value) VALUES ('last_rotation', ?)",
                        (self.last_rotation.isoformat(),)
                    )
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    # -------------------- Rotation --------------------
    @tasks.loop(hours=1)  # check every hour
    async def rotation_task(self):
        if not self.last_rotation:
            return
        now = datetime.now(IST)
        if now - self.last_rotation >= timedelta(hours=ROTATION_INTERVAL_HOURS):
            await self._run_rotation()

    @rotation_task.before_loop
    async def before_rotation(self):
        await self.bot.wait_until_ready()
        await self._load_db_to_cache()

    async def _run_rotation(self):
        async with self.db_lock:
            top = sorted(
                [(uid, score) for uid, score in self.cache.items() if uid not in self.blacklist],
                key=lambda x: x[1], reverse=True
            )[:TOP_N]

            for guild in self.bot.guilds:
                shame_channel = guild.get_channel(SHAME_CHANNEL_ID)
                shamer_role = guild.get_role(SHAMER_ROLE_ID)
                if not (shame_channel and shamer_role):
                    continue

                # Remove old role holders
                for member in list(shamer_role.members):
                    try:
                        await member.remove_roles(shamer_role, reason="Hall of Shame rotation")
                    except Exception:
                        pass

                # Assign new
                winners = []
                for uid, score in top:
                    member = guild.get_member(uid)
                    if member:
                        try:
                            await member.add_roles(shamer_role, reason="Top chatter (Hall of Shame)")
                            winners.append((member, score))
                        except Exception:
                            pass

                # Send leaderboard
                if winners:
                    desc_lines = [
                        f"**#{i}** {member.mention} — 🔥 {int(score)} shame points"
                        for i, (member, score) in enumerate(winners, start=1)
                    ]
                    embed = Embed(
                        title="💀 Hall of Shame — New Rotation",
                        description="\n".join(desc_lines),
                        color=Color.dark_gold(),
                        timestamp=datetime.now(IST)
                    )
                    embed.set_footer(text=f"Decay: {int(DECAY_RATE*100)}% every {DECAY_INTERVAL_MIN}m")
                    await shame_channel.send(embed=embed)
                    await shame_channel.send(
                        f"🎉 {shamer_role.mention}, you’ve earned your spot in the Hall of Shame once again 😏"
                    )

            # Half scores
            for uid in list(self.cache.keys()):
                self.cache[uid] *= 0.5
                if self.cache[uid] < 0.001:
                    del self.cache[uid]

            self.last_rotation = datetime.now(IST)
            await self._flush_cache_to_db()

    # -------------------- Commands --------------------
    @app_commands.command(name="shame_leaderboard", description="Show the top 5 shame leaderboard.")
    async def shame_leaderboard(self, interaction: discord.Interaction):
        async with self.db_lock:
            top = sorted([(uid, s) for uid, s in self.cache.items() if uid not in self.blacklist], key=lambda x: x[1], reverse=True)[:TOP_N]
        if not top:
            return await interaction.response.send_message("No tracked activity yet.")
        desc = "\n".join([f"**#{i}** <@{uid}> — 🔥 {int(score)} points" for i, (uid, score) in enumerate(top, 1)])
        embed = Embed(title="🏆 Hall of Shame — Leaderboard", description=desc, color=Color.blurple(), timestamp=datetime.now(IST))
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="shame_status", description="Show status and next rotation time.")
    async def shame_status(self, interaction: discord.Interaction):
        next_rot = (self.last_rotation + timedelta(hours=ROTATION_INTERVAL_HOURS)) if self.last_rotation else None
        remaining = (next_rot - datetime.now(IST)) if next_rot else None
        if remaining and remaining.total_seconds() > 0:
            hrs = remaining.seconds // 3600
            mins = (remaining.seconds % 3600) // 60
            remain_text = f"{remaining.days}d {hrs}h {mins}m"
        else:
            remain_text = "Soon"
        embed = Embed(
            title="📊 Hall of Shame Status",
            description=f"Next rotation: **{remain_text}**\nDecay: {int(DECAY_RATE*100)}% every {DECAY_INTERVAL_MIN}m",
            color=Color.orange(),
            timestamp=datetime.now(IST)
        )
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(HallOfShame(bot))
