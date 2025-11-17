import discord
from discord.ext import commands
from discord import app_commands
import json
import os

class SubmitCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.data_file = "submissions.json"
        self.submission_channel_id = 1439696653878820864
        self.load_data()
    
    def load_data(self):
        """Load submission data from JSON file"""
        if os.path.exists(self.data_file):
            with open(self.data_file, 'r') as f:
                self.submissions = json.load(f)
        else:
            self.submissions = {"entries": [], "user_ids": []}
    
    def save_data(self):
        """Save submission data to JSON file"""
        with open(self.data_file, 'w') as f:
            json.dump(self.submissions, f, indent=4)
    
    class SubmissionModal(discord.ui.Modal, title='Event Submission (Anonymous)'):
        def __init__(self, cog):
            super().__init__()
            self.cog = cog
            
        answer1 = discord.ui.TextInput(
            label='❤️ The One You Love the Most',
            placeholder='Enter your answer here ',
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000
        )
        
        answer2 = discord.ui.TextInput(
            label='💘 The One You’d Date',
            placeholder='Enter your answer here ',
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000
        )
        
        answer3 = discord.ui.TextInput(
            label='🤣 Funniest Member',
            placeholder='Enter your answer here ',
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000
        )
        
        answer4 = discord.ui.TextInput(
            label='😎 Most Cocky Member',
            placeholder='Enter your answer here ',
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000
        )

        answer5 = discord.ui.TextInput(
            label='😤 Most Annoying Member',
            placeholder='Enter your answer here ',
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000
        )
        
        async def on_submit(self, interaction: discord.Interaction):
            # Defer the response
            await interaction.response.defer(thinking=True, ephemeral=True)
            
            # Check if user has already submitted
            if str(interaction.user.id) in self.cog.submissions["user_ids"]:
                await interaction.followup.send("You have already submitted an entry!", ephemeral=True)
                return
            
            # Create entry
            entry_number = len(self.cog.submissions["entries"]) + 1
            entry_data = {
                "entry_number": entry_number,
                "user_id": str(interaction.user.id),
                "answers": {
                    "Q1": self.answer1.value,
                    "Q2": self.answer2.value,
                    "Q3": self.answer3.value,
                    "Q4": self.answer4.value
                }
            }
            
            # Save to data
            self.cog.submissions["entries"].append(entry_data)
            self.cog.submissions["user_ids"].append(str(interaction.user.id))
            self.cog.save_data()
            
            # Send to secret channel
            await self.cog.send_to_secret_channel(interaction, entry_data)
            
            await interaction.followup.send("Your submission has been received! Thank you!", ephemeral=True)
    
    async def send_to_secret_channel(self, interaction, entry_data):
        """Send submission to secret channel as embed"""
        if self.submission_channel_id is None:
            # If channel ID not set, try to find a channel named "submissions"
            for channel in interaction.guild.channels:
                if "submission" in channel.name.lower() and isinstance(channel, discord.TextChannel):
                    self.submission_channel_id = channel.id
                    break
        
        if self.submission_channel_id:
            channel = self.bot.get_channel(self.submission_channel_id)
            if channel:
                embed = discord.Embed(
                    title=f"ENTRY NO {entry_data['entry_number']}",
                    color=0xFFA500  # Orange color
                )
                
                for i, (question, answer) in enumerate(entry_data["answers"].items(), 1):
                    embed.add_field(
                        name=f"Q{i}",
                        value=f"→ {answer}",
                        inline=False
                    )
                
                # Add timestamp but no user info
                embed.set_footer(text="New Submission")
                
                await channel.send(embed=embed)
    
    @app_commands.command(name="submit_entry", description="Submit your event entry")
    async def submit_entry(self, interaction: discord.Interaction):
        """Submit command that opens a modal for event submission"""
        
        # Check if user has already submitted
        if str(interaction.user.id) in self.submissions["user_ids"]:
            await interaction.response.send_message("You have already submitted an entry!", ephemeral=True)
            return
        
        # Open the modal
        modal = self.SubmissionModal(self)
        await interaction.response.send_modal(modal)

async def setup(bot):
    await bot.add_cog(SubmitCog(bot))