import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timedelta
import asyncio

class MentionTracker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Hardcode your role IDs here
        self.tracked_role_ids = [1393171026690707557, 1393171029639168021, 1393171028351385651]  # Replace with your role IDs
    
    @app_commands.command(name="mentions", description="Check how many times roles were mentioned today")
    async def mentions_today(self, interaction: discord.Interaction):
        """Check how many times hardcoded roles were mentioned today"""
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
            
            # Check mentions for each role
            results = []
            total_mentions = 0
            
            for role in target_roles:
                mention_count = await self.count_role_mentions_today(interaction.channel, role, start_of_day)
                results.append((role, mention_count))
                total_mentions += mention_count
            
            # Create embed with results
            embed = discord.Embed(
                title="📊 Role Mentions Today",
                description=f"Mention counts for {today.strftime('%B %d, %Y')}",
                color=discord.Color.blue(),
                timestamp=datetime.now()
            )
            
            for role, count in results:
                embed.add_field(
                    name=f"{role.name}",
                    value=f"**{count}** mentions",
                    inline=True
                )
            
            embed.add_field(
                name="📈 Total",
                value=f"**{total_mentions}** mentions today",
                inline=False
            )
            
            embed.set_footer(text=f"Channel: #{interaction.channel.name}")
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            await interaction.followup.send(f"❌ Error checking mentions: {str(e)}", ephemeral=True)
    
    async def count_role_mentions_today(self, channel, role: discord.Role, start_of_day: datetime) -> int:
        """Count how many times a role was mentioned in a channel today"""
        mention_count = 0
        role_mention = f"<@&{role.id}>"
        
        try:
            # Since server gets 20k+ messages, we need to be efficient
            # Check messages in batches from today onwards
            last_message = None
            batch_count = 0
            
            while True:
                # Get messages in batches of 100
                messages = []
                async for message in channel.history(limit=100, before=last_message, after=start_of_day):
                    messages.append(message)
                
                if not messages:
                    break
                
                # Count mentions in this batch
                for message in messages:
                    if role_mention in message.content:
                        mention_count += 1
                
                batch_count += len(messages)
                print(f"[MentionTracker] Processed {batch_count} messages for role {role.name}")
                
                # Stop if we've processed a lot of messages or reached start of day
                if len(messages) < 100 or batch_count > 5000:  # Safety limit
                    break
                
                last_message = messages[-1]
                    
        except discord.Forbidden:
            print(f"[MentionTracker] No permission to read history in #{channel.name}")
        except Exception as e:
            print(f"[MentionTracker] Error reading channel history: {e}")
        
        return mention_count

async def setup(bot):
    await bot.add_cog(MentionTracker(bot))