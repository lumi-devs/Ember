import discord
from discord import app_commands
from discord.ext import commands, tasks
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict, Set, Optional
import math
import logging

# Set up logging
logger = logging.getLogger(__name__)

class VoteTimeout(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_votes: Dict[int, dict] = {}  # message_id -> vote_data
        self.user_cooldowns: Dict[int, datetime] = {}  # user_id -> last_vote_time (10 min cooldown)
        self.cleanup_task.start()
        self._lock = asyncio.Lock()  # For thread-safe operations
        
    def cog_unload(self):
        self.cleanup_task.cancel()
    
    @tasks.loop(minutes=5)
    async def cleanup_task(self):
        """Clean up expired votes and cooldowns"""
        now = datetime.now(timezone.utc)
        
        # Clean expired cooldowns (10 min cooldown)
        async with self._lock:
            expired_users = [
                user_id for user_id, last_time in self.user_cooldowns.items()
                if now - last_time > timedelta(minutes=10)
            ]
            for user_id in expired_users:
                try:
                    del self.user_cooldowns[user_id]
                except KeyError:
                    pass
        
        # Clean expired votes (votes older than 2 minutes)
        expired_votes = []
        for msg_id, data in list(self.active_votes.items()):  # Create a copy
            if now - data['created_at'] > timedelta(minutes=2):
                expired_votes.append(msg_id)
        
        for msg_id in expired_votes:
            try:
                data = self.active_votes.get(msg_id)
                if not data:
                    continue
                    
                channel = self.bot.get_channel(data['message_channel_id'])
                if channel:
                    try:
                        msg = await channel.fetch_message(msg_id)
                        await msg.edit(embed=discord.Embed(
                            title="❌ Vote Expired",
                            description="This vote has expired (2 minutes passed)",
                            color=discord.Color.red()
                        ))
                    except discord.NotFound:
                        pass
                    except discord.HTTPException as e:
                        logger.error(f"Failed to edit expired vote message: {e}")
            except Exception as e:
                logger.error(f"Error in cleanup for vote {msg_id}: {e}")
            finally:
                # Safely remove from active votes
                async with self._lock:
                    try:
                        del self.active_votes[msg_id]
                    except KeyError:
                        pass
    
    async def get_vc_members(self, voice_channel: discord.VoiceChannel) -> list:
        """Get human members in a VC (excluding bots)"""
        return [member for member in voice_channel.members if not member.bot]
    
    async def verify_same_vc(self, interaction: discord.Interaction, user: discord.Member) -> tuple[bool, Optional[discord.VoiceChannel], str]:
        """Verify that initiator, target, and all voters are in the same VC"""
        
        # Check if initiator is in a VC
        if not interaction.user.voice or not interaction.user.voice.channel:
            return False, None, "You must be in a voice channel to use this command."
        
        voice_channel = interaction.user.voice.channel
        
        # Check if target is in the same VC
        if not user.voice or user.voice.channel != voice_channel:
            return False, None, f"{user.display_name} must be in the same voice channel."
        
        # Get all human members in the VC
        vc_members = await self.get_vc_members(voice_channel)
        
        # Need at least 2 human members (initiator + target)
        if len(vc_members) < 2:
            return False, None, "Need at least 2 human members in the voice channel."
        
        return True, voice_channel, ""
    
    @app_commands.command(
        name="sybau",
        description="Start a vote to timeout a user in your voice channel (10 min cooldown)"
    )
    async def vote_timeout(
        self,
        interaction: discord.Interaction,
        user: discord.Member
    ):
        """Start a vote to timeout a user (requires 60% of VC members)"""
        
        # Check bot permissions
        if not interaction.guild.me.guild_permissions.moderate_members:
            await interaction.response.send_message(
                "❌ I need the 'Timeout Members' permission to use this command.",
                ephemeral=True
            )
            return
        
        # Check 10-minute cooldown for this user
        async with self._lock:
            if interaction.user.id in self.user_cooldowns:
                last_vote = self.user_cooldowns[interaction.user.id]
                now = datetime.now(timezone.utc)
                if now - last_vote < timedelta(minutes=10):
                    time_left = 10 - int((now - last_vote).total_seconds() / 60)
                    await interaction.response.send_message(
                        f"❌ You can start another vote in {time_left} minutes.",
                        ephemeral=True
                    )
                    return
        
        # Verify all conditions
        valid, voice_channel, error_msg = await self.verify_same_vc(interaction, user)
        if not valid:
            await interaction.response.send_message(f"❌ {error_msg}", ephemeral=True)
            return
        
        # Prevent self-targeting
        if user.id == interaction.user.id:
            await interaction.response.send_message(
                "❌ You cannot start a vote against yourself.",
                ephemeral=True
            )
            return
        
        # Prevent targeting bots
        if user.bot:
            await interaction.response.send_message(
                "❌ You cannot timeout bots with this command.",
                ephemeral=True
            )
            return
        
        # Prevent targeting users with higher permissions
        if interaction.user.top_role <= user.top_role and interaction.user != interaction.guild.owner:
            await interaction.response.send_message(
                "❌ You cannot start a vote against someone with higher or equal permissions.",
                ephemeral=True
            )
            return
        
        # Get VC members (excluding bots)
        vc_members = await self.get_vc_members(voice_channel)
        vc_member_ids = [member.id for member in vc_members]
        
        # Calculate required votes (60% rounded up)
        required_votes = math.ceil(len(vc_members) * 0.6)
        
        # Minimum of 2 votes required
        if required_votes < 2:
            required_votes = 2
        
        # Create embed
        embed = discord.Embed(
            title="⏰ Vote to Timeout",
            description=f"Vote to timeout {user.mention} for 5 minutes",
            color=discord.Color.orange(),
            timestamp=datetime.now(timezone.utc)
        )
        
        embed.add_field(
            name="📊 Vote Info",
            value=f"**Required Votes:** {required_votes}/{len(vc_members)}\n"
                  f"**Timeout Duration:** 5 minutes\n"
                  f"**Started by:** {interaction.user.mention}",
            inline=False
        )
        
        embed.add_field(
            name="👥 Eligible Voters",
            value=f"Members in {voice_channel.mention}: {len(vc_members)}\n"
                  f"All voters must remain in the voice channel!",
            inline=False
        )
        
        embed.add_field(
            name="✅ How to Vote",
            value="React with ✅ to vote YES\nReact with ❌ to vote NO",
            inline=False
        )
        
        embed.add_field(
            name="⚠️ Rules",
            value="• Only members in the VC can vote\n"
                  "• All participants must stay in the VC\n"
                  "• One vote per person\n"
                  "• Vote expires in 2 minutes",
            inline=False
        )
        
        embed.set_footer(text="Vote expires in 2 minutes | Must stay in VC to vote")
        
        # Send the embed
        await interaction.response.send_message(embed=embed)
        message = await interaction.original_response()
        
        # Add reactions
        await message.add_reaction("✅")
        await message.add_reaction("❌")
        
        # Store vote data
        async with self._lock:
            self.active_votes[message.id] = {
                'target_id': user.id,
                'channel_id': voice_channel.id,
                'starter_id': interaction.user.id,
                'voters': set(),  # Track who voted
                'yes_votes': 0,
                'no_votes': 0,
                'required_votes': required_votes,
                'total_members': len(vc_members),
                'vc_member_ids': vc_member_ids,  # Store who was initially in VC
                'created_at': datetime.now(timezone.utc),
                'guild_id': interaction.guild.id,
                'message_channel_id': interaction.channel.id,
                'message_id': message.id,
                'passed': False  # Track if vote has passed
            }
            
            # Set cooldown for starter (10 minutes regardless of outcome)
            self.user_cooldowns[interaction.user.id] = datetime.now(timezone.utc)
    
    async def is_valid_voter(self, vote_data: dict, user: discord.Member) -> tuple[bool, str]:
        """Check if a user is a valid voter"""
        guild = self.bot.get_guild(vote_data['guild_id'])
        if not guild:
            return False, "Guild not found"
        
        voice_channel = guild.get_channel(vote_data['channel_id'])
        if not voice_channel:
            return False, "Voice channel not found"
        
        # Check if user is in the VC
        if not user.voice or user.voice.channel != voice_channel:
            return False, "You must be in the voice channel to vote"
        
        # Get current VC members (excluding bots)
        vc_members = await self.get_vc_members(voice_channel)
        vc_member_ids = [member.id for member in vc_members]
        
        # Check if user is in the VC
        if user.id not in vc_member_ids:
            return False, "You must be in the voice channel to vote"
        
        return True, ""
    
    @commands.Cog.listener()
    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.Member):
        """Handle reaction votes"""
        # Ignore bot reactions
        if user.bot:
            return
        
        message_id = reaction.message.id
        
        # Check if this is an active vote
        if message_id not in self.active_votes:
            return
        
        vote_data = self.active_votes[message_id]
        
        # If vote already passed, ignore further reactions
        if vote_data.get('passed', False):
            try:
                await reaction.remove(user)
            except:
                pass
            return
        
        # Check if user is trying to vote multiple times
        if user.id in vote_data['voters']:
            # Remove their reaction if they already voted
            try:
                await reaction.remove(user)
            except:
                pass
            return
        
        # Check if user is a valid voter (in the same VC)
        valid, error_msg = await self.is_valid_voter(vote_data, user)
        if not valid:
            # Remove reaction if user isn't valid
            try:
                await reaction.remove(user)
            except:
                pass
            return
        
        # Get current VC info
        guild = self.bot.get_guild(vote_data['guild_id'])
        if not guild:
            return
        
        voice_channel = guild.get_channel(vote_data['channel_id'])
        if not voice_channel:
            return
        
        # Check if initiator is still in VC (only check initiator, not target)
        initiator = guild.get_member(vote_data['starter_id'])
        if not initiator or not initiator.voice or initiator.voice.channel != voice_channel:
            # Cancel vote if initiator left
            try:
                await reaction.message.edit(embed=discord.Embed(
                    title="❌ Vote Cancelled",
                    description="Vote initiator left the voice channel.",
                    color=discord.Color.red()
                ))
            except:
                pass
            finally:
                # Safely delete from active votes
                async with self._lock:
                    try:
                        del self.active_votes[message_id]
                    except KeyError:
                        pass
            return
        
        # Get current VC members for recalculating
        vc_members = await self.get_vc_members(voice_channel)
        
        # Recalculate required votes based on current VC population
        new_required_votes = math.ceil(len(vc_members) * 0.6)
        if new_required_votes < 2:
            new_required_votes = 2
        
        # Update required votes if VC population changed
        vote_data['required_votes'] = new_required_votes
        vote_data['total_members'] = len(vc_members)
        
        # Count the vote
        vote_data['voters'].add(user.id)
        
        if str(reaction.emoji) == "✅":
            vote_data['yes_votes'] += 1
        elif str(reaction.emoji) == "❌":
            vote_data['no_votes'] += 1
        else:
            # Remove invalid reaction
            try:
                await reaction.remove(user)
            except:
                pass
            vote_data['voters'].remove(user.id)  # Remove from voters since we removed reaction
            return
        
        # Update embed with current count
        try:
            embed = reaction.message.embeds[0]
            
            # Update fields
            embed.set_field_at(
                0,
                name="📊 Vote Info",
                value=f"**Required Votes:** {vote_data['yes_votes']}/{vote_data['required_votes']}\n"
                      f"**Current Votes:** ✅ {vote_data['yes_votes']} | ❌ {vote_data['no_votes']}\n"
                      f"**Started by:** <@{vote_data['starter_id']}>",
                inline=False
            )
            
            embed.set_field_at(
                1,
                name="👥 Eligible Voters",
                value=f"Members in {voice_channel.mention}: {len(vc_members)}\n"
                      f"All voters must remain in the voice channel!",
                inline=False
            )
            
            await reaction.message.edit(embed=embed)
            
            # Check if vote passed
            if vote_data['yes_votes'] >= vote_data['required_votes']:
                # Mark vote as passed
                vote_data['passed'] = True
                
                # Get target user
                target = guild.get_member(vote_data['target_id'])
                
                # Check if user is already timed out
                now = datetime.now(timezone.utc)
                if target and target.timed_out_until and target.timed_out_until > now:
                    # User already timed out, show success message
                    success_embed = discord.Embed(
                        title="✅ Successfully SYBAU'ed!",
                        description=f"{target.mention} is already timed out!",
                        color=discord.Color.green()
                    )
                    success_embed.add_field(
                        name="Vote Results",
                        value=f"✅ Yes: {vote_data['yes_votes']}\n❌ No: {vote_data['no_votes']}",
                        inline=False
                    )
                    success_embed.add_field(
                        name="Current Timeout",
                        value=f"Until {target.timed_out_until.strftime('%H:%M:%S UTC')}",
                        inline=False
                    )
                    
                    await reaction.message.edit(embed=success_embed)
                else:
                    # Try to timeout the user for 5 MINUTES
                    if target:
                        try:
                            timeout_duration = timedelta(minutes=5)
                            await target.timeout(
                                timeout_duration,
                                reason=f"Vote timeout by VC members. {vote_data['yes_votes']}/{vote_data['total_members']} voted yes."
                            )
                            
                            # Show SUCCESS message with SYBAU wording
                            success_embed = discord.Embed(
                                title="✅ Successfully SYBAU'ed!",
                                description=f"{target.mention} has been timed out for 5 minutes.",
                                color=discord.Color.green()
                            )
                            success_embed.add_field(
                                name="Vote Results",
                                value=f"✅ Yes: {vote_data['yes_votes']}\n❌ No: {vote_data['no_votes']}",
                                inline=False
                            )
                            success_embed.add_field(
                                name="Voice Channel",
                                value=f"{voice_channel.mention}\nParticipants: {len(vc_members)}",
                                inline=False
                            )
                            
                            await reaction.message.edit(embed=success_embed)
                            
                        except discord.Forbidden:
                            # If no permission, still show success message (vote passed)
                            success_embed = discord.Embed(
                                title="✅ Vote Passed! (Permission Error)",
                                description=f"Vote to timeout {target.mention} passed but I don't have permission to timeout users.",
                                color=discord.Color.orange()
                            )
                            success_embed.add_field(
                                name="Vote Results",
                                value=f"✅ Yes: {vote_data['yes_votes']}\n❌ No: {vote_data['no_votes']}",
                                inline=False
                            )
                            await reaction.message.edit(embed=success_embed)
                        except discord.HTTPException as e:
                            # If other error, still show success message (vote passed)
                            success_embed = discord.Embed(
                                title="✅ Vote Passed! (Execution Error)",
                                description=f"Vote to timeout {target.mention} passed but failed to apply timeout: {str(e)}",
                                color=discord.Color.orange()
                            )
                            success_embed.add_field(
                                name="Vote Results",
                                value=f"✅ Yes: {vote_data['yes_votes']}\n❌ No: {vote_data['no_votes']}",
                                inline=False
                            )
                            await reaction.message.edit(embed=success_embed)
                    else:
                        # Target not found (possibly left server), still show success
                        success_embed = discord.Embed(
                            title="✅ Vote Passed!",
                            description=f"Vote passed but user is no longer in the server.",
                            color=discord.Color.green()
                        )
                        success_embed.add_field(
                            name="Vote Results",
                            value=f"✅ Yes: {vote_data['yes_votes']}\n❌ No: {vote_data['no_votes']}",
                            inline=False
                        )
                        await reaction.message.edit(embed=success_embed)
                
                # Remove from active votes
                async with self._lock:
                    try:
                        del self.active_votes[message_id]
                    except KeyError:
                        pass
                
            # Check if vote failed (more than 40% voted no)
            elif vote_data['no_votes'] > vote_data['total_members'] * 0.4:
                target = guild.get_member(vote_data['target_id'])
                target_name = target.mention if target else "User"
                
                failed_embed = discord.Embed(
                    title="❌ Vote Failed",
                    description=f"Vote to timeout {target_name} has failed (too many no votes).",
                    color=discord.Color.red()
                )
                failed_embed.add_field(
                    name="Vote Results",
                    value=f"✅ Yes: {vote_data['yes_votes']}\n❌ No: {vote_data['no_votes']}",
                    inline=False
                )
                failed_embed.add_field(
                    name="Voice Channel",
                    value=f"{voice_channel.mention}\nParticipants: {len(vc_members)}",
                    inline=False
                )
                
                await reaction.message.edit(embed=failed_embed)
                # Safely delete from active votes
                async with self._lock:
                    try:
                        del self.active_votes[message_id]
                    except KeyError:
                        pass
                
        except Exception as e:
            logger.error(f"Error updating vote: {e}")
            import traceback
            traceback.print_exc()
    
    @commands.Cog.listener()
    async def on_reaction_remove(self, reaction: discord.Reaction, user: discord.Member):
        """Handle reaction removal"""
        if user.bot:
            return
        
        message_id = reaction.message.id
        
        if message_id in self.active_votes and user.id in self.active_votes[message_id]['voters']:
            vote_data = self.active_votes[message_id]
            
            # Don't allow vote removal if vote already passed
            if vote_data.get('passed', False):
                return
            
            if str(reaction.emoji) == "✅":
                vote_data['yes_votes'] -= 1
            elif str(reaction.emoji) == "❌":
                vote_data['no_votes'] -= 1
            
            vote_data['voters'].remove(user.id)
            
            # Update embed
            try:
                embed = reaction.message.embeds[0]
                embed.set_field_at(
                    0,
                    name="📊 Vote Info",
                    value=f"**Required Votes:** {vote_data['yes_votes']}/{vote_data['required_votes']}\n"
                          f"**Current Votes:** ✅ {vote_data['yes_votes']} | ❌ {vote_data['no_votes']}\n"
                          f"**Started by:** <@{vote_data['starter_id']}>",
                    inline=False
                )
                await reaction.message.edit(embed=embed)
            except:
                pass
    
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        """Monitor voice state changes - only cancel if initiator leaves"""
        
        # Check if member was in a VC before
        if before.channel:
            # Get votes where this user is initiator
            votes_to_cancel = []
            
            # Create a copy of items to avoid dictionary changed during iteration
            for message_id, vote_data in list(self.active_votes.items()):
                if vote_data['guild_id'] != member.guild.id:
                    continue
                
                # Skip if vote already passed
                if vote_data.get('passed', False):
                    continue
                
                voice_channel = member.guild.get_channel(vote_data['channel_id'])
                if not voice_channel or voice_channel != before.channel:
                    continue
                
                # Only cancel if initiator leaves
                if member.id == vote_data['starter_id']:
                    votes_to_cancel.append((message_id, "Vote initiator left the voice channel."))
            
            # Process cancellations
            for message_id, reason in votes_to_cancel:
                try:
                    vote_data = self.active_votes.get(message_id)
                    if not vote_data:
                        continue
                        
                    channel = member.guild.get_channel(vote_data['message_channel_id'])
                    if channel:
                        try:
                            msg = await channel.fetch_message(message_id)
                            await msg.edit(embed=discord.Embed(
                                title="❌ Vote Cancelled",
                                description=reason,
                                color=discord.Color.red()
                            ))
                        except discord.NotFound:
                            pass
                        except discord.HTTPException as e:
                            logger.error(f"Failed to edit cancelled vote message: {e}")
                except Exception as e:
                    logger.error(f"Error cancelling vote {message_id}: {e}")
                finally:
                    # Safely delete from active votes
                    async with self._lock:
                        try:
                            del self.active_votes[message_id]
                        except KeyError:
                            pass

async def setup(bot):
    await bot.add_cog(VoteTimeout(bot))