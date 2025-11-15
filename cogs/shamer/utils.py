import discord
from typing import List, Dict, Any
from .database import ShameDatabase
from .config import ShameConfig

class ShameUtils:
    def __init__(self, database: ShameDatabase, bot):
        self.db = database
        self.bot = bot
    
    async def can_earn_points(self, user: discord.Member) -> bool:
        """Check if user can earn points (not blacklisted)"""
        try:
            user_roles = [role.id for role in user.roles]
            return not await self.db.is_blacklisted(user.id, user_roles)
        except Exception as e:
            print(f"[ShameUtils] Error in can_earn_points: {e}")
            return False
    
    async def is_channel_tracked(self, channel: discord.TextChannel) -> bool:
        """Check if messages in this channel should be tracked for points"""
        try:
            # Check if channel is explicitly blacklisted
            if channel.id in ShameConfig.BLACKLISTED_CHANNEL_IDS:
                return False
            
            # Check if channel's category is blacklisted
            if channel.category and channel.category.id in ShameConfig.BLACKLISTED_CATEGORY_IDS:
                return False
            
            # Optional: Whitelist mode (uncomment if you want to use it)
            # if ShameConfig.WHITELISTED_CHANNEL_IDS:
            #     return channel.id in ShameConfig.WHITELISTED_CHANNEL_IDS
            
            return True
            
        except Exception as e:
            print(f"[ShameUtils] Error in is_channel_tracked: {e}")
            return True  # Default to tracking if there's an error
    
    async def calculate_decay(self, current_points: int) -> int:
        """Calculate decay points (min of 10% or 10 points)"""
        try:
            decay_by_percentage = int(current_points * ShameConfig.DECAY_PERCENTAGE)
            return min(decay_by_percentage, ShameConfig.MAX_DECAY_PER_HOUR)
        except Exception as e:
            print(f"[ShameUtils] Error in calculate_decay: {e}")
            return min(10, current_points)
    
    async def assign_shame_role(self, guild: discord.Guild, top_users: List[int]):
        """Assign shame role to top 5 users and remove from others"""
        try:
            shame_role = guild.get_role(ShameConfig.SHAME_ROLE_ID)
            if not shame_role:
                print(f"[ShameUtils] Shame role not found in guild {guild.name}")
                return
            
            # Remove role from all current members
            for member in shame_role.members:
                try:
                    await member.remove_roles(shame_role)
                except Exception as e:
                    print(f"[ShameUtils] Error removing role from {member}: {e}")
            
            # Add role to top 5 users
            for user_id in top_users[:5]:
                member = guild.get_member(user_id)
                if member:
                    try:
                        await member.add_roles(shame_role)
                    except Exception as e:
                        print(f"[ShameUtils] Error adding role to {member}: {e}")
        except Exception as e:
            print(f"[ShameUtils] Error in assign_shame_role: {e}")