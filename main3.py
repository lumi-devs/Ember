import discord
from discord import app_commands, Embed, Color, TextStyle
from discord.ext import commands
from discord.ui import Modal, TextInput, View
import json
from datetime import datetime, timezone, timedelta
import re
from github import Github, Auth
import threading
import time
from flask import Flask

# --- CONFIG ---
BOT_TOKEN = "***DISCORD_TOKEN_REMOVED***"
GITHUB_TOKEN = "***GITHUB_TOKEN_REMOVED***"
REPO_NAME = "reejit/discord-data"
FILE_PATH = "data.json"
BRANCH = "main"
GUILD_ID = 1388612461703467128
BOOSTER_ROLE_ID = 1388962512820637786
ROLE_BELOW = 1413538764684267725
MOD_IDS = [1296550727652610189, 987654321098765432]

# --- GitHub ---
auth = Auth.Token(GITHUB_TOKEN)
g = Github(auth=auth)
repo = g.get_repo(REPO_NAME)

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
def load_db():
    try:
        file = repo.get_contents(FILE_PATH, ref=BRANCH)
        data = json.loads(file.decoded_content.decode())
        return data, file
    except Exception:
        return {"roles": {}, "blacklist": []}, None

def save_db(data, file=None):
    new_content = json.dumps(data, indent=2)
    if file:
        repo.update_file(
            path=file.path,
            message="update data.json",
            content=new_content,
            sha=file.sha,
            branch=BRANCH
        )
    else:
        repo.create_file(
            path=FILE_PATH,
            message="create data.json",
            content=new_content,
            branch=BRANCH
        )
# ---------------- Slash Commands ----------------

# --- Blacklist ---
@bot.tree.command(name="blacklist", description="Manage the blacklist (mods only)")
@app_commands.describe(
    action="Action: add, remove, or list",
    user="User to add/remove (ignored for list)"
)
async def blacklist(interaction: discord.Interaction, action: str, user: discord.User = None):
    await interaction.response.defer(ephemeral=False)
    if interaction.user.id not in MOD_IDS:
        await interaction.followup.send("🚫 You are not allowed to use this command.")
        return

    db, file = load_db()

    if action.lower() == "list":
        bl_users = db.get("blacklist", [])
        if not bl_users:
            await interaction.followup.send("📜 Blacklist is empty.")
            return
        mentions = [f"<@{uid}>" for uid in bl_users]
        await interaction.followup.send("📜 **Blacklisted Users:**\n" + "\n".join(mentions))
        return

    if not user:
        await interaction.followup.send("❌ You must specify a user for this action.")
        return

    if action.lower() == "add":
        if str(user.id) in db["blacklist"]:
            await interaction.followup.send("⚠️ User already blacklisted.")
            return
        db["blacklist"].append(str(user.id))
        save_db(db, file)
        await interaction.followup.send(f"✅ {user.mention} has been blacklisted.")

    elif action.lower() == "remove":
        if str(user.id) not in db["blacklist"]:
            await interaction.followup.send("⚠️ User not in blacklist.")
            return
        db["blacklist"].remove(str(user.id))
        save_db(db, file)
        await interaction.followup.send(f"✅ {user.mention} has been removed from blacklist.")
    else:
        await interaction.followup.send("❌ Invalid action. Use add, remove, or list.")
    
