import discord
from discord.ui import Button, View, UserSelect, Select
from .modals import RenameModal, LimitModal
from .config import JOIN_CHANNELS, DB_PATH
import aiosqlite
import time


class ControlPanel(View):
    """Base view for control panels. Interaction checks ensure only the owner can use."""

    def __init__(self, cog, channel_id: int, owner_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.channel_id = channel_id
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "❌ Only the VC owner can use these controls!", ephemeral=True
            )
            return False
        return True

    async def update_database(self, **kwargs):
        """Update the voiceChannel table using voiceID as key."""
        if not kwargs:
            return
        async with aiosqlite.connect(DB_PATH) as db:
            set_clause = ", ".join([f"{key} = ?" for key in kwargs.keys()])
            values = list(kwargs.values()) + [self.channel_id]
            await db.execute(f"UPDATE voiceChannel SET {set_clause} WHERE voiceID = ?", values)
            await db.commit()

    @staticmethod
    def _has_allow(overwrite: discord.PermissionOverwrite) -> bool:
        return any(getattr(overwrite, p) is True for p in ("connect", "view_channel", "speak", "stream"))

    @staticmethod
    def _has_deny(overwrite: discord.PermissionOverwrite) -> bool:
        return any(getattr(overwrite, p) is False for p in ("connect", "view_channel", "speak", "stream"))


# --------------------- MAIN CONTROLS VIEW (ALL BUTTONS IN ONE PLACE) ---------------------
class BasicControlsView(ControlPanel):
    def __init__(self, cog, channel_id: int, owner_id: int):
        super().__init__(cog, channel_id, owner_id)
        
        # Get channel to check current states
        channel = cog.bot.get_channel(channel_id)
        
        # Get current permission states
        if channel:
            default_overwrites = channel.overwrites_for(channel.guild.default_role)
            is_locked = default_overwrites.connect is False
            is_hidden = default_overwrites.view_channel is False
        else:
            is_locked = False
            is_hidden = False
        
        # Row 0: Basic Controls (Name, Limit, Delete)
        self.add_item(NameButton())                          # 👤 Name
        self.add_item(LimitButton())                         # 👥 Limit
        self.add_item(DeleteButton())                        # 🗑️ Delete
        
        # Row 1: Privacy & Actions (Lock/Unlock, Hide/Unhide, Kick)
        # These buttons will update dynamically when clicked
        self.add_item(LockButton(is_locked=is_locked))       # 🔒 Lock / 🔓 Unlock (toggles)
        self.add_item(HideButton(is_hidden=is_hidden))       # 🕵️ Hide / 👀 Unhide (toggles)
        self.add_item(KickButton())                           # 👢 Kick
        
        # Row 2: Member Management (Trust, Untrust, Block, Unblock)
        self.add_item(TrustButton())       # ✅ Trust
        self.add_item(UntrustButton())     # ❌ Untrust
        self.add_item(BlockButton())       # 🚫 Block
        self.add_item(UnblockButton())     # ✅ Unblock
        
        # Row 3: Advanced (Transfer, Claim)
        self.add_item(TransferButton())    # 🔄 Transfer
        self.add_item(ClaimButton())       # 🎯 Claim


# --------------------- BUTTON CLASSES ---------------------

class NameButton(Button):
    def __init__(self):
        super().__init__(label="👤 Name", style=discord.ButtonStyle.secondary, custom_id="name", row=0)

    async def callback(self, interaction: discord.Interaction):
        view: ControlPanel = self.view
        channel = interaction.guild.get_channel(view.channel_id)
        if not channel:
            await interaction.response.send_message("❌ Channel not found!", ephemeral=True)
            return
        modal = RenameModal(view.cog, channel)
        await interaction.response.send_modal(modal)


class LimitButton(Button):
    def __init__(self):
        super().__init__(label="👥 Limit", style=discord.ButtonStyle.secondary, custom_id="limit", row=0)

    async def callback(self, interaction: discord.Interaction):
        view: ControlPanel = self.view
        channel = interaction.guild.get_channel(view.channel_id)
        if not channel:
            await interaction.response.send_message("❌ Channel not found!", ephemeral=True)
            return
        modal = LimitModal(view.cog, channel)
        await interaction.response.send_modal(modal)


