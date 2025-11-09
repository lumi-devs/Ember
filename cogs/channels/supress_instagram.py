# no_insta_embed.py
import re
import discord
from discord.ext import commands
from discord import app_commands


BLOCK_CHANNELS = {
    1393171137017413712,
    1393171121154560010
}
# -------------------------------------

INSTAGRAM_RE = re.compile(
    r"(https?://)?(www\.)?(instagram\.com|instagr\.am)(/[^\s]*)?",
    flags=re.IGNORECASE
)

class NoInstaEmbed(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # runtime-settable copy (optional)
        self.block_channels = set(BLOCK_CHANNELS)

    def is_instagram_url(self, content: str) -> bool:
        if not content:
            return False
        return bool(INSTAGRAM_RE.search(content))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # ignore bot messages and webhooks
        if message.author.bot or message.webhook_id is not None:
            return

        # only act if channel is in blocked set
        if message.channel.id not in self.block_channels:
            return

        # check content + attachments' URLs (in case someone posts a link in attachment name)
        content_to_check = message.content or ""
        # attachments often do not contain the original URL, but we still check content
        if self.is_instagram_url(content_to_check):
            # Protect: ensure bot has Manage Messages permission in that channel
            try:
                # suppress_embeds is a coroutine on Message
                await message.suppress_embeds(suppress=True)
            except discord.Forbidden:
                # bot lacks permission
                # optionally log or notify admins — we silently return
                return
            except Exception:
                # unexpected error - ignore to avoid crashing the bot
                return


async def setup(bot: commands.Bot):
    await bot.add_cog(NoInstaEmbed(bot))
