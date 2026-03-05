import discord
from discord.ext import commands, tasks
from .debate_config import (
    FORUM_CHANNEL_ID,
    HOT_TAG_NAME,
    INACTIVE_HOURS,
    STATUS_EMOJIS,
    ACTIVITY_THRESHOLD,
    COLORS,
)
from datetime import datetime, timedelta
import asyncio

import logging

logger = logging.getLogger(__name__)


class DebateAutomation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.check_activity.start()
        self.last_hot_check = {}

    def cog_unload(self):
        self.check_activity.cancel()

    @tasks.loop(minutes=30)
    async def check_activity(self):
        """Check debate activity and update status/close inactive"""
        await self.bot.wait_until_ready()

        forum = self.bot.get_channel(FORUM_CHANNEL_ID)
        if not forum:
            return

        # Clean expired mutes from DB
        await self.bot.db.clean_expired_mutes()

        for thread in forum.threads:
            if thread.archived:
                continue

            # Get thread stats from DB
            debate_info = await self.bot.db.get_debate_info(str(thread.id))

            if not debate_info:
                continue

            last_activity_str, message_count, closed_at = debate_info
            last_activity = (
                datetime.fromisoformat(last_activity_str) if last_activity_str else None
            )

            if closed_at:
                continue

            # Check for inactivity
            if last_activity and (datetime.now() - last_activity) > timedelta(
                hours=INACTIVE_HOURS
            ):
                await self.close_debate(thread)
                await asyncio.sleep(2)  # Rate limit protection for API call
                continue

            # Update status emoji in title
            await self.update_thread_status(thread, last_activity, message_count or 0)

            # Apply hot tag based on activity level
            await self.apply_hot_tag(thread, message_count or 0, last_activity)

    async def update_thread_status(self, thread, last_activity, message_count):
        """Update thread title with activity emoji"""
        if not last_activity:
            return

        # Calculate activity level
        hours_since = (datetime.now() - last_activity).total_seconds() / 3600
        if hours_since < 24:
            msgs_per_day = message_count / max(1, hours_since / 24)
            if msgs_per_day >= ACTIVITY_THRESHOLD["high"]:
                emoji = STATUS_EMOJIS["high"]
            elif msgs_per_day >= ACTIVITY_THRESHOLD["active"]:
                emoji = STATUS_EMOJIS["active"]
            else:
                emoji = STATUS_EMOJIS["slow"]
        else:
            emoji = STATUS_EMOJIS["slow"]

        # Update title if needed
        current_name = thread.name
        # Remove existing emoji if present
        base_name = current_name
        for e in STATUS_EMOJIS.values():
            if current_name.startswith(e):
                base_name = current_name[len(e) :].strip()
                break

        new_name = f"{emoji} {base_name}"
        if current_name != new_name:
            try:
                await thread.edit(name=new_name)
            except Exception:
                pass  # Skip if we can't edit

    async def apply_hot_tag(self, thread, message_count, last_activity):
        """Automatically apply hot tag to highly active debates"""
        if not last_activity:
            return

        thread_id = str(thread.id)

        # Check if we've checked this thread recently
        last_check = self.last_hot_check.get(thread_id)
        if last_check and (datetime.now() - last_check) < timedelta(hours=2):
            return

        # Store current check time
        self.last_hot_check[thread_id] = datetime.now()

        # Calculate activity level
        hours_since = (datetime.now() - last_activity).total_seconds() / 3600

        # Find the hot tag using the configured name
        hot_tag = None
        for tag in thread.parent.available_tags:
            if tag.name == HOT_TAG_NAME:  # Use the constant from config
                hot_tag = tag
                break

        if not hot_tag:
            return  # No hot tag configured in forum

        # Check if debate is hot
        is_hot = False
        if hours_since < 24:
            msgs_per_day = message_count / max(1, hours_since / 24)
            is_hot = msgs_per_day >= ACTIVITY_THRESHOLD["high"]

        # Apply or remove hot tag
        current_tags = list(thread.applied_tags)
        has_hot_tag = hot_tag in current_tags

        try:
            if is_hot and not has_hot_tag and len(current_tags) < 5:
                # Add hot tag
                current_tags.append(hot_tag)
                await thread.edit(applied_tags=current_tags)
                logger.info(f"🔥 Applied hot tag to {thread.name}")

                # Optional: Send a celebratory message
                await thread.send(
                    content=f"🔥 This debate is heating up! It's now tagged as **{HOT_TAG_NAME}**!",
                    delete_after=60,
                )
                await asyncio.sleep(2)  # Only sleep when we actually made an edit

            elif not is_hot and has_hot_tag:
                # Remove hot tag
                current_tags.remove(hot_tag)
                await thread.edit(applied_tags=current_tags)
                logger.info(f"❄️ Removed hot tag from {thread.name}")
                await asyncio.sleep(2)  # Only sleep when we actually made an edit

        except discord.HTTPException as e:
            print(f"Error updating hot tag for {thread.name}: {e}")

    async def close_debate(self, thread):
        """Close a debate and post summary"""
        # Get debate info
        debate_info = await self.bot.db.get_debate_info(str(thread.id))
        if not debate_info:
            return

        # Get additional info
        participant_count = await self.bot.db.get_participant_count(str(thread.id))
        top_participant = await self.bot.db.get_top_participant(str(thread.id))
        duration = await self.bot.db.get_debate_duration(
            str(thread.id)
        )  # Now this works!

        # Create summary embed
        embed = discord.Embed(
            title="📊 Debate Summary", color=COLORS["info"], timestamp=datetime.now()
        )

        # Try to get topic from thread name
        topic = thread.name
        for e in STATUS_EMOJIS.values():
            if topic.startswith(e):
                topic = topic[len(e) :].strip()
                break

        embed.description = f"**{topic}**"
        embed.add_field(name="Participants", value=participant_count or 0)
        embed.add_field(name="Total Messages", value=debate_info[1] or 0)

        if top_participant:
            user = self.bot.get_user(int(top_participant[0]))
            if user:
                embed.add_field(
                    name="Most Active",
                    value=f"{user.mention} ({top_participant[1]} msgs)",
                )
            else:
                embed.add_field(
                    name="Most Active",
                    value=f"<@{top_participant[0]}> ({top_participant[1]} msgs)",
                )

        if duration:
            embed.add_field(name="Duration", value=f"{duration:.1f} days")

        # Post summary and close
        try:
            await thread.send(embed=embed)
        except Exception:
            pass

        await self.bot.db.close_debate(str(thread.id))

        try:
            # Remove status emoji and add closed emoji
            base_name = thread.name
            for e in STATUS_EMOJIS.values():
                if base_name.startswith(e):
                    base_name = base_name[len(e) :].strip()
                    break
            await thread.edit(archived=True, locked=True, name=f"🔒 {base_name}")
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_message(self, message):
        """Track activity and handle muted users + dashboard cleaning"""
        if message.author.bot:
            return

        if not isinstance(message.channel, discord.Thread):
            return

        thread = message.channel
        if (
            not isinstance(thread.parent, discord.ForumChannel)
            or thread.parent.id != FORUM_CHANNEL_ID
        ):
            return

        # --- DASHBOARD CLEANING (Priority 1) ---
        # Check if this is the dashboard thread first
        if thread.name == "📋 Debate Dashboard":
            try:
                # Delete the user's message
                await message.delete()

                # Send a brief warning that auto-deletes
                warning = await thread.send(
                    f"{message.author.mention} ⚠️ Please use the buttons below to interact. "
                    f"This thread is for dashboard use only.",
                    delete_after=5,
                )
                return  # Stop processing - don't track activity for dashboard
            except discord.Forbidden:
                pass  # Bot doesn't have permission to delete messages
            except discord.HTTPException as e:
                logger.error(f"Error deleting dashboard message: {e}")
                return

        # --- MUTE CHECKING (Priority 2) ---
        # Check if user is muted
        muted_users = await self.bot.db.get_all_mutes()
        muted_user_ids = [m["user_id"] for m in muted_users]

        if str(message.author.id) in muted_user_ids:
            # Find the mute info
            mute_info = next(
                (m for m in muted_users if m["user_id"] == str(message.author.id)), None
            )
            if mute_info:
                # Check if mute expired
                if mute_info["expiry"] and mute_info["expiry"] < datetime.now():
                    # Auto unmute
                    await self.bot.db.remove_mute(str(message.author.id))
                else:
                    # Delete the message
                    try:
                        await message.delete()

                        # Optional: Send a brief warning to the user (will be deleted quickly)
                        warning = await thread.send(
                            f"🔇 {message.author.mention} You are currently silenced in debates. "
                            f"Reason: {mute_info['reason']}",
                            delete_after=3,
                        )
                    except Exception:
                        pass
                    return  # Stop processing this message

        # --- ACTIVITY TRACKING (Only for non-dashboard, non-muted users) ---
        # Original activity tracking continues below...
        await self.bot.db.update_debate_activity(str(thread.id))
        await self.bot.db.add_participant(str(message.author.id), str(thread.id))
        await self.bot.db.update_participant_activity(
            str(message.author.id), str(thread.id)
        )

        # Add weekly points (1 per message) - WITH ANTI-SPAM
        points_added = await self.bot.db.add_weekly_score(
            str(message.author.id), 1, message_sent=True
        )

        # If points weren't added due to rate limit, maybe send subtle warning?
        if not points_added:
            # Optional: React with ⏳ to indicate rate limited
            try:
                await message.add_reaction("⏳")
                # Remove after 2 seconds
                await asyncio.sleep(2)
                await message.clear_reaction("⏳")
            except Exception:
                pass

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction, user):
        """Track reactions for quality points - with duplicate prevention"""
        if user.bot:
            return

        if not isinstance(reaction.message.channel, discord.Thread):
            return

        thread = reaction.message.channel
        if (
            not isinstance(thread.parent, discord.ForumChannel)
            or thread.parent.id != FORUM_CHANNEL_ID
        ):
            return

        # Don't count self-reactions
        if reaction.message.author.id == user.id:
            return

        # Check if this reaction has already been counted
        reaction_counted = await self.bot.db.check_reaction_exists(
            str(reaction.message.id), str(user.id)
        )

        if not reaction_counted:
            # Add bonus points (0.5 per reaction) - with anti-spam
            # FIXED: Removed from_reaction argument
            points_added = await self.bot.db.add_weekly_score(
                str(reaction.message.author.id),
                0.5,
                message_sent=False,  # This is a reaction, not a message
                debate_created=False,
            )

            # Record this reaction to prevent duplicates
            await self.bot.db.record_reaction(
                str(reaction.message.id), str(reaction.message.author.id), str(user.id)
            )

            # If points weren't added due to rate limit, maybe notify?
            if not points_added:
                try:
                    # Subtle hint that they're rate limited
                    await reaction.message.channel.send(
                        f"ℹ️ {reaction.message.author.mention} is at their hourly point limit. Reaction points not added.",
                        delete_after=5,
                    )
                except discord.HTTPException:
                    pass


async def setup(bot):
    await bot.add_cog(DebateAutomation(bot))
