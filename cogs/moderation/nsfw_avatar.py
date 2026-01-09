import discord
from discord.ext import commands
from huggingface_hub import InferenceClient
import os
import asyncio
import functools
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("NSFW_Scanner")

# Configuration
HF_TOKEN = os.getenv("HF_TOKEN")  # Ensure this is set in your environment variables
MODEL_NAME = "Falconsai/nsfw_image_detection"
CONFIDENCE_THRESHOLD = 0.8  # 80% confidence required to kick

class NSFWScanner(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        if not HF_TOKEN:
            logger.warning("HF_TOKEN not found in environment variables!")
        
        self.client = InferenceClient(
            provider="hf-inference",
            api_key=HF_TOKEN,
        )

    def _classify_image_sync(self, image_url: str):
        """
        Synchronous function to query HF API. 
        Must be run in an executor to avoid blocking Discord loop.
        """
        try:
            # The API accepts URLs directly
            result = self.client.image_classification(image_url, model=MODEL_NAME)
            return result
        except Exception as e:
            logger.error(f"API Error processing {image_url}: {e}")
            return None

    async def is_avatar_nsfw(self, image_url: str) -> bool:
        """Async wrapper for the classification"""
        if not image_url:
            return False

        # Run the blocking API call in a separate thread
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, 
            functools.partial(self._classify_image_sync, image_url)
        )

        if not result:
            return False

        nsfw_score = 0.0
        
        # Parse results based on the specific model structure
        # Falconsai usually returns [{'label': 'nsfw', 'score': 0.9}, {'label': 'normal', 'score': 0.1}]
        for item in result:
            label = getattr(item, 'label', '').lower()
            score = getattr(item, 'score', 0.0)

            if label == 'nsfw':
                nsfw_score = score
                break
        
        logger.info(f"Scanned image. NSFW Score: {nsfw_score:.2f}")
        return nsfw_score >= CONFIDENCE_THRESHOLD

    async def kick_user(self, member: discord.Member):
        """Helper function to kick a member"""
        try:
            if member.top_role >= member.guild.me.top_role:
                logger.warning(f"Cannot kick {member} - Role hierarchy issue.")
                return

            await member.kick(reason="NSFW Avatar Detected")
            logger.info(f"Kicked {member} (ID: {member.id}) from {member.guild.name} for NSFW avatar.")
            
            # Optional: Send a message to the system channel
            if member.guild.system_channel:
                await member.guild.system_channel.send(f"🚨 **{member.mention}** was kicked for having an NSFW profile picture.")
                
        except discord.Forbidden:
            logger.error(f"Missing permissions to kick {member} in {member.guild.name}")
        except Exception as e:
            logger.error(f"Error kicking {member}: {e}")

    # --- EVENT LISTENERS ---

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Triggered when a user joins the server"""
        if member.bot: 
            return

        avatar_url = member.display_avatar.url
        logger.info(f"Scanning new member: {member} ({member.id})")
        
        if await self.is_avatar_nsfw(str(avatar_url)):
            await self.kick_user(member)

    @commands.Cog.listener()
    async def on_user_update(self, before: discord.User, after: discord.User):
        """Triggered when a user updates their profile (Global event)"""
        if after.bot: 
            return

        # Only process if the avatar actually changed
        if before.avatar == after.avatar:
            return

        logger.info(f"User {after} changed avatar. Scanning...")
        
        is_nsfw = await self.is_avatar_nsfw(str(after.display_avatar.url))

        if is_nsfw:
            # on_user_update is global, so we must find which mutual guilds the user is in
            # and kick them from the ones where the bot has permissions.
            for guild in self.bot.guilds:
                member = guild.get_member(after.id)
                if member:
                    await self.kick_user(member)

async def setup(bot):
    await bot.add_cog(NSFWScanner(bot))