import discord
from discord.ext import commands, tasks
from .debate_config import MOD_CHANNEL_ID, COLORS, MOD_ROLE_ID
from datetime import datetime, timedelta
import asyncio


class DebateCleaner(commands.Cog):
    """Clean up old data and auto-reject expired submissions"""

    def __init__(self, bot):
        self.bot = bot
        self.cleanup_task.start()
        self.auto_reject_task.start()

    def cog_unload(self):
        self.cleanup_task.cancel()
        self.auto_reject_task.cancel()

    @tasks.loop(hours=24)  # Run once per day
    async def cleanup_task(self):
        """Clean up old data daily"""
        await self.bot.wait_until_ready()

        # Clean data older than 30 days
        await self.bot.db.cleanup_old_data(days=30)

        # Log cleanup
        mod_channel = self.bot.get_channel(MOD_CHANNEL_ID)
        if mod_channel:
            embed = discord.Embed(
                title="🧹 Database Cleanup Complete",
                description="Old records older than 30 days have been purged.",
                color=COLORS["info"],
                timestamp=datetime.now(),
            )
            await mod_channel.send(embed=embed)

    @tasks.loop(minutes=30)  # Check every 30 minutes
    async def auto_reject_task(self):
        """Auto-reject pending submissions older than 24 hours"""
        await self.bot.wait_until_ready()

        # Get expired submissions
        expired = await self.bot.db.auto_reject_expired_submissions(hours=24)

        if expired:
            mod_channel = self.bot.get_channel(MOD_CHANNEL_ID)
            if mod_channel:
                for user_id, mod_message_id in expired:
                    # Try to notify user
                    user = self.bot.get_user(int(user_id))
                    if user:
                        try:
                            embed = discord.Embed(
                                title="⏰ Debate Proposal Expired",
                                description="Your debate proposal was automatically rejected because it wasn't reviewed within 24 hours.",
                                color=COLORS["warning"],
                            )
                            await user.send(embed=embed)
                        except:
                            pass

                    # Try to update mod message
                    if mod_message_id:
                        try:
                            msg = await mod_channel.fetch_message(int(mod_message_id))
                            if msg:
                                embed = msg.embeds[0] if msg.embeds else None
                                if embed:
                                    new_embed = discord.Embed(
                                        title="⏰ EXPIRED - " + embed.title,
                                        description=embed.description,
                                        color=COLORS["warning"],
                                    )
                                    for field in embed.fields:
                                        new_embed.add_field(
                                            name=field.name,
                                            value=field.value,
                                            inline=field.inline,
                                        )
                                    new_embed.set_footer(
                                        text="Auto-rejected after 24 hours"
                                    )
                                    await msg.edit(embed=new_embed, view=None)
                        except:
                            pass

    @cleanup_task.before_loop
    async def before_cleanup(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(10)  # Wait for bot to be fully ready

    @auto_reject_task.before_loop
    async def before_auto_reject(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(15)  # Wait for bot to be fully ready

    @commands.command(name="cleannow")
    @commands.has_permissions(administrator=True)
    async def force_cleanup(self, ctx, days: int = 30):
        """Force cleanup of old data (Admin only)"""
        await ctx.send(f"🧹 Cleaning up data older than {days} days...")

        await self.bot.db.cleanup_old_data(days=days)

        embed = discord.Embed(
            title="✅ Manual Cleanup Complete",
            description=f"Records older than {days} days have been purged.",
            color=COLORS["success"],
            timestamp=datetime.now(),
        )
        await ctx.send(embed=embed)

    @commands.command(name="expired")
    @commands.has_role(MOD_ROLE_ID)
    async def check_expired(self, ctx):
        """Check for expired pending submissions (Mod only)"""
        pending = await self.bot.db.get_all_pending_submissions()

        if not pending:
            await ctx.send("✅ No pending submissions found.")
            return

        expired = []
        active = []

        for sub in pending:
            age = datetime.now() - sub["created_at"]
            if age.total_seconds() > 86400:  # 24 hours
                expired.append(sub)
            else:
                active.append(sub)

        embed = discord.Embed(
            title="📋 Pending Submissions Status",
            color=COLORS["info"],
            timestamp=datetime.now(),
        )

        embed.add_field(
            name=f"✅ Active ({len(active)})",
            value="\n".join(
                [f"<@{s['user_id']}>: {s['topic'][:30]}..." for s in active[:5]]
            )
            or "None",
            inline=False,
        )

        embed.add_field(
            name=f"⏰ Expired ({len(expired)})",
            value="\n".join(
                [f"<@{s['user_id']}>: {s['topic'][:30]}..." for s in expired[:5]]
            )
            or "None",
            inline=False,
        )

        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(DebateCleaner(bot))