class DeleteButton(Button):
    def __init__(self):
        super().__init__(label="🗑️ Delete", style=discord.ButtonStyle.danger, custom_id="delete", row=0)

    async def callback(self, interaction: discord.Interaction):
        view: ControlPanel = self.view
        channel = interaction.guild.get_channel(view.channel_id)
        if not channel:
            await interaction.response.send_message("❌ Channel not found!", ephemeral=True)
            return
        
        # Create confirmation view
        confirm_view = View(timeout=30)
        
        async def confirm_callback(confirm_interaction: discord.Interaction):
            if confirm_interaction.user.id != view.owner_id:
                await confirm_interaction.response.send_message("❌ Only the owner can delete!", ephemeral=True)
                return
            
            await confirm_interaction.response.defer(ephemeral=True)
            try:
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("DELETE FROM voiceChannel WHERE voiceID = ?", (channel.id,))
                    await db.commit()
                await channel.delete(reason="VC owner requested deletion")
                await confirm_interaction.followup.send("✅ Voice channel deleted!", ephemeral=True)
            except Exception:
                await confirm_interaction.followup.send("❌ Failed to delete channel!", ephemeral=True)
        
        async def cancel_callback(cancel_interaction: discord.Interaction):
            await cancel_interaction.response.edit_message(content="Deletion cancelled.", view=None)
        
        confirm_button = Button(label="✅ Confirm Delete", style=discord.ButtonStyle.danger)
        confirm_button.callback = confirm_callback
        cancel_button = Button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
        cancel_button.callback = cancel_callback
        
        confirm_view.add_item(confirm_button)
        confirm_view.add_item(cancel_button)
        
        await interaction.response.send_message(
            "⚠️ Are you sure you want to delete this voice channel? This action cannot be undone!",
            view=confirm_view,
            ephemeral=True
        )


class LockButton(Button):
    def __init__(self, is_locked: bool = False):
        # Dynamic label based on state
        label = "🔓 Unlock" if is_locked else "🔒 Lock"
        super().__init__(
            label=label,
            style=discord.ButtonStyle.secondary,
            custom_id="lock_unlock",
            row=1
        )
        self.is_locked = is_locked

    async def callback(self, interaction: discord.Interaction):
        view: ControlPanel = self.view
        channel = interaction.guild.get_channel(view.channel_id)
        if not channel:
            await interaction.response.send_message("❌ Channel not found!", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True)
        
        current_overwrites = channel.overwrites_for(interaction.guild.default_role)
        
        if self.is_locked:
            # Currently locked -> Unlock
            await channel.set_permissions(
                interaction.guild.default_role,
                connect=True,
                view_channel=current_overwrites.view_channel
            )
            
            # Remove per-member allows (preserve owner)
            for member in list(channel.guild.members):
                if member.id == view.owner_id:
                    continue
                overwrite = channel.overwrites_for(member)
                if overwrite and (overwrite.connect is True or overwrite.view_channel is True):
                    try:
                        await channel.set_permissions(member, overwrite=None)
                    except Exception:
                        continue
            
            await view.update_database(locked=False)
            action_msg = "🔓 Channel unlocked!"
        else:
            # Currently unlocked -> Lock
            await channel.set_permissions(
                interaction.guild.default_role,
                connect=False,
                view_channel=current_overwrites.view_channel
            )
            
            # Grant current members explicit access
            for member in list(channel.members):
                try:
                    await channel.set_permissions(member, connect=True, view_channel=True)
                except Exception:
                    continue
            
            await view.update_database(locked=True)
            action_msg = "🔒 Channel locked! Current members kept access."
        
        # Refresh the view with updated button states
        new_view = BasicControlsView(view.cog, view.channel_id, view.owner_id)
        await interaction.edit_original_response(view=new_view)
        await interaction.followup.send(action_msg, ephemeral=True)


