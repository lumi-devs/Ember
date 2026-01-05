import discord
from discord.ext import commands, tasks
from github import Github
from pathlib import Path
import os
import base64
import hashlib
import json
import logging
import asyncio
from datetime import datetime
from typing import Optional, Dict, List, Tuple

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO = os.environ.get("REPO_NAME")
DATA_FOLDER = Path("data")
HASH_FILE = DATA_FOLDER / ".file_hashes.json"
SYNC_INTERVAL_SECONDS = 45

logging.basicConfig(level=logging.INFO)

class GitHubSync(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.github: Optional[Github] = None
        self.repo = None
        self.file_hashes: Dict[str, str] = {}
        
        if GITHUB_TOKEN and GITHUB_REPO:
            bot.loop.create_task(self.startup_tasks())
        else:
            logging.warning("GitHub credentials not set. Sync disabled.")

    async def startup_tasks(self):
        """Start the upload loop."""
        await self.bot.wait_until_ready()
        await self.initialize_github()
        self.load_hashes()
        self.cleanup_stale_hashes()  # Clean up on startup
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

    def calculate_file_hash(self, file_path: Path) -> str:
        """Calculate SHA256 hash of a file."""
        sha256_hash = hashlib.sha256()
        try:
            with open(file_path, "rb") as f:
                # Read file in chunks to handle large files
                for byte_block in iter(lambda: f.read(4096), b""):
                    sha256_hash.update(byte_block)
            return sha256_hash.hexdigest()
        except Exception as e:
            logging.error(f"Error calculating hash for {file_path}: {e}")
            return ""

    def get_changed_files(self) -> Tuple[List[Path], List[Path]]:
        """Get lists of changed and deleted files based on hashes."""
        DATA_FOLDER.mkdir(exist_ok=True)
        
        changed_files = []
        deleted_files = []
        current_files = set()
        
        # Check all existing files in data directory
        all_files = self.get_all_files()
        
        for file_path in all_files:
            if file_path == HASH_FILE:  # Skip hash file itself
                continue
                
            rel_path_str = str(file_path.relative_to(DATA_FOLDER))
            current_files.add(rel_path_str)
            
            current_hash = self.calculate_file_hash(file_path)
            if not current_hash:
                continue
                
            old_hash = self.file_hashes.get(rel_path_str)
            
            # File is new or changed
            if old_hash != current_hash:
                changed_files.append(file_path)
                self.file_hashes[rel_path_str] = current_hash
        
        # Check for deleted files
        stale_entries = []
        for rel_path_str in list(self.file_hashes.keys()):
            if rel_path_str not in current_files:
                deleted_files.append(Path(rel_path_str))
                stale_entries.append(rel_path_str)
        
        # Remove stale entries immediately
        for rel_path_str in stale_entries:
            del self.file_hashes[rel_path_str]
        
        return changed_files, deleted_files

    def cleanup_stale_hashes(self):
        """Remove hashes for files that no longer exist."""
        if not DATA_FOLDER.exists():
            self.file_hashes.clear()
            self.save_hashes()
            return
            
        current_files = set()
        for file_path in self.get_all_files():
            if file_path == HASH_FILE:
                continue
            rel_path_str = str(file_path.relative_to(DATA_FOLDER))
            current_files.add(rel_path_str)
        
        # Find and remove stale entries
        stale_entries = []
        for rel_path_str in list(self.file_hashes.keys()):
            if rel_path_str not in current_files:
                stale_entries.append(rel_path_str)
        
        if stale_entries:
            for rel_path_str in stale_entries:
                del self.file_hashes[rel_path_str]
            logging.info(f"Cleaned up {len(stale_entries)} stale hash entries")
            self.save_hashes()

    def get_all_files(self):
        """Get all files in data directory recursively."""
        all_files = []
        if not DATA_FOLDER.exists():
            return all_files
            
        for root, dirs, files in os.walk(DATA_FOLDER):
            for file in files:
                full_path = Path(root) / file
                all_files.append(full_path)
        return all_files

    def load_hashes(self):
        """Load file hashes from disk."""
        try:
            if HASH_FILE.exists():
                with open(HASH_FILE, 'r') as f:
                    self.file_hashes = json.load(f)
                logging.info(f"Loaded {len(self.file_hashes)} file hashes")
        except Exception as e:
            logging.error(f"Error loading hashes: {e}")
            self.file_hashes = {}

    def save_hashes(self):
        """Save file hashes to disk."""
        try:
            with open(HASH_FILE, 'w') as f:
                json.dump(self.file_hashes, f, indent=2)
        except Exception as e:
            logging.error(f"Error saving hashes: {e}")

    def sync_to_github_single_commit(self):
        """Upload all changed files in a single commit - Synchronous version."""
        if not self.repo:
            return 0, 0
            
        # Get changed and deleted files
        changed_files, deleted_paths = self.get_changed_files()
        
        if not changed_files and not deleted_paths:
            #print("✅ No changes detected")
            return 0, 0
            
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        commit_message = f"Sync {timestamp}\n"
        
        if changed_files:
            commit_message += f"Updated: {len(changed_files)} files\n"
        if deleted_paths:
            commit_message += f"Deleted: {len(deleted_paths)} files"
        
        print(f"⬆ Committing {len(changed_files)} changed and {len(deleted_paths)} deleted files...")
        
        # Collect all file operations
        tree_operations = []
        
        # Process changed files
        for file_path in changed_files:
            try:
                rel_path = file_path.relative_to(DATA_FOLDER)
                github_path = f"data/{rel_path}"
                
                with open(file_path, 'rb') as f:
                    content_bytes = f.read()
                
                # Create blob for the file
                blob = self.repo.create_git_blob(
                    base64.b64encode(content_bytes).decode('utf-8'),
                    'base64'
                )
                
                # Add to tree operations
                tree_operations.append({
                    'path': github_path,
                    'mode': '100644',  # Regular file
                    'type': 'blob',
                    'sha': blob.sha
                })
                
                print(f"  ✅ Staged: {rel_path}")
                
            except Exception as e:
                print(f"  ❌ Failed to stage {file_path}: {e}")
        
        # Process deleted files
        for rel_path in deleted_paths:
            github_path = f"data/{rel_path}"
            # For deletion, we need to get the current tree and remove the file
            tree_operations.append({
                'path': github_path,
                'mode': '100644',
                'type': 'blob',
                'sha': None  # SHA set to None means delete
            })
            print(f"  🗑️  Marked for deletion: {rel_path}")
        
        if not tree_operations:
            return 0, 0
        
        try:
            # Get reference to main branch
            ref = self.repo.get_git_ref("heads/main")
            
            # Get current commit
            commit = self.repo.get_git_commit(ref.object.sha)
            
            # Create new tree
            tree = self.repo.create_git_tree(tree_operations, base_tree=commit.tree)
            
            # Create new commit
            new_commit = self.repo.create_git_commit(
                commit_message,
                tree,
                [commit]
            )
            
            # Update reference
            ref.edit(new_commit.sha)
            
            # Save hashes after successful commit
            # Note: Deleted files have already been removed from self.file_hashes in get_changed_files()
            self.save_hashes()
            
            print(f"✅ Commit successful: {new_commit.sha[:8]}")
            return len(changed_files), len(deleted_paths)
            
        except Exception as e:
            print(f"❌ Commit failed: {e}")
            # On failure, reload hashes to discard unsaved changes
            self.load_hashes()
            return 0, 0

    async def sync_to_github(self):
        """Async wrapper for sync_to_github_single_commit."""
        if not self.repo:
            return 0, 0
        return await asyncio.to_thread(self.sync_to_github_single_commit)

    @tasks.loop(seconds=SYNC_INTERVAL_SECONDS)
    async def upload_loop(self):
        """Periodic upload to GitHub."""
        if self.repo:
            changed, deleted = await self.sync_to_github()
            if changed > 0 or deleted > 0:
                print(f"✅ Synced {changed} changed, {deleted} deleted files")

    @upload_loop.before_loop
    async def before_upload_loop(self):
        """Wait until bot is ready."""
        await self.bot.wait_until_ready()

    @commands.command(name="upload")
    @commands.is_owner()
    async def manual_upload(self, ctx: commands.Context):
        """Manually upload changed files to GitHub RIGHT NOW."""
        if not self.repo:
            return await ctx.send("❌ GitHub not connected.")
            
        # Force AFK checkpoint if exists
        afk_cog = self.bot.get_cog("AFK")
        if afk_cog and hasattr(afk_cog, 'force_checkpoint'):
            await afk_cog.force_checkpoint()
        
        await ctx.send("⏳ Checking for changes and uploading to GitHub...")
        
        changed, deleted = await self.sync_to_github()
        
        if changed == 0 and deleted == 0:
            await ctx.send("✅ No changes detected. Everything is up to date!")
        else:
            await ctx.send(f"✅ Successfully synced {changed} changed and {deleted} deleted files!")

    @commands.command(name="forceupload")
    @commands.is_owner()
    async def force_upload_all(self, ctx: commands.Context):
        """Force upload ALL files regardless of changes."""
        if not self.repo:
            return await ctx.send("❌ GitHub not connected.")
            
        await ctx.send("⏳ Force uploading ALL files to GitHub...")
        
        # Force AFK checkpoint if exists
        afk_cog = self.bot.get_cog("AFK")
        if afk_cog and hasattr(afk_cog, 'force_checkpoint'):
            await afk_cog.force_checkpoint()
        
        # Clear all hashes to force re-upload
        self.file_hashes.clear()
        self.save_hashes()
        
        changed, deleted = await self.sync_to_github()
        await ctx.send(f"✅ Force uploaded {changed} files!")

    @commands.command(name="gitstatus")
    @commands.is_owner()
    async def git_status(self, ctx: commands.Context):
        """Show sync status and pending changes."""
        if not DATA_FOLDER.exists():
            return await ctx.send("❌ No data folder.")
        
        # Use get_changed_files to detect current changes
        changed_files, deleted_files = self.get_changed_files()
        
        embed = discord.Embed(
            title="📊 GitHub Sync Status",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        
        embed.add_field(
            name="Tracked Files",
            value=f"```{len(self.file_hashes)} files```",
            inline=True
        )
        
        embed.add_field(
            name="Pending Changes",
            value=f"```{len(changed_files)} modified\n{len(deleted_files)} deleted```",
            inline=True
        )
        
        # Show sample of changed files (first 5)
        if changed_files or deleted_files:
            changes_list = []
            for i, file_path in enumerate(changed_files[:3]):
                rel_path = file_path.relative_to(DATA_FOLDER)
                changes_list.append(f"📝 `{rel_path}`")
            
            for i, rel_path in enumerate(deleted_files[:2]):
                changes_list.append(f"🗑️  `{rel_path}`")
            
            if len(changed_files) > 3 or len(deleted_files) > 2:
                changes_list.append(f"... and {max(0, len(changed_files)-3) + max(0, len(deleted_files)-2)} more")
            
            if changes_list:
                embed.add_field(
                    name="Sample Changes",
                    value="\n".join(changes_list),
                    inline=False
                )
        
        await ctx.send(embed=embed)

    @commands.command(name="githash")
    @commands.is_owner()
    async def check_hash(self, ctx: commands.Context, *, file_path: str):
        """Check the hash of a specific file."""
        full_path = DATA_FOLDER / file_path
        
        if not full_path.exists() or not full_path.is_file():
            return await ctx.send("❌ File not found.")
        
        rel_path_str = str(full_path.relative_to(DATA_FOLDER))
        current_hash = self.calculate_file_hash(full_path)
        stored_hash = self.file_hashes.get(rel_path_str, "Not tracked")
        
        embed = discord.Embed(
            title=f"🔍 Hash Check: {file_path}",
            color=discord.Color.green() if current_hash == stored_hash else discord.Color.orange()
        )
        
        embed.add_field(name="Current Hash", value=f"`{current_hash[:16]}...`", inline=False)
        embed.add_field(name="Stored Hash", value=f"`{stored_hash[:16] if stored_hash != 'Not tracked' else stored_hash}...`", inline=False)
        embed.add_field(name="Status", 
                       value="✅ Synced" if current_hash == stored_hash else "⚠️ Changed", 
                       inline=False)
        
        await ctx.send(embed=embed)

    @commands.command(name="cleanhashes")
    @commands.is_owner()
    async def clean_hashes(self, ctx: commands.Context):
        """Clean up stale hash entries manually."""
        old_count = len(self.file_hashes)
        self.cleanup_stale_hashes()
        new_count = len(self.file_hashes)
        
        removed = old_count - new_count
        if removed > 0:
            await ctx.send(f"✅ Cleaned up {removed} stale hash entries. Now tracking {new_count} files.")
        else:
            await ctx.send(f"✅ No stale entries found. Tracking {new_count} files.")

    @commands.command(name="hashstats")
    @commands.is_owner()
    async def hash_stats(self, ctx: commands.Context):
        """Show hash file statistics."""
        if not HASH_FILE.exists():
            return await ctx.send("❌ Hash file doesn't exist yet.")
        
        size_kb = HASH_FILE.stat().st_size / 1024
        file_count = len(self.file_hashes)
        
        embed = discord.Embed(
            title="📊 Hash File Statistics",
            color=discord.Color.purple(),
            timestamp=datetime.now()
        )
        
        embed.add_field(name="File Size", value=f"{size_kb:.2f} KB", inline=True)
        embed.add_field(name="Files Tracked", value=str(file_count), inline=True)
        embed.add_field(name="Avg per File", value=f"{(size_kb * 1024 / max(1, file_count)):.1f} bytes", inline=True)
        
        # Show sample of tracked files
        if file_count > 0:
            sample = list(self.file_hashes.keys())[:5]
            sample_list = [f"`{f}`" for f in sample]
            if file_count > 5:
                sample_list.append(f"... and {file_count - 5} more")
            
            embed.add_field(
                name="Tracked Files (sample)",
                value="\n".join(sample_list),
                inline=False
            )
        
        await ctx.send(embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(GitHubSync(bot))