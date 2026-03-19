"""
staff_review.py — AI-powered staff review cog (Groq edition, free tier).

Uses Groq's free API (llama-3.3-70b-versatile) to analyse every data point
your analytics system tracks PLUS full historical promotion/demotion records
for each staff member before making a recommendation.

Humans ALWAYS make the final decision via Accept / Reject buttons.
Every AI recommendation and every human decision is persisted.

──────────────────────────────────────────────
ENV VARS REQUIRED
──────────────────────────────────────────────
  GROQ_API_KEY             free at console.groq.com
  STAFF_ROLE_HIERARCHY     comma-separated Discord role IDs, lowest → highest
                           e.g.  "111,222,333,444"
  STAFF_ROLE_NAMES         matching display names (same order)
                           e.g.  "Trial Mod,Moderator,Senior Mod,Admin"
  STAFF_REVIEW_LOG_CHANNEL channel ID where decisions are posted (optional)
  STAFF_REVIEW_DB          path for review history DB (default: staff_reviews.db)

──────────────────────────────────────────────
COMMANDS
──────────────────────────────────────────────
  /staffreview  @member [days]  — full AI review of one person (with attached prompt file)
  /staffreviewall [days]        — review entire roster, sorted by urgency
  /staffhierarchy               — show the configured role ladder
  /staffpromote @member         — manual promote (confirm button)
  /staffdemote  @member         — manual demote  (confirm button)
  /staffhistory @member         — show full promotion/demotion history
"""

import os
import json
import asyncio
import logging
import aiosqlite
import aiohttp
import io
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Tuple, Any

import discord
from discord import app_commands
from discord.ext import commands

from .database import DatabaseManager
from .models   import RankTier
from .utils    import get_member_safe, create_error_embed, create_info_embed

logger = logging.getLogger(__name__)

# ─── Groq config ─────────────────────────────────────────────────────────────

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL   = "llama-3.3-70b-versatile"   # free, fast, 128k context

# Rate limits (free tier)
TOKENS_PER_MINUTE = 12000
AVG_TOKENS_PER_REVIEW = 950
MAX_REVIEWS_PER_MINUTE = TOKENS_PER_MINUTE // AVG_TOKENS_PER_REVIEW  # ~12

# ─── Verdict constants ────────────────────────────────────────────────────────

PROMOTE = "PROMOTE"
DEMOTE  = "DEMOTE"
WATCH   = "WATCH"
KEEP    = "KEEP"

VERDICT_COLORS = {
    PROMOTE: discord.Color.from_str("#57F287"),
    DEMOTE:  discord.Color.from_str("#ED4245"),
    WATCH:   discord.Color.from_str("#FEE75C"),
    KEEP:    discord.Color.from_str("#5865F2"),
}
VERDICT_EMOJIS = {PROMOTE: "📈", DEMOTE: "📉", WATCH: "👁️", KEEP: "✅"}

REVIEW_DB_PATH = os.getenv("STAFF_REVIEW_DB", "data/staff_analytics.db")


# ─── Role hierarchy helpers ───────────────────────────────────────────────────

def _load_hierarchy() -> Tuple[List[int], List[str]]:
    ids   = [int(x.strip()) for x in os.getenv("STAFF_ROLE_HIERARCHY", "").split(",") if x.strip().isdigit()]
    names = [x.strip()      for x in os.getenv("STAFF_ROLE_NAMES",     "").split(",") if x.strip()]
    while len(names) < len(ids):
        names.append(f"Role-{ids[len(names)]}")
    return ids, names


def _role_index(member: discord.Member, hierarchy_ids: List[int]) -> int:
    member_ids = {r.id for r in member.roles}
    best = -1
    for i, rid in enumerate(hierarchy_ids):
        if rid in member_ids:
            best = i
    return best


# ─── Review history database ──────────────────────────────────────────────────