class HideButton(Button):
    def __init__(self, is_hidden: bool = False):
        # Dynamic label based on state
        label = "👀 Unhide" if is_hidden else "🕵️ Hide"
        super().__init__(
            label=label,
            style=discord.ButtonStyle.secondary,
            custom_id="hide_unhide",
            row=1
        )
        self.is_hidden = is_hidden

    async def callback(self, interaction: discord.Interaction):
        view: ControlPanel = self.view
        channel = interaction.guild.get_channel(view.channel_id)
        if not channel:
            await interaction.response.send_message("❌ Channel not found!", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True)
        
        current_overwrites = channel.overwrites_for(interaction.guild.default_role)
        
        if self.is_hidden:
            # Currently hidden -> Unhide
            await channel.set_permissions(
                interaction.guild.default_role,
                view_channel=True,
                connect=current_overwrites.connect
            )
            
            # Remove per-member allows (preserve owner)
            for member in list(channel.guild.members):
                if member.id == view.owner_id:
                    continue
                overwrite = channel.overwrites_for(member)
                if overwrite and (overwrite.view_channel is True):
                    try:
                        await channel.set_permissions(member, overwrite=None)
                    except Exception:
                        continue
            
            await view.update_database(hidden=False)
            action_msg = "👀 Channel now visible!"
        else:
            # Currently visible -> Hide
            await channel.set_permissions(
                interaction.guild.default_role,
                view_channel=False,
                connect=current_overwrites.connect
            )
            
            # Grant current members explicit access
            for member in list(channel.members):
                try:
                    await channel.set_permissions(member, view_channel=True)
                except Exception:
                    continue
            
            await view.update_database(hidden=True)
            action_msg = "🕵️ Channel hidden! Current members can still see it."
        
        # Refresh the view with updated button states
        new_view = BasicControlsView(view.cog, view.channel_id, view.owner_id)
        await interaction.edit_original_response(view=new_view)
        await interaction.followup.send(action_msg, ephemeral=True)


class KickButton(Button):
    def __init__(self):
        super().__init__(label="👢 Kick", style=discord.ButtonStyle.secondary, custom_id="kick", row=1)

    async def callback(self, interaction: discord.Interaction):
        view: ControlPanel = self.view
        channel = interaction.guild.get_channel(view.channel_id)
        if not channel:
            await interaction.response.send_message("❌ Channel not found!", ephemeral=True)
            return
        
        # Get users in VC (excluding owner)
        vc_users = [m for m in channel.members if m.id != view.owner_id]
        
        if not vc_users:
            await interaction.response.send_message("❌ No users in VC to kick!", ephemeral=True)
            return
        
        # Create user select menu
        select_view = View(timeout=60)
        
        select = UserSelect(
            placeholder="Select users to kick...",
            min_values=1,
            max_values=min(25, len(vc_users)),
            row=0
        )
        
        async def select_callback(select_interaction: discord.Interaction):
            await select_interaction.response.defer(ephemeral=True)
            successful = []
            
            for member in select.values:
                try:
                    if member.voice and member.voice.channel == channel:
                        await member.move_to(None)
                        successful.append(member.display_name)
                except Exception as e:
                    print(f"Error kicking {member.id}: {e}")
            
            if successful:
                await select_interaction.followup.send(f"👢 Kicked: {', '.join(successful)}", ephemeral=True)
            else:
                await select_interaction.followup.send("❌ Failed to kick users!", ephemeral=True)
        
        select.callback = select_callback
        select_view.add_item(select)
        
        # Add cancel button
        cancel_button = Button(label="❌ Cancel", style=discord.ButtonStyle.secondary, row=1)
        
        async def cancel_callback(cancel_interaction: discord.Interaction):
            await cancel_interaction.response.edit_message(content="Kick cancelled.", view=None)
        
        cancel_button.callback = cancel_callback
        select_view.add_item(cancel_button)
        
        await interaction.response.send_message(
            "Select users to kick:",
            view=select_view,
            ephemeral=True
        )


