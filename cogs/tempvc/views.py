import discord
from discord.ui import Button, View, UserSelect
from .modals import RenameModal, LimitModal
from .config import JOIN_CHANNELS, DB_PATH, GAMING_DB_PATH
import aiosqlite
import time


class ControlPanel(View):
    """Base view for control panels. Interaction checks ensure only the owner can use."""

    def __init__(self, cog, channel_id: int, owner_id: int, message_id: int = None):
        # timeout=0 means the view never expires
        super().__init__(timeout=0)
        self.cog = cog
        self.channel_id = channel_id
        self.owner_id = owner_id
        self.message_id = message_id  # Store the message ID for persistence

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "❌ Only the VC owner can use these controls!", ephemeral=True
            )
            return False
        return True

    async def on_error(self, interaction: discord.Interaction, error: Exception, item):
        """Handle errors gracefully"""
        try:
            await interaction.response.send_message(
                f"❌ An error occurred: {str(error)}", ephemeral=True
            )
        except Exception:
            pass
        print(f"[TempVoice] View error: {error}")

    async def update_database(self, **kwargs):
        """Update the correct channel table using voiceID as key."""
        if not kwargs:
            return
        db_path = getattr(self, "db_path", DB_PATH)
        # Determine table: gaming channels live in gamingVoiceChannel
        is_gaming = getattr(self, "is_gaming", False)
        table = "gamingVoiceChannel" if is_gaming else "voiceChannel"
        async with aiosqlite.connect(db_path) as db:
            set_clause = ", ".join([f"{key} = ?" for key in kwargs.keys()])
            values = list(kwargs.values()) + [self.channel_id]
            await db.execute(
                f"UPDATE {table} SET {set_clause} WHERE voiceID = ?", values
            )
            await db.commit()

    @staticmethod
    def _has_allow(overwrite: discord.PermissionOverwrite) -> bool:
        return any(
            getattr(overwrite, p) is True
            for p in ("connect", "view_channel", "speak", "stream")
        )

    @staticmethod
    def _has_deny(overwrite: discord.PermissionOverwrite) -> bool:
        return any(
            getattr(overwrite, p) is False
            for p in ("connect", "view_channel", "speak", "stream")
        )


class BasicControlsView(ControlPanel):
    def __init__(
        self,
        cog,
        channel_id: int,
        owner_id: int,
        is_gaming: bool = False,
        message_id: int = None,
    ):
        super().__init__(cog, channel_id, owner_id, message_id)
        self.is_gaming = is_gaming
        self.db_path = GAMING_DB_PATH if is_gaming else DB_PATH

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

        # Generate base for custom IDs that will survive restarts
        # Use a consistent format that doesn't rely on message_id
        base_id = f"{channel_id}_{owner_id}_{'g' if is_gaming else 'm'}"

        # Row 0: Basic Controls (Name, Limit, Delete)
        name_btn = NameButton(is_gaming=is_gaming)
        name_btn.custom_id = f"name_{base_id}"

        limit_btn = LimitButton(is_gaming=is_gaming)
        limit_btn.custom_id = f"limit_{base_id}"

        delete_btn = DeleteButton(is_gaming=is_gaming)
        delete_btn.custom_id = f"delete_{base_id}"

        self.add_item(name_btn)
        self.add_item(limit_btn)
        self.add_item(delete_btn)

        # Row 1: Privacy & Actions
        lock_btn = LockButton(is_locked=is_locked, is_gaming=is_gaming)
        lock_btn.custom_id = f"lock_{base_id}"

        hide_btn = HideButton(is_hidden=is_hidden, is_gaming=is_gaming)
        hide_btn.custom_id = f"hide_{base_id}"

        kick_btn = KickButton(is_gaming=is_gaming)
        kick_btn.custom_id = f"kick_{base_id}"

        self.add_item(lock_btn)
        self.add_item(hide_btn)
        self.add_item(kick_btn)

        # Row 2: Member Management
        trust_btn = TrustButton(is_gaming=is_gaming)
        trust_btn.custom_id = f"trust_{base_id}"

        untrust_btn = UntrustButton(is_gaming=is_gaming)
        untrust_btn.custom_id = f"untrust_{base_id}"

        block_btn = BlockButton(is_gaming=is_gaming)
        block_btn.custom_id = f"block_{base_id}"

        unblock_btn = UnblockButton(is_gaming=is_gaming)
        unblock_btn.custom_id = f"unblock_{base_id}"

        self.add_item(trust_btn)
        self.add_item(untrust_btn)
        self.add_item(block_btn)
        self.add_item(unblock_btn)

        # Row 3: Advanced
        transfer_btn = TransferButton(is_gaming=is_gaming)
        transfer_btn.custom_id = f"transfer_{base_id}"

        claim_btn = ClaimButton(is_gaming=is_gaming)
        claim_btn.custom_id = f"claim_{base_id}"

        self.add_item(transfer_btn)
        self.add_item(claim_btn)


