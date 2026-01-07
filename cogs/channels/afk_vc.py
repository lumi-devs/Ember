import discord
from discord.ext import commands, tasks
from datetime import datetime, timedelta
import asyncio

class VCAFKManager(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.tracked_users = {}  
        self.afk_minutes = 10    
        self.check_afk.start()
        self.vc_name="lounge"
    
    def cog_unload(self):
        self.check_afk.cancel()
    
    @tasks.loop(minutes=1)
    async def check_afk(self):
        current_time = datetime.utcnow()
        users_to_remove = []
        
        for user_id, data in list(self.tracked_users.items()):
            channel_id = data["channel_id"]
            time_joined = data["time_joined"]
            
            # Check if the configured time has passed
            if current_time - time_joined >= timedelta(minutes=self.afk_minutes):
                try:
                    # Get the channel and user
                    # Note: Using guilds[0] assumes bot is only in one server. 
                    # If bot is in multiple, you might need to fetch the specific guild ID from data if stored.
                    guild = self.bot.guilds[0] 
                    channel = guild.get_channel(channel_id)
                    member = guild.get_member(user_id)
                    
                    if not channel or not member:
                        users_to_remove.append(user_id)
                        continue
                    
                    # Check if channel name contains "lounge" (case-insensitive)
                    if "lounge" in channel.name.lower():
                        # Check if user is still alone in the channel
                        if len(channel.members) == 1 and member in channel.members:
                            # Kick user from voice channel
                            await member.move_to(None)
                            
                            # Send DM embed to user
                            embed = discord.Embed(
                                title="Sorry for the interruption!",
                                description=(
                                    "**We saw that you were afk in the lounge for too long!**\n\n"
                                    "To keep the lounge available for all members, "
                                    "we kicked you to make space for others.\n\n",
                                    
                                    "This does not apply to private VCs or vc rooms."
                                ),
                                color=discord.Color.orange(),
                                timestamp=discord.utils.utcnow()
                            )
                            embed.footer("The Aakhri Station Lounge Management")
                            
                            # Use server icon as avatar URL if available
                            if guild.icon:
                                embed.set_thumbnail(url=guild.icon.url)
                            else:
                                # Fallback orange icon
                                embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/1065803493734817832/1185132312303153152/orange.png")
                            
                            embed.set_footer(text=f"{guild.name}", icon_url=guild.icon.url if guild.icon else None)
                            
                            try:
                                await member.send(embed=embed)
                            except discord.Forbidden:
                                # Can't send DM, log it
                                print(f"Could not send DM to {member.name}")
                            
                            print(f"Kicked {member.name} from {channel.name} for being AFK alone for {self.afk_minutes}+ minutes")
                        
                        users_to_remove.append(user_id)
                    
                except Exception as e:
                    print(f"Error processing AFK check: {e}")
                    users_to_remove.append(user_id)
        
        # Clean up tracked users
        for user_id in users_to_remove:
            if user_id in self.tracked_users:
                del self.tracked_users[user_id]
    
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        # User joined a voice channel
        if after.channel and not before.channel:
            if self.vc_name in after.channel.name.lower():
                # Check if user is alone in the channel
                if len(after.channel.members) == 1:
                    self.tracked_users[member.id] = {
                        "channel_id": after.channel.id,
                        "time_joined": datetime.utcnow()
                    }
                    print(f"Started tracking {member.name} in {after.channel.name}")
                else:
                    # Remove from tracking if they join a non-empty channel
                    if member.id in self.tracked_users:
                        del self.tracked_users[member.id]
        
        # User left a voice channel or moved channels
        elif before.channel:
            # If user left voice entirely or moved to a different channel
            if member.id in self.tracked_users:
                # If they moved to another channel, check if it's a lounge
                if after.channel and "lounge" in after.channel.name.lower():
                    # Check if they're alone in the new channel
                    if len(after.channel.members) == 1:
                        self.tracked_users[member.id] = {
                            "channel_id": after.channel.id,
                            "time_joined": datetime.utcnow()
                        }
                    else:
                        del self.tracked_users[member.id]
                else:
                    # User left lounge or moved to non-lounge
                    del self.tracked_users[member.id]
        
        # Handle when users join/leave the same channel as tracked user
        if before.channel and before.channel.id in [data["channel_id"] for data in self.tracked_users.values()]:
            for user_id, data in list(self.tracked_users.items()):
                if data["channel_id"] == before.channel.id:
                    channel = before.channel
                    if len(channel.members) != 1:
                        # Someone else joined or the tracked user left
                        del self.tracked_users[user_id]
                    elif len(channel.members) == 1:
                        member_in_channel = channel.members[0]
                        if member_in_channel.id == user_id:
                            # Still alone, continue tracking
                            pass
                        else:
                            # Different person is alone now
                            del self.tracked_users[user_id]
    
    @commands.command()
    @commands.has_permissions(administrator=True)
    async def afkstats(self, ctx):
        """Show current AFK tracking statistics"""
        if not self.tracked_users:
            await ctx.send(f"No users are currently being tracked for AFK. (Timeout: {self.afk_minutes} mins)")
            return
        
        embed = discord.Embed(
            title="AFK Tracking Statistics",
            color=discord.Color.blue(),
            timestamp=datetime.utcnow()
        )
        
        for user_id, data in self.tracked_users.items():
            member = ctx.guild.get_member(user_id)
            channel = ctx.guild.get_channel(data["channel_id"])
            time_in_channel = datetime.utcnow() - data["time_joined"]
            minutes_alone = int(time_in_channel.total_seconds() / 60)
            
            if member and channel:
                embed.add_field(
                    name=f"👤 {member.name}",
                    value=f"Channel: {channel.mention}\n"
                          f"Time alone: {minutes_alone} minutes\n"
                          f"Time joined: {data['time_joined'].strftime('%H:%M:%S')}",
                    inline=False
                )
        
        embed.set_footer(text=f"Users will be kicked after {self.afk_minutes} minutes alone in lounge channels")
        await ctx.send(embed=embed)
    
    @commands.command()
    @commands.has_permissions(administrator=True)
    async def resetafk(self, ctx, member: discord.Member = None):
        """Reset AFK tracking for a user"""
        if member:
            if member.id in self.tracked_users:
                del self.tracked_users[member.id]
                await ctx.send(f"Reset AFK tracking for {member.mention}")
            else:
                await ctx.send(f"{member.mention} is not being tracked for AFK.")
        else:
            self.tracked_users.clear()
            await ctx.send("Reset AFK tracking for all users.")

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def setafktime(self, ctx, minutes: int):
        """Set the AFK timeout duration in minutes"""
        if minutes < 1:
            await ctx.send("Please specify a time of at least 1 minute.")
            return
        
        self.afk_minutes = minutes
        await ctx.send(f"✅ AFK timeout has been updated to **{self.afk_minutes} minutes**.")

async def setup(bot):
    await bot.add_cog(VCAFKManager(bot))