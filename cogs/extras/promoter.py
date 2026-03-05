import discord
from discord.ext import tasks, commands
import sqlite3
from datetime import datetime


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
        self.server_tag = "Raही"
        self.log_channel_id = 1443119503285293098

        self.init_db()
        self.status_cache = {}  # Cache to track status changes

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
    async def on_ready(self):
        print("🚀 PromoterRole cog loaded!")
        print(f"📊 Monitoring: Status & Primary Guild Tag '{self.server_tag}'")
        self.fallback_check.start()

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

        # Check primary guild tag changes
        before_tag = self.has_primary_guild_tag(before)
        after_tag = self.has_primary_guild_tag(after)

        if before_tag != after_tag:
            changes.append(f"Primary tag {'added' if after_tag else 'removed'}")

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

    def has_primary_guild_tag(self, member):
        """Check if member has the specific primary guild tag"""
        try:
            if not hasattr(member, "primary_guild") or member.primary_guild is None:
                return False

            primary_guild = member.primary_guild
            if not hasattr(primary_guild, "tag") or primary_guild.tag is None:
                return False

            return str(primary_guild.tag) == str(self.server_tag)
        except Exception:
            return False

    async def has_promotion_content(self, member):
        """Check status and primary guild tag for promotion content"""

        # 1. Check Primary Guild Tag
        if self.has_primary_guild_tag(member):
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

        # Check primary guild tag
        if self.has_primary_guild_tag(member):
            sources.append(f"Primary Guild Tag: {self.server_tag}")

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
        """30-second fallback check"""
        for guild in self.bot.guilds:
            role = guild.get_role(self.promoter_role_id)
            if not role:
                continue

            for member in guild.members:
                if not member.bot:
                    has_tag = self.has_primary_guild_tag(member)
                    has_role = role in member.roles

                    if has_tag != has_role:
                        full_promotion = await self.has_promotion_content(member)
                        if full_promotion != has_role:
                            await self.instant_member_check(member, "30s fallback")

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

            embed.add_field(
                name="Role Action", value=f"<@&{self.promoter_role_id}>", inline=True
            )
            embed.set_thumbnail(url=member.display_avatar.url)

            await channel.send(embed=embed)

        except Exception:
            pass

    # COMMANDS
    @commands.command(name="pgtag")
    async def pgtag(self, ctx, member: discord.Member = None):
        """Check a member's primary guild tag"""
        member = member or ctx.author

        try:
            primary_guild = member.primary_guild
            if primary_guild is None:
                return await ctx.reply("❌ This user has no primary guild set.")

            tag = primary_guild.tag
            if tag is None:
                return await ctx.reply(
                    "⚠️ This user has a primary guild but no tag set."
                )

            would_promote = tag == self.server_tag

            embed = discord.Embed(title="🏷️ Primary Guild Tag", color=0x7289DA)
            embed.add_field(name="Member", value=member.mention, inline=True)
            embed.add_field(name="Primary Guild Tag", value=f"**{tag}**", inline=True)
            embed.add_field(
                name="Promoter Status",
                value="✅ Would Get Role" if would_promote else "❌ No Role",
                inline=True,
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

        members_with_tag = 0
        for member in ctx.guild.members:
            if not member.bot and self.has_primary_guild_tag(member):
                members_with_tag += 1

        embed = discord.Embed(title="🤖 Promoter System Stats", color=0x7289DA)
        embed.add_field(name="👥 Promoters", value=total_promoters, inline=True)
        embed.add_field(name="🏷️ Primary Tags", value=members_with_tag, inline=True)
        embed.add_field(name="🔄 Check Interval", value="30 seconds", inline=True)

        await ctx.send(embed=embed)

    @commands.command(name="force_check")
    @commands.has_permissions(manage_roles=True)
    async def force_check(self, ctx, member: discord.Member = None):
        """Force instant check"""
        if member:
            await self.instant_member_check(member, "Manual force check")
            await ctx.send(f"✅ Checked {member.mention}")
        else:
            message = await ctx.send("⚡ Checking all members...")
            checked = 0
            for member in ctx.guild.members:
                if not member.bot:
                    await self.instant_member_check(member, "Manual bulk check")
                    checked += 1

            await message.edit(content=f"✅ Checked {checked} members")

    @commands.command(name="debug_promoter")
    @commands.has_permissions(manage_roles=True)
    async def debug_promoter(self, ctx, member: discord.Member = None):
        """Debug promoter info"""
        target = member or ctx.author

        embed = discord.Embed(title=f"🐛 Debug - {target.display_name}", color=0xFF9900)
        embed.add_field(name="🆔 ID", value=target.id, inline=True)
        embed.add_field(name="🌐 Status", value=str(target.status).title(), inline=True)

        has_primary_tag = self.has_primary_guild_tag(target)
        primary_guild = target.primary_guild
        tag_value = primary_guild.tag if primary_guild and primary_guild.tag else "None"

        embed.add_field(
            name="🎯 Primary Tag",
            value="✅ Yes" if has_primary_tag else "❌ No",
            inline=True,
        )
        embed.add_field(name="🏷️ Tag Value", value=tag_value, inline=True)

        has_promotion = await self.has_promotion_content(target)
        role = ctx.guild.get_role(self.promoter_role_id)
        has_role = role in target.roles if role else False

        embed.add_field(
            name="📢 Promotion",
            value="✅ Yes" if has_promotion else "❌ No",
            inline=True,
        )
        embed.add_field(
            name="🎪 Has Role", value="✅ Yes" if has_role else "❌ No", inline=True
        )

        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(PromoterRole(bot))
