import discord
from discord.ext import commands
import aiohttp
import re
from typing import Optional, List, Tuple, Dict, Set, Union
import asyncio
from collections import defaultdict, deque
import time


class StealEmoji(commands.Cog):
    """Steal emojis from messages by replying! Only steals new ones not already on server."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Fixed regex - properly captures animated flag
        self.emoji_regex = re.compile(r"<(a?):(\w+):(\d+)>")

        # Rate limiting (5 steals per minute per user)
        self.user_cooldowns: Dict[int, deque] = defaultdict(lambda: deque(maxlen=5))
        self.cooldown_period = 60  # seconds

        # Cache for existing server emojis (to avoid duplicate checks)
        self.server_emoji_cache: Dict[int, Set[str]] = (
            {}
        )  # guild_id: set of emoji names
        self.server_emoji_ids: Dict[int, Set[int]] = {}  # guild_id: set of emoji IDs

    def update_server_cache(self, guild: discord.Guild):
        """Update the cache of emojis for a server."""
        self.server_emoji_cache[guild.id] = {
            emoji.name.lower() for emoji in guild.emojis
        }
        self.server_emoji_ids[guild.id] = {emoji.id for emoji in guild.emojis}

    def get_server_emojis(self, guild: discord.Guild) -> Tuple[Set[str], Set[int]]:
        """Get cached emoji names and IDs for a server."""
        if guild.id not in self.server_emoji_cache:
            self.update_server_cache(guild)
        return self.server_emoji_cache[guild.id], self.server_emoji_ids[guild.id]

    def check_rate_limit(self, user_id: int) -> Tuple[bool, Optional[int]]:
        """Check if user is rate limited."""
        now = time.time()
        user_timestamps = self.user_cooldowns[user_id]

        # Clean old timestamps
        while user_timestamps and now - user_timestamps[0] > self.cooldown_period:
            user_timestamps.popleft()

        if len(user_timestamps) >= 5:
            # Rate limited - return time until next available
            wait_time = int(self.cooldown_period - (now - user_timestamps[0]))
            return False, wait_time

        return True, None

    def extract_emojis_from_message(
        self, message: discord.Message
    ) -> List[Tuple[str, int, bool]]:
        """Extract all custom emojis from a message."""
        matches = self.emoji_regex.findall(message.content)
        emojis = []
        for match in matches:
            # match is a tuple like: ('a', 'emoji_name', '123456') or ('', 'emoji_name', '123456')
            animated_flag = match[0]  # This will be 'a' for animated, '' for static
            name = match[1]
            emoji_id = match[2]

            is_animated = bool(animated_flag)  # True if animated_flag is 'a'
            emojis.append((name, int(emoji_id), is_animated))

        return emojis

    def get_emoji_limits(self, guild: discord.Guild) -> Tuple[int, int]:
        """Get static and animated emoji limits based on boost tier."""
        # Base limits
        if guild.premium_tier == 0:
            return 50, 50  # static, animated
        elif guild.premium_tier == 1:
            return 100, 100
        elif guild.premium_tier == 2:
            return 150, 150
        else:  # tier 3
            return 250, 250

    def get_emoji_counts(self, guild: discord.Guild) -> Tuple[int, int]:
        """Get current static and animated emoji counts."""
        static = len([e for e in guild.emojis if not e.animated])
        animated = len([e for e in guild.emojis if e.animated])
        return static, animated

    def check_emoji_space(
        self, guild: discord.Guild, is_animated: bool
    ) -> Tuple[bool, int, int, int]:
        """Check if there's space for an emoji of given type."""
        static_count, animated_count = self.get_emoji_counts(guild)
        static_limit, animated_limit = self.get_emoji_limits(guild)

        if is_animated:
            available = animated_limit - animated_count
            return available > 0, available, animated_count, animated_limit
        else:
            available = static_limit - static_count
            return available > 0, available, static_count, static_limit

    async def steal_single_emoji(
        self, ctx: commands.Context, emoji_name: str, emoji_id: int, is_animated: bool
    ) -> Tuple[bool, Union[discord.Emoji, str]]:
        """Steal a single emoji and add to server."""
        try:
            # Check if we have space for this type
            has_space, available, current, limit = self.check_emoji_space(
                ctx.guild, is_animated
            )

            if not has_space:
                emoji_type = "animated" if is_animated else "static"
                return False, f"no_space|{emoji_type}|{current}|{limit}"

            # Check if name already exists (case insensitive)
            existing_names, existing_ids = self.get_server_emojis(ctx.guild)

            if emoji_name.lower() in existing_names:
                return False, "name_exists"

            if emoji_id in existing_ids:
                return False, "already_exists"

            # Download the emoji
            extension = "gif" if is_animated else "png"
            url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{extension}?quality=lossless"

            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        # Try alternative extension
                        alt_ext = "png" if is_animated else "gif"
                        url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{alt_ext}?quality=lossless"
                        async with session.get(url) as resp2:
                            if resp2.status != 200:
                                # Try without quality parameter
                                url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{extension}"
                                async with session.get(url) as resp3:
                                    if resp3.status != 200:
                                        return False, "download_failed"
                                    img_data = await resp3.read()
                            else:
                                img_data = await resp2.read()
                    else:
                        img_data = await resp.read()

            # Check file size limits
            max_size = 256 * 1024  # 256KB for static
            if is_animated:
                max_size = 1024 * 1024  # 1MB for animated

            if len(img_data) > max_size:
                return False, "too_large"

            # Create the emoji
            new_emoji = await ctx.guild.create_custom_emoji(
                name=emoji_name,
                image=img_data,
                reason=f"Stolen from message by {ctx.author} ({ctx.author.id})",
            )

            # Update cache
            self.update_server_cache(ctx.guild)

            return True, new_emoji

        except discord.Forbidden:
            return False, "no_permission"
        except discord.HTTPException as e:
            if "image" in str(e).lower():
                return False, "invalid_image"
            return False, "http_error"
        except Exception as e:
            print(f"Error stealing emoji {emoji_name}: {e}")
            return False, str(e)

    @commands.command(name="steal", aliases=["stealemoji", "addemoji", "clone"])
    @commands.has_permissions(manage_emojis=True)
    async def steal_emoji(
        self, ctx: commands.Context, *, emoji_input: Optional[str] = None
    ):
        """
        Steal emojis by replying to a message!

        Usage:
        • Reply to any message with `.steal` - steals ALL new emojis from that message
        • `.steal <emoji>` - steal a specific emoji
        • `.steal` with no reply - shows help
        """
        try:
            await ctx.message.delete()
        except Exception:
            pass

        # Check rate limit
        allowed, wait_time = self.check_rate_limit(ctx.author.id)
        if not allowed:
            embed = discord.Embed(
                title="⏰ Rate Limited",
                description=f"Please wait {wait_time} seconds before stealing more emojis.",
                color=discord.Color.orange(),
            )
            msg = await ctx.send(embed=embed)
            await asyncio.sleep(5)
            await msg.delete()
            return

        # Get current emoji counts for display
        static_count, animated_count = self.get_emoji_counts(ctx.guild)
        static_limit, animated_limit = self.get_emoji_limits(ctx.guild)

        # Case 1: User provided emoji input
        if emoji_input:
            # Try to parse the emoji
            match = self.emoji_regex.search(emoji_input)
            if match:
                # match groups: (animated_flag, name, id)
                animated_flag = match.group(1)  # Will be 'a' or empty string
                name = match.group(2)
                emoji_id = match.group(3)
                is_animated = bool(animated_flag)  # True if animated_flag is 'a'

                # Check if emoji already exists
                existing_names, existing_ids = self.get_server_emojis(ctx.guild)

                if int(emoji_id) in existing_ids:
                    embed = discord.Embed(
                        title="❌ Already Exists",
                        description="That exact emoji is already on this server!",
                        color=discord.Color.orange(),
                    )
                    msg = await ctx.send(embed=embed)
                    await asyncio.sleep(5)
                    await msg.delete()
                    return

                # Check space
                has_space, available, current, limit = self.check_emoji_space(
                    ctx.guild, is_animated
                )
                emoji_type = "animated" if is_animated else "static"

                if not has_space:
                    embed = discord.Embed(
                        title="❌ No Space",
                        description=f"No {emoji_type} emoji slots available!",
                        color=discord.Color.red(),
                    )
                    embed.add_field(
                        name="Current", value=f"{current}/{limit}", inline=True
                    )
                    embed.add_field(
                        name="Type", value=emoji_type.capitalize(), inline=True
                    )
                    embed.add_field(name="Available", value="0", inline=True)
                    msg = await ctx.send(embed=embed)
                    await asyncio.sleep(5)
                    await msg.delete()
                    return

                # Steal it
                success, result = await self.steal_single_emoji(
                    ctx, name, int(emoji_id), is_animated
                )

                if success:
                    embed = discord.Embed(
                        title="✅ Emoji Stolen!",
                        description=f"Successfully added {result} as `:{result.name}:`",
                        color=discord.Color.green(),
                    )
                    embed.add_field(
                        name="Type",
                        value="Animated" if is_animated else "Static",
                        inline=True,
                    )
                    embed.add_field(
                        name="Slots Left",
                        value=f"{available-1} {emoji_type}",
                        inline=True,
                    )

                    # Show updated counts
                    new_static, new_animated = self.get_emoji_counts(ctx.guild)
                    usage = f"Static: {new_static}/{static_limit}\nAnimated: {new_animated}/{animated_limit}"
                    embed.add_field(name="📊 Updated Usage", value=usage, inline=False)

                    await ctx.send(embed=embed)
                else:
                    error_msgs = {
                        "name_exists": f"An emoji named `:{name}:` already exists on this server!",
                        "already_exists": "This exact emoji is already on the server!",
                        "download_failed": "Failed to download the emoji. It might be from a private server or deleted.",
                        "too_large": "The emoji file is too large!",
                        "no_permission": "I don't have permission to create emojis!",
                        "invalid_image": "The emoji image format is invalid!",
                        "http_error": "Discord API error occurred. Try again!",
                    }

                    # Handle no_space specially
                    if isinstance(result, str) and result.startswith("no_space|"):
                        parts = result.split("|")
                        emoji_type = parts[1]
                        current = parts[2]
                        limit = parts[3]
                        embed = discord.Embed(
                            title="❌ No Space",
                            description=f"No {emoji_type} emoji slots available!",
                            color=discord.Color.red(),
                        )
                        embed.add_field(
                            name="Current", value=f"{current}/{limit}", inline=True
                        )
                        embed.add_field(
                            name="Type", value=emoji_type.capitalize(), inline=True
                        )
                    else:
                        embed = discord.Embed(
                            title="❌ Steal Failed",
                            description=error_msgs.get(result, f"Error: {result}"),
                            color=discord.Color.red(),
                        )

                    msg = await ctx.send(embed=embed)
                    await asyncio.sleep(5)
                    await msg.delete()
            else:
                embed = discord.Embed(
                    title="❌ Invalid Emoji",
                    description="Please provide a valid custom emoji like `:pepe:` or `<:pepe:123456789>`",
                    color=discord.Color.red(),
                )
                msg = await ctx.send(embed=embed)
                await asyncio.sleep(5)
                await msg.delete()
            return

        # Case 2: User replied to a message
        if ctx.message.reference and ctx.message.reference.message_id:
            try:
                replied_message = await ctx.channel.fetch_message(
                    ctx.message.reference.message_id
                )
                emojis = self.extract_emojis_from_message(replied_message)

                if not emojis:
                    embed = discord.Embed(
                        title="❌ No Emojis Found",
                        description="The replied message doesn't contain any custom emojis.",
                        color=discord.Color.orange(),
                    )
                    msg = await ctx.send(embed=embed)
                    await asyncio.sleep(5)
                    await msg.delete()
                    return

                # Check which emojis are new and have space
                existing_names, existing_ids = self.get_server_emojis(ctx.guild)

                # Track available slots dynamically
                static_available = static_limit - static_count
                animated_available = animated_limit - animated_count

                new_emojis = []
                already_have = []
                name_conflicts = []
                no_space_static = []
                no_space_animated = []

                for name, emoji_id, is_animated in emojis:
                    if emoji_id in existing_ids:
                        already_have.append(f":{name}:")
                    elif name.lower() in existing_names:
                        name_conflicts.append(f":{name}:")
                    else:
                        # Check if we have space for this type
                        if is_animated:
                            if animated_available > 0:
                                new_emojis.append((name, emoji_id, is_animated))
                                animated_available -= 1
                            else:
                                no_space_animated.append(f":{name}:")
                        else:
                            if static_available > 0:
                                new_emojis.append((name, emoji_id, is_animated))
                                static_available -= 1
                            else:
                                no_space_static.append(f":{name}:")

                if not new_emojis:
                    embed = discord.Embed(
                        title="😴 Nothing to Steal",
                        description="No new emojis can be added right now!",
                        color=discord.Color.orange(),
                    )

                    if already_have:
                        embed.add_field(
                            name="Already Have",
                            value=", ".join(already_have[:10]),
                            inline=False,
                        )
                    if name_conflicts:
                        embed.add_field(
                            name="Name Conflicts",
                            value=", ".join(name_conflicts[:5]),
                            inline=False,
                        )
                    if no_space_static:
                        embed.add_field(
                            name="No Static Slots",
                            value=", ".join(no_space_static[:5]),
                            inline=False,
                        )
                    if no_space_animated:
                        embed.add_field(
                            name="No Animated Slots",
                            value=", ".join(no_space_animated[:5]),
                            inline=False,
                        )

                    # Show current usage
                    usage = f"Static: {static_count}/{static_limit}\nAnimated: {animated_count}/{animated_limit}"
                    embed.add_field(name="📊 Current Usage", value=usage, inline=False)

                    msg = await ctx.send(embed=embed)
                    await asyncio.sleep(8)
                    await msg.delete()
                    return

                # Steal the emojis
                progress_embed = discord.Embed(
                    title="🔄 Stealing Emojis...",
                    description=f"Found {len(new_emojis)} new emoji(s) to steal!",
                    color=discord.Color.orange(),
                )

                # Show current usage
                usage = f"Static: {static_count}/{static_limit}\nAnimated: {animated_count}/{animated_limit}"
                progress_embed.add_field(name="📊 Before", value=usage, inline=False)

                progress_msg = await ctx.send(embed=progress_embed)

                successful = []
                failed = []
                failed_reasons = []

                for i, (name, emoji_id, is_animated) in enumerate(new_emojis):
                    # Update progress
                    progress_embed.description = (
                        f"Stealing emoji {i+1}/{len(new_emojis)}: `:{name}:`"
                    )
                    await progress_msg.edit(embed=progress_embed)

                    # Steal it
                    success, result = await self.steal_single_emoji(
                        ctx, name, emoji_id, is_animated
                    )

                    if success:
                        successful.append(result)
                    else:
                        failed.append(name)
                        failed_reasons.append(result)

                    await asyncio.sleep(0.5)  # Small delay between steals

                # Final result
                if successful:
                    # Get updated counts
                    new_static, new_animated = self.get_emoji_counts(ctx.guild)

                    result_embed = discord.Embed(
                        title="✅ Stealing Complete!",
                        description=f"Successfully stole {len(successful)} emoji(s)!",
                        color=discord.Color.green(),
                    )

                    # Show stolen emojis
                    emoji_list = " ".join(str(e) for e in successful[:10])
                    result_embed.add_field(
                        name="Stolen Emojis", value=emoji_list, inline=False
                    )

                    # Show updated usage
                    new_usage = f"Static: {new_static}/{static_limit}\nAnimated: {new_animated}/{animated_limit}"
                    result_embed.add_field(
                        name="📊 After", value=new_usage, inline=True
                    )

                    if failed:
                        # Group failed reasons
                        reason_counts = {}
                        for reason in failed_reasons:
                            if isinstance(reason, str) and reason.startswith(
                                "no_space|"
                            ):
                                reason_counts["No Space"] = (
                                    reason_counts.get("No Space", 0) + 1
                                )
                            else:
                                reason_counts[reason] = reason_counts.get(reason, 0) + 1

                        reason_text = ", ".join(
                            [f"{k}: {v}" for k, v in reason_counts.items()]
                        )
                        result_embed.add_field(
                            name="Failed", value=reason_text, inline=True
                        )

                    if already_have:
                        result_embed.add_field(
                            name="Already Had",
                            value=str(len(already_have)),
                            inline=True,
                        )

                    if name_conflicts:
                        result_embed.add_field(
                            name="Name Conflicts",
                            value=str(len(name_conflicts)),
                            inline=True,
                        )

                    if no_space_static or no_space_animated:
                        no_space_total = len(no_space_static) + len(no_space_animated)
                        result_embed.add_field(
                            name="No Space", value=str(no_space_total), inline=True
                        )

                    result_embed.set_footer(
                        text=f"Stolen from {replied_message.author.display_name}'s message"
                    )

                    await progress_msg.edit(embed=result_embed)

                    # Delete after 15 seconds
                    await asyncio.sleep(15)
                    await progress_msg.delete()
                else:
                    embed = discord.Embed(
                        title="❌ Steal Failed",
                        description="Couldn't steal any emojis!",
                        color=discord.Color.red(),
                    )

                    # Show why
                    if failed_reasons:
                        reason_counts = {}
                        for reason in failed_reasons:
                            if isinstance(reason, str) and reason.startswith(
                                "no_space|"
                            ):
                                parts = reason.split("|")
                                emoji_type = parts[1]
                                reason_counts[f"No {emoji_type} space"] = (
                                    reason_counts.get(f"No {emoji_type} space", 0) + 1
                                )
                            else:
                                reason_counts[reason] = reason_counts.get(reason, 0) + 1

                        reason_text = "\n".join(
                            [f"• {k}: {v}" for k, v in reason_counts.items()]
                        )
                        embed.add_field(name="Reasons", value=reason_text, inline=False)

                    await progress_msg.edit(embed=embed)
                    await asyncio.sleep(5)
                    await progress_msg.delete()

            except discord.NotFound:
                embed = discord.Embed(
                    title="❌ Message Not Found",
                    description="The replied message was not found.",
                    color=discord.Color.red(),
                )
                msg = await ctx.send(embed=embed)
                await asyncio.sleep(5)
                await msg.delete()
                return
            except Exception as e:
                embed = discord.Embed(
                    title="❌ Error",
                    description=f"An error occurred: {str(e)[:100]}",
                    color=discord.Color.red(),
                )
                msg = await ctx.send(embed=embed)
                await asyncio.sleep(5)
                await msg.delete()
                return

        # Case 3: No input and no reply - show help
        else:
            embed = discord.Embed(
                title="🔄 Steal Emoji Help",
                description="**Reply to any message with `.steal` to steal ALL NEW emojis from it!**",
                color=discord.Color.orange(),
            )
            embed.add_field(
                name="📝 How to Use",
                value="1. Find a message with cool emojis\n"
                "2. Reply to it with `.steal`\n"
                "3. Watch me automatically add ONLY emojis not already on the server!",
                inline=False,
            )
            embed.add_field(
                name="✨ Features",
                value="• **Smart detection** - Only steals emojis you don't already have\n"
                "• **Name conflict protection** - Won't overwrite existing emoji names\n"
                "• **Bulk steal** - Get all new emojis from a message at once\n"
                "• **Rate limited** - 5 steals per minute (anti-spam)",
                inline=False,
            )

            # Show current server limits
            usage = f"Static: {static_count}/{static_limit}\nAnimated: {animated_count}/{animated_limit}"
            embed.add_field(name="📊 Your Server Stats", value=usage, inline=False)

            embed.add_field(
                name="📋 Examples",
                value="`.steal` - (reply to a message)\n"
                "`.steal :pepe:` - Steal a specific emoji",
                inline=False,
            )
            embed.add_field(
                name="⚠️ Note",
                value="You need **Manage Emojis** permission to use this command!",
                inline=False,
            )
            embed.set_footer(text="Only steals emojis not already on your server!")

            await ctx.send(embed=embed)

    @steal_emoji.error
    async def steal_emoji_error(self, ctx: commands.Context, error):
        """Error handler for steal command."""
        if isinstance(error, commands.MissingPermissions):
            embed = discord.Embed(
                title="❌ Permission Denied",
                description="You need the **Manage Emojis** permission to steal emojis.",
                color=discord.Color.red(),
            )
            msg = await ctx.send(embed=embed)
            await asyncio.sleep(5)
            await msg.delete()
        elif isinstance(error, commands.BotMissingPermissions):
            embed = discord.Embed(
                title="❌ Bot Missing Permissions",
                description="I need the **Manage Emojis** permission to steal emojis.",
                color=discord.Color.red(),
            )
            msg = await ctx.send(embed=embed)
            await asyncio.sleep(5)
            await msg.delete()
        elif isinstance(error, commands.MissingRequiredArgument):
            # Show help for missing argument
            static_count, animated_count = self.get_emoji_counts(ctx.guild)
            static_limit, animated_limit = self.get_emoji_limits(ctx.guild)

            embed = discord.Embed(
                title="🔄 Steal Emoji Help",
                description="Reply to a message with `.steal` to steal emojis!",
                color=discord.Color.orange(),
            )
            usage = f"Static: {static_count}/{static_limit}\nAnimated: {animated_count}/{animated_limit}"
            embed.add_field(name="📊 Your Server Stats", value=usage, inline=False)
            await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(StealEmoji(bot))