class ReviewHistoryDB:
    """
    Stores every AI recommendation and every human decision.
    Metric snapshots are stored alongside each review so the LLM
    can see exactly how numbers evolved across every past cycle.
    """

    def __init__(self, path: str = REVIEW_DB_PATH):
        self.path = path
        self._conn: Optional[aiosqlite.Connection] = None

    async def initialize(self):
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS review_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                staff_id        INTEGER NOT NULL,
                reviewer_id     INTEGER NOT NULL,
                verdict         TEXT    NOT NULL,
                confidence      TEXT    NOT NULL DEFAULT 'MEDIUM',
                from_role_id    INTEGER,
                from_role_name  TEXT,
                to_role_id      INTEGER,
                to_role_name    TEXT,
                reasons         TEXT    NOT NULL,
                watchlist_note  TEXT    DEFAULT '',
                decision        TEXT    DEFAULT 'PENDING',
                decided_by      INTEGER,
                days_window     INTEGER NOT NULL DEFAULT 30,
                created_at      TIMESTAMP NOT NULL,
                decided_at      TIMESTAMP,
                snap_engagement_score     REAL,
                snap_help_events          INTEGER,
                snap_moderation_actions   INTEGER,
                snap_vc_hours             REAL,
                snap_active_days          INTEGER,
                snap_first_responses      INTEGER,
                snap_new_members_helped   INTEGER,
                snap_quality_events       INTEGER,
                snap_avg_quality_score    REAL,
                snap_current_streak       INTEGER,
                snap_proactive_help       INTEGER,
                snap_fast_responses       INTEGER,
                snap_reactions_given      INTEGER,
                snap_threads_created      INTEGER,
                snap_total_messages       INTEGER,
                snap_unique_members       INTEGER,
                snap_mod_severity_sum     REAL
            );
            CREATE INDEX IF NOT EXISTS idx_rl_staff ON review_log(staff_id);
            CREATE INDEX IF NOT EXISTS idx_rl_ts    ON review_log(created_at);
        """)
        await self._conn.commit()

    async def close(self):
        if self._conn:
            await self._conn.close()

    async def insert(
        self,
        staff_id: int, reviewer_id: int,
        verdict: str, confidence: str,
        from_role_id: Optional[int], from_role_name: str,
        to_role_id:   Optional[int], to_role_name:   str,
        reasons: List[str], watchlist_note: str,
        days_window: int, stats: Dict, consistency: Dict,
    ) -> int:
        cur = await self._conn.execute(
            """INSERT INTO review_log (
                staff_id, reviewer_id, verdict, confidence,
                from_role_id, from_role_name, to_role_id, to_role_name,
                reasons, watchlist_note, decision, days_window, created_at,
                snap_engagement_score, snap_help_events, snap_moderation_actions,
                snap_vc_hours, snap_active_days, snap_first_responses,
                snap_new_members_helped, snap_quality_events, snap_avg_quality_score,
                snap_current_streak, snap_proactive_help, snap_fast_responses,
                snap_reactions_given, snap_threads_created, snap_total_messages,
                snap_unique_members, snap_mod_severity_sum
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                staff_id, reviewer_id, verdict, confidence,
                from_role_id, from_role_name, to_role_id, to_role_name,
                json.dumps(reasons), watchlist_note, "PENDING", days_window,
                datetime.now().isoformat(),
                stats.get("engagement_score"),    stats.get("help_events"),
                stats.get("moderation_actions"),  stats.get("vc_hours"),
                stats.get("active_days"),         stats.get("first_responses"),
                stats.get("new_members_helped"),  stats.get("quality_events"),
                stats.get("avg_quality_score"),   consistency.get("current_streak"),
                stats.get("proactive_help"),      stats.get("fast_responses"),
                stats.get("reactions_given"),     stats.get("threads_created"),
                stats.get("total_messages"),      stats.get("unique_members_helped"),
                stats.get("moderation_severity_sum"),
            ),
        )
        await self._conn.commit()
        return cur.lastrowid

    async def resolve(self, review_id: int, decision: str, decided_by: int):
        await self._conn.execute(
            "UPDATE review_log SET decision=?, decided_by=?, decided_at=? WHERE id=?",
            (decision, decided_by, datetime.now().isoformat(), review_id),
        )
        await self._conn.commit()

    async def get_promotion_demotions(self, staff_id: int) -> List[Dict]:
        """All accepted PROMOTE/DEMOTE records — the actual role changes."""
        rows = await (await self._conn.execute(
            """SELECT id, verdict, from_role_name, to_role_name, decided_at, created_at,
                      days_window, snap_engagement_score, snap_active_days,
                      snap_current_streak, snap_help_events, snap_moderation_actions
               FROM review_log
               WHERE staff_id=? AND verdict IN ('PROMOTE','DEMOTE') AND decision='ACCEPTED'
               ORDER BY created_at ASC""",
            (staff_id,),
        )).fetchall()
        return [dict(r) for r in rows]

    async def get_last_n_reviews(self, staff_id: int, n: int = 6) -> List[Dict]:
        """Last N reviews oldest-first for trajectory analysis."""
        rows = await (await self._conn.execute(
            """SELECT id, verdict, confidence, from_role_name, to_role_name,
                      decision, created_at, days_window, watchlist_note, reasons,
                      snap_engagement_score, snap_help_events, snap_moderation_actions,
                      snap_vc_hours, snap_active_days, snap_first_responses,
                      snap_new_members_helped, snap_quality_events, snap_avg_quality_score,
                      snap_current_streak, snap_total_messages
               FROM review_log WHERE staff_id=?
               ORDER BY created_at DESC LIMIT ?""",
            (staff_id, n),
        )).fetchall()
        return list(reversed([dict(r) for r in rows]))   # oldest first

    async def get_all_reviews(self, staff_id: int) -> List[Dict]:
        rows = await (await self._conn.execute(
            "SELECT * FROM review_log WHERE staff_id=? ORDER BY created_at ASC",
            (staff_id,),
        )).fetchall()
        return [dict(r) for r in rows]


# ─── Groq LLM client with improved prompt ─────────────────────────────────────

