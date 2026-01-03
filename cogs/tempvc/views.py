import discord
from discord.ui import Button, View, UserSelect
from .modals import RenameModal, TransferModal, LimitModal
from .config import JOIN_CHANNELS, DB_PATH
import aiosqlite
import time
from typing import Iterable, List


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
        """Update the voiceChannel table using userID as key."""
        if not kwargs:
            return
        async with aiosqlite.connect(DB_PATH) as db:
            set_clause = ", ".join([f"{key} = ?" for key in kwargs.keys()])
            values = list(kwargs.values()) + [self.owner_id]
            await db.execute(f"UPDATE voiceChannel SET {set_clause} WHERE userID = ?", values)
            await db.commit()

    # small helpers
    @staticmethod
    def _has_allow(overwrite: discord.PermissionOverwrite) -> bool:
        return any(getattr(overwrite, p) is True for p in ("connect", "view_channel", "speak", "stream"))

    @staticmethod
    def _has_deny(overwrite: discord.PermissionOverwrite) -> bool:
        return any(getattr(overwrite, p) is False for p in ("connect", "view_channel", "speak", "stream"))


# --------------------- Main Controls ---------------------
class BasicControlsView(ControlPanel):
    def __init__(self, cog, channel_id: int, owner_id: int):
        super().__init__(cog, channel_id, owner_id)
        # Row 1: Name, Members, Privacy
        self.add_item(NameButton())
        self.add_item(MembersButton())
        self.add_item(PrivacyButton())
        
        # Row 2: Limit, Transfer, Claim
        self.add_item(LimitButton())
        self.add_item(TransferButton())
        self.add_item(ClaimButton())
        
        # Row 3: Delete (alone)
        self.add_item(DeleteButton())


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


class PrivacyButton(Button):
    def __init__(self):
        super().__init__(label="🔐 Privacy", style=discord.ButtonStyle.secondary, custom_id="privacy", row=0)

    async def callback(self, interaction: discord.Interaction):
        view: ControlPanel = self.view
        await interaction.response.defer(ephemeral=True)
        channel = interaction.guild.get_channel(view.channel_id)
        if not channel:
            await interaction.followup.send("❌ Channel not found!", ephemeral=True)
            return
        await interaction.followup.send(view=PrivacyView(view.cog, view.channel_id, view.owner_id, channel), ephemeral=True)


class PrivacyView(ControlPanel):
    """Privacy controls. When locking/hiding, we grant explicit perms to current members so they can remain.

    When unlocking/unhiding, we restore the default role perms and remove the explicit allows we added.
    """

    def __init__(self, cog, channel_id: int, owner_id: int, channel: discord.abc.GuildChannel):
        super().__init__(cog, channel_id, owner_id)
        self.channel = channel

        current_overwrites = channel.overwrites_for(channel.guild.default_role)
        is_locked = current_overwrites.connect is False
        is_hidden = current_overwrites.view_channel is False

        self.add_item(LockButton(is_locked))
        self.add_item(UnlockButton(is_locked))
        self.add_item(HideButton(is_hidden))
        self.add_item(UnhideButton(is_hidden))


async def _grant_current_members(channel: discord.VoiceChannel):
    """Grant explicit allow perms to members currently inside the voice channel.

    This ensures they can still see/join even if default role is denied.
    """
    for member in list(channel.members):
        try:
            await channel.set_permissions(member, connect=True, view_channel=True)
        except Exception:
            # ignore individual failures
            continue


async def _remove_member_allows(channel: discord.VoiceChannel, preserve_ids: Iterable[int]):
    """Remove explicit overwrites for members we previously allowed when making channel public again.

    preserve_ids: member ids to keep (e.g., owner)
    """
    for member in list(channel.guild.members):
        if member.id in preserve_ids:
            continue
        overwrite = channel.overwrites_for(member)
        # only remove if the overwrite is an explicit allow for connect/view
        if overwrite and (overwrite.connect is True or overwrite.view_channel is True):
            try:
                await channel.set_permissions(member, overwrite=None)
            except Exception:
                continue


