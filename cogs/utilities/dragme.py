import os
import json
import discord
from discord.ext import commands
import asyncio

DRAGME_CHANNEL_ID = 1418501302626816010
TIMEOUT_DURATION = 60  # seconds
REQUESTS_FILE = "active_requests.json"

BLACKLISTED_VOICE_IDS = set()
if os.environ.get("BLACKLISTED_VOICE_ROLES"):
    BLACKLISTED_VOICE_IDS = {
        int(rid.strip())
        for rid in os.environ["BLACKLISTED_VOICE_ROLES"].split(",")
        if rid.strip()
    }

# --- Persistence helpers ---
def load_requests():
    if not os.path.exists(REQUESTS_FILE):
        return []
    try:
        with open(REQUESTS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return []

def save_requests_safe(requests):
    """Atomic save to prevent partial writes."""
    tmp = REQUESTS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(requests, f)
    os.replace(tmp, REQUESTS_FILE)


# ─── Global state ───────────────────────────────────────
active_requests = load_requests()
requests_lock = asyncio.Lock()  # prevent race conditions


async def setup(bot: commands.Bot):
    # Add cleanup task for orphaned requests - FIXED VERSION
    async def cleanup_orphaned_requests():
        """Fixed: Wait for bot to be ready and use proper guild access."""
        await bot.wait_until_ready()  # CRITICAL: Wait for bot to be ready
        
        while not bot.is_closed():
            try:
                # Use the first available guild instead of hardcoded ID
                guild = None
                for g in bot.guilds:
                    guild = g
                    break
                
                if not guild:
                    await asyncio.sleep(60)
                    continue
                
                async with requests_lock:
                    updated_requests = []
                    for request in active_requests[:]:
                        requester_id = request["requester"]
                        target_id = request["target"]
                        
                        requester = guild.get_member(requester_id)
                        target = guild.get_member(target_id)
                        
                        # Remove request if either user left the server or is not in VC anymore
                        if (not requester or not target or 
                            not requester.voice or not target.voice or
                            requester.voice.channel == target.voice.channel):
                            continue
                            
                        updated_requests.append(request)
                    
                    if len(updated_requests) != len(active_requests):
                        active_requests.clear()
                        active_requests.extend(updated_requests)
                        save_requests_safe(active_requests)
                        print(f"[DragMe] Cleaned up {len(active_requests) - len(updated_requests)} orphaned requests")
                
                await asyncio.sleep(300)  # Check every 5 minutes
                
            except Exception as e:
                print(f"[DragMe] Cleanup error: {e}")
                await asyncio.sleep(60)

    # Start the cleanup task properly
    bot.loop.create_task(cleanup_orphaned_requests())

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
        if requester.id == target.id:
            return await interaction.followup.send(
                "❌ You cannot drag yourself!", ephemeral=True
            )

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
                f"⚠️ You're already in {target.mention}'s VC.", ephemeral=True
            )

        # --- VC limit check (allow up to limit × 2) ---
        target_channel = target.voice.channel
        limit = target_channel.user_limit
        current_users = len(target_channel.members)
        if limit > 0 and current_users >= (limit * 2):
            return await interaction.followup.send(
                f"⚠️ VC limit reached. (Max {limit * 2})",
                ephemeral=True
            )

        request_key = {"requester": requester.id, "target": target.id}

        async with requests_lock:
            # Check for existing requests in both directions
            existing_request = next((r for r in active_requests 
                                   if (r["requester"] == requester.id and r["target"] == target.id) or
                                      (r["requester"] == target.id and r["target"] == requester.id)), None)
            
            if existing_request:
                return await interaction.followup.send(
                    f"⚠️ There's already an active request between you and {target.mention}",
                    ephemeral=True,
                )
            
            active_requests.append(request_key)
            save_requests_safe(active_requests)

        await interaction.followup.send("✅ Your request was sent.", ephemeral=True)

        # Create embed for better visual
        embed = discord.Embed(
            title="🎯 VC Join Request",
            description=f"{requester.mention} wants to join your VC:- **{target_channel.name}**",
            color=discord.Color.orange()
        )
        embed.set_footer(text=f"Request expires in {TIMEOUT_DURATION} seconds")

        class DragmeView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=TIMEOUT_DURATION)
                self.msg: discord.Message | None = None
                self.finished = False

            async def remove_request(self):
                async with requests_lock:
                    if request_key in active_requests:
                        active_requests.remove(request_key)
                        save_requests_safe(active_requests)

            async def finish(self, content: str, color=discord.Color.green()):
                self.finished = True
                await self.remove_request()
                if self.msg:
                    embed = discord.Embed(
                        description=content,
                        color=color
                    )
                    await self.msg.edit(embed=embed, view=None)
                self.stop()

            async def on_timeout(self):
                if not self.finished and self.msg:
                    await self.remove_request()
                    embed = discord.Embed(
                        description="⌛ Request timed out.",
                        color=discord.Color.red()
                    )
                    await self.msg.edit(embed=embed, view=None)

            @discord.ui.button(style=discord.ButtonStyle.green, label="✅ Accept", emoji="✅")
            async def accept_button(
                self, interaction: discord.Interaction, button: discord.ui.Button
            ):
                if interaction.user.id != target.id:
                    return await interaction.response.send_message("❌ This is not your request!", ephemeral=True)

                # Refresh channel info
                updated_target = interaction.guild.get_member(target.id)
                if not updated_target or not updated_target.voice or not updated_target.voice.channel:
                    return await interaction.response.send_message(
                        "❌ You are no longer in a voice channel.", ephemeral=True
                    )

                updated_channel = updated_target.voice.channel
                
                # Re-check limits only (allow private/locked VCs)
                limit = updated_channel.user_limit
                if limit > 0 and len(updated_channel.members) >= (limit * 2):
                    return await interaction.response.send_message(
                        f"⚠️ VC limit reached. (Max {limit * 2})", ephemeral=True
                    )

                try:
                    await requester.move_to(updated_channel)
                    await self.finish(
                        f"✅ {target.mention} accepted. {requester.mention} was moved to **{updated_channel.name}**",
                        discord.Color.green()
                    )
                except discord.Forbidden:
                    await self.finish(
                        f"❌ Missing permissions to move {requester.mention}",
                        discord.Color.red()
                    )
                except discord.HTTPException as e:
                    await self.finish(
                        f"❌ Failed to move user: {str(e)}",
                        discord.Color.red()
                    )

            @discord.ui.button(style=discord.ButtonStyle.red, label="❌ Decline", emoji="❌")
            async def decline_button(
                self, interaction: discord.Interaction, button: discord.ui.Button
            ):
                if interaction.user.id != target.id:
                    return await interaction.response.send_message("❌ This is not your request!", ephemeral=True)
                await self.finish(f"❌ {target.mention} declined {requester.mention}'s request", discord.Color.red())

        view = DragmeView()
        dragme_channel = bot.get_channel(DRAGME_CHANNEL_ID)
        if dragme_channel:
            view.msg = await dragme_channel.send(f"{target.mention}", embed=embed, view=view)

    # Add command to view active requests
    @bot.tree.command(
        name="active_requests",
        description="View all active dragme requests"
    )
    async def active_requests_cmd(interaction: discord.Interaction):
        if interaction.channel_id != DRAGME_CHANNEL_ID:
            return await interaction.response.send_message(
                f"This command can only be used in <#{DRAGME_CHANNEL_ID}>", ephemeral=True
            )

        async with requests_lock:
            if not active_requests:
                return await interaction.response.send_message(
                    "No active requests right now.", ephemeral=True
                )

            embed = discord.Embed(
                title="📋 Active DragMe Requests",
                color=discord.Color.blue()
            )
            
            for request in active_requests:
                requester = interaction.guild.get_member(request["requester"])
                target = interaction.guild.get_member(request["target"])
                
                if requester and target:
                    embed.add_field(
                        name=f"{requester.display_name} → {target.display_name}",
                        value=f"Requester: {requester.mention}\nTarget: {target.mention}",
                        inline=True
                    )
            
            await interaction.response.send_message(embed=embed, ephemeral=True)