class TrustButton(Button):
    def __init__(self):
        super().__init__(label="✅ Trust", style=discord.ButtonStyle.secondary, custom_id="trust", row=2)

    async def callback(self, interaction: discord.Interaction):
        view: ControlPanel = self.view
        channel = interaction.guild.get_channel(view.channel_id)
        if not channel:
            await interaction.response.send_message("❌ Channel not found!", ephemeral=True)
            return
        
        # Create user select menu
        select_view = View(timeout=60)
        
        # Get all members who don't already have allow perms
        available_users = []
        for member in channel.guild.members:
            if member.id == view.owner_id or member.bot:
                continue
            overwrite = channel.overwrites_for(member)
            if not view._has_allow(overwrite):
                available_users.append(member)
        
        if not available_users:
            await interaction.response.send_message("✅ All users already have access permissions!", ephemeral=True)
            return
        
        select = UserSelect(
            placeholder="Select users to trust...",
            min_values=1,
            max_values=min(25, len(available_users)),
            row=0
        )
        
        async def select_callback(select_interaction: discord.Interaction):
            await select_interaction.response.defer(ephemeral=True)
            successful = []
            
            for member in select.values:
                try:
                    await channel.set_permissions(member, connect=True, view_channel=True, speak=True, stream=True)
                    successful.append(member.display_name)
                except Exception as e:
                    print(f"Error trusting {member.id}: {e}")
            
            if successful:
                await select_interaction.followup.send(f"✅ Granted access to: {', '.join(successful)}", ephemeral=True)
            else:
                await select_interaction.followup.send("❌ Failed to grant access!", ephemeral=True)
        
        select.callback = select_callback
        select_view.add_item(select)
        
        # Add cancel button
        cancel_button = Button(label="❌ Cancel", style=discord.ButtonStyle.secondary, row=1)
        
        async def cancel_callback(cancel_interaction: discord.Interaction):
            await cancel_interaction.response.edit_message(content="Trust cancelled.", view=None)
        
        cancel_button.callback = cancel_callback
        select_view.add_item(cancel_button)
        
        await interaction.response.send_message("Select users to trust:", view=select_view, ephemeral=True)


class UntrustButton(Button):
    def __init__(self):
        super().__init__(label="❌ Untrust", style=discord.ButtonStyle.secondary, custom_id="untrust", row=2)

    async def callback(self, interaction: discord.Interaction):
        view: ControlPanel = self.view
        channel = interaction.guild.get_channel(view.channel_id)
        if not channel:
            await interaction.response.send_message("❌ Channel not found!", ephemeral=True)
            return
        
        # Create user select menu
        select_view = View(timeout=60)
        
        # Get members with allow perms
        trusted_users = []
        for member in channel.guild.members:
            if member.id == view.owner_id or member.bot:
                continue
            overwrite = channel.overwrites_for(member)
            if view._has_allow(overwrite):
                trusted_users.append(member)
        
        if not trusted_users:
            await interaction.response.send_message("❌ No users have trusted access!", ephemeral=True)
            return
        
        select = UserSelect(
            placeholder="Select users to untrust...",
            min_values=1,
            max_values=min(25, len(trusted_users)),
            row=0
        )
        
        async def select_callback(select_interaction: discord.Interaction):
            await select_interaction.response.defer(ephemeral=True)
            successful = []
            
            for member in select.values:
                try:
                    await channel.set_permissions(member, overwrite=None)
                    successful.append(member.display_name)
                except Exception as e:
                    print(f"Error untrusting {member.id}: {e}")
            
            if successful:
                await select_interaction.followup.send(f"❌ Revoked access from: {', '.join(successful)}", ephemeral=True)
            else:
                await select_interaction.followup.send("❌ Failed to revoke access!", ephemeral=True)
        
        select.callback = select_callback
        select_view.add_item(select)
        
        # Add cancel button
        cancel_button = Button(label="❌ Cancel", style=discord.ButtonStyle.secondary, row=1)
        
        async def cancel_callback(cancel_interaction: discord.Interaction):
            await cancel_interaction.response.edit_message(content="Untrust cancelled.", view=None)
        
        cancel_button.callback = cancel_callback
        select_view.add_item(cancel_button)
        
        await interaction.response.send_message("Select users to untrust:", view=select_view, ephemeral=True)


