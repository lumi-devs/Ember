import discord
from discord.ext import commands, tasks
from discord import app_commands
from helpers.github_data import load_db, save_db
import os
import logging

MOD_IDS = list(map(int, os.environ.get("MOD_IDS", "").split(",")))
logging.basicConfig(level=logging.WARNING)

# ----------------------------
# Permission check decorator
# ----------------------------
def is_mod():
    async def predicate(interaction: discord.Interaction):
        return (
            interaction.user.id in MOD_IDS
            or getattr(interaction.user.guild_permissions, "manage_messages", False)
        )
    return app_commands.check(predicate)


class StickyManager(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.stickies: dict = {}
        self.check_stickies_loop.start()

    def cog_unload(self):
        self.check_stickies_loop.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        db, _ = load_db()
        self.stickies = db.get("stickies", {})
        print(f"[StickyManager] Loaded stickies for {len(self.stickies)} channels.")

    # ----------------------------
    # Helpers
    # ----------------------------
    async def set_sticky(self, channel: discord.TextChannel, content: str, embed_mode=False):
        cid = str(channel.id)

        if cid in self.stickies:
            old = self.stickies[cid]
            try:
                prev = await channel.fetch_message(old["message_id"])
                await prev.delete()
            except discord.NotFound:
                pass

        if embed_mode:
            embed = discord.Embed(description=content, color=discord.Color(0x747c8b))
            embed.title = "Sticky Message"
            msg = await channel.send(embed=embed)
        else:
            msg = await channel.send(content)

        self.stickies[cid] = {
            "message_id": msg.id,
            "content": content,
            "embed": embed_mode,
            "active": True,
        }

        db, file = load_db()
        db["stickies"] = self.stickies
        save_db(db, file)

    # ----------------------------
    # Maintain stickies
    # ----------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        cid = str(message.channel.id)
        info = self.stickies.get(cid)
        if not info or not info.get("active", True):
            return

        if info.get("message_id"):
            try:
                prev = await message.channel.fetch_message(info["message_id"])
                await prev.delete()
            except discord.NotFound:
                pass

        if info.get("embed"):
            embed = discord.Embed(description=info["content"], color=discord.Color(0x747c8b))
            embed.title = "Sticky Message"
            new_msg = await message.channel.send(embed=embed)
        else:
            new_msg = await message.channel.send(info["content"])

        self.stickies[cid]["message_id"] = new_msg.id
        db, file = load_db()
        db["stickies"] = self.stickies
        save_db(db, file)

    @tasks.loop(minutes=1)
    async def check_stickies_loop(self):
        for cid, info in list(self.stickies.items()):
            if not info.get("active", True):
                continue
            ch = self.bot.get_channel(int(cid))
            if not ch:
                continue
            try:
                last = await ch.fetch_message(ch.last_message_id)
                if last.id != info["message_id"]:
                    await self.on_message(last)
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

    @sticky.command(name="set_embed", description="Set a sticky message (embed)")
    @is_mod()
    async def sticky_set_embed(self, interaction: discord.Interaction, content: str):
        await self.set_sticky(interaction.channel, content, embed_mode=True)
        await interaction.response.send_message("✅ Sticky embed set!", ephemeral=True)

    @sticky.command(name="remove", description="Remove sticky in this channel")
    @is_mod()
    async def sticky_remove(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        cid = str(interaction.channel.id)
        if cid in self.stickies:
            info = self.stickies.pop(cid)
            db, file = load_db()
            db["stickies"] = self.stickies
            save_db(db, file)
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
            db, file = load_db()
            db["stickies"] = self.stickies
            save_db(db, file)
            await interaction.response.send_message("⏸ Sticky disabled.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ No sticky here.", ephemeral=True)

    @sticky.command(name="enable", description="Enable sticky in this channel")
    @is_mod()
    async def sticky_enable(self, interaction: discord.Interaction):
        cid = str(interaction.channel.id)
        if cid in self.stickies:
            self.stickies[cid]["active"] = True
            db, file = load_db()
            db["stickies"] = self.stickies
            save_db(db, file)
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
            embed.add_field(
                name=f"#{ch.name if ch else cid}",
                value=f"Status: {status}\nMessage: {info['content']}",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(StickyManager(bot))
