import discord
from discord.ext import commands
import asyncio

# ✅ Replace with your actual channel IDs (these must be channels in the same guild)
ALL_MEMBERS_CHANNEL_ID = 1393175609638912010
NON_BOTS_CHANNEL_ID = 1393175726467055636


class MemberCount(commands.Cog):
    """Updates two channel names on join/leave events with optimizations."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.update_queue = asyncio.Queue()
        self.worker_task: asyncio.Task | None = None
        self.last_update: dict[int, float] = {}  # last update timestamp per guild (by id)

    async def cog_load(self):
        """Start the background worker when cog loads."""
        # Create the worker only once
        if not self.worker_task or self.worker_task.done():
            self.worker_task = asyncio.create_task(self._update_worker(), name="MemberCountWorker")
            print("[MemberCount] Background worker started.")

    async def cog_unload(self):
        """Stop the background worker when cog unloads."""
        if self.worker_task:
            self.worker_task.cancel()
            try:
                await self.worker_task
            except asyncio.CancelledError:
                pass
            print("[MemberCount] Background worker stopped.")
            self.worker_task = None

    async def _update_worker(self):
        """Background worker to process update requests."""
        print("[MemberCount] Worker running.")
        while True:
            try:
                guild: discord.Guild = await self.update_queue.get()

                # Rate limiting: 5 seconds between updates per guild
                current_ts = discord.utils.utcnow().timestamp()
                last = self.last_update.get(guild.id, 0)
                if current_ts - last < 5:
                    # already updated recently; skip
                    self.update_queue.task_done()
                    continue

                # Mark last update time and perform update
                self.last_update[guild.id] = current_ts
                await self._update_counts(guild)
                self.update_queue.task_done()

                # small delay between queue items to avoid burst
                await asyncio.sleep(0.5)

            except asyncio.CancelledError:
                # clean exit
                print("[MemberCount] Worker cancelled.")
                break
            except Exception as e:
                print(f"[MemberCount] Worker error: {e}")
                # Sleep a bit so we don't tight-loop on errors
                await asyncio.sleep(1)

    async def update_counts(self, guild: discord.Guild):
        """Queue an update request (non-blocking)."""
        try:
            await self.update_queue.put(guild)
        except Exception as e:
            print(f"[MemberCount] Failed to queue update for {getattr(guild, 'name', guild.id)}: {e}")

    async def _update_counts(self, guild: discord.Guild):
        """Update both counters efficiently with error handling."""
        try:
            # Use guild.member_count (fast) and compute non-bot count from members (requires intents.members)
            total_members = guild.member_count
            non_bots = sum(1 for m in guild.members if not m.bot)

            # Prefer bot.get_channel so we can find channels across caches; verify they belong to this guild
            all_channel = self.bot.get_channel(ALL_MEMBERS_CHANNEL_ID)
            nonbot_channel = self.bot.get_channel(NON_BOTS_CHANNEL_ID)

            updates = []

            if all_channel and getattr(all_channel, "guild", None) and all_channel.guild.id == guild.id:
                new_name = f"🌎 All members: {total_members}"
                if getattr(all_channel, "name", None) != new_name:
                    updates.append(all_channel.edit(name=new_name))
            else:
                # Debug info
                if all_channel is None:
                    print(f"[MemberCount] All-members channel {ALL_MEMBERS_CHANNEL_ID} not found (bot.get_channel returned None).")
                else:
                    print(f"[MemberCount] All-members channel {ALL_MEMBERS_CHANNEL_ID} exists but not in guild {guild.id}.")

            if nonbot_channel and getattr(nonbot_channel, "guild", None) and nonbot_channel.guild.id == guild.id:
                new_name = f"🧑 Members: {non_bots}"
                if getattr(nonbot_channel, "name", None) != new_name:
                    updates.append(nonbot_channel.edit(name=new_name))
            else:
                if nonbot_channel is None:
                    print(f"[MemberCount] Non-bots channel {NON_BOTS_CHANNEL_ID} not found (bot.get_channel returned None).")
                else:
                    print(f"[MemberCount] Non-bots channel {NON_BOTS_CHANNEL_ID} exists but not in guild {guild.id}.")

            # Execute updates concurrently and inspect results
            if updates:
                results = await asyncio.gather(*updates, return_exceptions=True)
                # Print any exceptions
                for r in results:
                    if isinstance(r, Exception):
                        print(f"[MemberCount] Channel edit error: {r}")
                print(f"[MemberCount] Updated counts in {guild.name} ({guild.id}): {total_members} total, {non_bots} humans")
            else:
                print(f"[MemberCount] No channel updates needed for guild {guild.name} ({guild.id}).")

        except Exception as e:
            print(f"[MemberCount] Error updating {getattr(guild, 'name', guild.id)}: {e}")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Handle member join with queueing."""
        try:
            await self.update_counts(member.guild)
        except Exception as e:
            print(f"[MemberCount] on_member_join error: {e}")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """Handle member leave with queueing."""
        try:
            await self.update_counts(member.guild)
        except Exception as e:
            print(f"[MemberCount] on_member_remove error: {e}")

    @commands.Cog.listener()
    async def on_ready(self):
        """Update counts when bot starts (once ready)."""
        print("[MemberCount] on_ready: initializing member counts for cached guilds...")
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
