import discord
from discord import app_commands
import os

# Optional: list of mod IDs from environment
MOD_IDS = list(map(int, os.environ.get("MOD_IDS", "").split(","))) if os.environ.get("MOD_IDS") else []

def is_mod():
    """
    Slash command check for mods or admins.
    Returns True if the user is:
      - in MOD_IDS,
      - has Manage Messages permission, or
      - has Administrator permission.
    """
    async def predicate(interaction: discord.Interaction):
        perms = interaction.user.guild_permissions
        return (
            interaction.user.id in MOD_IDS
            or getattr(perms, "administrator", False)
        )
    return app_commands.check(predicate)