# --------------------- BUTTON CLASSES ---------------------
# Update all button classes to accept is_gaming parameter


class NameButton(Button):
    def __init__(self, is_gaming: bool = False):
        super().__init__(label="👤 Name", style=discord.ButtonStyle.secondary, row=0)
        self.is_gaming = is_gaming

    async def callback(self, interaction: discord.Interaction):
        view: ControlPanel = self.view
        channel = interaction.guild.get_channel(view.channel_id)
        if not channel:
            await interaction.response.send_message(
                "❌ Channel not found!", ephemeral=True
            )
            return
        modal = RenameModal(view.cog, channel, is_gaming=self.is_gaming)
        await interaction.response.send_modal(modal)


class LimitButton(Button):
    def __init__(self, is_gaming: bool = False):
        super().__init__(label="👥 Limit", style=discord.ButtonStyle.secondary, row=0)
        self.is_gaming = is_gaming

    async def callback(self, interaction: discord.Interaction):
        view: ControlPanel = self.view
        channel = interaction.guild.get_channel(view.channel_id)
        if not channel:
            await interaction.response.send_message(
                "❌ Channel not found!", ephemeral=True
            )
            return
        modal = LimitModal(view.cog, channel, is_gaming=self.is_gaming)
        await interaction.response.send_modal(modal)


class DeleteButton(Button):
    def __init__(self, is_gaming: bool = False):
        super().__init__(label="🗑️ Delete", style=discord.ButtonStyle.danger, row=0)
        self.is_gaming = is_gaming

    async def callback(self, interaction: discord.Interaction):
        view: ControlPanel = self.view
        channel = interaction.guild.get_channel(view.channel_id)
        if not channel:
            await interaction.response.send_message(
                "❌ Channel not found!", ephemeral=True
            )
            return

        # Create confirmation view
        confirm_view = View(timeout=60)

        async def confirm_callback(confirm_interaction: discord.Interaction):
            if confirm_interaction.user.id != view.owner_id:
                await confirm_interaction.response.send_message(
                    "❌ Only the owner can delete!", ephemeral=True
                )
                return

            await confirm_interaction.response.defer(ephemeral=True)
            try:
                db_path = GAMING_DB_PATH if self.is_gaming else DB_PATH
                table = "gamingVoiceChannel" if self.is_gaming else "voiceChannel"
                async with aiosqlite.connect(db_path) as db:
                    await db.execute(
                        f"DELETE FROM {table} WHERE voiceID = ?", (channel.id,)
                    )
                    await db.commit()
                await channel.delete(reason="VC owner requested deletion")
                await confirm_interaction.followup.send(
                    "✅ Voice channel deleted!", ephemeral=True
                )
            except Exception as e:
                print(f"Error deleting channel: {e}")
                await confirm_interaction.followup.send(
                    "❌ Failed to delete channel!", ephemeral=True
                )

        async def cancel_callback(cancel_interaction: discord.Interaction):
            await cancel_interaction.response.edit_message(
                content="Deletion cancelled.", view=None
            )

        confirm_button = Button(
            label="✅ Confirm Delete", style=discord.ButtonStyle.danger
        )
        confirm_button.callback = confirm_callback
        cancel_button = Button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
        cancel_button.callback = cancel_callback

        confirm_view.add_item(confirm_button)
        confirm_view.add_item(cancel_button)

        await interaction.response.send_message(
            "⚠️ Are you sure you want to delete this voice channel? This action cannot be undone!",
            view=confirm_view,
            ephemeral=True,
        )


