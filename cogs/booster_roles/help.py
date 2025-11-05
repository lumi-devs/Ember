import discord
from discord import app_commands
from discord.ext import commands

class HelpCommand(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="tashelp", description="Get help for TAS custom role commands")
    async def tashelp(self, interaction: discord.Interaction):
        """Get help for TAS role commands. Usage: /tashelp"""

        embed = discord.Embed(
            title="🎨 TAS Custom Roles Help",
            description="Here's everything you need to know about managing your custom booster role!",
            color=discord.Color.orange()
        )

        embed.add_field(
            name="✨ Create a Role",
            value="`/tascreate <role name>`\n"
                  "Example: `/tascreate CoolBooster`\n\n",
            inline=False
        )

        embed.add_field(
            name="🔄 Update Role",
            value="`/tasname <new name>` → Change role name\n"
                  "`/tasprimary #hexcolor` → Change primary color\n"
                  "`/tassecondary #hexcolor` → Add secondary color\n"
                  "`/tassecondary remove` → Remove secondary color\n"
                  "`/tasicon <emoji>` → Set role icon\n"
                  "`/tasicon remove` → Remove role icon\n\n",
            inline=False
        )

        embed.add_field(
            name="ℹ️ Role Info",
            value="`/tasrole` → Shows your role details\n\n",
            inline=False
        )

        embed.add_field(
            name="🗑 Delete Role",
            value="`/tasdelete` → Delete your own role\n"
                  "Mods can also delete other users' roles with `/tasdelete @user`\n\n",
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
                  "• Use `/tasicon remove` to remove icon\n\n",
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
            value="• Use `/tascreate YourRoleName` to get started\n"
                  "• Set colors with `/tasprimary #color` and `/tassecondary #color`\n"
                  "• Add icons with `/tasicon 😎` or `/tasicon <:emoji:123>`\n"
                  "• Contact mods if you encounter any issues\n\n",
            inline=False
        )

        embed.set_footer(
            text=f"Requested by {interaction.user.display_name} | Need help? Ask a mod!",
            icon_url=interaction.guild.icon.url if interaction.guild.icon else None
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)

async def setup(bot):
    await bot.add_cog(HelpCommand(bot))
