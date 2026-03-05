import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiosqlite
import asyncio
import os
from datetime import datetime, date as date_class
from typing import Optional, List, Tuple
import pytz
from zoneinfo import ZoneInfo
import re

# ─── CONFIG (env vars) ───────────────────────────────────────
DB_PATH = os.environ.get("BIRTHDAY_DB_PATH", "data/birthdays.db")
ANNOUNCEMENT_CHANNEL_ID = int(os.environ.get("BIRTHDAY_CHANNEL_ID", "0")) or None
BIRTHDAY_ROLE_ID = int(os.environ.get("BIRTHDAY_ROLE_ID", "0")) or None


class BirthdayView(discord.ui.View):
    """View for timezone selection."""

    def __init__(self, user_id: int, birth_date: str, birth_year: Optional[int]):
        super().__init__(timeout=300)  # 5 minute timeout
        self.user_id = user_id
        self.birth_date = birth_date
        self.birth_year = birth_year
        self.selected_timezone = None

        # Add timezone select menu
        self.add_item(TimezoneSelect())

    async def on_timeout(self):
        """Delete message on timeout."""
        for item in self.children:
            item.disabled = True
        await self.message.edit(view=self)
        await asyncio.sleep(5)
        await self.message.delete()


class TimezoneSelect(discord.ui.Select):
    """Timezone selection dropdown."""

    def __init__(self):
        # Get common timezones first
        common_zones = [
            # --- South Asia (Priority) ---
            "Asia/Kolkata",  # India
            "Asia/Karachi",  # Pakistan
            "Asia/Dhaka",  # Bangladesh
            "Asia/Colombo",  # Sri Lanka
            "Asia/Kathmandu",  # Nepal
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
        """Handle timezone selection."""
        view: BirthdayView = self.view
        view.selected_timezone = self.values[0]

        # Disable select
        self.disabled = True

        # Create confirmation embed
        embed = discord.Embed(
            title="🎂 Birthday Confirmation",
            description="Please confirm your birthday details:",
            color=discord.Color.gold(),
        )

        birth_display = view.birth_date
        if view.birth_year:
            birth_display += f" ({view.birth_year})"
            age = datetime.now().year - view.birth_year
            embed.add_field(name="Age", value=f"{age} years old", inline=True)

        embed.add_field(name="Birthday", value=birth_display, inline=True)
        embed.add_field(name="Timezone", value=view.selected_timezone, inline=True)

        # Calculate next birthday
        tz = ZoneInfo(view.selected_timezone)
        now = datetime.now(tz)
        month, day = map(int, view.birth_date.split("-"))
        next_birthday = datetime(now.year, month, day, tzinfo=tz)

        if next_birthday < now:
            next_birthday = datetime(now.year + 1, month, day, tzinfo=tz)

        days_until = (next_birthday - now).days
        embed.add_field(
            name="Next Birthday", value=f"in {days_until} days", inline=True
        )

        # Add confirm/cancel buttons
        view.clear_items()
        view.add_item(ConfirmButton())
        view.add_item(CancelButton())

        await interaction.response.edit_message(embed=embed, view=view)


class ConfirmButton(discord.ui.Button):
    """Confirm birthday setup."""

    def __init__(self):
        super().__init__(style=discord.ButtonStyle.green, label="✅ Confirm", row=1)

    async def callback(self, interaction: discord.Interaction):
        """Save birthday data."""
        view: BirthdayView = self.view

        # Save to database
        cog = interaction.client.get_cog("Birthday")
        if cog:
            await cog.set_birthday(
                interaction.user.id,
                view.birth_date,
                view.birth_year,
                view.selected_timezone,
            )

        # Success message
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

        # Delete after 10 seconds with error handling
        await asyncio.sleep(10)
        try:
            await interaction.message.delete()
        except discord.NotFound:
            pass  # Message already deleted
        except Exception as e:
            print(f"Error deleting message: {e}")


class CancelButton(discord.ui.Button):
    """Cancel birthday setup."""

    def __init__(self):
        super().__init__(style=discord.ButtonStyle.red, label="❌ Cancel", row=1)

    async def callback(self, interaction: discord.Interaction):
        """Cancel and delete message."""
        embed = discord.Embed(
            title="❌ Cancelled",
            description="Birthday setup cancelled.",
            color=discord.Color.red(),
        )
        await interaction.response.edit_message(embed=embed, view=None)
        await asyncio.sleep(5)
        await interaction.message.delete()


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

        # Add buttons directly in init instead of calling separate method
        self._add_buttons()

    def _add_buttons(self):
        """Add navigation buttons."""
        # Previous button
        self.add_item(PreviousButton(self.current_page > 0))

        # Next button
        self.add_item(NextButton(self.current_page < self.max_page))

        # Page indicator
        self.add_item(PageIndicator(self.current_page + 1, self.max_page + 1))

        # Close button
        self.add_item(CloseButton())

    def get_page_embed(self) -> discord.Embed:
        """Get embed for current page."""
        start = self.current_page * self.per_page
        end = start + self.per_page
        page_birthdays = self.birthdays[start:end]

        embed = discord.Embed(
            title=f"🎈 Upcoming Birthdays in {self.guild_name}",
            description=f"Page {self.current_page + 1}/{self.max_page + 1}",
            color=discord.Color.orange(),
        )

        for idx, (user_id, birth_date, birth_year, timezone) in enumerate(
            page_birthdays, 1
        ):
            # Calculate next birthday
            month, day = map(int, birth_date.split("-"))
            tz = ZoneInfo(timezone)
            now_local = datetime.now(tz)

            next_bday = datetime(now_local.year, month, day, tzinfo=tz)
            if next_bday < now_local:
                next_bday = datetime(now_local.year + 1, month, day, tzinfo=tz)

            days_until = (next_bday - now_local).days

            # Get user
            user = self.bot.get_user(user_id)
            if user:
                name = getattr(user, "display_name", None) or getattr(
                    user, "name", f"{user_id}"
                )
            else:
                name = f"Unknown ({user_id})"

            # Build display string
            age = f" turning {next_bday.year - birth_year}" if birth_year else ""

            embed.add_field(
                name=f"{start + idx}. {name}",
                value=f"📅 {birth_date}{age}\n⏰ {days_until} days away",
                inline=False,
            )

        embed.set_footer(
            text="Use buttons to navigate • Times are in their local timezone"
        )
        return embed


class PreviousButton(discord.ui.Button):
    """Go to previous page."""

    def __init__(self, enabled: bool):
        super().__init__(
            style=discord.ButtonStyle.primary,
            label="◀ Previous",
            disabled=not enabled,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        view: UpcomingBirthdaysView = self.view
        view.current_page -= 1

        # Clear and re-add buttons with updated states
        view.clear_items()
        view._add_buttons()

        await interaction.response.edit_message(embed=view.get_page_embed(), view=view)


class NextButton(discord.ui.Button):
    """Go to next page."""

    def __init__(self, enabled: bool):
        super().__init__(
            style=discord.ButtonStyle.primary,
            label="Next ▶",
            disabled=not enabled,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        view: UpcomingBirthdaysView = self.view
        view.current_page += 1

        # Clear and re-add buttons with updated states
        view.clear_items()
        view._add_buttons()

        await interaction.response.edit_message(embed=view.get_page_embed(), view=view)


class PageIndicator(discord.ui.Button):
    """Show current page."""

    def __init__(self, current: int, total: int):
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label=f"📄 {current}/{total}",
            disabled=True,
            row=0,
        )


class CloseButton(discord.ui.Button):
    """Close the paginated view."""

    def __init__(self):
        super().__init__(style=discord.ButtonStyle.danger, label="❌ Close", row=0)

    async def callback(self, interaction: discord.Interaction):
        await interaction.message.delete()


class ConfirmRemoveButton(discord.ui.Button):
    """Confirm birthday removal."""

    def __init__(self):
        super().__init__(
            style=discord.ButtonStyle.danger, label="✅ Yes, Remove", row=0
        )

    async def callback(self, interaction: discord.Interaction):
        """Remove user data."""
        cog: Birthday = interaction.client.get_cog("Birthday")
        if cog and getattr(cog, "db", None):
            async with cog.db_lock:
                await cog.db.execute(
                    "DELETE FROM users WHERE user_id = ?", (interaction.user.id,)
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


class Birthday(commands.Cog):
    """🎂 Birthday Management System"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db: Optional[aiosqlite.Connection] = None
        self.db_lock: asyncio.Lock = asyncio.Lock()
        # Initialize DB and start the birthday check after DB ready
        self.bot.loop.create_task(self.init_database())

    async def init_database(self):
        """Initialize database tables."""
        self.db = await aiosqlite.connect(DB_PATH)
        # Ensure foreign keys and pragmas for performance
        await self.db.execute("PRAGMA foreign_keys = ON;")

        # Users table
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                birth_date TEXT NOT NULL,
                birth_year INTEGER,
                timezone TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Announcements table
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS announcements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                year INTEGER NOT NULL,
                announced BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)

        # Create indexes
        await self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_birth_date ON users(birth_date)"
        )
        await self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_timezone ON users(timezone)"
        )

        await self.db.commit()
        print("✅ Birthday database initialized")

        # Start the birthday check loop after DB ready
        try:
            self.birthday_check.start()
        except RuntimeError:
            pass

    @tasks.loop(minutes=1)
    async def birthday_check(self):
        """Check for birthdays every minute."""
        now = datetime.now(pytz.UTC)
        # Get all users (reuse persistent connection)
        async with self.db_lock:
            cursor = await self.db.execute("""
                SELECT user_id, birth_date, birth_year, timezone 
                FROM users
            """)
            users = await cursor.fetchall()

        for user_id, birth_date, birth_year, tz_name in users:
            try:
                # Check if it's birthday in their timezone
                tz = ZoneInfo(tz_name)
                local_time = now.astimezone(tz)

                # Is it midnight? (0:00 - 0:01 window)
                if local_time.hour == 0 and local_time.minute == 0:
                    today = local_time.strftime("%m-%d")

                    if birth_date == today:
                        await self.send_birthday_wishes(user_id, local_time.year)

            except Exception as e:
                print(f"Birthday check error for user {user_id}: {e}")

    async def send_birthday_wishes(self, user_id: int, year: int):
        """Send birthday wishes to all guilds the user is in."""
        user = self.bot.get_user(user_id)
        if not user:
            return
        # Get user's birthday data (reuse connection)
        async with self.db_lock:
            cursor = await self.db.execute(
                """
                SELECT birth_date, birth_year, timezone 
                FROM users WHERE user_id = ?
            """,
                (user_id,),
            )
            user_data = await cursor.fetchone()

        if not user_data:
            return

        birth_date, birth_year, tz_name = user_data

        # Calculate age
        age = f" turning {year - birth_year}" if birth_year else ""

        # Send to each guild the user is in
        for guild in self.bot.guilds:
            member = guild.get_member(user_id)
            if not member:
                continue

            # Use hardcoded channel from env
            if not ANNOUNCEMENT_CHANNEL_ID:
                continue

            channel = guild.get_channel(ANNOUNCEMENT_CHANNEL_ID)
            if not channel:
                continue

            # Check if already announced today
            async with self.db_lock:
                cursor = await self.db.execute(
                    """
                    SELECT id FROM announcements 
                    WHERE user_id = ? AND guild_id = ? AND year = ? AND announced = 1
                """,
                    (user_id, guild.id, year),
                )
                announced = await cursor.fetchone()

            if announced:
                continue

            # Create birthday embed
            embed = discord.Embed(
                title="🎂 Happy Birthday! 🎂",
                description=f"# **{member.display_name}** is celebrating their birthday today!",
                color=discord.Color.gold(),
            )
            # embed.set_thumbnail(url=user.display_avatar.url)
            embed.set_image(
                url=os.environ.get(
                    "BIRTHDAY_IMAGE_URL",
                    "https://cdn.discordapp.com/attachments/1453780776238845982/1479027409725689868/discordbday.jpg?ex=69aa8b1d&is=69a9399d&hm=3a9c3026a63385e555612395b61633053a97571a8ece852c7bb343a2ee7ac6d4&",
                )
            )

            if age:
                embed.add_field(name="Age", value=age, inline=True)

            embed.set_thumbnail(url=user.display_avatar.url)

            # Send announcement
            await channel.send(content=f"🎉 {member.mention}", embed=embed)

            # Assign role if configured
            if BIRTHDAY_ROLE_ID:
                role = guild.get_role(BIRTHDAY_ROLE_ID)
                if role:
                    try:
                        await member.add_roles(role, reason="Birthday role")
                        # Schedule removal after 24 hours
                        asyncio.create_task(
                            self.remove_role_after(
                                guild.id, user_id, BIRTHDAY_ROLE_ID, 86400
                            )
                        )
                    except Exception:
                        pass

            # Mark as announced
            async with self.db_lock:
                await self.db.execute(
                    """
                    INSERT INTO announcements (user_id, guild_id, channel_id, year, announced)
                    VALUES (?, ?, ?, ?, 1)
                """,
                    (user_id, guild.id, channel.id, year),
                )
                await self.db.commit()

    async def remove_role_after(
        self, guild_id: int, user_id: int, role_id: int, delay: int
    ):
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

    # ─── COMMANDS ─────────────────────────────────────────────────

    birthday_group = app_commands.Group(
        name="birthday", description="Birthday commands"
    )

    @birthday_group.command(name="add", description="Set your birthday")
    @app_commands.describe(date="Your birthday in YYYY-MM-DD format (year optional)")
    async def birthday_add(self, interaction: discord.Interaction, date: str):
        """Set your birthday with timezone selection."""
        # Validate date format
        date_pattern = r"^(\d{4}-)?\d{2}-\d{2}$"
        if not re.match(date_pattern, date):
            embed = discord.Embed(
                title="❌ Invalid Format",
                description="Please use format: `YYYY-MM-DD` or `MM-DD`\nExample: `2000-03-15` or `03-15`",
                color=discord.Color.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Parse date
        if "-" in date and len(date.split("-")) == 3:
            year, month, day = map(int, date.split("-"))
            birth_date = f"{month:02d}-{day:02d}"
            birth_year = year
        else:
            month, day = map(int, date.split("-"))
            birth_date = f"{month:02d}-{day:02d}"
            birth_year = None

        # Validate date exists
        try:
            if birth_year:
                # validate full date
                date_class(birth_year, month, day)
            else:
                # validate month/day using a placeholder year
                date_class(2000, month, day)
        except ValueError:
            embed = discord.Embed(
                title="❌ Invalid Date",
                description="That date doesn't exist!",
                color=discord.Color.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Create timezone selection view
        view = BirthdayView(interaction.user.id, birth_date, birth_year)

        embed = discord.Embed(
            title="🌍 Select Your Timezone",
            description="Please select your timezone from the dropdown below.\nThis ensures you get wished at exactly midnight your time!",
            color=discord.Color.orange(),
        )
        embed.add_field(name="Your Birthday", value=date, inline=True)

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        view.message = await interaction.original_response()

    async def set_birthday(
        self, user_id: int, birth_date: str, birth_year: Optional[int], timezone: str
    ):
        """Save birthday to database."""
        async with self.db_lock:
            await self.db.execute(
                """
                INSERT OR REPLACE INTO users (user_id, birth_date, birth_year, timezone)
                VALUES (?, ?, ?, ?)
            """,
                (user_id, birth_date, birth_year, timezone),
            )
            await self.db.commit()

    @birthday_group.command(name="upcoming", description="Show upcoming birthdays")
    async def birthday_upcoming(self, interaction: discord.Interaction):
        """Show paginated list of upcoming birthdays."""
        await interaction.response.defer()
        async with self.db_lock:
            cursor = await self.db.execute("""
                SELECT user_id, birth_date, birth_year, timezone 
                FROM users
            """)
            all_users = await cursor.fetchall()

        if not all_users:
            embed = discord.Embed(
                title="📭 No Birthdays",
                description="No birthdays have been set in this server yet!",
                color=discord.Color.orange(),
            )
            await interaction.followup.send(embed=embed)
            return

        # Calculate days until next birthday for each user
        datetime.now()
        upcoming = []

        for user_id, birth_date, birth_year, tz_name in all_users:
            # Check if user is in this guild
            member = interaction.guild.get_member(user_id)
            if not member:
                continue

            # Calculate next birthday
            month, day = map(int, birth_date.split("-"))
            tz = ZoneInfo(tz_name)
            now_local = datetime.now(tz)

            next_bday = datetime(now_local.year, month, day, tzinfo=tz)
            if next_bday < now_local:
                next_bday = datetime(now_local.year + 1, month, day, tzinfo=tz)

            days_until = (next_bday - now_local).days

            upcoming.append((days_until, user_id, birth_date, birth_year, tz_name))

        # Sort by days until
        upcoming.sort()

        # Take next 50 birthdays
        upcoming = [(uid, bd, by, tz) for _, uid, bd, by, tz in upcoming[:50]]

        if not upcoming:
            embed = discord.Embed(
                title="📭 No Upcoming Birthdays",
                description="No upcoming birthdays found in this server.",
                color=discord.Color.orange(),
            )
            await interaction.followup.send(embed=embed)
            return

        # Create paginated view
        view = UpcomingBirthdaysView(upcoming, interaction.guild.name, self.bot)
        await interaction.followup.send(embed=view.get_page_embed(), view=view)

    @birthday_group.command(
        name="remove", description="Remove your birthday from the system"
    )
    async def birthday_remove(self, interaction: discord.Interaction):
        """Remove your birthday data."""

        # Confirmation view
        view = discord.ui.View()
        view.add_item(ConfirmRemoveButton())
        view.add_item(CancelButton())

        embed = discord.Embed(
            title="⚠️ Confirm Removal",
            description="Are you sure you want to remove your birthday data?",
            color=discord.Color.orange(),
        )

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    # ─── CLEANUP ─────────────────────────────────────────────────

    def cog_unload(self):
        """Clean up tasks on unload."""
        self.birthday_check.cancel()
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
    await bot.add_cog(Birthday(bot))
