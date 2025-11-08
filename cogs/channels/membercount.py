import discord
from discord.ext import commands

# ✅ Replace with your actual channel IDs
ALL_MEMBERS_CHANNEL_ID = 1393175609638912010
NON_BOTS_CHANNEL_ID = 1393175726467055636


class MemberCount(commands.Cog):
    """Updates two channel names on join/leave events."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def update_counts(self, guild: discord.Guild):
        """Update both counters efficiently."""
        try:
            total_members = guild.member_count
            non_bots = sum(1 for m in guild.members if not m.bot)

            all_channel = guild.get_channel(ALL_MEMBERS_CHANNEL_ID)
            nonbot_channel = guild.get_channel(NON_BOTS_CHANNEL_ID)

            if all_channel and all_channel.name != f"⁠🌎 All members: {total_members}":
                await all_channel.edit(name=f"⁠🌎 All members: {total_members}")

            if nonbot_channel and nonbot_channel.name != f"N⁠🧑 Members: {non_bots}":
                await nonbot_channel.edit(name=f"⁠🧑 Members: {non_bots}")

        except Exception as e:
            print(f"[MemberCount] Error updating: {e}")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        await self.update_counts(member.guild)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        await self.update_counts(member.guild)


async def setup(bot: commands.Bot):
    await bot.add_cog(MemberCount(bot))
