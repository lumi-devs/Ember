import discord
from discord import app_commands, Embed, Color
from discord.ext import commands
import json
from datetime import datetime, timezone, timedelta
import time
from github import Github, Auth
from discord import app_commands, Interaction, Color, TextStyle
from discord.ui import Modal, TextInput, View, button



BOT_TOKEN = "***DISCORD_TOKEN_REMOVED***"
GITHUB_TOKEN = "***GITHUB_TOKEN_REMOVED***"
REPO_NAME = "reejit/discord-data"
FILE_PATH = "data.json"
BRANCH = "main"
GUILD_ID = 1388612461703467128
BOOSTER_ROLE_ID = 1393170995157798983
ROLE_BELOW = 1413538764684267725

auth = Auth.Token(GITHUB_TOKEN)
g = Github(auth=auth)
repo = g.get_repo(REPO_NAME)

intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)


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
@bot.tree.command(name="create_role", description="Create a custom role")
@app_commands.describe(
    name="Name of your role",
    color="Color hex like #ff0000 (optional)",
    secondary_color = "Secondary color if you want a gradient."
)
async def create_role(interaction: discord.Interaction, name: str, color: str = None, secondary_color: str = None):
    # Booster role check
    role=None
    await interaction.response.defer(ephemeral=False)
    booster_role = interaction.guild.get_role(BOOSTER_ROLE_ID)
    if booster_role not in interaction.user.roles:
        await interaction.followup.send("🚫 Booster role required.")
        return

    db, file = load_db()
    highest_bot_role = max(interaction.guild.me.roles)
    print(highest_bot_role)
    if str(interaction.user.id) in db["roles"]:
        await interaction.followup.send("❌ You already have a role.")
        return
 
    color_type="normal"
    if color == None:
        color=discord.Color.default()
    elif color!=None and secondary_color==None:
        print(1)
        try:
            primary_color = discord.Color.from_str(color)
            role = await interaction.guild.create_role(name=name, colour=primary_color,reason=f"Custom role for {interaction.user}")
            await role.edit(position=interaction.guild.get_role(ROLE_BELOW).position-1)
            await interaction.user.add_roles(role)
            
            embed = Embed(
                   title="✅ Role Created!",
                      description=f"Your role `{role.name}` has been created successfully.",
                      color=role.color  # Use the role's color
            )
            embed.add_field(name="Name", value=str(role), inline=True)
            embed.add_field(name="Colour Hex", value=str(color), inline=True)
            embed.set_footer(text=f"Created for {interaction.user}\nThanks for boosting!")
            await interaction.followup.send(embed=embed)
            role = interaction.guild.get_role(role.id)
            print(role.position)
            
        except Exception as e:
            print(e)
            await interaction.followup.send("Please enter color name in correct format.", ephemeral=True)

    elif color!=None and secondary_color != None:
        color_type="gradient"
        print(2)
        try:
            primary_color = discord.Color.from_str(color)
            secondary_color = discord.Color.from_str(secondary_color)
            role = await interaction.guild.create_role(name=name, colour=primary_color,reason=f"Custom role for {interaction.user}") 
            await role.edit(position=interaction.guild.get_role(ROLE_BELOW).position-1)
            await interaction.user.add_roles(role)
            role = interaction.guild.get_role(role.id)
            role=await role.edit(colour=primary_color,secondary_color=secondary_color)
            embed = Embed(
                   title="✅ Role Created!",
                      description=f"Your role `{role.name}` has been created successfully.",
                      color=role.color  # Use the role's color
            )
            embed.add_field(name="Name", value=str(role), inline=True)
            embed.add_field(name="Colour Hex", value=str(color), inline=True)
            embed.add_field(name="Secondary Color Hex", value=str(color), inline=True)
            embed.set_footer(text=f"Created for {interaction.user}\nThanks for boosting!")
            await interaction.followup.send(embed=embed)
            
        except Exception as e:
            print(e)
            await interaction.followup.send("Please enter secondary color name in correct format.", ephemeral=True)
    else:
        print("hmmmm")



    ist_time = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    db["roles"][str(interaction.user.id)] = {
        "role_id": role.id,
        "name": name,
        "color_type": color_type,
        "color": color,
        "created_at": ist_time.isoformat()
    }
    if secondary_color:
        db["roles"][str(interaction.user.id)]["secondary_color"] = str(secondary_color)
    save_db(db, file)

