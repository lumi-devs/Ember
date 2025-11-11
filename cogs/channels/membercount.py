import discord
from discord.ext import commands
import asyncio

# ✅ Replace with your actual channel IDs
ALL_MEMBERS_CHANNEL_ID = 1393175609638912010
NON_BOTS_CHANNEL_ID = 1393175726467055636


class MemberCount(commands.Cog):
    """Updates two channel names on join/leave events with optimizations."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.update_queue = asyncio.Queue()
        self.processing = False
        self.last_update = {}  # Track last update time per guild
        self.worker_task = None

    async def cog_load(self):
        """Start the background worker when cog loads."""
        self.worker_task = asyncio.create_task(self._update_worker())
        print("[MemberCount] Background worker started.")

    async def cog_unload(self):
        """Stop the background worker when cog unloads."""
        if self.worker_task:
            self.worker_task.cancel()
            print("[MemberCount] Background worker stopped.")

    async def _update_worker(self):
        """Background worker to process update requests."""
        while not self.bot.is_closed():
            try:
                guild = await self.update_queue.get()
                
                # Rate limiting: 5 seconds between updates per guild
                current_time = discord.utils.utcnow().timestamp()
                if (guild.id in self.last_update and 
                    current_time - self.last_update[guild.id] < 5):
                    self.update_queue.task_done()
                    continue
                
                self.last_update[guild.id] = current_time
                await self._update_counts(guild)
                self.update_queue.task_done()
                
                # Small delay to prevent API spam
                await asyncio.sleep(1)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[MemberCount] Worker error: {e}")
                await asyncio.sleep(1)

    async def update_counts(self, guild: discord.Guild):
        """Queue an update request (non-blocking)."""
        await self.update_queue.put(guild)

    async def _update_counts(self, guild: discord.Guild):
        """Update both counters efficiently with error handling."""
        try:
            total_members = guild.member_count
            non_bots = sum(1 for m in guild.members if not m.bot)

            all_channel = guild.get_channel(ALL_MEMBERS_CHANNEL_ID)
            nonbot_channel = guild.get_channel(NON_BOTS_CHANNEL_ID)

            updates = []

            if all_channel:
                new_name = f"⁠🌎 All members: {total_members}"
                if all_channel.name != new_name:
                    updates.append(all_channel.edit(name=new_name))

            if nonbot_channel:
                new_name = f"⁠🧑 Members: {non_bots}"
                if nonbot_channel.name != new_name:
                    updates.append(nonbot_channel.edit(name=new_name))

            # Execute all updates concurrently
            if updates:
                await asyncio.gather(*updates, return_exceptions=True)
                print(f"[MemberCount] Updated counts in {guild.name}: {total_members} total, {non_bots} humans")

        except Exception as e:
            print(f"[MemberCount] Error updating {guild.name}: {e}")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Handle member join with queueing."""
        await self.update_counts(member.guild)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """Handle member leave with queueing."""
        await self.update_counts(member.guild)

    @commands.Cog.listener()
    async def on_ready(self):
        """Update counts when bot starts."""
        print("[MemberCount] Initializing member counts...")
        for guild in self.bot.guilds:
            await self.update_counts(guild)

    # Optional: Manual update command
    @commands.command(name='updatecounts')
    @commands.has_permissions(manage_channels=True)
    async def manual_update(self, ctx):
        """Manually update member count channels."""
        await self.update_counts(ctx.guild)
        embed = discord.Embed(
            description="✅ Member counts update queued!",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed, delete_after=10)

    # Optional: View current queue status
    @commands.command(name='countstatus')
    @commands.has_permissions(manage_channels=True)
    async def count_status(self, ctx):
        """Show member count system status."""
        embed = discord.Embed(title="📊 Member Count Status", color=discord.Color.blue())
        embed.add_field(name="Queue Size", value=self.update_queue.qsize(), inline=True)
        embed.add_field(name="Total Members", value=ctx.guild.member_count, inline=True)
        embed.add_field(name="Human Members", 
                       value=sum(1 for m in ctx.guild.members if not m.bot), 
                       inline=True)
        await ctx.send(embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(MemberCount(bot))