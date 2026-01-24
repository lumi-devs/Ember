import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiohttp
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any, Set
import re
import base64
from io import BytesIO
from PIL import Image
import openai
import json
import time
from collections import defaultdict


class ImageModerator(commands.Cog):
    """IMAGE-ONLY moderation using OpenAI's Moderation API with rate limiting"""

    def __init__(self, bot):
        self.bot = bot
        self.session = aiohttp.ClientSession()
        self.openai_client = None

        # Your mod log channel ID
        self.mod_log_channel_id = 123456789012345678

        # Initialize OpenAI client if API key is provided
        self.openai_api_key = "***OPENAI_KEY_REMOVED***"
        if self.openai_api_key and self.openai_api_key != "YOUR_OPENAI_API_KEY":
            self.openai_client = openai.OpenAI(api_key=self.openai_api_key)

        # ========== RATE LIMITING SYSTEM ==========
        self.rate_limit = {
            'max_per_minute': 5,  # Start conservative
            'max_per_hour': 50,
            'cooldown_seconds': 3  # 3 seconds between requests
        }

        # Track API calls
        self.api_calls = {
            'minute': [],
            'hour': [],
            'total_today': 0
        }

        # Track cooldowns
        self.last_request_time = 0
        self.user_cooldowns = {}  # user_id: timestamp

        # ========== CACHE SYSTEM ==========
        self.checked_urls = {}  # url: {result, timestamp}
        self.cache_duration = 3600  # 1 hour

        # ========== THRESHOLDS (IMAGE-ONLY) ==========
        self.thresholds = {
            'sexual': 0.6,
            'sexual/minors': 0.4,
            'violence': 0.7,
            'violence/graphic': 0.8,
            'self-harm': 0.5,
            'self-harm/intent': 0.6,
            'self-harm/instructions': 0.7,
            'illicit': 0.6,
            'illicit/violent': 0.7
        }

        # ========== OPTIMIZATION ==========
        self.settings = {
            'enabled': True,
            'max_images_per_message': 1,  # Check only FIRST image
            'skip_small_images': True,  # Skip < 10KB
            'skip_large_images': True,  # Skip > 10MB
            'check_attachments': True,  # Check file attachments
            'check_urls': True,  # Check image URLs in text
            'user_cooldown_minutes': 10,  # 10 min cooldown per user
            'skip_bots': True,
            'whitelisted_channels': [],
            'whitelisted_roles': [],
        }

        # Start cleanup task
        self.cleanup_task.start()

    async def cog_unload(self):
        self.cleanup_task.cancel()
        await self.session.close()

    @tasks.loop(minutes=5)
    async def cleanup_task(self):
        """Clean up old cache entries"""
        now = time.time()

        # Clean rate limit records
        self.api_calls['minute'] = [t for t in self.api_calls['minute'] if now - t < 60]
        self.api_calls['hour'] = [t for t in self.api_calls['hour'] if now - t < 3600]

        # Clean cache
        expired = []
        for url, data in list(self.checked_urls.items()):
            if now - data['timestamp'] > self.cache_duration:
                expired.append(url)
        for url in expired:
            del self.checked_urls[url]

        # Clean user cooldowns
        expired_users = [uid for uid, ts in self.user_cooldowns.items() if now - ts > 600]
        for uid in expired_users:
            del self.user_cooldowns[uid]

    def can_make_api_call(self) -> bool:
        """Check if we can make an API call"""
        now = time.time()

        # Check minute limit
        recent_minute = [t for t in self.api_calls['minute'] if now - t < 60]
        if len(recent_minute) >= self.rate_limit['max_per_minute']:
            return False

        # Check hour limit
        recent_hour = [t for t in self.api_calls['hour'] if now - t < 3600]
        if len(recent_hour) >= self.rate_limit['max_per_hour']:
            return False

        # Check cooldown
        if now - self.last_request_time < self.rate_limit['cooldown_seconds']:
            return False

        return True

    def record_api_call(self):
        """Record an API call"""
        now = time.time()
        self.api_calls['minute'].append(now)
        self.api_calls['hour'].append(now)
        self.api_calls['total_today'] += 1
        self.last_request_time = now

    def is_user_on_cooldown(self, user_id: int) -> bool:
        """Check if user is on cooldown"""
        if user_id in self.user_cooldowns:
            if time.time() - self.user_cooldowns[user_id] < self.settings['user_cooldown_minutes'] * 60:
                return True
        return False

    def put_user_on_cooldown(self, user_id: int):
        """Put user on cooldown"""
        self.user_cooldowns[user_id] = time.time()

    async def check_image_only(self, image_url: str) -> Dict:
        """Check ONLY images using OpenAI Moderation API"""

        if not self.openai_client:
            return {
                'error': 'OpenAI API not configured',
                'flagged': False,
                'categories': {}
            }

        # Check cache first
        if image_url in self.checked_urls:
            cached = self.checked_urls[image_url]
            if time.time() - cached['timestamp'] < self.cache_duration:
                return cached['result']

        # Check rate limits
        if not self.can_make_api_call():
            return {
                'error': 'Rate limit exceeded. Please try again later.',
                'flagged': False,
                'categories': {}
            }

        try:
            # Record API call
            self.record_api_call()

            # Call OpenAI Moderation API - IMAGE ONLY
            response = self.openai_client.moderations.create(
                model="omni-moderation-latest",
                input=[{
                    "type": "image_url",
                    "image_url": {"url": image_url}
                }]
            )

            # Process response
            if response.results:
                result = response.results[0]

                categories = {}
                flagged_categories = []

                for category, score in result.category_scores.items():
                    if category in self.thresholds:
                        categories[category] = {
                            'score': score,
                            'flagged': score >= self.thresholds[category]
                        }
                        if categories[category]['flagged']:
                            flagged_categories.append(category)

                overall_flagged = result.flagged or len(flagged_categories) > 0

                api_result = {
                    'flagged': overall_flagged,
                    'categories': categories,
                    'flagged_categories': flagged_categories,
                    'model': response.model,
                    'image_url': image_url,
                    'error': None,
                    'api_calls_today': self.api_calls['total_today']
                }

                # Cache the result
                self.checked_urls[image_url] = {
                    'result': api_result,
                    'timestamp': time.time()
                }

                return api_result

            return {
                'error': 'No results from API',
                'flagged': False,
                'categories': {}
            }

        except openai.RateLimitError:
            return {
                'error': 'OpenAI rate limit exceeded',
                'flagged': False,
                'categories': {}
            }
        except Exception as e:
            print(f"Image moderation error: {e}")
            return {
                'error': str(e),
                'flagged': False,
                'categories': {}
            }

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Check ONLY images in messages - IGNORE TEXT"""

        # ========== EARLY EXIT CHECKS ==========
        if not self.settings['enabled'] or not self.openai_client:
            return

        if self.settings['skip_bots'] and message.author.bot:
            return

        if message.channel.id == self.mod_log_channel_id:
            return

        if message.channel.id in self.settings['whitelisted_channels']:
            return

        if any(role.id in self.settings['whitelisted_roles'] for role in message.author.roles):
            return

        if self.is_user_on_cooldown(message.author.id):
            return

        # ========== CHECK FOR IMAGES ONLY ==========
        image_urls = []

        # Check attachments
        if self.settings['check_attachments']:
            for attachment in message.attachments:
                if attachment.filename.lower().endswith(('png', 'jpg', 'jpeg', 'gif', 'webp')):

                    # Skip by size
                    if self.settings['skip_small_images'] and attachment.size < 10240:
                        continue
                    if self.settings['skip_large_images'] and attachment.size > 10 * 1024 * 1024:
                        continue

                    image_urls.append(attachment.url)

                    # Limit to max images per message
                    if len(image_urls) >= self.settings['max_images_per_message']:
                        break

        # Check image URLs in text
        if self.settings['check_urls'] and len(image_urls) < self.settings['max_images_per_message']:
            urls = self.extract_image_urls(message.content)
            image_urls.extend(urls[:self.settings['max_images_per_message'] - len(image_urls)])

        # ========== CHECK IMAGES ==========
        if image_urls:
            for image_url in image_urls:
                result = await self.check_image_only(image_url)

                if result and result.get('flagged', False):
                    await self.handle_image_violation(message, result, image_url)
                    self.put_user_on_cooldown(message.author.id)
                    break  # Stop after first violation

    def extract_image_urls(self, text: str) -> List[str]:
        """Extract ONLY image URLs from text"""
        if not text:
            return []

        # Common image hosting domains + discord CDN
        image_pattern = r'https?://(?:[^\s]+\.)?(?:discord\.(?:com|app)|cdn\.discordapp\.com|media\.discordapp\.net|images-ext-\d+\.discordapp\.net|i\.imgur\.com|imgur\.com|i\.redd\.it|redd\.it|pbs\.twimg\.com|twimg\.com|instagram\.com|\.(?:png|jpg|jpeg|gif|webp|bmp))(?:\?[^\s]*)?'

        urls = re.findall(image_pattern, text, re.IGNORECASE)

        # Filter to ensure they end with image extensions
        filtered = []
        for url in urls:
            if re.search(r'\.(?:png|jpg|jpeg|gif|webp|bmp)(?:\?|$)', url, re.IGNORECASE):
                filtered.append(url)

        return filtered

    async def handle_image_violation(self, message: discord.Message, result: Dict, image_url: str):
        """Handle detected image violations"""
        try:
            # Delete the message
            await message.delete()

            # Get flagged categories
            flagged_cats = result.get('flagged_categories', [])

            # Send DM warning
            try:
                warning_embed = discord.Embed(
                    title="⚠️ Image Removed",
                    description="Your image was automatically removed by the moderation system.",
                    color=discord.Color.orange(),
                    timestamp=datetime.utcnow()
                )
                warning_embed.add_field(name="Channel", value=message.channel.mention, inline=False)
                warning_embed.add_field(name="Violations",
                                        value="\n".join(flagged_cats) if flagged_cats else "Inappropriate content",
                                        inline=False)
                warning_embed.add_field(name="Action", value="Message deleted", inline=False)
                warning_embed.set_footer(text=f"User ID: {message.author.id}")

                await message.author.send(embed=warning_embed)
            except discord.Forbidden:
                pass  # Can't DM

            # Log to mod channel
            await self.log_image_violation(message, result, image_url)

        except discord.Forbidden:
            print(f"No permission to delete message in {message.guild.name}")
        except Exception as e:
            print(f"Error handling image violation: {e}")

    async def get_mod_log_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        """Get mod log channel"""
        try:
            return guild.get_channel(self.mod_log_channel_id) or await guild.fetch_channel(self.mod_log_channel_id)
        except:
            return None

    async def log_image_violation(self, message: discord.Message, result: Dict, image_url: str):
        """Log image violations"""
        log_channel = await self.get_mod_log_channel(message.guild)
        if not log_channel:
            return

        flagged_cats = result.get('flagged_categories', [])

        embed = discord.Embed(
            title="🚨 Image Violation Detected",
            color=discord.Color.red(),
            timestamp=datetime.utcnow()
        )

        embed.add_field(name="User", value=f"{message.author.mention}\nID: `{message.author.id}`", inline=True)
        embed.add_field(name="Channel", value=f"{message.channel.mention}", inline=True)

        if flagged_cats:
            categories = result.get('categories', {})
            cat_details = []
            for cat in flagged_cats:
                if cat in categories:
                    score = categories[cat].get('score', 0)
                    cat_details.append(f"**{cat}**: {score:.1%}")

            if cat_details:
                embed.add_field(name="Violations", value="\n".join(cat_details), inline=False)

        # Add image URL (truncated)
        if len(image_url) > 100:
            embed.add_field(name="Image URL", value=f"`{image_url[:100]}...`", inline=False)
        else:
            embed.add_field(name="Image URL", value=f"`{image_url}`", inline=False)

        embed.set_footer(text=f"Message ID: {message.id} | Model: {result.get('model', 'Unknown')}")

        try:
            await log_channel.send(embed=embed)
        except:
            pass

    @commands.command(name="checkimage", aliases=["scanimg", "imgcheck"])
    @commands.has_permissions(manage_messages=True)
    async def check_image_command(self, ctx, image_url: str = None):
        """Manually check an image URL

        Usage:
        !checkimage https://example.com/image.jpg
        or attach an image with the command
        """
        if not image_url and not ctx.message.attachments:
            await ctx.send(
                "❌ Please provide an image URL or attach an image.\nExample: `!checkimage https://example.com/image.jpg`")
            return

        await ctx.trigger_typing()

        # Get image URL from attachment or provided URL
        if not image_url and ctx.message.attachments:
            attachment = ctx.message.attachments[0]
            if attachment.filename.lower().endswith(('png', 'jpg', 'jpeg', 'gif', 'webp')):
                image_url = attachment.url
            else:
                await ctx.send("❌ Please attach an image file (PNG, JPG, GIF, WEBP)")
                return

        result = await self.check_image_only(image_url)

        if result.get('error'):
            await ctx.send(f"❌ Error: {result['error']}")
            return

        # Send results
        flagged = result.get('flagged', False)
        flagged_cats = result.get('flagged_categories', [])

        embed = discord.Embed(
            title="🔍 Image Analysis Results",
            color=discord.Color.red() if flagged else discord.Color.green(),
            timestamp=datetime.utcnow()
        )

        embed.add_field(name="Status", value="🚨 **FLAGGED**" if flagged else "✅ **CLEAN**", inline=False)

        if flagged_cats:
            embed.add_field(name="Violations", value="\n".join(flagged_cats), inline=False)

        # Add all category scores
        categories = result.get('categories', {})
        if categories:
            scores_text = []
            for cat, data in sorted(categories.items(), key=lambda x: x[1].get('score', 0), reverse=True):
                score = data.get('score', 0)
                is_flagged = data.get('flagged', False)
                emoji = "🚩" if is_flagged else "📊"
                scores_text.append(f"{emoji} **{cat}**: {score:.1%}")

            embed.add_field(name="Category Scores", value="\n".join(scores_text), inline=False)

        embed.set_footer(
            text=f"Model: {result.get('model', 'Unknown')} | API calls today: {result.get('api_calls_today', 0)}")

        await ctx.send(embed=embed)

    @commands.command(name="imgstats", aliases=["imagestats"])
    @commands.has_permissions(manage_messages=True)
    async def show_image_stats(self, ctx):
        """Show image moderation statistics"""
        now = time.time()

        minute_calls = len([t for t in self.api_calls['minute'] if now - t < 60])
        hour_calls = len([t for t in self.api_calls['hour'] if now - t < 3600])

        embed = discord.Embed(
            title="📊 Image Moderation Stats",
            color=discord.Color.blue(),
            timestamp=datetime.utcnow()
        )

        embed.add_field(
            name="API Usage",
            value=f"**Minute:** {minute_calls}/{self.rate_limit['max_per_minute']}\n"
                  f"**Hour:** {hour_calls}/{self.rate_limit['max_per_hour']}\n"
                  f"**Today:** {self.api_calls['total_today']}",
            inline=True
        )

        embed.add_field(
            name="Cache",
            value=f"**URLs cached:** {len(self.checked_urls)}\n"
                  f"**Users on cooldown:** {len(self.user_cooldowns)}",
            inline=True
        )

        embed.add_field(
            name="Settings",
            value=f"**Enabled:** {self.settings['enabled']}\n"
                  f"**Max per message:** {self.settings['max_images_per_message']}\n"
                  f"**User cooldown:** {self.settings['user_cooldown_minutes']}min",
            inline=True
        )

        await ctx.send(embed=embed)

    @commands.command(name="toggleimgmod")
    @commands.has_permissions(administrator=True)
    async def toggle_moderation(self, ctx):
        """Toggle image moderation on/off"""
        self.settings['enabled'] = not self.settings['enabled']
        status = "✅ **ENABLED**" if self.settings['enabled'] else "❌ **DISABLED**"

        embed = discord.Embed(
            title="🔄 Image Moderation Toggled",
            description=f"Image moderation is now {status}",
            color=discord.Color.green() if self.settings['enabled'] else discord.Color.red(),
            timestamp=datetime.utcnow()
        )

        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(ImageModerator(bot))