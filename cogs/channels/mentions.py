import discord
from discord import app_commands
from discord.ext import commands, tasks
from datetime import datetime, timedelta
import json
import os
import asyncio

class MentionTracker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.tracked_role_ids = [123456789, 987654321, 112233445]  # Replace with your role IDs
        self.data_file = "data/mentions.json"
        self.mentions_data = {}
        self.setup_data_directory()
        
    def setup_data_directory(self):
        """Create data directory if it doesn't exist"""
        os.makedirs("data", exist_ok=True)
        self.load_mentions_data()
    
    def load_mentions_data(self):
        """Load mentions data from file"""
        try:
            if os.path.exists(self.data_file):
                with open(self.data_file, 'r') as f:
                    self.mentions_data = json.load(f)
                print(f"[MentionTracker] Loaded mentions data from {self.data_file}")
            else:
                self.mentions_data = {}
                print("[MentionTracker] Created new mentions data file")
        except Exception as e:
            print(f"[MentionTracker] Error loading data: {e}")
            self.mentions_data = {}
    
    def save_mentions_data(self):
        """Save mentions data to file"""
        try:
            with open(self.data_file, 'w') as f:
                json.dump(self.mentions_data, f, indent=2)
        except Exception as e:
            print(f"[MentionTracker] Error saving data: {e}")
    
    async def cog_load(self):
        """Called when cog is loaded"""
        print("[MentionTracker] Cog loaded, starting background scan...")
        # Start initial scan in background
        asyncio.create_task(self.initial_scan())
    
    async def initial_scan(self):
        """Initial scan of recent messages when bot starts"""
        await self.bot.wait_until_ready()
        print("[MentionTracker] Starting initial message scan...")
        await self.scan_recent_messages()
    
    async def scan_recent_messages(self):
        """Scan recent messages across all guilds"""
        for guild in self.bot.guilds:
            print(f"[MentionTracker] Scanning recent messages in {guild.name}...")
            await self.scan_guild_messages(guild)
    
    async def scan_guild_messages(self, guild: discord.Guild):
        """Scan messages in a specific guild"""
        today = datetime.now().date()
        date_key = today.isoformat()
        
        # Initialize today's data if not exists
        if date_key not in self.mentions_data:
            self.mentions_data[date_key] = {str(role_id): 0 for role_id in self.tracked_role_ids}
        
        # Scan all text channels
        for channel in guild.text_channels:
            if not channel.permissions_for(guild.me).read_messages:
                continue
            
            try:
                # Scan messages from today (last 6 hours to be safe)
                start_time = datetime.now() - timedelta(hours=6)
                async for message in channel.history(limit=2000, after=start_time):
                    await self.process_message_for_mentions(message, date_key)
                    
            except Exception as e:
                print(f"[MentionTracker] Error scanning #{channel.name}: {e}")
        
        self.save_mentions_data()
        print(f"[MentionTracker] Finished scanning {guild.name}")
    
    async def process_message_for_mentions(self, message: discord.Message, date_key: str):
        """Process a single message for role mentions"""
        for role_id in self.tracked_role_ids:
            role_mention = f"<@&{role_id}>"
            if role_mention in message.content:
                # Increment count for this role on this date
                role_str = str(role_id)
                if role_str in self.mentions_data[date_key]:
                    self.mentions_data[date_key][role_str] += 1
                else:
                    self.mentions_data[date_key][role_str] = 1
    
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listen for new messages and update counts in real-time"""
        if message.author.bot:
            return
        
        today = datetime.now().date()
        date_key = today.isoformat()
        
        # Initialize today's data if not exists
        if date_key not in self.mentions_data:
            self.mentions_data[date_key] = {str(role_id): 0 for role_id in self.tracked_role_ids}
        
        # Check for role mentions
        mention_found = False
        for role_id in self.tracked_role_ids:
            role_mention = f"<@&{role_id}>"
            if role_mention in message.content:
                role_str = str(role_id)
                self.mentions_data[date_key][role_str] += 1
                mention_found = True
        
        # Save only if mentions were found
        if mention_found:
            self.save_mentions_data()
    
    @app_commands.command(name="mentions_today", description="Check today's role mentions (instant results)")
    async def mentions_today(self, interaction: discord.Interaction):
        """Instantly show today's mention counts from stored data"""
        await interaction.response.defer(ephemeral=True)
        
        try:
            today = datetime.now().date()
            date_key = today.isoformat()
            
            # Get role objects
            target_roles = []
            for role_id in self.tracked_role_ids:
                role = interaction.guild.get_role(role_id)
                if role:
                    target_roles.append(role)
            
            if not target_roles:
                await interaction.followup.send("❌ No valid roles found from hardcoded IDs.", ephemeral=True)
                return
            
            # Get counts from stored data
            results = []
            total_mentions = 0
            
            if date_key in self.mentions_data:
                for role in target_roles:
                    role_str = str(role.id)
                    count = self.mentions_data[date_key].get(role_str, 0)
                    results.append((role, count))
                    total_mentions += count
            else:
                # No data for today yet
                for role in target_roles:
                    results.append((role, 0))
            
            # Create results embed
            embed = discord.Embed(
                title="📊 Role Mentions Today",
                description=f"Live mention counts for {today.strftime('%B %d, %Y')}",
                color=discord.Color.green(),
                timestamp=datetime.now()
            )
            
            for role, count in results:
                embed.add_field(
                    name=f"{role.name}",
                    value=f"**{count}** mentions",
                    inline=True
                )
            
            embed.add_field(
                name="📈 Total",
                value=f"**{total_mentions}** mentions today",
                inline=False
            )
            
            embed.set_footer(text="Updated in real-time • Monitoring all channels")
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            await interaction.followup.send(f"❌ Error checking mentions: {str(e)}", ephemeral=True)
    
    @app_commands.command(name="rescan_mentions", description="Rescan all channels for today's mentions (Admin only)")
    async def rescan_mentions(self, interaction: discord.Interaction):
        """Force rescan all channels for mentions"""
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ You need administrator permissions to use this command!", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True)
        
        embed = discord.Embed(
            title="🔍 Rescanning Mentions...",
            description="Scanning all channels for today's role mentions...\nThis may take a while.",
            color=discord.Color.blue()
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        
        # Rescan messages
        await self.scan_guild_messages(interaction.guild)
        
        # Show updated results
        today = datetime.now().date()
        date_key = today.isoformat()
        
        results_embed = discord.Embed(
            title="✅ Rescan Complete",
            description=f"Updated mention counts for {today.strftime('%B %d, %Y')}",
            color=discord.Color.green()
        )
        
        total_mentions = 0
        if date_key in self.mentions_data:
            for role_id in self.tracked_role_ids:
                role = interaction.guild.get_role(role_id)
                if role:
                    count = self.mentions_data[date_key].get(str(role_id), 0)
                    results_embed.add_field(name=role.name, value=f"**{count}**", inline=True)
                    total_mentions += count
        
        results_embed.add_field(name="Total", value=f"**{total_mentions}** mentions", inline=False)
        
        await interaction.edit_original_response(embed=results_embed)

async def setup(bot):
    await bot.add_cog(MentionTracker(bot))