import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any
import re
import base64
from io import BytesIO
from PIL import Image
import openai
import json


class NSFWDetector(commands.Cog):
    """NSFW and content moderation using OpenAI's Moderation API"""

    def __init__(self, bot):
        self.bot = bot
        self.session = aiohttp.ClientSession()
        self.openai_client = None

        # Your mod log channel ID - REPLACE THIS WITH YOUR ACTUAL CHANNEL ID
        self.mod_log_channel_id = 1413074803421614100  # Replace with your actual channel ID

        # Initialize OpenAI client if API key is provided
        self.openai_api_key = "***OPENAI_KEY_REMOVED***"  # Replace with your key
        if self.openai_api_key and self.openai_api_key != "YOUR_OPENAI_API_KEY":
            self.openai_client = openai.OpenAI(api_key=self.openai_api_key)

        # Cache system
        self.checked_urls = {}
        self.checked_content = {}
        self.cache_duration = 3600  # 1 hour

        # Thresholds for different content types
        self.thresholds = {
            'sexual': 0.7,
            'sexual/minors': 0.5,
            'violence': 0.7,
            'violence/graphic': 0.8,
            'harassment': 0.6,
            'harassment/threatening': 0.7,
            'hate': 0.6,
            'hate/threatening': 0.7,
            'self-harm': 0.5,
            'self-harm/intent': 0.6,
            'self-harm/instructions': 0.7,
            'illicit': 0.6,
            'illicit/violent': 0.7
        }

        # Configure which content types to monitor
        self.monitored_categories = [
            'sexual',
            'sexual/minors',
            'violence',
            'violence/graphic',
            'harassment',
            'harassment/threatening',
            'hate',
            'hate/threatening',
            'self-harm',
            'self-harm/intent',
            'self-harm/instructions',
            'illicit',
            'illicit/violent'
        ]

    async def cog_unload(self):
        await self.session.close()

    async def check_content(self, text: Optional[str] = None,
                            image_url: Optional[str] = None,
                            image_base64: Optional[str] = None) -> Dict:
        """Check content using OpenAI Moderation API"""

        if not self.openai_client:
            return {
                'error': 'OpenAI API not configured',
                'flagged': False,
                'categories': {}
            }

        try:
            # Prepare inputs for OpenAI API
            inputs = []

            if text:
                inputs.append({
                    "type": "text",
                    "text": text
                })

            if image_url:
                inputs.append({
                    "type": "image_url",
                    "image_url": {
                        "url": image_url
                    }
                })

            if image_base64:
                inputs.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{image_base64}"
                    }
                })

            if not inputs:
                return {
                    'error': 'No content provided',
                    'flagged': False,
                    'categories': {}
                }

            # Call OpenAI Moderation API
            response = self.openai_client.moderations.create(
                model="omni-moderation-latest",
                input=inputs
            )

            # Process the response
            if response.results:
                result = response.results[0]

                # Extract category scores
                categories = {}
                flagged_categories = []

                for category, score in result.category_scores.items():
                    if category in self.monitored_categories:
                        categories[category] = {
                            'score': score,
                            'flagged': score >= self.thresholds.get(category, 0.7)
                        }
                        if categories[category]['flagged']:
                            flagged_categories.append(category)

                # Determine overall flagged status
                overall_flagged = result.flagged or len(flagged_categories) > 0

                return {
                    'flagged': overall_flagged,
                    'categories': categories,
                    'flagged_categories': flagged_categories,
                    'model': response.model,
                    'raw_response': result,
                    'error': None
                }

            return {
                'error': 'No results from moderation API',
                'flagged': False,
                'categories': {}
            }

        except Exception as e:
            print(f"OpenAI Moderation error: {e}")
            return {
                'error': str(e),
                'flagged': False,
                'categories': {}
            }

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Automatically check messages for inappropriate content"""
        if message.author.bot or not self.openai_client:
            return

        # Don't check messages in mod log channel
        if message.channel.id == self.mod_log_channel_id:
            return

        # Check text content
        text_content = None
        if message.content:
            text_content = message.content

        # Check attachments
        image_urls = []
        for attachment in message.attachments:
            if attachment.filename.lower().endswith(('png', 'jpg', 'jpeg', 'gif', 'webp')):
                image_urls.append(attachment.url)

        # Check for image URLs in message
        text_urls = self.extract_image_urls(message.content)
        image_urls.extend(text_urls)

        # If we have content to check
        if text_content or image_urls:
            result = await self.check_message_content(message, text_content, image_urls)

            if result and result.get('flagged', False):
                await self.handle_content_violation(message, result)

    def extract_image_urls(self, text: str) -> List[str]:
        """Extract image URLs from text"""
        if not text:
            return []

        url_pattern = r'https?://[^\s]+\.(?:png|jpg|jpeg|gif|webp|bmp)(?:\?[^\s]*)?'
        return re.findall(url_pattern, text, re.IGNORECASE)

    async def check_message_content(self, message: discord.Message,
                                    text: Optional[str],
                                    image_urls: List[str]) -> Optional[Dict]:
        """Check message content with caching"""

        # Create a cache key
        cache_key = f"{message.author.id}:{hash(text or '')}:{hash(str(image_urls))}"

        # Check cache
        if cache_key in self.checked_content:
            cached = self.checked_content[cache_key]
            if (datetime.now() - cached['timestamp']).seconds < self.cache_duration:
                return cached['result']

        # Check text first
        result = None
        if text:
            result = await self.check_content(text=text)

        # Check images if text wasn't flagged or doesn't exist
        if (not result or not result.get('flagged')) and image_urls:
            for image_url in image_urls[:3]:  # Limit to 3 images to avoid rate limits
                image_result = await self.check_content(image_url=image_url)

                if image_result and image_result.get('flagged'):
                    result = image_result
                    break

        # Cache the result
        if result:
            self.checked_content[cache_key] = {
                'result': result,
                'timestamp': datetime.now()
            }

        return result

    async def handle_content_violation(self, message: discord.Message, result: Dict):
        """Handle detected content violations"""
        try:
            # Delete the message
            await message.delete()

            # Get flagged categories
            flagged_cats = result.get('flagged_categories', [])

            # Create warning message
            warning_msg = (
                f"{message.author.mention}, your message was removed for violating content policies.\n"
                f"**Violations detected:** {', '.join(flagged_cats)}\n"
                f"Please review the server rules."
            )

            # Try to send DM warning
            try:
                warning_embed = discord.Embed(
                    title="⚠️ Message Removed",
                    description="Your message was automatically removed by the content moderation system.",
                    color=discord.Color.orange(),
                    timestamp=datetime.utcnow()
                )
                warning_embed.add_field(name="Channel", value=message.channel.mention, inline=False)
                warning_embed.add_field(name="Violations", value="\n".join(
                    flagged_cats) if flagged_cats else "Multiple content violations", inline=False)
                warning_embed.set_footer(text=f"User ID: {message.author.id}")

                await message.author.send(embed=warning_embed)
            except discord.Forbidden:
                # Can't DM user, send ephemeral message instead
                pass

            # Log the violation
            await self.log_content_violation(message, result)

        except discord.Forbidden:
            print(f"No permission to delete message in {message.guild.name}")
        except Exception as e:
            print(f"Error handling content violation: {e}")

    async def get_mod_log_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        """Get the moderation log channel for the guild using configured ID"""
        try:
            channel = guild.get_channel(self.mod_log_channel_id)
            if channel:
                return channel

            # Try to fetch if not in cache
            channel = await guild.fetch_channel(self.mod_log_channel_id)
            return channel

        except discord.NotFound:
            print(f"Mod log channel not found: {self.mod_log_channel_id}")
            return None
        except discord.Forbidden:
            print(f"No permission to access mod log channel: {self.mod_log_channel_id}")
            return None
        except Exception as e:
            print(f"Error getting mod log channel: {e}")
            return None

    async def log_content_violation(self, message: discord.Message, result: Dict):
        """Log content violations to mod log channel"""
        log_channel = await self.get_mod_log_channel(message.guild)
        if not log_channel:
            # Try alternative: find any channel with "log" in name
            for channel in message.guild.text_channels:
                if "log" in channel.name.lower():
                    log_channel = channel
                    break

            if not log_channel:
                return

        flagged_cats = result.get('flagged_categories', [])

        # Create detailed embed
        embed = discord.Embed(
            title="🚨 Content Violation Detected",
            color=discord.Color.red(),
            timestamp=datetime.utcnow()
        )

        # Add user information
        embed.add_field(
            name="User",
            value=f"{message.author.mention}\n`{message.author}`\nID: `{message.author.id}`",
            inline=True
        )

        # Add channel information
        embed.add_field(
            name="Channel",
            value=f"{message.channel.mention}\n`{message.channel.name}`\nID: `{message.channel.id}`",
            inline=True
        )

        # Add message context (first 100 chars)
        if message.content:
            preview = message.content[:97] + "..." if len(message.content) > 100 else message.content
            embed.add_field(name="Message Preview", value=f"```{preview}```", inline=False)

        # Add category scores
        categories = result.get('categories', {})
        if categories and flagged_cats:
            cat_details = []
            for cat in flagged_cats:
                if cat in categories:
                    score = categories[cat].get('score', 0)
                    cat_details.append(f"**{cat}**: {score:.1%}")

            if cat_details:
                embed.add_field(name="Flagged Categories", value="\n".join(cat_details), inline=False)

        # Add attachments info
        if message.attachments:
            attachment_info = []
            for i, attachment in enumerate(message.attachments[:3]):  # Show first 3
                attachment_info.append(f"[Attachment {i + 1}]({attachment.url})")
            if attachment_info:
                embed.add_field(name="Attachments", value="\n".join(attachment_info), inline=False)

        # Add action taken
        embed.add_field(name="Action Taken", value="✅ Message Deleted\n⚠️ User Warned", inline=True)

        # Add model info
        embed.add_field(name="Detection Model", value=result.get('model', 'Unknown'), inline=True)

        # Set author and thumbnail
        embed.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)
        embed.set_thumbnail(url=message.author.display_avatar.url)
        embed.set_footer(text=f"Message ID: {message.id}")

        try:
            await log_channel.send(embed=embed)
        except Exception as e:
            print(f"Error sending mod log: {e}")

    @commands.command(name="moderate", aliases=["check"])
    @commands.has_permissions(manage_messages=True)
    async def moderate_command(self, ctx, *, text: Optional[str] = None):
        """Manually check text or image for content violations"""
        if not text and not ctx.message.attachments:
            await ctx.send(
                "❌ Please provide text or an image to check.\nExample: `!moderate This is some text` or attach an image.")
            return

        await ctx.trigger_typing()

        # Check text or first attachment
        if text:
            result = await self.check_content(text=text)
        else:
            attachment = ctx.message.attachments[0]
            if attachment.filename.lower().endswith(('png', 'jpg', 'jpeg', 'gif', 'webp')):
                result = await self.check_content(image_url=attachment.url)
            else:
                await ctx.send("❌ Please provide an image file (PNG, JPG, GIF, WEBP).")
                return

        if result.get('error'):
            await ctx.send(f"❌ Error: {result['error']}")
            return

        await self.send_moderation_result(ctx, result)

    async def send_moderation_result(self, ctx, result: Dict):
        """Send moderation results as an embed"""
        flagged = result.get('flagged', False)
        categories = result.get('categories', {})
        flagged_cats = result.get('flagged_categories', [])

        embed = discord.Embed(
            title="🔍 Moderation Analysis Results",
            color=discord.Color.red() if flagged else discord.Color.green(),
            timestamp=datetime.utcnow()
        )

        # Overall status
        status_value = "🚨 **CONTENT FLAGGED**" if flagged else "✅ **CONTENT CLEAN**"
        if flagged_cats:
            status_value += f"\n\n**Violations:** {', '.join(flagged_cats)}"

        embed.add_field(name="Status", value=status_value, inline=False)

        # Add all category scores
        if categories:
            # Split categories into rows for better display
            all_cats = list(categories.items())
            rows = [all_cats[i:i + 4] for i in range(0, len(all_cats), 4)]

            for row in rows:
                row_text = []
                for cat, data in row:
                    score = data.get('score', 0)
                    is_flagged = data.get('flagged', False)
                    emoji = "🚩" if is_flagged else "✅"
                    row_text.append(f"{emoji} **{cat.split('/')[-1]}**: {score:.1%}")

                embed.add_field(name="\u200b", value="\n".join(row_text), inline=False)

        # Add model info
        embed.set_footer(text=f"Model: {result.get('model', 'Unknown')} | Requested by {ctx.author}")

        await ctx.send(embed=embed)

    @commands.command(name="scan_message", aliases=["scan", "checkmessage"])
    @commands.has_permissions(manage_messages=True)
    async def scan_message_command(self, ctx, message_id: str, channel: Optional[discord.TextChannel] = None):
        """Scan a specific message for content violations

        Usage:
        !scan_message 123456789012345678
        !scan_message 123456789012345678 #general
        !scan 123456789012345678
        """

        # Send typing indicator
        await ctx.trigger_typing()

        # Use provided channel or current channel
        target_channel = channel or ctx.channel

        try:
            message = await target_channel.fetch_message(int(message_id))
        except discord.NotFound:
            await ctx.send("❌ Message not found. Make sure the ID is correct and I have access to that channel.")
            return
        except discord.Forbidden:
            await ctx.send("❌ I don't have permission to read messages in that channel.")
            return
        except ValueError:
            await ctx.send("❌ Invalid message ID. Please provide a valid numeric ID.")
            return
        except Exception as e:
            await ctx.send(f"❌ Error fetching message: {str(e)}")
            return

        # Check the message
        text_content = message.content
        image_urls = []

        for attachment in message.attachments:
            if attachment.filename.lower().endswith(('png', 'jpg', 'jpeg', 'gif', 'webp')):
                image_urls.append(attachment.url)

        image_urls.extend(self.extract_image_urls(text_content))

        result = await self.check_message_content(message, text_content, image_urls[:3])

        if result.get('error'):
            await ctx.send(f"❌ Error: {result['error']}")
            return

        # Create result embed
        flagged = result.get('flagged', False)
        flagged_cats = result.get('flagged_categories', [])

        embed = discord.Embed(
            title="🔍 Message Scan Results",
            description=f"Scanning message from {message.author.mention}",
            color=discord.Color.red() if flagged else discord.Color.green(),
            timestamp=datetime.utcnow()
        )

        embed.add_field(name="Message ID", value=f"`{message.id}`", inline=True)
        embed.add_field(name="Channel", value=target_channel.mention, inline=True)
        embed.add_field(name="Author", value=message.author.mention, inline=True)

        embed.add_field(name="Overall Status", value="🚨 **FLAGGED**" if flagged else "✅ **CLEAN**", inline=False)

        if flagged_cats:
            embed.add_field(name="Violations", value="\n".join(flagged_cats), inline=False)

        # Add detailed category scores
        categories = result.get('categories', {})
        if categories:
            score_text = []
            for cat, data in sorted(categories.items(), key=lambda x: x[1].get('score', 0), reverse=True)[:5]:
                score = data.get('score', 0)
                is_flagged = data.get('flagged', False)
                emoji = "🚩" if is_flagged else "📊"
                score_text.append(f"{emoji} **{cat}**: {score:.1%}")

            embed.add_field(name="Category Scores", value="\n".join(score_text), inline=False)

        # Add message preview
        if message.content:
            preview = message.content[:200] + "..." if len(message.content) > 200 else message.content
            embed.add_field(name="Message Content", value=f"```{preview}```", inline=False)

        # Add attachments info
        if image_urls:
            embed.add_field(name="Attachments", value=f"Found {len(image_urls)} image(s)", inline=True)

        embed.set_footer(text=f"Scan requested by {ctx.author} | Model: {result.get('model', 'Unknown')}")

        await ctx.send(embed=embed)

    # If you still want the slash command version, keep it separate
    @app_commands.command(name="scan_message_slash",
                          description="Scan a specific message for content violations (Slash Command)")
    @app_commands.describe(
        message_id="The ID of the message to scan",
        channel="The channel where the message is (optional)"
    )
    @app_commands.checks.has_permissions(manage_messages=True)
    async def scan_message_slash(self, interaction: discord.Interaction,
                                 message_id: str,
                                 channel: Optional[discord.TextChannel] = None):
        """Slash command version - same functionality"""
        await interaction.response.defer()

        # Use provided channel or current channel
        target_channel = channel or interaction.channel

        try:
            message = await target_channel.fetch_message(int(message_id))
        except discord.NotFound:
            await interaction.followup.send(
                "❌ Message not found. Make sure the ID is correct and I have access to that channel.")
            return
        except discord.Forbidden:
            await interaction.followup.send("❌ I don't have permission to read messages in that channel.")
            return
        except ValueError:
            await interaction.followup.send("❌ Invalid message ID. Please provide a valid numeric ID.")
            return
        except Exception as e:
            await interaction.followup.send(f"❌ Error fetching message: {str(e)}")
            return


    @commands.command(name="thresholds", aliases=["settings", "config"])
    @commands.has_permissions(manage_messages=True)
    async def show_thresholds(self, ctx):
        """Show current threshold settings"""
        embed = discord.Embed(
            title="🔧 Content Moderation Configuration",
            color=discord.Color.blue(),
            description="Current threshold values for content detection\n*(Lower values = more sensitive)*"
        )

        # Group categories
        sexual_cats = {k: v for k, v in self.thresholds.items() if 'sexual' in k}
        violence_cats = {k: v for k, v in self.thresholds.items() if 'violence' in k}
        harm_cats = {k: v for k, v in self.thresholds.items() if 'harm' in k}
        other_cats = {k: v for k, v in self.thresholds.items() if
                      k not in sexual_cats and k not in violence_cats and k not in harm_cats}

        def add_category_group(name, categories_dict):
            if categories_dict:
                cat_text = []
                for cat, threshold in sorted(categories_dict.items()):
                    cat_text.append(f"**{cat}**: {threshold:.2f}")
                embed.add_field(name=name, value="\n".join(cat_text), inline=False)

        add_category_group("🔞 Sexual Content", sexual_cats)
        add_category_group("⚔️ Violence Content", violence_cats)
        add_category_group("🚫 Harm Content", harm_cats)
        add_category_group("⚠️ Other Categories", other_cats)

        embed.set_footer(text=f"Total categories: {len(self.thresholds)} | Use /set_threshold to modify")

        await ctx.send(embed=embed)

    @commands.command(name="checkstatus", aliases=["apistatus"])
    @commands.has_permissions(manage_messages=True)
    async def check_api_status(self, ctx):
        """Check if the OpenAI Moderation API is working"""
        await ctx.trigger_typing()

        if not self.openai_client:
            await ctx.send("❌ OpenAI API is not configured. Please set your API key.")
            return

        # Test with a simple message
        test_text = "This is a test message to check if the API is working."

        try:
            start_time = datetime.now()
            result = await self.check_content(text=test_text)
            end_time = datetime.now()

            response_time = (end_time - start_time).total_seconds()

            embed = discord.Embed(
                title="🔧 API Status Check",
                color=discord.Color.green() if not result.get('error') else discord.Color.red(),
                timestamp=datetime.utcnow()
            )

            if result.get('error'):
                embed.add_field(name="Status", value="❌ OFFLINE", inline=True)
                embed.add_field(name="Error", value=result['error'], inline=False)
            else:
                embed.add_field(name="Status", value="✅ ONLINE", inline=True)
                embed.add_field(name="Response Time", value=f"{response_time:.2f} seconds", inline=True)
                embed.add_field(name="Model", value=result.get('model', 'Unknown'), inline=True)
                embed.add_field(name="Cache Size", value=f"{len(self.checked_content)} items", inline=True)

            await ctx.send(embed=embed)

        except Exception as e:
            await ctx.send(f"❌ Error checking API status: {str(e)}")


async def setup(bot):
    await bot.add_cog(NSFWDetector(bot))