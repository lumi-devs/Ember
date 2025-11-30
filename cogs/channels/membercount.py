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
        self.rate_limit_delay = 5  # Reduced from 10 to 5 seconds

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

                # Process all pending updates in this cycle
                guilds_to_process = list(self.pending_updates)
                self.pending_updates.clear()
                
                for guild_id in guilds_to_process:
                    guild = self.bot.get_guild(guild_id)
                    if not guild:
                        continue

                    # Less aggressive rate limiting
                    current_time = time.time()
                    last_update = self.last_update.get(guild_id, 0)
                    
                    if current_time - last_update < self.rate_limit_delay:
                        # Still rate limited, re-add to pending
                        self.pending_updates.add(guild_id)
                        continue

                    # Perform the update
                    success = await self._update_counts(guild)
                    if success:
                        self.last_update[guild_id] = current_time
                    else:
                        # If update failed, retry later
                        self.pending_updates.add(guild_id)

                # Small delay between processing cycles
                await asyncio.sleep(2)

            except Exception as e:
                print(f"[MemberCount] Worker error: {e}")
                await asyncio.sleep(5)  # Longer delay on error

    async def request_update(self, guild: discord.Guild):
        """Request an update for a guild (non-blocking)."""
        if not guild:
            return
            
        self.pending_updates.add(guild.id)
        print(f"[MemberCount] Update requested for guild: {guild.name} ({guild.id})")

    async def _update_counts(self, guild: discord.Guild) -> bool:
        """Update both counters efficiently with error handling. Returns success status."""
        try:
            # Get member counts
            total_members = guild.member_count
            
            # Count non-bot members
            non_bots = sum(1 for m in guild.members if not m.bot)

            print(f"[MemberCount] Processing {guild.name}: {total_members} total, {non_bots} humans")

            # Get channels - IMPORTANT: These channels must be in the same guild!
            all_channel = guild.get_channel(ALL_MEMBERS_CHANNEL_ID)
            nonbot_channel = guild.get_channel(NON_BOTS_CHANNEL_ID)

            if not all_channel or not nonbot_channel:
                print(f"[MemberCount] ERROR: Channels not found in {guild.name}! "
                      f"All: {bool(all_channel)}, Non-bot: {bool(nonbot_channel)}")
                return False

            # Verify channel types and permissions
            if not isinstance(all_channel, discord.VoiceChannel):
                print(f"[MemberCount] ERROR: All members channel is not a voice channel in {guild.name}")
                return False
                
            if not isinstance(nonbot_channel, discord.VoiceChannel):
                print(f"[MemberCount] ERROR: Non-bot channel is not a voice channel in {guild.name}")
                return False

            updates_made = 0

            # Update all members channel
            new_all_name = f"🌎 All Members: {total_members}"
            if all_channel.name != new_all_name:
                try:
                    await all_channel.edit(name=new_all_name)
                    updates_made += 1
                    print(f"[MemberCount] Updated {guild.name} - All Members: {total_members}")
                except discord.Forbidden:
                    print(f"[MemberCount] Missing permissions to update all members channel in {guild.name}")
                    return False
                except discord.HTTPException as e:
                    print(f"[MemberCount] HTTP error updating all members channel in {guild.name}: {e}")
                    # Don't return False here, try the other channel

            # Update non-bots channel
            new_nonbot_name = f"🧑 Members: {non_bots}"
            if nonbot_channel.name != new_nonbot_name:
                try:
                    await nonbot_channel.edit(name=new_nonbot_name)
                    updates_made += 1
                    print(f"[MemberCount] Updated {guild.name} - Non-Bots: {non_bots}")
                except discord.Forbidden:
                    print(f"[MemberCount] Missing permissions to update non-bots channel in {guild.name}")
                    return False
                except discord.HTTPException as e:
                    print(f"[MemberCount] HTTP error updating non-bots channel in {guild.name}: {e}")

            if updates_made > 0:
                print(f"[MemberCount] Successfully updated {updates_made} channel(s) in {guild.name}")
            else:
                print(f"[MemberCount] No updates needed for {guild.name}")

            return True

        except Exception as e:
            print(f"[MemberCount] Critical error updating counts for {guild.name}: {e}")
            return False

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Handle member join."""
        # Skip bots to reduce unnecessary updates
        if member.bot:
            return
            
        print(f"[MemberCount] Member joined: {member.display_name}")
        await asyncio.sleep(3)  # Wait for member count to update
        await self.request_update(member.guild)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """Handle member leave."""
        print(f"[MemberCount] Member left: {member.display_name}")
        await asyncio.sleep(3)  # Wait for member count to update
        await self.request_update(member.guild)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        """Handle member updates (like bot status changes)."""
        # If a member changes from bot to non-bot or vice versa
        if before.bot != after.bot:
            print(f"[MemberCount] Bot status changed for {after.display_name}")
            await asyncio.sleep(2)
            await self.request_update(after.guild)

    @commands.Cog.listener()
    async def on_ready(self):
        """Update counts when bot starts."""
        print("[MemberCount] Bot ready - updating counts for all guilds...")
        
        # Wait a bit for everything to stabilize
        await asyncio.sleep(10)
        
        for guild in self.bot.guilds:
            print(f"[MemberCount] Initial update for {guild.name}")
            await self.request_update(guild)
            await asyncio.sleep(2)  # Stagger initial updates

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
        
        human_count = sum(1 for m in ctx.guild.members if not m.bot)
        embed.add_field(name="Human Members", value=human_count, inline=True)
            
        embed.add_field(name="Worker Running", value=self.worker_running, inline=True)
        
        # Add channel status
        all_channel = ctx.guild.get_channel(ALL_MEMBERS_CHANNEL_ID)
        nonbot_channel = ctx.guild.get_channel(NON_BOTS_CHANNEL_ID)
        
        embed.add_field(name="All Members Channel", value=all_channel.mention if all_channel else "❌ Not Found", inline=True)
        embed.add_field(name="Non-Bot Channel", value=nonbot_channel.mention if nonbot_channel else "❌ Not Found", inline=True)
        
        await ctx.send(embed=embed)

    @commands.command(name='forceupdate')
    @commands.has_permissions(manage_channels=True)
    async def force_update(self, ctx):
        """Force immediate update bypassing rate limits."""
        self.last_update[ctx.guild.id] = 0  # Reset rate limit
        await self._update_counts(ctx.guild)  # Immediate update
        
        embed = discord.Embed(
            description="🔄 Force update completed!",
            color=discord.Color.orange()
        )
        await ctx.send(embed=embed, delete_after=10)

async def setup(bot: commands.Bot):
    await bot.add_cog(MemberCount(bot))