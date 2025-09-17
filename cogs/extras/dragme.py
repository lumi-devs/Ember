import discord
from discord.ext import commands

# Track active requests (requester_id, target_id)
active_requests = set()

async def setup(bot: commands.Bot):
    @bot.tree.command(name="dragme", description="Request to be dragged into another user's VC")
    @discord.app_commands.describe(user="The user whose VC you want to join")
    async def dragme(interaction: discord.Interaction, user: discord.Member):
        requester = interaction.user
        target = user

        if not target.voice or not target.voice.channel:
            return await interaction.response.send_message(
                f"❌ {target.mention} must be in a voice channel.", ephemeral=True
            )

        if requester.voice and requester.voice.channel == target.voice.channel:
            return await interaction.response.send_message(
                f"⚠️ You’re already in {target.mention}'s VC.", ephemeral=True
            )

        request_key = (requester.id, target.id)
        if request_key in active_requests:
            return await interaction.response.send_message(
                f"⚠️ You already have an active request pending with {target.mention}.",
                ephemeral=True
            )

        active_requests.add(request_key)

        channel = target.voice.channel
        msg_text = f"🔔 {target.mention}, {requester.mention} wants to join your VC (**{channel.name}**). Accept?"

        view = discord.ui.View(timeout=180)  # 3 minutes

        async def cleanup(msg, new_content):
            if request_key in active_requests:
                active_requests.remove(request_key)
            await msg.edit(content=new_content, view=None)

        async def accept_callback(i: discord.Interaction):
            if i.user.id != target.id:
                return await i.response.send_message("Not your request!", ephemeral=True)

            await requester.move_to(channel)
            await cleanup(msg, f"✅ {target.mention} accepted. {requester.mention} has been moved to **{channel.name}**.")
            view.stop()

        async def decline_callback(i: discord.Interaction):
            if i.user.id != target.id:
                return await i.response.send_message("Not your request!", ephemeral=True)

            await cleanup(msg, f"❌ {target.mention} declined {requester.mention}'s request.")
            view.stop()

        accept = discord.ui.Button(style=discord.ButtonStyle.green, label="Accept")
        decline = discord.ui.Button(style=discord.ButtonStyle.red, label="Decline")

        accept.callback = accept_callback
        decline.callback = decline_callback

        view.add_item(accept)
        view.add_item(decline)

        await interaction.response.send_message(msg_text, view=view)
        msg = await interaction.original_response()

        async def on_timeout():
            if not view.is_finished():
                await cleanup(msg, "⌛ Request timed out.")

        view.on_timeout = on_timeout
