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
SYNC_INTERVAL_SECONDS = 300  # 5 minutes - increased to reduce API calls
GITHUB_API_TIMEOUT = 30  # seconds

logging.basicConfig(level=logging.INFO)

class GitHubSync(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.github: Optional[Github] = None
        self.repo = None
        self.sync_enabled = True
        self.sync_attempts = 0
        self.max_attempts = 3
        
        if GITHUB_TOKEN and GITHUB_REPO:
            bot.loop.create_task(self.startup_tasks())
        else:
            logging.warning("GitHub credentials not set. Sync disabled.")

    def cog_unload(self):
        if self.sync_loop.is_running():
            self.sync_loop.cancel()

    async def _force_sqlite_checkpoints(self):
        """Force checkpoint for AFK database only."""
        logging.info("🔧 Forcing AFK database checkpoint for git sync...")
        
        afk_cog = self.bot.get_cog("AFK")
        if afk_cog and hasattr(afk_cog, 'force_checkpoint'):
            try:
                result = await afk_cog.force_checkpoint()
                if result:
                    logging.info("  ✅ AFK checkpoint successful")
                    return True
                else:
                    logging.warning("  ⚠️ AFK checkpoint failed")
            except Exception as e:
                logging.warning(f"  ⚠️ AFK checkpoint error: {e}")
        
        # For other databases, they'll sync when they're ready
        logging.info("  ℹ️ Other databases will sync when modified")
        return True

    async def startup_tasks(self):
        """Run pull first, then start the push loop."""
        await self.bot.wait_until_ready()
        await self.initialize_github()
        if self.repo:
            await self.pull_from_github()
            self.sync_loop.start()

    async def initialize_github(self):
        """Initialize GitHub connection with timeout."""
        try:
            # Run blocking GitHub init in thread
            self.github = await asyncio.wait_for(
                asyncio.to_thread(lambda: Github(GITHUB_TOKEN)),
                timeout=GITHUB_API_TIMEOUT
            )
            self.repo = await asyncio.wait_for(
                asyncio.to_thread(lambda: self.github.get_repo(GITHUB_REPO)),
                timeout=GITHUB_API_TIMEOUT
            )
            logging.info("✅ GitHub connection initialized")
        except asyncio.TimeoutError:
            logging.error("❌ GitHub connection timeout")
            self.sync_enabled = False
        except Exception as e:
            logging.error(f"❌ Failed to initialize GitHub: {e}")
            self.sync_enabled = False

    async def pull_from_github(self):
        """Pull all files from GitHub `data/` folder to local."""
        if not self.sync_enabled or not self.repo:
            return
            
        logging.info("⏳ Pulling data from GitHub...")
        try:
            # Run blocking GitHub API call in thread with timeout
            contents = await asyncio.wait_for(
                asyncio.to_thread(lambda: self.repo.get_contents("data")),
                timeout=GITHUB_API_TIMEOUT
            )
            
            DATA_FOLDER.mkdir(exist_ok=True)
            
            for file_content in contents:
                try:
                    file_path = DATA_FOLDER / file_content.name
                    # Decode content in thread
                    content_bytes = await asyncio.wait_for(
                        asyncio.to_thread(
                            lambda: base64.b64decode(file_content.content)
                        ),
                        timeout=10
                    )
                    
                    # Write file
                    await asyncio.to_thread(file_path.write_bytes, content_bytes)
                    logging.info(f"⬇ Pulled {file_content.name} from GitHub")
                    
                except Exception as e:
                    logging.error(f"❌ Failed to pull {file_content.name}: {e}")
                    
            logging.info("✅ Pull from GitHub completed successfully.")
            self.sync_attempts = 0  # Reset attempts on success
            
        except asyncio.TimeoutError:
            logging.warning("⚠️ GitHub pull timeout, will retry later")
            self.sync_attempts += 1
        except Exception as e:
            logging.error(f"❌ Failed to pull from GitHub: {e}")
            self.sync_attempts += 1
            
        # Disable sync if too many failures
        if self.sync_attempts >= self.max_attempts:
            logging.error("❌ Too many GitHub failures, disabling sync")
            self.sync_enabled = False
            if self.sync_loop.is_running():
                self.sync_loop.cancel()

    async def sync_to_github(self):
        """Sync local files to GitHub with proper async handling."""
        if not self.sync_enabled or not self.repo:
            return
            
        try:
            DATA_FOLDER.mkdir(exist_ok=True)
            
            # ===== NEW: FORCE SQLITE CHECKPOINTS =====
            # Force checkpoints for all SQLite databases before comparing
            checkpoint_success = await self._force_sqlite_checkpoints()
            if not checkpoint_success:
                logging.warning("⚠️ Some databases may not be fully synced")
            # =========================================
            
            files = list(DATA_FOLDER.iterdir())
            
            if not files:
                return
                
            logging.info(f"⏳ Syncing {len(files)} files to GitHub...")
            
            for file_path in files:
                if not file_path.is_file():
                    continue
                    
                if file_path.name.endswith(('-shm', '-wal', '-journal')):
                    continue  # Skip SQLite temporary files
                    
                github_path = f"data/{file_path.name}"
                
                try:
                    # Read local file in thread
                    local_content = await asyncio.wait_for(
                        asyncio.to_thread(file_path.read_bytes),
                        timeout=5
                    )
                    
                    # Check if file exists on GitHub (in thread)
                    try:
                        existing_file = await asyncio.wait_for(
                            asyncio.to_thread(
                                lambda: self.repo.get_contents(github_path)
                            ),
                            timeout=10
                        )
                        
                        # Compare with remote content
                        remote_bytes = await asyncio.wait_for(
                            asyncio.to_thread(
                                lambda: base64.b64decode(existing_file.content.encode())
                            ),
                            timeout=5
                        )
                        
                        if remote_bytes != local_content:
                            # Update file (in thread)
                            await asyncio.wait_for(
                                asyncio.to_thread(
                                    lambda: self.repo.update_file(
                                        github_path,
                                        f"Update {file_path.name}",
                                        local_content,
                                        existing_file.sha
                                    )
                                ),
                                timeout=15
                            )
                            logging.info(f"♻ Updated {file_path.name} on GitHub")
                        else:
                            logging.info(f"✅ {file_path.name} unchanged, skipping commit")
                            
                    except Exception as e:
                        if "Not Found" in str(e):
                            # Create new file (in thread)
                            await asyncio.wait_for(
                                asyncio.to_thread(
                                    lambda: self.repo.create_file(
                                        github_path,
                                        f"Add {file_path.name}",
                                        local_content
                                    )
                                ),
                                timeout=15
                            )
                            logging.info(f"➕ Created {file_path.name} on GitHub")
                        else:
                            logging.warning(f"⚠️ Could not check {file_path.name}: {e}")
                            
                except asyncio.TimeoutError:
                    logging.warning(f"⚠️ Timeout syncing {file_path.name}, skipping")
                except Exception as e:
                    logging.error(f"❌ Failed to sync {file_path.name}: {e}")
            
            logging.info("✅ Sync to GitHub completed")
            self.sync_attempts = 0  # Reset attempts on success
            
        except asyncio.TimeoutError:
            logging.warning("⚠️ GitHub sync timeout")
            self.sync_attempts += 1
        except Exception as e:
            logging.error(f"❌ Failed to sync to GitHub: {e}")
            self.sync_attempts += 1
            
        # Disable sync if too many failures
        if self.sync_attempts >= self.max_attempts:
            logging.error("❌ Too many GitHub failures, disabling sync")
            self.sync_enabled = False
            if self.sync_loop.is_running():
                self.sync_loop.cancel()

    @tasks.loop(seconds=SYNC_INTERVAL_SECONDS)
    async def sync_loop(self):
        """Periodic push to GitHub with retry logic."""
        if not self.sync_enabled:
            return
            
        # Add jitter to prevent exact interval syncs
        jitter = asyncio.create_task(asyncio.sleep(5))
        await self.sync_to_github()
        await jitter  # Ensure at least 5 seconds pass

    @sync_loop.before_loop
    async def before_sync_loop(self):
        """Wait until bot is ready before starting sync loop."""
        await self.bot.wait_until_ready()

    @sync_loop.error
    async def sync_loop_error(self, error):
        """Handle sync loop errors."""
        logging.error(f"Sync loop error: {error}")
        self.sync_attempts += 1
        
        if self.sync_attempts >= self.max_attempts:
            logging.error("❌ Too many sync loop errors, stopping")
            self.sync_enabled = False
            self.sync_loop.cancel()

    @commands.command(name="gsync")
    @commands.is_owner()
    async def manual_sync(self, ctx: commands.Context):
        """Manually push local data folder to GitHub."""
        if not self.sync_enabled:
            return await ctx.send("❌ GitHub sync is disabled due to previous errors.")
            
        await ctx.send("⏳ Syncing to GitHub...")
        await self.sync_to_github()
        await ctx.send("✅ Data folder synced to GitHub.")

    @commands.command(name="gpull")
    @commands.is_owner()
    async def manual_pull(self, ctx: commands.Context):
        """Manually pull data from GitHub to local folder."""
        if not self.sync_enabled:
            return await ctx.send("❌ GitHub sync is disabled due to previous errors.")
            
        await ctx.send("⏳ Pulling from GitHub...")
        await self.pull_from_github()
        await ctx.send("✅ Data folder pulled from GitHub.")

    @commands.command(name="gitstatus")
    @commands.is_owner()
    async def git_status(self, ctx: commands.Context):
        """Show GitHub sync status."""
        embed = discord.Embed(
            title="🔄 GitHub Sync Status",
            color=discord.Color.blue() if self.sync_enabled else discord.Color.red()
        )
        
        embed.add_field(name="Enabled", value=str(self.sync_enabled), inline=True)
        embed.add_field(name="Repo", value=GITHUB_REPO or "Not set", inline=True)
        embed.add_field(name="Failures", value=str(self.sync_attempts), inline=True)
        embed.add_field(name="Interval", value=f"{SYNC_INTERVAL_SECONDS}s", inline=True)
        embed.add_field(name="Loop Running", value=str(self.sync_loop.is_running()), inline=True)
        
        if DATA_FOLDER.exists():
            files = list(DATA_FOLDER.iterdir())
            file_count = len([f for f in files if f.is_file()])
            embed.add_field(name="Local Files", value=str(file_count), inline=True)
        
        await ctx.send(embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(GitHubSync(bot))