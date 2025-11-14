import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timedelta
import asyncio

class MentionTracker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Hardcode your role IDs here
        self.tracked_role_ids = [123456789, 987654321, 112233445]  # Replace with your role IDs
    
    @app_commands.command(name="mentions_today", description="Check how many times roles were mentioned today across the server")
    async def mentions_today(self, interaction: discord.Interaction):
        """Check how many times hardcoded roles were mentioned today across ALL channels"""
        await interaction.response.defer(ephemeral=True)
        
        try:
            # Get today's date
            today = datetime.now().date()
            start_of_day = datetime.combine(today, datetime.min.time())
            
            # Get role objects
            target_roles = []
            for role_id in self.tracked_role_ids:
                role = interaction.guild.get_role(role_id)
                if role:
                    target_roles.append(role)
            
            if not target_roles:
                await interaction.followup.send("❌ No valid roles found from hardcoded IDs.", ephemeral=True)
                return
            
            # Check mentions for each role using Discord search
            results = []
            total_mentions = 0
            
            embed = discord.Embed(
                title="🔍 Scanning Role Mentions...",
                description=f"Searching all channels for today's mentions...\nThis may take a while for large servers.",
                color=discord.Color.blue()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            
            for role in target_roles:
                mention_count = await self.search_role_mentions_today(interaction.guild, role, start_of_day)
                results.append((role, mention_count))
                total_mentions += mention_count
            
            # Create results embed
            results_embed = discord.Embed(
                title="📊 Role Mentions Today",
                description=f"Mention counts for {today.strftime('%B %d, %Y')} across all channels",
                color=discord.Color.green(),
                timestamp=datetime.now()
            )
            
            for role, count in results:
                results_embed.add_field(
                    name=f"{role.name}",
                    value=f"**{count}** mentions",
                    inline=True
                )
            
            results_embed.add_field(
                name="📈 Total",
                value=f"**{total_mentions}** mentions today",
                inline=False
            )
            
            results_embed.set_footer(text=f"Scanned all channels • {len(target_roles)} roles tracked")
            
            await interaction.edit_original_response(embed=results_embed)
            
        except Exception as e:
            await interaction.followup.send(f"❌ Error checking mentions: {str(e)}", ephemeral=True)
    
    async def search_role_mentions_today(self, guild: discord.Guild, role: discord.Role, start_of_day: datetime) -> int:
        """Search ALL channels for role mentions today using Discord's search"""
        mention_count = 0
        role_mention = f"<@&{role.id}>"
        
        print(f"[MentionTracker] Searching for mentions of {role.name}...")
        
        try:
            # Search across all text channels
            for channel in guild.text_channels:
                if not channel.permissions_for(guild.me).read_messages:
                    continue
                
                try:
                    # Use Discord's search feature with date filter
                    # Note: Discord search doesn't have precise date filtering, so we'll search and filter manually
                    messages = []
                    async for message in channel.history(limit=500, after=start_of_day):
                        messages.append(message)
                    
                    # Count mentions in this channel
                    channel_mentions = 0
                    for message in messages:
                        if role_mention in message.content:
                            channel_mentions += 1
                    
                    if channel_mentions > 0:
                        print(f"[MentionTracker] Found {channel_mentions} mentions in #{channel.name}")
                        mention_count += channel_mentions
                        
                except discord.Forbidden:
                    continue
                except Exception as e:
                    print(f"[MentionTracker] Error searching #{channel.name}: {e}")
                    continue
                    
        except Exception as e:
            print(f"[MentionTracker] Search error for {role.name}: {e}")
        
        return mention_count

async def setup(bot):
    await bot.add_cog(MentionTracker(bot))