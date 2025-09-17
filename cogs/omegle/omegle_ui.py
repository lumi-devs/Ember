# cogs/omegle/omegle_ui.py
import discord
from discord.ui import View, Button, Select
from datetime import datetime
import os
from cogs.omegle.omegle_matchmaker import Matchmaker

LOG_CHANNEL_ID = 1393171149038293023  # Replace with your logs channel ID

# ---------------------- Gender Selection ----------------------
class GenderSelectView(View):
    def __init__(self, matchmaker, user):
        super().__init__(timeout=900)
        self.matchmaker = matchmaker
        self.user = user
        options = [
            discord.SelectOption(label="Male", value="male"),
            discord.SelectOption(label="Female", value="female"),
            discord.SelectOption(label="Anyone", value="any")
        ]
        self.add_item(GenderSelect(options))

class GenderSelect(Select):
    def __init__(self, options):
        super().__init__(placeholder="Choose your preference...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        self.view.user_choice = self.values[0]
        # Add user to queue
        added = await self.view.matchmaker.add_to_queue(self.view.user, self.view.user_choice)
        if added:
            await interaction.followup.send(f"✅ Preference selected: {self.values[0]}. You are now in the queue.", ephemeral=True)
        else:
            await interaction.followup.send("⚠️ You are already in the queue.", ephemeral=True)

# ---------------------- Start Button ----------------------
class StartButton(Button):
    def __init__(self, matchmaker: Matchmaker):
        super().__init__(label="Start Chat", style=discord.ButtonStyle.green, custom_id="omegle_start_chat")
        self.matchmaker = matchmaker

    async def callback(self, interaction: discord.Interaction):
        from cogs.omegle.omegle_ui import GenderSelectView  # import here to avoid circular import
        view = GenderSelectView(self.matchmaker, interaction.user)
        await interaction.response.send_message(
            ":speech_balloon: Choose your preference to join the queue:",
            view=view,
            ephemeral=True
        )

# ---------------------- Persistent View ----------------------
class StartButtonView(View):
    def __init__(self, matchmaker: Matchmaker):
        super().__init__(timeout=None)  # Important for persistence
        self.matchmaker = matchmaker
        self.add_item(StartButton(self.matchmaker))
# ---------------------- Private Chat Buttons ----------------------
class PrivateChatView(discord.ui.View):
    def __init__(self, matchmaker, thread, user1, user2):
        super().__init__(timeout=None)
        self.thread = thread
        self.matchmaker = matchmaker
        self.users = [user1, user2]
        self.chat_log = []  # store chat here

        # Pass the view itself as view_ref
        self.add_item(LeaveButton(thread, user1, user2, self))
        self.add_item(SkipButton(thread, user1, user2, self))



class LeaveButton(discord.ui.Button):
    def __init__(self, thread, user1, user2, view_ref):
        super().__init__(style=discord.ButtonStyle.danger, label="Leave Chat")
        self.thread = thread
        self.users = [user1, user2]
        self.view_ref = view_ref  

    async def callback(self, interaction: discord.Interaction):
        # Log that this user left
        self.view_ref.chat_log.append((interaction.user.display_name, "left"))

        # Save chat to file
        log_content = "\n".join(f"{u}: {m}" for u, m in self.view_ref.chat_log)
        with open("chat_log.txt", "w") as f:
            f.write(log_content)

        # Upload log to a specific channel
        log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            await log_channel.send(file=discord.File("chat_log.txt"))

        # Delete thread
        await self.thread.delete()

class SkipButton(Button):
    def __init__(self, matchmaker, thread, u1, u2):
        super().__init__(label="Skip & Rejoin Queue", style=discord.ButtonStyle.grey)
        self.matchmaker = matchmaker
        self.thread = thread
        self.u1 = u1
        self.u2 = u2
        self.log_file = "./chat_log.txt"

    async def callback(self, interaction: discord.Interaction):
        for user in [self.u1, self.u2]:
            try:
                await self.thread.remove_user(user)
            except:
                pass
            # Re-add to queue with previous choice
            await self.matchmaker.add_to_queue(user, "any")  # or store last choice

        await interaction.response.send_message("✅ Users skipped. Re-added to the queue.", ephemeral=True)
        # Log skip
        with open(self.log_file, "a") as f:
            f.write(f"{self.u1.display_name} skipped\n")
            f.write(f"{self.u2.display_name} skipped\n")

        # Upload log
        log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            await log_channel.send(
                content=f"💬 Chat log for skipped thread {self.thread.name}:",
                file=discord.File(self.log_file)
            )

        # Delete thread
        try:
            await self.thread.delete()
        except:
            pass

        if os.path.exists(self.log_file):
            os.remove(self.log_file)