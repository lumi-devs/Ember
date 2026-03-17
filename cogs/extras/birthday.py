import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiosqlite
import asyncio
import os
from datetime import datetime, date as date_class, timedelta
from typing import Optional, List, Tuple
import pytz
from zoneinfo import ZoneInfo
import re

# ─── CONFIG (env vars) ───────────────────────────────────────
DB_PATH = os.environ.get("BIRTHDAY_DB_PATH", "data/birthdays.db")
ANNOUNCEMENT_CHANNEL_ID = int(os.environ.get("BIRTHDAY_CHANNEL_ID", "0")) or None
BIRTHDAY_ROLE_ID = int(os.environ.get("BIRTHDAY_ROLE_ID", "0")) or None

# How many days a user must wait before changing their birthday again.
BIRTHDAY_CHANGE_COOLDOWN_DAYS = 30

# Minimum age (in years) a user must claim. Discord's own ToS is 13.
MIN_AGE_YEARS = 13

# How many distinct user IDs may share the same birth_date+birth_year before
# an admin warning is emitted (soft duplicate/alt detection).
DUPLICATE_BIRTHDAY_THRESHOLD = 5

# Grace window (minutes past midnight) in which the loop will still fire a
# missed announcement.  Keeps the 0-9 min window idempotent via INSERT OR IGNORE.
BIRTHDAY_GRACE_MINUTES = 9


# ─── HELPERS ─────────────────────────────────────────────────

def safe_birthday_datetime(year: int, month: int, day: int, tzinfo) -> datetime:
    """Build a birthday datetime, rolling Feb-29 back to Feb-28 on non-leap years."""
    try:
        return datetime(year, month, day, tzinfo=tzinfo)
    except ValueError:
        # Only Feb-29 in a non-leap year triggers this
        return datetime(year, 2, 28, tzinfo=tzinfo)


# ─── UI COMPONENTS ───────────────────────────────────────────

class BirthdayView(discord.ui.View):
    """View for timezone selection."""

    def __init__(self, user_id: int, birth_date: str, birth_year: Optional[int]):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.birth_date = birth_date
        self.birth_year = birth_year
        self.selected_timezone = None
        self.add_item(TimezoneSelect())

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if not hasattr(self, "message") or self.message is None:
            return
        try:
            await self.message.edit(view=self)
            await asyncio.sleep(5)
            await self.message.delete()
        except (discord.NotFound, discord.HTTPException):
            pass