class LockButton(Button):
    def __init__(self, is_locked: bool):
        super().__init__(label="🔒 Lock", style=discord.ButtonStyle.secondary, custom_id="lock_row", row=0, disabled=is_locked)

    async def callback(self, interaction: discord.Interaction):
        view: PrivacyView = self.view
        channel = view.channel
        await interaction.response.defer(ephemeral=True)
        # deny default role connect (keep view as-is)
        current_overwrites = channel.overwrites_for(interaction.guild.default_role)
        await channel.set_permissions(interaction.guild.default_role, connect=False, view_channel=current_overwrites.view_channel)
        # grant current members explicit access so they won't be kicked out of view
        if isinstance(channel, discord.VoiceChannel):
            await _grant_current_members(channel)
        await view.update_database(locked=True)
        await interaction.edit_original_response(view=PrivacyView(view.cog, view.channel_id, view.owner_id, channel))
        await interaction.followup.send("🔒 Channel locked! Current members kept access.", ephemeral=True)


class UnlockButton(Button):
    def __init__(self, is_locked: bool):
        super().__init__(label="🔓 Unlock", style=discord.ButtonStyle.secondary, custom_id="unlock_row", row=0, disabled=not is_locked)

    async def callback(self, interaction: discord.Interaction):
        view: PrivacyView = self.view
        channel = view.channel
        await interaction.response.defer(ephemeral=True)
        # restore default connect True, keep view as-is
        current_overwrites = channel.overwrites_for(interaction.guild.default_role)
        await channel.set_permissions(interaction.guild.default_role, connect=True, view_channel=current_overwrites.view_channel)
        # remove per-member allows we added earlier (preserve owner)
        if isinstance(channel, discord.VoiceChannel):
            await _remove_member_allows(channel, preserve_ids={view.owner_id})
        await view.update_database(locked=False)
        await interaction.edit_original_response(view=PrivacyView(view.cog, view.channel_id, view.owner_id, channel))
        await interaction.followup.send("🔓 Channel unlocked!", ephemeral=True)


class HideButton(Button):
    def __init__(self, is_hidden: bool):
        super().__init__(label="🕵️ Hide", style=discord.ButtonStyle.secondary, custom_id="hide_row", row=1, disabled=is_hidden)

    async def callback(self, interaction: discord.Interaction):
        view: PrivacyView = self.view
        channel = view.channel
        await interaction.response.defer(ephemeral=True)
        current_overwrites = channel.overwrites_for(interaction.guild.default_role)
        await channel.set_permissions(interaction.guild.default_role, view_channel=False, connect=current_overwrites.connect)
        if isinstance(channel, discord.VoiceChannel):
            await _grant_current_members(channel)
        await view.update_database(hidden=True)
        await interaction.edit_original_response(view=PrivacyView(view.cog, view.channel_id, view.owner_id, channel))
        await interaction.followup.send("🕵️ Channel hidden! Current members can still see it.", ephemeral=True)


class UnhideButton(Button):
    def __init__(self, is_hidden: bool):
        super().__init__(label="👀 Unhide", style=discord.ButtonStyle.secondary, custom_id="unhide_row", row=1, disabled=not is_hidden)

    async def callback(self, interaction: discord.Interaction):
        view: PrivacyView = self.view
        channel = view.channel
        await interaction.response.defer(ephemeral=True)
        current_overwrites = channel.overwrites_for(interaction.guild.default_role)
        await channel.set_permissions(interaction.guild.default_role, view_channel=True, connect=current_overwrites.connect)
        if isinstance(channel, discord.VoiceChannel):
            await _remove_member_allows(channel, preserve_ids={view.owner_id})
        await view.update_database(hidden=False)
        await interaction.edit_original_response(view=PrivacyView(view.cog, view.channel_id, view.owner_id, channel))
        await interaction.followup.send("👀 Channel now visible!", ephemeral=True)


# --------------------- Members Management ---------------------
class MembersButton(Button):
    def __init__(self):
        super().__init__(label="👥 Members", style=discord.ButtonStyle.secondary, custom_id="members", row=0)

    async def callback(self, interaction: discord.Interaction):
        view: ControlPanel = self.view
        await interaction.response.defer(ephemeral=True)
        channel = interaction.guild.get_channel(view.channel_id)
        if not channel:
            await interaction.followup.send("❌ Channel not found!", ephemeral=True)
            return
        await interaction.followup.send(view=MembersMainView(view.cog, view.channel_id, view.owner_id, channel), ephemeral=True)


