import os
import discord
from discord.ext import commands

active_requests = set()
DRAGME_CHANNEL_ID = 1418501302626816010
TIMEOUT_DURATION = 30  # seconds

BLACKLISTED_VOICE_IDS = set()
if os.environ.get("BLACKLISTED_VOICE_ROLES"):
    BLACKLISTED_VOICE_IDS = set(
        int(rid.strip())
        for rid in os.environ["BLACKLISTED_VOICE_ROLES"].split(",")
        if rid.strip()
    )


async def setup(bot: commands.Bot):
    @bot.tree.command(
        name="dragme",
        description="Request to be dragged into another user's VC"
    )
    @discord.app_commands.describe(user="The user whose VC you want to join")
    async def dragme(interaction: discord.Interaction, user: discord.Member):
        requester = interaction.user
        target = user

        # --- Checks ---
        if any(role.id in BLACKLISTED_VOICE_IDS for role in requester.roles):
            return await interaction.response.send_message(
                "❌ You are not allowed to use this command.", ephemeral=True
            )
        if interaction.channel_id != DRAGME_CHANNEL_ID:
            return await interaction.response.send_message(
                f"You can only use this command in <#{DRAGME_CHANNEL_ID}>", ephemeral=True
            )
        if not requester.voice or not requester.voice.channel:
            return await interaction.response.send_message(
                "❌ You must join a voice channel first.", ephemeral=True
            )
        if not target.voice or not target.voice.channel:
            return await interaction.response.send_message(
                f"❌ {target.mention} must be in a voice channel.", ephemeral=True
            )
        if requester.voice.channel == target.voice.channel:
            return await interaction.response.send_message(
                f"⚠️ You’re already in {target.mention}'s VC.", ephemeral=True
            )

        request_key = (requester.id, target.id)
        if request_key in active_requests:
            return await interaction.response.send_message(
                f"⚠️ You already have an active request pending with {target.mention}",
                ephemeral=True,
            )

        # --- Add request ---
        active_requests.add(request_key)

        # --- Ephemeral confirmation to requester ---
        await interaction.response.send_message(
            "✅ Your request was sent.", ephemeral=True
        )

        # --- Public message in dragme channel ---
        channel = target.voice.channel
        msg_text = f"🔔 {target.mention}, {requester.mention} wants to join your VC {channel.mention}. Accept?"

        class DragmeView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=TIMEOUT_DURATION)
                self.msg: discord.Message | None = None
                self.finished = False

            async def finish(self, content: str):
                self.finished = True
                active_requests.discard(request_key)
                if self.msg:
                    await self.msg.edit(content=content, view=None)
                self.stop()

            async def on_timeout(self):
                if not self.finished and self.msg:
                    active_requests.discard(request_key)
                    await self.msg.edit(content="⌛ Request timed out.", view=None)

            @discord.ui.button(style=discord.ButtonStyle.green, label="Accept")
            async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                if interaction.user.id != target.id:
                    return await interaction.response.send_message("Not your request!", ephemeral=True)

                await requester.move_to(channel)
                await self.finish(
                    f"✅ {target.mention} accepted. {requester.mention} has been moved to {channel.mention}"
                )

            @discord.ui.button(style=discord.ButtonStyle.red, label="Decline")
            async def decline_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                if interaction.user.id != target.id:
                    return await interaction.response.send_message("Not your request!", ephemeral=True)

                await self.finish(
                    f"❌ {target.mention} declined {requester.mention}'s request"
                )

        view = DragmeView()
        # send the public interactive message in the same channel
        dragme_channel = bot.get_channel(DRAGME_CHANNEL_ID)
        view.msg = await dragme_channel.send(msg_text, view=view)