class BlockButton(Button):
    def __init__(self):
        super().__init__(label="🚫 Block", style=discord.ButtonStyle.secondary, custom_id="block", row=2)

    async def callback(self, interaction: discord.Interaction):
        view: ControlPanel = self.view
        channel = interaction.guild.get_channel(view.channel_id)
        if not channel:
            await interaction.response.send_message("❌ Channel not found!", ephemeral=True)
            return
        
        # Create user select menu
        select_view = View(timeout=60)
        
        # Get users who aren't already blocked
        available_users = []
        for member in channel.guild.members:
            if member.id == view.owner_id or member.bot:
                continue
            overwrite = channel.overwrites_for(member)
            if not view._has_deny(overwrite):
                available_users.append(member)
        
        if not available_users:
            await interaction.response.send_message("✅ All users are already blocked!", ephemeral=True)
            return
        
        select = UserSelect(
            placeholder="Select users to block...",
            min_values=1,
            max_values=min(25, len(available_users)),
            row=0
        )
        
        async def select_callback(select_interaction: discord.Interaction):
            await select_interaction.response.defer(ephemeral=True)
            successful = []
            
            for member in select.values:
                try:
                    # Kick if in VC
                    if member.voice and member.voice.channel == channel:
                        await member.move_to(None)
                    
                    # Block permissions
                    await channel.set_permissions(member, connect=False, view_channel=False, speak=False, stream=False)
                    successful.append(member.display_name)
                except Exception as e:
                    print(f"Error blocking {member.id}: {e}")
            
            if successful:
                await select_interaction.followup.send(f"🚫 Blocked: {', '.join(successful)}", ephemeral=True)
            else:
                await select_interaction.followup.send("❌ Failed to block users!", ephemeral=True)
        
        select.callback = select_callback
        select_view.add_item(select)
        
        # Add cancel button
        cancel_button = Button(label="❌ Cancel", style=discord.ButtonStyle.secondary, row=1)
        
        async def cancel_callback(cancel_interaction: discord.Interaction):
            await cancel_interaction.response.edit_message(content="Block cancelled.", view=None)
        
        cancel_button.callback = cancel_callback
        select_view.add_item(cancel_button)
        
        await interaction.response.send_message("Select users to block:", view=select_view, ephemeral=True)


class UnblockButton(Button):
    def __init__(self):
        super().__init__(label="✅ Unblock", style=discord.ButtonStyle.secondary, custom_id="unblock", row=3)

    async def callback(self, interaction: discord.Interaction):
        view: ControlPanel = self.view
        channel = interaction.guild.get_channel(view.channel_id)
        if not channel:
            await interaction.response.send_message("❌ Channel not found!", ephemeral=True)
            return
        
        # Create user select menu
        select_view = View(timeout=60)
        
        # Get blocked users
        blocked_users = []
        for member in channel.guild.members:
            if member.id == view.owner_id or member.bot:
                continue
            overwrite = channel.overwrites_for(member)
            if view._has_deny(overwrite):
                blocked_users.append(member)
        
        if not blocked_users:
            await interaction.response.send_message("✅ No users are blocked!", ephemeral=True)
            return
        
        select = UserSelect(
            placeholder="Select users to unblock...",
            min_values=1,
            max_values=min(25, len(blocked_users)),
            row=0
        )
        
        async def select_callback(select_interaction: discord.Interaction):
            await select_interaction.response.defer(ephemeral=True)
            successful = []
            
            for member in select.values:
                try:
                    await channel.set_permissions(member, overwrite=None)
                    successful.append(member.display_name)
                except Exception as e:
                    print(f"Error unblocking {member.id}: {e}")
            
            if successful:
                await select_interaction.followup.send(f"✅ Unblocked: {', '.join(successful)}", ephemeral=True)
            else:
                await select_interaction.followup.send("❌ Failed to unblock users!", ephemeral=True)
        
        select.callback = select_callback
        select_view.add_item(select)
        
        # Add cancel button
        cancel_button = Button(label="❌ Cancel", style=discord.ButtonStyle.secondary, row=1)
        
        async def cancel_callback(cancel_interaction: discord.Interaction):
            await cancel_interaction.response.edit_message(content="Unblock cancelled.", view=None)
        
        cancel_button.callback = cancel_callback
        select_view.add_item(cancel_button)
        
        await interaction.response.send_message("Select users to unblock:", view=select_view, ephemeral=True)