class MembersMainView(ControlPanel):
    def __init__(self, cog, channel_id: int, owner_id: int, channel: discord.abc.GuildChannel):
        super().__init__(cog, channel_id, owner_id)
        self.channel = channel
        self.add_item(GrantAccessButton())
        self.add_item(RevokeAccessButton())
        self.add_item(KickButton())
        self.add_item(BlockButton())
        self.add_item(UnblockButton())
        self.add_item(ViewAccessListButton())
        self.add_item(ViewBlockedButton())


class GrantAccessButton(Button):
    def __init__(self):
        super().__init__(label="✅ Grant Access", style=discord.ButtonStyle.secondary, custom_id="grant_access", row=0)

    async def callback(self, interaction: discord.Interaction):
        view: MembersMainView = self.view
        await interaction.response.defer(ephemeral=True)
        users = []
        for member in view.channel.guild.members:
            if member.id == view.owner_id or member.bot:
                continue
            overwrite = view.channel.overwrites_for(member)
            if not view._has_allow(overwrite):
                users.append(member)
        if users:
            await interaction.followup.send(view=UserSelectActionView(view.cog, view.channel_id, view.owner_id, view.channel, users, "grant", "Select users to grant access..."), ephemeral=True)
        else:
            await interaction.followup.send("✅ All users already have explicit access permissions!", ephemeral=True)


class RevokeAccessButton(Button):
    def __init__(self):
        super().__init__(label="❌ Revoke Access", style=discord.ButtonStyle.secondary, custom_id="revoke_access", row=0)

    async def callback(self, interaction: discord.Interaction):
        view: MembersMainView = self.view
        await interaction.response.defer(ephemeral=True)
        users = []
        for member in view.channel.guild.members:
            if member.id == view.owner_id or member.bot:
                continue
            overwrite = view.channel.overwrites_for(member)
            if view._has_allow(overwrite):
                users.append(member)
        if users:
            await interaction.followup.send(view=CustomUserDropdownView(view.cog, view.channel_id, view.owner_id, view.channel, users, "revoke", "Select users to revoke access..."), ephemeral=True)
        else:
            await interaction.followup.send("❌ No users have special access permissions!", ephemeral=True)


class KickButton(Button):
    def __init__(self):
        super().__init__(label="👢 Kick", style=discord.ButtonStyle.secondary, custom_id="kick", row=1)

    async def callback(self, interaction: discord.Interaction):
        view: MembersMainView = self.view
        await interaction.response.defer(ephemeral=True)
        
        # Only get users currently in the voice channel (excluding owner)
        vc_users = [m for m in view.channel.members if m.id != view.owner_id]
        
        if vc_users:
            await interaction.followup.send(
                view=UserSelectActionView(
                    view.cog, view.channel_id, view.owner_id, view.channel, 
                    vc_users, "kick", "Select users to kick from VC..."
                ), 
                ephemeral=True
            )
        else:
            await interaction.followup.send("❌ No users in VC to kick!", ephemeral=True)


class BlockButton(Button):
    def __init__(self):
        super().__init__(label="🚫 Block", style=discord.ButtonStyle.secondary, custom_id="block", row=1)

    async def callback(self, interaction: discord.Interaction):
        view: MembersMainView = self.view
        await interaction.response.defer(ephemeral=True)
        users_to_block = []
        for member in view.channel.guild.members:
            if member.id == view.owner_id or member.bot:
                continue
            overwrite = view.channel.overwrites_for(member)
            if not view._has_deny(overwrite):
                users_to_block.append(member)
        if users_to_block:
            await interaction.followup.send(view=UserSelectActionView(view.cog, view.channel_id, view.owner_id, view.channel, users_to_block, "block", "Select users to block..."), ephemeral=True)
        else:
            await interaction.followup.send("✅ All non-owner users are already blocked!", ephemeral=True)