class GroqReviewer:

    def __init__(self):
        self._api_key = os.getenv("GROQ_API_KEY", "")
        if not self._api_key:
            logger.warning("GROQ_API_KEY not set.")

    @staticmethod
    def _fmt_promo_history(records: List[Dict]) -> str:
        if not records:
            return "  None on record."
        lines = []
        for r in records:
            date = (r.get("decided_at") or r.get("created_at") or "")[:10]
            lines.append(
                f"  {date}  {r['verdict']:7}  {r['from_role_name']} → {r['to_role_name']}"
                f"  (score={r.get('snap_engagement_score') or '?'},"
                f" helps={r.get('snap_help_events') or '?'},"
                f" mod={r.get('snap_moderation_actions') or '?'},"
                f" streak={r.get('snap_current_streak') or '?'}d,"
                f" active={r.get('snap_active_days') or '?'}d)"
            )
        return "\n".join(lines)

    @staticmethod
    def _fmt_trajectory(records: List[Dict]) -> str:
        if not records:
            return "  No previous AI reviews."
        lines = []
        for r in records:
            date    = (r.get("created_at") or "")[:10]
            verdict = r.get("verdict", "?")
            decision= r.get("decision", "?")
            score   = r.get("snap_engagement_score")
            helps   = r.get("snap_help_events")
            mod     = r.get("snap_moderation_actions")
            streak  = r.get("snap_current_streak")
            active  = r.get("snap_active_days")
            msgs    = r.get("snap_total_messages")
            reasons = json.loads(r.get("reasons") or "[]")
            top_r   = reasons[0][:80] if reasons else ""
            watch   = r.get("watchlist_note","")
            lines.append(
                f"  {date}  verdict={verdict:7} decision={decision:8}"
                f"  score={str(score or '?'):>7}  helps={str(helps or '?'):>4}"
                f"  mod={str(mod or '?'):>4}  streak={str(streak or '?')}d"
                f"  active={str(active or '?')}d  msgs={str(msgs or '?')}"
            )
            if top_r:
                lines.append(f"    reason: {top_r}")
            if watch:
                lines.append(f"    watch:  {watch[:80]}")
        return "\n".join(lines)

    def _build_prompt(
        self,
        member_name: str,
        current_role: str,
        days_at_role: int,
        hierarchy: List[str],
        stats: Dict,
        consistency: Dict,
        trend: Dict,
        notes: List[Dict],
        days: int,
        promo_history: List[Dict],
        review_trajectory: List[Dict],
    ) -> str:
        notes_text = "\n".join(
            f"  {'[+]' if n['is_positive'] else '[-]'}  {n['date']}  {n['note']}"
            for n in notes
        ) or "  (none)"

        return f"""=== STAFF PERFORMANCE REVIEW — {member_name} ===

IDENTITY
  Current role:       {current_role}
  Days at this role:  {days_at_role} days
  Role ladder (low→high): {' → '.join(hierarchy)}
  Review window:      last {days} days

CURRENT PERIOD METRICS
  Engagement score:        {stats.get('engagement_score', 0):.1f}   [{stats.get('rank_tier','?')} tier]
  ── Help ──────────────────────────────────────────────
  Help events:             {stats.get('help_events', 0)}
    Proactive help:        {stats.get('proactive_help', 0)}
    New members helped:    {stats.get('new_members_helped', 0)}
    Unique members helped: {stats.get('unique_members_helped', 0)}
  First responses:         {stats.get('first_responses', 0)}
  Fast responses (≤5min):  {stats.get('fast_responses', 0)}
  Avg response time:       {stats.get('avg_response_seconds', 0):.0f}s
  ── Quality ────────────────────────────────────────────
  Quality events:          {stats.get('quality_events', 0)}
  Avg quality score:       {stats.get('avg_quality_score', 0):.2f}
  Avg message words:       {stats.get('avg_message_words', 0):.1f}
  ── Moderation ─────────────────────────────────────────
  Moderation actions:      {stats.get('moderation_actions', 0)}
  Mod weighted severity:   {stats.get('moderation_severity_sum', 0):.1f}
  Reports filed:           {stats.get('reports_filed', 0)}
  ── Presence ───────────────────────────────────────────
  VC hours:                {stats.get('vc_hours', 0):.1f}h
  Total messages:          {stats.get('total_messages', 0)}
  Active days (/{days}):   {stats.get('active_days', 0)}
  Channels participated:   {stats.get('channels_participated', 0)}
  Reactions given:         {stats.get('reactions_given', 0)}
  Threads created:         {stats.get('threads_created', 0)}
  Server bumps:            {stats.get('bumps_performed', 0)}

CONSISTENCY
  Current streak:          {consistency.get('current_streak', 0)} days
  Longest streak ever:     {consistency.get('longest_streak', 0)} days
  Peak-hours activity:     {consistency.get('peak_hours_activity', 0)}
  Off-peak activity:       {consistency.get('off_peak_activity', 0)}
  Consistency score:       {consistency.get('consistency_score', 0):.1f}

TREND  (this 30d vs previous 30d)
  Help events: {trend.get('help_current', 0):>5}  →  {trend.get('help_previous', 0):<5}  {'⬆' if trend.get('help_current',0) > trend.get('help_previous',0) else '⬇' if trend.get('help_current',0) < trend.get('help_previous',0) else '='}
  Mod actions: {trend.get('mod_current', 0):>5}  →  {trend.get('mod_previous', 0):<5}  {'⬆' if trend.get('mod_current',0) > trend.get('mod_previous',0) else '⬇' if trend.get('mod_current',0) < trend.get('mod_previous',0) else '='}

ALL-TIME ACCEPTED PROMOTIONS / DEMOTIONS
{self._fmt_promo_history(promo_history)}

AI REVIEW TRAJECTORY  (last {len(review_trajectory)} reviews, oldest → newest)
{self._fmt_trajectory(review_trajectory)}

ADMIN NOTES
{notes_text}

=== OUTPUT INSTRUCTIONS ===
Respond with ONLY this raw JSON object — no markdown, no backticks, nothing else:

{{
  "verdict":            "PROMOTE" | "DEMOTE" | "WATCH" | "KEEP",
  "target_role":        "<exact role name from the ladder, current role if KEEP/WATCH>",
  "confidence":         "HIGH" | "MEDIUM" | "LOW",
  "reasons": [
    "<reason 1 — cite specific numbers or historical pattern>",
    "<reason 2>",
    "<reason 3>",
    "<optional reason 4>",
    "<optional reason 5>"
  ],
  "watchlist_note":      "<if WATCH: what to check at next review, else empty string>",
  "trajectory_summary":  "<1 sentence describing overall performance arc across all history>"
}}

VERDICT RULES (enforce strictly)
  PROMOTE  — sustained high output across MULTIPLE review cycles (not just one good month).
             Score AND trend AND consistency must point up. Days at role must be meaningful.
             If this is the member's first review with no history, default to KEEP unless metrics are exceptional.
  DEMOTE   — measurable decline across ≥2 metrics, OR chronic inactivity (<7 active days),
             OR a WATCH verdict in the previous review that was accepted but not improved.
  WATCH    — early warning: 1–2 declining metrics, or first-time inactivity.
             If the member was already on WATCH last review and metrics haven't improved, that is DEMOTE.
  KEEP     — performing adequately. The safe default when data is mixed or history is thin.
"""

    async def review(
        self,
        member_name: str, current_role: str, days_at_role: int,
        hierarchy: List[str], stats: Dict, consistency: Dict,
        trend: Dict, notes: List[Dict], days: int,
        promo_history: List[Dict], review_trajectory: List[Dict],
    ) -> Tuple[Dict[str, Any], str]:
        """
        Returns:
            (parsed_json, raw_prompt_string)
        """
        prompt = self._build_prompt(
            member_name, current_role, days_at_role,
            hierarchy, stats, consistency, trend,
            notes, days, promo_history, review_trajectory,
        )

        payload = {
            "model":       GROQ_MODEL,
            "temperature": 0.15,
            "max_tokens":  700,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a senior Discord community manager doing structured staff reviews. "
                        "You always output raw JSON only — no markdown, no explanation, no backticks. "
                        "You weigh historical trends heavily. A single good or bad month means little "
                        "compared to a consistent multi-cycle pattern. "
                        "Admin notes describing concrete incidents override raw metrics."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
        }

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type":  "application/json",
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(GROQ_API_URL, headers=headers, json=payload) as resp:
                if resp.status != 200:
                    err = await resp.text()
                    raise RuntimeError(f"Groq API {resp.status}: {err[:300]}")
                data = await resp.json()

        raw = data["choices"][0]["message"]["content"].strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip()), prompt


