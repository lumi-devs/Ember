import discord
from discord.ext import commands
from discord import app_commands

class AutoThreads(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.target_bot_id = 655390915325591629  # Specific bot to watch for
        self.shamer_channel_id = 1437084755291279371  # Your shamer channel ID
        
    @commands.Cog.listener()
    async def on_message(self, message):
        # Ignore messages from the bot itself
        if message.author.id == self.bot.user.id:
            return
            
        # Check if message is in the shamer channel
        if message.channel.id != self.shamer_channel_id:
            return
            
        # Check conditions for thread creation
        should_create_thread = False
        thread_name = ""
        
        # Condition 1: Message from specific bot
        if message.author.id == self.target_bot_id:
            should_create_thread = True
            thread_name = f"Shame!!!"
            
        # Condition 2: Message contains image
        elif message.attachments:
            for attachment in message.attachments:
                if any(attachment.filename.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp']):
                    should_create_thread = True
                    thread_name = "Shame!!"
                    break
                    
        # Condition 3: Message contains image URLs
        elif any(domain in message.content.lower() for domain in ['discordapp.com', 'cdn.discordapp.com']):
            should_create_thread = True
            thread_name = "Shame!!"

        # Create thread if conditions are met
        if should_create_thread:
            try:
                # Create thread with auto-archive duration (1440 minutes = 24 hours)
                thread = await message.create_thread(
                    name=thread_name,
                    auto_archive_duration=60,
                    reason=f"Auto-thread for {message.author.name}"
                )
                
                # Send welcome message in the thread
                welcome_embed = discord.Embed(
                    title="💬 Discussion Thread",
                    description="Shame the user!",
                    color=discord.Color.orange()
                )

                welcome_embed.set_footer(text="Thread auto-archives after 1h of inactivity")

                await thread.send(embed=welcome_embed)
                
                print(f"[AutoThreads] ✅ Created thread '{thread_name}' for message {message.id}")
                
            except discord.Forbidden:
                print(f"[AutoThreads] ❌ Missing permissions to create threads in #{message.channel.name}")
            except discord.HTTPException as e:
                print(f"[AutoThreads] ❌ Failed to create thread: {e}")
            except Exception as e:
                print(f"[AutoThreads] ❌ Unexpected error: {e}")

async def setup(bot):
    await bot.add_cog(AutoThreads(bot))
    print("[AutoThreads] Cog loaded - watching for bot messages and images! ✅")