class UnblockButton(Button):
    def __init__(self):
        super().__init__(label="✅ Unblock", style=discord.ButtonStyle.secondary, custom_id="unblock", row=1)

    async def callback(self, interaction: discord.Interaction):
        view: MembersMainView = self.view
        await interaction.response.defer(ephemeral=True)
        blocked = []
        for member in view.channel.guild.members:
            if member.id == view.owner_id or member.bot:
                continue
            overwrite = view.channel.overwrites_for(member)
            if view._has_deny(overwrite):
                blocked.append(member)
        if blocked:
            await interaction.followup.send(view=CustomUserDropdownView(view.cog, view.channel_id, view.owner_id, view.channel, blocked, "unblock", "Select users to unblock..."), ephemeral=True)
        else:
            await interaction.followup.send("✅ No users are currently blocked!", ephemeral=True)


class ViewAccessListButton(Button):
    def __init__(self):
        super().__init__(label="📋 Access List", style=discord.ButtonStyle.secondary, custom_id="view_access", row=2)

    async def callback(self, interaction: discord.Interaction):
        view: MembersMainView = self.view
        await interaction.response.defer(ephemeral=True)
        users_with_access = []
        users_with_deny = []
        for member in view.channel.guild.members:
            if member.id == view.owner_id or member.bot:
                continue
            overwrite = view.channel.overwrites_for(member)
            if view._has_allow(overwrite):
                perms = []
                if overwrite.connect is True: perms.append("Join")
                if overwrite.view_channel is True: perms.append("View")
                if overwrite.speak is True: perms.append("Speak")
                if overwrite.stream is True: perms.append("Stream")
                users_with_access.append(f"✅ {member.display_name} - {', '.join(perms)}")
            elif view._has_deny(overwrite):
                deny_perms = []
                if overwrite.connect is False: deny_perms.append("Join")
                if overwrite.view_channel is False: deny_perms.append("View")
                if overwrite.speak is False: deny_perms.append("Speak")
                if overwrite.stream is False: deny_perms.append("Stream")
                users_with_deny.append(f"❌ {member.display_name} - {', '.join(deny_perms)}")
        embed = discord.Embed(title="📋 Channel Permissions", color=discord.Color.blue())
        if users_with_access:
            embed.add_field(name="✅ Users with Explicit Access", value="\n".join(users_with_access[:25]), inline=False)
        if users_with_deny:
            embed.add_field(name="❌ Users with Explicit Deny", value="\n".join(users_with_deny[:25]), inline=False)
        if not users_with_access and not users_with_deny:
            embed.description = "No users have explicit permissions"
        await interaction.followup.send(embed=embed, ephemeral=True)


class ViewBlockedButton(Button):
    def __init__(self):
        super().__init__(label="🚫 Blocked Users", style=discord.ButtonStyle.secondary, custom_id="view_blocked", row=2)

    async def callback(self, interaction: discord.Interaction):
        view: MembersMainView = self.view
        await interaction.response.defer(ephemeral=True)
        blocked_users = []
        for member in view.channel.guild.members:
            if member.id == view.owner_id or member.bot:
                continue
            overwrite = view.channel.overwrites_for(member)
            if view._has_deny(overwrite):
                blocked_users.append(member.display_name)
        embed = discord.Embed(title="🚫 Blocked Users", description=("\n".join(blocked_users[:25]) if blocked_users else "No users are blocked"), color=discord.Color.red())
        await interaction.followup.send(embed=embed, ephemeral=True)


