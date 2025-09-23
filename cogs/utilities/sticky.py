import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import json
import logging
from pathlib import Path
from helpers.is_mod import is_mod
logging.basicConfig(level=logging.WARNING)

DATA_FILE = Path("data/sticky.json")
DATA_FILE.parent.mkdir(exist_ok=True)

def load_sticky_db() -> dict:
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text())
        except json.JSONDecodeError:
            return {}
    return {}

def save_sticky_db(data: dict):
    DATA_FILE.write_text(json.dumps(data, indent=2))


class StickyEmbedModal(discord.ui.Modal, title="Create Sticky Embed"):
    title_input = discord.ui.TextInput(label="Title", required=False, max_length=256)
    description_input = discord.ui.TextInput(
        label="Description", style=discord.TextStyle.paragraph, required=True, max_length=2000
    )
    image_input = discord.ui.TextInput(label="Image URL", required=False)
    author_image_input = discord.ui.TextInput(label="Author Image URL", required=False)
    color_input = discord.ui.TextInput(label="Embed Color (Hex, e.g., #FF0000)", required=False, max_length=7)

    def __init__(self, sticky_manager: "StickyManager", channel: discord.TextChannel):
        super().__init__()
        self.sticky_manager = sticky_manager
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction):
        color = 0x747c8b  # default
        if self.color_input.value:
            try:
                color = int(self.color_input.value.lstrip("#"), 16)
            except ValueError:
                await interaction.response.send_message(
                    "Invalid hex color! Using default.", ephemeral=True
                )

        await self.sticky_manager.set_sticky(
            self.channel,
            self.description_input.value,
            embed_mode=True,
            title=self.title_input.value or None,
            color=color,
            author_icon=self.author_image_input.value or None,
            image_url=self.image_input.value or None
        )

        await interaction.response.send_message("✅ Sticky embed set!", ephemeral=True)


