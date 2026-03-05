import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import re
from typing import Optional


class IntroChannelCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.intro_channel_id = None  # Set this to your intro channel ID
        self.format_patterns = [
            r"name.*:|name\s*is",  # Checks for name field
            r"age.*:|age\s*is",  # Checks for age field
            r"from.*:|location",  # Checks for location
            r"hobbies|interests",  # Checks for hobbies/interests
        ]
        self.auto_delete_time = 60  # Seconds before deleting non-intros
        self.reaction_emojis = ["👋", "🎉", "👍", "🙌"]  # Emojis to add to valid intros

    async def cog_check(self, ctx):
        """Ensure commands are used in the intro channel"""
        if ctx.channel.id == self.intro_channel_id:
            return True
        await ctx.send(f"This command only works in the intro channel!", ephemeral=True)
        return False

    @commands.Cog.listener()
    async def on_ready(self):
        """Set up when bot is ready"""
        print(f"{self.__class__.__name__} cog ready!")
        # You can set the channel ID here or use a command
        # self.intro_channel_id = 123456789012345678  # Replace with your channel ID

    @commands.Cog.listener()
    async def on_message(self, message):
        """Check every message in intro channel"""
        if message.author.bot:
            return

        # Only check in intro channel
        if message.channel.id != self.intro_channel_id:
            return

        # Check if message is a valid intro
        is_valid_intro = await self.check_intro_format(message.content)

        if is_valid_intro:
            # Add reactions to valid intro
            await self.add_intro_reactions(message)

            # Optional: Send format confirmation
            # await message.reply("✅ Thanks for your intro!", delete_after=10)

        else:
            # Delete non-intro after delay
            await self.delete_non_intro(message)

    async def check_intro_format(self, content: str) -> bool:
        """Check if message follows intro format"""
        content_lower = content.lower()

        # Check for minimum 2 format patterns
        matches = 0
        for pattern in self.format_patterns:
            if re.search(pattern, content_lower):
                matches += 1

        # Also check for minimum length (at least 20 chars)
        if matches >= 2 and len(content) >= 20:
            return True
        return False

    async def add_intro_reactions(self, message):
        """Add reaction emojis to valid intro"""
        for emoji in self.reaction_emojis:
            try:
                await message.add_reaction(emoji)
                await asyncio.sleep(0.5)  # Delay to avoid rate limits
            except:
                pass

    async def delete_non_intro(self, message):
        """Delete non-intro messages after delay"""
        try:
            # Send warning first
            warning = await message.reply(
                f"⚠️ This doesn't look like a proper intro. "
                f"Please use the format:\n"
                f"```Name: \nAge: \nLocation: \nHobbies: \nAbout me:```\n"
                f"Message will be deleted in {self.auto_delete_time} seconds.",
                delete_after=self.auto_delete_time,
            )

            # Wait and delete
            await asyncio.sleep(self.auto_delete_time)
            await message.delete()

        except discord.NotFound:
            pass  # Message already deleted
        except discord.Forbidden:
            pass  # Bot doesn't have permissions

    @commands.command(name="set_intro_channel")
    @commands.has_permissions(administrator=True)
    async def set_intro_channel(self, ctx, channel: discord.TextChannel):
        """Set the intro channel (Admin only)"""
        self.intro_channel_id = channel.id
        await ctx.send(f"✅ Intro channel set to {channel.mention}")

    @commands.command(name="intro_format")
    async def show_intro_format(self, ctx):
        """Show the recommended intro format"""
        format_embed = discord.Embed(
            title="📝 Intro Format",
            description="Please use this format for your intro:",
            color=discord.Color.blue(),
        )

        format_embed.add_field(
            name="Required Fields:",
            value=(
                "**Name:** Your name or nickname\n"
                "**Age:** Your age (optional but preferred)\n"
                "**Location:** Where you're from\n"
                "**Hobbies/Interests:** What you enjoy doing\n"
                "**About me:** A brief introduction"
            ),
            inline=False,
        )

        format_embed.add_field(
            name="Example:",
            value=(
                "```Name: Alex\n"
                "Age: 25\n"
                "Location: New York\n"
                "Hobbies: Gaming, coding, hiking\n"
                "About me: Hello! I'm a developer who loves Discord bots!```"
            ),
            inline=False,
        )

        format_embed.set_footer(
            text="Your intro will get auto-reactions if it follows the format!"
        )
        await ctx.send(embed=format_embed)

    @commands.command(name="check_intro")
    async def check_intro_command(self, ctx, *, intro_text: Optional[str] = None):
        """Check if your intro follows the format"""
        if intro_text is None:
            # Check last message if no text provided
            async for message in ctx.channel.history(limit=2):
                if message.author == ctx.author and not message.author.bot:
                    intro_text = message.content
                    break

        if not intro_text:
            await ctx.send("Please provide an intro text to check!", ephemeral=True)
            return

        is_valid = await self.check_intro_format(intro_text)

        if is_valid:
            await ctx.send(
                "✅ Your intro follows the format! Good job!", ephemeral=True
            )
        else:
            await ctx.send(
                "⚠️ Your intro might be missing some required fields.\n"
                "Use `!intro_format` to see the recommended format.",
                ephemeral=True,
            )

    @commands.command(name="intro_stats", aliases=["introinfo"])
    @commands.has_permissions(manage_messages=True)
    async def intro_stats(self, ctx):
        """Show intro channel stats (Mod only)"""
        if not self.intro_channel_id:
            await ctx.send("Intro channel not set yet!")
            return

        channel = self.bot.get_channel(self.intro_channel_id)
        if not channel:
            await ctx.send("Intro channel not found!")
            return

        # Count messages in last 7 days
        week_ago = discord.utils.utcnow() - discord.utils.timedelta(days=7)
        intro_count = 0

        async for message in channel.history(after=week_ago, limit=500):
            if not message.author.bot:
                if await self.check_intro_format(message.content):
                    intro_count += 1

        embed = discord.Embed(
            title="📊 Intro Channel Stats", color=discord.Color.green()
        )
        embed.add_field(name="Channel", value=channel.mention, inline=True)
        embed.add_field(
            name="Intros (Last 7 days)", value=str(intro_count), inline=True
        )
        embed.add_field(
            name="Auto-delete time", value=f"{self.auto_delete_time}s", inline=True
        )
        embed.add_field(
            name="Reaction Emojis", value=" ".join(self.reaction_emojis), inline=False
        )

        await ctx.send(embed=embed)

    @commands.command(name="set_reactions")
    @commands.has_permissions(manage_messages=True)
    async def set_reactions(self, ctx, *emojis: str):
        """Set custom reaction emojis for intros (Mod only)"""
        if len(emojis) > 10:
            await ctx.send("Too many emojis! Max is 10.")
            return

        self.reaction_emojis = list(emojis)[:10]  # Limit to 10 emojis
        await ctx.send(f"✅ Reaction emojis set to: {' '.join(self.reaction_emojis)}")

    @commands.command(name="set_delete_time")
    @commands.has_permissions(manage_messages=True)
    async def set_delete_time(self, ctx, seconds: int):
        """Set auto-delete delay for non-intros (Mod only)"""
        if seconds < 10:
            await ctx.send("Time must be at least 10 seconds!")
            return
        if seconds > 300:
            await ctx.send("Time must be less than 5 minutes (300 seconds)!")
            return

        self.auto_delete_time = seconds
        await ctx.send(f"✅ Auto-delete time set to {seconds} seconds")


async def setup(bot):
    await bot.add_cog(IntroChannelCog(bot))
