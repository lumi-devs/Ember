import discord
from discord.ext import commands

class NicknameChanger(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name='nick')
    @commands.has_permissions(manage_nicknames=True)
    async def change_nickname(self, ctx, member: discord.Member, *, new_nick: str = None):
        """Change or reset a member's nickname if they are lower in role hierarchy."""

        # Prevent changing own or bot's nickname
        if member == ctx.author:
            embed = discord.Embed(
                description="⚠️ You can't change your **own nickname** with this command.",
                color=discord.Color.orange()
            )
            return await ctx.send(embed=embed)

        if member == ctx.guild.me:
            embed = discord.Embed(
                description="⚠️ You can't change **my nickname** with this command.",
                color=discord.Color.orange()
            )
            return await ctx.send(embed=embed)

        # Role hierarchy check (user vs target)
        if member.top_role.position >= ctx.author.top_role.position:
            embed = discord.Embed(
                description="❌ You can only change the nickname of users **below** you in the role hierarchy.",
                color=discord.Color.red()
            )
            return await ctx.send(embed=embed)

        # Role hierarchy check (bot vs target)
        if member.top_role.position >= ctx.guild.me.top_role.position:
            embed = discord.Embed(
                description="❌ I can't change the nickname of someone **equal to or higher** than my role.",
                color=discord.Color.red()
            )
            return await ctx.send(embed=embed)

        try:
            old_nick = member.nick or member.name
            if new_nick:
                await member.edit(nick=new_nick)
                embed = discord.Embed(
                    title="✅ Nickname Changed",
                    description=f"**{old_nick}**'s nickname changed to **{new_nick}**",
                    color=discord.Color.green()
                )
            else:
                await member.edit(nick=None)
                embed = discord.Embed(
                    title="✅ Nickname Reset",
                    description=f"**{old_nick}**'s nickname has been **reset** to their username.",
                    color=discord.Color.green()
                )

            embed.set_footer(text=f"Changed by {ctx.author}", icon_url=ctx.author.avatar.url if ctx.author.avatar else None)
            await ctx.send(embed=embed)

        except discord.Forbidden:
            embed = discord.Embed(
                description="❌ I don't have permission to change that user's nickname.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)

        except discord.HTTPException:
            embed = discord.Embed(
                description="⚠️ Something went wrong while trying to change the nickname.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)

    @change_nickname.error
    async def nick_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            embed = discord.Embed(
                description="🚫 You don't have permission to use this command.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)

        elif isinstance(error, commands.MissingRequiredArgument):
            embed = discord.Embed(
                description="❗ Usage:\n`.nick @user` → Reset nickname\n`.nick @user new_nick` → Change nickname",
                color=discord.Color.orange()
            )
            await ctx.send(embed=embed)

        elif isinstance(error, commands.BadArgument):
            embed = discord.Embed(
                description="❗ Couldn't find that user. Make sure you **mention them properly**.",
                color=discord.Color.orange()
            )
            await ctx.send(embed=embed)

        else:
            embed = discord.Embed(
                description="⚠️ An unexpected error occurred.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(NicknameChanger(bot))
