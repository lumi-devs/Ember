import os
import discord
from discord.ext import tasks, commands
import sqlite3
from datetime import datetime, timedelta
import asyncio


class PromoterRole(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_path = "promoter_role.db"

        # HARDCODED SETTINGS
        self.promoter_role_id = 1440636027923333182
        self.your_server_invites = [
            ".gg/aakhristation",
            "discord.gg/aakhristation",
            "aakhristation",
        ]
        self.target_primary_guild_id = int(os.environ.get("GUILD_ID", 0))
        self.log_channel_id = 1443119503285293098

        self.init_db()
        self.status_cache = {}  # Cache to track status changes
        
        # Timer for periodic full rechecks
        self.full_recheck_interval = 14400  # Recheck every hour (in seconds)
        self.last_full_recheck = datetime.now()

    def init_db(self):
        """Initialize the database tables"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS promoter_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER,
                    member_id INTEGER,
                    member_name TEXT,
                    action TEXT,
                    reason TEXT,
                    content TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            conn.commit()

    @commands.Cog.listener()
    async def on_guild_update(self, before, after):
        """Detect when the target guild properties change"""
        # Only care about our target guild
        if after.id != self.target_primary_guild_id:
            return
            
        # Check if the tag changed
        if before.tag != after.tag:
            print(f"🏷️ Target server tag changed from '{before.tag}' to '{after.tag}'")
            print(f"🔄 Rechecking all members with this server as primary guild...")
            
            # Find all guilds where we need to check members
            for guild in self.bot.guilds:
                role = guild.get_role(self.promoter_role_id)
                if not role:
                    continue
                    
                # Check all members in this guild
                for member in guild.members:
                    if member.bot:
                        continue
                        
                    # Only recheck members who have this server as their primary guild
                    if self.has_target_primary_guild(member):
                        await self.instant_member_check(
                            member, 
                            f"Target server tag changed from '{before.tag}' to '{after.tag}'"
                        )

    @commands.Cog.listener()
    async def on_ready(self):
        print("🚀 PromoterRole cog loaded!")
        print(f"📊 Monitoring: Status & Primary Guild ID '{self.target_primary_guild_id}'")
        print(f"⏰ Full recheck timer: Every {self.full_recheck_interval} seconds")
        self.fallback_check.start()
        self.full_recheck_timer.start()

    @commands.Cog.listener()
    async def on_presence_update(self, before, after):
        """Status change detection"""
        if after.bot:
            return

        # Get cache key
        cache_key = f"{after.guild.id}-{after.id}"

        # Get current status content
        current_status = self.get_status_content(after)
        previous_status = self.status_cache.get(cache_key)

        # Update cache
        self.status_cache[cache_key] = current_status

        # Check if status content actually changed
        if current_status != previous_status:
            await self.instant_member_check(after, "Status changed")

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        """Member update detection"""
        if after.bot:
            return

        changes = []

        # Check primary guild ID changes
        before_has_primary = self.has_target_primary_guild(before)
        after_has_primary = self.has_target_primary_guild(after)

        if before_has_primary != after_has_primary:
            changes.append(f"Primary guild {'added' if after_has_primary else 'removed'}")

        # Check global name changes
        if before.global_name != after.global_name:
            changes.append("Global name changed")

        # If any changes detected, run instant check
        if changes:
            await self.instant_member_check(
                after, f"Member update: {', '.join(changes)}"
            )

    def get_status_content(self, member):
        """Get status text for comparison"""
        try:
            if member.status == discord.Status.offline:
                return "offline"

            for activity in member.activities:
                if isinstance(activity, discord.CustomActivity) and activity.name:
                    return activity.name.lower()

            return ""
        except Exception:
            return ""

    def has_target_primary_guild(self, member):
        """Check if member's primary guild is our target server"""
        try:
            if not hasattr(member, "primary_guild") or member.primary_guild is None:
                return False

            primary_guild = member.primary_guild
            return primary_guild.id == self.target_primary_guild_id
        except Exception:
            return False

    async def has_promotion_content(self, member):
        """Check status and primary guild for promotion content"""

        # 1. Check Primary Guild ID
        if self.has_target_primary_guild(member):
            return True

        # 2. Check Custom Status
        if member.status != discord.Status.offline:
            status_content = self.get_status_content(member)
            if status_content and status_content != "offline":
                status_lower = status_content.lower()
                for invite in self.your_server_invites:
                    if invite.lower() in status_lower:
                        return True

        return False

    async def get_promotion_content(self, member):
        """Get promotion source for logging"""
        sources = []

        # Check primary guild ID
        if self.has_target_primary_guild(member):
            primary_guild = member.primary_guild
            if primary_guild:
                # PrimaryGuild only has id and tag attributes, no name
                if primary_guild.tag:
                    sources.append(f"Primary Guild (ID: {primary_guild.id}, Tag: {primary_guild.tag})")
                else:
                    sources.append(f"Primary Guild (ID: {primary_guild.id})")
            else:
                sources.append(f"Primary Guild ID: {self.target_primary_guild_id}")

        # Check status
        if member.status != discord.Status.offline:
            status_content = self.get_status_content(member)
            if status_content and status_content != "offline":
                sources.append(f"Status: {status_content}")

        return " | ".join(sources) if sources else "No promotion detected"

    def log_promotion_action(
        self, guild_id, member_id, member_name, action, reason, content=""
    ):
        """Database logging"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO promoter_logs (guild_id, member_id, member_name, action, reason, content)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (guild_id, member_id, member_name, action, reason, content),
            )
            conn.commit()

    async def instant_member_check(self, member, reason):
        """Instant check for promotion content"""
        try:
            role = member.guild.get_role(self.promoter_role_id)
            if not role:
                return

            current_has_promotion = await self.has_promotion_content(member)
            currently_has_role = role in member.roles

            if current_has_promotion and not currently_has_role:
                # Give promoter role
                promotion_content = await self.get_promotion_content(member)
                await member.add_roles(role, reason=f"Promotion detected: {reason}")
                self.log_promotion_action(
                    member.guild.id,
                    member.id,
                    str(member),
                    "ROLE_ASSIGNED",
                    reason,
                    promotion_content,
                )
                await self.send_promotion_log(member, "assigned", promotion_content)

            elif not current_has_promotion and currently_has_role:
                # Remove promoter role
                promotion_content = await self.get_promotion_content(member)
                await member.remove_roles(role, reason=f"Promotion removed: {reason}")
                self.log_promotion_action(
                    member.guild.id,
                    member.id,
                    str(member),
                    "ROLE_REMOVED",
                    reason,
                    promotion_content,
                )
                await self.send_promotion_log(member, "removed", promotion_content)

        except discord.Forbidden:
            print(f"❌ No permission to manage roles for {member.display_name}")
        except Exception as e:
            print(f"❌ Instant check error with {member.display_name}: {e}")

    @tasks.loop(seconds=30)
    async def fallback_check(self):
        """30-second fallback check - only checks mismatches"""
        for guild in self.bot.guilds:
            role = guild.get_role(self.promoter_role_id)
            if not role:
                continue

            for member in guild.members:
                if not member.bot:
                    has_target = self.has_target_primary_guild(member)
                    has_role = role in member.roles

                    if has_target != has_role:
                        full_promotion = await self.has_promotion_content(member)
                        if full_promotion != has_role:
                            await self.instant_member_check(member, "30s fallback")

    @tasks.loop(seconds=3600)  # Default to hourly, can be changed
    async def full_recheck_timer(self):
        """Periodic full recheck of ALL members (not just mismatches)"""
        print(f"🔄 Starting scheduled full recheck of all members...")
        await self.full_recheck_all_members(reason="Scheduled full recheck")
        self.last_full_recheck = datetime.now()

    async def full_recheck_all_members(self, reason="Full recheck"):
        """Comprehensive recheck of all members in all guilds"""
        total_checked = 0
        total_updated = 0
        
        for guild in self.bot.guilds:
            role = guild.get_role(self.promoter_role_id)
            if not role:
                continue
            
            print(f"📋 Rechecking guild: {guild.name} ({len(guild.members)} members)")
            
            for member in guild.members:
                if member.bot:
                    continue
                
                total_checked += 1
                
                # Clear cache for this member to force fresh check
                cache_key = f"{guild.id}-{member.id}"
                if cache_key in self.status_cache:
                    del self.status_cache[cache_key]
                
                # Check current state
                current_has_promotion = await self.has_promotion_content(member)
                currently_has_role = role in member.roles
                
                if current_has_promotion and not currently_has_role:
                    await member.add_roles(role, reason=reason)
                    promotion_content = await self.get_promotion_content(member)
                    self.log_promotion_action(
                        guild.id, member.id, str(member), 
                        "ROLE_ASSIGNED", reason, promotion_content
                    )
                    await self.send_promotion_log(member, "assigned", promotion_content)
                    total_updated += 1
                    
                elif not current_has_promotion and currently_has_role:
                    await member.remove_roles(role, reason=reason)
                    promotion_content = await self.get_promotion_content(member)
                    self.log_promotion_action(
                        guild.id, member.id, str(member), 
                        "ROLE_REMOVED", reason, promotion_content
                    )
                    await self.send_promotion_log(member, "removed", promotion_content)
                    total_updated += 1
                
                # Small delay to avoid rate limits
                if total_checked % 10 == 0:
                    await asyncio.sleep(0.5)
        
        print(f"✅ Full recheck complete: {total_checked} members checked, {total_updated} updated")
        
        # Log to channel if available
        try:
            # Find first guild with log channel
            for guild in self.bot.guilds:
                channel = guild.get_channel(self.log_channel_id)
                if channel:
                    embed = discord.Embed(
                        title="🔄 Full Recheck Complete",
                        description=f"**Members Checked:** {total_checked}\n**Members Updated:** {total_updated}\n**Reason:** {reason}",
                        color=0x7289DA,
                        timestamp=datetime.now()
                    )
                    await channel.send(embed=embed)
                    break
        except Exception:
            pass

    async def send_promotion_log(self, member, action_type, content=""):
        """Send log to designated channel"""
        try:
            channel = member.guild.get_channel(self.log_channel_id)
            if not channel:
                return

            if action_type == "assigned":
                embed = discord.Embed(
                    title="⚡ New Promoter!",
                    description=f"**{member.mention}** is promoting our server!",
                    color=0x00FF00,
                    timestamp=datetime.now(),
                )
            else:
                embed = discord.Embed(
                    title="⚡ Promoter Removed",
                    description=f"**{member.mention}** stopped promoting",
                    color=0xFF9900,
                    timestamp=datetime.now(),
                )

            if content and content != "No promotion detected":
                embed.add_field(
                    name="Promotion Source", value=f"```{content}```", inline=False
                )
            embed.set_thumbnail(url=member.display_avatar.url)

            await channel.send(embed=embed)

        except Exception:
            pass

    # COMMANDS
    @commands.command(name="pgcheck")
    async def pgcheck(self, ctx, member: discord.Member = None):
        """Check a member's primary guild"""
        member = member or ctx.author

        try:
            primary_guild = member.primary_guild
            if primary_guild is None:
                return await ctx.reply("❌ This user has no primary guild set.")

            is_target = primary_guild.id == self.target_primary_guild_id

            embed = discord.Embed(title="🏷️ Primary Guild Information", color=0x7289DA)
            embed.add_field(name="Member", value=member.mention, inline=True)
            embed.add_field(name="Guild ID", value=primary_guild.id, inline=True)
            
            if primary_guild.tag:
                embed.add_field(name="Guild Tag", value=primary_guild.tag, inline=True)
            else:
                embed.add_field(name="Guild Tag", value="None", inline=True)
                
            embed.add_field(
                name="Promoter Status",
                value="✅ Would Get Role" if is_target else "❌ No Role",
                inline=True
            )

            await ctx.send(embed=embed)

        except Exception as e:
            await ctx.reply(f"❌ Error: {e}")

    @commands.command(name="promoter_stats")
    @commands.has_permissions(manage_roles=True)
    async def promoter_stats(self, ctx):
        """Check promoter statistics"""
        role = ctx.guild.get_role(self.promoter_role_id)
        total_promoters = (
            len([m for m in ctx.guild.members if role in m.roles and not m.bot])
            if role
            else 0
        )

        members_with_target_guild = 0
        for member in ctx.guild.members:
            if not member.bot and self.has_target_primary_guild(member):
                members_with_target_guild += 1

        # Calculate time until next full recheck
        next_recheck = self.last_full_recheck + timedelta(seconds=self.full_recheck_interval)
        time_until = next_recheck - datetime.now()
        minutes_until = int(time_until.total_seconds() / 60)

        embed = discord.Embed(title="🤖 Promoter System Stats", color=0x7289DA)
        embed.add_field(name="👥 Promoters", value=total_promoters, inline=True)
        embed.add_field(name="🏷️ Has Primary Guild", value=members_with_target_guild, inline=True)
        embed.add_field(name="🔄 Fallback Interval", value="30 seconds", inline=True)
        embed.add_field(name="⏰ Full Recheck Interval", value=f"{self.full_recheck_interval} seconds", inline=True)
        embed.add_field(name="⏱️ Next Full Recheck", value=f"In ~{minutes_until} minutes", inline=True)
        embed.add_field(name="📊 Cache Size", value=len(self.status_cache), inline=True)

        await ctx.send(embed=embed)

    @commands.command(name="force_check")
    @commands.has_permissions(manage_roles=True)
    async def force_check(self, ctx, member: discord.Member = None):
        """Force instant check (uses cache for status)"""
        if member:
            await self.instant_member_check(member, "Manual force check")
            await ctx.send(f"✅ Checked {member.mention}")
        else:
            message = await ctx.send("⚡ Checking all members (using cache)...")
            checked = 0
            for member in ctx.guild.members:
                if not member.bot:
                    await self.instant_member_check(member, "Manual bulk check")
                    checked += 1

            await message.edit(content=f"✅ Checked {checked} members")

    @commands.command(name="force_recheck")
    @commands.has_permissions(manage_guild=True)
    async def force_recheck(self, ctx, scope: str = "all"):
        """
        Force a FULL recheck from scratch (no cache)
        
        Scope options:
        - all: Recheck all members in all guilds (default)
        - here: Recheck only members in this guild
        - promoters: Recheck only current promoters
        """
        await ctx.send(f"🔄 Starting full recheck (scope: {scope})...")
        
        if scope.lower() == "here":
            # Recheck only current guild
            total = 0
            updated = 0
            role = ctx.guild.get_role(self.promoter_role_id)
            
            for member in ctx.guild.members:
                if member.bot:
                    continue
                
                # Clear cache
                cache_key = f"{ctx.guild.id}-{member.id}"
                if cache_key in self.status_cache:
                    del self.status_cache[cache_key]
                
                current_has_promotion = await self.has_promotion_content(member)
                currently_has_role = role in member.roles if role else False
                
                if current_has_promotion and not currently_has_role:
                    await member.add_roles(role, reason="Manual full recheck")
                    updated += 1
                elif not current_has_promotion and currently_has_role:
                    await member.remove_roles(role, reason="Manual full recheck")
                    updated += 1
                
                total += 1
            
            await ctx.send(f"✅ Recheck complete: {total} members checked, {updated} updated")
            
        elif scope.lower() == "promoters":
            # Recheck only current promoters
            role = ctx.guild.get_role(self.promoter_role_id)
            if not role:
                return await ctx.send("❌ Promoter role not found")
            
            promoters = [m for m in ctx.guild.members if role in m.roles and not m.bot]
            await ctx.send(f"🔄 Rechecking {len(promoters)} current promoters...")
            
            updated = 0
            for member in promoters:
                # Clear cache
                cache_key = f"{ctx.guild.id}-{member.id}"
                if cache_key in self.status_cache:
                    del self.status_cache[cache_key]
                
                if not await self.has_promotion_content(member):
                    await member.remove_roles(role, reason="Manual promoter recheck")
                    updated += 1
                
                await asyncio.sleep(0.2)  # Small delay
            
            await ctx.send(f"✅ Promoter recheck complete: {updated} promoters removed")
            
        else:
            # Recheck all members in all guilds
            await ctx.send("🔄 Full global recheck started. This may take a while...")
            await self.full_recheck_all_members(reason="Manual full recheck")
            await ctx.send("✅ Full global recheck complete!")

    @commands.command(name="debug_promoter")
    @commands.has_permissions(manage_roles=True)
    async def debug_promoter(self, ctx, member: discord.Member = None):
        """Debug promoter info"""
        target = member or ctx.author

        embed = discord.Embed(title=f"🐛 Debug - {target.display_name}", color=0xFF9900)
        embed.add_field(name="🆔 ID", value=target.id, inline=True)
        embed.add_field(name="🌐 Status", value=str(target.status).title(), inline=True)

        has_target_guild = self.has_target_primary_guild(target)
        primary_guild = target.primary_guild
        
        if primary_guild:
            # PrimaryGuild only has id and tag, no name
            guild_info = f"ID: {primary_guild.id}"
            if primary_guild.tag:
                guild_info += f" | Tag: {primary_guild.tag}"
        else:
            guild_info = "None"

        embed.add_field(
            name="🎯 Has Target Guild",
            value="✅ Yes" if has_target_guild else "❌ No",
            inline=True,
        )
        embed.add_field(name="🏷️ Primary Guild", value=guild_info[:50], inline=True)

        has_promotion = await self.has_promotion_content(target)
        role = ctx.guild.get_role(self.promoter_role_id)
        has_role = role in target.roles if role else False

        # Show cache info
        cache_key = f"{ctx.guild.id}-{target.id}"
        cached_status = self.status_cache.get(cache_key, "Not cached")

        embed.add_field(
            name="📢 Promotion",
            value="✅ Yes" if has_promotion else "❌ No",
            inline=True,
        )
        embed.add_field(
            name="🎪 Has Role", value="✅ Yes" if has_role else "❌ No", inline=True
        )
        embed.add_field(
            name="💾 Cached Status", value=f"```{cached_status}```", inline=False
        )

        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(PromoterRole(bot))