import discord
from discord.ext import commands, tasks
from github import Github, GithubException
from pathlib import Path
import os
import base64
import logging
import asyncio
from typing import Optional

# Configuration
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO = os.environ.get("REPO_NAME")
DATA_FOLDER = Path("data")
SYNC_INTERVAL_SECONDS = 45

logging.basicConfig(level=logging.INFO)


class GitHubSync(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.github: Optional[Github] = None
        self.repo = None

        if GITHUB_TOKEN and GITHUB_REPO:
            bot.loop.create_task(self.startup_tasks())
        else:
            logging.warning("GitHub credentials not set. Sync disabled.")

    async def startup_tasks(self):
        """Logic to run when the cog loads."""
        await self.bot.wait_until_ready()
        await self.initialize_github()

        if self.repo:
            # 1. Pull any files that exist on GitHub but are missing locally
            logging.info("Checking for missing local files...")
            await self.download_missing_files()

            # 2. Start the periodic upload loop
            self.upload_loop.start()

    async def initialize_github(self):
        """Initialize GitHub connection."""
        try:
            self.github = await asyncio.to_thread(lambda: Github(GITHUB_TOKEN))
            self.repo = await asyncio.to_thread(lambda: self.github.get_repo(GITHUB_REPO))
            print("✅ GitHub connection initialized")
        except Exception as e:
            logging.error(f"❌ Failed to initialize GitHub: {e}")
            self.repo = None

    def get_all_files(self):
        """Get all valid files in data directory, skipping temp DB files."""
        all_files = []
        if not DATA_FOLDER.exists():
            return all_files

        for root, dirs, files in os.walk(DATA_FOLDER):
            for file in files:
                # Skip SQLite temporary files to prevent "Database is locked" errors
                if file.endswith(("-journal", "-wal", "-shm")) or file.startswith("."):
                    continue
                full_path = Path(root) / file
                all_files.append(full_path)
        return all_files

    async def download_missing_files(self):
        """Pull files from GitHub only if they do not exist locally."""
        if not self.repo: return

        def pull_logic():
            try:
                # Get all files currently in the 'data/' folder on GitHub
                contents = self.repo.get_contents("data")
                while contents:
                    file_content = contents.pop(0)
                    if file_content.type == "dir":
                        contents.extend(self.repo.get_contents(file_content.path))
                    else:
                        local_path = Path(file_content.path)
                        # Mirror only: Pull if missing
                        if not local_path.exists():
                            local_path.parent.mkdir(parents=True, exist_ok=True)
                            with open(local_path, "wb") as f:
                                f.write(file_content.decoded_content)
                            print(f"  📥 Recovered: {local_path}")
            except Exception as e:
                logging.error(f"❌ Pull logic error: {e}")

        await asyncio.to_thread(pull_logic)

    def upload_all_files_sync(self):
        """Push local changes to GitHub. Overwrites GitHub with local content."""
        if not self.repo: return

        DATA_FOLDER.mkdir(exist_ok=True)
        all_files = self.get_all_files()

        for file_path in all_files:
            try:
                rel_path = file_path.relative_to(DATA_FOLDER)
                github_path = f"data/{rel_path}"

                with open(file_path, 'rb') as f:
                    content_bytes = f.read()

                content_base64 = base64.b64encode(content_bytes).decode('utf-8')

                try:
                    # Get existing file to check SHA and content
                    existing = self.repo.get_contents(github_path, ref="main")

                    # Compare content (stripped of GitHub newlines)
                    online_content = existing.content.replace('\n', '')
                    if online_content == content_base64:
                        continue  # Skip if no changes

                    self.repo.update_file(
                        github_path,
                        f"Update {rel_path}",
                        content_base64,
                        existing.sha,
                        branch="main"
                    )
                except GithubException as e:
                    if e.status == 404:
                        # File not on GitHub, create it
                        self.repo.create_file(
                            github_path,
                            f"Add {rel_path}",
                            content_base64,
                            branch="main"
                        )
                        print(f"  ✅ Created on Git: {rel_path}")
                    else:
                        logging.error(f"❌ SHA/API Error for {rel_path}: {e}")
            except Exception as e:
                logging.error(f"❌ File Access Error {file_path}: {e}")

    async def upload_all_files(self):
        await asyncio.to_thread(self.upload_all_files_sync)

    @tasks.loop(seconds=SYNC_INTERVAL_SECONDS)
    async def upload_loop(self):
        if self.repo:
            await self.upload_all_files()

    @upload_loop.before_loop
    async def before_upload_loop(self):
        await self.bot.wait_until_ready()

    @commands.command(name="upload")
    @commands.is_owner()
    async def manual_upload(self, ctx: commands.Context):
        """Push all local data to GitHub immediately."""
        if not self.repo:
            return await ctx.send("❌ GitHub not connected.")

        await ctx.send("⏳ Pushing ALL files to GitHub...")

        # Checkpoint AFK data if applicable
        afk_cog = self.bot.get_cog("AFK")
        if afk_cog and hasattr(afk_cog, 'force_checkpoint'):
            await afk_cog.force_checkpoint()

        await self.upload_all_files()
        await ctx.send("✅ Mirror push complete!")

    @commands.command(name="gitpull")
    @commands.is_owner()
    async def manual_pull(self, ctx: commands.Context):
        """Force pull all files from GitHub (overwrites local)."""
        if not self.repo:
            return await ctx.send("❌ GitHub not connected.")

        await ctx.send("⏳ Force pulling data from GitHub (Overwriting local)...")

        def force_pull():
            contents = self.repo.get_contents("data")
            while contents:
                file_content = contents.pop(0)
                if file_content.type == "dir":
                    contents.extend(self.repo.get_contents(file_content.path))
                else:
                    local_path = Path(file_content.path)
                    local_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(local_path, "wb") as f:
                        f.write(file_content.decoded_content)

        await asyncio.to_thread(force_pull)
        await ctx.send("✅ Local files updated from GitHub!")

    @commands.command(name="gitlist")
    @commands.is_owner()
    async def list_files(self, ctx: commands.Context):
        """Show files tracked for syncing."""
        all_files = self.get_all_files()
        if not all_files:
            return await ctx.send("📁 No files currently in data folder.")

        embed = discord.Embed(title="📁 Managed Data Files", color=discord.Color.blue())
        file_list = [f"`{f.relative_to(DATA_FOLDER)}`" for f in all_files[:15]]

        if len(all_files) > 15:
            file_list.append(f"... and {len(all_files) - 15} more")

        embed.add_field(name=f"Total: {len(all_files)}", value="\n".join(file_list))
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(GitHubSync(bot))