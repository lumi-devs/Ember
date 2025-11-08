# cogs/hall_of_shame.py
import asyncio
import aiosqlite
import discord
from discord.ext import commands, tasks
from discord import app_commands, Embed, Color
from datetime import datetime, timezone, timedelta
from typing import Dict

DB_PATH = "data/shame.db"

# ----- HARD-CODED SETTINGS (replace with your IDs) -----
SHAME_CHANNEL_ID = 123456789012345678  # channel where embed posts go
SHAMER_ROLE_ID = 234567890123456789    # role to assign to top 5
IGNORED_ROLE_IDS = [111111111111111111, 222222222222222222]  # roles to ignore (staff etc)
# -------------------------------------------------------

DECAY_RATE = 0.02              # 2% decay every DECAY_INTERVAL_MIN minutes
DECAY_INTERVAL_MIN = 10        # minutes between decays and DB flush
ROTATION_INTERVAL_HOURS = 96   # 4 days
TOP_N = 5                      # top N shamers per rotation

# Helper timezone
UTC = timezone.utc


class HallOfShame(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db_lock = asyncio.Lock()
        # in-memory cache: user_id -> float(score)
        self.cache: Dict[int, float] = {}
        # in-memory blacklist set
        self.blacklist = set()
        # meta: last_rotation timestamp (UTC)
        self.last_rotation: datetime | None = None

        # start tasks
        self.decay_and_flush_task.start()
        self.rotation_task.start()

    # -------------------- Startup / DB init --------------------
    async def cog_load(self):
        # ensure DB and tables exist and load initial state
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

        # load DB contents to memory
        await self._load_db_to_cache()

    async def _load_db_to_cache(self):
        async with self.db_lock:
            async with aiosqlite.connect(DB_PATH) as db:
                # load messages
                cur = await db.execute("SELECT user_id, count FROM messages")
                rows = await cur.fetchall()
                self.cache = {int(r[0]): float(r[1]) for r in rows}
                # load blacklist
                cur = await db.execute("SELECT user_id FROM blacklist")
                rows = await cur.fetchall()
                self.blacklist = {int(r[0]) for r in rows}
                # load last_rotation
                cur = await db.execute("SELECT value FROM meta WHERE key = 'last_rotation'")
                row = await cur.fetchone()
                if row and row[0]:
                    try:
                        self.last_rotation = datetime.fromisoformat(row[0]).astimezone(UTC)
                    except Exception:
                        self.last_rotation = None

                # if no last_rotation set, initialize to now (so first rotation occurs in ROTATION_INTERVAL_HOURS)
                if not self.last_rotation:
                    self.last_rotation = datetime.now(UTC)
                    await db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('last_rotation', ?)",
                                     (self.last_rotation.isoformat(),))
                    await db.commit()

    # -------------------- Shutdown --------------------
    def cog_unload(self):
        # cancel tasks
        self.decay_and_flush_task.cancel()
        self.rotation_task.cancel()
        # synchronous flush isn't available, but ensure flush scheduled at next tick (best-effort)
        # Alternatively, you can call asyncio.create_task(self._flush_cache_to_db()) here if desired.

    # -------------------- Message tracking & thread creation --------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        # ignore members that have any ignored roles
        try:
            if any(r.id in IGNORED_ROLE_IDS for r in message.author.roles):
                return
        except Exception:
            # in rare DM or partial member cases, ignore role-check failures safely
            pass

        # ignore blacklisted users
        if message.author.id in self.blacklist:
            return

        # increment cache
        self.cache[message.author.id] = self.cache.get(message.author.id, 0.0) + 1.0

        # Auto-create thread for attachments (non-text/sticker)
        try:
            if message.attachments and not message.stickers:
                # check bot permissions in that channel
                me = message.guild.me
                if message.channel.permissions_for(me).create_public_threads:
                    # minimal thread name as requested
                    try:
                        await message.create_thread(name=".", auto_archive_duration=60)
                    except Exception as e:
                        # sometimes thread creation fails if one already exists or perm issues - ignore
                        print(f"[HallOfShame] Thread creation error: {e}")
        except Exception:
            # fail safe: don't let thread errors interrupt counting
            pass

    # -------------------- Decay + periodic flush --------------------
    @tasks.loop(minutes=DECAY_INTERVAL_MIN)
    async def decay_and_flush_task(self):
        """Apply decay to in-memory scores and flush to DB."""
        async with self.db_lock:
            # apply decay
            for uid in list(self.cache.keys()):
                # apply multiplicative decay
                self.cache[uid] = float(self.cache[uid]) * (1.0 - DECAY_RATE)
                # prune very small values for cleanliness
                if self.cache[uid] < 0.001:
                    del self.cache[uid]

            # flush to DB
            await self._flush_cache_to_db()

    @decay_and_flush_task.before_loop
    async def before_decay_flush(self):
        await self.bot.wait_until_ready()
        # ensure DB loaded into cache
        await self._load_db_to_cache()

    async def _flush_cache_to_db(self):
        """Upsert all cached user counts into the messages table and store meta."""
        async with aiosqlite.connect(DB_PATH) as db:
            # transactions with executemany/upsert
            await db.execute("BEGIN")
            try:
                for uid, score in self.cache.items():
                    await db.execute("""
                        INSERT INTO messages (user_id, count, last_update)
                        VALUES (?, ?, ?)
                        ON CONFLICT(user_id) DO UPDATE SET count = excluded.count, last_update = excluded.last_update
                    """, (int(uid), float(score), datetime.now(UTC).isoformat()))
                # also persist blacklist to DB (re-sync)
                await db.execute("DELETE FROM blacklist")
                for uid in self.blacklist:
                    await db.execute("INSERT OR IGNORE INTO blacklist (user_id) VALUES (?)", (int(uid),))
                # persist last_rotation meta
                if self.last_rotation:
                    await db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('last_rotation', ?)",
                                     (self.last_rotation.isoformat(),))
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    # -------------------- Rotation task (every 4 days) --------------------
    @tasks.loop(hours=ROTATION_INTERVAL_HOURS)
    async def rotation_task(self):
        """Select top N, assign shamer role, post embed, DM winners, half scores."""
        # run rotation per guild
        await self._run_rotation()

    @rotation_task.before_loop
    async def before_rotation(self):
        await self.bot.wait_until_ready()
        # ensure DB loaded
        await self._load_db_to_cache()

    async def _run_rotation(self):
        async with self.db_lock:
            # compute top users from cache
            top = sorted(self.cache.items(), key=lambda x: x[1], reverse=True)
            top = [(uid, score) for uid, score in top if uid not in self.blacklist]
            top5 = top[:TOP_N]

            # for each guild, assign roles & announce to SHAME_CHANNEL_ID if present
            for guild in self.bot.guilds:
                shame_channel = guild.get_channel(SHAME_CHANNEL_ID)
                shamer_role = guild.get_role(SHAMER_ROLE_ID)
                if not shamer_role:
                    # if role doesn't exist in this guild, skip assigning there
                    continue

                # Remove role from everyone who currently has it
                try:
                    for member in list(shamer_role.members):
                        try:
                            await member.remove_roles(shamer_role, reason="Hall of Shame rotation")
                        except Exception:
                            # ignore remove failures
                            pass
                except Exception:
                    pass

                # Assign role to top N users if they are members of this guild
                winners = []
                for uid, score in top5:
                    member = guild.get_member(uid)
                    if member:
                        try:
                            await member.add_roles(shamer_role, reason="Top chatter (Hall of Shame)")
                            winners.append((member, score))
                        except Exception:
                            # ignore role assign failures (e.g. hierarchy)
                            pass

                # Build embed and send to shame channel
                if shame_channel and winners:
                    desc_lines = []
                    for i, (member, score) in enumerate(winners, start=1):
                        desc_lines.append(f"**#{i}** {member.mention} — {int(score)} msgs")
                    embed = Embed(
                        title=f"💀 Hall of Shame — Rotation",
                        description="\n".join(desc_lines),
                        color=Color.dark_gold(),
                        timestamp=datetime.now(UTC)
                    )
                    embed.set_footer(text=f"Decay: {int(DECAY_RATE*100)}% every {DECAY_INTERVAL_MIN}m")
                    try:
                        await shame_channel.send(embed=embed)
                    except Exception:
                        pass

                # DM each winner (if possible)
                for member, score in winners:
                    try:
                        await member.send(
                            f"🎉 Congrats {member.display_name}! You were chosen as one of the Shamers for this fortnight. "
                            "Keep up the chatting... or maybe slow down 😏"
                        )
                    except Exception:
                        # ignore DM failures
                        pass

            # After rotation, halve everyone scores (soft reset)
            for uid in list(self.cache.keys()):
                self.cache[uid] = float(self.cache.get(uid, 0.0)) * 0.5
                if self.cache[uid] < 0.001:
                    del self.cache[uid]

            # update last_rotation and persist
            self.last_rotation = datetime.now(UTC)
            await self._flush_cache_to_db()

    # -------------------- Slash Commands --------------------
    @app_commands.command(name="shame_leaderboard", description="Show current top 5 Hall of Shame leaderboard.")
    async def shame_leaderboard(self, interaction: discord.Interaction):
        # build top 5 from cache (viewable by everyone)
        async with self.db_lock:
            top = sorted(self.cache.items(), key=lambda x: x[1], reverse=True)
            top = [(uid, score) for uid, score in top if uid not in self.blacklist]
            rows = top[:TOP_N]

        if not rows:
            await interaction.response.send_message("No tracked activity yet.", ephemeral=False)
            return

        desc_lines = []
        for i, (uid, score) in enumerate(rows, start=1):
            desc_lines.append(f"**#{i}** <@{uid}> — {int(score)} msgs")

        embed = Embed(
            title="🏆 Hall of Shame — Current Top",
            description="\n".join(desc_lines),
            color=Color.blurple(),
            timestamp=datetime.now(UTC)
        )
        await interaction.response.send_message(embed=embed, ephemeral=False)

    @app_commands.command(name="shame_status", description="Show decay, next rotation, and current top 3.")
    async def shame_status(self, interaction: discord.Interaction):
        async with self.db_lock:
            # next rotation
            if self.last_rotation:
                next_rot = self.last_rotation + timedelta(hours=ROTATION_INTERVAL_HOURS)
                remaining = next_rot - datetime.now(UTC)
                if remaining.total_seconds() < 0:
                    remaining_text = "Due very soon"
                else:
                    # format time remaining
                    days = remaining.days
                    hrs = remaining.seconds // 3600
                    mins = (remaining.seconds % 3600) // 60
                    remaining_text = f"{days}d {hrs}h {mins}m"
            else:
                remaining_text = "Unknown"

            # top 3
            top = sorted(self.cache.items(), key=lambda x: x[1], reverse=True)
            top = [(uid, score) for uid, score in top if uid not in self.blacklist]
            top3 = top[:3]

        desc = f"Decay: {int(DECAY_RATE*100)}% every {DECAY_INTERVAL_MIN} minutes\nNext rotation in: **{remaining_text}**\n\n"
        if top3:
            desc += "Top right now:\n" + "\n".join([f"**#{i+1}** <@{uid}> — {int(score)} msgs" for i, (uid, score) in enumerate(top3)])
        else:
            desc += "No active users yet."

        embed = Embed(title="📊 Hall of Shame Status", description=desc, color=Color.orange(), timestamp=datetime.now(UTC))
        await interaction.response.send_message(embed=embed, ephemeral=False)

    @app_commands.command(name="shame_blacklist", description="Toggle blacklist for a user (Manage Server only).")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def shame_blacklist(self, interaction: discord.Interaction, member: discord.Member):
        async with self.db_lock:
            if member.id in self.blacklist:
                self.blacklist.remove(member.id)
                # remove from db
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("DELETE FROM blacklist WHERE user_id = ?", (member.id,))
                    await db.commit()
                await interaction.response.send_message(f"✅ {member.mention} removed from blacklist.", ephemeral=True)
            else:
                self.blacklist.add(member.id)
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("INSERT OR IGNORE INTO blacklist (user_id) VALUES (?)", (member.id,))
                    await db.commit()
                # also clear any cache count
                if member.id in self.cache:
                    self.cache.pop(member.id, None)
                await interaction.response.send_message(f"🚫 {member.mention} added to blacklist.", ephemeral=True)

    # -------------------- Utility / Sync endpoint --------------------
    @commands.command(name="shame_force_rotation", hidden=True)
    @commands.has_permissions(manage_guild=True)
    async def cmd_force_rotation(self, ctx: commands.Context):
        """Owner/admin utility (text command) to force the rotation immediately."""
        await ctx.send("Running rotation...")
        # run rotation once
        await self._run_rotation()
        await ctx.send("Rotation complete.")

    # Optionally expose sync flush command for debugging
    @commands.command(name="shame_flush", hidden=True)
    @commands.has_permissions(manage_guild=True)
    async def cmd_flush(self, ctx: commands.Context):
        """Force flush cache to DB."""
        async with self.db_lock:
            await self._flush_cache_to_db()
        await ctx.send("Flushed cache to DB.")

async def setup(bot: commands.Bot):
    cog = HallOfShame(bot)
    await bot.add_cog(cog)
