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
        
        # SINGLE RULE APPROACH - We'll maintain one rule that we update
        self.main_rule_id = None  # This will store our single rule's ID
        self.rule_guild_id = None  # The guild where our rule exists
        
        # Track active role blocks: role_id -> {expires_at, role_name}
        self.active_blocks = self.cog.data_manager.load_automod_rules()
        
        # Track keywords currently in the rule
        self.current_keywords = []
        
        # Start the main rule management task
        self.rule_manager_task.start()
        
        # Start cleanup task for expired blocks
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
        self.rule_manager_task.cancel()
        self.cleanup_task.cancel()
    
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
                    if hasattr(rule.trigger, 'keyword_filter'):
                        self.current_keywords = rule.trigger.keyword_filter
                    
                    print(f"[AutoMod] Found existing main rule: {self.main_rule_id}")
                    return
            
            # Create new rule if none exists
            rule = await guild.create_automod_rule(
                name="🛡️ Dynamic Role Protection",
                event_type=AutoModRuleEventType.message_send,
                trigger=AutoModTrigger(
                    type=AutoModRuleTriggerType.keyword,
                    keyword_filter=["dummy_keyword_that_will_be_replaced"]  # Temporary placeholder
                ),
                actions=[
                    AutoModRuleAction(
                        type=discord.AutoModRuleActionType.block_message,
                        custom_message=f"This role is temporarily protected from mention spam."
                    )
                ],
                enabled=True,
                reason="Dynamic role protection system initialized"
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
                            keyword_filter=new_keywords
                        )
                    )
                    
                    self.current_keywords = new_keywords
                    print(f"[AutoMod] Updated rule keywords: {len(new_keywords)} total")
                    
                    # Log the update if there are active blocks
                    if self.active_blocks:
                        await self.log_rule_update(guild, new_keywords)
                        
            except Exception as e:
                print(f"[AutoMod] Error updating rule keywords: {e}")
    
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
    
    def is_protected_role(self, role_id: str) -> bool:
        """Check if a role ID is in the protected list"""
        return str(role_id) in self.PROTECTED_ROLE_IDS
    
    async def log_block_created(self, guild, role: discord.Role, duration_minutes: int):
        """Log when a new role block is added"""
        log_channel: discord.TextChannel = self.cog.get_log_channel()
        if not log_channel:
            return
        
        expires_at = datetime.now(self.ist) + timedelta(minutes=duration_minutes)
        
        embed = discord.Embed(
            title="🛡️ Role Protection Activated",
            description=f"Role mention protection has been added",
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
            name="Currently Protected Roles",
            value=f"{len(self.active_blocks)} role(s) protected",
            inline=True
        )
        
        embed.set_footer(text="Protection will auto-remove when expired")
        
        await log_channel.send(embed=embed, delete_after=3600)
    
    async def log_block_expired(self, guild, block_data):
        """Log when a role block expires"""
        log_channel:discord.TextChannel = self.cog.get_log_channel()
        if not log_channel:
            return
        
        role_mention = f"<@&{block_data['role_id']}>" if 'role_id' in block_data else "Unknown Role"
        
        embed = discord.Embed(
            title="✅ Role Protection Expired",
            description=f"Role mention protection has been removed",
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
            value=f"{block_data.get('duration_minutes', 120)} minutes",
            inline=True
        )
        
        embed.add_field(
            name="Created",
            value=f"<t:{int(block_data['created_at'].timestamp())}:R>",
            inline=True
        )
        
        embed.add_field(
            name="Remaining Protected Roles",
            value=f"{len(self.active_blocks) - 1} role(s) still protected",
            inline=True
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
            timestamp=datetime.now(pytz.UTC)
        )
        
        embed.add_field(
            name="Protected Roles",
            value=f"{len(self.active_blocks)} role(s)",
            inline=True
        )
        
        # List active roles
        if self.active_blocks:
            roles_list = ""
            for role_id, block_data in list(self.active_blocks.items())[:5]:
                expires = block_data['expires_at']
                roles_list += f"• <@&{role_id}> - expires <t:{int(expires.timestamp())}:R>\n"
            
            if len(self.active_blocks) > 5:
                roles_list += f"*...and {len(self.active_blocks) - 5} more*"
            
            embed.add_field(
                name="Currently Protected",
                value=roles_list or "None",
                inline=False
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
            timestamp=datetime.now(pytz.UTC)
        )
        
        embed.add_field(
            name="Guild",
            value=guild.name,
            inline=True
        )
        
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
        
        # Check if message contains trigger keywords (optional - remove if you want to block on ANY mention)
        content_lower = message.content.lower()
        has_trigger = any(keyword in content_lower for keyword in ["spam", "mass", "everyone", "here"])
        
        # For now, let's block on any mention of protected roles (remove the has_trigger check if you want)
        # if not has_trigger:
        #     return
        
        # Add role to protection list
        duration_minutes = self.BLOCK_DURATION_MINUTES
        
        block_data = {
            "guild_id": str(message.guild.id),
            "role_id": str(role.id),
            "role_name": role.name,
            "created_at": datetime.now(self.ist),
            "expires_at": datetime.now(self.ist) + timedelta(minutes=duration_minutes),
            "duration_minutes": duration_minutes,
            "trigger_message_id": str(message.id),
            "trigger_user_id": str(message.author.id)
        }
        
        self.active_blocks[str(role.id)] = block_data
        self.cog.data_manager.save_automod_rules(self.active_blocks)
        
        # Update the main rule with new keywords
        await self.update_rule_keywords()
        
        # Log the block
        await self.log_block_created(message.guild, role, duration_minutes)
        
        # Also notify in the trigger channel
        try:
            await message.channel.send(
                f"⚠️ **Role Protection Activated**\n"
                f"Role {role.mention} is now protected from mention spam for {duration_minutes} minutes.\n"
                f"Anyone mentioning this role will have their message blocked."
            )
        except:
            pass
    
    @commands.group(name="automod", aliases=["am"])
    @commands.has_permissions(administrator=True)
    async def automod_group(self, ctx):
        """Manage auto-mod blocking settings"""
        if ctx.invoked_subcommand is None:
            embed = discord.Embed(
                title="🛡️ Auto-Mod Manager",
                description="Dynamic role protection system",
                color=discord.Color.blue()
            )
            
            embed.add_field(
                name="Current Status",
                value=f"Protected Roles: {len(self.PROTECTED_ROLE_IDS)}\n"
                      f"Currently Blocked: {len(self.active_blocks)}\n"
                      f"Mention Limit: {self.MENTION_LIMIT} per message\n"
                      f"Block Duration: {self.BLOCK_DURATION_MINUTES} minutes",
                inline=False
            )
            
            # Show currently blocked roles
            if self.active_blocks:
                blocks_list = ""
                for role_id, block_data in list(self.active_blocks.items())[:5]:
                    expires = block_data['expires_at']
                    blocks_list += f"• <@&{role_id}> - expires <t:{int(expires.timestamp())}:R>\n"
                
                if len(self.active_blocks) > 5:
                    blocks_list += f"*...and {len(self.active_blocks) - 5} more*"
                
                embed.add_field(
                    name="Currently Blocked Roles",
                    value=blocks_list,
                    inline=False
                )
            
            embed.add_field(
                name="Commands",
                value=(
                    "**`.automod list`** - List blocked roles\n"
                    "**`.automod remove <role_id>`** - Manually remove a role from protection\n"
                    "**`.automod add <role_id>`** - Manually add a role to protection\n"
                    "**`.automod reload`** - Reload config from environment"
                ),
                inline=False
            )
            
            await ctx.send(embed=embed)
    
    @automod_group.command(name="list")
    async def automod_list(self, ctx):
        """List all currently blocked roles"""
        if not self.active_blocks:
            await ctx.send("No roles are currently blocked.")
            return
        
        embed = discord.Embed(
            title=f"Currently Blocked Roles ({len(self.active_blocks)})",
            color=discord.Color.orange()
        )
        
        for role_id, block_data in self.active_blocks.items():
            role_mention = f"<@&{role_id}>"
            expires = block_data['expires_at']
            
            embed.add_field(
                name=f"Role: {block_data.get('role_name', 'Unknown')}",
                value=(
                    f"**Role:** {role_mention}\n"
                    f"**Expires:** <t:{int(expires.timestamp())}:R>\n"
                    f"**Added:** <t:{int(block_data['created_at'].timestamp())}:R>"
                ),
                inline=False
            )
        
        await ctx.send(embed=embed)
    
    @automod_group.command(name="add")
    async def automod_add(self, ctx, role: discord.Role):
        """Manually add a role to protection"""
        if str(role.id) in self.active_blocks:
            await ctx.send(f"❌ Role {role.mention} is already blocked!")
            return
        
        duration_minutes = self.BLOCK_DURATION_MINUTES
        
        block_data = {
            "guild_id": str(ctx.guild.id),
            "role_id": str(role.id),
            "role_name": role.name,
            "created_at": datetime.now(self.ist),
            "expires_at": datetime.now(self.ist) + timedelta(minutes=duration_minutes),
            "duration_minutes": duration_minutes,
            "trigger_message_id": None,
            "trigger_user_id": str(ctx.author.id),
            "manually_added": True
        }
        
        self.active_blocks[str(role.id)] = block_data
        self.cog.data_manager.save_automod_rules(self.active_blocks)
        
        # Update the main rule
        await self.update_rule_keywords()
        
        # Log the manual addition
        await self.log_block_created(ctx.guild, role, duration_minutes)
        
        await ctx.send(f"✅ Role {role.mention} has been added to protection for {duration_minutes} minutes!")
    
    @automod_group.command(name="remove")
    async def automod_remove(self, ctx, role: discord.Role):
        """Manually remove a role from protection"""
        if str(role.id) not in self.active_blocks:
            await ctx.send(f"❌ Role {role.mention} is not currently blocked!")
            return
        
        block_data = self.active_blocks[str(role.id)]
        del self.active_blocks[str(role.id)]
        self.cog.data_manager.save_automod_rules(self.active_blocks)
        
        # Update the main rule
        await self.update_rule_keywords()
        
        # Log the manual removal
        log_channel = self.cog.get_log_channel()
        if log_channel:
            embed = discord.Embed(
                title="🛠️ Role Protection Manually Removed",
                description=f"Role was removed by {ctx.author.mention}",
                color=discord.Color.purple()
            )
            embed.add_field(
                name="Role",
                value=role.mention,
                inline=True
            )
            embed.add_field(
                name="Originally Added",
                value=f"<t:{int(block_data['created_at'].timestamp())}:R>",
                inline=True
            )
            await log_channel.send(embed=embed)
        
        await ctx.send(f"✅ Role {role.mention} has been removed from protection!")
    
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