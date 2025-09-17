import discord
from discord import app_commands, Embed, Color, TextStyle
from discord.ext import commands
from discord.ui import Modal, TextInput, View
import json
from datetime import datetime, timezone, timedelta
import re

import threading
import time
from flask import Flask

# --- CONFIG ---


# --- GitHub ---


intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

app = Flask(__name__)

@app.route('/')
def hello_world():
    return "Hello, World! Flask is running alongside Discord bot 🚀"

def run_flask():
    app.run(host="0.0.0.0", port=5000, debug=False)

# ---------------- GitHub DB ----------------

# ---------------- Slash Commands ----------------

# --- Blacklist ---




    

# 📌 /update command (only for editing)

# ---------------- Update Group ----------------



        
        
        
        
        
        
@bot.tree.command(name="help", description="Get help for custom role commands")
async def help_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    embed = discord.Embed(
    title="📘 Custom Roles Help",
    description="Here’s everything you need to know about managing your custom booster role!",
    color=discord.Color.blurple()
    )

    embed.add_field(
    name="✨ Create a Role",
    value="`/create_role name:<name> color:<#hex>`\n"
          "Example: `/create_role name:CoolBooster color:#ff0000`\n\n",
    inline=False
    )

    embed.add_field(
    name="🛠 Update Role",
    value="`/update name <new name>` → Change role name\n"
          "`/update color <#hex>` → Change primary color\n"
          "`/update secondary <#hex>` → Add or update secondary color\n"
          "`/update icon <emoji>` → Set or update role icon\n\n",
    inline=False
    )

    embed.add_field(
    name="ℹ️ Role Info",
    value="`/me` → Shows your role details (name, colors, icon, etc.)\n\n",
    inline=False
    )
    embed.add_field(
    name="🗑 Delete Role",
    value="`/delete_role` → Delete your own role\n"
          "Mods can also delete other users’ roles with `/delete_role user:<@user>`\n\n",
    inline=False
    )

    embed.add_field(
    name="🚫 Blacklist (Mods only)",
    value="`/blacklist add <@user>` → Add to blacklist\n"
          "`/blacklist remove <@user>` → Remove from blacklist\n"
          "`/blacklist list` → Show all blacklisted users\n\n",
    inline=False
    )

    embed.add_field(
    name="🎨 Colors Guide",
    value="• Colors **must begin with `#`** (hex format)\n"
          "• Example: `#ff0000` (red), `#00ff00` (green), `#7289da` (Discord blurple)\n"
          "• You can add a **secondary color** for gradient storage (visual only, not applied in Discord).\n\n",
    inline=False
    )

    embed.add_field(
    name="😀 Emoji Guide",
    value="• You can use **default emojis** like 😎 🎉 ❤️\n"
          "• Or **custom emojis from this server**: <:cutepepe:1400771649728548904>\n"
          "• If you want a custom emoji added, please ask a mod.\n\n",
    inline=False
    )

    embed.add_field(
    name="🧹 Removing Gradient & Icon",
    value="• To **remove gradient**, run `/update secondary` and leave it empty.\n"
          "• To **remove role icon/emoji**, run `/update icon` and leave it empty.\n\n",
    inline=False
    )

    embed.set_footer(
    text=f"Requested by {interaction.user.display_name} | Need help? Ask a mod!",
    icon_url=interaction.guild.icon.url if interaction.guild.icon else None
    )

    await interaction.followup.send(embed=embed)




# ---------------- On Ready ----------------
@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        await bot.change_presence(
        status=discord.Status.dnd,
        activity=discord.Game("Managing custom roles 🚀")
        )
        print(f"✅ Synced {len(synced)} commands")
    except Exception as e:
        print(e)
    print(f"✅ Logged in as {bot.user}")


if __name__ == "__main__":
    # Start Flask in a background thread
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()

    # Run Discord bot
    bot.run(BOT_TOKEN)