class TransferButton(Button):
    def __init__(self):
        super().__init__(label="🔄 Transfer", style=discord.ButtonStyle.secondary, custom_id="transfer", row=3)

    async def callback(self, interaction: discord.Interaction):
        view: ControlPanel = self.view
        channel = interaction.guild.get_channel(view.channel_id)
        if not channel:
            await interaction.response.send_message("❌ Channel not found!", ephemeral=True)
            return
        
        # Get users currently in the VC (excluding owner and bots)
        vc_users = [m for m in channel.members if m.id != view.owner_id and not m.bot]
        
        if not vc_users:
            await interaction.response.send_message(
                "❌ No users in the voice channel to transfer ownership to!", 
                ephemeral=True
            )
            return
        
        # Create user select view
        select_view = View(timeout=60)
        
        select = UserSelect(
            placeholder="Select a user in VC to transfer ownership...",
            min_values=1,
            max_values=1,
            row=0
        )
        
        async def select_callback(select_interaction: discord.Interaction):
            await select_interaction.response.defer(ephemeral=True)
            
            new_owner = select.values[0]
            
            # Verify they're still in the VC
            if new_owner not in channel.members:
                await select_interaction.followup.send(
                    "❌ That user is no longer in the voice channel!",
                    ephemeral=True
                )
                return
            
            try:
                # Update database
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute(
                        "UPDATE voiceChannel SET userID = ? WHERE voiceID = ?",
                        (new_owner.id, channel.id)
                    )
                    await db.commit()
                
                # Update permissions
                old_owner = interaction.guild.get_member(view.owner_id)
                if old_owner:
                    await channel.set_permissions(old_owner, manage_channels=False)
                
                await channel.set_permissions(new_owner, manage_channels=True)
                
                embed = discord.Embed(
                    title="🔄 VC Ownership Transferred",
                    description=f"{new_owner.mention} is now the owner of this voice channel!",
                    color=discord.Color.blue()
                )
                await channel.send(embed=embed)
                
                await select_interaction.followup.send(
                    f"✅ Successfully transferred ownership to {new_owner.mention}!",
                    ephemeral=True
                )
                
                # Refresh the original message
                try:
                    new_view = BasicControlsView(view.cog, view.channel_id, new_owner.id)
                    await interaction.edit_original_response(view=new_view)
                except:
                    pass
                
            except Exception as e:
                print(f"Error transferring ownership: {e}")
                await select_interaction.followup.send(
                    "❌ Failed to transfer ownership. Please try again.",
                    ephemeral=True
                )
        
        select.callback = select_callback
        select_view.add_item(select)
        
        # Add cancel button
        cancel_button = Button(label="❌ Cancel", style=discord.ButtonStyle.secondary, row=1)
        
        async def cancel_callback(cancel_interaction: discord.Interaction):
            await cancel_interaction.response.edit_message(content="Transfer cancelled.", view=None)
        
        cancel_button.callback = cancel_callback
        select_view.add_item(cancel_button)
        
        await interaction.response.send_message(
            "👥 Select a user from the voice channel to transfer ownership to:",
            view=select_view,
            ephemeral=True
        )