# Custom dropdown used for revoke/unblock (shows descriptions of perms)
class CustomUserDropdownView(ControlPanel):
    def __init__(self, cog, channel_id: int, owner_id: int, channel: discord.abc.GuildChannel, users: List[discord.Member], action_type: str, placeholder: str):
        super().__init__(cog, channel_id, owner_id)
        self.channel = channel
        self.action_type = action_type
        options = []
        for member in users:
            overwrite = channel.overwrites_for(member)
            if action_type == "revoke":
                perms = []
                if overwrite.connect is True: perms.append("Join")
                if overwrite.view_channel is True: perms.append("View")
                if overwrite.speak is True: perms.append("Speak")
                if overwrite.stream is True: perms.append("Stream")
                desc = f"({', '.join(perms)})" if perms else "(No specific perms)"
            else:  # unblock
                deny_perms = []
                if overwrite.connect is False: deny_perms.append("Join")
                if overwrite.view_channel is False: deny_perms.append("View")
                if overwrite.speak is False: deny_perms.append("Speak")
                if overwrite.stream is False: deny_perms.append("Stream")
                desc = f"(Blocked: {', '.join(deny_perms)})" if deny_perms else "(Blocked)"
            options.append(discord.SelectOption(label=member.display_name, value=str(member.id), description=desc[:100]))  # Limit description length
        if options:
            self.add_item(CustomUserDropdown(options, action_type, placeholder))
        else:
            select = discord.ui.Select(placeholder="No users available", options=[discord.SelectOption(label="No users available", value="none")], disabled=True, row=0)
            self.add_item(select)


class CustomUserDropdown(discord.ui.Select):
    def __init__(self, options: List[discord.SelectOption], action_type: str, placeholder: str):
        super().__init__(placeholder=placeholder, options=options, min_values=1, max_values=len(options), row=0)
        self.action_type = action_type

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        successful = []
        for user_id in self.values:
            try:
                member = interaction.guild.get_member(int(user_id))
                if not member:
                    continue
                if self.action_type in ("revoke", "unblock"):
                    await self.view.channel.set_permissions(member, overwrite=None)
                    successful.append(member.display_name)
            except Exception as e:
                print(f"Error in {self.action_type} action: {e}")
        if successful:
            action_text = {"revoke": "❌ Revoked access from", "unblock": "✅ Unblocked"}
            await interaction.followup.send(f"{action_text[self.action_type]}: {', '.join(successful)}", ephemeral=True)
        else:
            await interaction.followup.send(f"❌ Failed to {self.action_type}!", ephemeral=True)


# Unified select view for grant/kick/block actions
class UserSelectActionView(ControlPanel):
    def __init__(self, cog, channel_id: int, owner_id: int, channel: discord.abc.GuildChannel, users: List[discord.Member], action_type: str, placeholder: str):
        super().__init__(cog, channel_id, owner_id)
        self.channel = channel
        self.action_type = action_type
        if users:
            self.add_item(UserSelectAction(users, action_type, placeholder))
        else:
            select = UserSelect(placeholder="No users available", disabled=True, row=0)
            self.add_item(select)


class UserSelectAction(UserSelect):
    def __init__(self, users: List[discord.Member], action_type: str, placeholder: str):
        super().__init__(placeholder=placeholder, min_values=1, max_values=min(25, len(users)), row=0)
        self.action_type = action_type

    async def callback(self, interaction: discord.Interaction):
        try:
            # Try to defer first with proper error handling
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)
        except discord.NotFound:
            # Interaction already expired, send a new message
            await interaction.followup.send("❌ This interaction has expired. Please use the control panel again.", ephemeral=True)
            return

        successful = []
        for member in self.values:
            try:
                if self.action_type == "grant":
                    await self.view.channel.set_permissions(member, connect=True, view_channel=True, speak=True, stream=True)
                    successful.append(member.display_name)
                elif self.action_type == "revoke":
                    await self.view.channel.set_permissions(member, overwrite=None)
                    successful.append(member.display_name)
                elif self.action_type == "kick":
                    if member.voice and member.voice.channel == self.view.channel:
                        await member.move_to(None)
                        successful.append(member.display_name)
                elif self.action_type == "block":
                    if member.voice and member.voice.channel == self.view.channel:
                        await member.move_to(None)
                    await self.view.channel.set_permissions(member, connect=False, view_channel=False, speak=False, stream=False)
                    successful.append(member.display_name)
            except Exception as e:
                print(f"Error in {self.action_type} for {getattr(member, 'id', 'unknown')}: {e}")

        if successful:
            action_text = {"grant": "granted access to", "revoke": "revoked access from", "kick": "kicked", "block": "blocked"}
            await interaction.followup.send(f"✅ {action_text[self.action_type]}: {', '.join(successful)}", ephemeral=True)
        else:
            await interaction.followup.send("❌ Failed to perform action!", ephemeral=True)


