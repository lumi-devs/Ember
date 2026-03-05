import discord
from discord.ext import commands, tasks
from .debate_config import COLORS, FORUM_CHANNEL_ID
from datetime import datetime
import asyncio

class DebateAnalytics(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bot.analytics_message_id = None
        self.update_analytics.start()
    
    def cog_unload(self):
        self.update_analytics.cancel()
    
    async def get_analytics_embed(self):
        """Generate the analytics embed with current data"""
        # Get stats
        stats = await self.bot.db.get_dashboard_stats()
        leaderboard = await self.bot.db.get_weekly_leaderboard(5)
        trending = await self.bot.db.get_trending_debaters(5)
        
        # Create embed
        embed = discord.Embed(
            title="📊 Debate Hall of Stats & Leaderboard",
            color=COLORS["analytics"],
            timestamp=datetime.now()
        )
        
        # Stats field
        stats_text = f"""
        Total Debates: {stats['total_debates']}
        Active Now: {stats['active_debates']}
        Total Messages: {stats['total_messages']}
        Unique Debaters: {stats['unique_participants']}
        Avg Debate Length: {stats['avg_length']}h
        """
        
        embed.add_field(name="📈 Server Debate Analytics", value=stats_text, inline=False)

        # Leaderboard field (Weekly Top)
        if leaderboard:
            lb_text = ""
            medals = ["🥇", "🥈", "🥉"]
            for i, user in enumerate(leaderboard):
                medal = medals[i] if i < 3 else f"{i+1}."
                user_obj = self.bot.get_user(int(user['user_id']))
                name = user_obj.display_name if user_obj else f"User {user['user_id'][:5]}"
                lb_text += f"{medal} **{name}** — {user['score']} pts\n"
        else:
            lb_text = "No data this week"

        embed.add_field(name="🏆 Weekly Top Debaters", value=lb_text, inline=False)
        
        # Trending Debaters (Last 24h)
        if trending:
            trending_text = ""
            for i, user in enumerate(trending):
                user_obj = self.bot.get_user(int(user['user_id']))
                name = user_obj.display_name if user_obj else f"User {user['user_id'][:5]}"
                trending_text += f"**{i+1}.** {name} — {user['recent_messages']} msgs ({user['active_debates']} debates)\n"
        else:
            trending_text = "No active debaters in last 24h"
        
        embed.add_field(name="🔥 Trending Now (Last 24h)", value=trending_text, inline=False)
        
        embed.set_footer(text="Updates every 10 minutes • Anti-spam: 10pts/hour limit")
        
        return embed
    
    @tasks.loop(minutes=10)
    async def update_analytics(self):
        """Update analytics embed every 10 minutes"""
        await self.bot.wait_until_ready()
        await self._update_analytics()
    
    async def _update_analytics(self):
        """Internal method to update analytics"""
        forum = self.bot.get_channel(FORUM_CHANNEL_ID)
        if not forum:
            return
        
        # Find the dashboard thread
        dashboard_thread = None
        for thread in forum.threads:
            if thread.name == "📋 Debate Dashboard":
                dashboard_thread = thread
                break
        
        if not dashboard_thread:
            return
        
        # Get analytics message
        if not self.bot.analytics_message_id:
            return
        
        try:
            msg = await dashboard_thread.fetch_message(self.bot.analytics_message_id)
        except discord.NotFound:
            return
        except discord.HTTPException:
            return
        
        # Get and send updated embed
        embed = await self.get_analytics_embed()
        await msg.edit(embed=embed)

    @update_analytics.before_loop
    async def before_update(self):
        await self.bot.wait_until_ready()
        # Run once immediately on startup after a short delay
        await asyncio.sleep(5)  # Short delay to ensure everything is ready
        await self._update_analytics()
    
    @commands.Cog.listener()
    async def on_ready(self):
        """Also run when the bot is ready"""
        await asyncio.sleep(2)  # Small delay to ensure other cogs are loaded
        await self._update_analytics()

async def setup(bot):
    await bot.add_cog(DebateAnalytics(bot))