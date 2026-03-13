import discord
from discord.ext import commands, tasks
from .debate_config import COLORS, FORUM_CHANNEL_ID
from datetime import datetime
import asyncio
import logging

logger = logging.getLogger(__name__)


class DebateAnalytics(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.analytics_message_id = None
        self.update_analytics.start()
        self._ready = False  # Add ready flag

    async def cog_load(self):
        """Load analytics message ID from database."""
        try:
            stored_id = await self.bot.db.get_config("analytics_message_id")
            if stored_id:
                self.analytics_message_id = int(stored_id)
                logger.info(f"Loaded analytics message ID: {self.analytics_message_id}")
        except Exception as e:
            logger.error(f"Failed to load analytics message ID: {e}")

        # Mark as ready after loading
        self._ready = True

    def cog_unload(self):
        self.update_analytics.cancel()

    async def get_analytics_embed(self):
        """Generate the analytics embed with current data"""
        try:
            stats = await self.bot.db.get_dashboard_stats()
            leaderboard = await self.bot.db.get_weekly_leaderboard(5)
            trending_debates = await self.bot.db.get_trending_debates(5)
        except Exception as e:
            logger.error(f"Error fetching analytics data: {e}")
            # Return a fallback embed if DB query fails
            embed = discord.Embed(
                title="📊 Debate Hall of Stats & Leaderboard",
                description="⚠️ Unable to fetch analytics data. Retrying soon...",
                color=COLORS["warning"],
                timestamp=datetime.now(),
            )
            embed.set_footer(text="Updates every 10 minutes")
            return embed

        embed = discord.Embed(
            title="📊 Debate Hall of Stats & Leaderboard",
            color=COLORS["analytics"],
            timestamp=datetime.now(),
        )

        stats_text = (
            f"Total Debates: {stats['total_debates']}\n"
            f"Active Now: {stats['active_debates']}\n"
            f"Total Messages: {stats['total_messages']}\n"
            f"Unique Debaters: {stats['unique_participants']}\n"
            f"Avg Debate Length: {stats['avg_length']}h"
        )
        embed.add_field(
            name="📈 Server Debate Analytics", value=stats_text, inline=False
        )

        if leaderboard:
            lb_text = ""
            medals = ["🥇", "🥈", "🥉"]
            for i, user in enumerate(leaderboard):
                medal = medals[i] if i < 3 else f"{i+1}."
                user_obj = self.bot.get_user(int(user["user_id"]))
                name = (
                    user_obj.display_name if user_obj else f"User {user['user_id'][:5]}"
                )
                lb_text += f"{medal} **{name}** — {user['score']} pts\n"
        else:
            lb_text = "No data this week"
        embed.add_field(name="🏆 Weekly Top Debaters", value=lb_text, inline=False)

        if trending_debates:
            trending_text = ""
            for i, debate in enumerate(trending_debates):
                thread = self.bot.get_channel(int(debate["thread_id"]))
                if thread:
                    debate_link = thread.mention
                else:
                    debate_link = f"https://discord.com/channels/@{'me'}/{FORUM_CHANNEL_ID}/{debate['thread_id']}"
                safe_topic = (
                    debate["topic"][:40] + "..."
                    if len(debate["topic"]) > 40
                    else debate["topic"]
                )
                trending_text += f"**{i+1}.** {debate_link} | {safe_topic} — {debate['message_count']} msgs ({debate['participant_count']} participants)\n"
        else:
            trending_text = "No active debates in last 24h"
        embed.add_field(
            name="🔥 Trending Debates (Last 24h)", value=trending_text, inline=False
        )

        embed.set_footer(text="Updates every 10 minutes")
        return embed

    async def get_dashboard_thread(self) -> discord.Thread | None:
        """Find the dashboard thread in active or archived threads. Unarchive if needed."""
        forum = self.bot.get_channel(FORUM_CHANNEL_ID)
        if not forum:
            logger.error(f"Could not find forum with ID {FORUM_CHANNEL_ID}")
            return None

        # Look in active threads first
        for thread in forum.threads:
            if thread.name == "📋 Debate Dashboard":
                return thread

        # If not found, search archived threads (limit to recent 50 to avoid rate limits)
        try:
            async for thread in forum.archived_threads(limit=50):
                if thread.name == "📋 Debate Dashboard":
                    # Unarchive it so we can update it
                    try:
                        await thread.edit(archived=False)
                        logger.info("Unarchived dashboard thread")
                        return thread
                    except Exception as e:
                        logger.error(f"Failed to unarchive dashboard thread: {e}")
                        return None
        except Exception as e:
            logger.error(f"Error fetching archived threads: {e}")
            return None
        return None

    async def get_or_create_analytics_message(
        self, dashboard_thread: discord.Thread
    ) -> discord.Message | None:
        """
        Get the analytics message using stored ID, or find an existing one, or create a new one.
        Updates self.analytics_message_id and stores it in DB.
        """
        # Try stored ID first
        if self.analytics_message_id:
            try:
                msg = await dashboard_thread.fetch_message(self.analytics_message_id)
                return msg
            except discord.NotFound:
                logger.warning(
                    "Stored analytics message not found; will try to find another."
                )
                self.analytics_message_id = None
                # Clear invalid ID from database
                await self.bot.db.set_config("analytics_message_id", "")
            except discord.HTTPException as e:
                logger.error(f"HTTP error fetching analytics message: {e}")

        # Search last 50 messages for an analytics embed from the bot
        try:
            async for msg in dashboard_thread.history(limit=50):
                if msg.author == self.bot.user and msg.embeds:
                    # Check if this is likely the analytics embed
                    embed = msg.embeds[0]
                    if embed.title and "Debate Hall of Stats" in embed.title:
                        self.analytics_message_id = msg.id
                        await self.bot.db.set_config(
                            "analytics_message_id", str(msg.id)
                        )
                        logger.info(f"Found existing analytics message: {msg.id}")
                        return msg
        except Exception as e:
            logger.error(f"Error searching message history: {e}")

        # If none found, create a new one
        try:
            embed = await self.get_analytics_embed()
            new_msg = await dashboard_thread.send(embed=embed)
            self.analytics_message_id = new_msg.id
            await self.bot.db.set_config("analytics_message_id", str(new_msg.id))
            logger.info(f"Created new analytics message: {new_msg.id}")
            return new_msg
        except Exception as e:
            logger.error(f"Failed to create new analytics message: {e}")
            return None

    async def _update_analytics(self):
        """Internal method to update analytics embed."""
        # Wait for cog to be fully loaded
        if not hasattr(self, "_ready") or not self._ready:
            logger.info("Analytics cog not ready yet, skipping update")
            return

        try:
            dashboard_thread = await self.get_dashboard_thread()
            if not dashboard_thread:
                logger.error(
                    "Dashboard thread not found (even in archives). Skipping analytics update."
                )
                return

            msg = await self.get_or_create_analytics_message(dashboard_thread)
            if not msg:
                logger.error("Could not obtain analytics message for editing.")
                return

            embed = await self.get_analytics_embed()
            await msg.edit(embed=embed)
            logger.debug("Analytics embed updated successfully")
        except Exception as e:
            logger.error(f"Error in _update_analytics: {e}")

    @tasks.loop(minutes=10)
    async def update_analytics(self):
        """Update analytics embed every 10 minutes"""
        await self.bot.wait_until_ready()
        await self._update_analytics()

    @update_analytics.before_loop
    async def before_update(self):
        await self.bot.wait_until_ready()
        # Wait longer for everything to initialize
        await asyncio.sleep(10)
        # Do an initial update
        await self._update_analytics()

    @commands.Cog.listener()
    async def on_ready(self):
        """Handle bot ready event"""
        logger.info("Bot ready - DebateAnalytics will update soon")
        # Don't update immediately - let the before_loop handle it
        pass


async def setup(bot):
    await bot.add_cog(DebateAnalytics(bot))
