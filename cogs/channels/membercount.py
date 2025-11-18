import discord
from discord.ext import commands
import asyncio
from typing import Dict, Set
import time

# ✅ Replace with your actual channel IDs (these must be channels in the same guild)
ALL_MEMBERS_CHANNEL_ID = 1393175609638912010
NON_BOTS_CHANNEL_ID = 1393175726467055636


class MemberCount(commands.Cog):
    """Updates two channel names on join/leave events with optimizations."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.pending_updates: Set[int] = set()  # guild IDs that need updates
        self.worker_running = False
        self.last_update: Dict[int, float] = {}  # last update timestamp per guild
        self.rate_limit_delay = 10  # seconds between updates per guild

    async def cog_load(self):
        """Start the background worker when cog loads."""
        self.worker_running = True
        asyncio.create_task(self._update_worker(), name="MemberCountWorker")
        print("[MemberCount] Background worker started.")

    async def cog_unload(self):
        """Stop the background worker when cog unloads."""
        self.worker_running = False
        print("[MemberCount] Background worker stopped.")

    async def _update_worker(self):
        """Background worker to process update requests."""
        print("[MemberCount] Worker running.")
        while self.worker_running:
            try:
                if not self.pending_updates:
                    await asyncio.sleep(1)
                    continue

                # Get the next guild that needs updating
                guild_id = next(iter(self.pending_updates))
                self.pending_updates.discard(guild_id)
                
                guild = self.bot.get_guild(guild_id)
                if not guild:
                    continue

                # Rate limiting check
                current_time = time.time()
                last_update = self.last_update.get(guild_id, 0)
                
                if current_time - last_update < self.rate_limit_delay:
                    # Too soon, re-add to pending and wait
                    self.pending_updates.add(guild_id)
                    await asyncio.sleep(1)
                    continue

                # Perform the update
                await self._update_counts(guild)
                self.last_update[guild_id] = current_time

                # Small delay between updates
                await asyncio.sleep(0.5)

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[MemberCount] Worker error: {e}")
                await asyncio.sleep(1)

    async def request_update(self, guild: discord.Guild):
        """Request an update for a guild (non-blocking)."""
        if not guild:
            return
            
        self.pending_updates.add(guild.id)
        print(f"[MemberCount] Update requested for guild: {guild.name} ({guild.id})")

    async def _update_counts(self, guild: discord.Guild):
        """Update both counters efficiently with error handling."""
        try:
            # Get member counts
            total_members = guild.member_count
            
            # Count non-bot members (requires members intent)
            non_bots = 0
            try:
                # Try to use cached members first
                non_bots = sum(1 for m in guild.members if not m.bot)
            except Exception as e:
                print(f"[MemberCount] Error counting non-bots in {guild.name}: {e}")
                # Fallback: assume 90% are non-bots if we can't count
                non_bots = int(total_members * 0.9)

            # Get channels
            all_channel = self.bot.get_channel(ALL_MEMBERS_CHANNEL_ID)
            nonbot_channel = self.bot.get_channel(NON_BOTS_CHANNEL_ID)

            updates = []
            updated_channels = []

            # Update all members channel
            if (all_channel and 
                isinstance(all_channel, discord.VoiceChannel) and 
                all_channel.guild.id == guild.id):
                
                new_name = f"🌎 All Members: {total_members}"
                if all_channel.name != new_name:
                    try:
                        await all_channel.edit(name=new_name)
                        updated_channels.append("All Members")
                        print(f"[MemberCount] Updated {guild.name} - All Members: {total_members}")
                    except discord.HTTPException as e:
                        print(f"[MemberCount] Failed to update all members channel in {guild.name}: {e}")

            # Update non-bots channel
            if (nonbot_channel and 
                isinstance(nonbot_channel, discord.VoiceChannel) and 
                nonbot_channel.guild.id == guild.id):
                
                new_name = f"🧑 Members: {non_bots}"
                if nonbot_channel.name != new_name:
                    try:
                        await nonbot_channel.edit(name=new_name)
                        updated_channels.append("Non-Bots")
                        print(f"[MemberCount] Updated {guild.name} - Non-Bots: {non_bots}")
                    except discord.HTTPException as e:
                        print(f"[MemberCount] Failed to update non-bots channel in {guild.name}: {e}")

            if updated_channels:
                print(f"[MemberCount] Successfully updated {', '.join(updated_channels)} in {guild.name}")
            else:
                print(f"[MemberCount] No updates needed for {guild.name}")

        except Exception as e:
            print(f"[MemberCount] Error updating counts for {guild.name}: {e}")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Handle member join."""
        try:
            # Small delay to ensure member count is updated
            await asyncio.sleep(2)
            await self.request_update(member.guild)
        except Exception as e:
            print(f"[MemberCount] on_member_join error: {e}")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """Handle member leave."""
        try:
            # Small delay to ensure member count is updated
            await asyncio.sleep(2)
            await self.request_update(member.guild)
        except Exception as e:
            print(f"[MemberCount] on_member_remove error: {e}")

    @commands.Cog.listener()
    async def on_ready(self):
        """Update counts when bot starts."""
        print("[MemberCount] Bot ready - updating counts for all guilds...")
        
        # Wait a bit for everything to stabilize
        await asyncio.sleep(5)
        
        for guild in self.bot.guilds:
            await self.request_update(guild)
            await asyncio.sleep(1)  # Stagger initial updates

    @commands.command(name='updatecounts')
    @commands.has_permissions(manage_channels=True)
    async def manual_update(self, ctx):
        """Manually update member count channels."""
        await self.request_update(ctx.guild)
        embed = discord.Embed(
            description="✅ Member counts update requested!",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed, delete_after=10)

    @commands.command(name='countstatus')
    @commands.has_permissions(manage_channels=True)
    async def count_status(self, ctx):
        """Show member count system status."""
        guild_id = ctx.guild.id
        last_update = self.last_update.get(guild_id, 0)
        time_since = time.time() - last_update
        
        embed = discord.Embed(title="📊 Member Count Status", color=discord.Color.blue())
        embed.add_field(name="Pending Updates", value=len(self.pending_updates), inline=True)
        embed.add_field(name="Last Update", value=f"{time_since:.1f}s ago", inline=True)
        embed.add_field(name="Total Members", value=ctx.guild.member_count, inline=True)
        
        try:
            human_count = sum(1 for m in ctx.guild.members if not m.bot)
            embed.add_field(name="Human Members", value=human_count, inline=True)
        except:
            embed.add_field(name="Human Members", value="Unknown", inline=True)
            
        embed.add_field(name="Worker Running", value=self.worker_running, inline=True)
        
        await ctx.send(embed=embed)

    @commands.command(name='forceupdate')
    @commands.has_permissions(manage_channels=True)
    async def force_update(self, ctx):
        """Force immediate update bypassing rate limits."""
        self.last_update[ctx.guild.id] = 0  # Reset rate limit
        await self.request_update(ctx.guild)
        
        embed = discord.Embed(
            description="🔄 Force update requested!",
            color=discord.Color.orange()
        )
        await ctx.send(embed=embed, delete_after=10)

async def setup(bot: commands.Bot):
    await bot.add_cog(MemberCount(bot))