@bot.tree.command(name="create_role", description="Create a custom booster role")
@app_commands.describe(
    name="Name of your role",
    color="Color hex like #ff0000 (optional)"
)
async def create_role(interaction: discord.Interaction, name: str, color: str = None):
    await interaction.response.defer(ephemeral=False)

    booster_role = interaction.guild.get_role(BOOSTER_ROLE_ID)
    if booster_role not in interaction.user.roles:
        await interaction.followup.send("🚫 Booster role required.")
        return

    db, file = load_db()

    if str(interaction.user.id) in db.get("blacklist", []):
        await interaction.followup.send("🚫 You are blacklisted and cannot use this command.", ephemeral=True)
        return

    if str(interaction.user.id) in db["roles"]:
        await interaction.followup.send("❌ You already have a role.")
        return

    role = None
    try:
        primary_color = Color.from_str(color) if color else Color.default()
    except Exception:
        await interaction.followup.send("❌ Invalid color format. Use hex like #ff0000", ephemeral=True)
        return

    try:
        role = await interaction.guild.create_role(
            name=name,
            colour=primary_color,
            reason=f"Custom booster role for {interaction.user}"
        )

        # Place role just below a specific role
        time.sleep(2)
        role = await interaction.guild.fetch_role(role.id)
        await role.edit(position=interaction.guild.get_role(1413538764684267725).position - 1)
        await interaction.user.add_roles(role)

        # Embed confirmation
        IST = timezone(timedelta(hours=5, minutes=30))
        now_ist = datetime.now(IST)
        sidebar_color = role.color if role.color.value != 0 else Color.light_grey()

        embed = Embed(
            color=sidebar_color,
            timestamp=now_ist
        )
        embed.set_author(
            name="New Booster Role created!",
            icon_url=interaction.user.avatar.url if interaction.user.avatar else None
        )
        embed.add_field(name="Name", value=str(role), inline=True)
        embed.add_field(name="Color", value=color or "Default", inline=True)
        embed.set_footer(
            text="Thanks for boosting",
            icon_url=interaction.guild.icon.url if interaction.guild.icon else None
        )
        embed.set_image(url="https://cdn.discordapp.com/attachments/1393171142436458558/1415000838329339945/GWygmv0.gif?ex=68c19db9&is=68c04c39&hm=2adc901f6d163cc2c5de0f93ddcc0510f9ae77a9964942f16559c4caa36df20f&")
        await interaction.followup.send(embed=embed)

    except Exception as e:
        await interaction.followup.send(f"❌ Role creation failed: {e}", ephemeral=True)
        return

    # --- Save to DB ---
    ist_time = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    db["roles"][str(interaction.user.id)] = {
        "role_id": role.id,
        "name": name,
        "color": color or "default",
        "created_at": ist_time.isoformat(),
        "requested_by": str(interaction.user)  # Username + discriminator
    }
    save_db(db, file)


