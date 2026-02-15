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
        # Load trigger keywords from env (comma-separated list)
        env_keywords = os.getenv('AUTOMOD_TRIGGER_KEYWORDS', 'revive,newbies,night')
        if env_keywords:
            self.TRIGGER_KEYWORDS = [k.strip().lower() for k in env_keywords.split(',') if k.strip()]
        else:
            self.TRIGGER_KEYWORDS = ["revive", "newbies", "night"]  # Default
        
        # Load protected role IDs from env (JSON array or comma-separated list)
        env_role_ids = os.getenv('AUTOMOD_PROTECTED_ROLE_IDS', '[]')
        try:
            # Try to parse as JSON first
            self.PROTECTED_ROLE_IDS = json.loads(env_role_ids)
            if not isinstance(self.PROTECTED_ROLE_IDS, list):
                self.PROTECTED_ROLE_IDS = []
        except json.JSONDecodeError:
            # Fall back to comma-separated list
            self.PROTECTED_ROLE_IDS = [r.strip() for r in env_role_ids.split(',') if r.strip()]
        
        # Load block duration from env (in hours)
        try:
            self.BLOCK_DURATION_HOURS = int(os.getenv('AUTOMOD_BLOCK_DURATION_HOURS', '2'))
        except ValueError:
            self.BLOCK_DURATION_HOURS = 2
        
        # Load check interval from env (in seconds)
        try:
            self.CHECK_INTERVAL_SECONDS = int(os.getenv('AUTOMOD_CHECK_INTERVAL_SECONDS', '30'))
        except ValueError:
            self.CHECK_INTERVAL_SECONDS = 30
        
        # Log configuration
        print(f"[AutoMod] Loaded config: {len(self.TRIGGER_KEYWORDS)} keywords, {len(self.PROTECTED_ROLE_IDS)} protected roles, {self.BLOCK_DURATION_HOURS}h duration")
    
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
    
    async def log_rule_creation(self, guild, role: discord.Role, rule: discord.AutoModRule, duration_hours: int):
        """Log when a new auto-mod rule is created"""
        log_channel = self.cog.get_log_channel()
        if not log_channel:
            return
        
        expires_at = datetime.now(self.ist) + timedelta(hours=duration_hours)
        
        embed = discord.Embed(
            title="🛡️ Auto-Mod Rule Created",
            description=f"Temporary role mention blocking has been activated",
            color=discord.Color.red(),
            timestamp=datetime.now(pytz.UTC)
        )
        
        embed.add_field(
            name="Triggered Role",
            value=f"{role.mention} (`{role.name}`) - {'🔒 Protected' if self.is_protected_role(str(role.id)) else '⚠️ Auto-detected'}",
            inline=True
        )
        
        embed.add_field(
            name="Duration",
            value=f"{duration_hours} hours",
            inline=True
        )
        
        embed.add_field(
            name="Expires",
            value=f"<t:{int(expires_at.timestamp())}:R>",
            inline=True
        )
        
        embed.add_field(
            name="Trigger Keywords",
            value=", ".join(f"`{k}`" for k in self.TRIGGER_KEYWORDS),
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
            value=f"{rule_data.get('duration_hours', 2)} hours",
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
        # Check if role is in protected list
        if self.is_protected_role(str(role.id)):
            # For protected roles, just check message content
            content_lower = message.content.lower()
            message_triggers = any(keyword in content_lower for keyword in self.TRIGGER_KEYWORDS)
            
            if not message_triggers:
                return  # Protected role but no trigger keywords = no block
        else:
            # For auto-detected roles, check both role name and message content
            role_name_lower = role.name.lower()
            role_triggers = any(keyword in role_name_lower for keyword in self.TRIGGER_KEYWORDS)
            
            if not role_triggers:
                return  # Role name doesn't contain trigger keywords
            
            content_lower = message.content.lower()
            message_triggers = any(keyword in content_lower for keyword in self.TRIGGER_KEYWORDS)
            
            if not message_triggers:
                return  # Message doesn't contain trigger keywords
        
        # If we get here, blocking conditions are met
        guild_id = str(message.guild.id)
        role_id = str(role.id)
        
        # Look for existing active rule
        for rule_id, rule_data in self.active_rules.items():
            if (rule_data.get("guild_id") == guild_id and 
                rule_data.get("role_id") == role_id):
                # Rule already exists, just log this mention
                return
        
        # Create new auto-mod rule
        await self.create_block_rule(message.guild, role, message)
    
    async def create_block_rule(self, guild: discord.Guild, role: discord.Role, trigger_message: discord.Message):
        """Create a temporary auto-mod rule to block mentions of this role"""
        try:
            # Check if bot has permissions
            if not guild.me.guild_permissions.manage_guild:
                await self.log_error(guild, "Missing 'Manage Guild' permission")
                return
            
            duration_hours = self.BLOCK_DURATION_HOURS
            
            # Create keyword list based on role type
            if self.is_protected_role(str(role.id)):
                # For protected roles, only block when trigger keywords appear
                keywords = self.TRIGGER_KEYWORDS.copy()
            else:
                # For auto-detected roles, block role name + trigger keywords
                keywords = [role.name.lower()] + self.TRIGGER_KEYWORDS
            
            # Create the auto-mod rule
            rule = await guild.create_auto_mod_rule(
                name=f"Temporary Block: {role.name} (Auto)",
                event_type=AutoModRuleEventType.message_send,
                trigger=AutoModTrigger(
                    type=AutoModRuleTriggerType.keyword,
                    keyword_filter=keywords
                ),
                actions=[
                    AutoModRuleAction(
                        type=discord.AutoModRuleActionType.block_message,
                        custom_message=f"This role ({role.name}) is temporarily blocked due to mention spam. Please wait {duration_hours} hours."
                    ),
                ],
                enabled=True,
                reason=f"Auto-block triggered by mention of {role.name} in #{trigger_message.channel.name}"
            )
            
            # Store the rule
            rule_data = {
                "guild_id": str(guild.id),
                "role_id": str(role.id),
                "role_name": role.name,
                "created_at": datetime.now(self.ist),
                "expires_at": datetime.now(self.ist) + timedelta(hours=duration_hours),
                "duration_hours": duration_hours,
                "trigger_message_id": str(trigger_message.id),
                "trigger_user_id": str(trigger_message.author.id),
                "is_protected": self.is_protected_role(str(role.id))
            }
            
            self.active_rules[str(rule.id)] = rule_data
            self.cog.data_manager.save_automod_rules(self.active_rules)
            
            # Log the creation
            await self.log_rule_creation(guild, role, rule, duration_hours)
            
            # Also log in the trigger channel
            try:
                role_type = "🔒 Protected" if self.is_protected_role(str(role.id)) else "⚠️ Auto-detected"
                await trigger_message.channel.send(
                    f"⚠️ **Auto-Mod Activated**\n"
                    f"{role_type} role {role.mention} mentions have been temporarily blocked for {duration_hours} hours due to potential spam.\n"
                    f"Staff has been notified."
                )
            except:
                pass
            
        except discord.Forbidden:
            await self.log_error(guild, "Missing permissions to create auto-mod rules")
        except Exception as e:
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
            protected_count = 0
            for rule_id, rule_data in self.active_rules.items():
                if rule_data.get("guild_id") == str(ctx.guild.id):
                    active_count += 1
                    if rule_data.get("is_protected"):
                        protected_count += 1
            
            embed.add_field(
                name="Current Status",
                value=f"Active Rules: {active_count} ({protected_count} protected)\n"
                      f"Trigger Keywords: {', '.join(f'`{k}`' for k in self.TRIGGER_KEYWORDS)}\n"
                      f"Block Duration: {self.BLOCK_DURATION_HOURS} hours",
                inline=False
            )
            
            embed.add_field(
                name="Protected Roles",
                value=f"{len(self.PROTECTED_ROLE_IDS)} roles configured in env",
                inline=False
            )
            
            embed.add_field(
                name="Commands",
                value=(
                    "**`.automod list`** - List active rules\n"
                    "**`.automod remove <rule_id>`** - Manually remove a rule\n"
                    "**`.automod keywords [add/remove] <keyword>`** - Manage trigger keywords\n"
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
            role_type = "🔒 Protected" if rule_data.get("is_protected") else "⚠️ Auto"
            
            embed.add_field(
                name=f"{role_type} - {rule_data.get('role_name', 'Unknown')}",
                value=(
                    f"**Role:** {role_mention}\n"
                    f"**Expires:** <t:{int(expires.timestamp())}:R>\n"
                    f"**Rule ID:** `{rule_id}`"
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
            old_keywords = self.TRIGGER_KEYWORDS.copy()
            old_protected = self.PROTECTED_ROLE_IDS.copy()
            old_duration = self.BLOCK_DURATION_HOURS
            
            self.load_config_from_env()
            
            embed = discord.Embed(
                title="🔄 Auto-Mod Config Reloaded",
                color=discord.Color.green()
            )
            
            embed.add_field(
                name="Keywords",
                value=f"Old: {', '.join(old_keywords)}\nNew: {', '.join(self.TRIGGER_KEYWORDS)}",
                inline=False
            )
            
            embed.add_field(
                name="Protected Roles",
                value=f"Old: {len(old_protected)}\nNew: {len(self.PROTECTED_ROLE_IDS)}",
                inline=True
            )
            
            embed.add_field(
                name="Block Duration",
                value=f"Old: {old_duration}h\nNew: {self.BLOCK_DURATION_HOURS}h",
                inline=True
            )
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            await ctx.send(f"❌ Error reloading config: {str(e)}")
    
    @automod_group.group(name="keywords")
    async def automod_keywords(self, ctx):
        """Manage trigger keywords"""
        if ctx.invoked_subcommand is None:
            await ctx.send(
                f"**Current Trigger Keywords:**\n"
                f"{', '.join(f'`{k}`' for k in self.TRIGGER_KEYWORDS)}\n\n"
                f"**Usage:**\n"
                f"`.automod keywords add <keyword>`\n"
                f"`.automod keywords remove <keyword>`"
            )
    
    @automod_keywords.command(name="add")
    async def keywords_add(self, ctx, *, keyword: str):
        """Add a trigger keyword"""
        keyword = keyword.lower().strip()
        if keyword in self.TRIGGER_KEYWORDS:
            await ctx.send(f"❌ `{keyword}` is already a trigger keyword!")
            return
        
        self.TRIGGER_KEYWORDS.append(keyword)
        await ctx.send(f"✅ Added `{keyword}` to trigger keywords!")
    
    @automod_keywords.command(name="remove")
    async def keywords_remove(self, ctx, *, keyword: str):
        """Remove a trigger keyword"""
        keyword = keyword.lower().strip()
        if keyword not in self.TRIGGER_KEYWORDS:
            await ctx.send(f"❌ `{keyword}` is not a trigger keyword!")
            return
        
        self.TRIGGER_KEYWORDS.remove(keyword)
        await ctx.send(f"✅ Removed `{keyword}` from trigger keywords!")