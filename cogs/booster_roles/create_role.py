import asyncio
from datetime import datetime, timezone, timedelta
import os
import json
from pathlib import Path
import discord
from discord.ext import commands
from discord import Color, Embed

# Ensure data folder exists
DATA_FILE = Path("data/custom_roles.json")
DATA_FILE.parent.mkdir(parents=True, exist_ok=True)

# --- Load DB ---
def load_db():
    if DATA_FILE.exists():
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    else:
        return {"roles": {}, "blacklist": []}

# --- Save DB ---
def save_db(db):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=4)

class CustomRoles(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name='TAScreate', description='Create a custom booster role')
    async def tascreate(self, ctx, *, name: str):
        """Create a custom booster role. Usage: .TAScreate <role_name>"""
        
        booster_role = ctx.guild.get_role(int(os.environ.get("BOOSTER_ROLE_ID")))
        if booster_role not in ctx.author.roles:
            await ctx.send("🚫 Booster role required.")
            return

        db = load_db()

        if str(ctx.author.id) in db.get("blacklist", []):
            await ctx.send("🚫 You are blacklisted and cannot use this command.")
            return

        if str(ctx.author.id) in db["roles"]:
            await ctx.send("❌ You already have a role.")
            return

        try:
            # Create role with default color
            role = await ctx.guild.create_role(
                name=name,
                reason=f"Custom booster role for {ctx.author}"
            )
            
            # Get the placed above role
            placed_above_role = ctx.guild.get_role(int(os.environ.get("PLACED_ABOVE_ROLE_ID")))
            
            if placed_above_role:
                # Position the new role above the specified role
                await role.edit(position=placed_above_role.position + 1)
            else:
                # Fallback: position below booster role if placed_above_role not found
                await role.edit(position=booster_role.position - 1)

            await ctx.author.add_roles(role)

            # Embed confirmation
            IST = timezone(timedelta(hours=5, minutes=30))
            now_ist = datetime.now(IST)

            embed = Embed(
                color=Color.gold(),
                timestamp=now_ist
            )
            embed.set_author(
                name="New Booster Role created!",
                icon_url=ctx.author.avatar.url if ctx.author.avatar else None
            )
            embed.add_field(name="Role Name", value=name, inline=True)
            embed.add_field(name="Created For", value=ctx.author.mention, inline=True)
            embed.set_footer(
                text="Thanks for boosting",
                icon_url=ctx.guild.icon.url if ctx.guild.icon else None
            )
            embed.set_image(url="https://cdn.discordapp.com/attachments/1393171142436458558/1415000838329339945/GWygmv0.gif")

            await ctx.send(embed=embed)

        except Exception as e:
            print(e)
            await ctx.send(f"❌ Role creation failed: {e}")
            return

        # --- Save to DB locally ---
        ist_time = datetime.now(timezone(timedelta(hours=5, minutes=30)))
        db["roles"][str(ctx.author.id)] = {
            "role_id": role.id,
            "name": name,
            "created_at": ist_time.isoformat(),
            "requested_by": str(ctx.author)
        }

        try:
            save_db(db)
            print("Saved to data/custom_roles.json")
        except Exception as e:
            print(f"Failed to save DB: {e}")

async def setup(bot):
    await bot.add_cog(CustomRoles(bot))