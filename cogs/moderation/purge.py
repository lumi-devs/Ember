# cogs/purge_cog.py
import discord
from discord.ext import commands
import os

# List of mod IDs allowed to purge
MOD_IDS = [int(x.strip()) for x in os.environ.get("MOD_IDS", "").split(",") if x.strip()]


class PurgeCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="purge")
    async def purge(self, ctx, amount: int = None):
        # Only mods can use
        if ctx.author.id not in MOD_IDS:
            return await ctx.send("❌ You are not allowed to use this command.", delete_after=5)

        # If replying to a message
        if ctx.message.reference:
            try:
                ref_msg = await ctx.channel.fetch_message(ctx.message.reference.message_id)
                deleted = await ctx.channel.purge(limit=None, after=ref_msg)
                await ctx.send(f"✅ Deleted {len(deleted)} messages.", delete_after=5)
            except discord.NotFound:
                await ctx.send("❌ Could not find the referenced message.", delete_after=5)
            return

        # If no amount given
        if amount is None:
            return await ctx.send("❌ Please specify the number of messages to delete, e.g. `.purge 100`", delete_after=5)

        if amount < 1:
            return await ctx.send("❌ Number must be at least 1.", delete_after=5)

        # Delete messages
        deleted = await ctx.channel.purge(limit=amount + 1)  # +1 to include the purge command message
        await ctx.send(f"✅ Deleted {len(deleted)-1} messages.", delete_after=5)

async def setup(bot):
    await bot.add_cog(PurgeCog(bot))
