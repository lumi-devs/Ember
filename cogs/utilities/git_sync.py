import discord
from discord.ext import commands, tasks
from github import Github
from pathlib import Path
import os
import base64
import logging
import asyncio
from typing import Optional

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
        """Start the upload loop."""
        await self.bot.wait_until_ready()
        await self.initialize_github()
        if self.repo:
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
        """Get all files in data directory recursively."""
        all_files = []
        for root, dirs, files in os.walk(DATA_FOLDER):
            for file in files:
                full_path = Path(root) / file
                all_files.append(full_path)
        return all_files

    def upload_all_files_sync(self):
        """Upload ALL files to GitHub - Synchronous version for to_thread."""
        if not self.repo:
            return
            
        DATA_FOLDER.mkdir(exist_ok=True)
        all_files = self.get_all_files()
        
        if not all_files:
            return
            
        print(f"⬆ Uploading {len(all_files)} files to GitHub...")
        
        for file_path in all_files:
            try:
                # Get relative path for GitHub
                rel_path = file_path.relative_to(DATA_FOLDER)
                github_path = f"data/{rel_path}"
                
                # Read file
                with open(file_path, 'rb') as f:
                    content_bytes = f.read()
                
                # Create/Update file on GitHub
                try:
                    # Try to get existing file
                    existing = self.repo.get_contents(github_path)
                    # Just update it
                    self.repo.update_file(
                        github_path,
                        f"Update {rel_path}",
                        content_bytes,
                        existing.sha
                    )
                    #logging.info(f"  ✅ Updated: {rel_path}")
                except Exception:
                    # Create new file
                    self.repo.create_file(
                        github_path,
                        f"Add {rel_path}",
                        content_bytes
                    )
                    print(f"  ✅ Created: {rel_path}")
                    
            except Exception as e:
                print(f"  ❌ Failed {file_path}: {e}")
        
        print("✅ All files uploaded!")

    async def upload_all_files(self):
        """Async wrapper for upload_all_files_sync."""
        if not self.repo:
            return
        await asyncio.to_thread(self.upload_all_files_sync)

    @tasks.loop(seconds=SYNC_INTERVAL_SECONDS)
    async def upload_loop(self):
        """Periodic upload to GitHub."""
        if self.repo:
            await self.upload_all_files()

    @upload_loop.before_loop
    async def before_upload_loop(self):
        """Wait until bot is ready."""
        await self.bot.wait_until_ready()

    @commands.command(name="upload")
    @commands.is_owner()
    async def manual_upload(self, ctx: commands.Context):
        """Manually upload all files to GitHub RIGHT NOW."""
        if not self.repo:
            return await ctx.send("❌ GitHub not connected.")
            
        await ctx.send("⏳ Uploading ALL files to GitHub...")
        
        # Force AFK checkpoint if exists
        afk_cog = self.bot.get_cog("AFK")
        if afk_cog and hasattr(afk_cog, 'force_checkpoint'):
            await afk_cog.force_checkpoint()
        
        await self.upload_all_files()
        await ctx.send("✅ ALL files uploaded to GitHub!")

    @commands.command(name="gitlist")
    @commands.is_owner()
    async def list_files(self, ctx: commands.Context):
        """Show what files will be uploaded."""
        if not DATA_FOLDER.exists():
            return await ctx.send("❌ No data folder.")
            
        all_files = self.get_all_files()
        
        if not all_files:
            return await ctx.send("❌ No files in data folder.")
            
        embed = discord.Embed(
            title="📁 Files to Upload",
            color=discord.Color.blue()
        )
        
        # Show first 10 files
        file_list = []
        for file_path in all_files[:10]:
            rel_path = file_path.relative_to(DATA_FOLDER)
            size = file_path.stat().st_size / 1024  # KB
            file_list.append(f"`{rel_path}` ({size:.1f} KB)")
        
        if len(all_files) > 10:
            file_list.append(f"... and {len(all_files) - 10} more")
        
        embed.add_field(
            name=f"Total: {len(all_files)} files",
            value="\n".join(file_list),
            inline=False
        )
        
        await ctx.send(embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(GitHubSync(bot))