import discord
from discord.ext import commands
import asyncio
from typing import Set, Dict, List, Optional
from collections import defaultdict
import time

class ActivityRoles(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.roles: Dict[str, int] = {
            "Crunchyroll": 1418229568191467530,
            "YouTube": 1418229577679114291,
            "Spotify": 1418229573325426769,
        }
        self.INTERVAL_TIME_UPDATE = 10 # seconds
        self.processing: Set[int] = set()  # Prevent concurrent processing
        self.rate_limit: Dict[int, float] = {}  # Rate limiting per user
        self._guild_roles_cache: Dict[int, Dict[str, Optional[discord.Role]]] = {}
        self.cleanup_done: bool = False

    async def cog_load(self):
        """Called when the cog is loaded - performs initial cleanup"""
        # Don't block startup with cleanup
        self.bot.loop.create_task(self.cleanup_activity_roles())

    def get_guild_roles(self, guild: discord.Guild) -> Dict[str, Optional[discord.Role]]:
        """Cache guild roles to avoid repeated API calls"""
        if guild.id not in self._guild_roles_cache:
            self._guild_roles_cache[guild.id] = {
                "Crunchyroll": guild.get_role(self.roles["Crunchyroll"]),
                "YouTube": guild.get_role(self.roles["YouTube"]),
                "Spotify": guild.get_role(self.roles["Spotify"])
            }
        return self._guild_roles_cache[guild.id]

    async def cleanup_activity_roles(self):
        """Cleanup: remove all activity roles when bot starts."""
        if self.cleanup_done:
            return
            
        print("🧹 Cleaning up activity roles on startup...")
        cleanup_count = 0
        
        for guild in self.bot.guilds:
            guild_roles = self.get_guild_roles(guild)
            activity_roles = [role for role in guild_roles.values() if role is not None]
            
            if not activity_roles:
                print(f"❌ No activity roles found in {guild.name}")
                continue

            # Process members in chunks for better performance
            members_processed = 0
            async for member in guild.fetch_members(limit=None):
                members_processed += 1
                
                # Check if member has any activity roles
                roles_to_remove = [role for role in activity_roles if role in member.roles]
                if not roles_to_remove:
                    continue

                try:
                    await member.remove_roles(*roles_to_remove, reason="Startup cleanup")
                    cleanup_count += len(roles_to_remove)
                    print(f"🧹 Removed {[r.name for r in roles_to_remove]} from {member.display_name}")
                except discord.Forbidden:
                    print(f"❌ Missing permissions to clean roles for {member.display_name}")
                except discord.HTTPException as e:
                    print(f"❌ Cleanup failed for {member.display_name}: {e}")
                
                # Batch processing delay every 50 members
                if members_processed % 50 == 0:
                    await asyncio.sleep(0.1)
        
        self.cleanup_done = True
        print(f"✅ Cleanup complete. Processed {cleanup_count} role removals across {len(self.bot.guilds)} guilds.")

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member):
        """Handle activity-based role assignments with rate limiting."""
        if after.guild is None or after.bot:
            return

        # Rate limiting: 1 update per user every INTERVAL_TIME_UPDATE seconds
        current_time = time.time()
        if after.id in self.rate_limit and current_time - self.rate_limit[after.id] < self.INTERVAL_TIME_UPDATE:
            return
        
        # Prevent concurrent processing for same user
        if after.id in self.processing:
            return
            
        self.processing.add(after.id)
        self.rate_limit[after.id] = current_time

        try:
            await self._process_activity_roles(before, after)
        except Exception as e:
            print(f"❌ Error processing activity roles for {after.display_name}: {e}")
        finally:
            self.processing.discard(after.id)

    async def _process_activity_roles(self, before: discord.Member, after: discord.Member):
        """Process activity role changes for a member."""
        guild = after.guild
        guild_roles = self.get_guild_roles(guild)

        # Check if all roles exist
        if not all(guild_roles.values()):
            missing_roles = [name for name, role in guild_roles.items() if role is None]
            print(f"❌ Missing activity roles in {guild.name}: {missing_roles}")
            return

        crunchy_role, yt_role, spotify_role = guild_roles.values()

        # Detect current activities more efficiently
        active_roles = set()
        current_roles = set(after.roles)
        
        for activity in after.activities:
            # Skip custom activities early
            if activity.type == discord.ActivityType.custom:
                continue
                
            # Check for Spotify activity
            if isinstance(activity, discord.Spotify):
                active_roles.add(spotify_role)
                continue
                
            # Check for regular activities by name
            if isinstance(activity, discord.Activity) and activity.name:
                activity_name_lower = activity.name.lower()
                
                if "youtube" in activity_name_lower:
                    active_roles.add(yt_role)
                elif "crunchyroll" in activity_name_lower:
                    active_roles.add(crunchy_role)

        all_activity_roles = {crunchy_role, yt_role, spotify_role}
        to_add = active_roles - current_roles
        to_remove = (all_activity_roles - active_roles) & current_roles

        # Only proceed if there are changes
        if not to_add and not to_remove:
            return

        # Batch role operations
        try:
            if to_add:
                await after.add_roles(*to_add, reason="Activity detected")
                print(f"✅ Added {[r.name for r in to_add]} to {after.display_name}")
                
            if to_remove:
                await after.remove_roles(*to_remove, reason="Activity ended")
                print(f"✅ Removed {[r.name for r in to_remove]} from {after.display_name}")
                
        except discord.Forbidden:
            print(f"❌ Missing permissions to edit roles for {after.display_name}")
        except discord.HTTPException as e:
            print(f"❌ Failed to update roles for {after.display_name}: {e}")

    # Cache for role member counts
    _role_count_cache: Dict[int, Dict[int, int]] = defaultdict(dict)
    _last_count_update: float = 0

    async def get_role_member_count(self, guild: discord.Guild, role: discord.Role) -> int:
        """Get member count for a role with caching."""
        current_time = time.time()
        
        # Refresh cache every 5 minutes
        if (current_time - self._last_count_update > 300 or 
            guild.id not in self._role_count_cache or 
            role.id not in self._role_count_cache[guild.id]):
            
            # Count members with this role
            count = 0
            async for member in guild.fetch_members(limit=None):
                if role in member.roles:
                    count += 1
            
            self._role_count_cache[guild.id][role.id] = count
            self._last_count_update = current_time
        
        return self._role_count_cache[guild.id][role.id]

    @commands.hybrid_command(name='activityroles', description='Show current activity role configuration')
    @commands.has_permissions(manage_roles=True)
    async def activity_roles_info(self, ctx: commands.Context):
        """Show current activity role configuration"""
        embed = discord.Embed(title="🎯 Activity Roles", color=discord.Color.blue())
        
        guild_roles = self.get_guild_roles(ctx.guild)
        
        for platform, role in guild_roles.items():
            if role:
                member_count = await self.get_role_member_count(ctx.guild, role)
                embed.add_field(
                    name=platform,
                    value=f"{role.mention}\n{member_count} members",
                    inline=True
                )
            else:
                embed.add_field(
                    name=platform,
                    value="❌ Role not found",
                    inline=True
                )
        
        await ctx.send(embed=embed)


    @commands.hybrid_command(name='refreshroles', description='Refresh activity roles for a specific member')
    @commands.has_permissions(manage_roles=True)
    async def refresh_roles(self, ctx: commands.Context, member: discord.Member):
        """Manually refresh activity roles for a member"""
        if member.bot:
            await ctx.send("❌ Cannot refresh roles for bots.")
            return
            
        await ctx.defer()
        
        # Use actual before state if available, otherwise create minimal mock
        mock_before = discord.Object(id=member.id)
        mock_before.roles = member.roles
        mock_before.activities = []
        
        try:
            await self._process_activity_roles(mock_before, member)
            await ctx.send(f"✅ Refreshed activity roles for {member.mention}")
        except Exception as e:
            await ctx.send(f"❌ Failed to refresh roles: {e}")

    def cog_unload(self):
        """Clean up when cog is unloaded"""
        self._guild_roles_cache.clear()
        self._role_count_cache.clear()

async def setup(bot: commands.Bot):
    await bot.add_cog(ActivityRoles(bot))