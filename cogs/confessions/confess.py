import discord
from discord import ui, app_commands
from discord.ext import commands
from datetime import datetime
import asyncio
import aiosqlite

from cogs.confessions.helpers import DB_PATH, extract_url, init_db, process_media, is_user_banned, get_confession_by_id
from cogs.confessions.reply import ReplyModal

# ==============================
# CONFIGURATION
# ==============================
CONFESSION_CHANNEL_ID = 1399658407178272798
REPORT_CHANNEL_ID = 1393171149038293023
LOG_CHANNEL_ID = 1424792373837172899
MEDIA_CHANNEL_ID = 1424139009931743382
ALLOWED_ROLE_IDS = [
    1393171037562077196, 1393171036635402354, 1393171035695612055,
    1393171034701561908, 1393171033774624848, 1393171032726306966, 1420878388297465866
]

class MediaWaitView(ui.View):
    """View for waiting for media with cancel button"""
    def __init__(self, user_id: int, confession_id: int):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.confession_id = confession_id
        self.media_received = asyncio.Event()
        self.media_url = None
        self.cancelled = False

    @ui.button(label="Cancel Media Upload", style=discord.ButtonStyle.danger, custom_id="cancel_media")
    async def cancel_button(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id == self.user_id:
            self.cancelled = True
            self.media_received.set()
            await interaction.response.send_message("Media upload cancelled. Your confession will be posted without media.", ephemeral=True)
            self.stop()

    async def on_timeout(self):
        if not self.media_received.is_set():
            self.media_received.set()
        self.stop()

class ConfessionModal(ui.Modal, title="Submit a Confession"):
    title_input = ui.TextInput(label="Title", max_length=100, required=False, placeholder="Optional title for your confession")
    description_input = ui.TextInput(
        label="Description", 
        style=discord.TextStyle.paragraph, 
        max_length=1000,
        placeholder="Share your thoughts...",
        required=True
    )

    def __init__(self, bot):
        super().__init__()
        self.bot = bot
        self.user_id = None

    async def on_submit(self, interaction: discord.Interaction):
        self.user_id = interaction.user.id
        
        # Defer immediately to avoid timeout
        await interaction.response.defer(ephemeral=True, thinking=True)

        # Check blacklist with improved error handling
        if await is_user_banned(self.user_id):
            return await interaction.followup.send("❌ You are banned from submitting confessions.", ephemeral=True)

        channel = self.bot.get_channel(CONFESSION_CHANNEL_ID)
        if not channel:
            return await interaction.followup.send("❌ Confession channel not found.", ephemeral=True)

        try:
            # Insert confession entry with transaction
            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute(
                    "INSERT INTO confessions (user_id, title, description) VALUES (?, ?, ?)",
                    (self.user_id, self.title_input.value, self.description_input.value)
                )
                await db.commit()
                confession_id = cursor.lastrowid

            # Create initial embed WITHOUT buttons yet
            embed = discord.Embed(
                title=f"💌 {self.title_input.value or f'Confession #{confession_id}'}",
                description=self.description_input.value,
                color=discord.Color.purple(),
                timestamp=datetime.utcnow()
            )
            embed.set_footer(text=f"Confession #{confession_id}")

            # Send message WITHOUT view initially
            msg = await channel.send(embed=embed)

            # Create thread for this confession with simple name
            thread_name = f"Confession #{confession_id}"
            
            # Create public thread with 60 minute auto-archive duration
            try:
                thread = await msg.create_thread(
                    name=thread_name,
                    auto_archive_duration=60  # 60 minutes
                )
            except discord.HTTPException as e:
                print(f"Error creating thread: {e}")
                # Continue without thread if creation fails
                thread = None

            # Save message_id
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "UPDATE confessions SET message_id=? WHERE id=?", 
                    (msg.id, confession_id)
                )
                await db.commit()

            # Send media prompt with cancel button
            media_view = MediaWaitView(self.user_id, confession_id)
            prompt_msg = await interaction.followup.send(
                f"✅ Confession submitted! You have **1 minute** to send an image URL or attachment in this channel.\n"
                f"**Please send your image or link in the confession channel now.**\n"
                f"Send `skip` to continue without media.\n\n"
                f"📝 Your confession ID: #{confession_id}",
                view=media_view,
                ephemeral=True,
                wait=True
            )

            # Wait for media in the confession channel
            image_url = await self.wait_for_media_channel(interaction, confession_id, media_view)
            
            # Clean up the media wait view
            try:
                await prompt_msg.edit(view=None)
            except:
                pass

            # Update embed with image if available
            if image_url:
                embed.set_image(url=image_url)
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("UPDATE confessions SET image_url=? WHERE id=?", (image_url, confession_id))
                    await db.commit()

            # Create and attach persistent view AFTER media processing
            final_view = PersistentConfessionView(confession_id)
            await msg.edit(embed=embed, view=final_view)

            # Log confession
            await self.log_confession(confession_id, image_url)

            # Send final confirmation
            if thread:
                thread_msg = f"A discussion thread has been created: {thread.mention}"
            else:
                thread_msg = "Note: Could not create discussion thread."
            
            await interaction.followup.send(
                f"✅ Confession #{confession_id} is now public! {thread_msg}",
                ephemeral=True
            )

        except Exception as e:
            print(f"Error submitting confession: {e}")
            import traceback
            traceback.print_exc()
            await interaction.followup.send("❌ An error occurred while submitting your confession.", ephemeral=True)

    async def wait_for_media_channel(self, interaction, confession_id, media_view):
        """Wait for media response in the confession channel with proper timeout handling"""
        
        def check(m):
            # Check if message is from the user AND in the confession channel
            return (m.author.id == self.user_id and 
                   m.channel.id == CONFESSION_CHANNEL_ID and
                   not m.content.startswith('/') and  # Ignore commands
                   not m.content.startswith('!'))
        
        try:
            # Wait for user response in the confession channel
            while not media_view.media_received.is_set():
                try:
                    # Wait for either a message or timeout
                    dm_msg = await asyncio.wait_for(
                        self.bot.wait_for("message", check=check),
                        timeout=1.0
                    )
                    
                    # If we got a message, process it
                    content_lower = dm_msg.content.lower().strip()
                    
                    if content_lower == 'skip':
                        media_view.media_received.set()
                        try:
                            await dm_msg.delete()
                        except:
                            pass
                        await interaction.followup.send("✅ Skipped media upload.", ephemeral=True)
                        return None
                    
                    # Process the media
                    result = await process_media(
                        self.bot, dm_msg.content, dm_msg.attachments, MEDIA_CHANNEL_ID
                    )
                    
                    # Try to delete the user's message after processing
                    try:
                        await dm_msg.delete()
                    except:
                        pass
                    
                    if result:
                        await interaction.followup.send("✅ Media added to your confession!", ephemeral=True)
                        media_view.media_received.set()
                        return result
                    else:
                        await interaction.followup.send(
                            "❌ Could not process the media. Please send a valid image URL or attachment, or type `skip` to continue without media.",
                            ephemeral=True
                        )
                        
                except asyncio.TimeoutError:
                    # This is expected - just continue checking the view state
                    continue
                except Exception as e:
                    print(f"Error processing message: {e}")
                    continue
            
            # Check if cancelled
            if media_view.cancelled:
                return None
                
        except asyncio.TimeoutError:
            await interaction.followup.send("⏰ Media upload timed out. Continuing without media.", ephemeral=True)
        except Exception as e:
            print(f"Error in wait_for_media_channel: {e}")
            await interaction.followup.send("❌ An error occurred while processing media. Continuing without media.", ephemeral=True)
        
        return None

    async def log_confession(self, confession_id, image_url):
        """Log confession to log channel"""
        log_channel = self.bot.get_channel(LOG_CHANNEL_ID)
        if not log_channel:
            return

        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute(
                    "SELECT user_id, title, description FROM confessions WHERE id=?", (confession_id,)
                )
                confession = await cursor.fetchone()
                if not confession:
                    return

                user_id, title, description = confession
                user = await self.bot.fetch_user(user_id)

                log_embed = discord.Embed(
                    title=f"Confession #{confession_id}",
                    description=description,
                    color=discord.Color.purple(),
                    timestamp=datetime.utcnow()
                )
                log_embed.add_field(name="User", value=f"{user.mention} ({user.id})", inline=False)
                if title:
                    log_embed.add_field(name="Title", value=title, inline=False)
                if image_url:
                    log_embed.set_image(url=image_url)
                    
                await log_channel.send(embed=log_embed)
        except Exception as e:
            print(f"Error logging confession: {e}")

