import discord
from typing import List, Dict, Any
from .database import ShameDatabase
from .config import ShameConfig

class ShameUtils:
    def __init__(self, database: ShameDatabase, bot):
        self.db = database
        self.bot = bot  # Add bot instance
    
    async def can_earn_points(self, user: discord.Member) -> bool:
        """Check if user can earn points (not blacklisted)"""
        user_roles = [role.id for role in user.roles]
        return not await self.db.is_blacklisted(user.id, user_roles)
    
    async def calculate_decay(self, current_points: int) -> int:
        """Calculate decay points (min of 10% or 10 points)"""
        decay_by_percentage = int(current_points * ShameConfig.DECAY_PERCENTAGE)
        return min(decay_by_percentage, ShameConfig.MAX_DECAY_PER_HOUR)
    
    async def format_leaderboard_embed(self, leaderboard_data: List[Dict[str, Any]], title: str) -> discord.Embed:
        embed = discord.Embed(
            title=title,
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow()
        )
        
        for i, user_data in enumerate(leaderboard_data, 1):
            user = self.bot.get_user(user_data['user_id'])  # Now this works!
            username = user.mention if user else f"Unknown User ({user_data['user_id']})"
            
            embed.add_field(
                name=f"{i}. {username}",
                value=f"Points: {user_data['points']} | Messages: {user_data['total_messages']}",
                inline=False
            )
        
        return embed
    
    async def assign_shame_role(self, guild: discord.Guild, top_users: List[int]):
        """Assign shame role to top 5 users and remove from others"""
        shame_role = guild.get_role(ShameConfig.SHAME_ROLE_ID)
        if not shame_role:
            return
        
        # Remove role from all current members
        for member in shame_role.members:
            await member.remove_roles(shame_role)
        
        # Add role to top 5 users
        for user_id in top_users[:5]:
            member = guild.get_member(user_id)
            if member:
                await member.add_roles(shame_role)