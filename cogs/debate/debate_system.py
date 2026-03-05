from datetime import datetime, timedelta
import aiosqlite
import discord
from discord.ext import commands
from .debate_config import STATUS_EMOJIS, DB_PATH
from .debate_database import DebateDatabase
from .debate_ui import CreateDebateView
from .debate_config import (
    FORUM_CHANNEL_ID,
    MOD_CHANNEL_ID,
    MOD_ROLE_ID,
    COLORS,
    HOT_TAG_NAME,
    HOT_TAG_EMOJI,
)


class DebateSystem(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bot.forum_channel_id = FORUM_CHANNEL_ID
        self.bot.mod_channel_id = MOD_CHANNEL_ID
        self.bot.mod_role_id = MOD_ROLE_ID
        self.bot.temp_submissions = {}  # Temporary storage for submissions
        self.bot.db = DebateDatabase()

    async def cog_load(self):
        """Called when cog is loaded"""
        await self.bot.db.init_db()
        await self.ensure_hot_tag_exists()

    @commands.group(name="debate", invoke_without_command=True)
    async def debate(self, ctx):
        """Main debate command group"""
        embed = discord.Embed(
            title="📢 Debate System Commands",
            description="Available subcommands:",
            color=COLORS["info"],
        )
        embed.add_field(
            name="📊 `debate stats [@user]`",
            value="View debate stats for yourself or another user",
            inline=False,
        )
        embed.add_field(
            name="🔒 `debate close`",
            value="Force close a debate (Mod only)",
            inline=False,
        )
        embed.add_field(
            name="🔒 `debate lock [reason]`",
            value="Lock a debate thread (Mod only)",
            inline=False,
        )
        embed.add_field(
            name="🔓 `debate unlock`",
            value="Unlock a debate thread (Mod only)",
            inline=False,
        )
        embed.add_field(
            name="🔇 `debate mute @user [duration] [reason]`",
            value="Silence a user in debates (Mod only)",
            inline=False,
        )
        embed.add_field(
            name="🔊 `debate unmute @user`",
            value="Remove silence from a user (Mod only)",
            inline=False,
        )
        embed.add_field(
            name="📋 `debate muted`",
            value="List all silenced users (Mod only)",
            inline=False,
        )
        embed.set_footer(text="Duration format: 10m, 1h, 1d, permanent")
        await ctx.send(embed=embed)

    @debate.command(name="setup")
    @commands.has_permissions(manage_guild=True)
    async def setup_debate_channel(self, ctx, channel: discord.ForumChannel = None):
        """
        Setup the debate forum with rules, stats, and create button
        Usage: !debate setup #forum-channel
        If no channel specified, uses the configured forum channel
        """
        # Determine which channel to use
        forum = channel or self.bot.get_channel(FORUM_CHANNEL_ID)

        if not forum:
            await ctx.send(
                "❌ Please specify a forum channel or set FORUM_CHANNEL_ID in config!"
            )
            return

        if not isinstance(forum, discord.ForumChannel):
            await ctx.send("❌ That's not a forum channel!")
            return

        # Create or get dashboard thread
        dashboard_thread = None
        for thread in forum.threads:
            if thread.name == "📋 Debate Dashboard" and not thread.archived:
                dashboard_thread = thread
                break

        if not dashboard_thread:
            # Create new dashboard thread
            dashboard_thread = await forum.create_thread(
                name="📋 Debate Dashboard",
                content="Welcome to the Debate System Dashboard!",
                applied_tags=[],
            )
            dashboard_thread = dashboard_thread[0]  # Get the thread object
            await ctx.send(
                f"✅ Created new dashboard thread: {dashboard_thread.mention}"
            )
        else:
            await ctx.send(
                f"✅ Found existing dashboard thread: {dashboard_thread.mention}"
            )

        # Clear old bot messages in dashboard thread
        cleared = 0
        async for message in dashboard_thread.history(limit=50):
            if message.author == self.bot.user:
                await message.delete()
                cleared += 1

        # Post rules embed
        rules_embed = discord.Embed(
            title="📜 Debate Rules",
            description=(
                "• **No personal attacks** — Argue the idea, not the person\n"
                "• **Stay on topic** — Keep discussions relevant to the debate\n"
                "• **Cite sources** — Back up claims with evidence\n"
                "• **No spam** — Quality over quantity\n"
                "• **Respect all participants** — Civil discourse only\n"
                "• **Follow server guidelines** — Standard rules apply"
            ),
            color=COLORS["rules"],
        )
        rules_embed.set_footer(text="Violations may result in removal from debates")
        await dashboard_thread.send(embed=rules_embed)

        # Post analytics embed
        analytics_embed = discord.Embed(
            title="📊 Debate Hall of Stats & Leaderboard",
            description="Loading stats...",
            color=COLORS["analytics"],
        )
        analytics_msg = await dashboard_thread.send(embed=analytics_embed)
        await self.bot.db.set_config("analytics_message_id", str(analytics_msg.id))

        # Post create debate button embed
        create_embed = discord.Embed(
            title="🆕 Start a New Debate",
            description="Click the button below to propose a debate topic. Moderators will review your submission.",
            color=COLORS["info"],
        )
        view = CreateDebateView(self.bot)
        await dashboard_thread.send(embed=create_embed, view=view)

        await ctx.send(
            f"✅ Debate system setup complete in {dashboard_thread.mention}!\nCleared {cleared} old messages."
        )

        # Preview what was created
        preview = discord.Embed(
            title="✨ Setup Complete!",
            description=f"Check {dashboard_thread.mention} for:",
            color=COLORS["success"],
        )
        preview.add_field(name="1️⃣", value="📜 Rules Embed", inline=True)
        preview.add_field(name="2️⃣", value="📊 Stats Embed", inline=True)
        preview.add_field(name="3️⃣", value="🆕 Create Button", inline=True)
        await ctx.send(embed=preview)

    @debate.command(name="stats")
    async def user_stats(self, ctx, user: discord.User = None):
        """View debate stats for a user"""
        user = user or ctx.author

        # Get user stats from database using the new method
        stats = await self.bot.db.get_user_stats(str(user.id))

        embed = discord.Embed(
            title=f"📊 Debate Stats: {user.display_name}",
            color=user.color if user.color.value else COLORS["info"],
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name="Debates Created", value=stats["debates_created"])
        embed.add_field(name="Messages Sent", value=stats["messages_sent"])
        embed.add_field(name="Weekly Score", value=stats["weekly_score"])

        await ctx.send(embed=embed)

    @debate.command(name="close")
    @commands.has_role(MOD_ROLE_ID)
    async def force_close(self, ctx):
        """
        Force close the current debate
        Usage: !debate close
        Use this command inside the debate thread you want to close
        """
        # Check if command is used in a thread
        if not isinstance(ctx.channel, discord.Thread):
            await ctx.send("❌ This command must be used inside a debate thread!")
            return

        thread = ctx.channel

        # Verify it's a debate thread
        if (
            not isinstance(thread.parent, discord.ForumChannel)
            or thread.parent.id != FORUM_CHANNEL_ID
        ):
            await ctx.send("❌ This is not a debate thread!")
            return

        await self.bot.db.close_debate(str(thread.id))
        await thread.edit(archived=True, locked=True)

        # Send confirmation in the thread before it closes
        await thread.send("✅ This debate has been closed by a moderator.")

    @debate.command(name="lock")
    @commands.has_role(MOD_ROLE_ID)
    async def lock_debate(self, ctx, *, reason: str = "No reason provided"):
        """
        Lock the current debate thread - prevents any new messages
        Usage: !debate lock Too heated, take a break
        Use this command inside the debate thread you want to lock
        """
        # Check if command is used in a thread
        if not isinstance(ctx.channel, discord.Thread):
            await ctx.send("❌ This command must be used inside a debate thread!")
            return

        thread = ctx.channel

        # Verify it's a debate thread
        if (
            not isinstance(thread.parent, discord.ForumChannel)
            or thread.parent.id != FORUM_CHANNEL_ID
        ):
            await ctx.send("❌ This is not a debate thread!")
            return

        # Check if already locked
        if thread.locked:
            await ctx.send("❌ This debate is already locked!")
            return

        # Lock the thread
        await thread.edit(
            locked=True,
            archived=False,  # Keep it visible but locked
            name=f"🔒 {thread.name[2:] if thread.name[0] in STATUS_EMOJIS.values() else thread.name}",
        )

        # Send lock notification in thread
        embed = discord.Embed(
            title="🔒 Debate Locked",
            description="This debate has been locked by a moderator.",
            color=COLORS["warning"],
            timestamp=datetime.now(),
        )
        embed.add_field(name="Reason", value=reason)
        embed.add_field(name="Moderator", value=ctx.author.mention)
        embed.set_footer(text="No further messages can be sent in this thread")

        await thread.send(embed=embed)

        # Log in mod channel
        mod_channel = self.bot.get_channel(MOD_CHANNEL_ID)
        if mod_channel:
            log_embed = discord.Embed(
                title="🔒 Debate Locked",
                description=f"Thread: {thread.mention}\nTopic: {thread.name}",
                color=COLORS["warning"],
                timestamp=datetime.now(),
            )
            log_embed.add_field(name="Moderator", value=ctx.author.mention)
            log_embed.add_field(name="Reason", value=reason)
            await mod_channel.send(embed=log_embed)

        # No need to send confirmation in the command channel since we're in the thread

    @debate.command(name="unlock")
    @commands.has_role(MOD_ROLE_ID)
    async def unlock_debate(self, ctx):
        """
        Unlock the current debate thread
        Usage: !debate unlock
        Use this command inside the debate thread you want to unlock
        """
        # Check if command is used in a thread
        if not isinstance(ctx.channel, discord.Thread):
            await ctx.send("❌ This command must be used inside a debate thread!")
            return

        thread = ctx.channel

        # Verify it's a debate thread
        if (
            not isinstance(thread.parent, discord.ForumChannel)
            or thread.parent.id != FORUM_CHANNEL_ID
        ):
            await ctx.send("❌ This is not a debate thread!")
            return

        if not thread.locked:
            await ctx.send("❌ This debate is not locked!")
            return

        # Unlock the thread
        await thread.edit(
            locked=False,
            name=thread.name[2:] if thread.name.startswith("🔒") else thread.name,
        )

        # Send unlock notification
        embed = discord.Embed(
            title="🔓 Debate Unlocked",
            description=f"This debate has been unlocked by {ctx.author.mention}",
            color=COLORS["success"],
            timestamp=datetime.now(),
        )
        await thread.send(embed=embed)

    # No need to send confirmation in the command channel since we're in the thread

    @debate.command(name="mute")
    @commands.has_role(MOD_ROLE_ID)
    async def mute_user(
        self,
        ctx,
        user: discord.Member,
        duration: str = "1h",
        *,
        reason: str = "No reason provided",
    ):
        """
        Silence a user in debates - deletes their messages automatically
        Duration format: 10m, 1h, 1d, permanent
        Usage: !debate mute @user 30m Stop spamming
        """
        # Parse duration
        duration_seconds = self.parse_duration(duration)
        if duration_seconds is None and duration.lower() != "permanent":
            await ctx.send(
                "❌ Invalid duration format! Use: `10m`, `1h`, `1d`, or `permanent`"
            )
            return

        is_permanent = duration.lower() == "permanent"

        # Save to database
        expiry = (
            None
            if is_permanent
            else datetime.now() + timedelta(seconds=duration_seconds)
        )
        await self.bot.db.add_mute(
            str(user.id),
            expiry.isoformat() if expiry else None,
            reason,
            str(ctx.author.id),
        )

        # Send confirmation
        duration_text = "permanently" if is_permanent else f"for {duration}"
        embed = discord.Embed(
            title="🔇 User Silenced in Debates",
            description=f"{user.mention} has been silenced {duration_text}",
            color=COLORS["warning"],
            timestamp=datetime.now(),
        )
        embed.add_field(name="Reason", value=reason)
        embed.add_field(name="Moderator", value=ctx.author.mention)

        await ctx.send(embed=embed)

        # Log in mod channel
        mod_channel = self.bot.get_channel(MOD_CHANNEL_ID)
        if mod_channel:
            log_embed = discord.Embed(
                title="🔇 Debate Mute Applied",
                description=f"**User:** {user.mention}\n**Duration:** {duration_text}\n**Reason:** {reason}",
                color=COLORS["warning"],
                timestamp=datetime.now(),
            )
            log_embed.set_footer(text=f"Mod: {ctx.author.display_name}")
            await mod_channel.send(embed=log_embed)

        # Try to DM the user
        try:
            dm_embed = discord.Embed(
                title="🔇 You've been silenced in debates",
                description=f"You cannot send messages in debate channels {duration_text}.",
                color=COLORS["warning"],
            )
            dm_embed.add_field(name="Reason", value=reason)
            dm_embed.add_field(name="Moderator", value=ctx.author.display_name)
            await user.send(embed=dm_embed)
        except Exception:
            pass  # Can't DM user, ignore

    @debate.command(name="unmute")
    @commands.has_role(MOD_ROLE_ID)
    async def unmute_user(self, ctx, user: discord.Member):
        """Remove silence from a user"""
        # Check if user is muted in DB
        muted_users = await self.bot.db.get_all_mutes()
        if str(user.id) not in [m["user_id"] for m in muted_users]:
            await ctx.send(f"❌ {user.mention} is not silenced.")
            return

        # Remove from database
        await self.bot.db.remove_mute(str(user.id))

        embed = discord.Embed(
            title="🔊 User Unsilenced",
            description=f"{user.mention} can now speak in debates again.",
            color=COLORS["success"],
        )
        await ctx.send(embed=embed)

        # Try to DM the user
        try:
            await user.send(
                f"✅ You have been unsilenced in debates by {ctx.author.display_name}"
            )
        except Exception:
            pass

    @debate.command(name="muted")
    @commands.has_role(MOD_ROLE_ID)
    async def list_muted(self, ctx):
        """Show all currently muted users"""
        muted_users = await self.bot.db.get_all_mutes()

        if not muted_users:
            await ctx.send("✅ No users are currently silenced.")
            return

        embed = discord.Embed(
            title="🔇 Silenced Users", color=COLORS["warning"], timestamp=datetime.now()
        )

        # List active mutes
        for mute_info in muted_users:
            user = self.bot.get_user(int(mute_info["user_id"]))
            if not user:
                continue

            expiry_text = (
                "Permanent"
                if not mute_info["expiry"]
                else f"<t:{int(mute_info['expiry'].timestamp())}:R>"
            )
            mod = self.bot.get_user(int(mute_info["mod_id"]))
            mod_name = mod.display_name if mod else "Unknown"

            embed.add_field(
                name=f"{user.display_name} ({user.name})",
                value=f"**Expires:** {expiry_text}\n**Reason:** {mute_info['reason']}\n**Mod:** {mod_name}",
                inline=False,
            )

        await ctx.send(embed=embed)

    def parse_duration(self, duration: str) -> int | None:
        """Parse duration string to seconds"""
        duration = duration.lower().strip()
        if duration == "permanent":
            return None

        try:
            unit = duration[-1]
            value = int(duration[:-1])

            if unit == "m":
                return value * 60
            elif unit == "h":
                return value * 3600
            elif unit == "d":
                return value * 86400
            else:
                return None
        except Exception:
            return None

    async def ensure_hot_tag_exists(self):
        """Ensure the hot tag exists in the forum channel"""
        forum = self.bot.get_channel(FORUM_CHANNEL_ID)
        if not forum or not isinstance(forum, discord.ForumChannel):
            return None

        # Check if hot tag already exists
        for tag in forum.available_tags:
            if tag.name == HOT_TAG_NAME:
                # Store the tag ID for later use
                return tag

        # Create the hot tag if it doesn't exist
        try:
            hot_tag = discord.ForumTag(
                name=HOT_TAG_NAME,
                emoji=HOT_TAG_EMOJI,
                moderated=False,  # Non-moderated so anyone can see/use it
            )

            current_tags = list(forum.available_tags)
            if len(current_tags) < 20:  # Discord's limit is 20 tags
                current_tags.append(hot_tag)
                await forum.edit(available_tags=current_tags)

                # Find and store the new tag's ID
                for tag in forum.available_tags:
                    if tag.name == HOT_TAG_NAME:
                        return tag
        except Exception as e:
            print(f"Error creating hot tag: {e}")

        return None


async def setup(bot):
    await bot.add_cog(DebateSystem(bot))
    from cogs.debate.debate_ui import CreateDebateView, ModApprovalView

    # Add the create debate button view
    bot.add_view(CreateDebateView(bot))

    # Re-attach any pending mod approval views from the database
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT user_id, mod_message_id FROM pending_submissions WHERE mod_message_id IS NOT NULL"
        )
        rows = await cursor.fetchall()

        for user_id, mod_message_id in rows:
            if mod_message_id:
                view = ModApprovalView(bot, int(user_id))
                bot.add_view(view, message_id=int(mod_message_id))
