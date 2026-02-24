import os
import json
import discord
from discord.ext import commands
import asyncio
import re

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
    # Add cleanup task for orphaned requests
    async def cleanup_orphaned_requests():
        """Wait for bot to be ready and use proper guild access."""
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

    # Get debug channel
    debug_channel = None

    # Function to send debug messages
    async def send_debug(message_content):
        nonlocal debug_channel
        if not debug_channel:
            debug_channel = bot.get_channel(DRAGME_CHANNEL_ID)
        if debug_channel:
            try:
                debug_embed = discord.Embed(
                    title="🔍 Debug Info",
                    description=message_content,
                    color=discord.Color.greyple(),
                    timestamp=discord.utils.utcnow()
                )
                await debug_channel.send(embed=debug_embed)
            except:
                pass

    # Function to extract user ID from various mention formats
    def extract_user_id(content):
        # Pattern for <@USER_ID> or <@!USER_ID> (nickname mention)
        mention_pattern = r'<@!?(\d+)>'
        match = re.search(mention_pattern, content)
        if match:
            return int(match.group(1))
        
        # Check if it's just a raw ID
        if content.strip().isdigit():
            return int(content.strip())
        
        return None

    # Message handler for dragme channel
    @bot.event
    async def on_message(message):
        # Ignore bot messages
        print(message)
        if message.author.bot:
            return
        
        # Only process messages in the dragme channel
        if message.channel.id != DRAGME_CHANNEL_ID:
            return
        
        # Send debug info about the message received
        debug_info = f"**Message received from:** {message.author.mention} (`{message.author.id}`)\n"
        debug_info += f"**Content:** `{message.content}`\n"
        debug_info += f"**Raw content:** `{message.content}`\n"
        debug_info += f"**Mentions list:** {[m.mention for m in message.mentions]}\n"
        debug_info += f"**Raw mentions:** {message.raw_mentions}\n"
        
        await send_debug(debug_info)
        
        # Try to extract user ID from message content
        user_id = extract_user_id(message.content)
        
        debug_info = f"**Extracted user_id:** `{user_id}`\n"
        
        if not user_id:
            # If no valid mention/ID found, delete message and notify
            debug_info += "❌ No valid user ID found in message\n"
            await send_debug(debug_info)
            
            try:
                await message.delete()
                warning = await message.channel.send(
                    f"{message.author.mention} ❌ Please mention a user or provide a user ID!\n"
                    f"Examples: `@username` or `<@123456789>` or `123456789`",
                    delete_after=8
                )
            except Exception as e:
                debug_info += f"❌ Error deleting message: {str(e)}\n"
                await send_debug(debug_info)
            return
        
        # Get the target member
        target = message.guild.get_member(user_id)
        
        debug_info += f"**Target found:** {target.mention if target else 'None'} (ID: {user_id})\n"
        
        if not target:
            # If user not found in server
            debug_info += "❌ User not found in server\n"
            await send_debug(debug_info)
            
            try:
                await message.delete()
                warning = await message.channel.send(
                    f"{message.author.mention} ❌ Could not find that user in this server!",
                    delete_after=8
                )
            except Exception as e:
                debug_info += f"❌ Error deleting message: {str(e)}\n"
                await send_debug(debug_info)
            return
        
        requester = message.author
        
        debug_info += f"**Requester:** {requester.mention} (`{requester.id}`)\n"
        debug_info += f"**Target:** {target.mention} (`{target.id}`)\n"
        await send_debug(debug_info)
        
        # Delete the original message
        try:
            await message.delete()
            debug_info = f"✅ Original message deleted successfully\n"
            await send_debug(debug_info)
        except Exception as e:
            debug_info = f"❌ Error deleting message: {str(e)}\n"
            await send_debug(debug_info)
        
        # Process the drag request
        await process_drag_request(message.channel, requester, target)

    async def process_drag_request(channel, requester, target):
        """Process the drag request from message"""
        
        debug_info = f"**Processing drag request**\n"
        debug_info += f"Requester: {requester.mention} (`{requester.id}`)\n"
        debug_info += f"Target: {target.mention} (`{target.id}`)\n"
        
        # --- Checks ---
        if requester.id == target.id:
            debug_info += "❌ User tried to drag themselves\n"
            await send_debug(debug_info)
            return await channel.send(
                f"{requester.mention} ❌ You cannot drag yourself!", delete_after=10
            )

        if any(role.id in BLACKLISTED_VOICE_IDS for role in requester.roles):
            debug_info += f"❌ User is blacklisted (roles: {[r.id for r in requester.roles]})\n"
            await send_debug(debug_info)
            return await channel.send(
                f"{requester.mention} ❌ You are not allowed to use this command.", delete_after=10
            )

        if not requester.voice or not requester.voice.channel:
            debug_info += "❌ Requester not in VC\n"
            await send_debug(debug_info)
            return await channel.send(
                f"{requester.mention} ❌ You must join a voice channel first.", delete_after=10
            )

        if not target.voice or not target.voice.channel:
            debug_info += "❌ Target not in VC\n"
            await send_debug(debug_info)
            return await channel.send(
                f"{requester.mention} ❌ {target.mention} must be in a voice channel.", delete_after=10
            )

        if requester.voice.channel == target.voice.channel:
            debug_info += "⚠️ Users already in same VC\n"
            await send_debug(debug_info)
            return await channel.send(
                f"{requester.mention} ⚠️ You're already in {target.mention}'s VC.", delete_after=10
            )

        debug_info += f"✅ Basic checks passed\n"
        debug_info += f"Requester VC: {requester.voice.channel.name} (ID: {requester.voice.channel.id})\n"
        debug_info += f"Target VC: {target.voice.channel.name} (ID: {target.voice.channel.id})\n"
        
        # --- Check if requester already has pending requests ---
        async with requests_lock:
            # Check if requester has any pending requests (as requester OR target)
            user_pending_requests = [
                r for r in active_requests 
                if r["requester"] == requester.id or r["target"] == requester.id
            ]
            
            if user_pending_requests:
                debug_info += f"❌ Requester has pending requests: {user_pending_requests}\n"
                await send_debug(debug_info)
                return await channel.send(
                    f"{requester.mention} ❌ You already have a pending request! Please wait for it to complete.",
                    delete_after=10
                )
            
            # Check if target already has pending requests (as requester OR target)
            target_pending_requests = [
                r for r in active_requests 
                if r["requester"] == target.id or r["target"] == target.id
            ]
            
            if target_pending_requests:
                debug_info += f"❌ Target has pending requests: {target_pending_requests}\n"
                await send_debug(debug_info)
                return await channel.send(
                    f"{requester.mention} ❌ {target.mention} already has a pending request! Please wait for it to complete.",
                    delete_after=10
                )

        # --- REMOVED: VC limit check with multiplier ---
        # Now just check if there's space in the VC normally
        target_channel = target.voice.channel

        request_key = {"requester": requester.id, "target": target.id}

        async with requests_lock:
            # Check for existing requests in both directions
            existing_request = next((r for r in active_requests 
                                   if (r["requester"] == requester.id and r["target"] == target.id) or
                                      (r["requester"] == target.id and r["target"] == requester.id)), None)
            
            if existing_request:
                debug_info += f"⚠️ Existing request found: {existing_request}\n"
                await send_debug(debug_info)
                return await channel.send(
                    f"{requester.mention} ⚠️ There's already an active request between you and {target.mention}",
                    delete_after=10,
                )
            
            active_requests.append(request_key)
            save_requests_safe(active_requests)
            debug_info += f"✅ Request saved: {request_key}\n"

        # Send confirmation in dragme channel
        await channel.send(f"✅ {requester.mention} Your request was sent to {target.mention}", delete_after=10)

        # Check if target VC is hidden/private
        is_hidden = not target_channel.permissions_for(target.guild.default_role).connect
        debug_info += f"Target VC hidden status: {is_hidden}\n"
        
        # Try to send request to target's VC first
        try:
            # Create embed for the target's VC
            vc_embed = discord.Embed(
                title="🔔 New DragMe Request!",
                description=f"{requester.mention} wants to join your voice channel!",
                color=discord.Color.gold()
            )
            vc_embed.add_field(name="Requester", value=f"{requester.name}\n{requester.mention}", inline=True)
            vc_embed.add_field(name="Their VC", value=requester.voice.channel.name, inline=True)
            vc_embed.add_field(name="Your VC", value=target_channel.name, inline=True)
            vc_embed.add_field(name="Current VC Users", value=f"{len(target_channel.members)}/{target_channel.user_limit if target_channel.user_limit > 0 else '∞'}", inline=True)
            vc_embed.set_footer(text=f"Use the buttons below to respond • Expires in {TIMEOUT_DURATION}s")

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

                async def finish(self, content: str, color=discord.Color.green(), log_channel=None):
                    self.finished = True
                    await self.remove_request()
                    
                    # Create log embed
                    log_embed = discord.Embed(
                        description=content,
                        color=color,
                        timestamp=discord.utils.utcnow()
                    )
                    log_embed.set_author(name=f"DragMe Request", icon_url=requester.display_avatar.url)
                    
                    # Send to dragme channel if provided
                    if log_channel:
                        await log_channel.send(embed=log_embed)
                    
                    if self.msg:
                        await self.msg.edit(embed=log_embed, view=None)
                    self.stop()

                async def on_timeout(self):
                    if not self.finished and self.msg:
                        await self.remove_request()
                        embed = discord.Embed(
                            description=f"⌛ Request from {requester.mention} to {target.mention} timed out.",
                            color=discord.Color.red(),
                            timestamp=discord.utils.utcnow()
                        )
                        embed.set_author(name="DragMe Request Timed Out", icon_url=requester.display_avatar.url)
                        
                        # Send timeout log to dragme channel
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
                        return await interaction.response.send_message("❌ This is not your request!", ephemeral=True)

                    # Refresh channel info
                    updated_target = interaction.guild.get_member(target.id)
                    if not updated_target or not updated_target.voice or not updated_target.voice.channel:
                        return await interaction.response.send_message(
                            "❌ You are no longer in a voice channel.", ephemeral=True
                        )

                    updated_channel = updated_target.voice.channel
                    
                    # Check if VC is full (normal limit check)
                    limit = updated_channel.user_limit
                    if limit > 0 and len(updated_channel.members) >= limit:
                        return await interaction.response.send_message(
                            f"⚠️ {updated_channel.name} is full (Max {limit} users).", ephemeral=True
                        )

                    # Check if target VC is hidden/private
                    is_hidden = not updated_channel.permissions_for(interaction.guild.default_role).connect

                    try:
                        # Move requester to target's VC
                        await requester.move_to(updated_channel)
                        
                        # If VC is hidden, give temporary permissions
                        if is_hidden:
                            try:
                                # Grant connect and speak permissions temporarily
                                await updated_channel.set_permissions(
                                    requester,
                                    connect=True,
                                    speak=True,
                                    reason=f"Temporary access granted via dragme request from {target.display_name}"
                                )
                                
                                # Schedule permission removal after user leaves
                                async def remove_perms_when_done():
                                    await asyncio.sleep(300)  # 5 minutes timeout
                                    try:
                                        current_member = interaction.guild.get_member(requester.id)
                                        if current_member and current_member.voice and current_member.voice.channel == updated_channel:
                                            # User is still in VC, don't remove perms
                                            return
                                        await updated_channel.set_permissions(requester, overwrite=None)
                                    except:
                                        pass
                                
                                asyncio.create_task(remove_perms_when_done())
                                
                                success_msg = f"✅ {target.mention} accepted. {requester.mention} was moved to **{updated_channel.name}** (temporary permissions granted for hidden VC)"
                            except:
                                success_msg = f"✅ {target.mention} accepted. {requester.mention} was moved to **{updated_channel.name}** (couldn't grant perms for hidden VC, contact admin)"
                        else:
                            success_msg = f"✅ {target.mention} accepted. {requester.mention} was moved to **{updated_channel.name}**"
                        
                        dragme_channel = bot.get_channel(DRAGME_CHANNEL_ID)
                        await self.finish(success_msg, discord.Color.green(), dragme_channel)
                        
                    except discord.Forbidden:
                        await self.finish(
                            f"❌ Missing permissions to move {requester.mention}",
                            discord.Color.red(),
                            bot.get_channel(DRAGME_CHANNEL_ID)
                        )
                    except discord.HTTPException as e:
                        await self.finish(
                            f"❌ Failed to move user: {str(e)}",
                            discord.Color.red(),
                            bot.get_channel(DRAGME_CHANNEL_ID)
                        )

                @discord.ui.button(style=discord.ButtonStyle.red, label="❌ Decline")
                async def decline_button(
                    self, interaction: discord.Interaction, button: discord.ui.Button
                ):
                    if interaction.user.id != target.id:
                        return await interaction.response.send_message("❌ This is not your request!", ephemeral=True)
                    
                    dragme_channel = bot.get_channel(DRAGME_CHANNEL_ID)
                    await self.finish(
                        f"❌ {target.mention} declined {requester.mention}'s request", 
                        discord.Color.red(),
                        dragme_channel
                    )

            view = DragmeView()
            
            # Try to send to target's VC
            try:
                view.msg = await target_channel.send(f"{target.mention}", embed=vc_embed, view=view)
                debug_info += f"✅ Request sent to target's VC: {target_channel.name}\n"
                await send_debug(debug_info)
            except (discord.Forbidden, discord.HTTPException) as e:
                debug_info += f"❌ Failed to send to VC: {str(e)}\n"
                debug_info += "Attempting to send to dragme channel instead...\n"
                await send_debug(debug_info)
                raise  # Re-raise to trigger the except block below
                
        except (discord.Forbidden, discord.HTTPException):
            # Can't send to VC (hidden or no permission), send to dragme channel instead
            dragme_channel = bot.get_channel(DRAGME_CHANNEL_ID)
            if dragme_channel:
                # Create alternative embed for dragme channel
                fallback_embed = discord.Embed(
                    title="🔔 New DragMe Request (Hidden VC)",
                    description=f"{requester.mention} wants to join {target.mention}'s hidden voice channel!",
                    color=discord.Color.purple()
                )
                fallback_embed.add_field(name="Requester", value=f"{requester.name}\n{requester.mention}", inline=True)
                fallback_embed.add_field(name="Target", value=f"{target.name}\n{target.mention}", inline=True)
                fallback_embed.add_field(name="Their VC", value=requester.voice.channel.name, inline=True)
                fallback_embed.add_field(name="Target VC", value="🔒 Hidden Voice Channel", inline=True)
                fallback_embed.add_field(name="Current VC Users", value=f"{len(target_channel.members)}/{target_channel.user_limit if target_channel.user_limit > 0 else '∞'}", inline=True)
                fallback_embed.set_footer(text=f"Request expires in {TIMEOUT_DURATION} seconds • Target is in hidden VC")
                
                # Create new view for the fallback message
                view = DragmeView()
                view.msg = await dragme_channel.send(f"{target.mention}", embed=fallback_embed, view=view)
                debug_info += f"✅ Request sent to dragme channel instead (hidden VC handling)\n"
                await send_debug(debug_info)

    # Keep the active_requests command as a helpful utility
    @bot.tree.command(
        name="active_requests",
        description="View all active dragme requests"
    )
    async def active_requests_cmd(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        async with requests_lock:
            if not active_requests:
                return await interaction.followup.send(
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
            
            await interaction.followup.send(embed=embed, ephemeral=True)