# ─── Confirmation view ────────────────────────────────────────────────────────

class ReviewConfirmView(discord.ui.View):

    def __init__(
        self, cog: "StaffReviewCog", review_id: int,
        target: discord.Member, verdict: str,
        from_role: Optional[discord.Role], to_role: Optional[discord.Role],
        log_channel: Optional[discord.TextChannel],
    ):
        super().__init__(timeout=600)
        self.cog = cog; self.review_id = review_id; self.target = target
        self.verdict = verdict; self.from_role = from_role; self.to_role = to_role
        self.log_channel = log_channel; self._done = False

    async def _finish(self, interaction: discord.Interaction, accepted: bool):
        if self._done:
            await interaction.response.send_message("Already decided.", ephemeral=True); return
        self._done = True
        for c in self.children: c.disabled = True

        decision = "ACCEPTED" if accepted else "REJECTED"
        await self.cog.review_db.resolve(self.review_id, decision, interaction.user.id)

        action_text = "No role change."
        if accepted and self.verdict in (PROMOTE, DEMOTE) and self.from_role and self.to_role:
            try:
                await self.target.remove_roles(self.from_role, reason=f"Review #{self.review_id}")
                await self.target.add_roles(self.to_role,      reason=f"Review #{self.review_id}")
                action_text = f"✅ **{self.from_role.name}** → **{self.to_role.name}**"
            except discord.Forbidden:
                action_text = "⚠️ Role change failed — missing permissions."
        elif not accepted:
            action_text = "Recommendation rejected. No action taken."

        color = discord.Color.green() if accepted else discord.Color.light_grey()
        embed = discord.Embed(
            title=f"{'✅ Accepted' if accepted else '❌ Rejected'} — {VERDICT_EMOJIS.get(self.verdict,'')} {self.verdict}",
            description=f"{action_text}\nDecided by {interaction.user.mention}",
            color=color, timestamp=datetime.now(),
        )
        await interaction.response.edit_message(embed=embed, view=self)

        if self.log_channel:
            log = discord.Embed(
                title=f"Staff Review — {self.target.display_name}",
                color=color, timestamp=datetime.now(),
            )
            log.add_field(name="Verdict",    value=f"{VERDICT_EMOJIS.get(self.verdict,'')} {self.verdict}", inline=True)
            log.add_field(name="Decision",   value=decision, inline=True)
            log.add_field(name="Decided by", value=interaction.user.mention, inline=True)
            log.add_field(name="Action",     value=action_text, inline=False)
            log.add_field(name="Review ID",  value=f"`#{self.review_id}`", inline=True)
            log.set_thumbnail(url=self.target.display_avatar.url)
            try:
                await self.log_channel.send(embed=log)
            except Exception:
                pass

    @discord.ui.button(label="✅ Accept", style=discord.ButtonStyle.success)
    async def accept(self, i: discord.Interaction, _: discord.ui.Button): await self._finish(i, True)

    @discord.ui.button(label="❌ Reject", style=discord.ButtonStyle.danger)
    async def reject(self, i: discord.Interaction, _: discord.ui.Button): await self._finish(i, False)


