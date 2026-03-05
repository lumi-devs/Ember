import discord
from discord.ext import tasks
import random
from datetime import datetime, timedelta
import pytz
from typing import List, Tuple, Optional, Dict, Set
from collections import defaultdict
import asyncio


class AutoPairSystem:
    def __init__(self, cog):
        self.cog = cog
        self.bot = cog.bot

        # Configuration from environment
        import ast
        import os

        self.MOD_ROLES = ast.literal_eval(os.environ.get("MOD_ROLES", "[]"))

        self.INACTIVITY_THRESHOLD_MINUTES = 60
        self.MIN_MESSAGES_FOR_ACTIVE = 50
        self.MAX_PAIRINGS_PER_MOD = 2
        self.PAIRING_COOLDOWN_HOURS = 12
        self.message_counts = {}
        # Data manager
        from .data_manager import DataManager

        self.data_manager = DataManager()
        self.system_data = self.data_manager.load_system_data()
        self.system_data.setdefault("sticky_messages", {})

        # Event-based activity tracking
        self.last_seen_times = {}  # member_id -> last_seen timestamp
        self.activity_check_queue = asyncio.Queue()
        self.debounce_tasks = {}  # member_id -> asyncio.Task

        # Role member caching
        self.role_member_cache = {}  # guild_id -> {role_id: Set[member_ids]}
        self.cache_last_updated = {}
        self.CACHE_TTL_SECONDS = 300  # 5 minutes

        # Messages to delete after 1 hour
        self.system_data = self.data_manager.load_system_data()
        self.member_activity = self.system_data.get("member_activity", {})
        self.priority_queue = self.system_data.get("priority_queue", [])
        self.messages_to_delete = {}
        for msg_id, data in self.system_data.get("messages_to_delete", {}).items():
            if "delete_at" in data:
                data["delete_at"] = datetime.fromisoformat(data["delete_at"])
                self.messages_to_delete[int(msg_id)] = data

        # Start cleanup task only (event-based for main logic)
        self.cleanup_old_messages.start()
        self.periodic_scan.start()
        self.periodic_data_save.start()
        # Dynamic priority queue
        self.priority_lock = asyncio.Lock()

    async def initialize_after_ready(self):
        """Initialize after bot is ready"""
        await self.bot.wait_until_ready()

        # Validate queue
        await self.validate_priority_queue()

        # Clean up invalid pairings
        await self.cleanup_invalid_pairings()

        # Start periodic scans for all guilds
        for guild in self.bot.guilds:
            await self.periodic_scan_single_guild(guild)

        print(
            f"[Auto-Pair] Initialized with {len(self.priority_queue)} in queue, {len(self.system_data.get('pairings', {}))} pairings"
        )

    def stop_tasks(self):
        """Stop all background tasks"""
        self.cleanup_old_messages.cancel()

        # Cancel all debounce tasks
        for task in self.debounce_tasks.values():
            task.cancel()

    def get_staff_channel(self, guild):
        """Get staff channel from cog"""
        return self.cog.get_staff_channel(guild)

    # ========== EVENT-BASED SCANNING IMPROVEMENTS ==========

    async def on_member_join(self, member: discord.Member):
        """Handle new member joins"""
        if member.bot:
            return

        # Schedule activity check after initial period
        await asyncio.sleep(1800)  # 30 minutes

        # Check if they have newbie role
        newbie_role = member.guild.get_role(self.cog.NEWBIE_ROLE_ID)
        if newbie_role and newbie_role in member.roles:
            # Add to priority queue
            await self.add_to_priority_queue(member)

    async def on_message(self, message: discord.Message):
        """Track messages for activity monitoring (event-based)"""
        if message.author.bot or not message.guild:
            return

        member = message.author
        member_id = str(member.id)
        guild_id = str(message.guild.id)
        now = datetime.now(pytz.UTC)

        # Update activity tracking
        if member_id not in self.member_activity:
            self.member_activity[member_id] = {
                "last_message": now,
                "message_count": 0,
                "guild_id": guild_id,
            }

        self.member_activity[member_id]["last_message"] = now
        self.member_activity[member_id]["message_count"] = (
            self.member_activity[member_id].get("message_count", 0) + 1
        )

        # Check if this member is a newbie
        newbie_role = message.guild.get_role(self.cog.NEWBIE_ROLE_ID)
        if newbie_role and newbie_role in member.roles:
            # Debounce activity check
            await self.schedule_debounced_check(member_id, guild_id)

            # Update priority queue score (lower priority when active)
            async with self.priority_lock:
                self.priority_queue = [
                    (score, mid, gid)
                    for score, mid, gid in self.priority_queue
                    if mid != member_id
                ]

    async def on_member_update(self, before: discord.Member, after: discord.Member):
        """Handle role changes"""
        newbie_role = after.guild.get_role(self.cog.NEWBIE_ROLE_ID)
        if not newbie_role:
            return

        # Check if member gained/lost newbie role
        had_role = newbie_role in before.roles
        has_role = newbie_role in after.roles

        if not had_role and has_role:
            # Member gained newbie role - add to priority queue
            await self.add_to_priority_queue(after)
        elif had_role and not has_role:
            # Member lost newbie role - remove from queue
            async with self.priority_lock:
                self.priority_queue = [
                    (score, mid, gid)
                    for score, mid, gid in self.priority_queue
                    if mid != str(after.id)
                ]

    async def schedule_debounced_check(self, member_id: str, guild_id: str):
        """Schedule a debounced check for this member"""
        # Cancel existing task if any
        if member_id in self.debounce_tasks:
            self.debounce_tasks[member_id].cancel()

        # Create new task
        async def debounced_check():
            await asyncio.sleep(
                self.INACTIVITY_THRESHOLD_MINUTES * 60
            )  # Convert to seconds
            await self.check_member_activity(member_id, guild_id)
            self.debounce_tasks.pop(member_id, None)

        task = asyncio.create_task(debounced_check())
        self.debounce_tasks[member_id] = task

    async def check_member_activity(self, member_id: str, guild_id: str):
        """Check if member is inactive and needs pairing"""
        guild = self.bot.get_guild(int(guild_id))
        if not guild:
            return

        member = guild.get_member(int(member_id))
        if not member or member.bot:
            return

        # Get member activity data
        activity_data = self.member_activity.get(member_id)
        if not activity_data:
            return

        # Check if inactive (no messages in threshold period)
        last_message = activity_data["last_message"]
        message_count = activity_data.get("message_count", 0)

        now = datetime.now(pytz.UTC)
        minutes_inactive = (now - last_message).total_seconds() / 60

        if (
            minutes_inactive >= self.INACTIVITY_THRESHOLD_MINUTES
            and message_count < self.MIN_MESSAGES_FOR_ACTIVE
        ):
            # Check cooldown
            last_paired = self.get_last_pairing_time(member_id)
            if last_paired:
                hours_since = (now - last_paired).total_seconds() / 3600
                if hours_since < self.PAIRING_COOLDOWN_HOURS:
                    return

            # Add to priority queue with calculated score
            await self.add_to_priority_queue(member)

    def add_ignore(self, mod_id: str, newbie_id: str, guild_id: str, hours: int = 12):
        """Add an ignore record so that newbie won't be assigned to this mod."""
        expires_at = datetime.now(pytz.UTC) + timedelta(hours=hours)
        ignore_record = {
            "guild_id": guild_id,
            "mod_id": mod_id,
            "newbie_id": newbie_id,
            "expires_at": expires_at.isoformat(),
        }
        self.system_data.setdefault("ignored", []).append(ignore_record)
        self.data_manager.save_system_data(self.system_data)

    def remove_ignore(self, mod_id: str, newbie_id: str, guild_id: str):
        """Remove an existing ignore record."""
        ignored = self.system_data.get("ignored", [])
        self.system_data["ignored"] = [
            i
            for i in ignored
            if not (
                i["guild_id"] == guild_id
                and i["mod_id"] == mod_id
                and i["newbie_id"] == newbie_id
            )
        ]
        self.data_manager.save_system_data(self.system_data)

    def is_ignored(self, mod_id: str, newbie_id: str, guild_id: str) -> bool:
        """Check if a mod is ignoring a newbie (active record)."""
        self._clean_expired_ignores()
        ignored = self.system_data.get("ignored", [])
        now = datetime.now(pytz.UTC)
        for i in ignored:
            if (
                i["guild_id"] == guild_id
                and i["mod_id"] == mod_id
                and i["newbie_id"] == newbie_id
            ):
                expires_at = datetime.fromisoformat(i["expires_at"])
                if expires_at > now:
                    return True
        return False

    def _clean_expired_ignores(self):
        """Remove ignore records that have passed their expiration."""
        now = datetime.now(pytz.UTC)
        ignored = self.system_data.get("ignored", [])
        valid = [i for i in ignored if datetime.fromisoformat(i["expires_at"]) > now]
        if len(valid) != len(ignored):
            self.system_data["ignored"] = valid
            self.data_manager.save_system_data(self.system_data)

    # ========== ROLE MEMBER CACHING IMPROVEMENTS ==========

    async def get_cached_role_members(
        self, guild: discord.Guild, role_id: int
    ) -> Set[int]:
        """Get cached role members or fetch and cache"""
        guild_id = str(guild.id)
        now = datetime.now(pytz.UTC).timestamp()

        # Initialize cache for guild if needed
        if guild_id not in self.role_member_cache:
            self.role_member_cache[guild_id] = {}
            self.cache_last_updated[guild_id] = now

        # Check cache validity
        cache_age = now - self.cache_last_updated.get(guild_id, 0)
        if cache_age > self.CACHE_TTL_SECONDS:
            # Refresh entire guild cache
            await self.refresh_guild_role_cache(guild)

        # Get from cache or fetch
        if role_id not in self.role_member_cache[guild_id]:
            role = guild.get_role(role_id)
            if role:
                member_ids = {member.id for member in role.members}
                self.role_member_cache[guild_id][role_id] = member_ids
            else:
                self.role_member_cache[guild_id][role_id] = set()

        return self.role_member_cache[guild_id][role_id]

    async def refresh_guild_role_cache(self, guild: discord.Guild):
        """Refresh cache for all roles in guild"""
        guild_id = str(guild.id)
        self.role_member_cache[guild_id] = {}

        # Cache relevant roles
        roles_to_cache = [
            self.cog.NEWBIE_ROLE_ID,
            self.cog.MALE_ROLE_ID,
            self.cog.FEMALE_ROLE_ID,
            *self.MOD_ROLES,
        ]

        for role_id in roles_to_cache:
            if role_id:
                role = guild.get_role(role_id)
                if role:
                    self.role_member_cache[guild_id][role_id] = {
                        member.id for member in role.members
                    }
                else:
                    self.role_member_cache[guild_id][role_id] = set()

        self.cache_last_updated[guild_id] = datetime.now(pytz.UTC).timestamp()

    def invalidate_role_cache(self, guild_id: str, role_id: int = None):
        """Invalidate cache for a role or entire guild"""
        if role_id and guild_id in self.role_member_cache:
            self.role_member_cache[guild_id].pop(role_id, None)
        elif guild_id in self.role_member_cache:
            del self.role_member_cache[guild_id]
            self.cache_last_updated.pop(guild_id, None)

    # ========== DYNAMIC PRIORITY QUEUE ==========

    async def add_to_priority_queue(self, member: discord.Member):
        """Add member to priority queue with calculated score"""
        member_id = str(member.id)
        guild_id = str(member.guild.id)

        # Calculate priority score
        score = await self.calculate_priority_score(member)

        async with self.priority_lock:
            # Remove existing entry if any
            self.priority_queue = [
                (s, mid, gid) for s, mid, gid in self.priority_queue if mid != member_id
            ]

            # Add with new score
            self.priority_queue.append((score, member_id, guild_id))

            # Sort by priority (higher score = higher priority)
            self.priority_queue.sort(reverse=True, key=lambda x: x[0])

            # Keep only top N in queue
            if len(self.priority_queue) > 50:
                self.priority_queue = self.priority_queue[:50]

        # Trigger pairing check if queue not empty
        await self.process_priority_queue()

    async def calculate_priority_score(self, member: discord.Member) -> float:
        """Calculate priority score based on multiple factors"""
        member_id = str(member.id)
        score = 0.0

        # 1. Time since join (recent join = higher priority)
        join_age_hours = (
            datetime.now(pytz.UTC) - member.joined_at
        ).total_seconds() / 3600
        score += min(
            100.0, 100.0 / (join_age_hours + 1)
        )  # Recent joins get higher score

        # 2. Inactivity score
        activity_data = self.member_activity.get(member_id, {})
        last_message = activity_data.get("last_message")
        message_count = activity_data.get("message_count", 0)

        if last_message:
            hours_inactive = (
                datetime.now(pytz.UTC) - last_message
            ).total_seconds() / 3600
            score += min(50.0, hours_inactive * 5)  # More inactive = higher score

        # 3. Message count (fewer messages = higher priority)
        score += max(0.0, 30.0 - (message_count * 0.5))

        # 4. Recent mentions (check last 100 messages)
        mentions_count = await self.get_recent_mentions(member)
        score += max(
            0.0, 20.0 - (mentions_count * 2)
        )  # Fewer mentions = higher priority

        # 5. Has gender role? (gender-specific pairing possible)
        male_role = member.guild.get_role(self.cog.MALE_ROLE_ID)
        female_role = member.guild.get_role(self.cog.FEMALE_ROLE_ID)

        if (male_role and male_role in member.roles) or (
            female_role and female_role in member.roles
        ):
            score += 10.0  # Bonus for having gender role

        is_manual_newbie = False
        for chan_id, list_data in self.cog.member_lists.items():
            if "members" in list_data and member_id in list_data["members"]:
                is_manual_newbie = True
                channel_id = chan_id
                break

        if is_manual_newbie:
            # Apply 60% bonus to current score
            score *= 1.6
            score = max(score, 30.0)  # Ensure minimum score for manual newbies
            print(
                f"[Priority] Member {member.display_name} gets 60% manual bonus: {score:.1f}"
            )

        # 6. Previous pairings penalty
        last_paired = self.get_last_pairing_time(member_id)
        if last_paired:
            hours_since = (datetime.now(pytz.UTC) - last_paired).total_seconds() / 3600
            if hours_since < self.PAIRING_COOLDOWN_HOURS:
                score -= 100.0  # Penalty for recent pairing

        return max(0.0, score)

    async def get_recent_mentions(
        self, member: discord.Member, limit: int = 100
    ) -> int:
        """Count recent mentions of member"""
        count = 0
        try:
            # Check in general channels
            for channel in member.guild.text_channels:
                if channel.permissions_for(member.guild.me).read_message_history:
                    try:
                        async for message in channel.history(limit=50):
                            if member in message.mentions:
                                count += 1
                    except:
                        continue
        except:
            pass
        return count

    async def process_priority_queue(self):
        """Process highest priority members for pairing"""
        async with self.priority_lock:
            if not self.priority_queue:
                return

            # Get top candidates
            top_candidates = []
            for _ in range(min(5, len(self.priority_queue))):
                score, member_id, guild_id = self.priority_queue.pop(0)
                top_candidates.append((score, member_id, guild_id))

            # Return unused candidates to queue
            self.priority_queue = top_candidates + self.priority_queue

        # Process candidates
        for score, member_id, guild_id in top_candidates:
            guild = self.bot.get_guild(int(guild_id))
            if not guild:
                continue

            member = guild.get_member(int(member_id))
            if not member:
                continue

            # Double-check inactivity before pairing
            activity_data = self.member_activity.get(member_id, {})
            message_count = activity_data.get("message_count", 0)

            if message_count < self.MIN_MESSAGES_FOR_ACTIVE:
                await self.pair_single_member(member)

    async def pair_single_member(self, member: discord.Member):
        """Pair a single member with an available mod"""
        guild = member.guild

        # Get available mods using cached role members
        available_mods = await self.get_available_mods(guild, for_newbie=member)
        if not available_mods:
            return

        # Get gender roles
        male_role = guild.get_role(self.cog.MALE_ROLE_ID)
        female_role = guild.get_role(self.cog.FEMALE_ROLE_ID)

        # Try gender-matched pairing first
        matched_mod = None
        gender_match = "mixed"

        if male_role and male_role in member.roles:
            male_mods = [
                m for m in available_mods if male_role and male_role in m.roles
            ]
            if male_mods:
                matched_mod = random.choice(male_mods)
                gender_match = "male-male"
        elif female_role and female_role in member.roles:
            female_mods = [
                m for m in available_mods if female_role and female_role in m.roles
            ]
            if female_mods:
                matched_mod = random.choice(female_mods)
                gender_match = "female-female"

        # Fall back to any mod
        if not matched_mod:
            matched_mod = random.choice(available_mods)

        # Create pairing
        now = datetime.now(pytz.UTC)
        self.system_data.setdefault("pairings", {})[str(member.id)] = {
            "mod_id": str(matched_mod.id),
            "mod_name": matched_mod.display_name,
            "newbie_id": str(member.id),
            "newbie_name": member.display_name,
            "paired_at": now.isoformat(),
            "gender_match": gender_match,
            "active": True,
            "guild_id": str(guild.id),
        }

        self.data_manager.save_system_data(self.system_data)

        # Send notification with conversational tone
        staff_channel = self.get_staff_channel(guild)
        if staff_channel:
            # Get some stats for context
            activity_data = self.member_activity.get(str(member.id), {})
            message_count = activity_data.get("message_count", 0)
            join_days = (now - member.joined_at).days

            # Create conversational message
            time_description = (
                "today"
                if join_days == 0
                else f"{join_days} day{'s' if join_days != 1 else ''} ago"
            )

            embed = discord.Embed(
                title="🎯 Newbie Mentor Assignment",
                description=(
                    f"Hey {matched_mod.mention}! 👋\n\n"
                    f"**{member.display_name}** joined {time_description} but hasn't been very active "
                    f"(only {message_count} message{'s' if message_count != 1 else ''} so far). "
                    f"Could you reach out and make them feel welcome?"
                ),
                color=discord.Color.orange(),
                timestamp=now,
            )

            # Add context about the pairing
            context_lines = []

            if message_count == 0:
                context_lines.append(
                    "• 🆕 **Brand new member** - hasn't sent any messages yet"
                )
            elif message_count < 5:
                context_lines.append("• 📝 **Very low activity** - only a few messages")
            else:
                context_lines.append(
                    f"• 📊 **Low activity** - {message_count} messages"
                )

            if join_days < 2:
                context_lines.append(f"• ⏰ **Recently joined** - {time_description}")

            # Add member info
            embed.add_field(
                name="👤 Member Info",
                value=(
                    f"**Name:** {member.display_name}\n"
                    f"**Joined:** {member.joined_at.strftime('%b %d, %Y')}\n"
                    f"**Status:** {str(member.status).title()}"
                ),
                inline=True,
            )

            # Add quick commands
            embed.add_field(
                name="⚡ Quick Commands",
                value=(
                    f"`.checkstatus {member.mention}` - Check activity\n"
                    f"`.paircomplete {member.mention}` - Mark as done\n"
                ),
                inline=True,
            )

            embed.set_footer(
                text=f"Auto-assigned based on activity • This notification disappears in 1 hour • Pairing ID: {member.id}"
            )

            # Try to include member avatar if possible
            try:
                embed.set_thumbnail(url=member.display_avatar.url)
            except:
                pass

            try:
                message = await staff_channel.send(embed=embed)
                self.schedule_message_deletion(message.id, staff_channel.id, guild.id)

                # Add a simple reaction prompt
                try:
                    await message.add_reaction("👋")  # Wave
                    await message.add_reaction("✅")  # Check mark
                    await message.add_reaction("🤝")  # Handshake
                except:
                    pass

            except Exception as e:
                print(f"[Auto-Pair] Failed to send staff notification: {e}")

    async def clear_pending_pairing(self, newbie_id: str):
        """Clear pending pairing if newbie becomes active"""
        pairing_data = self.system_data.get("pairings", {}).get(newbie_id)
        if pairing_data and pairing_data.get("active", False):
            paired_at_str = pairing_data.get("paired_at")
            if paired_at_str:
                try:
                    paired_at = datetime.fromisoformat(paired_at_str)
                    hours_since = (
                        datetime.now(pytz.UTC) - paired_at
                    ).total_seconds() / 3600
                    if hours_since < 1:
                        pairing_data["completed_at"] = datetime.now(
                            pytz.UTC
                        ).isoformat()
                        pairing_data["completed_reason"] = "became_active"
                        pairing_data["active"] = False
                        self.data_manager.save_system_data(self.system_data)

                        # Remove from priority queue
                        async with self.priority_lock:
                            self.priority_queue = [
                                (score, mid, gid)
                                for score, mid, gid in self.priority_queue
                                if mid != newbie_id
                            ]

                        # Notify in staff channel with conversational tone
                        guild = self.bot.get_guild(int(pairing_data["guild_id"]))
                        if guild:
                            staff_channel = self.get_staff_channel(guild)
                            if staff_channel:
                                newbie = guild.get_member(int(newbie_id))
                                mod = guild.get_member(int(pairing_data["mod_id"]))
                                if newbie and mod:
                                    embed = discord.Embed(
                                        title="🎉 Pairing Completed!",
                                        description=(
                                            f"Great news! {newbie.mention} just started being active in chat. "
                                            f"Looks like they're getting comfortable on their own! 🎊\n\n"
                                            f"**{mod.mention}** - no action needed from you. The pairing has been auto-completed. "
                                            f"Good job being available! 👍"
                                        ),
                                        color=discord.Color.green(),
                                        timestamp=datetime.now(pytz.UTC),
                                    )

                                    embed.add_field(
                                        name="📈 Activity Update",
                                        value=(
                                            f"**Member:** {newbie.mention}\n"
                                            f"**Started chatting:** Just now!\n"
                                            f"**Assigned to:** {mod.mention}\n"
                                            f"**Status:** Auto-completed 🏁"
                                        ),
                                        inline=False,
                                    )

                                    embed.set_footer(
                                        text="Nice! The system works! • This notification disappears in 1 hour"
                                    )

                                    message = await staff_channel.send(embed=embed)
                                    self.schedule_message_deletion(
                                        message.id, staff_channel.id, guild.id
                                    )

                                    try:
                                        await message.add_reaction("🎉")
                                        await message.add_reaction("✨")
                                    except:
                                        pass
                except Exception as e:
                    print(f"[Auto-Pair] Error clearing pending pairing: {e}")

    async def get_available_mods(
        self, guild: discord.Guild, for_newbie: discord.Member = None
    ) -> List[discord.Member]:
        """Get mods who are available and (if for_newbie given) not ignoring that newbie."""
        available_mods = []
        mod_member_ids = set()
        for role_id in self.MOD_ROLES:
            cached_members = await self.get_cached_role_members(guild, role_id)
            mod_member_ids.update(cached_members)

        for member_id in mod_member_ids:
            member = guild.get_member(member_id)
            if member and not member.bot:
                if (
                    self.count_active_pairings(str(member.id))
                    < self.MAX_PAIRINGS_PER_MOD
                ):
                    if for_newbie and self.is_ignored(
                        str(member.id), str(for_newbie.id), str(guild.id)
                    ):
                        continue
                    available_mods.append(member)
        return available_mods

    def count_active_pairings(self, mod_id: str) -> int:
        """Count how many active pairings a mod has"""
        count = 0
        guild_data = self.system_data.setdefault("pairings", {})

        for newbie_id, pairing_data in guild_data.items():
            if pairing_data.get("mod_id") == mod_id and pairing_data.get(
                "active", True
            ):
                paired_at_str = pairing_data.get("paired_at")
                if paired_at_str:
                    try:
                        paired_at = datetime.fromisoformat(paired_at_str)
                        hours_since = (
                            datetime.now(pytz.UTC) - paired_at
                        ).total_seconds() / 3600
                        if hours_since < 24:
                            count += 1
                    except:
                        count += 1

        return count

    def schedule_message_deletion(
        self, message_id: int, channel_id: int, guild_id: int
    ):
        """Schedule a message for deletion after 1 hour"""
        delete_time = datetime.now(pytz.UTC) + timedelta(hours=1)
        self.messages_to_delete[message_id] = {
            "delete_at": delete_time,
            "channel_id": channel_id,
            "guild_id": guild_id,
        }

    @tasks.loop(minutes=5)
    async def cleanup_old_messages(self):
        """Clean up old messages scheduled for deletion"""
        now = datetime.now(pytz.UTC)
        messages_to_remove = []

        for message_id, data in self.messages_to_delete.items():
            if now >= data["delete_at"]:
                try:
                    guild = self.bot.get_guild(data["guild_id"])
                    if guild:
                        channel = guild.get_channel(data["channel_id"])
                        if channel:
                            msg = await channel.fetch_message(message_id)
                            await msg.delete()
                except:
                    pass  # Message already deleted or not found
                messages_to_remove.append(message_id)

        for msg_id in messages_to_remove:
            self.messages_to_delete.pop(msg_id, None)

        # Also clean old activity data (older than 7 days)
        week_ago = now - timedelta(days=7)
        old_members = [
            mid
            for mid, data in self.member_activity.items()
            if data.get("last_message", now) < week_ago
        ]
        for mid in old_members:
            self.member_activity.pop(mid, None)

    def get_last_pairing_time(self, newbie_id: str) -> Optional[datetime]:
        """Get when this newbie was last paired"""
        pairing_data = self.system_data.get("pairings", {}).get(str(newbie_id))
        if pairing_data and "paired_at" in pairing_data:
            try:
                return datetime.fromisoformat(pairing_data["paired_at"])
            except:
                return None
        return None

    async def cleanup_invalid_pairings(self):
        """Remove pairings with invalid members or expired"""
        now = datetime.now(pytz.UTC)
        pairings_to_remove = []

        for newbie_id, pairing_data in list(
            self.system_data.get("pairings", {}).items()
        ):
            guild_id = pairing_data.get("guild_id")
            if not guild_id:
                continue

            guild = self.bot.get_guild(int(guild_id))
            if not guild:
                pairings_to_remove.append(newbie_id)
                continue

            # Check if newbie still exists
            newbie = guild.get_member(int(newbie_id))
            if not newbie or newbie.bot:
                pairings_to_remove.append(newbie_id)
                continue

            # Check if mod still exists
            mod_id = pairing_data.get("mod_id")
            if mod_id:
                mod = guild.get_member(int(mod_id))
                if not mod or mod.bot:
                    pairings_to_remove.append(newbie_id)
                    continue

            # Check if pairing is too old (more than 7 days)
            paired_at_str = pairing_data.get("paired_at")
            if paired_at_str:
                try:
                    paired_at = datetime.fromisoformat(paired_at_str)
                    if (now - paired_at).days > 7:
                        pairings_to_remove.append(newbie_id)
                except:
                    pairings_to_remove.append(newbie_id)

        # Remove invalid pairings
        for newbie_id in pairings_to_remove:
            self.system_data["pairings"].pop(newbie_id, None)

        if pairings_to_remove:
            self.data_manager.save_system_data(self.system_data)
            print(f"[Auto-Pair] Cleaned up {len(pairings_to_remove)} invalid pairings")

    async def validate_priority_queue(self):
        """Remove invalid entries from priority queue after restart"""
        async with self.priority_lock:
            valid_queue = []

            for score, member_id, guild_id in self.priority_queue:
                guild = self.bot.get_guild(int(guild_id)) if guild_id else None
                if not guild:
                    continue

                member = guild.get_member(int(member_id))
                if not member or member.bot:
                    continue

                # Check if still has newbie role
                newbie_role = guild.get_role(self.cog.NEWBIE_ROLE_ID)
                if not newbie_role or newbie_role not in member.roles:
                    continue

                # Check cooldown
                last_paired = self.get_last_pairing_time(member_id)
                if last_paired:
                    hours_since = (
                        datetime.now(pytz.UTC) - last_paired
                    ).total_seconds() / 3600
                    if hours_since < self.PAIRING_COOLDOWN_HOURS:
                        continue

                valid_queue.append((score, member_id, guild_id))

            self.priority_queue = valid_queue
            print(
                f"[Auto-Pair] Priority queue validated: {len(valid_queue)} valid entries"
            )

    # ========== PERIODIC MAINTENANCE (for edge cases) ==========

    @tasks.loop(hours=6)
    async def periodic_scan(self):
        """Periodic scan for edge cases and data restoration"""
        await self.bot.wait_until_ready()

        for guild in self.bot.guilds:
            # Refresh cache
            await self.refresh_guild_role_cache(guild)

            # Get newbie role members from cache
            newbie_member_ids = await self.get_cached_role_members(
                guild, self.cog.NEWBIE_ROLE_ID
            )

            for member_id in newbie_member_ids:
                member = guild.get_member(member_id)
                if member and not member.bot:
                    member_id_str = str(member_id)

                    # If no activity data exists, initialize with join time
                    if member_id_str not in self.member_activity:
                        self.member_activity[member_id_str] = {
                            "last_message": (
                                member.joined_at.replace(tzinfo=pytz.UTC)
                                if member.joined_at
                                else datetime.now(pytz.UTC)
                            ),
                            "message_count": 0,
                            "guild_id": str(guild.id),
                            "join_time": (
                                member.joined_at.isoformat()
                                if member.joined_at
                                else datetime.now(pytz.UTC).isoformat()
                            ),
                        }
                        # Add to priority queue with medium priority
                        await self.add_to_priority_queue(member)
                    else:
                        # Refresh existing entry with current guild info
                        self.member_activity[member_id_str]["guild_id"] = str(guild.id)

    @tasks.loop(minutes=5)
    async def periodic_data_save(self):
        """Periodically save activity data to disk"""
        self.system_data["member_activity"] = self.member_activity
        self.system_data["priority_queue"] = self.priority_queue

        # Convert messages_to_delete timestamps to ISO format for saving
        messages_to_save = {}
        for msg_id, data in self.messages_to_delete.items():
            save_data = data.copy()
            if "delete_at" in save_data and isinstance(
                save_data["delete_at"], datetime
            ):
                save_data["delete_at"] = save_data["delete_at"].isoformat()
            messages_to_save[str(msg_id)] = save_data

        self.system_data["messages_to_delete"] = messages_to_save

        self.data_manager.save_system_data(self.system_data)
