import logging

import discord
from discord import app_commands

from core.cog import EmberCog

from .helpers import set_service, sha256_user_id
from .services.confessions_service import ConfessionsService

log = logging.getLogger("ember.confessions.ban")


class ConfessBan(EmberCog, name="ConfessBan"):
    def __init__(self, bot):
        super().__init__(bot)
        self.service: ConfessionsService | None = None

    async def cog_load(self):
        # Get service from cog (will be set by main cog)
        if hasattr(self.bot, "confessions_service"):
            self.service = self.bot.confessions_service
            set_service(self.service)  # Also set global for helpers
        log.info("ConfessBan cog loaded with service")

    def has_permission(self, interaction: discord.Interaction) -> bool:
        return interaction.user.guild_permissions.administrator

    async def _resolve_target(
        self, interaction: discord.Interaction, target: str
    ) -> tuple[int | None, discord.User | None, str | None]:
        cleaned = target.strip().lstrip("<@!").rstrip(">")

        if cleaned.isdigit():
            uid = int(cleaned)
            try:
                user = await self.bot.fetch_user(uid)
                return uid, user, None
            except discord.NotFound:
                return uid, None, None
            except Exception as e:
                return None, None, f"Could not fetch user: {e}"

        if len(cleaned) == 64 and all(c in "0123456789abcdef" for c in cleaned.lower()):
            if self.service is None:
                return None, None, "❌ Service not initialized"
            uid = await self.service.lookup_user_id_from_hash(cleaned.lower())
            if uid is None:
                return (
                    None,
                    None,
                    "❌ No user found with that hash. "
                    "They may not have any confessions or replies on record.",
                )
            try:
                user = await self.bot.fetch_user(uid)
            except Exception:
                user = None
            return uid, user, None

        return (
            None,
            None,
            "❌ Invalid target. Provide a **user mention**, **user ID**, "
            "or the **SHA-256 hash** from the mod log.",
        )

    confessban_group = app_commands.Group(
        name="confessban",
        description="Manage confession bans",
        default_permissions=discord.Permissions(administrator=True),
    )

    @confessban_group.command(name="ban", description="Ban a user from submitting confessions")
    @app_commands.describe(
        target="User mention, user ID, or SHA-256 hash from the mod log",
        reason="Reason for the ban (optional)",
    )
    async def confessban_ban(
        self,
        interaction: discord.Interaction,
        target: str,
        reason: str | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        if self.service is None:
            return await interaction.followup.send("❌ Service not initialized.", ephemeral=True)

        user_id, user, error = await self._resolve_target(interaction, target)
        if error:
            return await interaction.followup.send(error, ephemeral=True)
        if user_id is None:
            return await interaction.followup.send("❌ Could not resolve target user.", ephemeral=True)

        user_display = f"{user.mention} ({user.id})" if user else f"User ID `{user_id}`"
        try:
            success = await self.service.ban_user(user_id, reason)
            if not success:
                return await interaction.followup.send("❌ Failed to ban user.", ephemeral=True)

            children = [
                discord.ui.TextDisplay("## ✅ User Banned from Confessions"),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                discord.ui.TextDisplay(f"**User:** {user_display}"),
                discord.ui.TextDisplay(f"**Banned by:** {interaction.user.mention}"),
            ]
            if reason:
                children.append(discord.ui.TextDisplay(f"**Reason:** {reason}"))
            children.append(discord.ui.TextDisplay(f"**SHA-256 hash:** `{sha256_user_id(user_id)}`"))
            children.append(
                discord.ui.TextDisplay(f"-# Banned at <t:{int(discord.utils.utcnow().timestamp())}:F>")
            )
            view = discord.ui.LayoutView(timeout=None)
            view.add_item(discord.ui.Container(*children, accent_color=discord.Color.from_str("#ED4245")))
            await interaction.followup.send(view=view)
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @confessban_group.command(name="unban", description="Unban a user from submitting confessions")
    @app_commands.describe(target="User mention, user ID, or SHA-256 hash from the mod log")
    async def confessban_unban(self, interaction: discord.Interaction, target: str):
        await interaction.response.defer(ephemeral=True)
        if self.service is None:
            return await interaction.followup.send("❌ Service not initialized.", ephemeral=True)

        user_id, user, error = await self._resolve_target(interaction, target)
        if error:
            return await interaction.followup.send(error, ephemeral=True)
        if user_id is None:
            return await interaction.followup.send("❌ Could not resolve target user.", ephemeral=True)

        user_display = f"{user.mention} ({user.id})" if user else f"User ID `{user_id}`"
        try:
            success = await self.service.unban_user(user_id)
            if not success:
                return await interaction.followup.send(
                    "❌ User was not banned or failed to unban.", ephemeral=True
                )
            children = [
                discord.ui.TextDisplay("## ✅ User Unbanned from Confessions"),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                discord.ui.TextDisplay(f"**User:** {user_display}"),
                discord.ui.TextDisplay(f"**Unbanned by:** {interaction.user.mention}"),
                discord.ui.TextDisplay(
                    f"-# Unbanned at <t:{int(discord.utils.utcnow().timestamp())}:F>"
                ),
            ]
            view = discord.ui.LayoutView(timeout=None)
            view.add_item(discord.ui.Container(*children, accent_color=discord.Color.from_str("#57F287")))
            await interaction.followup.send(view=view)
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @confessban_group.command(name="list", description="List all users banned from confessions")
    async def confessban_list(self, interaction: discord.Interaction):
        if self.service is None:
            return await interaction.response.send_message(
                "❌ Service not initialized.", ephemeral=True
            )
        try:
            bans = await self.service.get_banned_users()
            if not bans:
                return await interaction.response.send_message(
                    "No users are currently banned.", ephemeral=True
                )

            children = [
                discord.ui.TextDisplay("## 🚫 Banned Users from Confessions"),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            ]
            for ban in bans:
                user_id = ban["user_id"]
                reason = ban["reason"]
                banned_at = ban["banned_at"]
                try:
                    user = await self.bot.fetch_user(user_id)
                    user_text = f"{user.mention} ({user.id})"
                except Exception:
                    user_text = f"Unknown User (`{user_id}`)"

                ban_info = f"**Banned on:** {banned_at}\n**Hash:** `{sha256_user_id(user_id)[:16]}…`"
                if reason:
                    ban_info += f"\n**Reason:** {reason}"
                children.append(discord.ui.TextDisplay(f"**{user_text}**\n{ban_info}"))

            children.append(
                discord.ui.TextDisplay(
                    f"-# Total: {len(bans)} user(s) • <t:{int(discord.utils.utcnow().timestamp())}:F>"
                )
            )
            view = discord.ui.LayoutView(timeout=None)
            view.add_item(discord.ui.Container(*children, accent_color=discord.Color.from_str("#FEE75C")))
            await interaction.response.send_message(view=view, ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)


async def setup(bot):
    await bot.add_cog(ConfessBan(bot))
