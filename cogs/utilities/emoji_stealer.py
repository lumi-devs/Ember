import discord
from discord.ext import commands
import asyncio
import re
from typing import List, Optional, Tuple
import aiohttp

class StealEmoji(commands.Cog):
    """Steal emojis from other servers with proper permissions and limits."""
    
    def __init__(self, bot):
        self.bot = bot
        self.emoji_regex = re.compile(r'<a?:(\w+):(\d+)>')
        self.recent_steals = {}  # user_id: [timestamps] for rate limiting
    
    async def cog_check(self, ctx):
        """Check if user has manage_emojis permission."""
        if not ctx.author.guild_permissions.manage_emojis:
            embed = discord.Embed(
                title="❌ Permission Denied",
                description="You need the **Manage Emojis** permission to use this command.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed, delete_after=10)
            return False
        return True
    
    def get_emoji_urls(self, emoji_id: str, animated: bool) -> List[str]:
        """Get multiple possible CDN URLs for an emoji."""
        base_urls = []
        extension = "gif" if animated else "png"
        
        # Try different CDN URL formats
        base_urls.append(f"https://cdn.discordapp.com/emojis/{emoji_id}.{extension}")
        base_urls.append(f"https://cdn.discordapp.com/emojis/{emoji_id}.{extension}?quality=lossless")
        base_urls.append(f"https://cdn.discordapp.com/emojis/{emoji_id}.{extension}?v=1")
        
        # Also try the other extension in case detection is wrong
        alt_extension = "png" if animated else "gif"
        base_urls.append(f"https://cdn.discordapp.com/emojis/{emoji_id}.{alt_extension}")
        
        return base_urls
    
    async def download_emoji(self, emoji_id: str, animated: bool) -> Optional[bytes]:
        """Download emoji image data directly from CDN with multiple fallback URLs."""
        urls = self.get_emoji_urls(emoji_id, animated)
        
        for url in urls:
            try:
                print(f"Trying to download emoji from: {url}")
                async with aiohttp.ClientSession() as session:
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            data = await resp.read()
                            # Basic validation - ensure we got actual image data
                            if len(data) > 100:  # Minimum reasonable file size
                                print(f"Successfully downloaded emoji {emoji_id} from {url}")
                                return data
                            else:
                                print(f"Downloaded data too small: {len(data)} bytes")
                        else:
                            print(f"HTTP {resp.status} for URL: {url}")
            except Exception as e:
                print(f"Error downloading from {url}: {e}")
                continue
        
        print(f"Failed to download emoji {emoji_id} from all URLs")
        return None
    
    def check_rate_limit(self, user_id: int) -> bool:
        """Check if user is rate limited (5 steals per minute)."""
        now = discord.utils.utcnow().timestamp()
        if user_id not in self.recent_steals:
            self.recent_steals[user_id] = []
        
        # Clean old timestamps (older than 1 minute)
        self.recent_steals[user_id] = [ts for ts in self.recent_steals[user_id] if now - ts < 60]
        
        # Check if user has exceeded limit (5 per minute)
        if len(self.recent_steals[user_id]) >= 5:
            return False
        
        self.recent_steals[user_id].append(now)
        return True
    
    async def steal_single_emoji(self, ctx, emoji_input: str) -> bool:
        """Steal a single emoji from input."""
        try:
            # Try to parse as custom emoji
            match = self.emoji_regex.match(emoji_input)
            if not match:
                embed = discord.Embed(
                    title="❌ Invalid Emoji",
                    description="Please provide a valid custom emoji.",
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed, delete_after=10)
                return False
            
            emoji_name = match.group(1)
            emoji_id = match.group(2)
            
            # Check if it's animated
            is_animated = emoji_input.startswith('<a:')
            
            print(f"Attempting to steal emoji: {emoji_name} (ID: {emoji_id}, Animated: {is_animated})")
            
            # Check guild emoji limits
            if len(ctx.guild.emojis) >= ctx.guild.emoji_limit:
                embed = discord.Embed(
                    title="❌ Emoji Limit Reached",
                    description=f"This server has reached the emoji limit ({ctx.guild.emoji_limit}).",
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed, delete_after=10)
                return False
            
            # Check if emoji name already exists in this server
            existing_emoji = discord.utils.get(ctx.guild.emojis, name=emoji_name)
            if existing_emoji:
                embed = discord.Embed(
                    title="❌ Name Already Exists",
                    description=f"An emoji named `:{emoji_name}:` already exists in this server.",
                    color=discord.Color.orange()
                )
                await ctx.send(embed=embed, delete_after=10)
                return False
            
            # Download emoji data directly from CDN
            emoji_data = await self.download_emoji(emoji_id, is_animated)
            if not emoji_data:
                # Try the opposite animation type as fallback
                print(f"Trying opposite animation type for emoji {emoji_id}")
                emoji_data = await self.download_emoji(emoji_id, not is_animated)
                
            if not emoji_data:
                embed = discord.Embed(
                    title="❌ Download Failed",
                    description=f"Failed to download emoji `:{emoji_name}:`. The emoji may have been deleted or is from a private server.",
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed, delete_after=10)
                return False
            
            # Check file size limit (256KB for static, 1MB for animated)
            max_size = 1024 * 1024 if is_animated else 256 * 1024
            if len(emoji_data) > max_size:
                embed = discord.Embed(
                    title="❌ File Too Large",
                    description=f"Emoji file is too large ({len(emoji_data)} bytes).\nMax: {max_size} bytes.",
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed, delete_after=10)
                return False
            
            # Create the emoji
            try:
                new_emoji = await ctx.guild.create_custom_emoji(
                    name=emoji_name,
                    image=emoji_data,
                    reason=f"Stolen by {ctx.author} ({ctx.author.id})"
                )
                
                # Send success message
                emoji_type = "animated" if is_animated else "static"
                embed = discord.Embed(
                    title="✅ Emoji Stolen!",
                    description=f"Successfully added {new_emoji} as `:{new_emoji.name}:`",
                    color=discord.Color.green()
                )
                embed.add_field(name="Type", value=emoji_type, inline=True)
                embed.add_field(name="Name", value=new_emoji.name, inline=True)
                embed.add_field(name="ID", value=new_emoji.id, inline=True)
                embed.set_footer(text=f"Stolen by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
                
                await ctx.send(embed=embed)
                print(f"Successfully created emoji: {new_emoji.name} (ID: {new_emoji.id})")
                return True
                
            except discord.HTTPException as e:
                error_msg = str(e).lower()
                if "emoji limit" in error_msg:
                    embed = discord.Embed(
                        title="❌ Emoji Limit Reached",
                        description=f"This server has reached the emoji limit ({ctx.guild.emoji_limit}).",
                        color=discord.Color.red()
                    )
                    await ctx.send(embed=embed, delete_after=10)
                elif "invalid image" in error_msg:
                    embed = discord.Embed(
                        title="❌ Invalid Image",
                        description="Invalid emoji image format. This might be an unsupported emoji type.",
                        color=discord.Color.red()
                    )
                    await ctx.send(embed=embed, delete_after=10)
                elif "name" in error_msg and "already" in error_msg:
                    embed = discord.Embed(
                        title="❌ Name Already Exists",
                        description="An emoji with that name already exists in this server.",
                        color=discord.Color.red()
                    )
                    await ctx.send(embed=embed, delete_after=10)
                else:
                    embed = discord.Embed(
                        title="❌ Creation Failed",
                        description=f"Failed to create emoji: {e}",
                        color=discord.Color.red()
                    )
                    await ctx.send(embed=embed, delete_after=10)
                return False
                
        except Exception as e:
            print(f"Unexpected error in steal_single_emoji: {e}")
            embed = discord.Embed(
                title="❌ Unexpected Error",
                description=f"An unexpected error occurred: {e}",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed, delete_after=10)
            return False
    
    def extract_emojis_from_message(self, message: discord.Message) -> List[Tuple[str, str, bool]]:
        """Extract all custom emojis from a message with their names, IDs, and animation status."""
        # Find all emoji matches in the message
        matches = re.findall(r'<(a?):(\w+):(\d+)>', message.content)
        emojis = []
        for animation_flag, name, emoji_id in matches:
            is_animated = bool(animation_flag)  # "a" for animated, empty for static
            emojis.append((name, emoji_id, is_animated))
        return emojis
    
    @commands.command(name='steal', aliases=['stealemoji', 'addemoji'])
    async def steal_emoji(self, ctx, *, emoji_input: Optional[str] = None):
        """Steal emojis from other servers.
        
        Usage:
        `.steal <emoji>` - Steal a single emoji
        `.steal` (reply to message) - Steal all emojis from replied message
        `.steal` (no reply) - Shows help
        """
        # Check rate limit
        if not self.check_rate_limit(ctx.author.id):
            embed = discord.Embed(
                title="⏰ Rate Limited",
                description="You can only steal 5 emojis per minute. Please wait a bit.",
                color=discord.Color.orange()
            )
            await ctx.send(embed=embed, delete_after=10)
            return
        
        # Case 1: User provided emoji input
        if emoji_input:
            # Check if it's a raw emoji ID (just numbers)
            if emoji_input.isdigit() and len(emoji_input) >= 17:
                # User provided just an emoji ID, try both animated and static
                embed = discord.Embed(
                    title="❌ Need Full Emoji Format",
                    description="Please provide the full emoji format like `<:name:123456789>` or just use the emoji directly.",
                    color=discord.Color.orange()
                )
                await ctx.send(embed=embed, delete_after=10)
                return
            
            success = await self.steal_single_emoji(ctx, emoji_input)
            return
        
        # Case 2: User replied to a message
        if ctx.message.reference and ctx.message.reference.message_id:
            try:
                replied_message = await ctx.channel.fetch_message(ctx.message.reference.message_id)
                emojis = self.extract_emojis_from_message(replied_message)
                
                if not emojis:
                    embed = discord.Embed(
                        title="❌ No Emojis Found",
                        description="The replied message doesn't contain any custom emojis.",
                        color=discord.Color.orange()
                    )
                    await ctx.send(embed=embed, delete_after=10)
                    return
                
                # Steal multiple emojis
                await self.steal_multiple_emojis(ctx, emojis, replied_message)
                return
                
            except discord.NotFound:
                embed = discord.Embed(
                    title="❌ Message Not Found",
                    description="The replied message was not found.",
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed, delete_after=10)
                return
            except discord.HTTPException as e:
                embed = discord.Embed(
                    title="❌ Error Fetching Message",
                    description=f"Could not fetch the replied message: {e}",
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed, delete_after=10)
                return
        
        # Case 3: No input and no reply - show help
        embed = discord.Embed(
            title="🔄 Steal Emoji Help",
            description="Steal emojis from other servers by downloading them directly from Discord's CDN.",
            color=discord.Color.blue()
        )
        embed.add_field(
            name="Single Emoji",
            value="`.steal <:emoji_name:123456789>`\nOr just use the emoji directly",
            inline=False
        )
        embed.add_field(
            name="From Reply",
            value="Reply to a message with `.steal`\nSteals all emojis from that message",
            inline=False
        )
        embed.add_field(
            name="Features",
            value="• Works with any emoji (no server access needed)\n• Handles animated emojis\n• Automatic duplicate prevention",
            inline=False
        )
        embed.add_field(
            name="Limits",
            value=f"• 5 steals per minute\n• Server limit: {ctx.guild.emoji_limit} emojis\n• File size limits apply",
            inline=False
        )
        embed.set_footer(text="Requires Manage Emojis permission")
        
        await ctx.send(embed=embed)
    
    async def steal_multiple_emojis(self, ctx, emojis: List[Tuple[str, str, bool]], source_message: discord.Message):
        """Steal multiple emojis from a list."""
        if not emojis:
            return
        
        # Remove duplicates while preserving order
        unique_emojis = []
        seen_ids = set()
        for name, emoji_id, animated in emojis:
            if emoji_id not in seen_ids:
                unique_emojis.append((name, emoji_id, animated))
                seen_ids.add(emoji_id)
        
        # Limit to 5 emojis per command
        if len(unique_emojis) > 5:
            embed = discord.Embed(
                title="⚠️ Too Many Emojis",
                description=f"Found {len(unique_emojis)} emojis. Only stealing the first 5.",
                color=discord.Color.orange()
            )
            await ctx.send(embed=embed, delete_after=10)
            unique_emojis = unique_emojis[:5]
        
        # Check if we have enough slots
        available_slots = ctx.guild.emoji_limit - len(ctx.guild.emojis)
        if available_slots < len(unique_emojis):
            embed = discord.Embed(
                title="❌ Not Enough Slots",
                description=f"Only {available_slots} emoji slots available, but {len(unique_emojis)} emojis requested.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed, delete_after=10)
            return
        
        # Start stealing process
        progress_embed = discord.Embed(
            title="🔄 Stealing Emojis...",
            description=f"Processing {len(unique_emojis)} emoji(s)...",
            color=discord.Color.blue()
        )
        progress_embed.add_field(name="Progress", value="Starting...", inline=True)
        progress_embed.add_field(name="Successful", value="0", inline=True)
        progress_embed.add_field(name="Failed", value="0", inline=True)
        
        progress_msg = await ctx.send(embed=progress_embed)
        
        successful = []
        failed = []
        
        for i, (emoji_name, emoji_id, animated) in enumerate(unique_emojis):
            try:
                # Update progress
                progress_embed.set_field_at(0, name="Progress", value=f"{i+1}/{len(unique_emojis)}", inline=True)
                await progress_msg.edit(embed=progress_embed)
                
                # Try to steal the emoji - use proper format with name and ID
                emoji_prefix = "a" if animated else ""
                emoji_input = f"<{emoji_prefix}:{emoji_name}:{emoji_id}>"
                success = await self.steal_single_emoji(ctx, emoji_input)
                
                if success:
                    successful.append(emoji_name)
                    progress_embed.set_field_at(1, name="Successful", value=str(len(successful)), inline=True)
                else:
                    failed.append(emoji_name)
                    progress_embed.set_field_at(2, name="Failed", value=str(len(failed)), inline=True)
                
                await progress_msg.edit(embed=progress_embed)
                await asyncio.sleep(0.5)  # Small delay between steals
                
            except Exception as e:
                print(f"Error stealing emoji {emoji_name}: {e}")
                failed.append(emoji_name)
                progress_embed.set_field_at(2, name="Failed", value=str(len(failed)), inline=True)
                await progress_msg.edit(embed=progress_embed)
        
        # Final result
        result_embed = discord.Embed(
            title="📊 Stealing Complete",
            color=discord.Color.green() if successful else discord.Color.red()
        )
        result_embed.add_field(name="✅ Successful", value=str(len(successful)), inline=True)
        result_embed.add_field(name="❌ Failed", value=str(len(failed)), inline=True)
        result_embed.add_field(name="📝 Source", value=f"[Jump to Message]({source_message.jump_url})", inline=True)
        
        if successful:
            result_embed.description = f"Successfully stole {len(successful)} emoji(s): {', '.join(successful)}"
        if failed:
            result_embed.description = f"Completed with {len(failed)} failure(s)"
        
        await progress_msg.edit(embed=result_embed)
    
    @steal_emoji.error
    async def steal_emoji_error(self, ctx, error):
        """Error handler for steal command."""
        if isinstance(error, commands.MissingPermissions):
            # Already handled by cog_check
            pass
        elif isinstance(error, commands.BotMissingPermissions):
            embed = discord.Embed(
                title="❌ Bot Missing Permissions",
                description="I need the **Manage Emojis** permission to steal emojis.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed, delete_after=10)
        else:
            embed = discord.Embed(
                title="❌ Unexpected Error",
                description=f"An error occurred: {error}",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed, delete_after=10)

async def setup(bot):
    await bot.add_cog(StealEmoji(bot))