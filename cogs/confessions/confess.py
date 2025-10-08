import discord
from discord import ui, app_commands
from discord.ext import commands
from datetime import datetime
import asyncio
import aiosqlite
import re

from cogs.confessions.helpers import DB_PATH, extract_url, init_db, process_media
from cogs.confessions.reply import ReplyModal

# Channels
CONFESSION_CHANNEL_ID = 1399658407178272798
REPORT_CHANNEL_ID = 1393171149038293023
LOG_CHANNEL_ID = 1424792373837172899
MEDIA_CHANNEL_ID =  1424139009931743382
ALLOWED_ROLE_IDS=[1393171037562077196, 1393171036635402354, 1393171035695612055, 1393171034701561908, 1393171033774624848, 1393171032726306966]


class ConfessionModal(ui.Modal, title="Submit a Confession"):
    title_input = ui.TextInput(label="Title", max_length=100, required=False)
    description_input = ui.TextInput(label="Description", style=discord.TextStyle.paragraph, max_length=1000)

    def __init__(self, bot):
        super().__init__()
        self.bot = bot
        self.user_id = None

    async def on_submit(self, interaction: discord.Interaction):
        self.user_id = interaction.user.id
        channel = self.bot.get_channel(CONFESSION_CHANNEL_ID)
        log_channel = self.bot.get_channel(LOG_CHANNEL_ID)
        user = await self.bot.fetch_user(self.user_id)

        # Check blacklist
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT 1 FROM confessban WHERE user_id=?", (user.id,))
            if await cursor.fetchone():
                return await interaction.response.send_message("❌ You are banned.", ephemeral=True)

        # Insert confession
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "INSERT INTO confessions (user_id, title, description) VALUES (?, ?, ?)",
                (user.id, self.title_input.value, self.description_input.value)
            )
            await db.commit()
            confession_id = cursor.lastrowid

        # Send embed
        embed = discord.Embed(
            title=f"💌 {self.title_input.value or f'#{confession_id}'}",
            description=self.description_input.value,
            color=discord.Color.purple(),
            timestamp=datetime.utcnow()
        )
        embed.set_footer(text=f"Confession #{confession_id}")
        msg = await channel.send(embed=embed)

        # Save message_id
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE confessions SET message_id=? WHERE id=?", (msg.id, confession_id))
            await db.commit()

        await interaction.response.send_message(
            "✅ Confession submitted! You have 20s to send an image/URL or type `skip`.", ephemeral=True
        )
        cleaner_cog = self.bot.get_cog("ConfessionCleaner")
        if cleaner_cog:
            await cleaner_cog.allow_user_once(user.id)

        def check(m):
            return m.author.id == user.id and m.channel.id == CONFESSION_CHANNEL_ID

        try:
            msg_received = await self.bot.wait_for("message", timeout=20.0, check=check)
            image_url = await process_media(self.bot, msg_received.content, msg_received.attachments, MEDIA_CHANNEL_ID)
        except asyncio.TimeoutError:
            image_url = None
        finally:
            # delete user’s message to keep channel clean
            if 'msg_received' in locals():
                try:
                    await asyncio.sleep(2)
                    await msg_received.delete()
                except:
                    pass

        if image_url:
            embed.set_image(url=image_url)

        # Attach persistent buttons
        await msg.edit(embed=embed, view=ConfessionView(self.bot, confession_id, msg.id))

        # Log confession
        if log_channel:
            log_embed = discord.Embed(
                title=f"Confession #{confession_id}",
                description=self.description_input.value,
                color=discord.Color.purple(),
                timestamp=datetime.utcnow()
            )
            user = await self.bot.fetch_user(user.id)
            log_embed.add_field(name="User", value=user.mention, inline=False)
            if image_url:
                log_embed.set_image(url=image_url)
            await log_channel.send(f'#{confession_id}',embed=log_embed)


class ConfessionView(ui.View):
    def __init__(self, bot, confession_id, message_id=None):
        super().__init__(timeout=None)
        self.bot = bot
        self.confession_id = confession_id
        self.message_id = message_id

    @ui.button(label="Reply", style=discord.ButtonStyle.primary, custom_id="confession_reply")
    async def reply(self, interaction: discord.Interaction, button: ui.Button):
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT message_id FROM confessions WHERE id=?", (self.confession_id,))
            row = await cursor.fetchone()
            if not row:
                return await interaction.response.send_message("❌ Confession not found.", ephemeral=True)
            message_id = row[0]

        modal = ReplyModal(self.bot, self.confession_id, message_id)
        await interaction.response.send_modal(modal)

    @ui.button(label="Report", style=discord.ButtonStyle.danger, custom_id="confession_report")
    async def report(self, interaction: discord.Interaction, button: ui.Button):
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "SELECT user_id, title, description FROM confessions WHERE id=?", (self.confession_id,)
            )
            confession = await cursor.fetchone()
            if not confession:
                return await interaction.response.send_message("❌ Confession not found.", ephemeral=True)

            author_id, title, description = confession

            # Insert report
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
        embed.add_field(name="Title", value=title, inline=False)
        embed.add_field(name="Content", value=description, inline=False)

        mod_channel = self.bot.get_channel(REPORT_CHANNEL_ID)
        if mod_channel:
            await mod_channel.send("<@&1393170995157798983> <@&1406325951264194615>", embed=embed)

        await interaction.response.send_message("✅ Report submitted.", ephemeral=True)


class Confessions(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bot.loop.create_task(init_db())
        self.bot.loop.create_task(self.load_views())

    async def load_views(self):
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT id, message_id FROM confessions")
            rows = await cursor.fetchall()
            for row in rows:
                confession_id, message_id = row
                self.bot.add_view(ConfessionView(self.bot, confession_id, message_id))

    @app_commands.command(name="confess", description="Submit an anonymous confession")
    async def confess_command(self, interaction: discord.Interaction):
        # 1️⃣ Check channel

        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT 1 FROM confessban WHERE user_id=?", (interaction.user.id,))
            if await cursor.fetchone():
                return await interaction.response.send_message(
                    "❌ You are banned from making confessions.", ephemeral=True
                )

        if interaction.channel.id != CONFESSION_CHANNEL_ID:
            return await interaction.response.send_message(
                f"❌ Please use this command in <#{CONFESSION_CHANNEL_ID}>.", ephemeral=True
            )

        # 2️⃣ Check roles
        if not any(role.id in ALLOWED_ROLE_IDS for role in interaction.user.roles):
            return await interaction.response.send_message(
                "❌ You do not have atleast LVL 5 required to make confessions", ephemeral=True
            )

        # 3️⃣ Show modal
        modal = ConfessionModal(self.bot)
        await interaction.response.send_modal(modal)


async def setup(bot):
    await asyncio.sleep(2)
    await bot.add_cog(Confessions(bot))