# --------------------- Limit Button ---------------------
class LimitButton(Button):
    def __init__(self):
        super().__init__(label="👥 Limit", style=discord.ButtonStyle.secondary, custom_id="limit", row=1)

    async def callback(self, interaction: discord.Interaction):
        view: ControlPanel = self.view
        channel = interaction.guild.get_channel(view.channel_id)
        if not channel:
            await interaction.response.send_message("❌ Channel not found!", ephemeral=True)
            return
        modal = LimitModal(view.cog, channel)
        await interaction.response.send_modal(modal)


# --------------------- Transfer / Claim / Delete ---------------------
class TransferButton(Button):
    def __init__(self):
        super().__init__(label="🔄 Transfer", style=discord.ButtonStyle.secondary, custom_id="transfer", row=1)

    async def callback(self, interaction: discord.Interaction):
        view: ControlPanel = self.view
        modal = TransferModal(view.cog, view.channel_id)
        await interaction.response.send_modal(modal)


class ClaimButton(Button):
    def __init__(self):
        super().__init__(
            label="🎯 Claim",
            style=discord.ButtonStyle.secondary,
            custom_id="claim",
            row=1
        )

    async def callback(self, interaction: discord.Interaction):
        view: ControlPanel = self.view
        await interaction.response.defer(ephemeral=True)

        # User must be in VC
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.followup.send(
                "❌ You must be in a voice channel to claim it!",
                ephemeral=True
            )

        channel = interaction.user.voice.channel
        
        # Check if user is trying to claim a generator channel
        if channel.id in JOIN_CHANNELS:
            return await interaction.followup.send(
                "❌ You cannot claim a generator channel!",
                ephemeral=True
            )

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT userID FROM voiceChannel WHERE voiceID = ?", 
                (channel.id,)
            ) as cursor:
                data = await cursor.fetchone()

            if data:
                owner_id = data[0]
                owner = interaction.guild.get_member(owner_id)

                # --- Case 1: Owner is currently in the VC
                if owner and owner in channel.members:
                    return await interaction.followup.send(
                        f"❌ {owner.display_name} is currently in this VC. You can't claim it while they're here!",
                        ephemeral=True
                    )

                # --- Case 2: Check if channel has any owner at all (FIRST COME, FIRST SERVE LOGIC)
                # We just need to know if there's NO owner present, not wait for 10 seconds
                
                # Get all current members who are not the original owner
                current_members = [m for m in channel.members if m.id != owner_id]
                
                if not current_members:
                    return await interaction.followup.send(
                        "❌ No one is in the VC to claim it!",
                        ephemeral=True
                    )
                
                # Check if someone else is already trying to claim (in progress)
                cog = view.cog
                current_time = time.time()
                claim_lock_key = f"claim_{channel.id}"
                
                # Simple rate limiting: 2 seconds between claim attempts
                last_claim_attempt = cog.creation_cooldowns.get(claim_lock_key, 0)
                if current_time - last_claim_attempt < 2:
                    return await interaction.followup.send(
                        "⏳ Someone else is attempting to claim this channel. Please wait...",
                        ephemeral=True
                    )
                
                # Set claim lock
                cog.creation_cooldowns[claim_lock_key] = current_time
                
                # FIRST COME, FIRST SERVE: Update ownership immediately
                await db.execute(
                    "UPDATE voiceChannel SET userID = ? WHERE voiceID = ?",
                    (interaction.user.id, channel.id)
                )
                await db.commit()
                
                # Update permissions
                try:
                    # Remove old owner's manage permissions
                    if owner:
                        await channel.set_permissions(owner, manage_channels=False)
                    
                    # Give new owner manage permissions
                    await channel.set_permissions(interaction.user, manage_channels=True)
                    
                    # Remove the leave time tracking
                    cog.owner_leave_times.pop(channel.id, None)
                    
                    # Send notification
                    embed = discord.Embed(
                        title="🎯 VC Ownership Claimed",
                        description=f"{interaction.user.mention} has claimed ownership of this voice channel!\n"
                                  f"First come, first served!",
                        color=discord.Color.green()
                    )
                    await channel.send(embed=embed)
                    
                    await interaction.followup.send(
                        f"✅ You have successfully claimed ownership of {channel.mention}!\n"
                        f"First come, first served!",
                        ephemeral=True
                    )
                except Exception as e:
                    print(f"Error during claim transfer: {e}")
                    # Rollback on error
                    await db.execute(
                        "UPDATE voiceChannel SET userID = ? WHERE voiceID = ?",
                        (owner_id, channel.id)
                    )
                    await db.commit()
                    await interaction.followup.send(
                        f"❌ Failed to complete claim. Please try again.",
                        ephemeral=True
                    )
                finally:
                    # Clear claim lock
                    cog.creation_cooldowns.pop(claim_lock_key, None)
                return

            # --- Case 3: No owner entry in DB, create new (first come, first serve)
            limit = channel.user_limit
            
            # Check if someone else is already trying to claim
            cog = view.cog
            current_time = time.time()
            claim_lock_key = f"claim_{channel.id}"
            
            last_claim_attempt = cog.creation_cooldowns.get(claim_lock_key, 0)
            if current_time - last_claim_attempt < 2:
                return await interaction.followup.send(
                    "⏳ Someone else is attempting to claim this channel. Please wait...",
                    ephemeral=True
                )
            
            # Set claim lock
            cog.creation_cooldowns[claim_lock_key] = current_time
            
            try:
                # Insert new owner
                await db.execute(
                    "INSERT INTO voiceChannel (userID, voiceID, channelName, channelLimit, guildID, vcType, channelNumber) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (interaction.user.id, channel.id, channel.name, limit, interaction.guild.id, "Other", 1)
                )
                await db.commit()
                
                # Give owner manage permissions
                await channel.set_permissions(interaction.user, manage_channels=True)
                
                # Send notification
                embed = discord.Embed(
                    title="🎯 VC Ownership Claimed",
                    description=f"{interaction.user.mention} has claimed ownership of this voice channel!\n"
                              f"First come, first served!",
                    color=discord.Color.blue()
                )
                await channel.send(embed=embed)
                
                await interaction.followup.send(
                    f"✅ You have successfully claimed {channel.mention}!\n"
                    f"First come, first served!",
                    ephemeral=True
                )
            except Exception as e:
                print(f"Error during claim creation: {e}")
                await interaction.followup.send(
                    f"❌ Failed to claim channel. Please try again.",
                    ephemeral=True
                )
            finally:
                # Clear claim lock
                cog.creation_cooldowns.pop(claim_lock_key, None)


