import discord
from discord.ext import commands
import datetime

class Verification(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        
        # --- ⚙️ CONFIGURATION ---
        self.STAFF_ROLE_ID = 1460679564916297819  # Madam Moderator Role ID
        self.VERIFIED_ROLE_ID = 1460679593403879547  # The Verified Role ID
        self.LOG_CHANNEL_ID = 1460688886870180025  # Where logs go
        
        # 🔒 LIST OF ALLOWED CHANNEL IDs (Comma separated)
        # The bot will ONLY respond to commands in these channels
        self.ALLOWED_CHANNEL_IDS = [
            1460678841478680698,  # e.g., #staff-commands
            1413074803421614100,
            1424670074354143264   # e.g., #admin-chat
        ]
        # ------------------------

    async def cog_check(self, ctx):
        """
        Global check for this Cog.
        1. Must be in allowed channel.
        2. User must have staff role.
        """
        if not ctx.guild:
            return False

        # 1. Channel Check
        if ctx.channel.id not in self.ALLOWED_CHANNEL_IDS:
            return False # Silently ignore commands in wrong channels

        # 2. Role Check
        has_role = ctx.guild.get_role(self.STAFF_ROLE_ID) in ctx.author.roles
        if not has_role:
            return
        
        return has_role

    # --- HELPER TO SEND EMBEDS ---
    async def send_embed(self, ctx, message, color):
        embed = discord.Embed(description=message, color=color)
        embed.set_footer(text="Madâme System")
        await ctx.send(embed=embed)

    @commands.command(name="verify")
    async def verify_user(self, ctx, member: discord.Member):
        role = ctx.guild.get_role(self.VERIFIED_ROLE_ID)
        
        if not role:
            return await self.send_embed(ctx, "❌ **Error:** Verified Role ID is invalid.", discord.Color.red())

        if role in member.roles:
            return await self.send_embed(ctx, f"⚠️ {member.mention} is **already verified**!", discord.Color.gold())

        try:
            await member.add_roles(role)
            await self.send_embed(ctx, f"✅ **Success:** {member.mention} has been verified.", discord.Color.green())
            await self.log_action(ctx, member, "Verified", discord.Color.green())
        except discord.Forbidden:
            await self.send_embed(ctx, "❌ **Error:** I lack permission to manage roles.", discord.Color.red())

    @commands.command(name="unverify")
    async def unverify_user(self, ctx, member: discord.Member):
        role = ctx.guild.get_role(self.VERIFIED_ROLE_ID)
        
        if not role:
            return await self.send_embed(ctx, "❌ **Error:** Verified Role ID is invalid.", discord.Color.red())

        if role not in member.roles:
            return await self.send_embed(ctx, f"⚠️ {member.mention} is **not verified**.", discord.Color.gold())

        try:
            await member.remove_roles(role)
            await self.send_embed(ctx, f"✅ **Success:** Removed verification from {member.mention}.", discord.Color.red())
            await self.log_action(ctx, member, "Unverified", discord.Color.dark_red())
        except discord.Forbidden:
            await self.send_embed(ctx, "❌ **Error:** I lack permission to manage roles.", discord.Color.red())

    async def log_action(self, ctx, target, action, color):
        log_channel = self.bot.get_channel(self.LOG_CHANNEL_ID)
        if not log_channel:
            return 

        embed = discord.Embed(title=f"User {action}", color=color, timestamp=datetime.datetime.now())
        embed.add_field(name="User", value=f"{target.mention}\nID: `{target.id}`", inline=True)
        embed.add_field(name="Moderator", value=f"{ctx.author.mention}", inline=True)
        embed.set_thumbnail(url=target.display_avatar.url)
        
        await log_channel.send(embed=embed)

async def setup(bot):
    await bot.add_cog(Verification(bot))