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


# ---------- File Handling ----------
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


# ---------- Parsing Helpers ----------
def parse_simple_duration(s: str):
    s = s.strip().lower()
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    try:
        num = int(s[:-1])
        unit = s[-1]
        return num * units[unit]
    except Exception:
        return None


IST = timezone(timedelta(hours=5, minutes=30))

IST = timezone(timedelta(hours=5, minutes=30))

def parse_time_expression(expr: str):
    now_ist = datetime.now(IST)
    settings = {
        "RETURN_AS_TIMEZONE_AWARE": True,
        "TIMEZONE": "Asia/Kolkata",
        "TO_TIMEZONE": "Asia/Kolkata",
        "PREFER_DATES_FROM": "future",
        "RELATIVE_BASE": now_ist
    }
    parsed = dateparser.parse(expr, settings=settings)
    if parsed:
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=IST)
        return parsed.replace(microsecond=0)
    return None



def human_dt(dt: datetime):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    utc_str = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    ist = dt + timedelta(hours=5, minutes=30)
    ist_str = ist.strftime("%Y-%m-%d %H:%M:%S IST")
    return f"{utc_str} / {ist_str}"


# ---------- Cog ----------
class ReminderCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.reminders = load_reminders()
        self._tasks: dict[int, asyncio.Task] = {}
        self._lock = asyncio.Lock()
        self.bot.loop.create_task(self._startup_schedule())

    # ---------- Startup ----------
    async def _startup_schedule(self):
        await self.bot.wait_until_ready()
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

    # ---------- Workers ----------
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

    async def _reschedule(self, rem: dict):
        now = datetime.now(timezone.utc)
        if rem.get("repeat_type") == "interval":
            interval = rem.get("interval_seconds", 0)
            if interval <= 0:
                await self._remove_reminder_by_id(rem["id"])
                return
            rem["time"] = (now + timedelta(seconds=interval)).isoformat()
            await self._save_and_schedule(rem)
        elif rem.get("repeat_type") == "natural":
            expr = rem.get("repeat_expr")
            next_dt = parse_time_expression(expr, base_dt=now)
            if not next_dt:
                await self._remove_reminder_by_id(rem["id"])
                return
            rem["time"] = next_dt.isoformat()
            await self._save_and_schedule(rem)
        else:
            await self._remove_reminder_by_id(rem["id"])

    async def _save_and_schedule(self, rem: dict):
        async with self._lock:
            for i, r in enumerate(self.reminders):
                if r["id"] == rem["id"]:
                    self.reminders[i] = rem
                    break
            else:
                self.reminders.append(rem)
            save_reminders(self.reminders)

        now = datetime.now(timezone.utc)
        remind_at = datetime.fromisoformat(rem["time"])
        if remind_at.tzinfo is None:
            remind_at = remind_at.replace(tzinfo=timezone.utc)
        delay = (remind_at - now).total_seconds()
        task = self.bot.loop.create_task(self._reminder_worker(rem, max(delay, 0)))
        self._tasks[rem["id"]] = task

    async def _remove_reminder_by_id(self, rid: int):
        async with self._lock:
            self.reminders = [r for r in self.reminders if r.get("id") != rid]
            save_reminders(self.reminders)
        t = self._tasks.pop(rid, None)
        if t:
            t.cancel()

    # ---------- Commands ----------
    @commands.command(name="remindme")
    async def remindme(self, ctx: commands.Context, *, args: str):
        """One-time reminder for any user, auto-detects time expression."""
        # Try parsing the longest possible prefix as time
        words = args.split()
        when = None
        message = None
        for i in range(len(words), 0, -1):
            time_expr = " ".join(words[:i])
            m = " ".join(words[i:])
            parsed = parse_time_expression(time_expr)
            if parsed:
                when = parsed
                message = m
                break

        if not when:
            return await ctx.send("❌ Couldn't parse time/expression.")

        if not message:
            return await ctx.send("❌ Please provide a reminder message.")

        # schedule reminder
        rid = next(ID_COUNTER)
        rem = {
            "id": rid,
            "author_id": ctx.author.id,
            "channel_id": ctx.channel.id,
            "targets": [{"id": ctx.author.id, "is_role": False}],
            "message": message,
            "time": when.isoformat(),
            "repeat": False,
            "repeat_type": None,
            "interval_seconds": 0,
            "repeat_expr": None,
            "created_at": datetime.now(IST).isoformat(),
            "owner_only": False
        }

        async with self._lock:
            self.reminders.append(rem)
            save_reminders(self.reminders)

        delay = (when - datetime.now(IST)).total_seconds()
        task = self.bot.loop.create_task(self._reminder_worker(rem, max(delay, 0)))
        self._tasks[rid] = task

        embed = discord.Embed(title="✅ Reminder set", color=0x2F3136)
        embed.add_field(name="Message", value=message, inline=False)
        embed.add_field(name="When", value=human_dt(when), inline=False)
        embed.set_footer(text=f"Reminder ID: {rid}")
        await ctx.send(embed=embed)



    @commands.command(name="remind")
    @commands.is_owner()
    async def remind(self, ctx: commands.Context, target: discord.Role | discord.Member, time_expr: str, *, message: str):
        """Owner-only; supports interval & natural recurrence"""
        # detect repeat
        args = message.strip().split()
        repeat_flag = False
        repeat_type = None
        interval_seconds = 0
        repeat_expr = None

        if args and args[0].lower() == "repeat":
            repeat_flag = True
            sec = parse_simple_duration(time_expr)
            if sec:
                repeat_type = "interval"
                interval_seconds = sec
            else:
                repeat_type = "natural"
                repeat_expr = time_expr
        else:
            if time_expr.strip().lower().startswith("every"):
                repeat_flag = True
                repeat_type = "natural"
                repeat_expr = time_expr

        when = parse_time_expression(time_expr)
        if not when:
            return await ctx.send("❌ Couldn't parse time/expression.")

        rid = next(ID_COUNTER)
        rem = {
            "id": rid,
            "author_id": ctx.author.id,
            "channel_id": ctx.channel.id,
            "targets": [{"id": target.id, "is_role": isinstance(target, discord.Role)}],
            "message": message,
            "time": when.isoformat(),
            "repeat": repeat_flag,
            "repeat_type": repeat_type,
            "interval_seconds": interval_seconds,
            "repeat_expr": repeat_expr,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "owner_only": True
        }

        async with self._lock:
            self.reminders.append(rem)
            save_reminders(self.reminders)

        delay = (when - datetime.now(timezone.utc)).total_seconds()
        task = self.bot.loop.create_task(self._reminder_worker(rem, max(delay, 0)))
        self._tasks[rid] = task

        embed = discord.Embed(title="✅ Owner reminder set", color=0x2F3136)
        embed.add_field(name="Target", value=target.mention, inline=True)
        embed.add_field(name="Message", value=message, inline=False)
        embed.add_field(name="When", value=human_dt(when), inline=False)
        if repeat_flag:
            if repeat_type == "interval":
                embed.add_field(name="Repeat", value=f"Every {time_expr}", inline=True)
            else:
                embed.add_field(name="Repeat (natural)", value=f"{repeat_expr}", inline=True)
        embed.set_footer(text=f"Reminder ID: {rid}")
        await ctx.send(embed=embed)

    @commands.command(name="listreminders")
    async def listreminders(self, ctx: commands.Context, flag: str = None):
        show_all = flag == "--all" and await self._is_owner(ctx)
        items = self.reminders if show_all else [r for r in self.reminders if r.get("author_id") == ctx.author.id]

        if not items:
            return await ctx.send("No reminders found.")

        chunks = [items[i:i + 8] for i in range(0, len(items), 8)]
        for chunk in chunks:
            embed = discord.Embed(title="🔔 Pending Reminders", color=0x2F3136)
            for r in chunk:
                target_str = ", ".join(
                    f"<@&{t['id']}>" if t['is_role'] else f"<@{t['id']}>" 
                    for t in r.get("targets", [])
                )
                when = r.get("time")
                rid = r.get("id")
                repeat = r.get("repeat")
                desc = f"Target: {target_str}\nWhen: {human_dt(datetime.fromisoformat(when))}\nRepeat: {repeat}\nMessage: {r.get('message')}"
                embed.add_field(name=f"ID {rid}", value=desc, inline=False)
            await ctx.send(embed=embed)

    async def _is_owner(self, ctx: commands.Context):
        try:
            return await self.bot.is_owner(ctx.author)
        except Exception:
            return False

    @commands.command(name="cancelreminder")
    @commands.is_owner()
    async def cancelreminder(self, ctx: commands.Context, rid: int):
        found = any(r for r in self.reminders if r.get("id") == rid)
        if not found:
            return await ctx.send(f"❌ No reminder found with ID `{rid}`.")
        await self._remove_reminder_by_id(rid)
        await ctx.send(f"✅ Reminder `{rid}` cancelled.")

    def cog_unload(self):
        for t in list(self._tasks.values()):
            t.cancel()


async def setup(bot: commands.Bot):
    await asyncio.sleep(5)
    await bot.add_cog(ReminderCog(bot))
