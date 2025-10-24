import discord
from .config import JOIN_CHANNELS, DB_PATH
import aiosqlite

class LimitModal(discord.ui.Modal, title="Set User Limit"):
    def __init__(self, cog, channel):
        super().__init__()
        self.cog = cog
        self.channel = channel
        
        self.limit_input = discord.ui.TextInput(
            label="User Limit (0-99)",
            placeholder="Enter 0 for no limit...",
            default=str(channel.user_limit),
            max_length=2,
            required=True
        )
        self.add_item(self.limit_input)
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            limit = int(self.limit_input.value)
            if limit < 0 or limit > 99:
                await interaction.response.send_message("❌ Limit must be between 0 and 99!", ephemeral=True)
                return
            
            await self.channel.edit(user_limit=limit)
            
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE voiceChannel SET channelLimit = ? WHERE voiceID = ?", (limit, self.channel.id))
                await db.commit()
            
            await interaction.response.send_message(
                f"✅ User limit set to {limit}!" if limit > 0 else "✅ User limit removed!", 
                ephemeral=True
            )
            
        except ValueError:
            await interaction.response.send_message("❌ Please enter a valid number!", ephemeral=True)

class RenameModal(discord.ui.Modal, title="Rename Voice Channel"):
    def __init__(self, cog, channel):
        super().__init__()
        self.cog = cog
        self.channel = channel
        
        self.name_input = discord.ui.TextInput(
            label="New Channel Name",
            placeholder="Enter new name...",
            default=channel.name,
            max_length=100,
            required=True
        )
        self.add_item(self.name_input)
    
    async def on_submit(self, interaction: discord.Interaction):
        name = self.name_input.value.strip()
        if len(name) < 1:
            await interaction.response.send_message("❌ Please provide a valid name!", ephemeral=True)
            return
        
        old_name = self.channel.name
        await self.channel.edit(name=name)
        
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE voiceChannel SET channelName = ? WHERE voiceID = ?", (name, self.channel.id))
            await db.commit()
        
        await interaction.response.send_message(f"✏️ Renamed `{old_name}` to `{name}`", ephemeral=True)

class TransferModal(discord.ui.Modal, title="Transfer Voice Channel"):
    def __init__(self, cog, channel_id):
        super().__init__()
        self.cog = cog
        self.channel_id = channel_id
        
        self.user_input = discord.ui.TextInput(
            label="User ID or Mention",
            placeholder="Enter user ID or paste mention...",
            required=True
        )
        self.add_item(self.user_input)
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            # Extract user ID from input (handles both ID and mention)
            input_text = self.user_input.value.strip()
            if input_text.isdigit():
                user_id = int(input_text)
            else:
                # Try to extract from mention
                user_id = int(input_text.replace('<@', '').replace('>', ''))
            
            member = interaction.guild.get_member(user_id)
            if not member:
                await interaction.response.send_message("❌ User not found!", ephemeral=True)
                return
            
            if member.bot:
                await interaction.response.send_message("❌ You cannot transfer ownership to a bot!", ephemeral=True)
                return
            
            channel = interaction.guild.get_channel(self.channel_id)
            if not channel:
                await interaction.response.send_message("❌ Channel not found!", ephemeral=True)
                return
            
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE voiceChannel SET userID = ? WHERE voiceID = ?", (member.id, channel.id))
                await db.commit()
            
            await interaction.response.send_message(f"🔁 Ownership transferred to {member.mention}!", ephemeral=True)
            
        except ValueError:
            await interaction.response.send_message("❌ Please enter a valid user ID or mention!", ephemeral=True)