@bot.tree.command(name="update_role", description="Update your custom role")
async def update_role(interaction: discord.Interaction):
    db, file = load_db()
    role_data = db["roles"].get(str(interaction.user.id))
    if not role_data:
        await interaction.response.send_message("❌ You don't have a role.", ephemeral=True)
        return

    role_name = role_data["name"]
    primary_color = role_data["color"]
    secondary_color = role_data.get("secondary_color", "")

    # Modal
    modal = Modal(title="Update Your Role")

    name_input = TextInput(
        label="Role Name",
        style=TextStyle.short,
        default=role_name,
        required=False,
        max_length=100
    )
    modal.add_item(name_input)

    color_input = TextInput(
        label="Primary Color Hex (#RRGGBB)",
        style=TextStyle.short,
        default=primary_color,
        required=False
    )
    modal.add_item(color_input)

    secondary_input = TextInput(
        label="Secondary Color Hex (#RRGGBB)",
        style=TextStyle.short,
        default=secondary_color if secondary_color else "",
        required=False
    )
    modal.add_item(secondary_input)

    async def modal_callback(i: Interaction):
        role = interaction.guild.get_role(role_data["role_id"])
        if not role:
            await i.response.send_message("⚠️ Role not found.", ephemeral=True)
            return

        kwargs = {}
        updated = False

        # --- Name update ---
        new_name = name_input.value.strip()
        if new_name and new_name != role_data["name"]:
            kwargs["name"] = new_name
            role_data["name"] = new_name
            updated = True

        # --- Primary color update ---
        try:
            new_primary = color_input.value.strip()
            if new_primary and new_primary != role_data["color"]:
                kwargs["colour"] = Color.from_str(new_primary)
                role_data["color"] = new_primary
                updated = True
        except Exception:
            await i.response.send_message("❌ Primary color is invalid. Try again with #RRGGBB.", ephemeral=True)
            return

        # --- Secondary color logic ---
        sec_value = secondary_input.value.strip()
        if sec_value:  # user entered a secondary color
            try:
                Color.from_str(sec_value)  # validate
            except Exception:
                await i.response.send_message("❌ Secondary color is invalid. Try again with #RRGGBB.", ephemeral=True)
                return

            kwargs["colour"] = Color.from_str(role_data["color"])  # primary always included
            kwargs["secondary_colour"] = Color.from_str(sec_value)
            role_data["secondary_color"] = sec_value
            role_data["color_type"] = "gradient"
            updated = True

        else:  # empty input
            if "secondary_color" in role_data and role_data["secondary_color"]:
                # user removed existing secondary color
                kwargs["colour"] = Color.from_str(role_data["color"])
                kwargs.pop("secondary_colour", None)
                role_data.pop("secondary_color", None)
                role_data["color_type"] = "normal"
                updated = True

        # --- Apply changes ---
        if updated and kwargs:
            await role.edit(**kwargs)
            save_db(db, file)
            await i.response.send_message("✅ Your role changes were saved!", ephemeral=True)
        else:
            await i.response.send_message("ℹ️ No changes were made.", ephemeral=True)

    modal.on_submit = modal_callback
    await interaction.response.send_modal(modal)

@bot.tree.command(name="delete_role", description="Delete your custom role")
async def delete_role(interaction: discord.Interaction):
    db, file = load_db()
    role_data = db["roles"].get(str(interaction.user.id))
    if not role_data:
        await interaction.response.send_message("❌ You don't have a role.", ephemeral=True)
        return

    role_id = role_data["role_id"]
    role = interaction.guild.get_role(role_id)
    if not role:
        await interaction.response.send_message("❌ Role not found.", ephemeral=True)
        return

    delete_btn = discord.ui.Button(label="✅ Yes", style=discord.ButtonStyle.green)
    cancel_btn = discord.ui.Button(label="❌ No", style=discord.ButtonStyle.red)
    view = discord.ui.View(timeout=60)

    async def delete_callback(i: discord.Interaction):
        if i.user.id != interaction.user.id:  # only allow original user
            await i.response.send_message("❌ You can’t use this button.", ephemeral=True)
            return

        db["roles"].pop(str(interaction.user.id), None)
        save_db(db, file)
        await role.delete()
        await i.response.send_message(content="✅ Role was deleted.",ephemeral=True)  # ✅ FIX

    async def cancel_callback(i: discord.Interaction):
        if i.user.id != interaction.user.id:
            await i.response.send_message("❌ You can’t use this button.", ephemeral=True)
            return

        await i.response.send_message(content="❌ Okay! The role lives another day.", ephemeral=True)  # ✅ FIX

    delete_btn.callback = delete_callback
    cancel_btn.callback = cancel_callback
    view.add_item(delete_btn)
    view.add_item(cancel_btn)

    await interaction.response.send_message(
        "⚠️ Are you sure you want to delete your role?",
        view=view,
        ephemeral=True
    )

# ---------------- On Ready ----------------
@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} commands")
    except Exception as e:
        print(e)
    print(f"✅ Logged in as {bot.user}")


bot.run(BOT_TOKEN)