class LockButton(Button):
    def __init__(self, is_locked: bool = False, is_gaming: bool = False):
        label = "🔓 Unlock" if is_locked else "🔒 Lock"
        super().__init__(label=label, style=discord.ButtonStyle.secondary, row=1)
        self.is_locked = is_locked
        self.is_gaming = is_gaming

    async def callback(self, interaction: discord.Interaction):
        view: ControlPanel = self.view
        channel = interaction.guild.get_channel(view.channel_id)
        if not channel:
            await interaction.response.send_message(
                "❌ Channel not found!", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        current_overwrites = channel.overwrites_for(interaction.guild.default_role)

        if self.is_locked:
            # Currently locked -> Unlock
            await channel.set_permissions(
                interaction.guild.default_role,
                connect=True,
                view_channel=current_overwrites.view_channel,
            )

            # Remove per-member allows (preserve owner)
            for member in list(channel.guild.members):
                if member.id == view.owner_id:
                    continue
                overwrite = channel.overwrites_for(member)
                if overwrite and (
                    overwrite.connect is True or overwrite.view_channel is True
                ):
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
                view_channel=current_overwrites.view_channel,
            )

            # Grant current members explicit access
            for member in list(channel.members):
                try:
                    await channel.set_permissions(
                        member, connect=True, view_channel=True
                    )
                except Exception:
                    continue

            await view.update_database(locked=True)
            action_msg = "🔒 Channel locked! Current members kept access."

        # Refresh the view with updated button states
        new_view = BasicControlsView(
            view.cog, view.channel_id, view.owner_id,
            is_gaming=self.is_gaming, message_id=view.message_id
        )
        await interaction.edit_original_response(view=new_view)
        await interaction.followup.send(action_msg, ephemeral=True)