class TimezoneSelect(discord.ui.Select):
    """Timezone selection dropdown."""

    def __init__(self):
        common_zones = [
            # --- South Asia (Priority) ---
            "Asia/Kolkata",
            "Asia/Karachi",
            "Asia/Dhaka",
            "Asia/Colombo",
            "Asia/Kathmandu",
            # --- Rest of Asia & Pacific ---
            "Asia/Dubai",
            "Asia/Shanghai",
            "Asia/Tokyo",
            "Asia/Seoul",
            "Australia/Sydney",
            "Pacific/Auckland",
            # --- Americas ---
            "America/New_York",
            "America/Chicago",
            "America/Denver",
            "America/Los_Angeles",
            "Brazil/East",
            # --- Europe & Africa ---
            "Europe/London",
            "Europe/Paris",
            "Europe/Berlin",
            "Europe/Moscow",
            "Africa/Johannesburg",
            "UTC",
        ]
        options = [
            discord.SelectOption(label=tz, value=tz, description=f"Timezone: {tz}")
            for tz in common_zones
        ]
        super().__init__(
            placeholder="🌍 Select your timezone...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        view: BirthdayView = self.view
        view.selected_timezone = self.values[0]
        self.disabled = True

        embed = discord.Embed(
            title="🎂 Birthday Confirmation",
            description="Please confirm your birthday details:",
            color=discord.Color.gold(),
        )

        month, day = view.birth_date.split("-")
        if view.birth_year:
            birth_display = f"{day}-{month}-{view.birth_year}"
            today = datetime.now(ZoneInfo(view.selected_timezone))
            had_birthday_this_year = (today.month, today.day) >= (int(month), int(day))
            age = today.year - view.birth_year - (0 if had_birthday_this_year else 1)
            embed.add_field(name="Age", value=f"{age} years old", inline=True)
        else:
            birth_display = f"{day}-{month}"

        embed.add_field(name="Birthday", value=birth_display, inline=True)
        embed.add_field(name="Timezone", value=view.selected_timezone, inline=True)

        tz = ZoneInfo(view.selected_timezone)
        now = datetime.now(tz)
        m, d = map(int, view.birth_date.split("-"))
        next_birthday = safe_birthday_datetime(now.year, m, d, tz)
        if next_birthday < now:
            next_birthday = safe_birthday_datetime(now.year + 1, m, d, tz)
        days_until = (next_birthday - now).days
        embed.add_field(name="Next Birthday", value=f"in {days_until} days", inline=True)

        view.clear_items()
        view.add_item(ConfirmButton())
        view.add_item(CancelButton())
        await interaction.response.edit_message(embed=embed, view=view)


class ConfirmButton(discord.ui.Button):
    """Confirm birthday setup."""

    def __init__(self):
        super().__init__(style=discord.ButtonStyle.green, label="✅ Confirm", row=1)

    async def callback(self, interaction: discord.Interaction):
        view: BirthdayView = self.view
        cog = interaction.client.get_cog("Birthday")
        if cog:
            await cog.set_birthday(
                interaction.user.id,
                view.birth_date,
                view.birth_year,
                view.selected_timezone,
            )

        embed = discord.Embed(
            title="✅ Birthday Set!",
            description="Your birthday has been saved!",
            color=discord.Color.green(),
        )
        embed.add_field(name="Date", value=view.birth_date, inline=True)
        if view.birth_year:
            embed.add_field(name="Year", value=str(view.birth_year), inline=True)
        embed.add_field(name="Timezone", value=view.selected_timezone, inline=True)
        embed.set_footer(text="You'll get wished at midnight your time! 🎉")

        await interaction.response.edit_message(embed=embed, view=None)
        await asyncio.sleep(10)
        try:
            await interaction.message.delete()
        except discord.NotFound:
            pass
        except Exception as e:
            print(f"Error deleting message: {e}")


class CancelButton(discord.ui.Button):
    """Cancel birthday setup."""

    def __init__(self):
        super().__init__(style=discord.ButtonStyle.red, label="❌ Cancel", row=1)

    async def callback(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="❌ Cancelled",
            description="Birthday setup cancelled.",
            color=discord.Color.red(),
        )
        await interaction.response.edit_message(embed=embed, view=None)
        await asyncio.sleep(5)
        try:
            await interaction.message.delete()
        except (discord.NotFound, discord.HTTPException):
            pass


class UpcomingBirthdaysView(discord.ui.View):
    """Paginated view for upcoming birthdays."""

    def __init__(
        self,
        birthdays: List[Tuple],
        guild_name: str,
        bot: commands.Bot,
        per_page: int = 10,
    ):
        super().__init__(timeout=120)
        self.birthdays = birthdays
        self.guild_name = guild_name
        self.bot = bot
        self.per_page = per_page
        self.current_page = 0
        self.max_page = (len(birthdays) - 1) // per_page
        self._add_buttons()

    def _add_buttons(self):
        self.add_item(PreviousButton(self.current_page > 0))
        self.add_item(NextButton(self.current_page < self.max_page))
        self.add_item(PageIndicator(self.current_page + 1, self.max_page + 1))
        self.add_item(CloseButton())

    def get_page_embed(self) -> discord.Embed:
        start = self.current_page * self.per_page
        end = start + self.per_page
        page_birthdays = self.birthdays[start:end]

        embed = discord.Embed(
            title=f"🎈 Upcoming Birthdays in {self.guild_name}",
            description=f"Page {self.current_page + 1}/{self.max_page + 1}",
            color=discord.Color.orange(),
        )

        for idx, (user_id, birth_date, birth_year, timezone) in enumerate(page_birthdays, 1):
            month, day = map(int, birth_date.split("-"))
            tz = ZoneInfo(timezone)
            now_local = datetime.now(tz)

            next_bday = safe_birthday_datetime(now_local.year, month, day, tz)
            if next_bday < now_local:
                next_bday = safe_birthday_datetime(now_local.year + 1, month, day, tz)

            days_until = (next_bday - now_local).days
            user = self.bot.get_user(user_id)
            name = (
                getattr(user, "display_name", None) or getattr(user, "name", f"{user_id}")
                if user
                else f"Unknown ({user_id})"
            )

            display_date = f"{day:02d}-{month:02d}"
            age = f" (turning {next_bday.year - birth_year})" if birth_year else ""
            if days_until == 0:
                days_str = "🎂 **Today!**"
            elif days_until == 1:
                days_str = "⏰ **Tomorrow!**"
            else:
                days_str = f"⏰ {days_until} days away"

            embed.add_field(
                name=f"{start + idx}. {name}",
                value=f"📅 {display_date}{age}\n{days_str}",
                inline=False,
            )

        embed.set_footer(text="Use buttons to navigate • Times are in their local timezone")
        return embed


class PreviousButton(discord.ui.Button):
    def __init__(self, enabled: bool):
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label="◀ Previous",
            disabled=not enabled,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        view: UpcomingBirthdaysView = self.view
        view.current_page -= 1
        view.clear_items()
        view._add_buttons()
        await interaction.response.edit_message(embed=view.get_page_embed(), view=view)


class NextButton(discord.ui.Button):
    def __init__(self, enabled: bool):
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label="Next ▶",
            disabled=not enabled,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        view: UpcomingBirthdaysView = self.view
        view.current_page += 1
        view.clear_items()
        view._add_buttons()
        await interaction.response.edit_message(embed=view.get_page_embed(), view=view)


class PageIndicator(discord.ui.Button):
    def __init__(self, current: int, total: int):
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label=f"📄 {current}/{total}",
            disabled=True,
            row=0,
        )


