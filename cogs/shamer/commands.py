import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional
from .database import ShameDatabase
from .utils import ShameUtils
from .config import ShameConfig
import aiosqlite
class ShameCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = ShameDatabase(ShameConfig.DB_PATH)
        self.utils = ShameUtils(self.db)
    
    # User commands
    @app_commands.command(name="shame_rank", description="Check your rank in the shame leaderboard")
    async def shame_rank(self, interaction: discord.Interaction):
        user_data = await self.db.get_user(interaction.user.id)
        
        if not user_data or user_data['points'] == 0:
            await interaction.response.send_message("You don't have any points yet!", ephemeral=True)
            return
        
        rank = await self.db.get_user_rank(interaction.user.id)
        
        embed = discord.Embed(
            title="Your Shame Rank",
            color=discord.Color.blue()
        )
        embed.add_field(name="Rank", value=f"#{rank}", inline=True)
        embed.add_field(name="Points", value=user_data['points'], inline=True)
        embed.add_field(name="Total Messages", value=user_data['total_messages'], inline=True)
        
        await interaction.response.send_message(embed=embed)
    
    @app_commands.command(name="shame_top", description="Show top 10 in shame leaderboard")
    async def shame_top(self, interaction: discord.Interaction):
        leaderboard = await self.db.get_leaderboard(10)
        
        if not leaderboard:
            await interaction.response.send_message("No one has any points yet!", ephemeral=True)
            return
        
        embed = await self.utils.format_leaderboard_embed(leaderboard, "🏆 Top 10 - Hall of Shame")
        await interaction.response.send_message(embed=embed)
    
    @app_commands.command(name="shame_stats", description="Check your detailed shame statistics")
    async def shame_stats(self, interaction: discord.Interaction):
        user_data = await self.db.get_user(interaction.user.id)
        
        if not user_data:
            user_data = {'points': 0, 'total_messages': 0, 'hourly_points': 0}
        
        rank = await self.db.get_user_rank(interaction.user.id)
        is_blacklisted = await self.db.is_blacklisted(
            interaction.user.id, 
            [role.id for role in interaction.user.roles]
        )
        
        embed = discord.Embed(
            title="Your Shame Statistics",
            color=discord.Color.green() if not is_blacklisted else discord.Color.red()
        )
        
        embed.add_field(name="Rank", value=f"#{rank if rank > 0 else 'N/A'}", inline=True)
        embed.add_field(name="Total Points", value=user_data['points'], inline=True)
        embed.add_field(name="Total Messages", value=user_data['total_messages'], inline=True)
        embed.add_field(name="Hourly Points", value=f"{user_data['hourly_points']}/{ShameConfig.MAX_POINTS_PER_HOUR}", inline=True)
        embed.add_field(name="Status", value="Blacklisted ❌" if is_blacklisted else "Active ✅", inline=True)
        
        await interaction.response.send_message(embed=embed)
    
    # Blacklist commands group
    blacklist_group = app_commands.Group(name="shame_blacklist", description="Manage blacklist for shame system")
    
    @blacklist_group.command(name="add", description="Add a user or role to blacklist")
    @app_commands.describe(target="User or role to blacklist")
    async def blacklist_add(self, interaction: discord.Interaction, target: discord.Union[discord.Member, discord.Role]):
        # Check admin permissions
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("You need administrator permissions to use this command!", ephemeral=True)
            return
        
        entity_type = "user" if isinstance(target, discord.Member) else "role"
        
        await self.db.add_to_blacklist(target.id, entity_type)
        await interaction.response.send_message(f"✅ {entity_type.capitalize()} {target.mention} has been added to blacklist!")
    
    @blacklist_group.command(name="remove", description="Remove a user or role from blacklist")
    @app_commands.describe(target="User or role to remove from blacklist")
    async def blacklist_remove(self, interaction: discord.Interaction, target: discord.Union[discord.Member, discord.Role]):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("You need administrator permissions to use this command!", ephemeral=True)
            return
        
        await self.db.remove_from_blacklist(target.id)
        await interaction.response.send_message(f"✅ {target.mention} has been removed from blacklist!")
    
    @blacklist_group.command(name="list", description="Show all blacklisted users and roles")
    async def blacklist_list(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("You need administrator permissions to use this command!", ephemeral=True)
            return
        
        blacklist = await self.db.get_blacklist()
        
        if not blacklist:
            await interaction.response.send_message("No users or roles are blacklisted!", ephemeral=True)
            return
        
        embed = discord.Embed(title="Blacklisted Users/Roles", color=discord.Color.red())
        
        users = []
        roles = []
        
        for item in blacklist:
            if item['entity_type'] == 'user':
                user = self.bot.get_user(item['entity_id'])
                users.append(user.mention if user else f"Unknown User ({item['entity_id']})")
            else:
                role = interaction.guild.get_role(item['entity_id'])
                roles.append(role.mention if role else f"Unknown Role ({item['entity_id']})")
        
        if users:
            embed.add_field(name="Users", value="\n".join(users), inline=False)
        if roles:
            embed.add_field(name="Roles", value="\n".join(roles), inline=False)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @blacklist_group.command(name="clear", description="Clear all blacklisted items")
    async def blacklist_clear(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("You need administrator permissions to use this command!", ephemeral=True)
            return
        
        # Clear all blacklist entries
        async with aiosqlite.connect(self.db.db_path) as db:
            await db.execute('DELETE FROM blacklist')
            await db.commit()
        
        await interaction.response.send_message("✅ All blacklisted items have been cleared!")