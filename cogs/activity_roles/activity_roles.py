import discord
from discord.ext import commands
import asyncio
from typing import Set, Dict

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

    @commands.Cog.listener()
    async def on_ready(self):
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
            members_to_process = [member for member in guild.members if any(role in member.roles for role in activity_roles)]
            
            for i, member in enumerate(members_to_process):
                to_remove = [role for role in activity_roles if role in member.roles]
                if to_remove:
                    try:
                        await member.remove_roles(*to_remove, reason="Startup cleanup")
                        cleanup_count += 1
                        print(f"🧹 Removed {[r.name for r in to_remove]} from {member}")
                    except discord.Forbidden:
                        print(f"❌ Missing permissions to clean roles for {member}")
                    except discord.HTTPException as e:
                        print(f"❌ Cleanup failed for {member}: {e}")
                
                # Small delay every 10 members to avoid rate limits
                if i % 10 == 0:
                    await asyncio.sleep(0.5)
        
        self.cleanup_done = True
        print(f"✅ Cleanup complete. Processed {cleanup_count} members.")

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
            if isinstance(activity, discord.CustomActivity):
                continue
                
            if isinstance(activity, discord.Spotify):
                active_roles.add(spotify_role)
                print(f"🎵 {after} is listening to Spotify: {activity.title}")
                
            elif getattr(activity, "name", None) == "YouTube":
                active_roles.add(yt_role)
                print(f"📺 {after} is watching YouTube")
                
            elif getattr(activity, "name", None) == "Crunchyroll":
                active_roles.add(crunchy_role)
                print(f"🍿 {after} is watching Crunchyroll")

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
                print(f"✅ Added {[r.name for r in to_add]} to {after}")
                
            if to_remove:
                await after.remove_roles(*to_remove, reason="Activity ended")
                print(f"✅ Removed {[r.name for r in to_remove]} from {after}")
                
        except discord.Forbidden:
            print(f"❌ Missing permissions to edit roles for {after}")
        except discord.HTTPException as e:
            print(f"❌ Failed to update roles for {after}: {e}")

    # Optional: Manual role management commands
    @commands.command(name='activityroles')
    @commands.has_permissions(manage_roles=True)
    async def activity_roles_info(self, ctx):
        """Show current activity role configuration"""
        embed = discord.Embed(title="🎯 Activity Roles", color=discord.Color.blue())
        
        for platform, role_id in self.roles.items():
            role = ctx.guild.get_role(role_id)
            if role:
                members_with_role = len(role.members)
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

    @commands.command(name='forcecleanup')
    @commands.has_permissions(administrator=True)
    async def force_cleanup(self, ctx):
        """Force cleanup of all activity roles"""
        await self.on_ready()
        await ctx.send("✅ Force cleanup completed!")

async def setup(bot: commands.Bot):
    await bot.add_cog(ActivityRoles(bot))