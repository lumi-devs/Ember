import discord
from discord.ext import commands, tasks
from discord import app_commands
from cogs.omegle.omegle_matchmaker import Matchmaker
from cogs.omegle.omegle_ui import StartButtonView, PrivateChatView
from datetime import datetime
import os

MALE_ROLE = 1393171022047350845
FEMALE_ROLE = 1393171023024881735
ANYONE_ROLES = [1393171022047350845, FEMALE_ROLE, 1409995872611209376, 1393171031660957838]
HUB_CHANNEL_ID = 1417923188083654916  # hub channel for threads
LOG_CHANNEL_ID = 1393171149038293023  # channel where chat logs will be uploaded


class OmegleCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.matchmaker = Matchmaker(bot, MALE_ROLE, FEMALE_ROLE, ANYONE_ROLES, HUB_CHANNEL_ID)
        self.active_views = []  # store all active PrivateChatViews
        self.check_queue_loop.start()

    def cog_unload(self):
        self.check_queue_loop.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        # Register persistent Start button
        self.bot.add_view(StartButtonView(self.matchmaker))
        print("[DEBUG] Persistent StartButtonView registered")

    @app_commands.command(name="omegle_send", description="Send the Omegle start button")
    async def omegle_send(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message(":x: You cannot use this.", ephemeral=True)
            return

        start_view = StartButtonView(self.matchmaker)
        await channel.send(":speech_balloon: Click the button below to start a private chat!", view=start_view)
        await interaction.response.send_message(":white_check_mark: Omegle start interface sent.", ephemeral=True)

    @tasks.loop(seconds=5)
    async def check_queue_loop(self):
        for guild in self.bot.guilds:
            #print(f"[DEBUG] Current queue: {self.matchmaker.queue}")
            #print(f"[DEBUG] check_queue_loop tick at {datetime.now()}")

            while True:
                u1, u2 = await self.matchmaker.find_match(guild)
                if not u1 or not u2:
                    break

                print(f"[DEBUG] Match found: {u1} + {u2} in {guild.name}")

                # Create thread first
                thread = await self.matchmaker.create_chat(guild, u1, u2)
                if thread is None:
                    print(f"[DEBUG] Thread creation failed for {u1} + {u2} in {guild.name}")
                    continue

                print(f"[DEBUG] Thread created: {thread.name} ({thread.id})")

                # Remove users from queue & update DB
                self.matchmaker.queue = [
                    u for u in self.matchmaker.queue if u["user_id"] not in (u1.id, u2.id)
                ]
                from helpers.github_data import load_db, save_db
                db, file = load_db()
                db["omegle"]["queue"] = self.matchmaker.queue
                save_db(db, file)

                # Create a single PrivateChatView with logging
                private_view = PrivateChatView(self.matchmaker, thread, u1, u2)
                self.active_views.append(private_view)

                # Send matched message + Leave+Skip view
                try:
                    await thread.send(
                        f":wave: {u1.mention} and {u2.mention} matched! Say hi!",
                        view=private_view
                    )
                except Exception as e:
                    print(f"[DEBUG] Failed to send message in thread: {e}")

                # DM notification
                for user in [u1, u2]:
                    try:
                        await user.send(f"You have been matched with {u1.mention if user != u1 else u2.mention} in {guild.name}!")
                        print(f"[DEBUG] DM sent to {user}")
                    except Exception as e:
                        print(f"[DEBUG] Failed to DM {user}: {e}")
    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return
        if not hasattr(self, "active_views"):
            return
        for view in self.active_views:
            if message.channel.id == view.thread.id:
                timestamp = message.created_at.strftime("%H:%M:%S")
                # Log text
                if message.content:
                    view.chat_log.append((f"[{timestamp}] {message.author.display_name}", message.content))
                # Log attachments
                for attach in message.attachments:
                    view.chat_log.append((f"[{timestamp}] {message.author.display_name}", attach.url))
                # Log stickers
                for sticker in message.stickers:
                    view.chat_log.append((f"[{timestamp}] {message.author.display_name}", f"<Sticker: {sticker.name}>"))


async def setup(bot: commands.Bot):
    await bot.add_cog(OmegleCog(bot))
