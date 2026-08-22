import datetime

import discord
from discord.ext import commands, tasks
import time
import asyncio
from collections import defaultdict

# ──────────────────────────────────────────────
#  CONFIGURATION
# ──────────────────────────────────────────────
HEAT_CONFIG = {
    "heat_per_new_message":  20,   # % added per unique message reacted to
    "heat_per_remove_spam":  10,   # % added for add/remove cycling on a new message
    "decay_amount":           5,   # % removed each decay tick
    "decay_interval":         4,   # seconds between decay ticks
    "spam_window":           10,   # seconds — how long a message stays "seen"
    "spam_threshold":         4,   # unique messages within window = spike (doubles heat gain)
    "mute_duration":        180,   # seconds (3 minutes)
    "max_heat":             100,   # % that triggers mute
    "warning_delete_after":  20,   # seconds before any warning is deleted
    "warn_60_threshold":     60,   # % for first advisory warning
    "warn_80_threshold":     80,   # % for second stern warning
}


# ──────────────────────────────────────────────
#  COG
# ──────────────────────────────────────────────
class ReactionHeat(commands.Cog):
    """
    Silent heat system for reaction spam.

    Heat only rises when a user reacts to a *new* message they haven't
    already reacted to within the spam window.  Reacting with 1000
    different emojis on the same message counts as one event.
    Reacting to 1000 different messages ramps heat rapidly.

    Thresholds:
      60% — soft advisory (auto-deleted)
      80% — stern warning  (auto-deleted)
     100% — mute for 3 minutes (auto-deleted notice)
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # { guild_id: { user_id: heat_pct } }
        self.heat: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))

        # Tracks which messages a user has reacted to within the spam window.
        # { guild_id: { user_id: { message_id: first_reaction_timestamp } } }
        self.message_log: dict[int, dict[int, dict[int, float]]] = (
            defaultdict(lambda: defaultdict(dict))
        )

        # Tracks which warning tiers have already been sent so they only fire once
        # per heat cycle.  Cleared when heat returns to 0 or a mute fires.
        # { guild_id: { user_id: set_of_thresholds_already_warned } }
        self.warned: dict[int, dict[int, set]] = defaultdict(lambda: defaultdict(set))

        # { guild_id: { user_id: unmute_unix_ts } }
        self.muted: dict[int, dict[int, float]] = defaultdict(dict)

        self.decay_task.start()

    def cog_unload(self):
        self.decay_task.cancel()

    # ── internal helpers ──────────────────────

    def _add_heat(self, guild_id: int, user_id: int, amount: int) -> int:
        new_val = min(HEAT_CONFIG["max_heat"], self.heat[guild_id][user_id] + amount)
        self.heat[guild_id][user_id] = new_val
        return new_val

    def _reset_heat(self, guild_id: int, user_id: int):
        """Zero out heat and clear all per-user state for this cycle."""
        self.heat[guild_id].pop(user_id, None)
        self.warned[guild_id].pop(user_id, None)
        self.message_log[guild_id].pop(user_id, None)

    def _is_muted(self, guild_id: int, user_id: int) -> bool:
        if user_id in self.muted[guild_id]:
            if time.time() < self.muted[guild_id][user_id]:
                return True
            del self.muted[guild_id][user_id]
        return False

    def _log_message(self, guild_id: int, user_id: int, message_id: int) -> tuple[bool, int]:
        """
        Record a message reaction.

        Returns:
            is_new_message  — True if this message wasn't seen in the window
            unique_count    — how many unique messages are in the current window
        """
        now = time.time()
        cutoff = now - HEAT_CONFIG["spam_window"]
        log = self.message_log[guild_id][user_id]

        # Evict stale entries
        stale = [mid for mid, ts in log.items() if ts <= cutoff]
        for mid in stale:
            del log[mid]

        is_new = message_id not in log
        if is_new:
            log[message_id] = now

        return is_new, len(log)

    async def _apply_mute(self, guild: discord.Guild, member: discord.Member):
        duration = HEAT_CONFIG["mute_duration"]
        self.muted[guild.id][member.id] = time.time() + duration
        try:
            until = discord.utils.utcnow() + datetime.timedelta(seconds=duration)
            await member.timeout(until, reason="Reaction spam")
        except (discord.Forbidden, discord.HTTPException):
            muted_role = discord.utils.get(guild.roles, name="Muted")
            if muted_role:
                await member.add_roles(muted_role, reason="Reaction spam")
                await asyncio.sleep(duration)
                await member.remove_roles(muted_role, reason="Heat mute expired")

    async def _send_tiered_warning(
        self,
        channel: discord.TextChannel,
        member: discord.Member,
        tier: int,
    ):
        """Send the appropriate warning embed for the given heat tier."""
        delete_after = HEAT_CONFIG["warning_delete_after"]
        minutes = HEAT_CONFIG["mute_duration"] // 60

        if tier == 60:
            embed = discord.Embed(
                description=(
                    f"⚠️ {member.mention}, heads up — you're reacting across a lot of "
                    f"messages quickly. Slow down or you may be muted."
                ),
                color=discord.Color.yellow(),
            )
        elif tier == 80:
            embed = discord.Embed(
                description=(
                    f"🔶 {member.mention}, you're close to the reaction spam limit. "
                    f"**Stop reacting across new messages or you will be muted.**"
                ),
                color=discord.Color.orange(),
            )
        else:  # 100 — mute notice
            embed = discord.Embed(
                description=(
                    f"🔇 {member.mention} has been muted for **{minutes} minute(s)**. "
                    f"Reason: **Reaction spam**"
                ),
                color=discord.Color.red(),
            )

        try:
            await channel.send(embed=embed, delete_after=delete_after)
        except (discord.Forbidden, discord.HTTPException):
            pass

    async def _check_and_warn(
        self,
        guild: discord.Guild,
        member: discord.Member,
        channel: discord.TextChannel,
        new_heat: int,
    ) -> bool:
        """
        Fire any pending tier warnings.  Returns True if a mute was issued.
        Warnings at 60 / 80 are only sent once per heat cycle.
        """
        cfg = HEAT_CONFIG
        already = self.warned[guild.id][member.id]

        if new_heat >= cfg["max_heat"]:
            self._reset_heat(guild.id, member.id)
            await self._send_tiered_warning(channel, member, 100)
            await self._apply_mute(guild, member)
            return True

        if new_heat >= cfg["warn_80_threshold"] and 80 not in already:
            already.add(80)
            await self._send_tiered_warning(channel, member, 80)

        elif new_heat >= cfg["warn_60_threshold"] and 60 not in already:
            already.add(60)
            await self._send_tiered_warning(channel, member, 60)

        return False

    # ── decay loop ────────────────────────────

    @tasks.loop(seconds=HEAT_CONFIG["decay_interval"])
    async def decay_task(self):
        now = time.time()
        cutoff = now - HEAT_CONFIG["spam_window"]

        for guild_id, guild_heat in list(self.heat.items()):
            for uid in list(guild_heat):
                guild_heat[uid] = max(0, guild_heat[uid] - HEAT_CONFIG["decay_amount"])

                if guild_heat[uid] == 0:
                    # fix 1: delete zero-heat entries rather than leaving them
                    del guild_heat[uid]
                    # fix 2: clear warned set once heat fully decays to 0
                    self.warned[guild_id].pop(uid, None)
                    # fix 3: drop the message_log user dict when heat expires
                    self.message_log[guild_id].pop(uid, None)

            # Drop empty guild dicts (bot left the server, or all users cooled off)
            if not guild_heat:
                del self.heat[guild_id]
                self.warned.pop(guild_id, None)
                self.message_log.pop(guild_id, None)

        # fix 4: actively sweep expired mute entries rather than relying on
        # lazy cleanup inside _is_muted (which never runs for absent users)
        for guild_id, guild_muted in list(self.muted.items()):
            for uid in list(guild_muted):
                if now >= guild_muted[uid]:
                    del guild_muted[uid]
            if not guild_muted:
                del self.muted[guild_id]

        # fix 5: sweep message_log user dicts whose entries are all stale.
        # Catches users who reacted once and never again — their heat was 0
        # from the start so fix 3 never ran for them.
        for guild_id, guild_log in list(self.message_log.items()):
            for uid in list(guild_log):
                stale = [mid for mid, ts in guild_log[uid].items() if ts <= cutoff]
                for mid in stale:
                    del guild_log[uid][mid]
                if not guild_log[uid]:
                    del guild_log[uid]
            if not guild_log:
                del self.message_log[guild_id]

    @decay_task.before_loop
    async def before_decay(self):
        await self.bot.wait_until_ready()

    # ── guild cleanup ─────────────────────────

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        """fix 6: immediately drop all state when the bot leaves a server."""
        self.heat.pop(guild.id, None)
        self.message_log.pop(guild.id, None)
        self.warned.pop(guild.id, None)
        self.muted.pop(guild.id, None)

    # ── listeners ─────────────────────────────

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.Member):
        if user.bot:
            return
        guild = reaction.message.guild
        if guild is None:
            return
        if self._is_muted(guild.id, user.id):
            return

        is_new, unique_count = self._log_message(guild.id, user.id, reaction.message.id)

        # Only heat up when the user touches a message they haven't reacted to
        # in the current window.  Piling more emojis on the same message is free.
        if not is_new:
            return

        cfg = HEAT_CONFIG
        gain = (
            cfg["heat_per_new_message"] * 2
            if unique_count >= cfg["spam_threshold"]
            else cfg["heat_per_new_message"]
        )

        new_heat = self._add_heat(guild.id, user.id, gain)
        await self._check_and_warn(guild, user, reaction.message.channel, new_heat)

    @commands.Cog.listener()
    async def on_reaction_remove(self, reaction: discord.Reaction, user: discord.Member):
        """Extra heat for add/remove cycling — but only when cross-message activity exists."""
        if user.bot:
            return
        guild = reaction.message.guild
        if guild is None:
            return
        if self._is_muted(guild.id, user.id):
            return

        unique_count = len(self.message_log[guild.id][user.id])
        if unique_count < 2:
            return

        new_heat = self._add_heat(guild.id, user.id, HEAT_CONFIG["heat_per_remove_spam"])
        await self._check_and_warn(guild, user, reaction.message.channel, new_heat)


# ── setup ─────────────────────────────────────
async def setup(bot: commands.Bot):
    await bot.add_cog(ReactionHeat(bot))