class CloseButton(discord.ui.Button):
    def __init__(self):
        super().__init__(style=discord.ButtonStyle.danger, label="❌ Close", row=0)

    async def callback(self, interaction: discord.Interaction):
        await interaction.message.delete()


class ConfirmRemoveButton(discord.ui.Button):
    def __init__(self):
        super().__init__(style=discord.ButtonStyle.danger, label="✅ Yes, Remove", row=0)

    async def callback(self, interaction: discord.Interaction):
        cog: Birthday = interaction.client.get_cog("Birthday")
        if cog and getattr(cog, "db", None):
            async with cog.db_lock:
                await cog.db.execute(
                    "DELETE FROM users WHERE user_id = ?", (interaction.user.id,)
                )
                await cog.db.execute(
                    "DELETE FROM announcements WHERE user_id = ?", (interaction.user.id,)
                )
                await cog.db.execute(
                    "DELETE FROM birthday_dms WHERE user_id = ?", (interaction.user.id,)
                )
                await cog.db.commit()

        embed = discord.Embed(
            title="✅ Birthday Removed",
            description="Your birthday data has been removed from the system.",
            color=discord.Color.green(),
        )
        await interaction.response.edit_message(embed=embed, view=None)
        await asyncio.sleep(5)
        await interaction.message.delete()


# ─── COG ─────────────────────────────────────────────────────