# ─── Main cog ─────────────────────────────────────────────────────────────────

class StaffReviewCog(commands.Cog, name="StaffReview"):

    def __init__(self, bot: commands.Bot, analytics_db: DatabaseManager):
        self.bot       = bot
        self.db        = analytics_db
        self.review_db = ReviewHistoryDB()
        self.llm       = GroqReviewer()

    async def cog_load(self):
        await self.review_db.initialize()
        logger.info("StaffReviewCog loaded (Groq / %s).", GROQ_MODEL)

    async def cog_unload(self):
        await self.review_db.close()

    # ── Private helpers ───────────────────────────────────────────────────────

    def _log_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        v = os.getenv("STAFF_REVIEW_LOG_CHANNEL", "")
        return guild.get_channel(int(v)) if v.isdigit() else None

    def _role_by_name(self, guild: discord.Guild, name: str, ids: List[int], names: List[str]) -> Optional[discord.Role]:
        for rid, rname in zip(ids, names):
            if rname.lower() == name.lower():
                return guild.get_role(rid)
        return None

    def _days_at_role(self, promo_history: List[Dict]) -> int:
        if not promo_history:
            return 0
        date_str = (promo_history[-1].get("decided_at") or promo_history[-1].get("created_at") or "")[:19]
        try:
            return (datetime.now() - datetime.fromisoformat(date_str)).days
        except (ValueError, TypeError):
            return 0

    async def _gather(self, member: discord.Member, days: int):
        weights   = await self.db.get_score_weights(member.guild.id)
        stats_obj = await self.db.get_staff_stats(member.id, days, weights)
        con_obj   = await self.db.get_consistency_stats(member.id)
        trend     = await self.db.get_trend_comparison(member.id)
        notes_raw = await self.db.get_staff_notes(member.id, limit=15)

        if con_obj:
            stats_obj.consistency = con_obj

        stats = stats_obj.to_dict()
        consistency = {
            "current_streak":      con_obj.current_streak      if con_obj else 0,
            "longest_streak":      con_obj.longest_streak      if con_obj else 0,
            "active_days":         con_obj.active_days         if con_obj else 0,
            "peak_hours_activity": con_obj.peak_hours_activity if con_obj else 0,
            "off_peak_activity":   con_obj.off_peak_activity   if con_obj else 0,
            "consistency_score":   con_obj.consistency_score   if con_obj else 0.0,
        }
        notes = [
            {"note": n.note, "is_positive": n.is_positive, "date": n.created_at.strftime("%Y-%m-%d")}
            for n in notes_raw
        ]
        promo_history     = await self.review_db.get_promotion_demotions(member.id)
        review_trajectory = await self.review_db.get_last_n_reviews(member.id, n=6)
        return stats, consistency, trend, notes, promo_history, review_trajectory

    async def _do_review(
        self, interaction: discord.Interaction,
        member: discord.Member, days: int,
    ) -> Optional[Tuple[discord.Embed, "ReviewConfirmView", discord.File]]:

        ids, names = _load_hierarchy()
        if not ids:
            await interaction.followup.send(
                embed=create_error_embed("Not configured", "Set `STAFF_ROLE_HIERARCHY` first."),
                ephemeral=True,
            )
            return None

        idx               = _role_index(member, ids)
        current_role_name = names[idx] if idx >= 0 else "No hierarchy role"
        stats, consistency, trend, notes, promo_history, review_trajectory = \
            await self._gather(member, days)
        days_at_role = self._days_at_role(promo_history)

        try:
            result, raw_prompt = await asyncio.wait_for(
                self.llm.review(
                    member_name=member.display_name, current_role=current_role_name,
                    days_at_role=days_at_role, hierarchy=names,
                    stats=stats, consistency=consistency, trend=trend,
                    notes=notes, days=days,
                    promo_history=promo_history, review_trajectory=review_trajectory,
                ),
                timeout=30,
            )
        except asyncio.TimeoutError:
            await interaction.followup.send(
                embed=create_error_embed("Timeout", "Groq took too long. Try again."), ephemeral=True
            )
            return None
        except Exception as exc:
            logger.error("GroqReviewer error for %s: %s", member.id, exc)
            await interaction.followup.send(
                embed=create_error_embed("LLM Error", str(exc)[:200]), ephemeral=True
            )
            return None

        verdict     = result.get("verdict",    KEEP)
        target_name = result.get("target_role", current_role_name)
        confidence  = result.get("confidence", "MEDIUM")
        reasons     = result.get("reasons",    [])
        watch_note  = result.get("watchlist_note", "")
        trajectory  = result.get("trajectory_summary", "")

        from_role = interaction.guild.get_role(ids[idx]) if idx >= 0 else None
        to_role   = self._role_by_name(interaction.guild, target_name, ids, names) \
                    if verdict in (PROMOTE, DEMOTE) else None
        to_name   = target_name if verdict in (PROMOTE, DEMOTE) else current_role_name

        review_id = await self.review_db.insert(
            staff_id=member.id, reviewer_id=interaction.user.id,
            verdict=verdict, confidence=confidence,
            from_role_id=from_role.id if from_role else None,
            from_role_name=current_role_name,
            to_role_id=to_role.id if to_role else None,
            to_role_name=to_name,
            reasons=reasons, watchlist_note=watch_note,
            days_window=days, stats=stats, consistency=consistency,
        )

        color = VERDICT_COLORS.get(verdict, discord.Color.blurple())
        embed = discord.Embed(
            title=f"{VERDICT_EMOJIS.get(verdict,'')} AI Review — {member.display_name}",
            color=color, timestamp=datetime.now(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Verdict",      value=f"**{verdict}**",     inline=True)
        embed.add_field(name="Confidence",   value=confidence,           inline=True)
        embed.add_field(name="Current role", value=current_role_name,    inline=True)
        embed.add_field(name="Days at role", value=f"{days_at_role}d",   inline=True)

        if verdict in (PROMOTE, DEMOTE):
            embed.add_field(name="→ Suggested", value=f"**{target_name}**", inline=True)

        if promo_history:
            ph_lines = []
            for p in promo_history[-4:]:
                date = (p.get("decided_at") or p.get("created_at") or "")[:10]
                ph_lines.append(
                    f"`{date}` **{p['verdict']}** {p['from_role_name']} → {p['to_role_name']}"
                    f" *(score {p.get('snap_engagement_score') or '?'})*"
                )
            embed.add_field(
                name=f"📜 Role changes ({len(promo_history)} total)",
                value="\n".join(ph_lines), inline=False,
            )

        if review_trajectory:
            traj_lines = []
            for r in review_trajectory[-4:]:
                date = (r.get("created_at") or "")[:10]
                v    = r.get("verdict","?")
                dec  = r.get("decision","?")
                sc   = r.get("snap_engagement_score")
                traj_lines.append(
                    f"`{date}` {VERDICT_EMOJIS.get(v,'')}{v:7} {dec:8} score={sc or '?'}"
                )
            embed.add_field(
                name=f"🗂️ Past reviews ({len(review_trajectory)} total)",
                value="\n".join(traj_lines), inline=False,
            )

        reasons_text = "\n".join(f"• {r}" for r in reasons) or "—"
        embed.add_field(name="Reasoning", value=reasons_text, inline=False)
        if watch_note:
            embed.add_field(name="👁️ Watch note",    value=watch_note,  inline=False)
        if trajectory:
            embed.add_field(name="📈 Performance arc", value=trajectory, inline=False)

        embed.set_footer(text=f"Review #{review_id} • {days}d window • Groq / {GROQ_MODEL} • Final call is yours")

        # Create a text file with the prompt
        prompt_file = discord.File(
            io.BytesIO(raw_prompt.encode('utf-8')),
            filename=f"review_{member.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_prompt.txt"
        )

        view = ReviewConfirmView(
            cog=self, review_id=review_id, target=member,
            verdict=verdict, from_role=from_role, to_role=to_role,
            log_channel=self._log_channel(interaction.guild),
        )
        return embed, view, prompt_file

    # ── Commands ──────────────────────────────────────────────────────────────

    @app_commands.command(name="staffreview", description="AI review of a staff member — includes full history.")
    @app_commands.describe(member="Who to review", days="Days of data to analyse (default 30)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def staffreview(self, interaction: discord.Interaction, member: discord.Member, days: int = 30):
        await interaction.response.defer(ephemeral=True)
        out = await self._do_review(interaction, member, days)
        if out:
            embed, view, prompt_file = out
            await interaction.followup.send(embed=embed, view=view, file=prompt_file, ephemeral=True)

    @app_commands.command(name="staffreviewall", description="AI reviews entire staff roster, sorted by urgency.")
    @app_commands.describe(days="Days of data to analyse (default 30)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def staffreviewall(self, interaction: discord.Interaction, days: int = 30):
        await interaction.response.defer(ephemeral=True)

        ids, names = _load_hierarchy()
        if not ids:
            await interaction.followup.send(
                embed=create_error_embed("Not configured", "Set `STAFF_ROLE_HIERARCHY` first."), ephemeral=True
            ); return

        hier_set = set(ids)
        staff = [m for m in interaction.guild.members if any(r.id in hier_set for r in m.roles) and not m.bot]
        if not staff:
            await interaction.followup.send(
                embed=create_info_embed("No staff", "No members hold a hierarchy role."), ephemeral=True
            ); return

        # Estimate time
        est_minutes = (len(staff) * 60) // MAX_REVIEWS_PER_MINUTE
        est_minutes = max(1, est_minutes)
        
        progress = discord.Embed(
            title="🔄 Running AI reviews…",
            description=f"Reviewing **{len(staff)}** staff members via Groq.\n"
                        f"Estimated time: **{est_minutes} minute{'s' if est_minutes > 1 else ''}**\n"
                        f"Rate limit: {MAX_REVIEWS_PER_MINUTE} reviews/minute",
            color=discord.Color.blurple(),
        )
        progress.add_field(name="Progress", value="⏳ Starting...", inline=False)
        msg = await interaction.followup.send(embed=progress, ephemeral=True, wait=True)

        results: List[Tuple[discord.Member, str, Dict, int]] = []
        
        # Process with rate limiting
        delay_between = 65 / MAX_REVIEWS_PER_MINUTE  # seconds between requests (a bit conservative)
        total = len(staff)
        completed = 0
        
        for member in staff:
            completed += 1
            idx = _role_index(member, ids)
            crn = names[idx] if idx >= 0 else "No hierarchy role"
            
            # Update progress every few members
            if completed % 3 == 0 or completed == total:
                progress.set_field_at(0, name="Progress", 
                    value=f"⏳ Processed {completed}/{total} members...", inline=False)
                await msg.edit(embed=progress)
            
            try:
                stats, consistency, trend, notes, ph, rt = await self._gather(member, days)
                dar = self._days_at_role(ph)
                
                # Add small jitter to avoid synchronized requests
                await asyncio.sleep(delay_between + (hash(str(member.id)) % 10) / 10)
                
                res, _ = await asyncio.wait_for(
                    self.llm.review(
                        member_name=member.display_name, current_role=crn,
                        days_at_role=dar, hierarchy=names,
                        stats=stats, consistency=consistency, trend=trend,
                        notes=notes, days=days, promo_history=ph, review_trajectory=rt,
                    ),
                    timeout=35,
                )
                
            except asyncio.TimeoutError:
                logger.error(f"Timeout for {member.id}")
                res = {"verdict": KEEP, "target_role": crn, "confidence": "LOW",
                       "reasons": ["Review timed out (Groq slow)"],
                       "watchlist_note": "", "trajectory_summary": ""}
                stats, consistency = {}, {}
                
            except Exception as exc:
                logger.error("reviewall %s: %s", member.id, exc)
                
                # Rate limit handling
                if "429" in str(exc):
                    logger.warning(f"Rate limit hit for {member.id}, waiting 10 seconds...")
                    await asyncio.sleep(10)
                    # Retry once
                    try:
                        res, _ = await asyncio.wait_for(
                            self.llm.review(
                                member_name=member.display_name, current_role=crn,
                                days_at_role=dar, hierarchy=names,
                                stats=stats, consistency=consistency, trend=trend,
                                notes=notes, days=days, promo_history=ph, review_trajectory=rt,
                            ),
                            timeout=35,
                        )
                    except Exception as retry_exc:
                        logger.error(f"Retry failed for {member.id}: {retry_exc}")
                        res = {"verdict": KEEP, "target_role": crn, "confidence": "LOW",
                               "reasons": [f"Review failed after rate limit: {str(retry_exc)[:80]}"],
                               "watchlist_note": "", "trajectory_summary": ""}
                else:
                    res = {"verdict": KEEP, "target_role": crn, "confidence": "LOW",
                           "reasons": [f"Review failed: {str(exc)[:80]}"],
                           "watchlist_note": "", "trajectory_summary": ""}
                stats, consistency = {}, {}

            verdict   = res.get("verdict", KEEP)
            from_role = interaction.guild.get_role(ids[idx]) if idx >= 0 else None
            to_role   = self._role_by_name(interaction.guild, res.get("target_role",""), ids, names) \
                        if verdict in (PROMOTE, DEMOTE) else None

            rid = await self.review_db.insert(
                staff_id=member.id, reviewer_id=interaction.user.id,
                verdict=verdict, confidence=res.get("confidence","LOW"),
                from_role_id=from_role.id if from_role else None,
                from_role_name=crn,
                to_role_id=to_role.id if to_role else None,
                to_role_name=res.get("target_role", crn),
                reasons=res.get("reasons",[]), watchlist_note=res.get("watchlist_note",""),
                days_window=days, stats=stats, consistency=consistency,
            )
            
            results.append((member, crn, res, rid))
            
            # Enforce rate limit: wait enough to stay under limit
            await asyncio.sleep(delay_between)
        
        # Sort results by verdict priority
        order = {DEMOTE: 0, WATCH: 1, PROMOTE: 2, KEEP: 3}
        results.sort(key=lambda x: order.get(x[2].get("verdict", KEEP), 3))

        summary = discord.Embed(
            title=f"📋 Staff Review Summary — {len(results)} members",
            color=discord.Color.blurple(), timestamp=datetime.now(),
        )
        summary.set_footer(text=f"{days}d window | /staffreview @member to act on individual cases")

        for vlabel in (DEMOTE, WATCH, PROMOTE, KEEP):
            group = [(m,cr,r,rid) for m,cr,r,rid in results if r.get("verdict") == vlabel]
            if not group: continue
            lines = []
            for m, cr, r, rid in group:
                top  = (r.get("reasons") or [""])[0][:75]
                conf = r.get("confidence","?")[0]
                if vlabel in (PROMOTE, DEMOTE):
                    lines.append(f"`#{rid}` [{conf}] **{m.display_name}** ({cr} → **{r.get('target_role',cr)}**)\n↳ {top}")
                elif vlabel == WATCH:
                    lines.append(f"`#{rid}` [{conf}] **{m.display_name}** ({cr})\n↳ {top}")
                else:
                    lines.append(f"**{m.display_name}** ({cr})")
            body = ("\n\n" if vlabel != KEEP else ", ").join(lines)
            if len(body) > 1020: body = body[:1020] + "…"
            summary.add_field(
                name=f"{VERDICT_EMOJIS.get(vlabel,'')} {vlabel} ({len(group)})",
                value=body or "—", inline=False,
            )
        await msg.edit(embed=summary)

    @app_commands.command(name="staffhistory", description="Full promotion/demotion and review history for a member.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(member="Who to look up")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def staffhistory(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)
        ph = await self.review_db.get_promotion_demotions(member.id)
        rt = await self.review_db.get_all_reviews(member.id)

        embed = discord.Embed(
            title=f"📜 Staff History — {member.display_name}",
            color=discord.Color.blurple(), timestamp=datetime.now(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)

        if ph:
            lines = [
                f"`{(p.get('decided_at') or p.get('created_at') or '')[:10]}` **{p['verdict']}**"
                f"  {p['from_role_name']} → {p['to_role_name']}"
                f"  *(score {p.get('snap_engagement_score') or '?'},"
                f" helps {p.get('snap_help_events') or '?'},"
                f" streak {p.get('snap_current_streak') or '?'}d)*"
                for p in ph
            ]
            embed.add_field(name=f"Role changes ({len(ph)})", value="\n".join(lines)[:1020], inline=False)
        else:
            embed.add_field(name="Role changes", value="None on record.", inline=False)

        if rt:
            lines = []
            for r in rt:
                date = (r.get("created_at") or "")[:10]
                v    = r.get("verdict","?")
                dec  = r.get("decision","?")
                sc   = r.get("snap_engagement_score")
                top  = (json.loads(r.get("reasons") or "[]") or [""])[0][:55]
                lines.append(f"`{date}` {VERDICT_EMOJIS.get(v,'')}{v:7} {dec:8} score={sc or '?'}  ↳ {top}")
            embed.add_field(name=f"All AI reviews ({len(rt)})", value="\n".join(lines)[:1020], inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)


    async def _manual_shift(self, interaction: discord.Interaction, member: discord.Member, direction: int):
        await interaction.response.defer(ephemeral=True)
        ids, names = _load_hierarchy()
        if not ids:
            await interaction.followup.send(
                embed=create_error_embed("Not configured", "Set `STAFF_ROLE_HIERARCHY` first."), ephemeral=True
            ); return

        idx = _role_index(member, ids)
        if idx < 0:
            await interaction.followup.send(
                embed=create_error_embed("Not in hierarchy", f"{member.mention} holds no hierarchy role."), ephemeral=True
            ); return

        new_idx = idx + direction
        if new_idx < 0:
            await interaction.followup.send(
                embed=create_error_embed("Already lowest", f"{member.mention} is at the bottom."), ephemeral=True
            ); return
        if new_idx >= len(ids):
            await interaction.followup.send(
                embed=create_error_embed("Already highest", f"{member.mention} is at the top."), ephemeral=True
            ); return

        from_role = interaction.guild.get_role(ids[idx])
        to_role   = interaction.guild.get_role(ids[new_idx])
        verdict   = PROMOTE if direction > 0 else DEMOTE

        review_id = await self.review_db.insert(
            staff_id=member.id, reviewer_id=interaction.user.id,
            verdict=verdict, confidence="HIGH",
            from_role_id=from_role.id if from_role else None,
            from_role_name=names[idx],
            to_role_id=to_role.id if to_role else None,
            to_role_name=names[new_idx],
            reasons=[f"Manual {verdict.lower()} by {interaction.user.display_name}."],
            watchlist_note="", days_window=0, stats={}, consistency={},
        )

        embed = discord.Embed(
            title=f"{VERDICT_EMOJIS[verdict]} Manual {verdict.title()} — {member.display_name}",
            description=(
                f"**{names[idx]}** → **{names[new_idx]}**\n\n"
                f"Confirm for {member.mention}? *(Review #{review_id})*"
            ),
            color=VERDICT_COLORS[verdict],
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        view = ReviewConfirmView(
            cog=self, review_id=review_id, target=member,
            verdict=verdict, from_role=from_role, to_role=to_role,
            log_channel=self._log_channel(interaction.guild),
        )
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="staffhierarchy", description="Show the configured staff role ladder.")
    @app_commands.checks.has_permissions(administrator=True)
    async def staffhierarchy(self, interaction: discord.Interaction):
        ids, names = _load_hierarchy()
        if not ids:
            await interaction.response.send_message(
                embed=create_error_embed("Not configured", "Set `STAFF_ROLE_HIERARCHY` and `STAFF_ROLE_NAMES` in .env."),
                ephemeral=True,
            ); return
        lines = []
        for i, (rid, rname) in enumerate(zip(ids, names)):
            role    = interaction.guild.get_role(rid)
            mention = role.mention if role else f"`{rid}` *(missing)*"
            tag     = "  ← highest" if i == len(ids)-1 else ("  ← lowest" if i == 0 else "")
            lines.append(f"`{i+1}.` {mention} **{rname}**{tag}")
        embed = discord.Embed(
            title="🪜 Staff Role Hierarchy",
            description="\n".join(reversed(lines)),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="Configured via STAFF_ROLE_HIERARCHY + STAFF_ROLE_NAMES")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        msg = "You need **Manage Roles**." if isinstance(error, app_commands.MissingPermissions) else str(error)[:200]
        try:
            await interaction.response.send_message(embed=create_error_embed("Error", msg), ephemeral=True)
        except discord.InteractionResponded:
            await interaction.followup.send(embed=create_error_embed("Error", msg), ephemeral=True)


# ─── Setup ────────────────────────────────────────────────────────────────────

async def setup(bot: commands.Bot):
    # Wait up to 15 s for StaffAnalytics to finish cog_load and set bot.analytics_db.
    # This handles load-order races when both extensions are loaded at startup.
    for _ in range(30):
        db: DatabaseManager = getattr(bot, "analytics_db", None)
        if db is not None:
            break
        await asyncio.sleep(0.5)
    else:
        raise RuntimeError(
            "StaffReviewCog: bot.analytics_db was never set after 15 s. "
            "Ensure underwork.mods.cog loads before underwork.mods.promoterz."
        )
    await bot.add_cog(StaffReviewCog(bot, db))
