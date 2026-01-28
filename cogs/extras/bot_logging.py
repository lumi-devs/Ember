"""
LOG MANAGER COG
Captures bot output to log.txt, keeps last 300 lines only.
Bot owner text commands to view, clear, and get log file.
"""

import discord
from discord.ext import commands
import logging
import io
import asyncio
from pathlib import Path
from typing import Optional
import traceback
import sys
from datetime import datetime

class LogManagerCog(commands.Cog):
    """Manages bot logging to file and provides log commands."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.log_file = Path("logs/log.txt")
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        self.max_lines = 300
        
        # Set up custom logging handler
        self.setup_logging()
        
        # Write initialization message (ensure newline)
        sys.__stdout__.write(f"[Log Manager] Initialized. Logging to {self.log_file}\n")
    
    def setup_logging(self):
        """Set up logging to capture all bot output."""
        logging.raiseExceptions = False  # <--- ADD THIS LINE
        
        # Create a custom handler that writes to our log file
        self.file_handler = logging.FileHandler(self.log_file, encoding='utf-8')
        self.file_handler.setLevel(logging.INFO)
        
        # Format for file logging
        file_formatter = logging.Formatter(
            '%(asctime)s | %(levelname)s | %(name)s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        self.file_handler.setFormatter(file_formatter)
        
        # Add handler to root logger
        root_logger = logging.getLogger()
        root_logger.addHandler(self.file_handler)
        
        # Redirect print statements
        self.redirect_print_to_log()
    
    def redirect_print_to_log(self):
        """Redirect print statements to log file without recursion."""
        class PrintLogger:
            def __init__(self, cog, is_stdout=True):
                self.cog = cog
                self.is_stdout = is_stdout
                self.original_stream = sys.__stdout__ if is_stdout else sys.__stderr__
                self.buffer = ""
            
            def write(self, text):
                # Add to buffer
                self.buffer += text
                
                # Check if we have a complete line (ends with newline)
                if '\n' in self.buffer:
                    lines = self.buffer.split('\n')
                    # Process all complete lines
                    for line in lines[:-1]:  # All except the last incomplete line
                        if line.strip():  # Only log non-empty lines
                            self._log_line(line)
                    
                    # Keep the last incomplete line in buffer
                    self.buffer = lines[-1]
            
            def _log_line(self, line):
                """Log a single line."""
                # Get timestamp
                timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                
                # Write to log file with newline
                try:
                    with open(self.cog.log_file, 'a', encoding='utf-8') as f:
                        f.write(f"{timestamp} | PRINT | {line}\n")
                except Exception:
                    pass # Fail silently on file errors to prevent loops
                
                # Trim log file if needed
                self.cog.trim_log_file()
                
                # Write to original stream with newline
                # FIX: Wrap this in try/except to handle disconnected terminals
                try:
                    self.original_stream.write(f"{line}\n")
                    self.original_stream.flush()
                except OSError:
                    # Terminal is disconnected/closed (Errno 5). 
                    # We ignore this because we already logged to the file.
                    pass
            
            def flush(self):
                # Flush any remaining buffer
                if self.buffer.strip():
                    self._log_line(self.buffer)
                    self.buffer = ""
                self.original_stream.flush()
        
        # Redirect stdout and stderr
        sys.stdout = PrintLogger(self, is_stdout=True)
        sys.stderr = PrintLogger(self, is_stdout=False)
    
    def trim_log_file(self):
        """Keep only the last N lines in the log file."""
        try:
            with open(self.log_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            if len(lines) > self.max_lines:
                # Keep only the last max_lines
                trimmed_lines = lines[-self.max_lines:]
                
                with open(self.log_file, 'w', encoding='utf-8') as f:
                    f.writelines(trimmed_lines)
                
                # Log the trimming action

        
        except Exception as e:
            self.original_print(f"[Log Manager] Error trimming log: {e}")
    
    async def is_bot_owner(ctx: commands.Context) -> bool:
        """Check if user is bot owner."""
        return await ctx.bot.is_owner(ctx.author)
    
    # ==================== TEXT COMMANDS ====================
    
    @commands.command(name="logs", aliases=["getlogs", "logfile"])
    @commands.check(is_bot_owner)
    async def get_logs(self, ctx: commands.Context, lines: Optional[int] = 50):
        """
        Get the latest log entries.
        Usage: .logs [number_of_lines]
        Example: .logs 50
        Default: 50 lines, max: 300
        """
        try:
            # Validate line count
            lines = min(max(1, lines), 300)
            
            if not self.log_file.exists():
                await ctx.send("❌ Log file doesn't exist yet.")
                return
            
            # Read the last N lines
            with open(self.log_file, 'r', encoding='utf-8') as f:
                all_lines = f.readlines()
            
            if not all_lines:
                await ctx.send("📭 Log file is empty.")
                return
            
            # Get requested lines
            requested_lines = all_lines[-lines:]
            
            # Always send as file (better for logs)
            log_content = "".join(requested_lines)
            file = discord.File(
                io.BytesIO(log_content.encode('utf-8')),
                filename=f"bot_logs_last_{lines}_lines.txt"
            )
            
            embed = discord.Embed(
                title="📁 Bot Logs",
                description=f"Last {lines} lines from log file",
                color=discord.Color.green()
            )
            embed.add_field(
                name="📄 File Info",
                value=f"• Lines: {len(requested_lines)}\n"
                      f"• File: `bot_logs_last_{lines}_lines.txt`\n"
                      f"• Total size: {len(log_content)} characters",
                inline=False
            )
            
            # Show preview of last 5 lines
            if len(requested_lines) >= 5:
                preview = "".join(requested_lines[-5:])
                if len(preview) > 500:
                    preview = preview[-500:] + "\n..."
                embed.add_field(
                    name="👀 Preview (last 5 lines)",
                    value=f"```{preview}```",
                    inline=False
                )
            
            embed.set_footer(text=f"Requested by {ctx.author.name}")
            
            await ctx.send(embed=embed, file=file)
                
        except Exception as e:
            await ctx.send(f"❌ Error reading logs: {e}")
            self.original_print(f"[Log Manager] Error in get_logs: {e}")
            traceback.print_exc()
    
    @commands.command(name="logstats", aliases=["loginfo"])
    @commands.check(is_bot_owner)
    async def log_stats(self, ctx: commands.Context):
        """Get statistics about the log file.
        Usage: .logstats
        """
        try:
            if not self.log_file.exists():
                await ctx.send("❌ Log file doesn't exist yet.")
                return
            
            # Get file stats
            with open(self.log_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            file_size = self.log_file.stat().st_size
            line_count = len(lines)
            
            # Get first and last timestamps
            import re
            timestamp_pattern = r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}'
            
            first_time = re.search(timestamp_pattern, lines[0]) if lines else None
            last_time = re.search(timestamp_pattern, lines[-1]) if lines else None
            
            embed = discord.Embed(
                title="📊 Log File Statistics",
                color=discord.Color.blue()
            )
            
            embed.add_field(name="📁 File Path", value=f"`{self.log_file}`", inline=False)
            embed.add_field(name="📊 Total Lines", value=f"`{line_count}`", inline=True)
            embed.add_field(name="📈 Max Lines", value=f"`{self.max_lines}`", inline=True)
            embed.add_field(name="💾 File Size", value=f"`{file_size:,} bytes`", inline=True)
            
            if first_time:
                embed.add_field(name="⏰ First Entry", value=f"`{first_time.group()}`", inline=True)
            if last_time:
                embed.add_field(name="🕒 Last Entry", value=f"`{last_time.group()}`", inline=True)
            
            # Count log levels from last 500 lines
            level_counts = {
                "INFO": 0,
                "WARNING": 0,
                "ERROR": 0,
                "CRITICAL": 0,
                "DEBUG": 0,
                "PRINT": 0
            }
            
            for line in lines[-500:]:  # Check last 500 lines
                if "| INFO |" in line:
                    level_counts["INFO"] += 1
                elif "| WARNING |" in line:
                    level_counts["WARNING"] += 1
                elif "| ERROR |" in line:
                    level_counts["ERROR"] += 1
                elif "| CRITICAL |" in line:
                    level_counts["CRITICAL"] += 1
                elif "| DEBUG |" in line:
                    level_counts["DEBUG"] += 1
                elif "| PRINT |" in line:
                    level_counts["PRINT"] += 1
            
            level_info = "\n".join([f"• {level}: {count}" for level, count in level_counts.items() if count > 0])
            embed.add_field(name="📝 Recent Log Levels", value=f"```{level_info}```", inline=False)
            
            embed.set_footer(text=f"Requested by {ctx.author.name}")
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            await ctx.send(f"❌ Error getting log stats: {e}")
    
    @commands.command(name="clearlogs", aliases=["resetlogs"])
    @commands.check(is_bot_owner)
    async def clear_logs(self, ctx: commands.Context):
        """Clear the log file (keeps header).
        Usage: .clearlogs
        """
        # Create confirmation button
        class ConfirmView(discord.ui.View):
            def __init__(self, cog):
                super().__init__(timeout=30)
                self.cog = cog
                self.confirmed = False
            
            @discord.ui.button(label="✅ Yes, Clear Logs", style=discord.ButtonStyle.danger)
            async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                if interaction.user.id != ctx.author.id:
                    await interaction.response.send_message(
                        "❌ This confirmation is not for you.",
                        ephemeral=True
                    )
                    return
                
                await interaction.response.defer()
                
                try:
                    # Create backup before clearing
                    if self.cog.log_file.exists():
                        backup_file = self.cog.log_file.with_suffix(f".backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
                        import shutil
                        shutil.copy2(self.cog.log_file, backup_file)
                    
                    # Clear and add header
                    with open(self.cog.log_file, 'w', encoding='utf-8') as f:
                        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        f.write(f"{timestamp} | INFO | LogManager | Log file cleared by {interaction.user}\n")
                        f.write(f"{timestamp} | INFO | LogManager | Bot: {self.cog.bot.user.name} ({self.cog.bot.user.id})\n")
                        f.write("="*80 + "\n")
                    
                    # Disable buttons
                    for child in self.children:
                        child.disabled = True
                    
                    await interaction.edit_original_response(
                        content="✅ Log file cleared successfully!",
                        view=self
                    )
                    
                    print(f"[Log Manager] Log file cleared by {interaction.user}")
                    
                except Exception as e:
                    await interaction.followup.send(f"❌ Error clearing logs: {e}")
            
            @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
            async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                if interaction.user.id != ctx.author.id:
                    await interaction.response.send_message(
                        "❌ This confirmation is not for you.",
                        ephemeral=True
                    )
                    return
                
                await interaction.response.edit_message(
                    content="❌ Log clearance cancelled.",
                    embed=None,
                    view=None
                )
        
        embed = discord.Embed(
            title="⚠️ Clear Log File",
            description="Are you sure you want to clear the log file?\n\n"
                       "**This will:**\n"
                       "• Create a backup of current logs\n"
                       "• Clear all log entries\n"
                       "• Keep only a header message",
            color=discord.Color.orange()
        )
        
        view = ConfirmView(self)
        await ctx.send(embed=embed, view=view)
    
    @commands.command(name="taillogs", aliases=["followlogs"])
    @commands.check(is_bot_owner)
    async def tail_logs(self, ctx: commands.Context, lines: Optional[int] = 20):
        """
        Continuously monitor new log entries (for 60 seconds).
        Usage: .taillogs [number_of_initial_lines]
        Example: .taillogs 10
        """
        try:
            lines = min(max(5, lines), 100)
            
            if not self.log_file.exists():
                await ctx.send("❌ Log file doesn't exist yet.")
                return
            
            # Send initial lines
            initial_msg = await ctx.send("🔄 Starting log tail...")
            
            # Read initial lines
            with open(self.log_file, 'r', encoding='utf-8') as f:
                all_lines = f.readlines()
            
            if all_lines:
                tail_lines = all_lines[-lines:]
                tail_text = "".join(tail_lines)
                
                if len(tail_text) > 1500:
                    tail_text = tail_text[-1500:] + "\n... (truncated)"
                
                await initial_msg.edit(content=f"```{tail_text}```")
            
            # Now follow new entries for 60 seconds
            last_position = self.log_file.stat().st_size
            
            for i in range(60):  # 60 seconds
                await asyncio.sleep(1)
                
                current_size = self.log_file.stat().st_size
                if current_size > last_position:
                    # Read new content
                    with open(self.log_file, 'r', encoding='utf-8') as f:
                        f.seek(last_position)
                        new_content = f.read()
                        last_position = current_size
                    
                    if new_content.strip():
                        # Update message with new content
                        new_lines = new_content.strip().split('\n')
                        for line in new_lines:
                            if line.strip():
                                # Send short updates
                                if len(line) < 150:
                                    await ctx.send(f"```{line}```", delete_after=10)
                                else:
                                    # Truncate very long lines
                                    await ctx.send(f"```{line[:197]}...```", delete_after=10)
            
            await ctx.send("⏹️ Log tailing stopped (60 second limit).")
            
        except Exception as e:
            await ctx.send(f"❌ Error in tail_logs: {e}")
    
    @commands.command(name="searchlogs", aliases=["grep"])
    @commands.check(is_bot_owner)
    async def search_logs(self, ctx: commands.Context, *, search_term: str):
        """
        Search for text in the log file.
        Usage: .searchlogs [search term]
        Example: .searchlogs error
        """
        try:
            if not self.log_file.exists():
                await ctx.send("❌ Log file doesn't exist yet.")
                return
            
            with open(self.log_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            # Case-insensitive search
            search_term_lower = search_term.lower()
            matching_lines = []
            
            for line in lines:
                if search_term_lower in line.lower():
                    matching_lines.append(line)
                    if len(matching_lines) >= 50:  # Limit results
                        break
            
            if not matching_lines:
                await ctx.send(f"❌ No matches found for `{search_term}`")
                return
            
            result_text = "".join(matching_lines[-25:])  # Last 25 matches
            
            if len(result_text) > 1500:
                result_text = result_text[-1500:] + "\n... (showing last 25 matches)"
            
            embed = discord.Embed(
                title="🔍 Log Search Results",
                description=f"Search term: `{search_term}`",
                color=discord.Color.blue()
            )
            embed.add_field(
                name=f"Found {len(matching_lines)} matches",
                value=f"```{result_text}```",
                inline=False
            )
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            await ctx.send(f"❌ Error searching logs: {e}")
    
    @commands.command(name="loghelp")
    @commands.check(is_bot_owner)
    async def log_help(self, ctx: commands.Context):
        """Show help for log management commands."""
        embed = discord.Embed(
            title="📖 Log Management Commands",
            description="Bot owner commands for managing logs",
            color=discord.Color.purple()
        )
        
        commands_list = [
            ("`.logs [lines=50]`", "Get last N lines of logs as file"),
            ("`.logstats`", "Get log file statistics"),
            ("`.clearlogs`", "Clear log file (with confirmation)"),
            ("`.taillogs [lines=20]`", "Monitor new logs for 60s"),
            ("`.searchlogs [term]`", "Search for text in logs"),
            ("`.loghelp`", "Show this help message")
        ]
        
        for cmd, desc in commands_list:
            embed.add_field(name=cmd, value=desc, inline=False)
        
        embed.set_footer(text=f"Log file: {self.log_file} | Max lines: {self.max_lines}")
        
        await ctx.send(embed=embed)
    
    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error):
        """Log command errors."""
        if isinstance(error, commands.CheckFailure):
            # Don't log permission errors
            return
        
        error_msg = f"Command '{ctx.command}' failed: {error}"
        print(f"[Command Error] {error_msg}")
        
        # Log full traceback if available
        if hasattr(error, 'original'):
            traceback.print_exception(type(error.original), error.original, error.original.__traceback__)
    
    def cog_unload(self):
        """Clean up when cog is unloaded."""
        # Remove our file handler
        root_logger = logging.getLogger()
        root_logger.removeHandler(self.file_handler)
        self.file_handler.close()
        
        # Restore original print
        sys.stdout = sys.__stdout__
        sys.stderr = sys.__stderr__
        
        print("[Log Manager] Cleaned up logging handlers")

async def setup(bot: commands.Bot):
    """Setup function for discord.py cog loading."""
    await bot.add_cog(LogManagerCog(bot))
    print("[Log Manager] Cog loaded successfully")