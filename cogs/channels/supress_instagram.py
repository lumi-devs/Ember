# no_insta_embed.py
import re
import os
import discord
from discord.ext import commands

BLOCK_CHANNELS = {
    int(x) for x in os.environ.get("INSTA_BLOCK_CHANNELS", "").split(",") if x.strip()
}
# -------------------------------------

INSTAGRAM_RE = re.compile(
    r"(https?://)?(www\.)?(instagram\.com|instagr\.am)(/[^\s]*)?", flags=re.IGNORECASE
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

        # ignore DMs — block_channels only makes sense in guilds
        if not message.guild:
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
                # suppress=True sets the SUPPRESS_EMBEDS message flag
                await message.edit(suppress=True)
            except discord.Forbidden:
                # bot lacks Manage Messages permission — silently return
                return
            except discord.NotFound:
                # message was deleted before we could suppress it
                return
            except discord.HTTPException:
                # unexpected HTTP error — ignore to avoid crashing the bot
                return


async def setup(bot: commands.Bot):
    await bot.add_cog(NoInstaEmbed(bot))