class StickyManager(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.stickies: dict = {}
        self.check_stickies_loop.start()

    def cog_unload(self):
        self.check_stickies_loop.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        db = load_sticky_db()
        self.stickies = db.get("stickies", {})
        print(f"[StickyManager] Loaded stickies for {len(self.stickies)} channels.")

    # ----------------------------
    # Helpers
    # ----------------------------
    def save(self):
        db = load_sticky_db()
        db["stickies"] = self.stickies
        save_sticky_db(db)

    async def _post_sticky(self, channel: discord.TextChannel, content: str, embed_mode=False,
                           title=None, footer=None, color=None, author_icon=None, image_url=None):
        if embed_mode:
            embed_color = color or 0x747c8b
            embed = discord.Embed(description=content, color=discord.Color(embed_color))
            if title:
                embed.title = title
            if footer:
                embed.set_footer(text=footer)
            if author_icon:
                embed.set_author(icon_url=author_icon)
            if image_url:
                embed.set_image(url=image_url)
            return await channel.send(embed=embed)
        else:
            return await channel.send(content)

    async def set_sticky(
        self,
        channel: discord.TextChannel,
        content: str,
        embed_mode=False,
        title=None,
        footer=None,
        color=None,
        author_icon=None,
        image_url=None
    ):
        cid = str(channel.id)

        # Delete previous sticky if exists
        if cid in self.stickies:
            try:
                prev_msg_id = self.stickies[cid].get("message_id")
                if prev_msg_id:
                    prev = await channel.fetch_message(prev_msg_id)
                    await prev.delete()
            except discord.NotFound:
                pass

        # Send new sticky
        msg = await self._post_sticky(channel, content, embed_mode, title, footer, color, author_icon, image_url)

        # Store the new sticky info
        self.stickies[cid] = {
            "message_id": msg.id,
            "content": content,
            "embed": embed_mode,
            "title": title,
            "footer": footer,
            "color": color,
            "author_icon": author_icon,
            "image_url": image_url,
            "active": True,
        }

        self.save()


    async def repost_sticky(self, channel: discord.TextChannel, info: dict):
        """Delete old sticky and re-send"""
        if info.get("message_id"):
            try:
                prev = await channel.fetch_message(info["message_id"])
                await prev.delete()
            except discord.NotFound:
                pass

        msg = await self._post_sticky(
            channel,
            info["content"],
            info.get("embed"),
            info.get("title"),
            info.get("footer"),
            info.get("color"),
            info.get("author_icon"),
            info.get("image_url")
        )
        self.stickies[str(channel.id)]["message_id"] = msg.id
        self.save()

    # ----------------------------
    # Events
    # ----------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot and not message.attachments:
           return
        cid = str(message.channel.id)
        info = self.stickies.get(cid)
        if not info or not info.get("active", True):
            return
        await self.repost_sticky(message.channel, info)

    @tasks.loop(seconds=30)
    async def check_stickies_loop(self):
        for cid, info in list(self.stickies.items()):
            if not info.get("active", True):
                continue
            ch = self.bot.get_channel(int(cid))
            if not ch:
                continue
            try:
                if ch.last_message_id:
                    last = await ch.fetch_message(ch.last_message_id)
                    if last.id != info["message_id"]:
                        await self.repost_sticky(ch, info)
            except Exception as e:
                logging.error(f"Sticky check error in {cid}: {e}")

    # ----------------------------
    # Slash Command Group
    # ----------------------------
    sticky = app_commands.Group(name="sticky", description="Manage sticky messages")

    @sticky.command(name="set", description="Set a sticky message (plain text)")
    @is_mod()
    async def sticky_set(self, interaction: discord.Interaction, content: str):
        await self.set_sticky(interaction.channel, content, embed_mode=False)
        await interaction.response.send_message("✅ Sticky message set!", ephemeral=True)

    @sticky.command(name="set_embed", description="Set a sticky message (embed) via modal")
    @is_mod()
    async def sticky_set_embed(self, interaction: discord.Interaction):
        """Open modal for embed creation"""
        await interaction.response.send_modal(StickyEmbedModal(self, interaction.channel))

    @sticky.command(name="remove", description="Remove sticky in this channel")
    @is_mod()
    async def sticky_remove(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        cid = str(interaction.channel.id)
        if cid in self.stickies:
            info = self.stickies.pop(cid)
            self.save()
            try:
                old_msg = await interaction.channel.fetch_message(info["message_id"])
                await old_msg.delete()
            except discord.NotFound:
                pass
            await interaction.followup.send("✅ Sticky removed.", ephemeral=True)
        else:
            await interaction.followup.send("❌ No sticky here.", ephemeral=True)

    @sticky.command(name="disable", description="Disable sticky in this channel")
    @is_mod()
    async def sticky_disable(self, interaction: discord.Interaction):
        cid = str(interaction.channel.id)
        if cid in self.stickies:
            self.stickies[cid]["active"] = False
            self.save()
            await interaction.response.send_message("⏸ Sticky disabled.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ No sticky here.", ephemeral=True)

    @sticky.command(name="enable", description="Enable sticky in this channel")
    @is_mod()
    async def sticky_enable(self, interaction: discord.Interaction):
        cid = str(interaction.channel.id)
        if cid in self.stickies:
            self.stickies[cid]["active"] = True
            self.save()
            await interaction.response.send_message("▶ Sticky enabled.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ No sticky here.", ephemeral=True)

    @sticky.command(name="list", description="List all sticky messages in this server")
    @is_mod()
    async def sticky_list(self, interaction: discord.Interaction):
        if not self.stickies:
            await interaction.response.send_message("No stickies set.", ephemeral=True)
            return

        embed = discord.Embed(title="Sticky Messages", color=discord.Color.blue())
        for cid, info in self.stickies.items():
            ch = self.bot.get_channel(int(cid))
            status = "Active" if info.get("active", True) else "Inactive"
            title = f" | {info['title']}" if info.get("title") else ""
            embed.add_field(
                name=f"#{ch.name if ch else cid}{title}",
                value=f"Status: {status}\nMessage: {info['content']}",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await asyncio.sleep(5)
    await bot.add_cog(StickyManager(bot))
