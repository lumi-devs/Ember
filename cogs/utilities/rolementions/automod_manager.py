from discord.ext import tasks
import discord
from discord import AutoModTrigger, AutoModRuleAction
from discord.enums import AutoModRuleEventType, AutoModRuleTriggerType
from datetime import datetime, timedelta
import pytz
from discord.ext import commands
import os
import json

class AutoModManager:
    def __init__(self, cog):
        self.cog = cog
        self.bot = cog.bot
        self.ist = pytz.timezone('Asia/Kolkata')
        
        # Load configuration from environment variables
        self.load_config_from_env()
        
        # Track active rules: rule_id -> {guild_id, role_ids, expires_at}
        self.active_rules = self.cog.data_manager.load_automod_rules()
        
        # Start cleanup task
        self.cleanup_task.start()
    
    def load_config_from_env(self):
        """Load configuration from environment variables"""
        # Load protected role IDs from env (comma-separated list)
        env_role_ids = os.getenv('AUTOMOD_PROTECTED_ROLE_IDS', '')
        if env_role_ids:
            self.PROTECTED_ROLE_IDS = [r.strip() for r in env_role_ids.split(',') if r.strip()]
        else:
            self.PROTECTED_ROLE_IDS = []
        
        # Load mention limit from env
        try:
            self.MENTION_LIMIT = int(os.getenv('AUTOMOD_MENTION_LIMIT', '3'))
        except ValueError:
            self.MENTION_LIMIT = 3
        
        # Load block duration from env (in minutes)
        try:
            self.BLOCK_DURATION_MINUTES = int(os.getenv('AUTOMOD_BLOCK_DURATION_MINUTES', '120'))
        except ValueError:
            self.BLOCK_DURATION_MINUTES = 120
        
        # Log configuration
        print(f"[AutoMod] Loaded config: {len(self.PROTECTED_ROLE_IDS)} protected roles, limit: {self.MENTION_LIMIT}, duration: {self.BLOCK_DURATION_MINUTES}m")
    
    def cog_unload(self):
        """Cleanup when cog is unloaded"""
        self.cleanup_task.cancel()
    
    @tasks.loop(seconds=30)
    async def cleanup_task(self):
        """Check for expired auto-mod rules and remove them"""
        await self.bot.wait_until_ready()
        now = datetime.now(self.ist)
        expired_rules = []
        
        for rule_id, rule_data in list(self.active_rules.items()):
            expires_at = rule_data.get("expires_at")
            if expires_at and expires_at <= now:
                try:
                    guild = self.bot.get_guild(int(rule_data["guild_id"]))
                    if guild:
                        # Delete the auto-mod rule
                        try:
                            rule = await guild.fetch_auto_mod_rule(int(rule_id))
                            await rule.delete()
                            
                            # Log the removal
                            await self.log_rule_removal(guild, rule_data)
                        except:
                            pass
                    
                    expired_rules.append(rule_id)
                except Exception as e:
                    print(f"[AutoMod] Error removing rule {rule_id}: {e}")
                    expired_rules.append(rule_id)
        
        # Remove expired rules from tracking
        for rule_id in expired_rules:
            del self.active_rules[rule_id]
        
        if expired_rules:
            self.cog.data_manager.save_automod_rules(self.active_rules)
    
    def is_protected_role(self, role_id: str) -> bool:
        """Check if a role ID is in the protected list"""
        return str(role_id) in self.PROTECTED_ROLE_IDS
    
    async def log_rule_creation(self, guild, role: discord.Role, rule: discord.AutoModRule, duration_minutes: int):
        """Log when a new auto-mod rule is created"""
        log_channel = self.cog.get_log_channel()
        if not log_channel:
            return
        
        expires_at = datetime.now(self.ist) + timedelta(minutes=duration_minutes)
        
        embed = discord.Embed(
            title="🛡️ Auto-Mod Rule Created",
            description=f"Temporary role mention blocking has been activated",
            color=discord.Color.red(),
            timestamp=datetime.now(pytz.UTC)
        )
        
        embed.add_field(
            name="Protected Role",
            value=f"{role.mention} (`{role.name}`)",
            inline=True
        )
        
        embed.add_field(
            name="Duration",
            value=f"{duration_minutes} minutes",
            inline=True
        )
        
        embed.add_field(
            name="Expires",
            value=f"<t:{int(expires_at.timestamp())}:R>",
            inline=True
        )
        
        embed.add_field(
            name="Mention Limit",
            value=f"{self.MENTION_LIMIT} mentions per message",
            inline=False
        )
        
        embed.add_field(
            name="Rule Details",
            value=(
                f"**Rule Name:** {rule.name}\n"
                f"**Rule ID:** `{rule.id}`\n"
                f"**Action:** Block message"
            ),
            inline=False
        )
        
        embed.set_footer(text="Rule will auto-delete when expired")
        
        await log_channel.send(embed=embed)
    
    async def log_rule_removal(self, guild, rule_data):
        """Log when an auto-mod rule expires and is removed"""
        log_channel = self.cog.get_log_channel()
        if not log_channel:
            return
        
        # Get role name from stored data
        role_mention = f"<@&{rule_data['role_id']}>" if 'role_id' in rule_data else "Unknown Role"
        
        embed = discord.Embed(
            title="✅ Auto-Mod Rule Expired",
            description=f"Temporary role mention blocking has been removed",
            color=discord.Color.green(),
            timestamp=datetime.now(pytz.UTC)
        )
        
        embed.add_field(
            name="Role",
            value=role_mention,
            inline=True
        )
        
        embed.add_field(
            name="Duration",
            value=f"{rule_data.get('duration_minutes', 120)} minutes",
            inline=True
        )
        
        embed.add_field(
            name="Created",
            value=f"<t:{int(rule_data['created_at'].timestamp())}:R>",
            inline=True
        )
        
        embed.set_footer(text="Normal role mentioning has been restored")
        
        await log_channel.send(embed=embed)
    
    async def check_and_block_role(self, message: discord.Message, role: discord.Role):
        """Check if a role mention should trigger auto-mod blocking"""
        # Only check protected roles
        if not self.is_protected_role(str(role.id)):
            return
        
        print(f"[AutoMod] Protected role mentioned: {role.name} ({role.id})")
        
        # Look for existing active rule for this role
        guild_id = str(message.guild.id)
        role_id = str(role.id)
        
        for rule_id, rule_data in self.active_rules.items():
            if (rule_data.get("guild_id") == guild_id and 
                rule_data.get("role_id") == role_id):
                print(f"[AutoMod] Rule already exists for this role")
                return
        
        # Create new mention spam rule
        await self.create_mention_spam_rule(message.guild, role, message)
    
    async def create_mention_spam_rule(self, guild: discord.Guild, role: discord.Role, trigger_message: discord.Message):
        """Create a temporary rule to block excessive mentions of this role"""
        try:
            # Check if bot has permissions
            print(f"[AutoMod] Bot permissions in {guild.name}:")
            print(f"  - manage_guild: {guild.me.guild_permissions.manage_guild}")
            
            if not guild.me.guild_permissions.manage_guild:
                await self.log_error(guild, "Missing 'Manage Guild' permission")
                return
            
            duration_minutes = self.BLOCK_DURATION_MINUTES
            
            # Create the auto-mod rule - THIS BLOCKS BASED ON MENTION COUNT
            rule = await guild.create_auto_mod_rule(
                name=f"Mention Protection: {role.name}",
                event_type=AutoModRuleEventType.message_send,
                trigger=AutoModTrigger(
                    type=AutoModRuleTriggerType.mention_spam,
                    mention_total_limit=self.MENTION_LIMIT,
                    mention_raid_protection_enabled=True  # This enables mention spam protection
                ),
                actions=[
                    AutoModRuleAction(
                        type=discord.AutoModRuleActionType.block_message,
                        custom_message=f"Please don't spam mention {role.name}. This role is temporarily protected."
                    ),
                    # Optional: Timeout the user
                    # AutoModRuleAction(
                    #     type=discord.AutoModRuleActionType.timeout,
                    #     duration=timedelta(minutes=10)
                    # )
                ],
                enabled=True,
                reason=f"Mention spam protection triggered by {trigger_message.author} in #{trigger_message.channel.name}"
            )
            
            print(f"[AutoMod] Rule created successfully! Rule ID: {rule.id}")
            
            # Store the rule
            rule_data = {
                "guild_id": str(guild.id),
                "role_id": str(role.id),
                "role_name": role.name,
                "created_at": datetime.now(self.ist),
                "expires_at": datetime.now(self.ist) + timedelta(minutes=duration_minutes),
                "duration_minutes": duration_minutes,
                "trigger_message_id": str(trigger_message.id),
                "trigger_user_id": str(trigger_message.author.id),
                "mention_limit": self.MENTION_LIMIT
            }
            
            self.active_rules[str(rule.id)] = rule_data
            self.cog.data_manager.save_automod_rules(self.active_rules)
            
            # Log the creation
            await self.log_rule_creation(guild, role, rule, duration_minutes)
            
            # Also log in the trigger channel
            try:
                await trigger_message.channel.send(
                    f"⚠️ **Mention Protection Activated**\n"
                    f"Role {role.mention} now has mention spam protection for {duration_minutes} minutes.\n"
                    f"Users will be blocked if they mention this role more than {self.MENTION_LIMIT} times in a single message."
                )
            except:
                pass
            
        except discord.Forbidden:
            print(f"[AutoMod] Forbidden error: Missing permissions")
            await self.log_error(guild, "Missing permissions to create auto-mod rules")
        except Exception as e:
            print(f"[AutoMod] Error creating rule: {str(e)}")
            await self.log_error(guild, f"Error creating auto-mod rule: {str(e)}")
    
    async def log_error(self, guild, error_message: str):
        """Log auto-mod errors"""
        log_channel = self.cog.get_log_channel()
        if not log_channel:
            return
        
        embed = discord.Embed(
            title="❌ Auto-Mod Error",
            description=error_message,
            color=discord.Color.red(),
            timestamp=datetime.now(pytz.UTC)
        )
        
        embed.add_field(
            name="Guild",
            value=guild.name,
            inline=True
        )
        
        await log_channel.send(embed=embed)
    
    @commands.group(name="automod", aliases=["am"])
    @commands.has_permissions(administrator=True)
    async def automod_group(self, ctx):
        """Manage auto-mod blocking settings"""
        if ctx.invoked_subcommand is None:
            embed = discord.Embed(
                title="🛡️ Auto-Mod Manager",
                description="Manage temporary role mention blocking",
                color=discord.Color.blue()
            )
            
            # Show active rules
            active_count = 0
            for rule_id, rule_data in self.active_rules.items():
                if rule_data.get("guild_id") == str(ctx.guild.id):
                    active_count += 1
            
            embed.add_field(
                name="Current Status",
                value=f"Active Rules: {active_count}\n"
                      f"Protected Roles: {len(self.PROTECTED_ROLE_IDS)}\n"
                      f"Mention Limit: {self.MENTION_LIMIT} per message\n"
                      f"Block Duration: {self.BLOCK_DURATION_MINUTES} minutes",
                inline=False
            )
            
            embed.add_field(
                name="Commands",
                value=(
                    "**`.automod list`** - List active rules\n"
                    "**`.automod remove <rule_id>`** - Manually remove a rule\n"
                    "**`.automod reload`** - Reload config from environment"
                ),
                inline=False
            )
            
            await ctx.send(embed=embed)
    
    @automod_group.command(name="list")
    async def automod_list(self, ctx):
        """List all active auto-mod rules in this server"""
        guild_id = str(ctx.guild.id)
        active_rules = []
        
        for rule_id, rule_data in self.active_rules.items():
            if rule_data.get("guild_id") == guild_id:
                active_rules.append((rule_id, rule_data))
        
        if not active_rules:
            await ctx.send("No active auto-mod rules in this server.")
            return
        
        embed = discord.Embed(
            title=f"Active Auto-Mod Rules ({len(active_rules)})",
            color=discord.Color.orange()
        )
        
        for rule_id, rule_data in active_rules:
            role_mention = f"<@&{rule_data['role_id']}>"
            expires = rule_data['expires_at']
            
            embed.add_field(
                name=f"Rule: {rule_data.get('role_name', 'Unknown')}",
                value=(
                    f"**Role:** {role_mention}\n"
                    f"**Expires:** <t:{int(expires.timestamp())}:R>\n"
                    f"**Rule ID:** `{rule_id}`\n"
                    f"**Limit:** {rule_data.get('mention_limit', 3)} mentions/message"
                ),
                inline=False
            )
        
        await ctx.send(embed=embed)
    
    @automod_group.command(name="remove")
    async def automod_remove(self, ctx, rule_id: str):
        """Manually remove an auto-mod rule"""
        if rule_id not in self.active_rules:
            await ctx.send(f"❌ Rule `{rule_id}` not found!")
            return
        
        rule_data = self.active_rules[rule_id]
        
        try:
            guild = self.bot.get_guild(int(rule_data["guild_id"]))
            if guild:
                rule = await guild.fetch_auto_mod_rule(int(rule_id))
                await rule.delete()
            
            del self.active_rules[rule_id]
            self.cog.data_manager.save_automod_rules(self.active_rules)
            
            await ctx.send(f"✅ Rule `{rule_id}` has been removed!")
            
            # Log the manual removal
            log_channel = self.cog.get_log_channel()
            if log_channel:
                embed = discord.Embed(
                    title="🛠️ Auto-Mod Rule Manually Removed",
                    description=f"Rule was removed by {ctx.author.mention}",
                    color=discord.Color.purple()
                )
                await log_channel.send(embed=embed)
                
        except Exception as e:
            await ctx.send(f"❌ Error removing rule: {str(e)}")
    
    @automod_group.command(name="reload")
    @commands.has_permissions(administrator=True)
    async def automod_reload(self, ctx):
        """Reload configuration from environment variables"""
        try:
            old_protected = self.PROTECTED_ROLE_IDS.copy()
            old_limit = self.MENTION_LIMIT
            old_duration = self.BLOCK_DURATION_MINUTES
            
            self.load_config_from_env()
            
            embed = discord.Embed(
                title="🔄 Auto-Mod Config Reloaded",
                color=discord.Color.green()
            )
            
            embed.add_field(
                name="Protected Roles",
                value=f"Old: {len(old_protected)}\nNew: {len(self.PROTECTED_ROLE_IDS)}",
                inline=True
            )
            
            embed.add_field(
                name="Mention Limit",
                value=f"Old: {old_limit}\nNew: {self.MENTION_LIMIT}",
                inline=True
            )
            
            embed.add_field(
                name="Block Duration",
                value=f"Old: {old_duration}m\nNew: {self.BLOCK_DURATION_MINUTES}m",
                inline=True
            )
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            await ctx.send(f"❌ Error reloading config: {str(e)}")