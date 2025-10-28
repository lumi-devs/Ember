import os
import json
import discord
from discord.ext import commands

DRAGME_CHANNEL_ID = 1418501302626816010
TIMEOUT_DURATION = 60  # seconds
REQUESTS_FILE = "active_requests.json"

BLACKLISTED_VOICE_IDS = set()
if os.environ.get("BLACKLISTED_VOICE_ROLES"):
    BLACKLISTED_VOICE_IDS = set(
        int(rid.strip())
        for rid in os.environ["BLACKLISTED_VOICE_ROLES"].split(",")
        if rid.strip()
    )


# --- Persistence helpers ---
def load_requests():
    if not os.path.exists(REQUESTS_FILE):
        return []
    try:
        with open(REQUESTS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return []


def save_requests(requests):
    with open(REQUESTS_FILE, "w") as f:
        json.dump(requests, f)


active_requests = []  # Always start fresh


async def setup(bot: commands.Bot):
    @bot.tree.command(
        name="dragme",
        description="Request to be dragged into another user's VC"
    )
    @discord.app_commands.describe(user="The user whose VC you want to join")
    async def dragme(interaction: discord.Interaction, user: discord.Member):
        await interaction.response.defer(ephemeral=True)
        requester = interaction.user
        target = user

        # --- Checks ---
        if any(role.id in BLACKLISTED_VOICE_IDS for role in requester.roles):
            return await interaction.followup.send(
                "❌ You are not allowed to use this command.", ephemeral=True
            )

        if interaction.channel_id != DRAGME_CHANNEL_ID:
            return await interaction.followup.send(
                f"You can only use this command in <#{DRAGME_CHANNEL_ID}>", ephemeral=True
            )

        if not requester.voice or not requester.voice.channel:
            return await interaction.followup.send(
                "❌ You must join a voice channel first.", ephemeral=True
            )

        if not target.voice or not target.voice.channel:
            return await interaction.followup.send(
                f"❌ {target.mention} must be in a voice channel.", ephemeral=True
            )

        if requester.voice.channel == target.voice.channel:
            return await interaction.followup.send(
                f"⚠️ You’re already in {target.mention}'s VC.", ephemeral=True
            )

        # --- Check VC user limit (allow up to limit × 2) ---
        channel = target.voice.channel
        limit = channel.user_limit  # 0 = unlimited
        current_users = len(channel.members)

        if limit > 0 and current_users >= (limit * 2):
            return await interaction.followup.send(
                f"⚠️ VC limit reached.\n"
                f"This channel allows up to {limit * 2} users (2× the normal limit).",
                ephemeral=True
            )

        request_key = {"requester": requester.id, "target": target.id}
        if request_key in active_requests:
            return await interaction.followup.send(
                f"⚠️ You already have an active request pending with {target.mention}",
                ephemeral=True,
            )

        # --- Add request ---
        active_requests.append(request_key)
        save_requests(active_requests)

        await interaction.followup.send("✅ Your request was sent.", ephemeral=True)

        msg_text = (
            f"🔔 {target.mention}, {requester.mention} wants to join your VC — **{channel.name}**. Accept?"
        )

        class DragmeView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=TIMEOUT_DURATION)
                self.msg: discord.Message | None = None
                self.finished = False

            def remove_request(self):
                nonlocal request_key
                if request_key in active_requests:
                    active_requests.remove(request_key)
                    save_requests(active_requests)

            async def finish(self, content: str):
                self.finished = True
                self.remove_request()
                if self.msg:
                    await self.msg.edit(content=content, view=None)
                self.stop()

            async def on_timeout(self):
                if not self.finished and self.msg:
                    self.remove_request()
                    await self.msg.edit(content="⌛ Request timed out.", view=None)

            @discord.ui.button(style=discord.ButtonStyle.green, label="Accept")
            async def accept_button(
                self, interaction: discord.Interaction, button: discord.ui.Button
            ):
                if interaction.user.id != target.id:
                    return await interaction.response.send_message("Not your request!", ephemeral=True)

                updated_channel = target.voice.channel
                if updated_channel and updated_channel.user_limit > 0:
                    if len(updated_channel.members) >= (updated_channel.user_limit * 2):
                        return await interaction.response.send_message(
                            f"⚠️ VC limit reached.\n"
                            f"This channel allows up to {updated_channel.user_limit * 2} users (2× the normal limit).",
                            ephemeral=True
                        )

                await requester.move_to(updated_channel)
                await self.finish(
                    f"✅ {target.mention} accepted. {requester.mention} has been moved to **{updated_channel.name}**"
                )

            @discord.ui.button(style=discord.ButtonStyle.red, label="Decline")
            async def decline_button(
                self, interaction: discord.Interaction, button: discord.ui.Button
            ):
                if interaction.user.id != target.id:
                    return await interaction.response.send_message("Not your request!", ephemeral=True)

                await self.finish(f"❌ {target.mention} declined {requester.mention}'s request")

        view = DragmeView()
        dragme_channel = bot.get_channel(DRAGME_CHANNEL_ID)
        view.msg = await dragme_channel.send(msg_text, view=view)
