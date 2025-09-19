import discord
from discord.ext import commands

class ActivityRoles(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.roles = {
            "Crunchyroll": 1418229568191467530,
            "YouTube": 1418229577679114291,
            "Spotify": 1418229573325426769,
        }

    @commands.Cog.listener()
    async def on_ready(self):
        """Cleanup: remove all activity roles when bot starts."""
        print("🧹 Cleaning up activity roles on startup...")
        for guild in self.bot.guilds:
            crunchy_role = guild.get_role(self.roles["Crunchyroll"])
            yt_role = guild.get_role(self.roles["YouTube"])
            spotify_role = guild.get_role(self.roles["Spotify"])
            all_roles = [r for r in [crunchy_role, yt_role, spotify_role] if r]

            if not all_roles:
                continue

            for member in guild.members:
                to_remove = [r for r in all_roles if r in member.roles]
                if to_remove:
                    try:
                        await member.remove_roles(*to_remove, reason="Startup cleanup")
                        print(f"🧹 Removed roles {[r.name for r in to_remove]} from {member}")
                    except discord.Forbidden:
                        print(f"❌ Missing permissions to clean roles for {member}")
                    except discord.HTTPException as e:
                        print(f"❌ Cleanup failed for {member}: {e}")
        print("✅ Cleanup complete.")

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member):
        if after.guild is None:
            return

        guild = after.guild

        crunchy_role = guild.get_role(self.roles["Crunchyroll"])
        yt_role = guild.get_role(self.roles["YouTube"])
        spotify_role = guild.get_role(self.roles["Spotify"])

        if not all([crunchy_role, yt_role, spotify_role]):
            return

        active_roles = set()

        for activity in after.activities:
            if isinstance(activity, discord.CustomActivity):
                continue
            if isinstance(activity, discord.Spotify):
                active_roles.add(spotify_role)
            elif getattr(activity, "name", None) == "YouTube":
                active_roles.add(yt_role)
            elif getattr(activity, "name", None) == "Crunchyroll":
                active_roles.add(crunchy_role)

        all_activity_roles = {crunchy_role, yt_role, spotify_role}
        current_roles = set(after.roles)

        to_add = active_roles - current_roles
        to_remove = (all_activity_roles - active_roles) & current_roles

        try:
            if to_add:
                await after.add_roles(*to_add, reason="Activity role add")
                print(f"✅ Added roles {[r.name for r in to_add]} to {after}.")
            if to_remove:
                await after.remove_roles(*to_remove, reason="Activity role remove")
                print(f"✅ Removed roles {[r.name for r in to_remove]} from {after}.")
        except discord.Forbidden:
            print(f"❌ Missing permissions to edit roles for {after}.")
        except discord.HTTPException as e:
            print(f"❌ Failed to update roles for {after}: {e}")

async def setup(bot: commands.Bot):
    await bot.add_cog(ActivityRoles(bot))
