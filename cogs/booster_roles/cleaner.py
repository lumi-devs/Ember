import asyncio
import json
import os
from pathlib import Path
from discord.ext import commands, tasks

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


class CustomRoleCleaner(commands.Cog):
    """Checks every 12h that users still have the booster role; deletes custom role if not."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.cleanup_task.start()

    def cog_unload(self):
        self.cleanup_task.cancel()

    @tasks.loop(hours=6)
    async def cleanup_task(self):
        print("[CustomRoleCleaner] Running booster role cleanup check...")
        db = load_db()
        updated = False

        for guild in self.bot.guilds:
            booster_role_id = int(os.environ.get("BOOSTER_ROLE_ID", 0))
            booster_role = guild.get_role(booster_role_id)
            if not booster_role:
                print(f"⚠️ Booster role not found in guild {guild.name}. Skipping.")
                continue

            for user_id, role_info in list(db.get("roles", {}).items()):
                role_id = role_info.get("role_id")
                member = guild.get_member(int(user_id))
                role = guild.get_role(int(role_id))

                # If user or role doesn't exist, cleanup
                if member is None or role is None:
                    db["roles"].pop(user_id, None)
                    updated = True
                    continue

                # If user lost the booster role, delete their custom role
                if booster_role not in member.roles:
                    try:
                        await role.delete(reason="Lost booster role - auto cleanup")
                        print(f"🗑️ Deleted custom role '{role.name}' (User: {member})")
                    except Exception as e:
                        print(f"⚠️ Failed to delete role {role_id}: {e}")
                    db["roles"].pop(user_id, None)
                    updated = True

        if updated:
            save_db(db)
            print("[CustomRoleCleaner] Database updated after cleanup.")

    @cleanup_task.before_loop
    async def before_cleanup(self):
        await self.bot.wait_until_ready()
        print("[CustomRoleCleaner] Booster cleanup task initialized.")


async def setup(bot: commands.Bot):
    await bot.add_cog(CustomRoleCleaner(bot))
