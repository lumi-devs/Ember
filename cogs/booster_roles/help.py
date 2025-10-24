import discord
from discord.ext import commands

class HelpCommand(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name='TAShelp', description='Get help for custom role commands')
    async def tashelp(self, ctx):
        """Get help for TAS role commands. Usage: .TAShelp"""

        embed = discord.Embed(
            title="🎨 TAS Custom Roles Help",
            description="Here's everything you need to know about managing your custom booster role!",
            color=discord.Color.orange()
        )

        embed.add_field(
            name="✨ Create a Role",
            value="`.TAScreate <role name>`\n"
                  "Example: `.TAScreate CoolBooster`\n\n",
            inline=False
        )

        embed.add_field(
            name="🔄 Update Role",
            value="`.TASname <new name>` → Change role name\n"
                  "`.TASprimary #hexcolor` → Change primary color\n"
                  "`.TASsecondary #hexcolor` → Add secondary color\n"
                  "`.TASsecondary remove` → Remove secondary color\n"
                  "`.TASicon <emoji>` → Set role icon\n"
                  "`.TASicon remove` → Remove role icon\n\n",
            inline=False
        )

        embed.add_field(
            name="ℹ️ Role Info",
            value="`.TASrole` → Shows your role details\n\n",
            inline=False
        )

        embed.add_field(
            name="🗑 Delete Role",
            value="`.TASdelete` → Delete your own role\n"
                  "Mods can also delete other users' roles with `.TASdelete @user`\n\n",
            inline=False
        )

        embed.add_field(
            name="🎨 Colors Guide",
            value="• Colors **must begin with `#`** (hex format)\n"
                  "• Example: `#ff0000` (red), `#00ff00` (green), `#7289da` (Discord blurple)\n"
                  "• Secondary color is stored but not applied by Discord (gradient storage)\n\n",
            inline=False
        )

        embed.add_field(
            name="😀 Emoji Guide",
            value="• Use **default emojis** like 😎 🎉 ❤️\n"
                  "• Or **custom emojis**: `<:pepe:123456789>`\n"
                  "• Custom emojis from any server are supported\n"
                  "• Use `.TASicon remove` to remove icon\n\n",
            inline=False
        )

        embed.add_field(
            name="📝 Requirements",
            value="• Must have **booster role** to create custom roles\n"
                  "• One custom role per user\n"
                  "• Role names must follow server rules\n\n",
            inline=False
        )

        embed.add_field(
            name="⚡ Quick Tips",
            value="• Use `.TAScreate YourRoleName` to get started\n"
                  "• Set colors with `.TASprimary #color` and `.TASsecondary #color`\n"
                  "• Add icons with `.TASicon 😎` or `.TASicon <:emoji:123>`\n"
                  "• Contact mods if you encounter any issues\n\n",
            inline=False
        )

        embed.set_footer(
            text=f"Requested by {ctx.author.display_name} | Need help? Ask a mod!",
            icon_url=ctx.guild.icon.url if ctx.guild.icon else None
        )

        # Send as ephemeral message
        await ctx.send(embed=embed, ephemeral=True)

async def setup(bot):
    await bot.add_cog(HelpCommand(bot))