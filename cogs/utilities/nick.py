import discord
from discord.ext import commands

class NicknameChanger(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name='nick')
    @commands.has_permissions(manage_nicknames=True)
    async def change_nickname(self, ctx, member: discord.Member, *, new_nick: str):
        """Change a member's nickname if they are lower in role hierarchy."""
        # Prevent changing nickname of self or bot
        if member == ctx.author:
            return await ctx.send("⚠️ You can't change your own nickname with this command.")
        if member == ctx.guild.me:
            return await ctx.send("⚠️ You can't change my nickname with this command.")

        # Role hierarchy check
        if member.top_role.position >= ctx.author.top_role.position:
            return await ctx.send("❌ You can only change the nickname of users **below** you in the role hierarchy.")

        # Bot's ability to manage the member
        if member.top_role.position >= ctx.guild.me.top_role.position:
            return await ctx.send("❌ I can't change the nickname of someone higher than or equal to my role.")

        try:
            old_nick = member.nick or member.name
            await member.edit(nick=new_nick)

            embed = discord.Embed(
                title="✅ Nickname Changed",
                description=f"**{old_nick}**'s nickname changed to **{new_nick}**",
                color=discord.Color.green()
            )
            embed.set_footer(text=f"Changed by {ctx.author}", icon_url=ctx.author.avatar.url if ctx.author.avatar else None)
            await ctx.send(embed=embed)

        except discord.Forbidden:
            await ctx.send("❌ I don't have permission to change that user's nickname.")
        except discord.HTTPException:
            await ctx.send("⚠️ Something went wrong while trying to change the nickname.")

    @change_nickname.error
    async def nick_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("🚫 You don't have permission to use this command.")
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("❗ Usage: `.nick @user new_nickname`")
        elif isinstance(error, commands.BadArgument):
            await ctx.send("❗ Couldn't find that user. Make sure you mention them properly.")
        else:
            await ctx.send("⚠️ An error occurred.")

async def setup(bot):
    await bot.add_cog(NicknameChanger(bot))
