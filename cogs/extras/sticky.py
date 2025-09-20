import discord
from discord.ext import commands, tasks
from discord import app_commands
from helpers.github_data import load_db, save_db
import os
import logging

MOD_IDS = list(map(int, os.environ.get("MOD_IDS", "").split(",")))
logging.basicConfig(level=logging.WARNING)


def is_mod():
    async def predicate(interaction: discord.Interaction):
        return (
            interaction.user.id in MOD_IDS
            or getattr(interaction.user.guild_permissions, "manage_messages", False)
        )
    return app_commands.check(predicate)


class StickyEmbedModal(discord.ui.Modal, title="Create Sticky Embed"):
    title_input = discord.ui.TextInput(label="Title", required=False, max_length=256)
    description_input = discord.ui.TextInput(label="Description", style=discord.TextStyle.paragraph, required=True, max_length=2000)
    image_input = discord.ui.TextInput(label="Image URL", required=False)
    author_name_input = discord.ui.TextInput(label="Author Name", required=False)
    author_image_input = discord.ui.TextInput(label="Author Image URL", required=False)
    color_input = discord.ui.TextInput(label="Embed Color (Hex, e.g., #FF0000)", required=False, max_length=7)
    footer_input = discord.ui.TextInput(label="Footer", required=False, max_length=2048)

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
                await interaction.response.send_message("Invalid hex color! Using default.", ephemeral=True)

        embed = discord.Embed(
            title=self.title_input.value or None,
            description=self.description_input.value,
            color=color,
        )
        if self.image_input.value:
            embed.set_image(url=self.image_input.value)
        if self.author_name_input.value:
            embed.set_author(name=self.author_name_input.value, icon_url=self.author_image_input.value or None)
        if self.footer_input.value:
            embed.set_footer(text=self.footer_input.value)

        await self.sticky_manager.set_sticky(
            self.channel,
            self.description_input.value,
            embed_mode=True,
            title=self.title_input.value or None,
            footer=self.footer_input.value
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
        db, _ = load_db("sticky.json")
        self.stickies = db.get("stickies", {})
        print(f"[StickyManager] Loaded stickies for {len(self.stickies)} channels.")

    # ----------------------------
    # Helpers
    # ----------------------------
    def save(self):
        db, file = load_db("sticky.json")
        db["stickies"] = self.stickies
        save_db(db, file_path="sticky.json")

    async def _post_sticky(self, channel: discord.TextChannel, content: str, embed_mode=False, title=None, footer=None):
        """Send a sticky message to channel"""
        if embed_mode:
            embed = discord.Embed(description=content, color=discord.Color(0x747c8b))
            if title:
                embed.title = title
            if footer:
                embed.set_footer(text=footer)
            return await channel.send(embed=embed)
        else:
            return await channel.send(content)

    async def set_sticky(self, channel: discord.TextChannel, content: str, embed_mode=False, title=None, footer=None):
        cid = str(channel.id)

        # delete old sticky if exists
        if cid in self.stickies:
            try:
                prev = await channel.fetch_message(self.stickies[cid]["message_id"])
                await prev.delete()
            except discord.NotFound:
                pass

        msg = await self._post_sticky(channel, content, embed_mode, title, footer)

        # store
        self.stickies[cid] = {
            "message_id": msg.id,
            "content": content,
            "embed": embed_mode,
            "title": title,
            "footer": footer,
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

        msg = await self._post_sticky(channel, info["content"], info.get("embed"), info.get("title"), info.get("footer"))
        self.stickies[str(channel.id)]["message_id"] = msg.id
        self.save()

    # ----------------------------
    # Events
    # ----------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        cid = str(message.channel.id)
        info = self.stickies.get(cid)
        if not info or not info.get("active", True):
            return
        await self.repost_sticky(message.channel, info)

    @tasks.loop(minutes=1)
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
    await bot.add_cog(StickyManager(bot))
