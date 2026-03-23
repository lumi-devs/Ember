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
    "heat_per_reaction":    20,   # % added per reaction
    "heat_per_remove_spam": 10,   # % added for add/remove cycling
    "decay_amount":          5,   # % removed each decay tick
    "decay_interval":        4,   # seconds between decay ticks
    "spam_window":           5,   # seconds window for spam detection
    "spam_threshold":        4,   # reactions within window = spike
    "mute_duration":       180,   # seconds (3 minutes)
    "max_heat":            100,   # % that triggers mute
    "warning_delete_after": 20,   # seconds before warning is deleted
}


# ──────────────────────────────────────────────
#  COG
# ──────────────────────────────────────────────
class ReactionHeat(commands.Cog):
    """
    Silent heat system for reaction spam.
    Runs headless — no embeds, no heat bars, no commands.
    At 100% heat the user is muted for 3 minutes and a plain
    warning message is sent then deleted after 20 seconds.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # { guild_id: { user_id: heat_pct } }
        self.heat: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))

        # { guild_id: { user_id: [timestamps] } }
        self.reaction_log: dict[int, dict[int, list]] = defaultdict(lambda: defaultdict(list))

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

    def _is_muted(self, guild_id: int, user_id: int) -> bool:
        if user_id in self.muted[guild_id]:
            if time.time() < self.muted[guild_id][user_id]:
                return True
            del self.muted[guild_id][user_id]
        return False

    def _log_reaction(self, guild_id: int, user_id: int) -> int:
        """Record a reaction timestamp; return count within spam window."""
        now = time.time()
        log = self.reaction_log[guild_id][user_id]
        log.append(now)
        cutoff = now - HEAT_CONFIG["spam_window"]
        self.reaction_log[guild_id][user_id] = [t for t in log if t > cutoff]
        return len(self.reaction_log[guild_id][user_id])

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

    async def _warn(self, channel: discord.TextChannel, member: discord.Member):
        """Send a plain-text mute notice that auto-deletes."""
        minutes = HEAT_CONFIG["mute_duration"] // 60
        delete_after = HEAT_CONFIG["warning_delete_after"]
        try:
            embed = discord.Embed(
                description=f"{member.mention} has been muted for **{minutes} minutes**. "
                            f"Reason: **Reaction spam**",
            )
            embed.color = discord.Color.orange()
            await channel.send(embed=embed, delete_after=delete_after)
        except (discord.Forbidden, discord.HTTPException):
            pass

    # ── decay loop ────────────────────────────

    @tasks.loop(seconds=HEAT_CONFIG["decay_interval"])
    async def decay_task(self):
        for guild_heat in self.heat.values():
            for uid in list(guild_heat):
                if guild_heat[uid] > 0:
                    guild_heat[uid] = max(0, guild_heat[uid] - HEAT_CONFIG["decay_amount"])

    @decay_task.before_loop
    async def before_decay(self):
        await self.bot.wait_until_ready()

    # ── listeners ────────────────────────────

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.Member):
        if user.bot:
            return
        guild = reaction.message.guild
        if guild is None:
            return
        if self._is_muted(guild.id, user.id):
            return

        count_in_window = self._log_reaction(guild.id, user.id)
        cfg = HEAT_CONFIG

        # Double heat if hitting the spam threshold
        gain = cfg["heat_per_reaction"] * 2 if count_in_window >= cfg["spam_threshold"] \
               else cfg["heat_per_reaction"]

        new_heat = self._add_heat(guild.id, user.id, gain)

        if new_heat >= cfg["max_heat"]:
            self.heat[guild.id][user.id] = 0
            await self._warn(reaction.message.channel, user)
            await self._apply_mute(guild, user)

    @commands.Cog.listener()
    async def on_reaction_remove(self, reaction: discord.Reaction, user: discord.Member):
        """Extra heat for add/remove cycling."""
        if user.bot:
            return
        guild = reaction.message.guild
        if guild is None:
            return
        if self._is_muted(guild.id, user.id):
            return

        now = time.time()
        recent = sum(
            1 for t in self.reaction_log[guild.id][user.id]
            if now - t < HEAT_CONFIG["spam_window"]
        )
        if recent >= 2:
            new_heat = self._add_heat(guild.id, user.id, HEAT_CONFIG["heat_per_remove_spam"])
            if new_heat >= HEAT_CONFIG["max_heat"]:
                self.heat[guild.id][user.id] = 0
                await self._warn(reaction.message.channel, user)
                await self._apply_mute(guild, user)


# ── setup ────────────────────────────────────
async def setup(bot: commands.Bot):
    await bot.add_cog(ReactionHeat(bot))