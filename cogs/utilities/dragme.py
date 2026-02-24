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

    # Message handler for dragme channel
    @bot.event
    async def on_message(message):
        # Ignore bot messages
        if message.author.bot:
            return
        
        # Only process messages in the dragme channel
        if message.channel.id != DRAGME_CHANNEL_ID:
            return
        
        # Check if message contains a user mention
        if not message.mentions:
            # If no mention, delete message and notify
            try:
                await message.delete()
                warning = await message.channel.send(f"{message.author.mention} Please mention a user to request drag! Example: @username", delete_after=5)
            except:
                pass
            return
        
        # Get the first mentioned user as target
        target = message.mentions[0]
        requester = message.author
        
        # Delete the original message
        try:
            await message.delete()
        except:
            pass
        
        # Process the drag request
        await process_drag_request(message.channel, requester, target)

    async def process_drag_request(channel, requester, target):
        """Process the drag request from message"""
        
        # --- Checks ---
        if requester.id == target.id:
            return await channel.send(
                f"{requester.mention} ❌ You cannot drag yourself!", delete_after=10
            )

        if any(role.id in BLACKLISTED_VOICE_IDS for role in requester.roles):
            return await channel.send(
                f"{requester.mention} ❌ You are not allowed to use this command.", delete_after=10
            )

        if not requester.voice or not requester.voice.channel:
            return await channel.send(
                f"{requester.mention} ❌ You must join a voice channel first.", delete_after=10
            )

        if not target.voice or not target.voice.channel:
            return await channel.send(
                f"{requester.mention} ❌ {target.mention} must be in a voice channel.", delete_after=10
            )

        if requester.voice.channel == target.voice.channel:
            return await channel.send(
                f"{requester.mention} ⚠️ You're already in {target.mention}'s VC.", delete_after=10
            )

        # --- Check if requester already has pending requests ---
        async with requests_lock:
            # Check if requester has any pending requests (as requester OR target)
            user_pending_requests = [
                r for r in active_requests 
                if r["requester"] == requester.id or r["target"] == requester.id
            ]
            
            if user_pending_requests:
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
                return await channel.send(
                    f"{requester.mention} ❌ {target.mention} already has a pending request! Please wait for it to complete.",
                    delete_after=10
                )

        # --- VC limit check (allow up to limit × 2) ---
        target_channel = target.voice.channel
        limit = target_channel.user_limit
        current_users = len(target_channel.members)
        if limit > 0 and current_users >= (limit * 2):
            return await channel.send(
                f"{requester.mention} ⚠️ VC limit reached. (Max {limit * 2})",
                delete_after=10
            )

        request_key = {"requester": requester.id, "target": target.id}

        async with requests_lock:
            # Check for existing requests in both directions
            existing_request = next((r for r in active_requests 
                                   if (r["requester"] == requester.id and r["target"] == target.id) or
                                      (r["requester"] == target.id and r["target"] == requester.id)), None)
            
            if existing_request:
                return await channel.send(
                    f"{requester.mention} ⚠️ There's already an active request between you and {target.mention}",
                    delete_after=10,
                )
            
            active_requests.append(request_key)
            save_requests_safe(active_requests)

        # Send confirmation in dragme channel
        await channel.send(f"✅ {requester.mention} Your request was sent to {target.mention}", delete_after=10)

        # Create embed for the target's VC
        embed = discord.Embed(
            title="🎯 VC Join Request",
            description=f"{requester.mention} wants to join your VC **{target_channel.name}**",
            color=discord.Color.orange()
        )
        embed.add_field(name="Requester", value=requester.mention, inline=True)
        embed.add_field(name="From VC", value=requester.voice.channel.name, inline=True)
        embed.add_field(name="To VC", value=target_channel.name, inline=True)
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
                
                # Re-check limits only (allow private/locked VCs)
                limit = updated_channel.user_limit
                if limit > 0 and len(updated_channel.members) >= (limit * 2):
                    return await interaction.response.send_message(
                        f"⚠️ VC limit reached. (Max {limit * 2})", ephemeral=True
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
        
        # Send request to target's VC
        target_vc = target.voice.channel
        if target_vc:
            # Create a more prominent embed for VC
            vc_embed = discord.Embed(
                title="🔔 New DragMe Request!",
                description=f"{requester.mention} wants to join your voice channel!",
                color=discord.Color.gold()
            )
            vc_embed.add_field(name="Requester", value=f"{requester.name}\n{requester.mention}", inline=True)
            vc_embed.add_field(name="Their VC", value=requester.voice.channel.name, inline=True)
            vc_embed.add_field(name="Your VC", value=target_vc.name, inline=True)
            vc_embed.set_footer(text=f"Use the buttons below to respond • Expires in {TIMEOUT_DURATION}s")
            
            # Send the request message in the target's VC
            view.msg = await target_vc.send(f"{target.mention}", embed=vc_embed, view=view)

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