import os
import json
import discord
from discord.ext import commands
import asyncio
import re
import time
from collections import defaultdict

DRAGME_CHANNEL_ID = os.environ.get("DRAGME_CHANNEL_ID")
TIMEOUT_DURATION = 60  # seconds
REQUESTS_FILE = "active_requests.json"

# Rate limiting
user_request_times = defaultdict(list)
RATE_LIMIT = 3  # requests per minute
RATE_WINDOW = 60  # seconds

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
    tmp = REQUESTS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(requests, f)
    os.replace(tmp, REQUESTS_FILE)


# Global state
active_requests = load_requests()
requests_lock = asyncio.Lock()


def extract_user_id(content):
    mention_pattern = r"<@!?(\d+)>"
    match = re.search(mention_pattern, content)
    if match:
        return int(match.group(1))

    # Try to find a standalone number (Discord IDs are 17-20 digits)
    numbers = re.findall(r"\b(\d{17,20})\b", content)
    if numbers:
        return int(numbers[0])

    return None


async def setup(bot: commands.Bot):
    # Cleanup task for orphaned requests
    async def cleanup_orphaned_requests():
        await bot.wait_until_ready()

        while not bot.is_closed():
            try:
                guild = bot.guilds[0] if bot.guilds else None
                if not guild:
                    await asyncio.sleep(60)
                    continue

                async with requests_lock:
                    updated_requests = []
                    for request in active_requests[:]:
                        requester = guild.get_member(request["requester"])
                        target = guild.get_member(request["target"])

                        if (
                            not requester
                            or not target
                            or not requester.voice
                            or not target.voice
                            or requester.voice.channel == target.voice.channel
                        ):
                            continue

                        updated_requests.append(request)

                    if len(updated_requests) != len(active_requests):
                        active_requests.clear()
                        active_requests.extend(updated_requests)
                        save_requests_safe(active_requests)

                await asyncio.sleep(300)

            except Exception:
                await asyncio.sleep(60)

    bot.loop.create_task(cleanup_orphaned_requests())

    @bot.event
    async def on_message(message):
        if message.author.bot or message.channel.id != DRAGME_CHANNEL_ID:
            return

        user_id = extract_user_id(message.content)

        if not user_id:
            try:
                await message.delete()
                await message.channel.send(
                    f"{message.author.mention} ❌ Please mention a user or provide a user ID!\n"
                    f"Examples: `@username` or `<@123456789>` or `123456789`",
                    delete_after=8,
                )
            except Exception as e:
                print(f"Failed to delete message or send response: {e}")
            return

        target = message.guild.get_member(user_id)

        if not target:
            try:
                await message.delete()
                await message.channel.send(
                    f"{message.author.mention} ❌ Could not find that user in this server!",
                    delete_after=8,
                )
            except Exception as e:
                print(f"Failed to delete message or send response: {e}")
            return

        requester = message.author

        try:
            await message.delete()
        except Exception as e:
            print(f"Failed to delete message: {e}")

        await process_drag_request(message.channel, requester, target)

    async def process_drag_request(channel, requester, target):
        # Rate limiting check
        now = time.time()
        user_times = user_request_times[requester.id]
        # Clean old entries
        user_times = [t for t in user_times if now - t < RATE_WINDOW]
        user_request_times[requester.id] = user_times
        
        if len(user_times) >= RATE_LIMIT:
            return await channel.send(
                f"{requester.mention} ❌ Too many requests. Please wait {RATE_WINDOW} seconds between requests.",
                delete_after=10
            )
        
        # Basic validation
        if requester.id == target.id:
            return await channel.send(
                f"{requester.mention} ❌ You cannot drag yourself!", delete_after=10
            )

        if any(role.id in BLACKLISTED_VOICE_IDS for role in requester.roles):
            return await channel.send(
                f"{requester.mention} ❌ You are not allowed to use this command.",
                delete_after=10,
            )

        if not requester.voice or not requester.voice.channel:
            return await channel.send(
                f"{requester.mention} ❌ You must join a voice channel first.",
                delete_after=10,
            )

        if not target.voice or not target.voice.channel:
            return await channel.send(
                f"{requester.mention} ❌ {target.mention} must be in a voice channel.",
                delete_after=10,
            )

        if requester.voice.channel == target.voice.channel:
            return await channel.send(
                f"{requester.mention} ⚠️ You're already in {target.mention}'s VC.",
                delete_after=10,
            )

        # Check if target's VC is full
        target_channel = target.voice.channel
        if len(target_channel.members) >= target_channel.user_limit > 0:
            return await channel.send(
                f"{requester.mention} ❌ {target.mention}'s VC is full.",
                delete_after=10
            )

        # ATOMIC: All checks and request creation inside lock
        async with requests_lock:
            # Check if requester already has any pending request
            if any(r["requester"] == requester.id or r["target"] == requester.id for r in active_requests):
                return await channel.send(
                    f"{requester.mention} ❌ You already have a pending request! Please wait for it to complete.",
                    delete_after=10,
                )

            # Check if target already has any pending request
            if any(r["requester"] == target.id or r["target"] == target.id for r in active_requests):
                return await channel.send(
                    f"{requester.mention} ❌ {target.mention} already has a pending request! Please wait for it to complete.",
                    delete_after=10,
                )

            # Check if there's already a request between these two users
            if any(
                (r["requester"] == requester.id and r["target"] == target.id) or
                (r["requester"] == target.id and r["target"] == requester.id)
                for r in active_requests
            ):
                return await channel.send(
                    f"{requester.mention} ⚠️ There's already an active request between you and {target.mention}",
                    delete_after=10,
                )

            # Create the request
            request_key = {"requester": requester.id, "target": target.id}
            active_requests.append(request_key)
            save_requests_safe(active_requests)
            
            # Add to rate limit after successful request
            user_request_times[requester.id].append(now)

        await channel.send(
            f"✅ {requester.mention} Your request was sent to {target.mention}",
            delete_after=10,
        )

        is_hidden = not target_channel.permissions_for(
            target.guild.default_role
        ).connect

        try:
            vc_embed = discord.Embed(
                title="🔔 New DragMe Request!",
                description=f"{requester.mention} wants to join your voice channel!",
                color=discord.Color.gold(),
            )
            vc_embed.add_field(
                name="Requester",
                value=f"{requester.name}\n{requester.mention}",
                inline=True,
            )
            vc_embed.add_field(
                name="Their VC", value=requester.voice.channel.name, inline=True
            )
            vc_embed.add_field(name="Your VC", value=target_channel.name, inline=True)
            vc_embed.add_field(
                name="Current VC Users",
                value=f"{len(target_channel.members)}/{target_channel.user_limit if target_channel.user_limit > 0 else '∞'}",
                inline=True,
            )
            vc_embed.set_footer(
                text=f"Use the buttons below to respond • Expires in {TIMEOUT_DURATION}s"
            )

            class DragmeView(discord.ui.View):
                def __init__(self):
                    super().__init__(timeout=TIMEOUT_DURATION)
                    self.msg: discord.Message | None = None
                    self.finished = False

                async def remove_request(self):
                    async with requests_lock:
                        # Find and remove the request
                        for i, r in enumerate(active_requests):
                            if r["requester"] == requester.id and r["target"] == target.id:
                                active_requests.pop(i)
                                save_requests_safe(active_requests)
                                break

                async def finish(self, content: str, color=discord.Color.green()):
                    self.finished = True
                    await self.remove_request()

                    log_embed = discord.Embed(
                        description=content,
                        color=color,
                        timestamp=discord.utils.utcnow(),
                    )
                    log_embed.set_author(
                        name="DragMe Request", icon_url=requester.display_avatar.url
                    )

                    dragme_channel = bot.get_channel(DRAGME_CHANNEL_ID)
                    if dragme_channel:
                        await dragme_channel.send(embed=log_embed)

                    if self.msg:
                        await self.msg.edit(embed=log_embed, view=None)
                    self.stop()

                async def on_timeout(self):
                    if not self.finished and self.msg:
                        await self.remove_request()
                        embed = discord.Embed(
                            description=f"⌛ Request from {requester.mention} to {target.mention} timed out.",
                            color=discord.Color.red(),
                            timestamp=discord.utils.utcnow(),
                        )
                        embed.set_author(
                            name="DragMe Request Timed Out",
                            icon_url=requester.display_avatar.url,
                        )

                        dragme_channel = bot.get_channel(DRAGME_CHANNEL_ID)
                        if dragme_channel:
                            await dragme_channel.send(embed=embed)

                        if self.msg:
                            await self.msg.edit(embed=embed, view=None)

                @discord.ui.button(style=discord.ButtonStyle.green, label="✅ Accept")
                async def accept_button(
                    self, interaction: discord.Interaction, button: discord.ui.Button
                ):
                    if interaction.user.id != target.id:
                        return await interaction.response.send_message(
                            "❌ This is not your request!", ephemeral=True
                        )

                    # Check bot permissions
                    if not interaction.guild.me.guild_permissions.move_members:
                        return await interaction.response.send_message(
                            "❌ Bot lacks permission to move members.", ephemeral=True
                        )

                    updated_target = interaction.guild.get_member(target.id)
                    if (
                        not updated_target
                        or not updated_target.voice
                        or not updated_target.voice.channel
                    ):
                        return await interaction.response.send_message(
                            "❌ You are no longer in a voice channel.", ephemeral=True
                        )

                    updated_channel = updated_target.voice.channel

                    # Check if channel is still not full
                    if len(updated_channel.members) >= updated_channel.user_limit > 0:
                        return await interaction.response.send_message(
                            "❌ Your VC is now full.", ephemeral=True
                        )

                    await interaction.response.defer()

                    is_hidden = not updated_channel.permissions_for(
                        interaction.guild.default_role
                    ).connect

                    try:
                        await requester.move_to(updated_channel)

                        if is_hidden:
                            try:
                                await updated_channel.set_permissions(
                                    requester,
                                    connect=True,
                                    speak=True,
                                    reason=f"Temporary access granted via dragme request from {target.display_name}",
                                )

                                async def remove_perms_when_done():
                                    await asyncio.sleep(300)
                                    try:
                                        current_member = interaction.guild.get_member(
                                            requester.id
                                        )
                                        if (
                                            current_member
                                            and current_member.voice
                                            and current_member.voice.channel
                                            == updated_channel
                                        ):
                                            return
                                        await updated_channel.set_permissions(
                                            requester, overwrite=None
                                        )
                                    except Exception as e:
                                        print(f"Failed to remove permissions: {e}")

                                asyncio.create_task(remove_perms_when_done())

                                success_msg = f"✅ {target.mention} accepted. {requester.mention} was moved to **{updated_channel.name}**"
                            except Exception as e:
                                print(f"Failed to set permissions: {e}")
                                success_msg = f"✅ {target.mention} accepted. {requester.mention} was moved to **{updated_channel.name}**"
                        else:
                            success_msg = f"✅ {target.mention} accepted. {requester.mention} was moved to **{updated_channel.name}**"

                        await self.finish(success_msg, discord.Color.green())

                    except discord.Forbidden:
                        await self.finish(
                            f"❌ Missing permissions to move {requester.mention}",
                            discord.Color.red(),
                        )
                    except discord.HTTPException as e:
                        await self.finish(
                            f"❌ Failed to move user: {str(e)}",
                            discord.Color.red(),
                        )

                @discord.ui.button(style=discord.ButtonStyle.red, label="❌ Decline")
                async def decline_button(
                    self, interaction: discord.Interaction, button: discord.ui.Button
                ):
                    if interaction.user.id != target.id:
                        return await interaction.response.send_message(
                            "❌ This is not your request!", ephemeral=True
                        )

                    await interaction.response.defer()
                    await self.finish(
                        f"❌ {target.mention} declined {requester.mention}'s request",
                        discord.Color.red(),
                    )

            view = DragmeView()

            try:
                view.msg = await target_channel.send(
                    f"{target.mention}", embed=vc_embed, view=view
                )
            except (discord.Forbidden, discord.HTTPException) as e:
                print(f"Failed to send to VC: {e}")
                dragme_channel = bot.get_channel(DRAGME_CHANNEL_ID)
                if dragme_channel:
                    fallback_embed = discord.Embed(
                        title="🔔 New DragMe Request (Hidden VC)",
                        description=f"{requester.mention} wants to join {target.mention}'s hidden voice channel!",
                        color=discord.Color.purple(),
                    )
                    fallback_embed.add_field(
                        name="Requester",
                        value=f"{requester.name}\n{requester.mention}",
                        inline=True,
                    )
                    fallback_embed.add_field(
                        name="Target",
                        value=f"{target.name}\n{target.mention}",
                        inline=True,
                    )
                    fallback_embed.add_field(
                        name="Their VC", value=requester.voice.channel.name, inline=True
                    )
                    fallback_embed.add_field(
                        name="Target VC", value="🔒 Hidden Voice Channel", inline=True
                    )
                    fallback_embed.add_field(
                        name="Current VC Users",
                        value=f"{len(target_channel.members)}/{target_channel.user_limit if target_channel.user_limit > 0 else '∞'}",
                        inline=True,
                    )
                    fallback_embed.set_footer(
                        text=f"Request expires in {TIMEOUT_DURATION} seconds • Target is in hidden VC"
                    )

                    view = DragmeView()
                    view.msg = await dragme_channel.send(
                        f"{target.mention}", embed=fallback_embed, view=view
                    )

        except Exception as e:
            print(f"Unexpected error in request: {e}")
            async with requests_lock:
                # Find and remove the request
                for i, r in enumerate(active_requests):
                    if r["requester"] == requester.id and r["target"] == target.id:
                        active_requests.pop(i)
                        save_requests_safe(active_requests)
                        break

    @bot.tree.command(
        name="active_requests", description="View all active dragme requests"
    )
    async def active_requests_cmd(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        async with requests_lock:
            if not active_requests:
                return await interaction.followup.send(
                    "No active requests right now.", ephemeral=True
                )

            embed = discord.Embed(
                title="📋 Active DragMe Requests", color=discord.Color.blue()
            )

            for request in active_requests:
                requester = interaction.guild.get_member(request["requester"])
                target = interaction.guild.get_member(request["target"])

                if requester and target:
                    embed.add_field(
                        name=f"{requester.display_name} → {target.display_name}",
                        value=f"Requester: {requester.mention}\nTarget: {target.mention}",
                        inline=True,
                    )

            await interaction.followup.send(embed=embed, ephemeral=True)