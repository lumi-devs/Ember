# cogs/reminder_cog.py
import discord
from discord.ext import commands
import asyncio
import json
from pathlib import Path
from datetime import datetime, timedelta, timezone
import dateparser
import itertools

REMINDERS_FILE = Path("data/reminders.json")
REMINDERS_FILE.parent.mkdir(parents=True, exist_ok=True)

ID_COUNTER = itertools.count(1)
STARTUP_TOLERANCE_SECONDS = 300  # ±5 minutes

def load_reminders():
    if REMINDERS_FILE.exists():
        try:
            with open(REMINDERS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data:
                    maxid = max(r.get("id", 0) for r in data)
                    for _ in range(maxid):
                        next(ID_COUNTER)
                return data
        except json.JSONDecodeError:
            return []
    return []

def save_reminders(reminders):
    with open(REMINDERS_FILE, "w", encoding="utf-8") as f:
        json.dump(reminders, f, indent=4, ensure_ascii=False)

def parse_simple_duration(s: str):
    s = s.strip().lower()
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    try:
        num = int(s[:-1])
        unit = s[-1]
        return num * units[unit]
    except Exception:
        return None

def parse_time_expression(expr: str, base_dt: datetime = None):
    sec = parse_simple_duration(expr)
    if sec is not None:
        target = datetime.now(timezone.utc) + timedelta(seconds=sec)
        return target.replace(microsecond=0)
    settings = {
        "RETURN_AS_TIMEZONE_AWARE": True,
        "TO_TIMEZONE": "UTC",
        "PREFER_DATES_FROM": "future",
    }
    if base_dt:
        settings["RELATIVE_BASE"] = base_dt.astimezone(timezone.utc)
    parsed = dateparser.parse(expr, settings=settings)
    if parsed:
        return parsed.astimezone(timezone.utc).replace(microsecond=0)
    return None

def human_dt(dt: datetime):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    utc_str = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    ist = dt + timedelta(hours=5, minutes=30)
    ist_str = ist.strftime("%Y-%m-%d %H:%M:%S IST")
    return f"{utc_str} / {ist_str}"

class ReminderCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.reminders = []
        self._tasks = {}
        self._lock = asyncio.Lock()
        self.bot.loop.create_task(self._startup_init())

    async def _startup_init(self):
        await self.bot.wait_until_ready()

        # --- GitHub pull step ---
        github_cog = self.bot.get_cog("GitHubSync")
        if github_cog:
            try:
                await github_cog.pull_from_github()
                print("[ReminderCog] Pulled reminders.json from GitHub.")
            except Exception as e:
                print(f"[ReminderCog] GitHub pull failed: {e}")

        # Load after pulling
        self.reminders = load_reminders()

        # Schedule reminders
        await self._startup_schedule()

    async def _startup_schedule(self):
        now = datetime.now(timezone.utc)
        for rem in list(self.reminders):
            try:
                remind_at = datetime.fromisoformat(rem["time"])
                if remind_at.tzinfo is None:
                    remind_at = remind_at.replace(tzinfo=timezone.utc)
            except Exception:
                await self._remove_reminder_by_id(rem.get("id"))
                continue
            delay = (remind_at - now).total_seconds()
            if abs(delay) <= STARTUP_TOLERANCE_SECONDS or delay < 0:
                self.bot.loop.create_task(self._execute_reminder(rem))
            else:
                task = self.bot.loop.create_task(self._reminder_worker(rem, delay))
                self._tasks[rem["id"]] = task

    async def _reminder_worker(self, rem: dict, delay: float):
        try:
            await asyncio.sleep(max(delay, 0))
            await self._execute_reminder(rem)
        except asyncio.CancelledError:
            return

    async def _execute_reminder(self, rem: dict):
        channel = self.bot.get_channel(rem.get("channel_id"))
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(rem.get("channel_id"))
            except Exception:
                channel = None

        content_mention = ""
        if rem.get("targets"):
            mentions = []
            for t in rem["targets"]:
                mentions.append(f"<@&{t['id']}>" if t.get("is_role") else f"<@{t['id']}>")
            content_mention = " ".join(mentions)

        embed = discord.Embed(title="⏰ Reminder", color=0x2F3136)
        embed.add_field(name="Message", value=rem.get("message", "(no message)"), inline=False)
        embed.add_field(name="Scheduled Time", value=human_dt(datetime.fromisoformat(rem["time"])), inline=False)
        embed.set_footer(text=f"Reminder ID: {rem['id']}")

        try:
            if channel:
                await channel.send(content=content_mention, embed=embed)
            else:
                author = self.bot.get_user(rem.get("author_id"))
                if author:
                    await author.send(content=content_mention, embed=embed)
        except Exception:
            pass

        if rem.get("repeat"):
            await self._reschedule(rem)
        else:
            await self._remove_reminder_by_id(rem["id"])

    # ... (rest of your methods unchanged) ...

    @commands.command(name="reloadreminders")
    @commands.is_owner()
    async def reloadreminders(self, ctx: commands.Context):
        """Force pull from GitHub + reload reminders (like AFK load)."""
        github_cog = self.bot.get_cog("GitHubSync")
        if github_cog:
            try:
                await github_cog.pull_from_github()
                await ctx.send("✅ Pulled latest reminders.json from GitHub.")
            except Exception as e:
                await ctx.send(f"⚠️ GitHub pull failed: {e}")

        self.reminders = load_reminders()
        for t in list(self._tasks.values()):
            t.cancel()
        self._tasks.clear()
        await self._startup_schedule()
        await ctx.send("🔄 Reminders reloaded & scheduled.")

    def cog_unload(self):
        for t in list(self._tasks.values()):
            t.cancel()

async def setup(bot: commands.Bot):
    await bot.add_cog(ReminderCog(bot))
