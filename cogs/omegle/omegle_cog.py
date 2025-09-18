# cogs/omegle/omegle_cog.py
import discord
from discord.ext import commands, tasks
from datetime import datetime
from cogs.omegle.omegle_matchmaker import Matchmaker
from cogs.omegle.omegle_ui import StartButtonView, PrivateChatView
from helpers.github_data import load_db, save_db
import os

# CONFIG — replace with your IDs
MALE_ROLE = 1393171022047350845
FEMALE_ROLE = 1393171023024881735
ANYONE_ROLES = [1393171022047350845, 1393171023024881735, 1409995872611209376, 1393171031660957838]
HUB_CHANNEL_ID = 1417923188083654916
LOG_CHANNEL_ID = 1393171149038293023

class OmegleCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.matchmaker = Matchmaker(bot, MALE_ROLE, FEMALE_ROLE, ANYONE_ROLES, HUB_CHANNEL_ID)
        # active_chats maps thread.id -> PrivateChatView
        self.active_chats = {}
        self.check_queue_loop.start()

    def cog_unload(self):
        self.check_queue_loop.cancel()


    @commands.Cog.listener()
    async def on_ready(self):
        # Register persistent start button view (so the button works across restarts)
        try:
            self.bot.add_view(StartButtonView(self.matchmaker))
            print("[OMEGLE] Registered persistent StartButtonView")
        except Exception as e:
            print("[OMEGLE] add_view failed:", e)

    @commands.hybrid_command(name="omegle_send")
    @commands.has_permissions(manage_messages=True)
    async def omegle_send(self, ctx: commands.Context, channel: discord.TextChannel):
        """Send the start button interface to a channel (mod only)."""
        start_view = StartButtonView(self.matchmaker)
        await channel.send(":speech_balloon: Click to start a private chat with a random member", view=start_view)
        try:
            await ctx.reply("✅ Interface sent.", ephemeral=True)
        except Exception:
            # fallback
            await ctx.send("✅ Interface sent.", delete_after=5)

    @tasks.loop(seconds=5)
    async def check_queue_loop(self):
        if not self.bot.is_ready():
            return
        for guild in self.bot.guilds:
            print(f"[DEBUG] check_queue_loop tick {datetime.utcnow().isoformat()} queue={self.matchmaker.queue}")
            while True:
                u1, u2 = await self.matchmaker.find_match(guild)
                if not u1 or not u2:
                    break

                print(f"[DEBUG] Matched {u1} with {u2} in guild {guild.name}")
                thread = await self.matchmaker.create_chat_thread(guild, u1, u2)
                if not thread:
                    print("[OMEGLE] Failed to create private thread for match")
                    continue

                # create PrivateChatView and register active chat
                private_view = PrivateChatView(self.matchmaker, thread, u1, u2, LOG_CHANNEL_ID)
                self.active_chats[thread.id] = private_view

                # send introduction message with view
                try:
                    await thread.send(f":wave: {u1.mention} and {u2.mention} matched! Say hi!", view=private_view)
                except Exception as e:
                    print("[OMEGLE] Failed to send match message:", e)

                # DM notifications
                for m in (u1, u2):
                    try:
                        await m.send(f"You've been matched in **{guild.name}** — check the private thread: {thread.mention}")
                    except Exception:
                        pass

    @check_queue_loop.before_loop
    async def before_queue(self):
        await self.bot.wait_until_ready()

    # Track messages in active private threads (text, attachments, stickers)
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        tid = message.channel.id
        view = self.active_chats.get(tid)
        if not view:
            return

        ts = message.created_at.strftime("%H:%M:%S")
        # text
        if message.content:
            view.chat_log.append((ts, message.author.display_name, message.content))
        # attachments
        for att in message.attachments:
            view.chat_log.append((ts, message.author.display_name, att.url))
        # stickers (StickerItem)
        for st in getattr(message, "stickers", []):
            try:
                view.chat_log.append((ts, message.author.display_name, f"<Sticker: {st.name}>"))
            except Exception:
                view.chat_log.append((ts, message.author.display_name, "<Sticker>"))

        # If the thread was deleted by some external process, remove from active_chats
        try:
            if isinstance(message.channel, discord.Thread) and message.channel.archived:
                # keep it — only cleanup on explicit delete via buttons
                pass
        except Exception:
            pass

    # Clean up active_chats entry when thread deleted externally
    @commands.Cog.listener()
    async def on_thread_delete(self, thread: discord.Thread):
        if thread.id in self.active_chats:
            try:
                self.active_chats.pop(thread.id, None)
            except Exception:
                pass


async def setup(bot: commands.Bot):
    await bot.add_cog(OmegleCog(bot))


