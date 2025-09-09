
import discord
from discord.ext import commands
import json
import os
from datetime import datetime
from github import Github,Auth
import sys


BOT_TOKEN = "***DISCORD_TOKEN_REMOVED***"
GITHUB_TOKEN = "***GITHUB_TOKEN_REMOVED***"
REPO_NAME = "reejit/discord-data"
FILE_PATH = "data.json"
BLACKLIST_FILE = "blacklist.json"
TARGET_CHANNEL_ID = 1388616879374860339
ROLE_BELOW = 1413538764684267725
GUILD_ID=1388612461703467128
BRANCH = "main"
BOOSTER_ROLE_ID=1393170995157798983

auth=Auth.Token(GITHUB_TOKEN)
g=Github(auth=auth)
repo = g.get_repo(REPO_NAME)

intents = discord.Intents.default()
intents.guilds = True
intents.guild_messages = True
intents.message_content = True
intents.guild_reactions = True
intents.members = True
bot = commands.Bot(command_prefix="A!", intents=intents)



### Initialization-----------------------------------------------------------

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
            message="update roles_db.json",
            content=new_content,
            sha=file.sha,
            branch=BRANCH
        )
    else:
        repo.create_file(
            path=FILE_PATH,
            message="create roles_db.json",
            content=new_content,
            branch=BRANCH
        )

### Bot on ready----------------------------------------------------------
@bot.event
async def on_ready():
    global guild, role_above, role_below

    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        print(f"❌ Guild {GUILD_ID} not found!")
        return

    role_above = guild.get_role(ROLE_BELOW)
    print(f"✅ Logged in as {bot.user}")
    print(f"🌐 Guild: {guild.name}")
    print(f"⬆️ Role Above: {role_above}")






# ---------------- HELP ----------------
@bot.command()
async def helpme(ctx):
    help_text = """
**Role Manager Commands**
`!create <name> <#hex> [emoji]` - Create your custom role.
`!update name <new_name>` - Update your role's name.
`!update color <#hex>` - Update your role's color.
`!update icon <emoji>` - Update your role's icon (boosters only).
`!delete` - Delete your role.
`!blacklist <@user>` - Blacklist a user (mods only).
"""
    await ctx.send(help_text)




async def get_emoji_bytes(emoji: str):
    """Convert a discord.py custom emoji to bytes for display_icon"""
    # If it's a unicode emoji, return None
    if emoji.isascii() or emoji in "😎😂🔥…":  # or just check len == 1
        return None

    # Try to parse <:name:id> format
    if emoji.startswith("<:") or emoji.startswith("<a:"):
        emoji_id = int(emoji.split(":")[-1][:-1])  # get the ID
        custom_emoji = await bot.fetch_emoji(emoji_id)
        return await custom_emoji.url.read()  # read as bytes

    return None


async def create_role(ctx, role_name: str, primary_hex: str = None, secondary_hex: str = None, emoji: str = None):
    guild = ctx.guild

    # Convert hex to discord.Color safely
    try:
        primary_color = discord.Color.from_str(primary_hex) if primary_hex else discord.Color.default()
    except Exception:
        primary_color = discord.Color.default()

    try:
        secondary_color = discord.Color.from_str(secondary_hex) if secondary_hex else discord.Color.default()
    except Exception:
        secondary_color = discord.Color.default()

    # Create role
    icon_bytes = await get_emoji_bytes(emoji)
    role_created = await guild.create_role(
        name=role_name,
        colour=primary_color,
        secondary_colour=secondary_color,
        display_icon=icon_bytes,
        reason=f"Custom role for {ctx.author}"
    )

    return role_created



@bot.command()
async def create(ctx, name: str, colour: str = None, secondary_colour: str = None, emoji: str = None):
    if ctx.guild.get_role(BOOSTER_ROLE_ID) not in ctx.author.roles:
        await ctx.send("Booster role nai hai")
        return
    if ctx.channel.id != TARGET_CHANNEL_ID:
        return

    db, file = load_db()

    # Blacklist check
    if ctx.author.id in db["blacklist"]:
        return await ctx.send("🚫 You are blacklisted from using role commands.")

    # Already has role?
    if str(ctx.author.id) in db["roles"]:
        return await ctx.send("❌ You already created a role. Use `!update` instead.")
    guild = ctx.guild
    role_below = guild.get_role(ROLE_BELOW)
    new_role= await create_role(ctx,name,color,secondary_colour,emoji)
    # Move role between anchors
    try:
        bot_top_role = ctx.guild.get_role(ROLE_BELOW)
        await new_role.edit(position=bot_top_role.position - 1)
    except Exception as e:
        await ctx.send(f"⚠️ Could not move role to correct position.\n\n{e}")

    # Set icon if booster and emoji given
    if emoji:
        try:
            await new_role.edit(display_icon=emoji)
        except Exception:
            await ctx.send("⚠️ Couldn't set emoji as role icon (must be custom server emoji).")

    # Assign role to user
    await ctx.author.add_roles(new_role)

    # Save to DB
    db["roles"][str(ctx.author.id)] = {
        "role_id": new_role.id,
        "name": name,
        "color": colour,
        "icon": emoji if emoji else None,
        "created_at": datetime.utcnow().isoformat(),
        "joined_at": ctx.author.joined_at.isoformat() if ctx.author.joined_at else None
    }
    save_db(db, file)

    await ctx.send(f"✅ Role `{name}` created and assigned!")