class ClaimButton(Button):
    def __init__(self):
        super().__init__(label="🎯 Claim", style=discord.ButtonStyle.secondary, custom_id="claim", row=3)

    async def callback(self, interaction: discord.Interaction):
        view: ControlPanel = self.view
        await interaction.response.defer(ephemeral=True)

        # User must be in VC
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.followup.send("❌ You must be in a voice channel to claim it!", ephemeral=True)
            return

        channel = interaction.user.voice.channel
        
        # Check if user is trying to claim a generator channel
        if channel.id in JOIN_CHANNELS:
            await interaction.followup.send("❌ You cannot claim a generator channel!", ephemeral=True)
            return

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT userID FROM voiceChannel WHERE voiceID = ?", 
                (channel.id,)
            ) as cursor:
                data = await cursor.fetchone()

            if data:
                owner_id = data[0]
                owner = interaction.guild.get_member(owner_id)

                # Owner is currently in the VC
                if owner and owner in channel.members:
                    await interaction.followup.send(
                        f"❌ {owner.display_name} is currently in this VC. You can't claim it while they're here!",
                        ephemeral=True
                    )
                    return
                
                # Check if someone else is already trying to claim
                cog = view.cog
                current_time = time.time()
                claim_lock_key = f"claim_{channel.id}"
                
                last_claim_attempt = cog.creation_cooldowns.get(claim_lock_key, 0)
                if current_time - last_claim_attempt < 2:
                    await interaction.followup.send(
                        "⏳ Someone else is attempting to claim this channel. Please wait...",
                        ephemeral=True
                    )
                    return
                
                cog.creation_cooldowns[claim_lock_key] = current_time
                
                # First come, first serve: Update ownership immediately
                await db.execute(
                    "UPDATE voiceChannel SET userID = ? WHERE voiceID = ?",
                    (interaction.user.id, channel.id)
                )
                await db.commit()
                
                # Update permissions
                try:
                    if owner:
                        await channel.set_permissions(owner, manage_channels=False)
                    
                    await channel.set_permissions(interaction.user, manage_channels=True)
                    
                    cog.owner_leave_times.pop(channel.id, None)
                    
                    embed = discord.Embed(
                        title="🎯 VC Ownership Claimed",
                        description=f"{interaction.user.mention} has claimed ownership of this voice channel!\n"
                                  f"First come, first served!",
                        color=discord.Color.green()
                    )
                    await channel.send(embed=embed)
                    
                    await interaction.followup.send(
                        f"✅ You have successfully claimed ownership of {channel.mention}!",
                        ephemeral=True
                    )
                except Exception as e:
                    print(f"Error during claim transfer: {e}")
                    await db.execute(
                        "UPDATE voiceChannel SET userID = ? WHERE voiceID = ?",
                        (owner_id, channel.id)
                    )
                    await db.commit()
                    await interaction.followup.send("❌ Failed to complete claim. Please try again.", ephemeral=True)
                finally:
                    cog.creation_cooldowns.pop(claim_lock_key, None)
                return

            # No owner entry in DB, create new
            limit = channel.user_limit
            
            cog = view.cog
            current_time = time.time()
            claim_lock_key = f"claim_{channel.id}"
            
            last_claim_attempt = cog.creation_cooldowns.get(claim_lock_key, 0)
            if current_time - last_claim_attempt < 2:
                await interaction.followup.send(
                    "⏳ Someone else is attempting to claim this channel. Please wait...",
                    ephemeral=True
                )
                return
            
            cog.creation_cooldowns[claim_lock_key] = current_time
            
            try:
                await db.execute(
                    "INSERT INTO voiceChannel (userID, voiceID, channelName, channelLimit, guildID, vcType, channelNumber) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (interaction.user.id, channel.id, channel.name, limit, interaction.guild.id, "Other", 1)
                )
                await db.commit()
                
                await channel.set_permissions(interaction.user, manage_channels=True)
                
                embed = discord.Embed(
                    title="🎯 VC Ownership Claimed",
                    description=f"{interaction.user.mention} has claimed ownership of this voice channel!\n"
                              f"First come, first served!",
                    color=discord.Color.blue()
                )
                await channel.send(embed=embed)
                
                await interaction.followup.send(
                    f"✅ You have successfully claimed {channel.mention}!",
                    ephemeral=True
                )
            except Exception as e:
                print(f"Error during claim creation: {e}")
                await interaction.followup.send("❌ Failed to claim channel. Please try again.", ephemeral=True)
            finally:
                cog.creation_cooldowns.pop(claim_lock_key, None)