@bot.tree.command(name="me", description="View your custom role info")
async def me(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    db, file = load_db()

    # Blacklist check
    if str(interaction.user.id) in db.get("blacklist", []):
        await interaction.followup.send("🚫 You are blacklisted and cannot use this command.")
        return

    # Role check
    role_data = db["roles"].get(str(interaction.user.id))
    if not role_data:
        await interaction.followup.send("❌ You don't have a role.")
        return

    role = interaction.guild.get_role(role_data["role_id"])
    if not role:
        await interaction.followup.send("⚠️ Role not found.")
        return

    # --- Handle icon rendering ---
    icon_value = role_data.get("icon_value")
    icon_type = role_data.get("icon_type")

    # Fallback: fetch from actual Discord role if DB has nothing
    if not icon_value:
        if role.unicode_emoji:
            icon_type = "unicode"
            icon_value = role.unicode_emoji
        elif role.display_icon:
            icon_type = "custom"
            icon_value = str(role.display_icon.id)

    # Render the icon properly
    icon_display = "None"
    if icon_value:
        if icon_type == "unicode":
            icon_display = icon_value
        elif icon_type == "custom":
            emoji_obj = interaction.guild.get_emoji(int(icon_value))
            icon_display = str(emoji_obj) if emoji_obj else "(not found)"

    # --- Build embed ---
    embed = discord.Embed(
        title="🌟 Your Role Info",
        color=discord.Color.from_str(role_data["color"]) if role_data.get("color") else discord.Color.blurple()
    )
    embed.add_field(name="👤 Name", value=role.name, inline=True)
    embed.add_field(name="🎨 Primary Color", value=role_data.get("color", "None"), inline=True)
    embed.add_field(name="🌈 Type", value=role_data.get("color_type", "normal"), inline=True)

    # Add secondary color if gradient
    if role_data.get("color_type") == "gradient":
        embed.add_field(name="🎨 Secondary Color", value=role_data.get("secondary_colour", "None"), inline=True)

    embed.add_field(name="🔹 Icon", value=icon_display, inline=True)

    # Footer with timestamp + guild icon
    if interaction.guild.icon:
        embed.set_footer(
            text=f"{interaction.guild.name} • {datetime.now().strftime('%d-%m-%Y %I:%M %p IST')}",
            icon_url=interaction.guild.icon.url
        )
    else:
        embed.set_footer(
            text=f"{interaction.guild.name} • {datetime.now().strftime('%d-%m-%Y %I:%M %p IST')}"
        )

    await interaction.followup.send(embed=embed, ephemeral=True)
    
    

# 📌 /update command (only for editing)

# ---------------- Update Group ----------------
update_group = app_commands.Group(name="update", description="Update your custom role")


# --- Helper ---
def get_role_data(interaction):
    db, file = load_db()
    if str(interaction.user.id) in db.get("blacklist", []):
        return None, None, "🚫 You are blacklisted and cannot use this command."
    role_data = db["roles"].get(str(interaction.user.id))
    if not role_data:
        return None, None, "❌ You don't have a role."
    role = interaction.guild.get_role(role_data["role_id"])
    if not role:
        return None, None, "⚠️ Role not found."
    return db, file, (role, role_data)


# --- /update name ---
@update_group.command(name="name", description="Update your role name")
async def update_name(interaction: discord.Interaction, new_name: str):
    await interaction.response.defer(ephemeral=False)
    db, file, result = get_role_data(interaction)
    if not db:
        await interaction.followup.send(result, ephemeral=True)
        return
    role, role_data = result
    await role.edit(name=new_name)
    role_data["name"] = new_name
    save_db(db, file)
    await interaction.followup.send(f"✅ Role name updated to **{new_name}**!")


# --- /update color (primary) ---
@update_group.command(name="color", description="Update your role primary color")
async def update_color(interaction: discord.Interaction, color: str):
    await interaction.response.defer(ephemeral=False)
    db, file, result = get_role_data(interaction)
    if not db:
        await interaction.followup.send(result, ephemeral=True)
        return
    role, role_data = result
    try:
        primary = Color.from_str(color)
        await role.edit(colour=primary)
        role_data["color"] = color
        if role_data.get("secondary_colour"):
            role_data["color_type"] = "gradient"
        else:
            role_data["color_type"] = "normal"
        save_db(db, file)
        await interaction.followup.send(f"✅ Primary color updated to `{color}`!")
    except Exception as e:
        await interaction.followup.send(f"❌ Invalid color: {e}", ephemeral=True)


# --- /update secondary ---
@update_group.command(name="secondary", description="Update or remove secondary color")
async def update_secondary(interaction: discord.Interaction, secondary: str = ''):
    await interaction.response.defer(ephemeral=False)
    db, file, result = get_role_data(interaction)
    if not db:
        await interaction.followup.send(result, ephemeral=True)
        return
    role, role_data = result
    # Check if primary color exists
    primary_hex = role_data.get("color")
    if not primary_hex or primary_hex.lower() == "default":
        await interaction.followup.send("⚠️ Please set a primary color first using `/update color`.", ephemeral=True)
        return
    try:
        primary_obj = Color.from_str(primary_hex)
    except Exception:
        await interaction.followup.send("❌ Stored primary color is invalid. Please set it again with `/update color`.", ephemeral=True)
        return
    # --- If secondary provided ---
    if secondary.strip():
        try:
            secondary_obj = Color.from_str(secondary)

            # save in DB
            role_data["secondary_colour"] = secondary
            role_data["color_type"] = "gradient"

            # NOTE: Discord roles don’t support gradients natively, so we just store it in DB
            await role.edit(colour=primary_obj, secondary_colour=secondary_obj)

            save_db(db, file)
            await interaction.followup.send(
                f"✅ Secondary color set to `{secondary}` (gradient stored in DB)."
            )
        except Exception as e:
            await interaction.followup.send(f"❌ Invalid secondary color: {e}", ephemeral=True)

    # --- If no secondary (remove gradient) ---
    else:
        if role_data.get("secondary_colour"):
            role_data.pop("secondary_colour", None)
            role_data["color_type"] = "normal"

            await role.edit(colour=primary_obj)  # reset to primary only

            save_db(db, file)
            await interaction.followup.send("ℹ️ Secondary color removed, role is now primary only.")
        else:
            await interaction.followup.send("ℹ️ No secondary color set.", ephemeral=True)


async def set_role_icon(role: discord.Role, emoji_str: str):
    """ Sets the role icon based on input emoji. Returns (icon_type, icon_value) for DB storage. """
    if not emoji_str:
        await role.edit(unicode_emoji=None, display_icon=None)
        return None, None

    # Check if custom emoji like <:smile:1234567890>
    match = re.match(r"<a?:\w+:(\d+)>", emoji_str)
    if match:
        emoji_id = int(match.group(1))
        emoji = discord.utils.get(role.guild.emojis, id=emoji_id)
        if not emoji:
            raise ValueError("Emoji not found in this server.")
        emoji_bytes = await emoji.read()
        await role.edit(display_icon=emoji_bytes)
        return "custom", str(emoji_id)

    # Otherwise assume unicode emoji
    await role.edit(display_icon=emoji_str)
    return "unicode", emoji_str


# --- /update icon ---
@update_group.command(name="icon", description="Update or remove role icon")
async def update_icon(interaction: discord.Interaction, icon: str = None):
    await interaction.response.defer(ephemeral=False)
    db, file, result = get_role_data(interaction)
    if not db:
        await interaction.followup.send(result, ephemeral=True)
        return

    role, role_data = result  # ✅ correctly unpack role + data

    if icon:
        try:
            icon_type, icon_value = await set_role_icon(role, icon)
            role_data["icon_type"] = icon_type
            role_data["icon_value"] = icon_value
            save_db(db, file)
            await interaction.followup.send("✅ Icon updated!")
        except Exception as e:
            await interaction.followup.send(f"❌ Invalid icon: {e}", ephemeral=True)
    else: 
        if role_data.get("icon_value"): 
            await role.edit(display_icon=None) 
            role_data.pop("icon_type", None) 
            role_data.pop("icon_value", None) 
            save_db(db, file) 
            await interaction.followup.send("ℹ️ Icon removed.") 
        else: 
            await interaction.followup.send("ℹ️ No icon set.", ephemeral=True)

# register the group
bot.tree.add_command(update_group)

@bot.tree.command(name="delete_role", description="Delete your custom role (mods can delete others)")
@app_commands.describe(user="User whose role to delete (mods only)")
async def delete_role(interaction: discord.Interaction, user: discord.User = None):
    db, file = load_db()
    await interaction.response.defer(ephemeral=True)
    if str(interaction.user.id) in db.get("blacklist", []):
        await interaction.followup.send("🚫 You are blacklisted and cannot use this command.", ephemeral=True)
        return
    # --- Determine target user ---
    target_user = interaction.user
    if user:
        if interaction.user.id not in MOD_IDS:
            await interaction.followup.send("🚫 You are not allowed to delete other users' roles.", ephemeral=True)
            return
        target_user = user

    role_data = db["roles"].get(str(target_user.id))
    if not role_data:
        await interaction.followup.send(f"❌ {target_user.mention} does not have a custom role.", ephemeral=True)
        return

    role_id = role_data["role_id"]
    role = interaction.guild.get_role(role_id)
    if not role:
        await interaction.followup.send("❌ Role not found.", ephemeral=True)
        return

    # --- Confirmation buttons (skip if mod deleting someone else) ---
    if target_user.id == interaction.user.id:
        delete_btn = discord.ui.Button(label="✅ Yes", style=discord.ButtonStyle.green)
        cancel_btn = discord.ui.Button(label="❌ No", style=discord.ButtonStyle.red)
        view = discord.ui.View(timeout=60)

        async def delete_callback(i: discord.Interaction):
            if i.user.id != interaction.user.id:
                await i.followup.send("❌ Not your button.", ephemeral=True)
                return
            db["roles"].pop(str(target_user.id), None)
            save_db(db, file)
            await role.delete()
            await i.response.send_message("✅ Your role has been deleted.", ephemeral=True)

        async def cancel_callback(i: discord.Interaction):
            if i.user.id != interaction.user.id:
                await i.response.send_message("❌ Not your button.", ephemeral=True)
                return
            await i.response.send_message("❌ Cancelled.", ephemeral=True)

        delete_btn.callback = delete_callback
        cancel_btn.callback = cancel_callback
        view.add_item(delete_btn)
        view.add_item(cancel_btn)

        await interaction.followup.send("⚠️ Are you sure you want to delete your role?",view=view, ephemeral=True)

    else:
        # --- Mod immediate delete ---
        db["roles"].pop(str(target_user.id), None)
        save_db(db, file)
        await role.delete(reason=f"Deleted by mod {interaction.user}")
        await interaction.followup.send(f"✅ Deleted {target_user.mention}'s role.", ephemeral=True)
        
        
        
        
        
        
        
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

