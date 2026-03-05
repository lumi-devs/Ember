import discord
from .config import JOIN_CHANNELS, DB_PATH, GAMING_DB_PATH
import aiosqlite


class LimitModal(discord.ui.Modal, title="Set User Limit"):
    def __init__(self, cog, channel, is_gaming: bool = False):
        super().__init__()
        self.cog = cog
        self.channel = channel
        self.is_gaming = is_gaming
        self.db_path = GAMING_DB_PATH if is_gaming else DB_PATH

        self.limit_input = discord.ui.TextInput(
            label="User Limit (0-99)",
            placeholder="Enter 0 for no limit...",
            default=str(channel.user_limit if channel.user_limit else 0),
            max_length=2,
            required=True,
        )
        self.add_item(self.limit_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            limit = int(self.limit_input.value)
            if limit < 0 or limit > 99:
                await interaction.followup.send(
                    "❌ Limit must be between 0 and 99!", ephemeral=True
                )
                return

            await self.channel.edit(user_limit=limit if limit > 0 else None)

            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "UPDATE voiceChannel SET channelLimit = ? WHERE voiceID = ?",
                    (limit if limit > 0 else None, self.channel.id),
                )
                await db.commit()

            await interaction.followup.send(
                (
                    f"✅ User limit set to {limit}!"
                    if limit > 0
                    else "✅ User limit removed!"
                ),
                ephemeral=True,
            )

        except ValueError:
            await interaction.followup.send(
                "❌ Please enter a valid number!", ephemeral=True
            )


class RenameModal(discord.ui.Modal, title="Rename Voice Channel"):
    def __init__(self, cog, channel, is_gaming: bool = False):
        super().__init__()
        self.cog = cog
        self.channel = channel
        self.is_gaming = is_gaming
        self.db_path = GAMING_DB_PATH if is_gaming else DB_PATH

        self.name_input = discord.ui.TextInput(
            label="New Channel Name",
            placeholder="Enter new name...",
            default=channel.name,
            max_length=100,
            required=True,
        )
        self.add_item(self.name_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        name = self.name_input.value.strip()
        if len(name) < 1:
            await interaction.followup.send(
                "❌ Please provide a valid name!", ephemeral=True
            )
            return

        old_name = self.channel.name
        await self.channel.edit(name=name)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE voiceChannel SET channelName = ? WHERE voiceID = ?",
                (name, self.channel.id),
            )
            await db.commit()

        await interaction.followup.send(
            f"✏️ Renamed `{old_name}` to `{name}`", ephemeral=True
        )
