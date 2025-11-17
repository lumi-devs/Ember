import discord
from discord.ext import commands
from discord import app_commands
import json
import os

class SubmitCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.data_file = "submissions.json"
        self.submission_channel_id = 1439696653878820864  # Set this to your secret channel ID
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
                    "Q4": self.answer4.value,
                    "Q5": self.answer5.value
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
    
    class SubmissionView(discord.ui.View):
        def __init__(self, cog):
            super().__init__(timeout=None)  # Persistent view
            self.cog = cog
        
        @discord.ui.button(label="Submit Entry", style=discord.ButtonStyle.primary, emoji="📝", custom_id="submit_entry_button")
        async def submit_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            # Check if user has already submitted
            if str(interaction.user.id) in self.cog.submissions["user_ids"]:
                await interaction.response.send_message("You have already submitted an entry!", ephemeral=True)
                return
            
            # Open the modal
            modal = self.cog.SubmissionModal(self.cog)
            await interaction.response.send_modal(modal)
    
    @commands.command(name="send")
    @commands.has_permissions(administrator=True)
    async def send_submission_message(self, ctx):
        """Admin command to send the submission message with button"""
        embed = discord.Embed(
            title="Event Submissions",
            description="Click the button below to submit your anonymous entry for the event!",
            color=0xFFA500  # Orange color
        )
        
        view = self.SubmissionView(self)
        await ctx.send(embed=embed, view=view)
        
        # Confirm to admin
        await ctx.send("✅ Submission message sent successfully!", delete_after=5)

async def setup(bot):
    await bot.add_cog(SubmitCog(bot))