# ==============================
# PERSISTENT CONFESSION VIEW (Buttons)
# ==============================
class PersistentConfessionView(ui.View):
    def __init__(self, confession_id):
        super().__init__(timeout=None)
        self.confession_id = confession_id
        
        # Add persistent buttons with custom IDs
        self.add_item(PersistentReplyButton(confession_id))
        self.add_item(PersistentReportButton(confession_id))

# ==============================
# PERSISTENT BUTTONS
# ==============================
class PersistentReplyButton(ui.Button):
    def __init__(self, confession_id):
        super().__init__(
            label="Reply",
            style=discord.ButtonStyle.primary,
            custom_id=f"confession_reply_{confession_id}"
        )
        self.confession_id = confession_id

    async def callback(self, interaction: discord.Interaction):
        bot = interaction.client
        
        # Check if confession still exists
        confession = await get_confession_by_id(self.confession_id)
        if not confession:
            return await interaction.response.send_message("❌ Confession not found.", ephemeral=True)
            
        message_id, author_id = confession[5], confession[1]  # message_id, user_id
        modal = ReplyModal(bot, self.confession_id, message_id, author_id)
        await interaction.response.send_modal(modal)

class PersistentReportButton(ui.Button):
    def __init__(self, confession_id):
        super().__init__(
            label="Report",
            style=discord.ButtonStyle.danger,
            custom_id=f"confession_report_{confession_id}"
        )
        self.confession_id = confession_id

    async def callback(self, interaction: discord.Interaction):
        bot = interaction.client
        
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute(
                    "SELECT user_id, title, description, image_url FROM confessions WHERE id=?", (self.confession_id,)
                )
                confession = await cursor.fetchone()
                if not confession:
                    return await interaction.response.send_message("❌ Confession not found.", ephemeral=True)

                author_id, title, description, image_url = confession

                await db.execute(
                    "INSERT INTO reports (target_type, target_id, reporter_id) VALUES (?, ?, ?)",
                    ("confession", self.confession_id, interaction.user.id)
                )
                await db.commit()

                cursor = await db.execute("SELECT 1 FROM confessban WHERE user_id=?", (author_id,))
                banned = await cursor.fetchone()

            embed = discord.Embed(
                title=f"🚨 Confession Report #{self.confession_id}",
                color=discord.Color.red(),
                timestamp=datetime.utcnow()
            )
            embed.add_field(name="Confession ID", value=str(self.confession_id), inline=False)
            embed.add_field(name="Author ID", value=str(author_id), inline=False)
            embed.add_field(name="Reported by", value=f"{interaction.user} ({interaction.user.id})", inline=False)
            embed.add_field(name="Banned?", value="Yes" if banned else "No", inline=False)
            embed.add_field(name="Title", value=title or "No title", inline=False)
            embed.add_field(name="Content", value=description or "No content", inline=False)
            if image_url:
                embed.set_image(url=image_url)

            mod_channel = bot.get_channel(REPORT_CHANNEL_ID)
            if mod_channel:
                await mod_channel.send("<@&1393170995157798983> <@&1406325951234794615>", embed=embed)

            await interaction.response.send_message("✅ Report submitted.", ephemeral=True)
            
        except Exception as e:
            print(f"Error reporting confession: {e}")
            await interaction.response.send_message("❌ An error occurred while reporting.", ephemeral=True)

