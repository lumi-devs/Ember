import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional, Union
from .database import ShameDatabase
from .utils import ShameUtils
from .config import ShameConfig
import aiosqlite

class ShameCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = ShameDatabase(ShameConfig.DB_PATH)
        self.utils = ShameUtils(self.db, self.bot)
    
    # Main shame command group
    shame = app_commands.Group(name="shame", description="Hall of Shame leaderboard commands")
    
    @shame.command(name="rank", description="Check your rank in the shame leaderboard")
    async def shame_rank(self, interaction: discord.Interaction):
        user_data = await self.db.get_user(interaction.user.id)
        
        if not user_data or user_data['points'] == 0:
            await interaction.response.send_message("You don't have any points yet!", ephemeral=True)
            return
        
        rank = await self.db.get_user_rank(interaction.user.id)
        
        # Calculate time until next reroll (4 days from now)
        from datetime import datetime, timedelta
        now = datetime.now()
        next_reroll = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=4)
        time_until_reroll = next_reroll - now
        
        days = time_until_reroll.days
        hours = time_until_reroll.seconds // 3600
        
        embed = discord.Embed(
            title="Your Shame Rank",
            color=discord.Color.orange(),
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="Rank", value=f"#{rank}", inline=True)
        embed.add_field(name="Points", value=user_data['points'], inline=True)
        embed.add_field(name="Total Messages", value=user_data['total_messages'], inline=True)
        
        # Set user avatar as embed thumbnail
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        
        # Set custom footer
        embed.set_footer(text=f"You can do better gng, rerolling in {days}d {hours}h")
        
        await interaction.response.send_message(embed=embed)
        await interaction.response.send_message(embed=embed)
        
    @shame.command(name="top", description="Show top 10 in shame leaderboard")
    async def shame_top(self, interaction: discord.Interaction):
        leaderboard = await self.db.get_leaderboard(10)
        
        if not leaderboard:
            await interaction.response.send_message("No one has any points yet!", ephemeral=True)
            return
        
        # Calculate tim                 e until next reroll
        from datetime import datetime, timedelta
        now = datetime.now()
        next_reroll = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=4)
        time_until_reroll = next_reroll - now
        
        days = time_until_reroll.days
        hours = time_until_reroll.seconds // 3600
        
        embed = discord.Embed(
            title="🏆 Hall of Shame - Top 10",
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow()
        )
        
        # Define symbols for each position
        symbols = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        
        for i, user_data in enumerate(leaderboard):
            user = self.bot.get_user(user_data['user_id'])
            username = user.mention if user else f"Unknown User ({user_data['user_id']})"
            
            symbol = symbols[i] if i < len(symbols) else "•"
            
            embed.add_field(
                name=f"{symbol} {username}",
                value=f"**{user_data['points']}** points • {user_data['total_messages']} messages",
                inline=False
            )
        
        embed.set_footer(text=f"You can do better gng, rerolling in {days}d {hours}h")
        
        await interaction.response.send_message(embed=embed)
        
    @shame.command(name="stats", description="Check your detailed shame statistics")
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

    # Blacklist commands as a subgroup under shame
    @shame.group(name="blacklist", description="Manage blacklist for shame system")
    async def shame_blacklist(self, interaction: discord.Interaction):
        pass  # This is the parent group command
    
    @shame_blacklist.command(name="add", description="Add a user or role to blacklist")
    @app_commands.describe(target="User or role to blacklist")
    async def blacklist_add(self, interaction: discord.Interaction, target: Union[discord.Member, discord.Role]):
        # Check admin permissions
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("You need administrator permissions to use this command!", ephemeral=True)
            return
        
        entity_type = "user" if isinstance(target, discord.Member) else "role"
        
        await self.db.add_to_blacklist(target.id, entity_type)
        await interaction.response.send_message(f"✅ {entity_type.capitalize()} {target.mention} has been added to blacklist!")
    
    @shame_blacklist.command(name="remove", description="Remove a user or role from blacklist")
    @app_commands.describe(target="User or role to remove from blacklist")
    async def blacklist_remove(self, interaction: discord.Interaction, target: Union[discord.Member, discord.Role]):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("You need administrator permissions to use this command!", ephemeral=True)
            return
        
        await self.db.remove_from_blacklist(target.id)
        await interaction.response.send_message(f"✅ {target.mention} has been removed from blacklist!")
    
    @shame_blacklist.command(name="list", description="Show all blacklisted users and roles")
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
    
    @shame_blacklist.command(name="clear", description="Clear all blacklisted items")
    async def blacklist_clear(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("You need administrator permissions to use this command!", ephemeral=True)
            return
        
        # Clear all blacklist entries
        async with aiosqlite.connect(self.db.db_path) as db:
            await db.execute('DELETE FROM blacklist')
            await db.commit()
        
        await interaction.response.send_message("✅ All blacklisted items have been cleared!")