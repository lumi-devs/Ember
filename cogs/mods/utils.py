"""Utility functions for Staff Analytics cog."""

import discord
from datetime import datetime, timedelta
from typing import List, Optional, Union, Dict
import re


# ─── Duration / number formatting ─────────────────────────────────────────────

def format_duration(minutes: float) -> str:
    if minutes < 1:
        return f"{minutes * 60:.0f}s"
    if minutes < 60:
        return f"{minutes:.1f}m"
    if minutes < 1440:
        return f"{minutes / 60:.1f}h"
    return f"{minutes / 1440:.1f}d"


def format_relative_time(dt: datetime) -> str:
    delta = datetime.now() - dt
    secs = int(delta.total_seconds())
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{delta.days}d ago"


def format_seconds(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.1f}m"
    return f"{seconds / 3600:.1f}h"


def format_number(num: Union[int, float]) -> str:
    if num >= 1_000_000:
        return f"{num / 1_000_000:.1f}M"
    if num >= 1_000:
        return f"{num / 1_000:.1f}K"
    return str(int(num))


def truncate_string(text: str, max_length: int = 100) -> str:
    if len(text) <= max_length:
        return text
    return text[:max_length - 3] + "…"


def parse_duration(duration_str: str) -> Optional[timedelta]:
    match = re.match(r"^(\d+)([dhms])$", duration_str.lower())
    if not match:
        return None
    value, unit = int(match.group(1)), match.group(2)
    return {"d": timedelta(days=value), "h": timedelta(hours=value),
            "m": timedelta(minutes=value), "s": timedelta(seconds=value)}.get(unit)


def parse_mentions(text: str) -> List[int]:
    user_ids = [int(x) for x in re.findall(r"<@!?(\d+)>", text)]
    role_ids = [int(x) for x in re.findall(r"<@&(\d+)>", text)]
    return user_ids + role_ids


def chunk_list(lst: List, chunk_size: int) -> List[List]:
    return [lst[i:i + chunk_size] for i in range(0, len(lst), chunk_size)]


# ─── Trend & delta formatting ─────────────────────────────────────────────────

def format_trend_delta(current: Union[int, float], previous: Union[int, float]) -> str:
    """Return a compact trend string: `+23 (+46%) ⬆️` or `⬇️ -5 (-10%)`."""
    if previous == 0:
        if current > 0:
            return f"`+{format_number(current)}` 🆕"
        return "`—`"
    delta = current - previous
    pct = abs(delta / previous) * 100
    if delta > 0:
        return f"`+{format_number(abs(delta))}` **+{pct:.0f}%** ⬆️"
    if delta < 0:
        return f"`-{format_number(abs(delta))}` **-{pct:.0f}%** ⬇️"
    return "`±0` ➡️"


def format_trend_arrow(current: Union[int, float], previous: Union[int, float]) -> str:
    """Compact arrow-only trend indicator."""
    if previous == 0:
        return "🆕" if current > 0 else "—"
    delta = current - previous
    pct = abs(delta / previous) * 100
    if delta > 0:
        return f"⬆️ +{pct:.0f}%"
    if delta < 0:
        return f"⬇️ -{pct:.0f}%"
    return "➡️ 0%"


def format_kpi_status(value: Union[int, float], target: Union[int, float]) -> str:
    """Return an emoji status indicator based on value vs KPI target."""
    if target == 0:
        return ""
    ratio = value / target
    if ratio >= 1.2:
        return "🟢"
    if ratio >= 0.8:
        return "🟡"
    if ratio >= 0.5:
        return "🟠"
    return "🔴"


# ─── Visual components ────────────────────────────────────────────────────────

def create_progress_bar(current: float, total: float, length: int = 12, filled_char: str = "█", empty_char: str = "░") -> str:
    if total == 0:
        return empty_char * length
    filled = min(int((current / total) * length), length)
    return filled_char * filled + empty_char * (length - filled)


