# cogs/omegle/omegle_ui.py
import discord
from discord.ui import View, Button, Select
from datetime import datetime
import os

# Note: do NOT import Matchmaker here at top-level to avoid circular issues in some setups.
# Gender select view - user chooses preference and gets added to queue.
class GenderSelectView(View):
    def __init__(self, matchmaker, user):
        super().__init__(timeout=900)
        self.matchmaker = matchmaker
        self.user = user
        options = [
            discord.SelectOption(label="Male", value="male"),
            discord.SelectOption(label="Female", value="female"),
            discord.SelectOption(label="Any", value="any")
        ]
        self.add_item(GenderSelect(options))

    async def add_to_queue_and_ack(self, interaction: discord.Interaction, choice: str):
        # Check existing
        existing = self.matchmaker.get_queue_entry(self.user.id)
        if existing:
            # already in queue - show leave/change view
            await interaction.followup.send(
                f"⚠️ You are already in queue (#{existing['serial']}). Choose Leave or Change Preference.",
                view=AlreadyInQueueView(self.matchmaker, self.user, existing['serial']),
                ephemeral=True
            )
            return

        added, serial = await self.matchmaker.add_user(self.user, choice)
        if not added:
            if serial is None:
                await interaction.followup.send(
                    "🚫 You must have a Male or Female role before joining the queue. Assign a role and try again.",
                    ephemeral=True
                )
            else:
                await interaction.followup.send("⚠️ Could not join queue.", ephemeral=True)
            return

        await interaction.followup.send(f"✅ You joined the queue at position #{serial}. Waiting for a match...", ephemeral=True)

class GenderSelect(Select):
    def __init__(self, options):
        super().__init__(placeholder="Choose your preference...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        choice = self.values[0]
        await self.view.add_to_queue_and_ack(interaction, choice)

# View shown if user is already in queue
class AlreadyInQueueView(View):
    def __init__(self, matchmaker, user, serial):
        super().__init__(timeout=900)
        self.matchmaker = matchmaker
        self.user = user
        self.serial = serial
        self.add_item(LeaveQueueButton(matchmaker, user))
        self.add_item(ChangePrefButton(matchmaker, user))

class LeaveQueueButton(Button):
    def __init__(self, matchmaker, user):
        super().__init__(label="Leave Queue", style=discord.ButtonStyle.red)
        self.matchmaker = matchmaker
        self.user = user

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        removed = await self.matchmaker.remove_user_by_id(self.user.id)
        if removed:
            await interaction.followup.send("✅ You left the queue.", ephemeral=True)
        else:
            await interaction.followup.send("❌ You were not in the queue.", ephemeral=True)

class ChangePrefButton(Button):
    def __init__(self, matchmaker, user):
        super().__init__(label="Change Preference", style=discord.ButtonStyle.primary)
        self.matchmaker = matchmaker
        self.user = user

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        view = GenderSelectView(self.matchmaker, self.user)
        await interaction.followup.send("Choose your new preference:", view=view, ephemeral=True)

# Persistent Start button (register via bot.add_view in on_ready, timeout=None)
class StartButton(Button):
    def __init__(self, matchmaker):
        super().__init__(label="Start Chat", style=discord.ButtonStyle.success, custom_id="omegle_start_v1")
        self.matchmaker = matchmaker

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        view = GenderSelectView(self.matchmaker, interaction.user)
        await interaction.followup.send("Choose your preference to join the queue:", view=view, ephemeral=True)

class StartButtonView(View):
    def __init__(self, matchmaker):
        super().__init__(timeout=None)
        self.add_item(StartButton(matchmaker))

# ---------------- Private chat view (one view per thread) ----------------
class PrivateChatView(View):
    def __init__(self, matchmaker, thread: discord.Thread, user1: discord.Member, user2: discord.Member, log_channel_id: int):
        super().__init__(timeout=None)
        self.matchmaker = matchmaker
        self.thread = thread
        self.user1 = user1
        self.user2 = user2
        self.log_channel_id = log_channel_id
        self.chat_log = []  # list of tuples (timestamp_str, username, content)
        self.users_left = set()  # track ids who have left
        # Buttons: Leave for skipper or both? We'll have Leave (any user presses => close thread, nobody requeue)
        # and Skip (requeue only skipper) — both appear once in the thread message.
        self.add_item(LeaveButton(self))
        self.add_item(SkipButton(self))

    async def export_log_and_cleanup(self):
        """
        Write chat_log to a .txt, upload to log channel, delete thread and do cleanup.
        """
        if not self.chat_log:
            # write a small note
            text = "(no messages captured)\n"
        else:
            text = ""
            for ts, name, content in self.chat_log:
                text += f"[{ts}] {name}: {content}\n"

        filename = f"omegle_chat_{self.thread.id}.txt"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(text)

        # upload to log channel
        guild = self.thread.guild
        log_channel = guild.get_channel(self.log_channel_id) if guild else None
        if log_channel:
            try:
                await log_channel.send(content=f"💬 Chat log for `{self.thread.name}` (id {self.thread.id}):", file=discord.File(filename))
            except Exception as e:
                print("[OMEGLE] Failed to upload log:", e)

        # remove file local copy
        try:
            os.remove(filename)
        except Exception:
            pass

        # delete thread
        try:
            await self.thread.delete()
        except Exception:
            pass

# Leave button: closes chat, uploads log, nobody requeued
class LeaveButton(Button):
    def __init__(self, view_ref: PrivateChatView):
        super().__init__(label="Leave Chat", style=discord.ButtonStyle.danger)
        self.view_ref = view_ref

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        user = interaction.user
        ts = datetime.utcnow().strftime("%H:%M:%S")
        # log that user left
        self.view_ref.chat_log.append((ts, user.display_name, "[left]"))
        # mark left
        self.view_ref.users_left.add(user.id)

        # export and cleanup regardless of who pressed - close the thread for both
        await self.view_ref.export_log_and_cleanup()

        # reply to skipper
        await interaction.followup.send("You left the chat. Thread closed and transcript uploaded.", ephemeral=True)

# Skip button: closes chat, uploads log, requeue only skipper (ask preference)
class SkipButton(Button):
    def __init__(self, view_ref: PrivateChatView):
        super().__init__(label="Skip & Rejoin (you only)", style=discord.ButtonStyle.secondary)
        self.view_ref = view_ref

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        skipper = interaction.user
        ts = datetime.utcnow().strftime("%H:%M:%S")
        self.view_ref.chat_log.append((ts, skipper.display_name, "[skipped & requeued]"))
        # export & cleanup
        await self.view_ref.export_log_and_cleanup()

        # re-queue skipper by letting them choose preference again
        # show GenderSelectView ephemeral to skipper
        view = GenderSelectView(self.view_ref.matchmaker, skipper)
        await interaction.followup.send("You chose to skip. Choose your preference to rejoin the queue:", view=view, ephemeral=True)
