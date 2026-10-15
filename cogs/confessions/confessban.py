import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
from datetime import datetime
from .helpers import DB_PATH, lookup_user_id_from_hash, sha256_user_id


class ConfessBan(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        await self.init_db()
        print("ConfessBan cog loaded and database initialized")

    async def init_db(self):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS confessban (
                    user_id INTEGER PRIMARY KEY,
                    reason TEXT,
                    banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor = await db.execute("PRAGMA table_info(confessban)")
            columns = {row[1] for row in await cursor.fetchall()}
            if "reason" not in columns:
                await db.execute("ALTER TABLE confessban ADD COLUMN reason TEXT")
            await db.commit()

    def has_permission(self, interaction: discord.Interaction) -> bool:
        return interaction.user.guild_permissions.administrator

    # target can be a mention, a user id, or a hash from the mod log
    async def _resolve_target(
        self, interaction: discord.Interaction, target: str
    ) -> tuple[int | None, discord.User | None, str | None]:
        cleaned = target.strip().lstrip("<@!").rstrip(">")

        # plain id or mention
        if cleaned.isdigit():
            uid = int(cleaned)
            try:
                user = await self.bot.fetch_user(uid)
                return uid, user, None
            except discord.NotFound:
                # they mightve left, still allow the ban
                return uid, None, None
            except Exception as e:
                return None, None, f"Could not fetch user: {e}"

        # sha256 hash from the mod log
        if len(cleaned) == 64 and all(c in "0123456789abcdef" for c in cleaned.lower()):
            uid = await lookup_user_id_from_hash(cleaned.lower())
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

    @app_commands.command(
        name="confessban",
        description="Ban or unban a user from submitting confessions",
    )
    @app_commands.describe(
        target="User mention, user ID, or SHA-256 hash from the mod log",
        action="Ban or unban",
        reason="Reason for the ban (optional)",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Ban", value="ban"),
            app_commands.Choice(name="Unban", value="unban"),
        ]
    )
    async def confessban(
        self,
        interaction: discord.Interaction,
        target: str,
        action: app_commands.Choice[str],
        reason: str = None,
    ):
        if not self.has_permission(interaction):
            return await interaction.response.send_message(
                "❌ You need administrator permissions to use this command.",
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True)

        user_id, user, error = await self._resolve_target(interaction, target)
        if error:
            return await interaction.followup.send(error, ephemeral=True)
        if user_id is None:
            return await interaction.followup.send(
                "❌ Could not resolve target user.", ephemeral=True
            )

        # show the mention if we could fetch them, else just the id
        user_display = f"{user.mention} ({user.id})" if user else f"User ID `{user_id}`"

        try:
            async with aiosqlite.connect(DB_PATH) as db:
                if action.value == "ban":
                    await db.execute(
                        "INSERT OR REPLACE INTO confessban (user_id, reason) VALUES (?, ?)",
                        (user_id, reason),
                    )
                    await db.commit()

                    embed = discord.Embed(
                        title="✅ User Banned from Confessions",
                        color=discord.Color.red(),
                        timestamp=discord.utils.utcnow(),
                    )
                    embed.add_field(name="User", value=user_display, inline=False)
                    embed.add_field(name="Banned by", value=interaction.user.mention, inline=False)
                    if reason:
                        embed.add_field(name="Reason", value=reason, inline=False)
                    # hash so mods can match it in the mod log
                    embed.add_field(
                        name="SHA-256 hash",
                        value=f"`{sha256_user_id(user_id)}`",
                        inline=False,
                    )
                    await interaction.followup.send(embed=embed)

                else:  # unban
                    await db.execute(
                        "DELETE FROM confessban WHERE user_id=?", (user_id,)
                    )
                    await db.commit()

                    embed = discord.Embed(
                        title="✅ User Unbanned from Confessions",
                        color=discord.Color.green(),
                        timestamp=discord.utils.utcnow(),
                    )
                    embed.add_field(name="User", value=user_display, inline=False)
                    embed.add_field(
                        name="Unbanned by", value=interaction.user.mention, inline=False
                    )
                    await interaction.followup.send(embed=embed)

        except Exception as e:
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @app_commands.command(name="confessban_list", description="List all banned users")
    async def confessban_list(self, interaction: discord.Interaction):
        if not self.has_permission(interaction):
            return await interaction.response.send_message(
                "❌ You need administrator permissions to use this command.",
                ephemeral=True,
            )

        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute(
                    "SELECT user_id, reason, banned_at FROM confessban ORDER BY banned_at DESC"
                )
                bans = await cursor.fetchall()

            if not bans:
                return await interaction.response.send_message(
                    "No users are currently banned.", ephemeral=True
                )

            embed = discord.Embed(
                title="🚫 Banned Users from Confessions",
                color=discord.Color.orange(),
                timestamp=discord.utils.utcnow(),
            )

            for user_id, reason, banned_at in bans:
                try:
                    user = await self.bot.fetch_user(user_id)
                    user_text = f"{user.mention} ({user.id})"
                except Exception:
                    user_text = f"Unknown User (`{user_id}`)"

                ban_info = f"**Banned on:** {banned_at}\n"
                ban_info += f"**Hash:** `{sha256_user_id(user_id)[:16]}…`\n"
                if reason:
                    ban_info += f"**Reason:** {reason}"

                embed.add_field(name=user_text, value=ban_info, inline=False)

            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)


async def setup(bot):
    await bot.add_cog(ConfessBan(bot))