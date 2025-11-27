import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
from datetime import datetime
from cogs.confessions.helpers import DB_PATH

class ConfessBan(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def init_db(self):
        """Initialize the database table with correct schema"""
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute('''
                CREATE TABLE IF NOT EXISTS confessban (
                    user_id INTEGER PRIMARY KEY,
                    reason TEXT,
                    banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            await db.commit()

    def has_permission(self, interaction: discord.Interaction):
        """Check if user has administrator permissions"""
        return interaction.user.guild_permissions.administrator

    @commands.Cog.listener()
    async def on_ready(self):
        """Initialize database when cog is loaded"""
        await self.init_db()
        print("ConfessBan cog loaded and database initialized")

    @app_commands.command(name="confessban", description="Ban or unban a user from submitting confessions")
    @app_commands.describe(
        user="The user to ban/unban",
        reason="Reason for ban (optional)"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="Ban", value="ban"),
        app_commands.Choice(name="Unban", value="unban")
    ])
    async def confessban(self, interaction: discord.Interaction, user: discord.User, action: app_commands.Choice[str], reason: str = None):
        # Only administrators
        if not self.has_permission(interaction):
            return await interaction.response.send_message("❌ You need administrator permissions to use this command.", ephemeral=True)

        try:
            async with aiosqlite.connect(DB_PATH) as db:
                if action.value == "ban":
                    await db.execute(
                        "INSERT OR REPLACE INTO confessban (user_id, reason) VALUES (?, ?)", 
                        (user.id, reason)
                    )
                    await db.commit()
                    
                    embed = discord.Embed(
                        title="✅ User Banned from Confessions",
                        color=discord.Color.red(),
                        timestamp=datetime.utcnow()
                    )
                    embed.add_field(name="User", value=f"{user.mention} ({user.id})", inline=False)
                    embed.add_field(name="Banned by", value=interaction.user.mention, inline=False)
                    if reason:
                        embed.add_field(name="Reason", value=reason, inline=False)
                        
                    await interaction.response.send_message(embed=embed)
                    
                else:  # unban
                    await db.execute("DELETE FROM confessban WHERE user_id=?", (user.id,))
                    await db.commit()
                    
                    embed = discord.Embed(
                        title="✅ User Unbanned from Confessions",
                        color=discord.Color.green(),
                        timestamp=datetime.utcnow()
                    )
                    embed.add_field(name="User", value=f"{user.mention} ({user.id})", inline=False)
                    embed.add_field(name="Unbanned by", value=interaction.user.mention, inline=False)
                    
                    await interaction.response.send_message(embed=embed)
                    
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)

    @app_commands.command(name="confessban_list", description="List all banned users")
    async def confessban_list(self, interaction: discord.Interaction):
        """List all banned users"""
        # Only administrators
        if not self.has_permission(interaction):
            return await interaction.response.send_message("❌ You need administrator permissions to use this command.", ephemeral=True)

        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute("SELECT user_id, reason, banned_at FROM confessban ORDER BY banned_at DESC")
                bans = await cursor.fetchall()

            if not bans:
                return await interaction.response.send_message("No users are currently banned.", ephemeral=True)

            embed = discord.Embed(
                title="🚫 Banned Users from Confessions",
                color=discord.Color.orange(),
                timestamp=datetime.utcnow()
            )

            for user_id, reason, banned_at in bans:
                try:
                    user = await self.bot.fetch_user(user_id)
                    user_text = f"{user.mention} ({user.id})"
                except:
                    user_text = f"Unknown User ({user_id})"
                
                ban_info = f"**Banned on:** {banned_at}\n"
                if reason:
                    ban_info += f"**Reason:** {reason}"
                    
                embed.add_field(name=user_text, value=ban_info, inline=False)

            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)

async def setup(bot):
    await bot.add_cog(ConfessBan(bot))