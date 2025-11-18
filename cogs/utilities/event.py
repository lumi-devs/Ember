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
        if os.path.exists(self.data_file):
            with open(self.data_file, 'r') as f:
                self.submissions = json.load(f)
        else:
            self.submissions = {"user_ids": []}
    
    def save_data(self):
        with open(self.data_file, 'w') as f:
            json.dump(self.submissions, f, indent=4)
    
    class SubmissionModal(discord.ui.Modal, title='Event Submission (Anonymous)'):
        def __init__(self, cog):
            super().__init__()
            self.cog = cog
            
        answer1 = discord.ui.TextInput(
            label='❤️ The One You Love the Most',
            placeholder='Enter your answer here',
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000
        )
        
        answer2 = discord.ui.TextInput(
            label='💘 The One You\'d Date',
            placeholder='Enter your answer here',
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000
        )
        
        answer3 = discord.ui.TextInput(
            label='🤣 Funniest Member',
            placeholder='Enter your answer here',
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000
        )
        
        answer4 = discord.ui.TextInput(
            label='😎 Most Cocky Member',
            placeholder='Enter your answer here',
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000
        )

        answer5 = discord.ui.TextInput(
            label='😤 Most Annoying Member',
            placeholder='Enter your answer here',
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000
        )
        
        async def on_submit(self, interaction: discord.Interaction):
            # Defer the response immediately
            await interaction.response.defer(thinking=True, ephemeral=True)
            
            if str(interaction.user.id) in self.cog.submissions["user_ids"]:
                await interaction.followup.send("You have already submitted an entry!", ephemeral=True)
                return
            
            try:
                entry_number = len(self.cog.submissions["user_ids"]) + 1
                
                self.cog.submissions["user_ids"].append(str(interaction.user.id))
                self.cog.save_data()
                
                await self.cog.send_to_secret_channel(interaction, entry_number, {
                    "Q1": self.answer1.value,
                    "Q2": self.answer2.value,
                    "Q3": self.answer3.value,
                    "Q4": self.answer4.value,
                    "Q5": self.answer5.value
                })
                
                await interaction.followup.send("Your submission has been received! Thank you!", ephemeral=True)
                
            except Exception as e:
                await interaction.followup.send("An error occurred while processing your submission. Please try again.", ephemeral=True)
                print(f"Error in submission: {e}")
    
    async def send_to_secret_channel(self, interaction, entry_number, answers):
        if self.submission_channel_id is None:
            for channel in interaction.guild.channels:
                if "submission" in channel.name.lower() and isinstance(channel, discord.TextChannel):
                    self.submission_channel_id = channel.id
                    break
        
        if self.submission_channel_id:
            channel = self.bot.get_channel(self.submission_channel_id)
            if channel:
                embed = discord.Embed(
                    title=f"ENTRY NO {entry_number}",
                    color=0xFFA500
                )
                
                question_labels = {
                    "Q1": "❤️ The One You Love the Most",
                    "Q2": "💘 The One You'd Date", 
                    "Q3": "🤣 Funniest Member",
                    "Q4": "😎 Most Cocky Member",
                    "Q5": "😤 Most Annoying Member"
                }
                
                for q_key, answer in answers.items():
                    embed.add_field(
                        name=question_labels[q_key],
                        value=f"→ {answer}",
                        inline=False
                    )
                
                embed.set_footer(text=f"Submission #{entry_number} • Received")
                
                await channel.send(embed=embed)
    
    class SubmissionView(discord.ui.View):
        def __init__(self, cog):
            super().__init__(timeout=None)
            self.cog = cog
        
        @discord.ui.button(label="Submit Entry", style=discord.ButtonStyle.primary, emoji="📝", custom_id="submit_entry_button")
        async def submit_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            # Defer the response immediately to avoid interaction timeout
            await interaction.response.defer(ephemeral=True)
            
            if str(interaction.user.id) in self.cog.submissions["user_ids"]:
                await interaction.followup.send("You have already submitted an entry!", ephemeral=True)
                return
            
            modal = self.cog.SubmissionModal(self.cog)
            # Send modal as followup since we already deferred
            await interaction.followup.send_modal(modal)

    async def setup_hook(self):
        """This is called when the cog is loaded to add the persistent view"""
        self.bot.add_view(self.SubmissionView(self))

    @commands.command(name="send")
    @commands.has_permissions(administrator=True)
    async def send_submission_message(self, ctx):
        embed = discord.Embed(
            title="Event Submissions",
            description="Click the button below to submit your anonymous entry for the event!",
            color=0xFFA500
        )
        
        view = self.SubmissionView(self)
        await ctx.send(embed=embed, view=view)
        
        await ctx.send("✅ Submission message sent successfully!", delete_after=5)

async def setup(bot):
    cog = SubmitCog(bot)
    await bot.add_cog(cog)
    # Register the persistent view when the cog is loaded
    bot.add_view(cog.SubmissionView(cog))