class HideButton(Button):
    def __init__(self, is_hidden: bool = False, is_gaming: bool = False):
        label = "👀 Unhide" if is_hidden else "🕵️ Hide"
        super().__init__(label=label, style=discord.ButtonStyle.secondary, row=1)
        self.is_hidden = is_hidden
        self.is_gaming = is_gaming

    async def callback(self, interaction: discord.Interaction):
        view: ControlPanel = self.view
        channel = interaction.guild.get_channel(view.channel_id)
        if not channel:
            await interaction.response.send_message(
                "❌ Channel not found!", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        current_overwrites = channel.overwrites_for(interaction.guild.default_role)

        if self.is_hidden:
            # Currently hidden -> Unhide
            await channel.set_permissions(
                interaction.guild.default_role,
                view_channel=True,
                connect=current_overwrites.connect,
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
                connect=current_overwrites.connect,
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
        new_view = BasicControlsView(
            view.cog, view.channel_id, view.owner_id,
            is_gaming=self.is_gaming, message_id=view.message_id
        )
        await interaction.edit_original_response(view=new_view)
        await interaction.followup.send(action_msg, ephemeral=True)


class KickButton(Button):
    def __init__(self, is_gaming: bool = False):
        super().__init__(label="👢 Kick", style=discord.ButtonStyle.secondary, row=1)
        self.is_gaming = is_gaming

    async def callback(self, interaction: discord.Interaction):
        view: ControlPanel = self.view
        channel = interaction.guild.get_channel(view.channel_id)
        if not channel:
            await interaction.response.send_message(
                "❌ Channel not found!", ephemeral=True
            )
            return

        # Get users in VC (excluding owner)
        vc_users = [m for m in channel.members if m.id != view.owner_id]

        if not vc_users:
            await interaction.response.send_message(
                "❌ No users in VC to kick!", ephemeral=True
            )
            return

        # Create user select menu
        select_view = View(timeout=60)

        select = UserSelect(
            placeholder="Select users to kick...",
            min_values=1,
            max_values=min(25, len(vc_users)),
            row=0,
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
                await select_interaction.followup.send(
                    f"👢 Kicked: {', '.join(successful)}", ephemeral=True
                )
            else:
                await select_interaction.followup.send(
                    "❌ Failed to kick users!", ephemeral=True
                )

        select.callback = select_callback
        select_view.add_item(select)

        # Add cancel button
        cancel_button = Button(
            label="❌ Cancel", style=discord.ButtonStyle.secondary, row=1
        )

        async def cancel_callback(cancel_interaction: discord.Interaction):
            await cancel_interaction.response.edit_message(
                content="Kick cancelled.", view=None
            )

        cancel_button.callback = cancel_callback
        select_view.add_item(cancel_button)

        await interaction.response.send_message(
            "Select users to kick:", view=select_view, ephemeral=True
        )


class TrustButton(Button):
    def __init__(self, is_gaming: bool = False):
        super().__init__(label="✅ Trust", style=discord.ButtonStyle.secondary, row=2)
        self.is_gaming = is_gaming

    async def callback(self, interaction: discord.Interaction):
        view: ControlPanel = self.view
        channel = interaction.guild.get_channel(view.channel_id)
        if not channel:
            await interaction.response.send_message(
                "❌ Channel not found!", ephemeral=True
            )
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
            await interaction.response.send_message(
                "✅ All users already have access permissions!", ephemeral=True
            )
            return

        select = UserSelect(
            placeholder="Select users to trust...",
            min_values=1,
            max_values=min(25, len(available_users)),
            row=0,
        )

        async def select_callback(select_interaction: discord.Interaction):
            await select_interaction.response.defer(ephemeral=True)
            successful = []

            for member in select.values:
                try:
                    await channel.set_permissions(
                        member, connect=True, view_channel=True, speak=True, stream=True
                    )
                    successful.append(member.display_name)
                except Exception as e:
                    print(f"Error trusting {member.id}: {e}")

            if successful:
                await select_interaction.followup.send(
                    f"✅ Granted access to: {', '.join(successful)}", ephemeral=True
                )
            else:
                await select_interaction.followup.send(
                    "❌ Failed to grant access!", ephemeral=True
                )

        select.callback = select_callback
        select_view.add_item(select)

        # Add cancel button
        cancel_button = Button(
            label="❌ Cancel", style=discord.ButtonStyle.secondary, row=1
        )

        async def cancel_callback(cancel_interaction: discord.Interaction):
            await cancel_interaction.response.edit_message(
                content="Trust cancelled.", view=None
            )

        cancel_button.callback = cancel_callback
        select_view.add_item(cancel_button)

        await interaction.response.send_message(
            "Select users to trust:", view=select_view, ephemeral=True
        )


class UntrustButton(Button):
    def __init__(self, is_gaming: bool = False):
        super().__init__(label="❌ Untrust", style=discord.ButtonStyle.secondary, row=2)
        self.is_gaming = is_gaming

    async def callback(self, interaction: discord.Interaction):
        view: ControlPanel = self.view
        channel = interaction.guild.get_channel(view.channel_id)
        if not channel:
            await interaction.response.send_message(
                "❌ Channel not found!", ephemeral=True
            )
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
            await interaction.response.send_message(
                "❌ No users have trusted access!", ephemeral=True
            )
            return

        select = UserSelect(
            placeholder="Select users to untrust...",
            min_values=1,
            max_values=min(25, len(trusted_users)),
            row=0,
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
                await select_interaction.followup.send(
                    f"❌ Revoked access from: {', '.join(successful)}", ephemeral=True
                )
            else:
                await select_interaction.followup.send(
                    "❌ Failed to revoke access!", ephemeral=True
                )

        select.callback = select_callback
        select_view.add_item(select)

        # Add cancel button
        cancel_button = Button(
            label="❌ Cancel", style=discord.ButtonStyle.secondary, row=1
        )

        async def cancel_callback(cancel_interaction: discord.Interaction):
            await cancel_interaction.response.edit_message(
                content="Untrust cancelled.", view=None
            )

        cancel_button.callback = cancel_callback
        select_view.add_item(cancel_button)

        await interaction.response.send_message(
            "Select users to untrust:", view=select_view, ephemeral=True
        )


class BlockButton(Button):
    def __init__(self, is_gaming: bool = False):
        super().__init__(label="🚫 Block", style=discord.ButtonStyle.secondary, row=2)
        self.is_gaming = is_gaming

    async def callback(self, interaction: discord.Interaction):
        view: ControlPanel = self.view
        channel = interaction.guild.get_channel(view.channel_id)
        if not channel:
            await interaction.response.send_message(
                "❌ Channel not found!", ephemeral=True
            )
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
            await interaction.response.send_message(
                "✅ All users are already blocked!", ephemeral=True
            )
            return

        select = UserSelect(
            placeholder="Select users to block...",
            min_values=1,
            max_values=min(25, len(available_users)),
            row=0,
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
                    await channel.set_permissions(
                        member,
                        connect=False,
                        view_channel=False,
                        speak=False,
                        stream=False,
                    )
                    successful.append(member.display_name)
                except Exception as e:
                    print(f"Error blocking {member.id}: {e}")

            if successful:
                await select_interaction.followup.send(
                    f"🚫 Blocked: {', '.join(successful)}", ephemeral=True
                )
            else:
                await select_interaction.followup.send(
                    "❌ Failed to block users!", ephemeral=True
                )

        select.callback = select_callback
        select_view.add_item(select)

        # Add cancel button
        cancel_button = Button(
            label="❌ Cancel", style=discord.ButtonStyle.secondary, row=1
        )

        async def cancel_callback(cancel_interaction: discord.Interaction):
            await cancel_interaction.response.edit_message(
                content="Block cancelled.", view=None
            )

        cancel_button.callback = cancel_callback
        select_view.add_item(cancel_button)

        await interaction.response.send_message(
            "Select users to block:", view=select_view, ephemeral=True
        )


class UnblockButton(Button):
    def __init__(self, is_gaming: bool = False):
        super().__init__(label="✅ Unblock", style=discord.ButtonStyle.secondary, row=3)
        self.is_gaming = is_gaming

    async def callback(self, interaction: discord.Interaction):
        view: ControlPanel = self.view
        channel = interaction.guild.get_channel(view.channel_id)
        if not channel:
            await interaction.response.send_message(
                "❌ Channel not found!", ephemeral=True
            )
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
            await interaction.response.send_message(
                "✅ No users are blocked!", ephemeral=True
            )
            return

        select = UserSelect(
            placeholder="Select users to unblock...",
            min_values=1,
            max_values=min(25, len(blocked_users)),
            row=0,
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
                await select_interaction.followup.send(
                    f"✅ Unblocked: {', '.join(successful)}", ephemeral=True
                )
            else:
                await select_interaction.followup.send(
                    "❌ Failed to unblock users!", ephemeral=True
                )

        select.callback = select_callback
        select_view.add_item(select)

        # Add cancel button
        cancel_button = Button(
            label="❌ Cancel", style=discord.ButtonStyle.secondary, row=1
        )

        async def cancel_callback(cancel_interaction: discord.Interaction):
            await cancel_interaction.response.edit_message(
                content="Unblock cancelled.", view=None
            )

        cancel_button.callback = cancel_callback
        select_view.add_item(cancel_button)

        await interaction.response.send_message(
            "Select users to unblock:", view=select_view, ephemeral=True
        )


class TransferButton(Button):
    def __init__(self, is_gaming: bool = False):
        super().__init__(
            label="🔄 Transfer", style=discord.ButtonStyle.secondary, row=3
        )
        self.is_gaming = is_gaming

    async def callback(self, interaction: discord.Interaction):
        view: ControlPanel = self.view
        channel = interaction.guild.get_channel(view.channel_id)
        if not channel:
            await interaction.response.send_message(
                "❌ Channel not found!", ephemeral=True
            )
            return

        # Get users currently in the VC (excluding owner and bots)
        vc_users = [m for m in channel.members if m.id != view.owner_id and not m.bot]

        if not vc_users:
            await interaction.response.send_message(
                "❌ No users in the voice channel to transfer ownership to!",
                ephemeral=True,
            )
            return

        # Create user select view
        select_view = View(timeout=60)

        select = UserSelect(
            placeholder="Select a user in VC to transfer ownership...",
            min_values=1,
            max_values=1,
            row=0,
        )

        async def select_callback(select_interaction: discord.Interaction):
            await select_interaction.response.defer(ephemeral=True)

            new_owner = select.values[0]

            # Verify they're still in the VC
            if new_owner not in channel.members:
                await select_interaction.followup.send(
                    "❌ That user is no longer in the voice channel!", ephemeral=True
                )
                return

            try:
                db_path = GAMING_DB_PATH if self.is_gaming else DB_PATH
                table = "gamingVoiceChannel" if self.is_gaming else "voiceChannel"
                # Update database
                async with aiosqlite.connect(db_path) as db:
                    await db.execute(
                        f"UPDATE {table} SET userID = ? WHERE voiceID = ?",
                        (new_owner.id, channel.id),
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
                    color=discord.Color.blue(),
                )
                await channel.send(embed=embed)

                await select_interaction.followup.send(
                    f"✅ Successfully transferred ownership to {new_owner.mention}!",
                    ephemeral=True,
                )

                # Refresh the original message
                try:
                    new_view = BasicControlsView(
                        view.cog,
                        view.channel_id,
                        new_owner.id,
                        is_gaming=self.is_gaming,
                        message_id=view.message_id,
                    )
                    await interaction.edit_original_response(view=new_view)
                except Exception:
                    pass

            except Exception as e:
                print(f"Error transferring ownership: {e}")
                await select_interaction.followup.send(
                    "❌ Failed to transfer ownership. Please try again.", ephemeral=True
                )

        select.callback = select_callback
        select_view.add_item(select)

        # Add cancel button
        cancel_button = Button(
            label="❌ Cancel", style=discord.ButtonStyle.secondary, row=1
        )

        async def cancel_callback(cancel_interaction: discord.Interaction):
            await cancel_interaction.response.edit_message(
                content="Transfer cancelled.", view=None
            )

        cancel_button.callback = cancel_callback
        select_view.add_item(cancel_button)

        await interaction.response.send_message(
            "👥 Select a user from the voice channel to transfer ownership to:",
            view=select_view,
            ephemeral=True,
        )


class ClaimButton(Button):
    def __init__(self, is_gaming: bool = False):
        super().__init__(label="🎯 Claim", style=discord.ButtonStyle.secondary, row=3)
        self.is_gaming = is_gaming

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Override: Don't require ownership for this button, just require being in the VC"""
        view: ControlPanel = self.view

        # Must be in a voice channel
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message(
                "❌ You must be in a voice channel to claim it!", ephemeral=True
            )
            return False

        # Must be in this specific voice channel
        if interaction.user.voice.channel.id != view.channel_id:
            await interaction.response.send_message(
                "❌ You can only claim the channel you're currently in!", ephemeral=True
            )
            return False

        return True

    async def callback(self, interaction: discord.Interaction):
        view: ControlPanel = self.view
        await interaction.response.defer(ephemeral=True)

        # Get the channel
        channel = interaction.user.voice.channel

        # Check if user is trying to claim a generator channel
        if channel.id in JOIN_CHANNELS:
            await interaction.followup.send(
                "❌ You cannot claim a generator channel!", ephemeral=True
            )
            return

        db_path = GAMING_DB_PATH if self.is_gaming else DB_PATH
        table = "gamingVoiceChannel" if self.is_gaming else "voiceChannel"
        async with aiosqlite.connect(db_path) as db:
            # Check if channel exists in database
            async with db.execute(
                f"SELECT userID FROM {table} WHERE voiceID = ?", (channel.id,)
            ) as cursor:
                data = await cursor.fetchone()

            if data:
                # Channel exists in database
                owner_id = data[0]
                owner = interaction.guild.get_member(owner_id)

                # Owner is currently in the VC
                if owner and owner in channel.members:
                    await interaction.followup.send(
                        f"❌ {owner.display_name} is currently in this VC. You can't claim it while they're here!",
                        ephemeral=True,
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
                        ephemeral=True,
                    )
                    return

                cog.creation_cooldowns[claim_lock_key] = current_time

                try:
                    # First come, first serve: Update ownership immediately
                    await db.execute(
                        f"UPDATE {table} SET userID = ? WHERE voiceID = ?",
                        (interaction.user.id, channel.id),
                    )
                    await db.commit()

                    # Update permissions
                    if owner:
                        await channel.set_permissions(owner, manage_channels=False)

                    await channel.set_permissions(
                        interaction.user, manage_channels=True
                    )

                    # Remove from owner leave tracking
                    cog.owner_leave_times.pop(channel.id, None)

                    # Send success message in channel
                    embed = discord.Embed(
                        title="🎯 VC Ownership Claimed",
                        description=f"{interaction.user.mention} has claimed ownership of this voice channel!\n"
                        f"First come, first served!",
                        color=discord.Color.green(),
                    )
                    await channel.send(embed=embed)

                    # Send success message to user
                    await interaction.followup.send(
                        f"✅ You have successfully claimed ownership of {channel.mention}!",
                        ephemeral=True,
                    )

                    # Refresh the control panel for the new owner, preserving message_id
                    try:
                        async for msg in channel.history(limit=10):
                            if (
                                msg.author == interaction.guild.me
                                and msg.embeds
                            ):
                                if (
                                    "Control Panel" in msg.embeds[0].title
                                    or "Voice Channel Controls"
                                    in msg.embeds[0].title
                                ):
                                    new_view = BasicControlsView(
                                        view.cog,
                                        view.channel_id,
                                        interaction.user.id,
                                        is_gaming=self.is_gaming,
                                        message_id=msg.id,
                                    )
                                    await msg.edit(view=new_view)
                                    break
                    except Exception as e:
                        print(f"[TempVoice] Error refreshing control panel: {e}")

                except Exception as e:
                    print(f"[TempVoice] Error during claim transfer: {e}")
                    # Rollback the database change
                    await db.execute(
                        f"UPDATE {table} SET userID = ? WHERE voiceID = ?",
                        (owner_id, channel.id),
                    )
                    await db.commit()
                    await interaction.followup.send(
                        "❌ Failed to complete claim. Please try again.", ephemeral=True
                    )
                finally:
                    cog.creation_cooldowns.pop(claim_lock_key, None)

            else:
                # Channel doesn't exist in database (shouldn't happen, but handle it)
                limit = channel.user_limit

                cog = view.cog
                current_time = time.time()
                claim_lock_key = f"claim_{channel.id}"

                last_claim_attempt = cog.creation_cooldowns.get(claim_lock_key, 0)
                if current_time - last_claim_attempt < 2:
                    await interaction.followup.send(
                        "⏳ Someone else is attempting to claim this channel. Please wait...",
                        ephemeral=True,
                    )
                    return

                cog.creation_cooldowns[claim_lock_key] = current_time

                try:
                    # Create new database entry
                    await db.execute(
                        """INSERT INTO voiceChannel 
                           (userID, voiceID, channelName, channelLimit, guildID, vcType, channelNumber) 
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (
                            interaction.user.id,
                            channel.id,
                            channel.name,
                            limit,
                            interaction.guild.id,
                            "Other",
                            1,
                        ),
                    )
                    await db.commit()

                    # Set permissions
                    await channel.set_permissions(
                        interaction.user, manage_channels=True
                    )

                    # Send success message
                    embed = discord.Embed(
                        title="🎯 VC Ownership Claimed",
                        description=f"{interaction.user.mention} has claimed ownership of this voice channel!",
                        color=discord.Color.blue(),
                    )
                    await channel.send(embed=embed)

                    await interaction.followup.send(
                        f"✅ You have successfully claimed {channel.mention}!",
                        ephemeral=True,
                    )

                except Exception as e:
                    print(f"[TempVoice] Error during claim creation: {e}")
                    await interaction.followup.send(
                        "❌ Failed to claim channel. Please try again.", ephemeral=True
                    )
                finally:
                    cog.creation_cooldowns.pop(claim_lock_key, None)