class DeleteButton(Button):
    def __init__(self):
        super().__init__(label="🗑️ Delete", style=discord.ButtonStyle.danger, custom_id="delete", row=2)

    async def callback(self, interaction: discord.Interaction):
        view: ControlPanel = self.view
        await interaction.response.defer(ephemeral=True)
        channel = interaction.guild.get_channel(view.channel_id)
        if not channel:
            await interaction.followup.send("❌ Channel not found!", ephemeral=True)
            return
        await interaction.followup.send(
            "⚠️ Are you sure you want to delete this voice channel? This action cannot be undone!",
            view=ConfirmDeleteView(view.cog, view.channel_id, view.owner_id, channel),
            ephemeral=True,
        )


class ConfirmDeleteView(View):
    def __init__(self, cog, channel_id: int, owner_id: int, channel: discord.abc.GuildChannel):
        super().__init__(timeout=30)
        self.cog = cog
        self.channel_id = channel_id
        self.owner_id = owner_id
        self.channel = channel

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.owner_id

    @discord.ui.button(label="✅ Confirm Delete", style=discord.ButtonStyle.danger)
    async def confirm_delete(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("DELETE FROM voiceChannel WHERE voiceID = ?", (self.channel.id,))
                await db.commit()
            await self.channel.delete(reason="VC owner requested deletion")
            await interaction.followup.send("✅ Voice channel deleted!", ephemeral=True)
        except Exception:
            await interaction.followup.send("❌ Failed to delete channel!", ephemeral=True)

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_delete(self, interaction: discord.Interaction, button: Button):
        await interaction.response.edit_message(content="Deletion cancelled.", view=None)