def create_activity_heatmap_compact(active_hours: Dict[int, int]) -> str:
    """
    Build a compact 3-line heatmap suitable for a single embed field.

    Example output (inside a code block):
       00 01 02 03 04 05 06 07 08 09 10 11
       ░  ░  ░  ░  ░  ░  ▒  ▒  ▓  █  █  ▓
       12 13 14 15 16 17 18 19 20 21 22 23
       ▓  ▒  ▒  ▓  █  █  ▓  ▒  ▒  ░  ░  ░
    """
    max_val = max(active_hours.values(), default=1) or 1
    chars = []
    for hour in range(24):
        count = active_hours.get(hour, 0) or active_hours.get(str(hour), 0)
        ratio = count / max_val
        if ratio == 0:
            c = "░"
        elif ratio < 0.25:
            c = "▒"
        elif ratio < 0.65:
            c = "▓"
        else:
            c = "█"
        chars.append(c)

    label_am  = "  ".join(f"{h:02d}" for h in range(0, 12))
    data_am   = "  ".join(chars[0:12])
    label_pm  = "  ".join(f"{h:02d}" for h in range(12, 24))
    data_pm   = "  ".join(chars[12:24])
    return f"{label_am}\n{data_am}\n{label_pm}\n{data_pm}"


def get_rank_badge(rank_name: str) -> str:
    from .constants import RANK_TIERS
    info = RANK_TIERS.get(rank_name, {})
    return info.get("emoji", "")


def get_time_based_greeting() -> str:
    hour = datetime.now().hour
    if hour < 12:
        return "Good morning"
    if hour < 17:
        return "Good afternoon"
    return "Good evening"


# ─── Discord helpers ──────────────────────────────────────────────────────────

async def get_member_safe(guild: discord.Guild, user_id: int) -> Optional[discord.Member]:
    try:
        return await guild.fetch_member(user_id)
    except (discord.NotFound, discord.HTTPException):
        return None


class Paginator:
    def __init__(self, items: List[str], items_per_page: int = 10):
        self.items = items
        self.items_per_page = items_per_page
        self.total_pages = max(1, (len(items) + items_per_page - 1) // items_per_page)
        self.current_page = 0

    def get_page(self, page: int) -> List[str]:
        if page < 0 or page >= self.total_pages:
            return []
        start = page * self.items_per_page
        return self.items[start:start + self.items_per_page]

    def next_page(self) -> List[str]:
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
        return self.get_page(self.current_page)

    def previous_page(self) -> List[str]:
        if self.current_page > 0:
            self.current_page -= 1
        return self.get_page(self.current_page)


# ─── Standard embed builders ──────────────────────────────────────────────────

def create_error_embed(title: str, description: str) -> discord.Embed:
    from .constants import EMBED_COLORS
    return discord.Embed(title=f"❌ {title}", description=description, color=EMBED_COLORS["error"])


def create_success_embed(title: str, description: str) -> discord.Embed:
    from .constants import EMBED_COLORS
    return discord.Embed(title=f"✅ {title}", description=description, color=EMBED_COLORS["success"])


def create_info_embed(title: str, description: str) -> discord.Embed:
    from .constants import EMBED_COLORS
    return discord.Embed(title=f"ℹ️ {title}", description=description, color=EMBED_COLORS["info"])


def create_warning_embed(title: str, description: str) -> discord.Embed:
    from .constants import EMBED_COLORS
    return discord.Embed(title=f"⚠️ {title}", description=description, color=EMBED_COLORS["warning"])


def build_stat_row(label: str, value: Union[int, float, str], trend: str = "", kpi_status: str = "") -> str:
    """
    Format a single KPI row for a stats embed field.
    e.g. `💬 Help events    143  🟢  ⬆️ +25%`
    """
    val_str = format_number(value) if isinstance(value, (int, float)) else str(value)
    parts = [f"**{val_str}**"]
    if kpi_status:
        parts.append(kpi_status)
    if trend:
        parts.append(trend)
    return f"`{label:<22}` {' '.join(parts)}"


def build_comparison_table(
    label: str,
    v1: Union[int, float],
    v2: Union[int, float],
) -> tuple:
    """Return (label_col, col1, col2) for a comparison embed."""
    def fmt(v):
        return f"{v:.1f}" if isinstance(v, float) else str(int(v))

    if v1 > v2:
        return label, f"✅ **{fmt(v1)}**", f"— {fmt(v2)}"
    if v2 > v1:
        return label, f"— {fmt(v1)}", f"✅ **{fmt(v2)}**"
    return label, f"= {fmt(v1)}", f"= {fmt(v2)}"