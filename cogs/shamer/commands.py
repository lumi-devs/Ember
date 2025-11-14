import discord
from discord import app_commands, Embed, Color
from datetime import datetime, timedelta
import aiosqlite
from .config import *

class ShamerCommands:
    def __init__(self, cog):
        self.cog = cog

    @app_commands.command(name="shame_status", description="Check your shame score and rank")
    async def shame_status(self, interaction: discord.Interaction):
        """Check user's current status in leaderboard"""
        user_id = interaction.user.id
        score = self.cog.scores.get(user_id, 0)
        
        # Calculate rank
        sorted_scores = sorted(self.cog.scores.items(), key=lambda x: x[1], reverse=True)
        rank = next((i + 1 for i, (uid, _) in enumerate(sorted_scores) if uid == user_id), None)
        
        # Check if in top 5
        in_top5 = user_id in self.cog.current_top5
        
        embed = Embed(
            title="😳 Your Shame Status",
            color=Color.orange(),
            timestamp=datetime.now()
        )
        embed.add_field(name="Score", value=f"{score:.1f}", inline=True)
        embed.add_field(name="Rank", value=f"#{rank}" if rank else "Unranked", inline=True)
        embed.add_field(name="Top 5", value="✅ Yes" if in_top5 else "❌ No", inline=True)
        embed.add_field(name="Hourly Gain", value=f"{self.cog.hourly_gain.get(user_id, 0)}/{MAX_HOURLY_GAIN}", inline=True)
        
        if self.cog.next_rotation:
            embed.add_field(name="Next Rotation", value=f"<t:{int(self.cog.next_rotation.timestamp())}:R>", inline=False)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="shame_leaderboard", description="Show top 10 most shameful users")
    async def shame_leaderboard(self, interaction: discord.Interaction):
        """Display top 10 leaderboard"""
        sorted_scores = sorted(self.cog.scores.items(), key=lambda x: x[1], reverse=True)[:TOP_N_DISPLAY]
        
        if not sorted_scores:
            await interaction.response.send_message("No scores yet! Start chatting to get on the leaderboard.", ephemeral=True)
            return
        
        description = ""
        for i, (user_id, score) in enumerate(sorted_scores):
            medal = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else "🔸"
            top5_indicator = " 👑" if user_id in self.cog.current_top5 else ""
            description += f"{medal} **#{i+1}** <@{user_id}> - `{score:.1f} points`{top5_indicator}\n"
        
        embed = Embed(
            title="🏆 Hall of Shame Leaderboard",
            description=description,
            color=Color.gold(),
            timestamp=datetime.now()
        )
        
        if self.cog.next_rotation:
            embed.set_footer(text=f"Next rotation: {self.cog.next_rotation.strftime('%Y-%m-%d %H:%M')}")
        
        await interaction.response.send_message(embed=embed)

    shame_blacklist = app_commands.Group(name="shame_blacklist", description="Manage blacklist for shame tracking")

    @shame_blacklist.command(name="add_user", description="Add user to blacklist")
    @app_commands.default_permissions(administrator=True)
    async def blacklist_add_user(self, interaction: discord.Interaction, user: discord.User):
        """Add user to blacklist"""
        self.cog.blacklisted_users.add(user.id)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT OR IGNORE INTO shame_blacklist_users (user_id) VALUES (?)", (user.id,))
            await db.commit()
        
        # Remove from top5 if they were in it
        if user.id in self.cog.current_top5:
            self.cog.current_top5.remove(user.id)
            await self.cog.save_meta('current_top5', ','.join(map(str, self.cog.current_top5)))
        
        await interaction.response.send_message(f"✅ {user.mention} added to blacklist", ephemeral=True)

    @shame_blacklist.command(name="remove_user", description="Remove user from blacklist")
    @app_commands.default_permissions(administrator=True)
    async def blacklist_remove_user(self, interaction: discord.Interaction, user: discord.User):
        """Remove user from blacklist"""
        self.cog.blacklisted_users.discard(user.id)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM shame_blacklist_users WHERE user_id = ?", (user.id,))
            await db.commit()
        await interaction.response.send_message(f"✅ {user.mention} removed from blacklist", ephemeral=True)

    @shame_blacklist.command(name="add_role", description="Add role to blacklist")
    @app_commands.default_permissions(administrator=True)
    async def blacklist_add_role(self, interaction: discord.Interaction, role: discord.Role):
        """Add role to blacklist"""
        self.cog.blacklisted_roles.add(role.id)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT OR IGNORE INTO shame_blacklist_roles (role_id) VALUES (?)", (role.id,))
            await db.commit()
        await interaction.response.send_message(f"✅ {role.mention} added to blacklist", ephemeral=True)

    @shame_blacklist.command(name="remove_role", description="Remove role from blacklist")
    @app_commands.default_permissions(administrator=True)
    async def blacklist_remove_role(self, interaction: discord.Interaction, role: discord.Role):
        """Remove role from blacklist"""
        self.cog.blacklisted_roles.discard(role.id)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM shame_blacklist_roles WHERE role_id = ?", (role.id,))
            await db.commit()
        await interaction.response.send_message(f"✅ {role.mention} removed from blacklist", ephemeral=True)

    @shame_blacklist.command(name="list", description="Show current blacklist")
    @app_commands.default_permissions(administrator=True)
    async def blacklist_list(self, interaction: discord.Interaction):
        """List all blacklisted users and roles"""
        users = [f"<@{uid}>" for uid in self.cog.blacklisted_users]
        roles = [f"<@&{rid}>" for rid in self.cog.blacklisted_roles]
        
        embed = Embed(title="🚫 Shame Blacklist", color=Color.red())
        embed.add_field(name="Users", value="\n".join(users) if users else "None", inline=False)
        embed.add_field(name="Roles", value="\n".join(roles) if roles else "None", inline=False)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="shame_rotate", description="Force immediate rotation (Admin only)")
    @app_commands.default_permissions(administrator=True)
    async def shame_rotate(self, interaction: discord.Interaction):
        """Force immediate rotation"""
        await interaction.response.defer(ephemeral=True)
        await self.cog.do_rotation()
        await interaction.followup.send("✅ Rotation completed!", ephemeral=True)