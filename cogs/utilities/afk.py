import discord
from discord.ext import commands
import aiosqlite
from datetime import datetime, timezone
import os


AFK_SET_COLOR = discord.Color.orange()
AFK_REMOVED_COLOR = discord.Color.gold()
AFK_MENTION_COLOR = discord.Color.from_rgb(255, 200, 0)


def afk_strip(name: str) -> str:
    return name[len("[AFK]"):].lstrip() if name.startswith("[AFK]") else name


def human_timedelta(dt: datetime) -> str:
    now = datetime.now(timezone.utc)
    delta = now - dt
    days, seconds = delta.days, delta.seconds
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)

    parts = []
    if days: parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours: parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes: parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    if seconds and not parts: parts.append(f"{seconds} second{'s' if seconds != 1 else ''}")

    return " ".join(parts) if parts else "just now"


class AFK(commands.Cog):



    # ---------------------------------------------------------
# Cleanup old AFK entries for users who already left
# ---------------------------------------------------------
    @commands.Cog.listener()
    async def on_ready(self):
        # Iterate all AFK entries
        async with self.db.execute("SELECT user_id FROM afk") as cur:
            rows = await cur.fetchall()

        for (user_id,) in rows:
            uid = int(user_id)

            # Check if user exists in ANY guild (multi-guild safe)
            user_in_guilds = any(g.get_member(uid) for g in self.bot.guilds)

            if not user_in_guilds:
                await self.db.execute("DELETE FROM afk WHERE user_id = ?", (user_id,))
        
        await self.db.commit()
        print("[AFK] Cleaned up users who already left servers.")


    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db_path = "data/afk.db"

        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.db = None

        # Launch async DB initializer
        bot.loop.create_task(self.init_db())

    # ---------------------------------------------------------
    # Init DB
    # ---------------------------------------------------------
    async def init_db(self):
        self.db = await aiosqlite.connect(self.db_path)
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS afk (
                user_id TEXT PRIMARY KEY,
                reason TEXT,
                since TEXT
            )
        """)

        # PERFORMANCE: indexes for instant lookups
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_afk_user ON afk (user_id)")
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_afk_since ON afk (since)")

        # PERFORMANCE: PRAGMAs for high-speed concurrent ops
        await self.db.execute("PRAGMA journal_mode=WAL;")
        await self.db.execute("PRAGMA synchronous=NORMAL;")
        await self.db.execute("PRAGMA temp_store=MEMORY;")
        await self.db.execute("PRAGMA cache_size=10000;")  # in pages (~10MB cache)

        await self.db.commit()
        print("[AFK] Database ready (optimized)")

    # ---------------------------------------------------------
    # Command: Set AFK
    # ---------------------------------------------------------
    @commands.command(name="afk")
    async def afk(self, ctx: commands.Context, *, reason: str = "AFK"):
        user = ctx.author

        try:
            await ctx.message.delete()
        except:
            pass

        since = datetime.now(timezone.utc).isoformat()

        await self.db.execute(
            "REPLACE INTO afk (user_id, reason, since) VALUES (?, ?, ?)",
            (str(user.id), reason, since)
        )
        await self.db.commit()

        # Update nickname
        if ctx.guild:
            try:
                dn = user.display_name
                if not dn.startswith("[AFK]"):
                    await user.edit(nick=f"[AFK] {dn}")
            except:
                pass

        embed = discord.Embed(
            title="✅ AFK Set",
            description=f"{user.mention}, you are now AFK: **{reason}**",
            color=AFK_SET_COLOR
        )
        await ctx.send(embed=embed, delete_after=20)

    # ---------------------------------------------------------
    # Owner: List AFK
    # ---------------------------------------------------------
    @commands.command(name="afklist")
    @commands.is_owner()
    async def afk_list(self, ctx: commands.Context):
        async with self.db.execute("SELECT user_id, reason, since FROM afk") as cur:
            rows = await cur.fetchall()

        if not rows:
            return await ctx.reply("No users are currently AFK.", mention_author=False)

        lines = []
        for user_id, reason, since in rows:
            user = self.bot.get_user(int(user_id))
            name = user.mention if user else f"Unknown ({user_id})"
            since_dt = datetime.fromisoformat(since)
            duration = human_timedelta(since_dt)

            lines.append(f"{name} — **{reason}** *(for {duration})*")

        embed = discord.Embed(
            title="📜 AFK List",
            description="\n".join(lines),
            color=AFK_MENTION_COLOR
        )
        await ctx.reply(embed=embed, mention_author=False)

    # ---------------------------------------------------------
    # Owner: Stats
    # ---------------------------------------------------------
    @commands.command(name="afkstats")
    @commands.is_owner()
    async def afk_stats(self, ctx: commands.Context):
        async with self.db.execute("SELECT COUNT(*) FROM afk") as cur:
            count = (await cur.fetchone())[0]

        embed = discord.Embed(
            title="📊 AFK Stats",
            description=f"**Current AFK Users:** {count}",
            color=discord.Color.blue()
        )
        await ctx.reply(embed=embed, mention_author=False)

    # ---------------------------------------------------------
    # Helper: Multi-user AFK lookup (1 DB query)
    # ---------------------------------------------------------
    async def get_multiple_afk(self, ids: list[int]):
        if not ids:
            return {}

        placeholders = ",".join("?" for _ in ids)
        async with self.db.execute(
            f"SELECT user_id, reason, since FROM afk WHERE user_id IN ({placeholders})",
            [str(i) for i in ids]
        ) as cur:
            rows = await cur.fetchall()

        return {int(uid): (reason, since) for uid, reason, since in rows}

    # ---------------------------------------------------------
    # Event: On message
    # ---------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):

        if message.author.bot or not message.content:
            return

        # -----------------------------
        # If message author is AFK
        # -----------------------------
        async with self.db.execute(
            "SELECT reason, since FROM afk WHERE user_id = ?", (str(message.author.id),)
        ) as cur:
            entry = await cur.fetchone()

        if entry:
            # Remove from DB
            await self.db.execute("DELETE FROM afk WHERE user_id = ?", (str(message.author.id),))
            await self.db.commit()

            # Remove prefix
            if message.guild:
                dn = message.author.display_name
                if dn.startswith("[AFK]"):
                    try:
                        await message.author.edit(nick=afk_strip(dn))
                    except:
                        pass

            since_dt = datetime.fromisoformat(entry[1])
            duration = human_timedelta(since_dt)

            embed = discord.Embed(
                title="👋 Welcome Back!",
                description=f"{message.author.mention}, AFK removed.\nYou were AFK for **{duration}**.",
                color=AFK_REMOVED_COLOR
            )
            await message.channel.send(embed=embed, delete_after=20)

        # -----------------------------
        # Mentions: batch lookup for AFK users
        # -----------------------------
        mentioned_ids = [u.id for u in message.mentions if u.id != message.author.id]

        if mentioned_ids:
            afk_map = await self.get_multiple_afk(mentioned_ids)

            for user in message.mentions:
                if user.id in afk_map:
                    reason, since = afk_map[user.id]
                    since_dt = datetime.fromisoformat(since)
                    duration = human_timedelta(since_dt)

                    embed = discord.Embed(
                        title=f"{afk_strip(user.display_name)} is AFK 💤",
                        description=f"**Reason:** {reason}\n**Since:** {duration} ago",
                        color=AFK_MENTION_COLOR
                    )
                    await message.channel.send(embed=embed, delete_after=600)

    # ---------------------------------------------------------
    # Cleanup
    # ---------------------------------------------------------
    async def cog_unload(self):
        if self.db:
            await self.db.close()


async def setup(bot: commands.Bot):
    await bot.add_cog(AFK(bot))
