import discord
from discord import Embed, Color
from datetime import datetime, timedelta
import asyncio
from .config import *

class ShamerRotation:
    def __init__(self, cog):
        self.cog = cog

    async def do_rotation(self):
        """Perform rotation: assign roles to top 5 and send announcement"""
        print("[Shamer] Starting rotation...")
        
        # Get top 5 users
        sorted_scores = sorted(self.cog.scores.items(), key=lambda x: x[1], reverse=True)
        new_top5 = [user_id for user_id, score in sorted_scores[:5]]
        
        # Assign roles
        await self.assign_top_roles(new_top5)
        
        # Update next rotation time
        self.cog.next_rotation = datetime.now().astimezone().replace(
            hour=0, minute=0, second=0, microsecond=0
        ) + timedelta(days=ROTATION_INTERVAL_DAYS)
        await self.cog.save_meta('next_rotation', self.cog.next_rotation.isoformat())
        
        # Send announcement
        await self.send_rotation_announcement(new_top5, sorted_scores[:5])
        
        print("[Shamer] Rotation completed!")

    async def assign_top_roles(self, new_top5: list):
        """Assign role to new top 5 and remove from previous holders"""
        guild = None
        for g in self.cog.bot.guilds:
            if g.get_role(TOP_N_ROLE):
                guild = g
                break
        
        if not guild:
            print(f"[Shamer] Could not find guild with role {TOP_N_ROLE}")
            return

        role = guild.get_role(TOP_N_ROLE)
        if not role:
            print(f"[Shamer] Role {TOP_N_ROLE} not found")
            return

        # Remove role from previous top5
        for user_id in self.cog.current_top5:
            if user_id not in new_top5:
                member = guild.get_member(user_id)
                if member and role in member.roles:
                    try:
                        await member.remove_roles(role)
                        print(f"[Shamer] Removed role from {member.display_name}")
                    except Exception as e:
                        print(f"[Shamer] Error removing role: {e}")

        # Add role to new top5
        for user_id in new_top5:
            member = guild.get_member(user_id)
            if member and role not in member.roles:
                try:
                    await member.add_roles(role)
                    print(f"[Shamer] Added role to {member.display_name}")
                except Exception as e:
                    print(f"[Shamer] Error adding role: {e}")

        # Update current top5
        self.cog.current_top5 = set(new_top5)
        await self.cog.save_meta('current_top5', ','.join(map(str, new_top5)))

    async def send_rotation_announcement(self, new_top5: list, top5_scores: list):
        """Send rotation announcement to shame channel"""
        channel = self.cog.bot.get_channel(SHAME_CHANNEL_ID)
        if not channel:
            print(f"[Shamer] Shame channel {SHAME_CHANNEL_ID} not found")
            return

        description = ""
        for i, (user_id, score) in enumerate(top5_scores):
            medal = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else "🔸"
            description += f"{medal} <@{user_id}> - `{score:.1f} points`\n"

        embed = Embed(
            title="🎭 Hall of Shame Rotation Complete!",
            description=description,
            color=Color.purple(),
            timestamp=datetime.now()
        )
        embed.add_field(
            name="👑 Role Assignment",
            value=f"Top 5 members have received the <@&{TOP_N_ROLE}> role!",
            inline=False
        )
        embed.add_field(
            name="⏰ Next Rotation",
            value=f"<t:{int(self.cog.next_rotation.timestamp())}:R>",
            inline=False
        )
        embed.set_footer(text="Stay active to climb the leaderboard!")

        await channel.send(embed=embed)