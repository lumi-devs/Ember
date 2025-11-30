import discord
from discord.ext import commands
import asyncio
from typing import Set, Dict, List

class ActivityRoles(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.roles: Dict[str, int] = {
            "Crunchyroll": 1418229568191467530,
            "YouTube": 1418229577679114291,
            "Spotify": 1418229573325426769,
        }
        self.processing: Set[int] = set()  # Prevent concurrent processing
        self.rate_limit: Dict[int, float] = {}  # Rate limiting per user
        self.cleanup_done: bool = False

    async def cog_load(self):
        """Called when the cog is loaded - performs initial cleanup"""
        await self.cleanup_activity_roles()

    async def cleanup_activity_roles(self):
        """Cleanup: remove all activity roles when bot starts."""
        if self.cleanup_done:
            return
            
        print("🧹 Cleaning up activity roles on startup...")
        cleanup_count = 0
        
        for guild in self.bot.guilds:
            # Get roles for this guild
            activity_roles = []
            for role_name, role_id in self.roles.items():
                role = guild.get_role(role_id)
                if role:
                    activity_roles.append(role)
                    print(f"✅ Found {role_name} role in {guild.name}")
                else:
                    print(f"❌ {role_name} role not found in {guild.name}")

            if not activity_roles:
                continue

            # Process members in batches to avoid rate limits
            async for member in guild.fetch_members(limit=None):
                if any(role in member.roles for role in activity_roles):
                    to_remove = [role for role in activity_roles if role in member.roles]
                    if to_remove:
                        try:
                            await member.remove_roles(*to_remove, reason="Startup cleanup")
                            cleanup_count += len(to_remove)
                            print(f"🧹 Removed {[r.name for r in to_remove]} from {member.display_name}")
                        except discord.Forbidden:
                            print(f"❌ Missing permissions to clean roles for {member.display_name}")
                        except discord.HTTPException as e:
                            print(f"❌ Cleanup failed for {member.display_name}: {e}")
                
                # Small delay every 10 members to avoid rate limits
                if cleanup_count % 10 == 0:
                    await asyncio.sleep(0.5)
        
        self.cleanup_done = True
        print(f"✅ Cleanup complete. Processed {cleanup_count} role removals.")

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member):
        """Handle activity-based role assignments with rate limiting."""
        if after.guild is None:
            return

        # Rate limiting: 1 update per user every 10 seconds
        current_time = discord.utils.utcnow().timestamp()
        if after.id in self.rate_limit and current_time - self.rate_limit[after.id] < 10:
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

        # Get roles for this guild
        crunchy_role = guild.get_role(self.roles["Crunchyroll"])
        yt_role = guild.get_role(self.roles["YouTube"])
        spotify_role = guild.get_role(self.roles["Spotify"])

        if not all([crunchy_role, yt_role, spotify_role]):
            print(f"❌ Missing activity roles in {guild.name}")
            return

        # Detect current activities
        active_roles = set()
        for activity in after.activities:
            # Skip custom activities
            if isinstance(activity, discord.CustomActivity):
                continue
                
            # Check for Spotify activity
            if isinstance(activity, discord.Spotify):
                active_roles.add(spotify_role)
                print(f"🎵 {after.display_name} is listening to Spotify: {activity.title}")
                continue
                
            # Check for regular activities by name
            if isinstance(activity, discord.Activity):
                activity_name_lower = activity.name.lower() if activity.name else ""
                
                if "youtube" in activity_name_lower:
                    active_roles.add(yt_role)
                    print(f"📺 {after.display_name} is watching YouTube: {activity.name}")
                    
                elif "crunchyroll" in activity_name_lower:
                    active_roles.add(crunchy_role)
                    print(f"🍿 {after.display_name} is watching Crunchyroll: {activity.name}")

        all_activity_roles = {crunchy_role, yt_role, spotify_role}
        current_roles = set(after.roles)

        to_add = active_roles - current_roles
        to_remove = (all_activity_roles - active_roles) & current_roles

        # Only proceed if there are changes
        if not to_add and not to_remove:
            return

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

    # Optional: Manual role management commands
    @commands.hybrid_command(name='activityroles', description='Show current activity role configuration')
    @commands.has_permissions(manage_roles=True)
    async def activity_roles_info(self, ctx: commands.Context):
        """Show current activity role configuration"""
        embed = discord.Embed(title="🎯 Activity Roles", color=discord.Color.blue())
        
        for platform, role_id in self.roles.items():
            role = ctx.guild.get_role(role_id)
            if role:
                # Use fetch_members to get accurate count
                members_with_role = len([m async for m in ctx.guild.fetch_members() if role in m.roles])
                embed.add_field(
                    name=platform,
                    value=f"{role.mention}\n{members_with_role} members",
                    inline=True
                )
            else:
                embed.add_field(
                    name=platform,
                    value="❌ Role not found",
                    inline=True
                )
        
        await ctx.send(embed=embed)

    @commands.hybrid_command(name='forcecleanup', description='Force cleanup of all activity roles')
    @commands.has_permissions(administrator=True)
    async def force_cleanup(self, ctx: commands.Context):
        """Force cleanup of all activity roles"""
        await ctx.defer()
        await self.cleanup_activity_roles()
        await ctx.send("✅ Force cleanup completed!")

    @commands.hybrid_command(name='refreshroles', description='Refresh activity roles for a specific member')
    @commands.has_permissions(manage_roles=True)
    async def refresh_roles(self, ctx: commands.Context, member: discord.Member):
        """Manually refresh activity roles for a member"""
        await ctx.defer()
        
        # Create a mock "before" state with no activities
        class MockMember:
            def __init__(self, member):
                self.activities = []
                self.roles = member.roles
                self.guild = member.guild
                self.id = member.id
                self.display_name = member.display_name
        
        mock_before = MockMember(member)
        
        try:
            await self._process_activity_roles(mock_before, member)
            await ctx.send(f"✅ Refreshed activity roles for {member.mention}")
        except Exception as e:
            await ctx.send(f"❌ Failed to refresh roles: {e}")

async def setup(bot: commands.Bot):
    await bot.add_cog(ActivityRoles(bot))