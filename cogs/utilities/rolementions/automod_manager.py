from discord.ext import tasks
import discord
from discord import AutoModTrigger, AutoModRuleAction
from discord.enums import AutoModRuleEventType, AutoModRuleTriggerType
from datetime import datetime, timedelta
import pytz
from discord.ext import commands
import os
import json
import ast
import re


class AutoModManager:
    def __init__(self, cog):
        self.cog = cog
        self.bot = cog.bot
        self.ist = pytz.timezone("Asia/Kolkata")

        # Load configuration from environment variables
        self.load_config_from_env()

        # SINGLE RULE APPROACH - We'll maintain one rule that we update
        self.main_rule_id = None  # This will store our single rule's ID
        self.rule_guild_id = None  # The guild where our rule exists

        # Track active role blocks: role_id -> {expires_at, role_name, duration}
        self.active_blocks = self.cog.data_manager.load_automod_rules()

        # Track keywords currently in the rule
        self.current_keywords = []

        # Start the main rule management task
        self.rule_manager_task.start()

        # Start cleanup task for expired blocks
        self.cleanup_task.start()

        # Start message update task
        self.message_update_task.start()

    def load_config_from_env(self):
        """Load configuration from environment variables"""
        # Load protected roles with durations from env
        # Format: {"role_id": minutes, "role_id": minutes} or {"role_id": "3h", "role_id": "2h"}
        env_roles = os.getenv("AUTOMOD_PROTECTED_ROLES", "{}")

        try:
            # Try to parse as JSON first
            raw_roles = json.loads(env_roles)
            self.PROTECTED_ROLES = {}

            for role_id, duration in raw_roles.items():
                # Handle string durations like "3h", "2h", "30m"
                if isinstance(duration, str):
                    duration_str = duration.lower()
                    if duration_str.endswith("h"):
                        minutes = int(duration_str[:-1]) * 60
                    elif duration_str.endswith("m"):
                        minutes = int(duration_str[:-1])
                    else:
                        minutes = int(duration_str)  # Assume minutes if just number
                else:
                    minutes = int(duration)  # Assume minutes if integer

                self.PROTECTED_ROLES[str(role_id)] = minutes

        except (json.JSONDecodeError, ValueError):
            # Fallback to Python dict syntax
            try:
                raw_roles = ast.literal_eval(env_roles)
                self.PROTECTED_ROLES = {}

                for role_id, duration in raw_roles.items():
                    if isinstance(duration, str):
                        duration_str = duration.lower()
                        if duration_str.endswith("h"):
                            minutes = int(duration_str[:-1]) * 60
                        elif duration_str.endswith("m"):
                            minutes = int(duration_str[:-1])
                        else:
                            minutes = int(duration_str)
                    else:
                        minutes = int(duration)

                    self.PROTECTED_ROLES[str(role_id)] = minutes
            except:
                self.PROTECTED_ROLES = {}

        # Load mention limit from env
        try:
            self.MENTION_LIMIT = int(os.getenv("AUTOMOD_MENTION_LIMIT", "3"))
        except ValueError:
            self.MENTION_LIMIT = 3

        # Default block duration for roles not specified (in minutes)
        try:
            self.DEFAULT_DURATION = int(os.getenv("AUTOMOD_DEFAULT_DURATION", "120"))
        except ValueError:
            self.DEFAULT_DURATION = 120

        # Log configuration
        print(
            f"[AutoMod] Loaded config: {len(self.PROTECTED_ROLES)} protected roles, limit: {self.MENTION_LIMIT}"
        )
        for role_id, minutes in self.PROTECTED_ROLES.items():
            hours = minutes / 60
            if hours >= 1:
                print(f"  - Role {role_id}: {hours:.1f}h ({minutes} minutes)")
            else:
                print(f"  - Role {role_id}: {minutes} minutes")

    def cog_unload(self):
        """Cleanup when cog is unloaded"""
        self.rule_manager_task.cancel()
        self.cleanup_task.cancel()
        self.message_update_task.cancel()

    @tasks.loop(seconds=60)  # Check every minute
    async def rule_manager_task(self):
        """Ensure the main AutoMod rule exists and is properly configured"""
        await self.bot.wait_until_ready()

        # If we don't have a rule yet, try to find or create one
        if not self.main_rule_id and self.bot.guilds:
            # Use the first guild (or you might want to specify which guild)
            guild = self.bot.guilds[0]
            await self.ensure_main_rule_exists(guild)

        # If we have a rule, make sure it has the correct keywords
        if self.main_rule_id and self.rule_guild_id:
            await self.update_rule_keywords()

    @tasks.loop(seconds=300)  # Update every 5 minutes
    async def message_update_task(self):
        """Update the custom block message with current time remaining"""
        await self.bot.wait_until_ready()

        if not self.main_rule_id or not self.rule_guild_id or not self.active_blocks:
            return

        try:
            guild = self.bot.get_guild(int(self.rule_guild_id))
            if not guild:
                return

            rule = await guild.fetch_automod_rule(int(self.main_rule_id))

            # Create new custom message with updated times
            if len(self.active_blocks) == 1:
                # Single role - show specific time
                role_id, block_data = next(iter(self.active_blocks.items()))
                role = guild.get_role(int(role_id))
                if role:
                    remaining = self.get_remaining_time(block_data["expires_at"])
                    custom_message = f"⚠️ @{role.name} protected ({remaining})"
                else:
                    custom_message = "⚠️ Role protected"
            else:
                # Multiple roles - compact format with times
                roles_text = []
                total_chars = 0
                for role_id, block_data in self.active_blocks.items():
                    role = guild.get_role(int(role_id))
                    if role:
                        remaining = self.get_remaining_time(block_data["expires_at"])
                        role_text = f"@{role.name}({remaining})"

                        # Check if adding this would exceed Discord's limit (~150 chars)
                        if (
                            total_chars + len(role_text) + 10 < 150
                        ):  # Leave room for prefix
                            roles_text.append(role_text)
                            total_chars += len(role_text)
                        else:
                            roles_text.append(
                                f"+{len(self.active_blocks) - len(roles_text)} more"
                            )
                            break

                custom_message = f"⚠️ Protected: {' '.join(roles_text)}"

            # Update the rule with new message (keeping same trigger)
            await rule.edit(
                actions=[
                    AutoModRuleAction(
                        type=discord.AutoModRuleActionType.block_message,
                        custom_message=custom_message,
                    )
                ]
            )

            print(f"[AutoMod] Updated block message: {custom_message}")

        except Exception as e:
            print(f"[AutoMod] Error updating block message: {e}")

    def get_remaining_time(self, expires_at):
        """Get human-readable remaining time"""
        now = datetime.now(self.ist)
        if expires_at <= now:
            return "expired"

        remaining = expires_at - now
        total_minutes = int(remaining.total_seconds() / 60)

        if total_minutes >= 60:
            hours = total_minutes / 60
            if hours >= 24:
                days = int(hours / 24)
                hours_remain = int(hours % 24)
                if hours_remain > 0:
                    return f"{days}d{hours_remain}h"
                else:
                    return f"{days}d"
            else:
                remaining_minutes = total_minutes % 60
                if remaining_minutes > 0:
                    return f"{int(hours)}h{remaining_minutes}m"
                else:
                    return f"{int(hours)}h"
        else:
            return f"{total_minutes}m"

    async def ensure_main_rule_exists(self, guild):
        """Create the main AutoMod rule if it doesn't exist"""
        try:
            # Check if bot has permissions
            if not guild.me.guild_permissions.manage_guild:
                print(f"[AutoMod] Missing 'Manage Guild' permission in {guild.name}")
                return

            # Look for existing rule with our special name
            existing_rules = await guild.fetch_automod_rules()
            for rule in existing_rules:
                if rule.name == "🛡️ Dynamic Role Protection":
                    self.main_rule_id = str(rule.id)
                    self.rule_guild_id = str(guild.id)

                    # Get current keywords from the rule
                    if hasattr(rule.trigger, "keyword_filter"):
                        self.current_keywords = rule.trigger.keyword_filter

                    print(f"[AutoMod] Found existing main rule: {self.main_rule_id}")
                    return

            # Create new rule if none exists
            rule = await guild.create_automod_rule(
                name="🛡️ Dynamic Role Protection",
                event_type=AutoModRuleEventType.message_send,
                trigger=AutoModTrigger(
                    type=AutoModRuleTriggerType.keyword,
                    keyword_filter=[
                        "dummy_keyword_that_will_be_replaced"
                    ],  # Temporary placeholder
                ),
                actions=[
                    AutoModRuleAction(
                        type=discord.AutoModRuleActionType.block_message,
                        custom_message="This role is temporarily protected from mention spam.",
                    )
                ],
                enabled=True,
                reason="Dynamic role protection system initialized",
            )

            self.main_rule_id = str(rule.id)
            self.rule_guild_id = str(guild.id)
            self.current_keywords = []

            print(f"[AutoMod] Created main rule: {self.main_rule_id}")

            # Update with actual keywords
            await self.update_rule_keywords()

        except Exception as e:
            print(f"[AutoMod] Error ensuring main rule exists: {e}")

    async def update_rule_keywords(self):
        """Update the main rule's keyword list based on active blocks"""
        if not self.main_rule_id or not self.rule_guild_id:
            return

        # Build keyword list from active blocks
        new_keywords = []
        guild = self.bot.get_guild(int(self.rule_guild_id))

        if not guild:
            return

        for role_id, block_data in self.active_blocks.items():
            # Add the role mention as a keyword
            role_mention = f"<@&{role_id}>"
            new_keywords.append(role_mention)

            # Also add the role name for text-based detection
            role = guild.get_role(int(role_id))
            if role:
                new_keywords.append(role.name.lower())

        # If no active blocks, add a dummy keyword (AutoMod requires at least 1)
        if not new_keywords:
            new_keywords = ["no_active_blocks_placeholder"]

        # Remove duplicates
        new_keywords = list(set(new_keywords))

        # Check if keywords have changed
        if set(new_keywords) != set(self.current_keywords):
            try:
                guild = self.bot.get_guild(int(self.rule_guild_id))
                if guild:
                    rule = await guild.fetch_automod_rule(int(self.main_rule_id))

                    # Update the rule with new keywords
                    await rule.edit(
                        trigger=AutoModTrigger(
                            type=AutoModRuleTriggerType.keyword,
                            keyword_filter=new_keywords,
                        )
                    )

                    self.current_keywords = new_keywords
                    print(f"[AutoMod] Updated rule keywords: {len(new_keywords)} total")

                    # Also update the message for the new role
                    await self.update_block_message(rule)

                    # Log the update if there are active blocks
                    if self.active_blocks:
                        await self.log_rule_update(guild, new_keywords)

            except Exception as e:
                print(f"[AutoMod] Error updating rule keywords: {e}")

    async def update_block_message(self, rule=None):
        """Update just the block message without changing keywords"""
        if not self.main_rule_id or not self.rule_guild_id or not self.active_blocks:
            return

        try:
            guild = self.bot.get_guild(int(self.rule_guild_id))
            if not guild:
                return

            if not rule:
                rule = await guild.fetch_automod_rule(int(self.main_rule_id))

            # Get current actions
            current_actions = rule.actions

            # Create new custom message with updated times
            if len(self.active_blocks) == 1:
                role_id, block_data = next(iter(self.active_blocks.items()))
                role = guild.get_role(int(role_id))
                if role:
                    remaining = self.get_remaining_time(block_data["expires_at"])
                    custom_message = f"⚠️ **{role.name}** is protected for another {remaining}. Please do not mention this role."
                else:
                    custom_message = (
                        "This role is temporarily protected from mention spam."
                    )
            else:
                custom_message = f"⚠️ **{len(self.active_blocks)} roles** are currently protected from mention spam. Please check back later."

            # Update just the message
            await rule.edit(
                actions=[
                    AutoModRuleAction(
                        type=discord.AutoModRuleActionType.block_message,
                        custom_message=custom_message,
                    )
                ]
            )

        except Exception as e:
            print(f"[AutoMod] Error updating block message: {e}")

    @tasks.loop(seconds=30)
    async def cleanup_task(self):
        """Check for expired role blocks and remove them"""
        await self.bot.wait_until_ready()
        now = datetime.now(self.ist)
        expired_blocks = []

        for role_id, block_data in list(self.active_blocks.items()):
            expires_at = block_data.get("expires_at")
            if expires_at and expires_at <= now:
                expired_blocks.append(role_id)

                # Log the expiration
                guild = self.bot.get_guild(int(block_data.get("guild_id", 0)))
                if guild:
                    await self.log_block_expired(guild, block_data)

        # Remove expired blocks
        for role_id in expired_blocks:
            del self.active_blocks[role_id]

        if expired_blocks:
            self.cog.data_manager.save_automod_rules(self.active_blocks)
            # Update the rule keywords since we removed some blocks
            await self.update_rule_keywords()

    def get_role_duration(self, role_id: str) -> int:
        """Get the duration in minutes for a protected role"""
        return self.PROTECTED_ROLES.get(str(role_id), self.DEFAULT_DURATION)

    def is_protected_role(self, role_id: str) -> bool:
        """Check if a role ID is in the protected list"""
        return str(role_id) in self.PROTECTED_ROLES

    async def log_block_created(self, guild, role: discord.Role, duration_minutes: int):
        """Log when a new role block is added"""
        log_channel: discord.TextChannel = self.cog.get_log_channel()
        if not log_channel:
            return

        expires_at = datetime.now(self.ist) + timedelta(minutes=duration_minutes)

        # Format duration nicely
        if duration_minutes >= 60:
            duration_display = f"{duration_minutes/60:.1f} hours"
        else:
            duration_display = f"{duration_minutes} minutes"

        embed = discord.Embed(
            title="🛡️ Role Protection Activated",
            description=f"Role mention protection has been added",
            color=discord.Color.red(),
            timestamp=datetime.now(pytz.UTC),
        )

        embed.add_field(
            name="Protected Role", value=f"{role.mention} (`{role.name}`)", inline=True
        )

        embed.add_field(name="Duration", value=duration_display, inline=True)

        embed.add_field(
            name="Expires", value=f"<t:{int(expires_at.timestamp())}:R>", inline=True
        )

        embed.add_field(
            name="Mention Limit",
            value=f"{self.MENTION_LIMIT} mentions per message",
            inline=False,
        )

        embed.add_field(
            name="Currently Protected Roles",
            value=f"{len(self.active_blocks)} role(s) protected",
            inline=True,
        )

        embed.set_footer(text="Protection will auto-remove when expired")

        await log_channel.send(embed=embed, delete_after=3600)

    async def log_block_expired(self, guild, block_data):
        """Log when a role block expires"""
        log_channel: discord.TextChannel = self.cog.get_log_channel()
        if not log_channel:
            return

        role_mention = (
            f"<@&{block_data['role_id']}>"
            if "role_id" in block_data
            else "Unknown Role"
        )
        duration_minutes = block_data.get("duration_minutes", 120)

        # Format duration nicely
        if duration_minutes >= 60:
            duration_display = f"{duration_minutes/60:.1f} hours"
        else:
            duration_display = f"{duration_minutes} minutes"

        embed = discord.Embed(
            title="✅ Role Protection Expired",
            description=f"Role mention protection has been removed",
            color=discord.Color.green(),
            timestamp=datetime.now(pytz.UTC),
        )

        embed.add_field(name="Role", value=role_mention, inline=True)

        embed.add_field(name="Duration", value=duration_display, inline=True)

        embed.add_field(
            name="Created",
            value=f"<t:{int(block_data['created_at'].timestamp())}:R>",
            inline=True,
        )

        embed.add_field(
            name="Remaining Protected Roles",
            value=f"{len(self.active_blocks) - 1} role(s) still protected",
            inline=True,
        )

        embed.set_footer(text="Normal role mentioning has been restored for this role")

        await log_channel.send(embed=embed, delete_after=3600)

    async def log_rule_update(self, guild, keywords):
        """Log when the main rule is updated"""
        log_channel = self.cog.get_log_channel()
        if not log_channel:
            return

        embed = discord.Embed(
            title="🔄 AutoMod Rule Updated",
            description=f"Protection list has been updated",
            color=discord.Color.blue(),
            timestamp=datetime.now(pytz.UTC),
        )

        embed.add_field(
            name="Protected Roles",
            value=f"{len(self.active_blocks)} role(s)",
            inline=True,
        )

        # List active roles
        if self.active_blocks:
            roles_list = ""
            for role_id, block_data in list(self.active_blocks.items())[:5]:
                expires = block_data["expires_at"]
                duration_minutes = block_data.get("duration_minutes", 120)
                remaining = self.get_remaining_time(expires)

                if duration_minutes >= 60:
                    duration_display = f"{duration_minutes/60:.1f}h"
                else:
                    duration_display = f"{duration_minutes}m"

                roles_list += (
                    f"• <@&{role_id}> - {duration_display} (remaining: {remaining})\n"
                )

            if len(self.active_blocks) > 5:
                roles_list += f"*...and {len(self.active_blocks) - 5} more*"

            embed.add_field(
                name="Currently Protected", value=roles_list or "None", inline=False
            )

        embed.set_footer(text="Single rule managing all protections")

        await log_channel.send(embed=embed)

    async def log_error(self, guild, error_message: str):
        """Log auto-mod errors"""
        log_channel = self.cog.get_log_channel()
        if not log_channel:
            return

        embed = discord.Embed(
            title="❌ Auto-Mod Error",
            description=error_message,
            color=discord.Color.red(),
            timestamp=datetime.now(pytz.UTC),
        )

        embed.add_field(name="Guild", value=guild.name, inline=True)

        await log_channel.send(embed=embed)

    async def check_and_block_role(self, message: discord.Message, role: discord.Role):
        """Check if a role mention should trigger adding it to the protection list"""
        # Only check protected roles
        if not self.is_protected_role(str(role.id)):
            return

        print(f"[AutoMod] Protected role mentioned: {role.name} ({role.id})")

        # Check if this role is already blocked
        if str(role.id) in self.active_blocks:
            print(f"[AutoMod] Role already in protection list")
            return

        # Get duration for this specific role
        duration_minutes = self.get_role_duration(str(role.id))

        # Add role to protection list
        block_data = {
            "guild_id": str(message.guild.id),
            "role_id": str(role.id),
            "role_name": role.name,
            "created_at": datetime.now(self.ist),
            "expires_at": datetime.now(self.ist) + timedelta(minutes=duration_minutes),
            "duration_minutes": duration_minutes,
            "trigger_message_id": str(message.id),
            "trigger_user_id": str(message.author.id),
        }

        self.active_blocks[str(role.id)] = block_data
        self.cog.data_manager.save_automod_rules(self.active_blocks)

        # Update the main rule with new keywords
        await self.update_rule_keywords()

        # Log the block
        await self.log_block_created(message.guild, role, duration_minutes)

        # Format duration for message
        if duration_minutes >= 60:
            duration_display = f"{duration_minutes/60:.1f} hours"
        else:
            duration_display = f"{duration_minutes} minutes"