class Birthday(commands.Cog):
    """🎂 Birthday Management System"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db: Optional[aiosqlite.Connection] = None
        self.db_lock: asyncio.Lock = asyncio.Lock()
        self.bot.loop.create_task(self.init_database())

    async def init_database(self):
        """Initialize database tables."""
        os.makedirs(os.path.dirname(DB_PATH) if os.path.dirname(DB_PATH) else ".", exist_ok=True)
        self.db = await aiosqlite.connect(DB_PATH)
        await self.db.execute("PRAGMA foreign_keys = ON;")

        # Users table — includes last_updated_at for change-cooldown enforcement.
        # We use INSERT OR IGNORE + UPDATE so created_at is never overwritten.
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id       INTEGER PRIMARY KEY,
                birth_date    TEXT    NOT NULL,
                birth_year    INTEGER,
                timezone      TEXT    NOT NULL,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Migrate: add last_updated_at if it doesn't exist yet (upgrading from old schema)
        try:
            await self.db.execute("ALTER TABLE users ADD COLUMN last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        except Exception:
            pass  # Column already exists

        # Announcements table — UNIQUE(user_id, guild_id, year) prevents duplicate rows
        # and makes INSERT OR IGNORE idempotent (safe for the grace-window retry logic).
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS announcements (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                guild_id   INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                year       INTEGER NOT NULL,
                announced  BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, guild_id, year),
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)

        # Persistent DM-sent tracker — survives bot restarts so we never double-DM.
        # Separate from announcements because a DM is per-user, not per-guild.
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS birthday_dms (
                user_id INTEGER NOT NULL,
                year    INTEGER NOT NULL,
                PRIMARY KEY (user_id, year)
            )
        """)

        await self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_birth_date ON users(birth_date)"
        )
        await self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_timezone ON users(timezone)"
        )

        await self.db.commit()
        print("✅ Birthday database initialized")

        try:
            self._birthday_check_loop.start()
        except RuntimeError:
            pass

    # ─── BIRTHDAY CHECK LOOP ──────────────────────────────────
    # FIX: Renamed from birthday_check → _birthday_check_loop to avoid Python
    # attribute collision with the /birthday check slash-command method below.
    # Both were previously named birthday_check; Python silently overwrote the
    # tasks.loop with the command method, so the loop never ran.

    @tasks.loop(minutes=1)
    async def _birthday_check_loop(self):
        """Check for birthdays every minute."""
        now = datetime.now(pytz.UTC)

        async with self.db_lock:
            cursor = await self.db.execute(
                "SELECT user_id, birth_date, birth_year, timezone FROM users"
            )
            users = await cursor.fetchall()

        for user_id, birth_date, birth_year, tz_name in users:
            try:
                tz = ZoneInfo(tz_name)
                local_time = now.astimezone(tz)

                # FIX: Use a grace window (0–BIRTHDAY_GRACE_MINUTES past midnight)
                # instead of only minute==0.  If the bot was down at midnight it will
                # catch up within the grace period.  INSERT OR IGNORE on the
                # announcements table makes this safe — duplicate fires are no-ops.
                if local_time.hour == 0 and local_time.minute <= BIRTHDAY_GRACE_MINUTES:
                    month, day = map(int, birth_date.split("-"))
                    today_str = local_time.strftime("%m-%d")

                    if birth_date == today_str:
                        await self.send_birthday_wishes(user_id, local_time.year)

            except Exception as e:
                print(f"Birthday check error for user {user_id}: {e}")

    @_birthday_check_loop.before_loop
    async def _before_birthday_check_loop(self):
        await self.bot.wait_until_ready()

    # ─── SEND BIRTHDAY WISHES ────────────────────────────────

    async def send_birthday_wishes(self, user_id: int, year: int):
        """Send birthday wishes to all guilds the user is in."""
        user = self.bot.get_user(user_id)
        if not user:
            return

        async with self.db_lock:
            cursor = await self.db.execute(
                "SELECT birth_date, birth_year, timezone FROM users WHERE user_id = ?",
                (user_id,),
            )
            user_data = await cursor.fetchone()

        if not user_data:
            return

        birth_date, birth_year, tz_name = user_data
        age_text = f" turning {year - birth_year}" if birth_year else ""
        age_num = year - birth_year if birth_year else None

        # FIX: Persist DM tracking in the database instead of an in-memory set.
        # The old _dm_sent_cache was wiped on every bot restart, causing duplicate DMs.
        async with self.db_lock:
            cursor = await self.db.execute(
                "SELECT 1 FROM birthday_dms WHERE user_id = ? AND year = ?",
                (user_id, year),
            )
            dm_already_sent = await cursor.fetchone()

        if not dm_already_sent:
            # Mark as sent *before* attempting — prevents a crash mid-send from
            # leaving it unmarked and causing a re-send on the next loop tick.
            async with self.db_lock:
                await self.db.execute(
                    "INSERT OR IGNORE INTO birthday_dms (user_id, year) VALUES (?, ?)",
                    (user_id, year),
                )
                await self.db.commit()
            try:
                dm_embed = discord.Embed(
                    title="🎂 Happy Birthday! 🎂",
                    description=(
                        "**Happy Birthday to you!** 🎉\n\n"
                        "Today is your special day and we have announced it in the server!"
                    ),
                    color=discord.Color.gold(),
                )
                if age_num:
                    dm_embed.add_field(
                        name="You're",
                        value=f"{age_num} years old today!",
                        inline=True,
                    )
                dm_embed.add_field(
                    name="What happens now?",
                    value=(
                        "• You've been wished in the server\n"
                        "• You will get a special birthday role\n"
                        "• Everyone can celebrate with you!\n\n"
                        "Have an amazing day! 🎈"
                    ),
                    inline=False,
                )
                dm_embed.set_footer(text="With <3 from The Aakhri Team ❤️")
                await user.send(embed=dm_embed)
            except discord.Forbidden:
                print(f"Could not send birthday DM to {user_id} - DMs disabled")
            except Exception as e:
                print(f"Error sending birthday DM to {user_id}: {e}")

        for guild in self.bot.guilds:
            member = guild.get_member(user_id)
            if not member:
                continue

            if not ANNOUNCEMENT_CHANNEL_ID:
                continue

            channel = guild.get_channel(ANNOUNCEMENT_CHANNEL_ID)
            if not channel:
                continue

            # FIX: INSERT OR IGNORE is atomic — no race condition between check
            # and insert.  The old code used SELECT then INSERT, which could fire
            # twice if two loop ticks raced.  We check rowcount instead.
            async with self.db_lock:
                cursor = await self.db.execute(
                    """
                    INSERT OR IGNORE INTO announcements
                        (user_id, guild_id, channel_id, year, announced)
                    VALUES (?, ?, ?, ?, 1)
                    """,
                    (user_id, guild.id, channel.id, year),
                )
                inserted = cursor.rowcount > 0
                await self.db.commit()

            if not inserted:
                # Already announced in this guild this year — skip.
                continue

            embed = discord.Embed(
                title="🎂 Happy Birthday! 🎂",
                description=f"# **{member.display_name}** is celebrating their birthday today!",
                color=discord.Color.gold(),
            )
            embed.set_image(
                url=os.environ.get(
                    "BIRTHDAY_IMAGE_URL",
                    "https://cdn.discordapp.com/attachments/1453780776238845982/1479027409725689868/discordbday.jpg",
                )
            )
            if age_text:
                embed.add_field(name="Age", value=age_text, inline=True)
            embed.set_thumbnail(url=user.display_avatar.url)

            await channel.send(content=f"🎉 {member.mention}", embed=embed)

            if BIRTHDAY_ROLE_ID:
                role = guild.get_role(BIRTHDAY_ROLE_ID)
                if role:
                    try:
                        await member.add_roles(role, reason="Birthday role")
                        asyncio.create_task(
                            self.remove_role_after(guild.id, user_id, BIRTHDAY_ROLE_ID, 86400)
                        )
                    except Exception:
                        pass

    async def remove_role_after(self, guild_id: int, user_id: int, role_id: int, delay: int):
        """Remove birthday role after delay."""
        await asyncio.sleep(delay)
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        member = guild.get_member(user_id)
        if not member:
            return
        role = guild.get_role(role_id)
        if role:
            try:
                await member.remove_roles(role, reason="Birthday role expired")
            except Exception:
                pass

    # ─── COMMANDS ─────────────────────────────────────────────

    birthday_group = app_commands.Group(name="birthday", description="Birthday commands")

    @birthday_group.command(name="add", description="Set your birthday")
    @app_commands.describe(date="Your birthday in DD-MM-YYYY format (year optional)")
    async def birthday_add(self, interaction: discord.Interaction, date: str):
        """Set your birthday with timezone selection."""
        date_pattern = r"^(\d{2}-\d{2}(-\d{4})?)$"
        if not re.match(date_pattern, date):
            embed = discord.Embed(
                title="❌ Invalid Format",
                description="Please use format: `DD-MM-YYYY` or `DD-MM`\nExample: `15-03-2000` or `15-03`",
                color=discord.Color.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        parts = date.split("-")
        if len(parts) == 3:
            day, month, year = map(int, parts)
            birth_date = f"{month:02d}-{day:02d}"
            birth_year = year
        else:
            day, month = map(int, parts)
            birth_date = f"{month:02d}-{day:02d}"
            birth_year = None

        # Validate the date actually exists
        try:
            date_class(birth_year or 2000, month, day)
        except ValueError:
            embed = discord.Embed(
                title="❌ Invalid Date",
                description="That date doesn't exist!",
                color=discord.Color.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        current_year = datetime.now().year

        if birth_year:
            if birth_year < 1900:
                embed = discord.Embed(
                    title="❌ Invalid Year",
                    description="Birth year cannot be before 1900.",
                    color=discord.Color.red(),
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
            if birth_year >= current_year:
                embed = discord.Embed(
                    title="❌ Invalid Year",
                    description="Birth year must be in the past.",
                    color=discord.Color.red(),
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
            # FIX: Enforce minimum age (Discord ToS requires 13+).
            if current_year - birth_year < MIN_AGE_YEARS:
                embed = discord.Embed(
                    title="❌ Age Requirement",
                    description=f"You must be at least {MIN_AGE_YEARS} years old to use this bot.",
                    color=discord.Color.red(),
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

        # FIX: Enforce change cooldown — users cannot alter their birthday more
        # than once every BIRTHDAY_CHANGE_COOLDOWN_DAYS days.
        async with self.db_lock:
            cursor = await self.db.execute(
                "SELECT last_updated_at FROM users WHERE user_id = ?",
                (interaction.user.id,),
            )
            existing = await cursor.fetchone()

        if existing and existing[0]:
            try:
                last_updated = datetime.fromisoformat(existing[0])
                days_since = (datetime.utcnow() - last_updated).days
                if days_since < BIRTHDAY_CHANGE_COOLDOWN_DAYS:
                    days_left = BIRTHDAY_CHANGE_COOLDOWN_DAYS - days_since
                    embed = discord.Embed(
                        title="⏳ Cooldown Active",
                        description=(
                            f"You can only change your birthday once every "
                            f"**{BIRTHDAY_CHANGE_COOLDOWN_DAYS} days**.\n"
                            f"You can change it again in **{days_left} day(s)**.\n\n"
                            f"If this is a mistake, please contact an admin."
                        ),
                        color=discord.Color.orange(),
                    )
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                    return
            except ValueError:
                pass  # Malformed timestamp — allow the update

        # FIX: Soft duplicate / alt-account detection.
        # Warn the admin channel if too many users share the same exact birthday.
        if birth_year:
            async with self.db_lock:
                cursor = await self.db.execute(
                    "SELECT COUNT(*) FROM users WHERE birth_date = ? AND birth_year = ?",
                    (birth_date, birth_year),
                )
                dupe_count = (await cursor.fetchone())[0]

            if dupe_count >= DUPLICATE_BIRTHDAY_THRESHOLD:
                print(
                    f"[BIRTHDAY WARNING] {dupe_count + 1} users now share "
                    f"birth_date={birth_date} birth_year={birth_year}. "
                    f"Possible alt accounts — latest request from user_id={interaction.user.id}."
                )
                # Optionally notify an admin channel here if you wire up a log channel env var.

        view = BirthdayView(interaction.user.id, birth_date, birth_year)
        embed = discord.Embed(
            title="🌍 Select Your Timezone",
            description=(
                "Please select your timezone from the dropdown below.\n"
                "This ensures you get wished at exactly midnight your time!"
            ),
            color=discord.Color.orange(),
        )
        embed.add_field(name="Your Birthday", value=date, inline=True)

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        view.message = await interaction.original_response()

    async def set_birthday(
        self,
        user_id: int,
        birth_date: str,
        birth_year: Optional[int],
        timezone: str,
        *,
        bypass_cooldown: bool = False,
    ):
        """Save birthday to database.

        FIX: Uses INSERT OR IGNORE + UPDATE so that created_at is never
        overwritten on subsequent saves, preserving the original registration
        timestamp needed for cooldown tracking.
        """
        async with self.db_lock:
            # Insert a new row only if the user doesn't exist yet.
            await self.db.execute(
                """
                INSERT OR IGNORE INTO users (user_id, birth_date, birth_year, timezone,
                                             created_at, last_updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (user_id, birth_date, birth_year, timezone),
            )
            # Update all mutable fields for existing rows.
            await self.db.execute(
                """
                UPDATE users
                SET birth_date = ?, birth_year = ?, timezone = ?,
                    last_updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ?
                """,
                (birth_date, birth_year, timezone, user_id),
            )
            await self.db.commit()

    @birthday_group.command(name="upcoming", description="Show upcoming birthdays")
    async def birthday_upcoming(self, interaction: discord.Interaction):
        """Show paginated list of upcoming birthdays."""
        await interaction.response.defer(ephemeral=True)

        async with self.db_lock:
            cursor = await self.db.execute(
                "SELECT user_id, birth_date, birth_year, timezone FROM users"
            )
            all_users = await cursor.fetchall()

        if not all_users:
            embed = discord.Embed(
                title="📭 No Birthdays",
                description="No birthdays have been set in this server yet!",
                color=discord.Color.orange(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        upcoming = []
        for user_id, birth_date, birth_year, tz_name in all_users:
            member = interaction.guild.get_member(user_id)
            if not member:
                continue
            month, day = map(int, birth_date.split("-"))
            tz = ZoneInfo(tz_name)
            now_local = datetime.now(tz)
            next_bday = safe_birthday_datetime(now_local.year, month, day, tz)
            if next_bday < now_local:
                next_bday = safe_birthday_datetime(now_local.year + 1, month, day, tz)
            days_until = (next_bday - now_local).days
            upcoming.append((days_until, user_id, birth_date, birth_year, tz_name))

        upcoming.sort()
        upcoming = [(uid, bd, by, tz) for _, uid, bd, by, tz in upcoming[:50]]

        if not upcoming:
            embed = discord.Embed(
                title="📭 No Upcoming Birthdays",
                description="No upcoming birthdays found in this server.",
                color=discord.Color.orange(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        view = UpcomingBirthdaysView(upcoming, interaction.guild.name, self.bot)
        await interaction.followup.send(embed=view.get_page_embed(), view=view, ephemeral=True)

    @birthday_group.command(name="remove", description="Remove your birthday from the system")
    async def birthday_remove(self, interaction: discord.Interaction):
        view = discord.ui.View()
        view.add_item(ConfirmRemoveButton())
        view.add_item(CancelButton())
        embed = discord.Embed(
            title="⚠️ Confirm Removal",
            description="Are you sure you want to remove your birthday data?",
            color=discord.Color.orange(),
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    # FIX: Renamed from birthday_check → birthday_lookup to avoid clobbering the
    # tasks.loop attribute (_birthday_check_loop) and to be self-documenting.
    @birthday_group.command(name="check", description="Check a user's birthday")
    @app_commands.describe(user="The user to look up (leave blank for yourself)")
    async def birthday_lookup(
        self,
        interaction: discord.Interaction,
        user: Optional[discord.Member] = None,
    ):
        """Look up any member's birthday."""
        await interaction.response.defer(ephemeral=True)

        target = user or interaction.user
        async with self.db_lock:
            cursor = await self.db.execute(
                "SELECT birth_date, birth_year, timezone FROM users WHERE user_id = ?",
                (target.id,),
            )
            row = await cursor.fetchone()

        if not row:
            who = "You haven't" if target == interaction.user else f"{target.display_name} hasn't"
            embed = discord.Embed(
                title="❓ No Birthday Set",
                description=f"{who} set a birthday yet.",
                color=discord.Color.orange(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        birth_date, birth_year, tz_name = row
        month, day = map(int, birth_date.split("-"))
        tz = ZoneInfo(tz_name)
        now_local = datetime.now(tz)

        # FIX: Use safe_birthday_datetime so Feb-29 people don't crash this command.
        next_bday = safe_birthday_datetime(now_local.year, month, day, tz)
        if next_bday < now_local:
            next_bday = safe_birthday_datetime(now_local.year + 1, month, day, tz)
        days_until = (next_bday - now_local).days

        display_date = f"{day:02d}-{month:02d}" + (f"-{birth_year}" if birth_year else "")
        if days_until == 0:
            days_str = "🎂 **Today is their birthday!**"
        elif days_until == 1:
            days_str = "⏰ **Tomorrow!**"
        else:
            days_str = f"⏰ {days_until} days away"

        embed = discord.Embed(
            title=f"🎂 {target.display_name}'s Birthday",
            color=discord.Color.gold(),
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="📅 Date", value=display_date, inline=True)
        embed.add_field(name="🌍 Timezone", value=tz_name, inline=True)

        if birth_year:
            had_birthday = (now_local.month, now_local.day) >= (month, day)
            current_age = now_local.year - birth_year - (0 if had_birthday else 1)
            next_age = next_bday.year - birth_year
            embed.add_field(
                name="🎈 Age",
                value=f"{current_age} (turning {next_age})",
                inline=True,
            )

        embed.add_field(name="Next Birthday", value=days_str, inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @birthday_group.command(name="today", description="See who has a birthday today")
    async def birthday_today(self, interaction: discord.Interaction):
        """Show all members celebrating a birthday right now."""
        await interaction.response.defer(ephemeral=True)

        async with self.db_lock:
            cursor = await self.db.execute(
                "SELECT user_id, birth_date, birth_year, timezone FROM users"
            )
            all_users = await cursor.fetchall()

        today_birthdays = []
        for user_id, birth_date, birth_year, tz_name in all_users:
            member = interaction.guild.get_member(user_id)
            if not member:
                continue
            try:
                tz = ZoneInfo(tz_name)
                local_now = datetime.now(tz)
                if local_now.strftime("%m-%d") == birth_date:
                    today_birthdays.append((member, birth_year, local_now.year))
            except Exception:
                continue

        if not today_birthdays:
            embed = discord.Embed(
                title="🎂 No Birthdays Today",
                description="Nobody in this server has a birthday today (in their local timezone).",
                color=discord.Color.orange(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        embed = discord.Embed(
            title=f"🎉 Today's Birthdays — {interaction.guild.name}",
            description=f"**{len(today_birthdays)}** member(s) are celebrating today!",
            color=discord.Color.gold(),
        )
        for member, birth_year, current_year in today_birthdays:
            age_str = ""
            if birth_year:
                age = current_year - birth_year
                age_str = f" — turning **{age}** 🎈"
            embed.add_field(
                name=f"🎂 {member.display_name}{age_str}",
                value=member.mention,
                inline=False,
            )
        await interaction.followup.send(embed=embed)

    # ─── ADMIN SUBGROUP ───────────────────────────────────────

    birthday_admin = app_commands.Group(
        name="birthday_admin",
        description="Admin birthday management",
        default_permissions=discord.Permissions(administrator=True),
    )

    @birthday_admin.command(name="set", description="Set a birthday for any user")
    @app_commands.describe(
        user="The member to set birthday for",
        date="Birthday in DD-MM-YYYY or DD-MM format",
        timezone="Timezone (e.g. Asia/Kolkata)",
    )
    async def admin_set(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        date: str,
        timezone: str = "UTC",
    ):
        """Admin: set or overwrite a member's birthday (bypasses cooldown)."""
        await interaction.response.defer(ephemeral=True)

        date_pattern = r"^(\d{2}-\d{2}(-\d{4})?)$"
        if not re.match(date_pattern, date):
            await interaction.followup.send("❌ Use format `DD-MM-YYYY` or `DD-MM`.", ephemeral=True)
            return

        parts = date.split("-")
        if len(parts) == 3:
            day, month, year = map(int, parts)
            birth_year = year
        else:
            day, month = map(int, parts)
            birth_year = None

        try:
            date_class(birth_year or 2000, month, day)
        except ValueError:
            await interaction.followup.send("❌ That date doesn't exist.", ephemeral=True)
            return

        current_year = datetime.now().year
        if birth_year:
            if birth_year < 1900 or birth_year >= current_year:
                await interaction.followup.send(
                    "❌ Birth year must be between 1900 and last year.", ephemeral=True
                )
                return
            # FIX: Admin set also enforces minimum age.
            if current_year - birth_year < MIN_AGE_YEARS:
                await interaction.followup.send(
                    f"❌ Birth year implies age under {MIN_AGE_YEARS}. Not allowed.", ephemeral=True
                )
                return

        try:
            ZoneInfo(timezone)
        except Exception:
            await interaction.followup.send(
                f"❌ Unknown timezone `{timezone}`. Use a valid IANA name like `Asia/Kolkata`.",
                ephemeral=True,
            )
            return

        birth_date = f"{month:02d}-{day:02d}"
        # bypass_cooldown=True so admins can correct data without restriction.
        await self.set_birthday(user.id, birth_date, birth_year, timezone, bypass_cooldown=True)

        display = f"{day:02d}-{month:02d}" + (f"-{birth_year}" if birth_year else "")
        embed = discord.Embed(
            title="✅ Birthday Set",
            description=f"Set birthday for {user.mention}",
            color=discord.Color.green(),
        )
        embed.add_field(name="Date", value=display, inline=True)
        embed.add_field(name="Timezone", value=timezone, inline=True)
        embed.set_footer(text=f"Set by {interaction.user}")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @birthday_admin.command(name="remove", description="Remove a birthday for any user")
    @app_commands.describe(user="The member whose birthday to remove")
    async def admin_remove(self, interaction: discord.Interaction, user: discord.Member):
        """Admin: forcibly remove a member's birthday data."""
        await interaction.response.defer(ephemeral=True)

        async with self.db_lock:
            cursor = await self.db.execute(
                "SELECT user_id FROM users WHERE user_id = ?", (user.id,)
            )
            exists = await cursor.fetchone()

        if not exists:
            await interaction.followup.send(
                f"❌ {user.mention} has no birthday set.", ephemeral=True
            )
            return

        async with self.db_lock:
            await self.db.execute("DELETE FROM users WHERE user_id = ?", (user.id,))
            await self.db.execute("DELETE FROM announcements WHERE user_id = ?", (user.id,))
            await self.db.execute("DELETE FROM birthday_dms WHERE user_id = ?", (user.id,))
            await self.db.commit()

        embed = discord.Embed(
            title="✅ Birthday Removed",
            description=f"Removed birthday data for {user.mention}.",
            color=discord.Color.green(),
        )
        embed.set_footer(text=f"Removed by {interaction.user}")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @birthday_admin.command(name="stats", description="Show birthday system statistics")
    async def admin_stats(self, interaction: discord.Interaction):
        """Admin: show DB stats."""
        await interaction.response.defer(ephemeral=True)

        async with self.db_lock:
            cursor = await self.db.execute("SELECT COUNT(*) FROM users")
            total_users = (await cursor.fetchone())[0]

            cursor = await self.db.execute(
                "SELECT COUNT(*) FROM users WHERE birth_year IS NOT NULL"
            )
            with_year = (await cursor.fetchone())[0]

            cursor = await self.db.execute("SELECT COUNT(*) FROM announcements")
            total_announcements = (await cursor.fetchone())[0]

            cursor = await self.db.execute(
                "SELECT COUNT(*) FROM announcements WHERE year = ?",
                (datetime.now().year,),
            )
            this_year = (await cursor.fetchone())[0]

            # FIX: Replaced the broken placeholder code that always returned 0.
            cursor = await self.db.execute("SELECT user_id FROM users")
            all_ids = {row[0] for row in await cursor.fetchall()}

        guild_count = sum(1 for m in interaction.guild.members if m.id in all_ids)

        embed = discord.Embed(
            title="📊 Birthday System Stats",
            color=discord.Color.blurple(),
            timestamp=datetime.now(),
        )
        embed.add_field(name="👥 Total Registered", value=f"`{total_users}`", inline=True)
        embed.add_field(name="🎂 With Birth Year", value=f"`{with_year}`", inline=True)
        embed.add_field(name="📢 All-time Announcements", value=f"`{total_announcements}`", inline=True)
        embed.add_field(
            name=f"📅 Announcements {datetime.now().year}",
            value=f"`{this_year}`",
            inline=True,
        )
        embed.add_field(
            name="🏠 Registered in This Server",
            value=f"`{guild_count}` / `{interaction.guild.member_count}`",
            inline=True,
        )
        embed.set_footer(text=f"Requested by {interaction.user}")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ─── CLEANUP ─────────────────────────────────────────────

    def cog_unload(self):
        self._birthday_check_loop.cancel()
        try:
            self.bot.loop.create_task(self._close_db())
        except Exception:
            pass

    async def _close_db(self):
        if self.db:
            try:
                await self.db.close()
            except Exception:
                pass


async def setup(bot: commands.Bot):
    cog = Birthday(bot)
    await bot.add_cog(cog)
    bot.tree.add_command(cog.birthday_admin)