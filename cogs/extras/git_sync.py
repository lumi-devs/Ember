import discord
from discord.ext import commands, tasks
from github import Github
from pathlib import Path
import os
import base64
import logging

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO = os.environ.get("REPO_NAME")
DATA_FOLDER = Path("data")
SYNC_INTERVAL_MINUTES = 5

logging.basicConfig(level=logging.INFO)

class GitHubSync(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.github = Github(GITHUB_TOKEN)
        self.repo = self.github.get_repo(GITHUB_REPO)
        bot.loop.create_task(self.startup_tasks())

    def cog_unload(self):
        self.sync_loop.cancel()

    async def startup_tasks(self):
        """Run pull first, then start the push loop."""
        await self.bot.wait_until_ready()
        await self.pull_from_github()
        self.sync_loop.start()

    async def pull_from_github(self):
        """Pull all files from GitHub `data/` folder to local."""
        logging.info("⏳ Pulling data from GitHub...")
        try:
            contents = self.repo.get_contents("data")
            DATA_FOLDER.mkdir(exist_ok=True)
            for file_content in contents:
                file_path = DATA_FOLDER / file_content.name
                file_path.write_bytes(base64.b64decode(file_content.content))
                logging.info(f"⬇ Pulled {file_content.name} from GitHub")
            logging.info("✅ Pull from GitHub completed successfully.")
        except Exception as e:
            logging.error(f"❌ Failed to pull from GitHub: {e}")


    async def sync_to_github(self):
        try:
            DATA_FOLDER.mkdir(exist_ok=True)
            for file_path in DATA_FOLDER.iterdir():
                if not file_path.is_file():
                    continue

                github_path = f"data/{file_path.name}"
                local_content = file_path.read_bytes()

                try:
                    existing_file = self.repo.get_contents(github_path)
                    # Decode GitHub content to bytes
                    remote_bytes = base64.b64decode(existing_file.content.encode())
                    if remote_bytes != local_content:
                        # Only update if content differs
                        self.repo.update_file(
                            github_path,
                            f"Update {file_path.name}",
                            local_content,
                            existing_file.sha
                        )
                        logging.info(f"♻ Updated {file_path.name} on GitHub")
                    else:
                        logging.info(f"✅ {file_path.name} unchanged, skipping commit")
                except Exception:
                    # File doesn’t exist → create it
                    self.repo.create_file(
                        github_path,
                        f"Add {file_path.name}",
                        local_content
                    )
                    logging.info(f"➕ Created {file_path.name} on GitHub")
        except Exception as e:
            logging.error(f"❌ Failed to sync to GitHub: {e}")



    @tasks.loop(minutes=SYNC_INTERVAL_MINUTES)
    async def sync_loop(self):
        """Periodic push to GitHub every N minutes."""
        await self.sync_to_github()

    @commands.command(name="gsync")
    @commands.is_owner()
    async def manual_sync(self, ctx: commands.Context):
        """Manually push local data folder to GitHub."""
        await self.sync_to_github()
        await ctx.send("✅ Data folder synced to GitHub.")

    @commands.command(name="gpull")
    @commands.is_owner()
    async def manual_pull(self, ctx: commands.Context):
        """Manually pull data from GitHub to local folder."""
        await self.pull_from_github()
        await ctx.send("✅ Data folder pulled from GitHub.")

async def setup(bot: commands.Bot):
    await bot.add_cog(GitHubSync(bot))
