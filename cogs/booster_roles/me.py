import discord
from discord.ext import commands
from datetime import datetime
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

class RoleInfo(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name='TASrole', description='View your custom role info')
    async def tasrole(self, ctx):
        """View your custom role info. Usage: .TASrole"""
        
        db = load_db()

        # --- Blacklist check ---
        if str(ctx.author.id) in db.get("blacklist", []):
            await ctx.send("🚫 You are blacklisted and cannot use this command.", ephemeral=True)
            return

        # --- Role check ---
        role_data = db["roles"].get(str(ctx.author.id))
        if not role_data:
            await ctx.send("❌ You don't have a role.", ephemeral=True)
            return

        role = ctx.guild.get_role(role_data["role_id"])
        if not role:
            await ctx.send("⚠️ Role not found.", ephemeral=True)
            return

        # --- Build embed ---
        embed = discord.Embed(
            title="🌟 Your Role Info",
            color=discord.Color.orange()
        )
        embed.add_field(name="👤 Role Name", value=role.name, inline=True)
        embed.add_field(name="🆔 Role ID", value=role.id, inline=True)
        embed.add_field(name="🎨 Color", value=str(role.color), inline=True)
        
        # Add creation date if available
        if role_data.get("created_at"):
            embed.add_field(name="📅 Created", value=role_data["created_at"][:10], inline=True)
        
        embed.add_field(name="👤 Created For", value=ctx.author.mention, inline=True)

        # --- Footer with timestamp + guild icon ---
        timestamp = datetime.now().strftime('%d-%m-%Y %I:%M %p IST')
        if ctx.guild.icon:
            embed.set_footer(text=f"{ctx.guild.name} • {timestamp}", icon_url=ctx.guild.icon.url)
        else:
            embed.set_footer(text=f"{ctx.guild.name} • {timestamp}")

        await ctx.send(embed=embed, ephemeral=True)

async def setup(bot):
    await bot.add_cog(RoleInfo(bot))