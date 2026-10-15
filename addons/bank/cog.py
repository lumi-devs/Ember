from __future__ import annotations

import discord
from discord import app_commands

from core import EmberCog, PermissionLevel, UserFeedbackError, requires
from core.bank import (
    can_spend,
    deposit,
    get_balance,
    get_currency_name,
    get_leaderboard,
    get_leaderboard_position,
    set_bank_name,
    set_currency_name,
    set_max_balance,
    transfer,
    withdraw,
)
from utils.cards import (
    make_info_card,
    make_list_card,
    make_success_card,
)


class Bank(EmberCog, name="Bank"):
    """Economy system with wallet balances and guild bank."""

    bank = app_commands.Group(name="bank", description="Economy system commands")

    @bank.command(name="balance", description="Check your or another user's balance")
    @app_commands.describe(user="User to check (default: yourself)")
    async def balance(
        self, interaction: discord.Interaction, user: discord.User | None = None
    ) -> None:
        if not interaction.guild:
            raise UserFeedbackError("Balance can only be checked in a server.")

        target = user or interaction.user
        bal = await get_balance(target, interaction.guild)
        currency = await get_currency_name(interaction.guild)
        await interaction.response.send_message(
            view=make_success_card(
                f"💰 {target.display_name}'s Balance", f"**{bal:,}** {currency}"
            ),
            ephemeral=False,
        )

    @bank.command(name="deposit", description="Deposit money into your wallet")
    @app_commands.describe(amount="Amount to deposit")
    async def deposit_cmd(self, interaction: discord.Interaction, amount: int) -> None:
        if not interaction.guild:
            raise UserFeedbackError("Deposits can only be made in a server.")
        if amount <= 0:
            raise UserFeedbackError("Amount must be positive.")

        new_bal = await deposit(interaction.user, interaction.guild, amount)
        currency = await get_currency_name(interaction.guild)
        await interaction.response.send_message(
            view=make_success_card(
                "✅ Deposited",
                f"**+{amount:,}** {currency}\nNew balance: **{new_bal:,}** {currency}",
            ),
            ephemeral=False,
        )

    @bank.command(name="withdraw", description="Withdraw money from your wallet")
    @app_commands.describe(amount="Amount to withdraw")
    async def withdraw_cmd(self, interaction: discord.Interaction, amount: int) -> None:
        if not interaction.guild:
            raise UserFeedbackError("Withdrawals can only be made in a server.")
        if amount <= 0:
            raise UserFeedbackError("Amount must be positive.")

        if not await can_spend(interaction.user, interaction.guild, amount):
            bal = await get_balance(interaction.user, interaction.guild)
            raise UserFeedbackError(f"You only have **{bal:,}**.")

        new_bal = await withdraw(interaction.user, interaction.guild, amount)
        currency = await get_currency_name(interaction.guild)
        await interaction.response.send_message(
            view=make_success_card(
                "✅ Withdrawn",
                f"**-{amount:,}** {currency}\nNew balance: **{new_bal:,}** {currency}",
            ),
            ephemeral=False,
        )

    @bank.command(name="pay", description="Transfer money to another user")
    @app_commands.describe(recipient="User to pay", amount="Amount to transfer")
    async def pay(
        self, interaction: discord.Interaction, recipient: discord.User, amount: int
    ) -> None:
        if not interaction.guild:
            raise UserFeedbackError("Transfers can only be made in a server.")
        if amount <= 0:
            raise UserFeedbackError("Amount must be positive.")
        if interaction.user.id == recipient.id:
            raise UserFeedbackError("You can't pay yourself.")

        if not await can_spend(interaction.user, interaction.guild, amount):
            bal = await get_balance(interaction.user, interaction.guild)
            raise UserFeedbackError(f"You only have **{bal:,}**.")

        await transfer(interaction.user, recipient, interaction.guild, amount)
        currency = await get_currency_name(interaction.guild)
        await interaction.response.send_message(
            view=make_success_card(
                "✅ Transferred", f"**{amount:,}** {currency} to {recipient.mention}"
            ),
            ephemeral=False,
        )

    @bank.command(name="leaderboard", description="Show top balances in this server")
    @app_commands.describe(limit="Number of entries (default 10)")
    async def leaderboard(self, interaction: discord.Interaction, limit: int = 10) -> None:
        if not interaction.guild:
            raise UserFeedbackError("Leaderboard can only be viewed in a server.")

        rows = await get_leaderboard(interaction.guild, limit)
        if not rows:
            await interaction.response.send_message(
                view=make_info_card("📊 Leaderboard", "No data yet."),
                ephemeral=False,
            )
            return

        currency = await get_currency_name(interaction.guild)
        lines = []
        for idx, row in enumerate(rows, start=1):
            user_id = row["user_id"]
            balance = row["balance"]
            member = interaction.guild.get_member(user_id)
            name = member.display_name if member else f"User {user_id}"
            lines.append(f"{idx}. **{name}** — {balance:,} {currency}")

        await interaction.response.send_message(
            view=make_list_card(f"🏆 Top {len(lines)} Balances", lines, per_page=10),
            ephemeral=False,
        )

    @bank.command(name="rank", description="Check your rank on the leaderboard")
    async def rank(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            raise UserFeedbackError("Rank can only be checked in a server.")

        rank_num = await get_leaderboard_position(interaction.user, interaction.guild)
        await interaction.response.send_message(
            view=make_info_card("📈 Your Rank", f"You are **#{rank_num}** on the leaderboard."),
            ephemeral=False,
        )

    @bank.command(name="setcurrency", description="Set the currency name for this server")
    @app_commands.describe(name="Currency name (e.g. credits, coins)")
    @requires(PermissionLevel.ADMIN)
    async def setcurrency(self, interaction: discord.Interaction, name: str) -> None:
        await set_currency_name(interaction.guild, name)
        await interaction.response.send_message(
            view=make_success_card("✅ Currency Set", f"Currency is now **{name}**."),
            ephemeral=True,
        )

    @bank.command(name="setmaxbalance", description="Set the maximum balance for this server")
    @app_commands.describe(amount="Maximum balance (integer)")
    @requires(PermissionLevel.ADMIN)
    async def setmaxbalance(self, interaction: discord.Interaction, amount: int) -> None:
        if amount <= 0:
            raise UserFeedbackError("Max balance must be positive.")

        await set_max_balance(interaction.guild.id, amount)
        await interaction.response.send_message(
            view=make_success_card("✅ Max Balance Set", f"Maximum balance is now **{amount:,}**."),
            ephemeral=True,
        )

    @bank.command(name="setbankname", description="Set the guild bank name")
    @app_commands.describe(name="Bank name")
    @requires(PermissionLevel.ADMIN)
    async def setbankname(self, interaction: discord.Interaction, name: str) -> None:
        await set_bank_name(interaction.guild.id, name)
        await interaction.response.send_message(
            view=make_success_card("✅ Bank Name Set", f"Bank name is now **{name}**."),
            ephemeral=True,
        )

    async def delete_data_for_user(self, user_id: int, guild_id: int | None) -> dict:
        """Delete user's bank data (all balances across all guilds)."""
        from core.bank import _get_db

        db = await _get_db()
        await db.delete_user(user_id)
        return {"deleted_balances": "all guilds"}

    async def export_data_for_user(self, user_id: int, guild_id: int | None) -> dict:
        """Export user's bank data."""
        from core.bank import _get_db

        db = await _get_db()
        rows = await db.store.find("accounts", user_id=user_id)
        data = []
        for row in rows:
            guild_id_row = row.get("guild_id")
            bal = row.get("balance", 0)
            entry = {
                "guild_id": guild_id_row,
                "balance": bal,
            }
            # Include guild settings if specific guild
            if guild_id == guild_id_row:
                settings = await db.get_guild_settings(guild_id_row)
                entry["guild_settings"] = settings
            data.append(entry)
        return {"balances": data, "total_guilds": len(data)}


setup = Bank.make_setup()
