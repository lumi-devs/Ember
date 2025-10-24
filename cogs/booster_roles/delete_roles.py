import discord
import os
from discord.ext import commands
from pathlib import Path
import json

# --- Local DB helpers ---
DATA_FILE = Path("data/custom_roles.json")
DATA_FILE.parent.mkdir(parents=True, exist_ok=True)

def load_db():
    if DATA_FILE.exists():
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"roles": {}, "blacklist": []}

def save_db(db):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=4)

class CustomRoles(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.mod_ids = [int(x) for x in os.environ.get("MOD_IDS", "").split(",") if x]

    @commands.command(name='TASdelete', description='Delete your custom role (mods can delete others)')
    async def tasdelete(self, ctx, user: discord.Member = None):
        """Delete your custom role. Usage: .TASdelete [@user] (mods only)"""
        
        db = load_db()

        # blacklist check
        if str(ctx.author.id) in db.get("blacklist", []):
            await ctx.send("🚫 You are blacklisted and cannot use this command.")
            return

        target_user = ctx.author
        if user:
            if ctx.author.id not in self.mod_ids:
                await ctx.send("🚫 You are not allowed to delete other users' roles.")
                return
            target_user = user

        role_data = db["roles"].get(str(target_user.id))
        if not role_data:
            await ctx.send(f"❌ {target_user.mention} does not have a custom role.")
            return

        role = ctx.guild.get_role(role_data["role_id"])
        if not role:
            await ctx.send("❌ Role not found.")
            return

        # Delete role immediately without confirmation
        db["roles"].pop(str(target_user.id), None)
        save_db(db)
        await role.delete(reason=f"Deleted by {ctx.author}")
        
        if target_user.id == ctx.author.id:
            await ctx.send("✅ Your role has been deleted.")
        else:
            await ctx.send(f"✅ Deleted {target_user.mention}'s role.")

async def setup(bot):
    await bot.add_cog(CustomRoles(bot))