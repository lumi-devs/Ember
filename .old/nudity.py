import discord
from discord.ext import commands, tasks
import aiohttp
import asyncio
import os
from datetime import datetime
from typing import Optional, Dict
import openai
import time
import hashlib


class StrictImageModerator(commands.Cog):
    """ULTRA-STRICT image-only moderation with aggressive rate limiting"""

    def __init__(self, bot):
        self.bot = bot
        self.session = None  # Lazy init
        self.openai_client = None

        # Your mod log channel ID
        self.mod_log_channel_id = 1416357489925947452

        # Initialize OpenAI client if API key is provided
        self.openai_api_key = os.environ.get("OPENAI_API_KEY", "")
        if self.openai_api_key:
            self.openai_client = openai.OpenAI(api_key=self.openai_api_key)
            print("✅ OpenAI client initialized")
        else:
            print("❌ OpenAI API key not set")
            return

        # ========== AGGRESSIVE RATE LIMITING ==========
        self.rate_limit = {
            "max_per_minute": 2,  # 2 calls per minute MAX
            "max_per_hour": 20,  # 20 calls per hour MAX
            "cooldown_seconds": 10,  # 10 seconds between calls
            "enabled": True,  # Global on/off switch
            "last_reset": time.time(),
        }

        # Track API calls
        self.api_calls = {
            "minute": [],  # Timestamps of calls in last minute
            "hour": [],  # Timestamps of calls in last hour
            "blocked": 0,  # Number of blocked calls
            "errors": 0,  # Number of errors
        }

        # ========== STRICT CHECKS ==========
        self.checks = {
            "attachments_only": True,  # ONLY check attachments, NO URLs
            "skip_gifs": True,  # Skip GIFs (they cause 400 errors)
            "skip_embeds": True,  # Skip embedded images
            "min_size_kb": 50,  # Min 50KB file size
            "max_size_mb": 5,  # Max 5MB file size
            "valid_extensions": [".png", ".jpg", ".jpeg", ".webp"],  # NO .gif
            "enabled_channels": [],  # ONLY check these channels (empty = all)
            "skip_channels": [],  # Skip these channels
            "skip_roles": [],  # Skip these roles
            "skip_users": [],  # Skip these users
            "debug_mode": True,  # Log what's being checked
        }

        # ========== CACHE ==========
        self.image_cache = {}  # hash: {result, timestamp}
        self.cache_duration = 7200  # 2 hours

        # ========== COOLDOWNS ==========
        self.user_cooldowns = {}  # user_id: timestamp
        self.channel_cooldowns = {}  # channel_id: timestamp

        # Start monitor
        self.monitor_task.start()

        print(
            f"🛡️ Strict Image Moderator loaded. Rate limit: {self.rate_limit['max_per_minute']}/min"
        )

    async def cog_unload(self):
        self.monitor_task.cancel()
        if self.session:
            await self.session.close()

    @tasks.loop(seconds=30)
    async def monitor_task(self):
        """Monitor and reset rate limits"""
        now = time.time()

        # Clean old API call records
        self.api_calls["minute"] = [t for t in self.api_calls["minute"] if now - t < 60]
        self.api_calls["hour"] = [t for t in self.api_calls["hour"] if now - t < 3600]

        # Clean old cache
        expired = []
        for key, data in list(self.image_cache.items()):
            if now - data["timestamp"] > self.cache_duration:
                expired.append(key)
        for key in expired:
            del self.image_cache[key]

        # Clean old cooldowns (1 hour)
        expired_users = [
            uid for uid, ts in list(self.user_cooldowns.items()) if now - ts > 3600
        ]
        for uid in expired_users:
            del self.user_cooldowns[uid]

        expired_channels = [
            cid for cid, ts in list(self.channel_cooldowns.items()) if now - ts > 3600
        ]
        for cid in expired_channels:
            del self.channel_cooldowns[cid]

    def can_make_api_call(self) -> bool:
        """STRICT rate limit check"""
        if not self.rate_limit["enabled"]:
            return False

        now = time.time()

        # Check minute limit
        minute_calls = len([t for t in self.api_calls["minute"] if now - t < 60])
        if minute_calls >= self.rate_limit["max_per_minute"]:
            self.api_calls["blocked"] += 1
            if self.checks["debug_mode"]:
                print(
                    f"⛔ Blocked: Minute limit ({minute_calls}/{self.rate_limit['max_per_minute']})"
                )
            return False

        # Check hour limit
        hour_calls = len([t for t in self.api_calls["hour"] if now - t < 3600])
        if hour_calls >= self.rate_limit["max_per_hour"]:
            self.api_calls["blocked"] += 1
            if self.checks["debug_mode"]:
                print(
                    f"⛔ Blocked: Hour limit ({hour_calls}/{self.rate_limit['max_per_hour']})"
                )
            return False

        # Check global cooldown
        if hasattr(self, "last_api_call"):
            if now - self.last_api_call < self.rate_limit["cooldown_seconds"]:
                return False

        return True

    def record_api_call(self):
        """Record an API call"""
        now = time.time()
        self.api_calls["minute"].append(now)
        self.api_calls["hour"].append(now)
        self.last_api_call = now

    def is_valid_image_attachment(self, attachment: discord.Attachment) -> bool:
        """STRICT validation of image attachments"""

        # Check extension
        ext = attachment.filename.lower()
        valid = False
        for valid_ext in self.checks["valid_extensions"]:
            if ext.endswith(valid_ext):
                valid = True
                break

        if not valid:
            if self.checks["debug_mode"]:
                print(f"❌ Invalid extension: {attachment.filename}")
            return False

        # Skip GIFs if configured
        if self.checks["skip_gifs"] and ext.endswith(".gif"):
            if self.checks["debug_mode"]:
                print(f"⏭️ Skipping GIF: {attachment.filename}")
            return False

        # Check size
        if attachment.size < self.checks["min_size_kb"] * 1024:
            if self.checks["debug_mode"]:
                print(
                    f"📏 Too small: {attachment.filename} ({attachment.size / 1024:.1f}KB)"
                )
            return False

        if attachment.size > self.checks["max_size_mb"] * 1024 * 1024:
            if self.checks["debug_mode"]:
                print(
                    f"📏 Too large: {attachment.filename} ({attachment.size / (1024 * 1024):.1f}MB)"
                )
            return False

        return True

    def get_image_hash(self, image_url: str) -> str:
        """Create hash for caching"""
        return hashlib.md5(image_url.encode()).hexdigest()

    async def check_single_image(self, image_url: str) -> Optional[Dict]:
        """Check ONE image with maximum safety"""

        if not self.openai_client:
            return None

        # Check cache
        image_hash = self.get_image_hash(image_url)
        if image_hash in self.image_cache:
            cached = self.image_cache[image_hash]
            if time.time() - cached["timestamp"] < self.cache_duration:
                if self.checks["debug_mode"]:
                    print(f"♻️ Using cached result for {image_url[:50]}...")
                return cached["result"]

        # Check rate limits
        if not self.can_make_api_call():
            self.api_calls["blocked"] += 1
            if self.checks["debug_mode"]:
                print(f"⛔ Rate limited, skipping: {image_url[:50]}...")
            return None

        # Make API call
        self.record_api_call()

        try:
            if self.checks["debug_mode"]:
                print(f"🔍 Checking image: {image_url[:50]}...")

            # Create session if needed
            if not self.session:
                self.session = aiohttp.ClientSession()

            # Call OpenAI with timeout
            response = await asyncio.wait_for(
                self.openai_client.moderations.create(
                    model="omni-moderation-latest",
                    input=[{"type": "image_url", "image_url": {"url": image_url}}],
                ),
                timeout=10.0,
            )

            if response.results:
                result = response.results[0]

                # Process categories
                categories = {}
                flagged_categories = []

                # Focus on image-relevant categories
                image_categories = {
                    "sexual": 0.7,
                    "sexual/minors": 0.5,
                    "violence": 0.7,
                    "violence/graphic": 0.8,
                    "self-harm": 0.6,
                }

                for category, threshold in image_categories.items():
                    if category in result.category_scores:
                        score = result.category_scores[category]
                        categories[category] = {
                            "score": score,
                            "flagged": score >= threshold,
                        }
                        if categories[category]["flagged"]:
                            flagged_categories.append(category)

                flagged = result.flagged or len(flagged_categories) > 0

                api_result = {
                    "flagged": flagged,
                    "categories": categories,
                    "flagged_categories": flagged_categories,
                    "model": response.model,
                    "url": image_url,
                    "error": None,
                    "timestamp": time.time(),
                }

                # Cache result
                self.image_cache[image_hash] = {
                    "result": api_result,
                    "timestamp": time.time(),
                }

                return api_result

        except asyncio.TimeoutError:
            if self.checks["debug_mode"]:
                print("⏰ Timeout checking image")
            self.api_calls["errors"] += 1
        except openai.RateLimitError:
            if self.checks["debug_mode"]:
                print("⛔ OpenAI rate limit error")
            self.api_calls["errors"] += 1
            # Disable further calls for 5 minutes
            self.rate_limit["enabled"] = False
            asyncio.create_task(self.enable_after_delay(300))
        except openai.APIError as e:
            if self.checks["debug_mode"]:
                print(f"🔌 OpenAI API error: {e}")
            self.api_calls["errors"] += 1
        except Exception as e:
            if self.checks["debug_mode"]:
                print(f"💥 Unexpected error: {e}")
            self.api_calls["errors"] += 1

        return None

    async def enable_after_delay(self, seconds: int):
        """Re-enable API calls after delay"""
        await asyncio.sleep(seconds)
        self.rate_limit["enabled"] = True
        if self.checks["debug_mode"]:
            print(f"✅ Re-enabled API calls after {seconds} second cooldown")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """ULTRA-STRICT image checking - attachments ONLY"""

        # Quick exit checks
        if not self.openai_client or not self.rate_limit["enabled"]:
            return

        if message.author.bot:
            return

        if message.channel.id == self.mod_log_channel_id:
            return

        # Check if channel is skipped
        if message.channel.id in self.checks["skip_channels"]:
            return

        # Check if only specific channels are enabled
        if (
            self.checks["enabled_channels"]
            and message.channel.id not in self.checks["enabled_channels"]
        ):
            return

        # Check if user/role is skipped
        if message.author.id in self.checks["skip_users"]:
            return

        if any(role.id in self.checks["skip_roles"] for role in message.author.roles):
            return

        # Check channel cooldown
        if message.channel.id in self.channel_cooldowns:
            if time.time() - self.channel_cooldowns[message.channel.id] < 60:
                return

        # Check user cooldown
        if message.author.id in self.user_cooldowns:
            if time.time() - self.user_cooldowns[message.author.id] < 300:  # 5 minutes
                return

        # ========== CHECK ATTACHMENTS ONLY ==========
        valid_attachments = []

        for attachment in message.attachments:
            if self.is_valid_image_attachment(attachment):
                valid_attachments.append(attachment)
                break  # Only check ONE image per message

        if not valid_attachments:
            return  # No valid images found

        # ========== CHECK THE IMAGE ==========
        attachment = valid_attachments[0]

        if self.checks["debug_mode"]:
            print(f"📸 Checking {attachment.filename} from {message.author}")

        result = await self.check_single_image(attachment.url)

        if result and result.get("flagged", False):
            # Apply cooldowns
            self.user_cooldowns[message.author.id] = time.time()
            self.channel_cooldowns[message.channel.id] = time.time()

            # Handle violation
            await self.handle_violation(message, result, attachment.url)

    async def handle_violation(
        self, message: discord.Message, result: Dict, image_url: str
    ):
        """Handle image violation"""
        try:
            # Delete message
            await message.delete()

            # Send warning
            try:
                embed = discord.Embed(
                    title="⚠️ Image Removed",
                    description="Your image was automatically removed.",
                    color=discord.Color.orange(),
                    timestamp=datetime.utcnow(),
                )
                embed.add_field(
                    name="Reason", value="Inappropriate content detected", inline=False
                )
                embed.set_footer(text=f"User ID: {message.author.id}")

                await message.author.send(embed=embed)
            except Exception:
                pass

            # Log to mod channel
            await self.log_violation(message, result, image_url)

        except discord.Forbidden:
            pass  # No permission
        except Exception as e:
            if self.checks["debug_mode"]:
                print(f"Error handling violation: {e}")

    async def log_violation(
        self, message: discord.Message, result: Dict, image_url: str
    ):
        """Log to mod channel"""
        try:
            channel = self.bot.get_channel(self.mod_log_channel_id)
            if not channel:
                return

            flagged_cats = result.get("flagged_categories", [])

            embed = discord.Embed(
                title="🚨 Image Violation",
                color=discord.Color.red(),
                timestamp=datetime.utcnow(),
            )

            embed.add_field(name="User", value=f"{message.author.mention}", inline=True)
            embed.add_field(name="Channel", value=message.channel.mention, inline=True)

            if flagged_cats:
                embed.add_field(
                    name="Categories", value=", ".join(flagged_cats), inline=False
                )

            # Add scores
            categories = result.get("categories", {})
            if categories:
                scores = []
                for cat, data in categories.items():
                    if data.get("flagged", False):
                        scores.append(f"**{cat}**: {data.get('score', 0):.1%}")

                if scores:
                    embed.add_field(
                        name="Scores", value="\n".join(scores), inline=False
                    )

            embed.set_footer(text=f"ID: {message.author.id}")

            await channel.send(embed=embed)

        except Exception as e:
            if self.checks["debug_mode"]:
                print(f"Logging error: {e}")

    @commands.command(name="imglimit")
    @commands.has_permissions(administrator=True)
    async def set_rate_limit(self, ctx, limit_type: str, value: int):
        """Set rate limits

        !imglimit minute 2    (2 calls per minute)
        !imglimit hour 20     (20 calls per hour)
        !imglimit cooldown 10 (10 seconds between calls)
        """
        limit_type = limit_type.lower()

        if limit_type == "minute":
            self.rate_limit["max_per_minute"] = value
            await ctx.send(f"✅ Minute limit: {value} calls per minute")
        elif limit_type == "hour":
            self.rate_limit["max_per_hour"] = value
            await ctx.send(f"✅ Hour limit: {value} calls per hour")
        elif limit_type == "cooldown":
            self.rate_limit["cooldown_seconds"] = value
            await ctx.send(f"✅ Cooldown: {value} seconds between calls")
        else:
            await ctx.send("❌ Use: minute, hour, or cooldown")

    @commands.command(name="imgstats")
    async def show_stats(self, ctx):
        """Show moderation statistics"""
        now = time.time()

        minute_calls = len([t for t in self.api_calls["minute"] if now - t < 60])
        hour_calls = len([t for t in self.api_calls["hour"] if now - t < 3600])

        embed = discord.Embed(
            title="📊 Image Moderation Stats",
            color=discord.Color.blue(),
            timestamp=datetime.utcnow(),
        )

        embed.add_field(
            name="Rate Limits",
            value=f"**Current:** {minute_calls}/{self.rate_limit['max_per_minute']} per minute\n"
            f"**Hour:** {hour_calls}/{self.rate_limit['max_per_hour']} per hour\n"
            f"**Cooldown:** {self.rate_limit['cooldown_seconds']}s",
            inline=False,
        )

        embed.add_field(
            name="Stats",
            value=f"**Total calls:** {len(self.api_calls['hour'])}\n"
            f"**Blocked:** {self.api_calls['blocked']}\n"
            f"**Errors:** {self.api_calls['errors']}\n"
            f"**Cached:** {len(self.image_cache)}",
            inline=True,
        )

        embed.add_field(
            name="Status",
            value=f"**Enabled:** {self.rate_limit['enabled']}\n"
            f"**Users on CD:** {len(self.user_cooldowns)}\n"
            f"**Channels on CD:** {len(self.channel_cooldowns)}",
            inline=True,
        )

        await ctx.send(embed=embed)

    @commands.command(name="pauseimgmod")
    @commands.has_permissions(administrator=True)
    async def pause_moderation(self, ctx, minutes: int = 30):
        """Pause image moderation"""
        self.rate_limit["enabled"] = False

        embed = discord.Embed(
            title="⏸️ Image Moderation Paused",
            description=f"Moderation paused for {minutes} minutes",
            color=discord.Color.orange(),
            timestamp=datetime.utcnow(),
        )

        await ctx.send(embed=embed)

        # Auto-resume
        await asyncio.sleep(minutes * 60)
        self.rate_limit["enabled"] = True

        try:
            await ctx.send("▶️ Image moderation resumed")
        except Exception:
            pass

    @commands.command(name="testimg")
    @commands.has_permissions(manage_messages=True)
    async def test_image(self, ctx, url: str = None):
        """Test an image URL"""
        if not url and not ctx.message.attachments:
            await ctx.send("❌ Provide URL or attach image")
            return

        # await ctx.trigger_typing()

        if not url:
            attachment = ctx.message.attachments[0]
            if not self.is_valid_image_attachment(attachment):
                await ctx.send("❌ Invalid image attachment")
                return
            url = attachment.url

        result = await self.check_single_image(url)

        if not result:
            await ctx.send("❌ Could not check image (rate limited?)")
            return

        flagged = result.get("flagged", False)

        embed = discord.Embed(
            title="🔍 Image Test",
            color=discord.Color.red() if flagged else discord.Color.green(),
            timestamp=datetime.utcnow(),
        )

        embed.add_field(
            name="Status", value="🚨 FLAGGED" if flagged else "✅ CLEAN", inline=False
        )

        categories = result.get("categories", {})
        if categories:
            scores = []
            for cat, data in categories.items():
                scores.append(f"**{cat}**: {data.get('score', 0):.1%}")

            if scores:
                embed.add_field(name="Scores", value="\n".join(scores), inline=False)

        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(StrictImageModerator(bot))
