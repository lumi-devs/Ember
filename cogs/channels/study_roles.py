import discord
from discord.ext import commands


class VoiceRoleManager(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.voice_channel_id = (
            1446175613382561895  # Set this to your specific voice channel ID
        )
        self.role_id = 1430254891833757796  # Set this to the role ID you want to assign
        self.log_channel_id = None  # Optional: for logging

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"✅ {self.__class__.__name__} cog is ready!")
        print("⚠️  Remember to set voice_channel_id and role_id in the cog!")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        """Triggered when a user changes voice state (joins/leaves/moves channels)"""

        # Skip if IDs are not configured
        if self.voice_channel_id is None or self.role_id is None:
            return

        # Get the target role and channel
        guild = member.guild
        target_role = guild.get_role(self.role_id)

        if target_role is None:
            print(f"❌ Role with ID {self.role_id} not found!")
            return

        target_channel = guild.get_channel(self.voice_channel_id)

        if target_channel is None:
            print(f"❌ Voice channel with ID {self.voice_channel_id} not found!")
            return

        # User joined the target voice channel
        if after.channel and after.channel.id == self.voice_channel_id:
            # Add role if they don't already have it
            if target_role not in member.roles:
                try:
                    await member.add_roles(
                        target_role, reason=f"Joined {target_channel.name}"
                    )
                    await self.log_action(
                        f"✅ {member.display_name} joined {target_channel.name} and received {target_role.name} role"
                    )
                except discord.Forbidden:
                    print(
                        f"❌ Missing permissions to add role to {member.display_name}"
                    )
                except Exception as e:
                    print(f"❌ Error adding role: {e}")

        # User left the target voice channel
        elif before.channel and before.channel.id == self.voice_channel_id:
            # Remove role if they have it
            if target_role in member.roles:
                try:
                    await member.remove_roles(
                        target_role, reason=f"Left {target_channel.name}"
                    )
                    await self.log_action(
                        f"❌ {member.display_name} left {target_channel.name} and lost {target_role.name} role"
                    )
                except discord.Forbidden:
                    print(
                        f"❌ Missing permissions to remove role from {member.display_name}"
                    )
                except Exception as e:
                    print(f"❌ Error removing role: {e}")

        # User moved from target channel to another channel
        elif (
            before.channel
            and before.channel.id == self.voice_channel_id
            and after.channel
            and after.channel.id != self.voice_channel_id
        ):
            # Remove role if they have it
            if target_role in member.roles:
                try:
                    await member.remove_roles(
                        target_role, reason=f"Moved from {target_channel.name}"
                    )
                    await self.log_action(
                        f"➡️ {member.display_name} moved from {target_channel.name} and lost {target_role.name} role"
                    )
                except discord.Forbidden:
                    print(
                        f"❌ Missing permissions to remove role from {member.display_name}"
                    )
                except Exception as e:
                    print(f"❌ Error removing role: {e}")

    async def log_action(self, message):
        """Log actions to a specified channel if configured"""
        if self.log_channel_id:
            try:
                log_channel = self.bot.get_channel(self.log_channel_id)
                if log_channel:
                    await log_channel.send(message)
            except Exception as e:
                print(f"❌ Error sending log: {e}")


async def setup(bot):
    await bot.add_cog(VoiceRoleManager(bot))
