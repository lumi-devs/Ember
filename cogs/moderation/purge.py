import discord
from discord.ext import commands
import os
import asyncio

# List of mod IDs allowed to purge
MOD_IDS = [
    int(x.strip()) for x in os.environ.get("MOD_IDS", "").split(",") if x.strip()
]


class PurgeCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.purge_cooldown = commands.CooldownMapping.from_cooldown(
            3, 60, commands.BucketType.user
        )  # 3 uses per minute per user

    @commands.command(name="purge")
    async def purge(self, ctx, amount: int = None):
        # Cooldown check
        bucket = self.purge_cooldown.get_bucket(ctx.message)
        retry_after = bucket.update_rate_limit()
        if retry_after:
            return await ctx.send(
                f"⏳ Please wait {retry_after:.1f}s before using purge again.",
                delete_after=5,
            )

        # Only mods can use
        if ctx.author.id not in MOD_IDS:
            embed = discord.Embed(
                description="❌ You are not allowed to use this command.",
                color=discord.Color.red(),
            )
            return await ctx.send(embed=embed, delete_after=5)

        # If replying to a message
        if ctx.message.reference:
            try:
                ref_msg = await ctx.channel.fetch_message(
                    ctx.message.reference.message_id
                )
                # Add confirmation for large purges
                messages_after_ref = len(
                    await ctx.channel.history(after=ref_msg, limit=None).flatten()
                )

                if messages_after_ref > 50:
                    confirm_embed = discord.Embed(
                        description=f"⚠️ This will delete **{messages_after_ref}** messages. Type `confirm` to proceed.",
                        color=discord.Color.orange(),
                    )
                    confirm_msg = await ctx.send(embed=confirm_embed)

                    def check(m):
                        return (
                            m.author == ctx.author
                            and m.channel == ctx.channel
                            and m.content.lower() == "confirm"
                        )

                    try:
                        await self.bot.wait_for("message", timeout=10.0, check=check)
                    except asyncio.TimeoutError:
                        await confirm_msg.delete()
                        return await ctx.send("❌ Purge cancelled.", delete_after=5)

                    await confirm_msg.delete()

                deleted = await ctx.channel.purge(limit=None, after=ref_msg)
                embed = discord.Embed(
                    description=f"✅ Deleted **{len(deleted)}** messages.",
                    color=discord.Color.green(),
                )
                await ctx.send(embed=embed, delete_after=5)

            except discord.NotFound:
                embed = discord.Embed(
                    description="❌ Could not find the referenced message.",
                    color=discord.Color.red(),
                )
                await ctx.send(embed=embed, delete_after=5)
            except discord.Forbidden:
                embed = discord.Embed(
                    description="❌ I don't have permission to delete messages.",
                    color=discord.Color.red(),
                )
                await ctx.send(embed=embed, delete_after=5)
            except discord.HTTPException as e:
                embed = discord.Embed(
                    description=f"❌ Failed to delete messages: {e}",
                    color=discord.Color.red(),
                )
                await ctx.send(embed=embed, delete_after=5)
            return

        # If no amount given
        if amount is None:
            embed = discord.Embed(
                description="❌ Please specify the number of messages to delete, e.g. `.purge 100`",
                color=discord.Color.orange(),
            )
            return await ctx.send(embed=embed, delete_after=5)

        if amount < 1:
            embed = discord.Embed(
                description="❌ Number must be at least 1.", color=discord.Color.red()
            )
            return await ctx.send(embed=embed, delete_after=5)

        # Safety limit
        if amount > 1000:
            embed = discord.Embed(
                description="❌ Cannot delete more than 1000 messages at once.",
                color=discord.Color.red(),
            )
            return await ctx.send(embed=embed, delete_after=5)

        # Confirmation for large purges
        if amount > 50:
            confirm_embed = discord.Embed(
                description=f"⚠️ This will delete **{amount}** messages. Type `confirm` to proceed.",
                color=discord.Color.orange(),
            )
            confirm_msg = await ctx.send(embed=confirm_embed)

            def check(m):
                return (
                    m.author == ctx.author
                    and m.channel == ctx.channel
                    and m.content.lower() == "confirm"
                )

            try:
                await self.bot.wait_for("message", timeout=10.0, check=check)
            except asyncio.TimeoutError:
                await confirm_msg.delete()
                return await ctx.send("❌ Purge cancelled.", delete_after=5)

            await confirm_msg.delete()

        # Delete messages
        try:
            deleted = await ctx.channel.purge(
                limit=amount + 1
            )  # +1 to include the purge command message
            embed = discord.Embed(
                description=f"✅ Deleted **{len(deleted)-1}** messages.",
                color=discord.Color.green(),
            )
            await ctx.send(embed=embed, delete_after=5)

        except discord.Forbidden:
            embed = discord.Embed(
                description="❌ I don't have permission to delete messages.",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed, delete_after=5)
        except discord.HTTPException as e:
            embed = discord.Embed(
                description=f"❌ Failed to delete messages: {e}",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed, delete_after=5)

    @commands.command(name="purgeuser")
    async def purge_user(self, ctx, user: discord.Member, amount: int = 100):
        """Purge messages from a specific user"""
        # Only mods can use
        if ctx.author.id not in MOD_IDS:
            embed = discord.Embed(
                description="❌ You are not allowed to use this command.",
                color=discord.Color.red(),
            )
            return await ctx.send(embed=embed, delete_after=5)

        if amount > 500:
            embed = discord.Embed(
                description="❌ Cannot delete more than 500 messages at once.",
                color=discord.Color.red(),
            )
            return await ctx.send(embed=embed, delete_after=5)

        def check(m):
            return m.author == user

        try:
            deleted = await ctx.channel.purge(limit=amount, check=check)
            embed = discord.Embed(
                description=f"✅ Deleted **{len(deleted)}** messages from {user.mention}.",
                color=discord.Color.green(),
            )
            await ctx.send(embed=embed, delete_after=5)

        except discord.Forbidden:
            embed = discord.Embed(
                description="❌ I don't have permission to delete messages.",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed, delete_after=5)
        except discord.HTTPException as e:
            embed = discord.Embed(
                description=f"❌ Failed to delete messages: {e}",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed, delete_after=5)

    @commands.command(name="purgeuntil")
    async def purge_until(self, ctx, message_id: int):
        """Purge all messages until a specific message ID"""
        # Only mods can use
        if ctx.author.id not in MOD_IDS:
            embed = discord.Embed(
                description="❌ You are not allowed to use this command.",
                color=discord.Color.red(),
            )
            return await ctx.send(embed=embed, delete_after=5)

        try:
            target_msg = await ctx.channel.fetch_message(message_id)
            deleted = await ctx.channel.purge(limit=None, after=target_msg)
            embed = discord.Embed(
                description=f"✅ Deleted **{len(deleted)}** messages.",
                color=discord.Color.green(),
            )
            await ctx.send(embed=embed, delete_after=5)

        except discord.NotFound:
            embed = discord.Embed(
                description="❌ Message not found.", color=discord.Color.red()
            )
            await ctx.send(embed=embed, delete_after=5)
        except discord.Forbidden:
            embed = discord.Embed(
                description="❌ I don't have permission to delete messages.",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed, delete_after=5)
        except discord.HTTPException as e:
            embed = discord.Embed(
                description=f"❌ Failed to delete messages: {e}",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed, delete_after=5)


async def setup(bot):
    await bot.add_cog(PurgeCog(bot))