# ==============================
# MAIN COG
# ==============================
class Confessions(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.initialized = False
        self.bot.loop.create_task(self.initialize())

    async def initialize(self):
        """Initialize database and load persistent views"""
        try:
            await init_db()
            await self.load_persistent_views()
            self.initialized = True
            print("[Confessions] ✅ System initialized successfully.")
        except Exception as e:
            print(f"[Confessions] ❌ Initialization failed: {e}")

    async def load_persistent_views(self):
        """Load all persistent confession views on startup"""
        await self.bot.wait_until_ready()
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute("SELECT id, message_id FROM confessions WHERE message_id IS NOT NULL") as cursor:
                    async for confession_id, message_id in cursor:
                        # Add persistent view for each confession
                        try:
                            view = PersistentConfessionView(confession_id)
                            self.bot.add_view(view)
                        except Exception as e:
                            print(f"Error loading view for confession {confession_id}: {e}")
            print(f"[Confessions] ✅ Persistent views loaded.")
        except Exception as e:
            print(f"[Confessions] ❌ Error loading views: {e}")

    @app_commands.command(name="confess", description="Submit an anonymous confession")
    async def confess_command(self, interaction: discord.Interaction):
        # Check initialization
        if not self.initialized:
            return await interaction.response.send_message(
                "❌ Confession system is initializing, please try again in a moment.", ephemeral=True
            )

        # Check for ban
        if await is_user_banned(interaction.user.id):
            return await interaction.response.send_message(
                "❌ You are banned from making confessions.", ephemeral=True
            )

        # Check correct channel
        if interaction.channel.id != CONFESSION_CHANNEL_ID:
            return await interaction.response.send_message(
                f"❌ Please use this command in <#{CONFESSION_CHANNEL_ID}>.", ephemeral=True
            )

        # Check allowed roles
        if not any(role.id in ALLOWED_ROLE_IDS for role in interaction.user.roles):
            return await interaction.response.send_message(
                "❌ You do not have at least LVL 5 required to make confessions.", ephemeral=True
            )

        modal = ConfessionModal(self.bot)
        await interaction.response.send_modal(modal)

    @app_commands.command(name="confess_stats", description="Show confession statistics")
    @app_commands.default_permissions(manage_messages=True)
    async def confess_stats(self, interaction: discord.Interaction):
        """Show statistics about confessions"""
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                # Total confessions
                cursor = await db.execute("SELECT COUNT(*) FROM confessions")
                total_confessions = (await cursor.fetchone())[0]
                
                # Today's confessions
                cursor = await db.execute("SELECT COUNT(*) FROM confessions WHERE DATE(created_at) = DATE('now')")
                today_confessions = (await cursor.fetchone())[0]
                
                # Total bans
                cursor = await db.execute("SELECT COUNT(*) FROM confessban")
                total_bans = (await cursor.fetchone())[0]
                
                # Total reports
                cursor = await db.execute("SELECT COUNT(*) FROM reports")
                total_reports = (await cursor.fetchone())[0]

            embed = discord.Embed(
                title="📊 Confession Statistics",
                color=discord.Color.blue(),
                timestamp=datetime.utcnow()
            )
            embed.add_field(name="Total Confessions", value=total_confessions, inline=True)
            embed.add_field(name="Today's Confessions", value=today_confessions, inline=True)
            embed.add_field(name="Total Bans", value=total_bans, inline=True)
            embed.add_field(name="Total Reports", value=total_reports, inline=True)
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
        except Exception as e:
            await interaction.response.send_message(f"❌ Error fetching statistics: {e}", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Confessions(bot))