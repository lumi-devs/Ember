# cogs/omegle/omegle_matchmaker.py
import discord
from discord.ext import commands

class Matchmaker:
    def __init__(self, bot: commands.Bot, male_role, female_role, anyone_roles, hub_channel_id):
        self.bot = bot
        self.male_role = male_role
        self.female_role = female_role
        self.anyone_roles = anyone_roles
        self.hub_channel_id = hub_channel_id
        self.queue = []  # list of {"user_id": int, "choice": str}

    async def add_to_queue(self, user: discord.Member, choice: str):
        # Avoid duplicates
        for entry in self.queue:
            if entry["user_id"] == user.id:
                return False
        self.queue.append({"user_id": user.id, "choice": choice})
        return True

    async def remove_from_queue(self, user_id: int):
        self.queue = [entry for entry in self.queue if entry["user_id"] != user_id]

    async def find_match(self, guild: discord.Guild):
        if len(self.queue) < 2:
            return None, None

        # Convert queue entries to actual members
        members = []
        for entry in self.queue:
         member = guild.get_member(entry["user_id"])
         if member:
            members.append({"member": member, "choice": entry["choice"]})

        # Find first compatible pair
        for i, a in enumerate(members):
         for j, b in enumerate(members):
            if i == j:
                continue
            if self._choices_compatible(a["choice"], b["choice"]):
                # Remove matched users from queue
                await self.remove_from_queue(a["member"].id)
                await self.remove_from_queue(b["member"].id)
                return a["member"], b["member"]
        return None, None

    def _choices_compatible(self, choice1, choice2):
     """Check if two users' preferences are compatible."""
     if choice1 == "any" or choice2 == "any":
        return True
     if choice1 == choice2:
        return False  # same gender choice cannot pair
     return True  # male/female combination


    async def create_chat(self, guild: discord.Guild, u1: discord.Member, u2: discord.Member):
        hub_channel = guild.get_channel(self.hub_channel_id)
        if not hub_channel:
            print(f"[DEBUG] Hub channel ID {self.hub_channel_id} not found in guild {guild.name}")
            return None

        try:
            thread = await hub_channel.create_thread(
                name=f"{u1.display_name}-{u2.display_name}",
                type=discord.ChannelType.private_thread,
                auto_archive_duration=60
            )
            print(f"[DEBUG] Thread successfully created: {thread.name} ({thread.id})")
            await thread.add_user(u1)
            await thread.add_user(u2)
            return thread
        except Exception as e:
            print(f"[DEBUG] Exception creating thread: {e}")
            return None