# ---------------- ROLE UPDATES ----------------
@bot.group()
async def update(ctx):
    if ctx.channel.id != TARGET_CHANNEL_ID:
        return
    if ctx.invoked_subcommand is None:
        await ctx.send("❌ Invalid update command. Use `!help` for options.")


@update.command()
async def name(ctx, *, new_name: str):
    if ctx.channel.id != TARGET_CHANNEL_ID:
        return
    db, file = load_db()
    role_data = db["roles"].get(str(ctx.author.id))
    if not role_data:
        return await ctx.send("❌ You don't own a role.")

    role = ctx.guild.get_role(role_data["role_id"])
    await role.edit(name=new_name)
    role_data["name"] = new_name
    save_db(db, file)
    await ctx.send(f"✅ Role name updated to `{new_name}`.")


@update.command()
async def color(ctx, new_color: str):
    if ctx.channel.id != TARGET_CHANNEL_ID:
        return
    db, file = load_db()
    role_data = db["roles"].get(str(ctx.author.id))
    if not role_data:
        return await ctx.send("❌ You don't own a role.")

    role = ctx.guild.get_role(role_data["role_id"])
    await role.edit(colour=discord.Colour.from_str(new_color))
    role_data["color"] = new_color
    save_db(db, file)
    await ctx.send(f"✅ Role color updated to `{new_color}`.")


@update.command()
async def icon(ctx, emoji: str):
    db, file = load_db()
    role_data = db["roles"].get(str(ctx.author.id))
    if not role_data:
        return await ctx.send("❌ You don't own a role.")

    role = ctx.guild.get_role(role_data["role_id"])

    # Default: Unicode emoji
    display_icon = emoji  
# Custom emoji handling
    if emoji.startswith("<:") or emoji.startswith("<a:"):
        try:
            emoji_id = int(emoji.split(":")[-1].replace(">", ""))
            discord_emoji = ctx.guild.get_emoji(emoji_id)
            if not discord_emoji:
                return await ctx.send("⚠️ Emoji not found in this server.")
            display_icon = discord_emoji  # use object for Discord API
        except Exception:
            line = sys.exc_info()[2].tb_lineno
            print(line)
            return await ctx.send("⚠️ Invalid custom emoji format.")

    try:
        # Apply to role (uses Emoji object or Unicode string)
        await role.edit(display_icon=display_icon)
        print(display_icon)
        await ctx.send(display_icon)

        # 🔑 Always save only the raw string to JSON
        role_data["icon"] = str(emoji_id)  
        save_db(db, file)

        await ctx.send(f"✅ Role icon updated to {emoji}")
    except Exception as e:
        line = sys.exc_info()[2].tb_lineno
        print(line)
        await ctx.send(f"⚠️ Couldn't update role icon: {e}")


# ---------------- DELETE ROLE ----------------
@bot.command()
async def delete(ctx):
    if ctx.channel.id != TARGET_CHANNEL_ID:
        return
    db, file = load_db()
    role_data = db["roles"].pop(str(ctx.author.id), None)
    if not role_data:
        return await ctx.send("❌ You don't own a role.")

    role = ctx.guild.get_role(role_data["role_id"])
    if role:
        await role.delete()
    save_db(db, file)
    await ctx.send("🗑️ Your custom role has been deleted.")


# ---------------- BLACKLIST ----------------
@bot.command()
@commands.has_permissions(manage_roles=True)
async def blacklist(ctx, member: discord.Member):
    if ctx.channel.id != TARGET_CHANNEL_ID:
        return
    db, file = load_db()
    if member.id not in db["blacklist"]:
        db["blacklist"].append(member.id)
        save_db(db, file)
        await ctx.send(f"🚫 {member.mention} has been blacklisted.")
    else:
        await ctx.send("⚠️ User is already blacklisted.")


bot.run(BOT_TOKEN)
