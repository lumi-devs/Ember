import os
import json
import discord
from discord.ext import commands
import asyncio
import re
import time

TIMEOUT_DURATION = 60  # seconds
REQUESTS_FILE = "active_requests.json"
MESSAGE_CLEANUP_DELAY = 5  # seconds - how long to wait before deleting user messages

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

    if content.strip().isdigit():
        return int(content.strip())

    return None

async def delete_message_after(message, delay: int):
    """Delete a message after specified delay"""
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except (discord.NotFound, discord.Forbidden):
        pass
    except Exception:
        pass

async def setup(bot: commands.Bot):
    # Load environment variables HERE after dotenv has run in main.py
    DRAGME_CHANNEL_ID = os.environ.get("DRAGME_CHANNEL_ID")
    if not DRAGME_CHANNEL_ID:
        print("⚠️ WARNING: DRAGME_CHANNEL_ID not set in environment variables!")
        DRAGME_CHANNEL_ID = None
    else:
        try:
            DRAGME_CHANNEL_ID = int(DRAGME_CHANNEL_ID)
        except ValueError:
            print(f"⚠️ WARNING: DRAGME_CHANNEL_ID must be an integer, got: {DRAGME_CHANNEL_ID}")
            DRAGME_CHANNEL_ID = None

    # Load blacklisted roles here
    BLACKLISTED_VOICE_IDS = set()
    if os.environ.get("BLACKLISTED_VOICE_ROLES"):
        try:
            BLACKLISTED_VOICE_IDS = {
                int(rid.strip())
                for rid in os.environ["BLACKLISTED_VOICE_ROLES"].split(",")
                if rid.strip()
            }
        except ValueError as e:
            print(f"⚠️ WARNING: Error parsing BLACKLISTED_VOICE_ROLES: {e}")

    # Store in bot instance so other cogs can access if needed
    bot.dragme_config = {
        "channel_id": DRAGME_CHANNEL_ID,
        "blacklisted_roles": BLACKLISTED_VOICE_IDS
    }

    # Channel cleanup task - periodically clean old user messages
    async def channel_cleanup_task():
        await bot.wait_until_ready()
        
        while not bot.is_closed():
            try:
                if DRAGME_CHANNEL_ID:
                    channel = bot.get_channel(DRAGME_CHANNEL_ID)
                    if channel:
                        # Delete user messages older than 1 minute
                        async for message in channel.history(limit=100):
                            if not message.author.bot:  # Only delete user messages
                                # Check if message is older than 1 minute
                                if (discord.utils.utcnow() - message.created_at).total_seconds() > 60:
                                    try:
                                        await message.delete()
                                    except:
                                        pass
            except Exception:
                pass
            
            # Run cleanup every 5 minutes
            await asyncio.sleep(300)
    
    bot.loop.create_task(channel_cleanup_task())

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

    # Use listener with high priority instead of event
    @bot.listen('on_message')
    async def dragme_on_message(message):
        # Check if DRAGME_CHANNEL_ID is set
        if not DRAGME_CHANNEL_ID:
            return
            
        if message.channel.id != DRAGME_CHANNEL_ID:
            return

        # ALWAYS process dragme channel messages regardless of other cogs
        # Delete user messages after a short delay (keep bot messages)
        if not message.author.bot:
            # Schedule deletion of user message
            asyncio.create_task(delete_message_after(message, MESSAGE_CLEANUP_DELAY))

            # Process the drag request from user message
            user_id = extract_user_id(message.content)

            if not user_id:
                # Send error message (will be auto-deleted after 8 seconds)
                await message.channel.send(
                    f"{message.author.mention} ❌ Please mention a user or provide a user ID!\n"
                    f"Examples: `@username` or `<@123456789>` or `123456789`",
                    delete_after=8,
                )
                return

            target = message.guild.get_member(user_id)

            if not target:
                await message.channel.send(
                    f"{message.author.mention} ❌ Could not find that user in this server!",
                    delete_after=8,
                )
                return

            requester = message.author

            # Process the drag request
            await process_drag_request(message.channel, requester, target, BLACKLISTED_VOICE_IDS, DRAGME_CHANNEL_ID)

    # Keep the original event as backup but with proper returns
    @bot.event
    async def on_message(message):
        # Let other cogs process messages first
        # This will be overridden by bot.process_commands anyway
        pass

    async def process_drag_request(channel, requester, target, BLACKLISTED_VOICE_IDS, DRAGME_CHANNEL_ID):
        # Helper function to send and track messages for auto-deletion (only for response messages)
        async def send_temp_message(content=None, embed=None, delete_after=10):
            msg = await channel.send(content=content, embed=embed, delete_after=delete_after)
            return msg

        if requester.id == target.id:
            return await send_temp_message(
                f"{requester.mention} ❌ You cannot drag yourself!", 
                delete_after=10
            )

        if any(role.id in BLACKLISTED_VOICE_IDS for role in requester.roles):
            return await send_temp_message(
                f"{requester.mention} ❌ You are not allowed to use this command.",
                delete_after=10,
            )

        if not requester.voice or not requester.voice.channel:
            return await send_temp_message(
                f"{requester.mention} ❌ You must join a voice channel first.",
                delete_after=10,
            )

        if not target.voice or not target.voice.channel:
            return await send_temp_message(
                f"{requester.mention} ❌ {target.mention} must be in a voice channel.",
                delete_after=10,
            )

        if requester.voice.channel == target.voice.channel:
            return await send_temp_message(
                f"{requester.mention} ⚠️ You're already in {target.mention}'s VC.",
                delete_after=10,
            )

        async with requests_lock:
            user_pending = [
                r
                for r in active_requests
                if r["requester"] == requester.id or r["target"] == requester.id
            ]

            if user_pending:
                return await send_temp_message(
                    f"{requester.mention} ❌ You already have a pending request! Please wait for it to complete.",
                    delete_after=10,
                )

        target_channel = target.voice.channel

        request_key = {"requester": requester.id, "target": target.id}

        async with requests_lock:
            existing = next(
                (
                    r
                    for r in active_requests
                    if (r["requester"] == requester.id and r["target"] == target.id)
                    or (r["requester"] == target.id and r["target"] == requester.id)
                ),
                None,
            )

            if existing:
                return await send_temp_message(
                    f"{requester.mention} ⚠️ There's already an active request between you and {target.mention}",
                    delete_after=10,
                )

            active_requests.append(request_key)
            save_requests_safe(active_requests)

        # Send confirmation (will auto-delete after 10 seconds)
        await send_temp_message(
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
                        if request_key in active_requests:
                            active_requests.remove(request_key)
                            save_requests_safe(active_requests)

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
                        log_msg = await dragme_channel.send(embed=log_embed)
                        # Schedule deletion of log message after 30 seconds (keep logs clean)
                        asyncio.create_task(delete_message_after(log_msg, 30))

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
                            log_msg = await dragme_channel.send(embed=embed)
                            asyncio.create_task(delete_message_after(log_msg, 30))

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
                                    except Exception:
                                        pass

                                asyncio.create_task(remove_perms_when_done())

                                success_msg = f"✅ {target.mention} accepted. {requester.mention} was moved to **{updated_channel.name}**"
                            except Exception:
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

                    await self.finish(
                        f"❌ {target.mention} declined {requester.mention}'s request",
                        discord.Color.red(),
                    )

            view = DragmeView()

            try:
                # Send the request message in target's VC (keep this, it's a bot message)
                view.msg = await target_channel.send(
                    f"{target.mention}", embed=vc_embed, view=view
                )
                
            except (discord.Forbidden, discord.HTTPException):
                # Fallback to dragme channel if can't send in VC
                dragme_channel = bot.get_channel(DRAGME_CHANNEL_ID)
                if dragme_channel:
                    fallback_embed = discord.Embed(
                        title="🔔 New DragMe Request (Hidden VC)",
                        description=f"{requester.mention} wants to join {target.mention}'s hidden voice channel!",
                        color=discord.Color.purple(),
                    )
                    fallback_embed.add_field(
                        name="Target",
                        value=f"{target.name}\n{target.mention}",
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
            async with requests_lock:
                if request_key in active_requests:
                    active_requests.remove(request_key)
                    save_requests_safe(active_requests)
            print(f"Error in drag request: {e}")

    @bot.tree.command(
        name="active_requests", description="View all active dragme requests"
    )
    @commands.has_permissions(manage_guild=True)
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

    # Optional: Add a command to manually clean user messages
    @bot.tree.command(
        name="clean_dragme", description="Clean all user messages in the dragme channel"
    )
    @commands.has_permissions(manage_messages=True)
    async def clean_dragme(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        if not DRAGME_CHANNEL_ID:
            return await interaction.followup.send("❌ DragMe channel not configured!", ephemeral=True)
        
        channel = bot.get_channel(DRAGME_CHANNEL_ID)
        if not channel:
            return await interaction.followup.send("❌ DragMe channel not found!", ephemeral=True)
        
        deleted = 0
        async for message in channel.history(limit=100):
            if not message.author.bot:  # Only delete user messages
                try:
                    await message.delete()
                    deleted += 1
                    await asyncio.sleep(0.5)  # Small delay to avoid rate limits
                except:
                    pass
        
        await interaction.followup.send(f"✅ Cleaned {deleted} user messages from {channel.mention}", ephemeral=True)