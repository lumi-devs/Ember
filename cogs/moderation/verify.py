import discord
from discord.ext import commands
import datetime
import logging  # Added for better error handling

# Set up logging
logger = logging.getLogger(__name__)

class Verification(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        
        # --- ⚙️ CONFIGURATION ---
        self.STAFF_ROLE_ID = 1460679564916297819  # Madam Moderator Role ID
        self.VERIFIED_ROLE_ID = 1460679593403879547  # The Verified Role ID
        self.LOG_CHANNEL_ID = 1460688886870180025  # Where logs go
        
        # 🔒 LIST OF ALLOWED CHANNEL IDs (Comma separated)
        # The bot will ONLY respond to commands in these channels
        self.ALLOWED_CHANNEL_IDS = [
            1460678841478680698,  # e.g., #staff-commands
            1413074803421614100,
            1424670074354143264   # e.g., #admin-chat
        ]
        # ------------------------

    async def cog_check(self, ctx):
        """
        Global check for this Cog.
        1. Must be in allowed channel.
        2. User must have staff role.
        """
        if not ctx.guild:
            return False

        # 1. Channel Check
        if ctx.channel.id not in self.ALLOWED_CHANNEL_IDS:
            return False  # Silently ignore commands in wrong channels

        # 2. Role Check
        # FIXED: Added explicit return statement for both conditions
        has_role = ctx.guild.get_role(self.STAFF_ROLE_ID) in ctx.author.roles
        
        # Also allow administrators as a fallback (optional but recommended)
        if not has_role:
            has_role = ctx.author.guild_permissions.administrator
        
        return has_role

    # --- HELPER TO SEND EMBEDS ---
    async def send_embed(self, ctx, message, color):
        embed = discord.Embed(description=message, color=color)
        embed.set_footer(text="Madâme System")
        await ctx.send(embed=embed)

    @commands.command(name="verify")
    async def verify_user(self, ctx, member: discord.Member):
        """Verify a member by giving them the verified role"""
        role = ctx.guild.get_role(self.VERIFIED_ROLE_ID)
        
        if not role:
            logger.error(f"Verified role with ID {self.VERIFIED_ROLE_ID} not found in guild {ctx.guild.id}")
            return await self.send_embed(ctx, "❌ **Error:** Verified Role ID is invalid.", discord.Color.red())

        if role in member.roles:
            return await self.send_embed(ctx, f"⚠️ {member.mention} is **already verified**!", discord.Color.gold())

        try:
            await member.add_roles(role, reason=f"Verified by {ctx.author} ({ctx.author.id})")
            await self.send_embed(ctx, f"✅ **Success:** {member.mention} has been verified.", discord.Color.green())
            await self.log_action(ctx, member, "Verified", discord.Color.green())
        except discord.Forbidden:
            await self.send_embed(ctx, "❌ **Error:** I lack permission to manage roles.", discord.Color.red())
            logger.error(f"Bot lacks permission to add role {role.id} to user {member.id}")
        except discord.HTTPException as e:
            await self.send_embed(ctx, f"❌ **Error:** Failed to add role. Please try again.", discord.Color.red())
            logger.error(f"HTTP error when adding role: {e}")

    @commands.command(name="unverify")
    async def unverify_user(self, ctx, member: discord.Member):
        """Remove verified role from a member"""
        role = ctx.guild.get_role(self.VERIFIED_ROLE_ID)
        
        if not role:
            logger.error(f"Verified role with ID {self.VERIFIED_ROLE_ID} not found in guild {ctx.guild.id}")
            return await self.send_embed(ctx, "❌ **Error:** Verified Role ID is invalid.", discord.Color.red())

        if role not in member.roles:
            return await self.send_embed(ctx, f"⚠️ {member.mention} is **not verified**.", discord.Color.gold())

        try:
            await member.remove_roles(role, reason=f"Unverified by {ctx.author} ({ctx.author.id})")
            await self.send_embed(ctx, f"✅ **Success:** Removed verification from {member.mention}.", discord.Color.red())
            await self.log_action(ctx, member, "Unverified", discord.Color.dark_red())
        except discord.Forbidden:
            await self.send_embed(ctx, "❌ **Error:** I lack permission to manage roles.", discord.Color.red())
            logger.error(f"Bot lacks permission to remove role {role.id} from user {member.id}")
        except discord.HTTPException as e:
            await self.send_embed(ctx, f"❌ **Error:** Failed to remove role. Please try again.", discord.Color.red())
            logger.error(f"HTTP error when removing role: {e}")

    async def log_action(self, ctx, target, action, color):
        """Log verification actions to the log channel"""
        log_channel = self.bot.get_channel(self.LOG_CHANNEL_ID)
        if not log_channel:
            logger.warning(f"Log channel with ID {self.LOG_CHANNEL_ID} not found!")
            return 

        embed = discord.Embed(
            title=f"User {action}", 
            color=color, 
            timestamp=datetime.datetime.now(datetime.timezone.utc)  # Use timezone-aware datetime
        )
        embed.add_field(name="User", value=f"{target.mention}\nID: `{target.id}`", inline=True)
        embed.add_field(name="Moderator", value=f"{ctx.author.mention}", inline=True)
        embed.set_thumbnail(url=target.display_avatar.url)
        
        try:
            await log_channel.send(embed=embed)
        except discord.Forbidden:
            logger.error(f"Bot lacks permission to send messages in log channel {log_channel.id}")
        except discord.HTTPException as e:
            logger.error(f"Failed to send log message: {e}")

    # Optional: Add error handling for the commands
    @verify_user.error
    async def verify_error(self, ctx, error):
        """Handle errors for the verify command"""
        if isinstance(error, commands.MissingRequiredArgument):
            await self.send_embed(ctx, "❌ **Usage:** `.verify @member`", discord.Color.red())
        elif isinstance(error, commands.BadArgument):
            await self.send_embed(ctx, "❌ **Error:** Please mention a valid member.", discord.Color.red())
    
    @unverify_user.error
    async def unverify_error(self, ctx, error):
        """Handle errors for the unverify command"""
        if isinstance(error, commands.MissingRequiredArgument):
            await self.send_embed(ctx, "❌ **Usage:** `!unverify @member`", discord.Color.red())
        elif isinstance(error, commands.BadArgument):
            await self.send_embed(ctx, "❌ **Error:** Please mention a valid member.", discord.Color.red())

    # Optional: Add a check command to see verification status
    @commands.command(name="verifylist")
    async def verify_list(self, ctx):
        """List all verified members in the server (Lady Mods only)"""
        # Get verified role
        role = ctx.guild.get_role(self.VERIFIED_ROLE_ID)
        
        if not role:
            await self.send_embed(ctx, "❌ **Error:** Verified Role ID is invalid.", discord.Color.red())
            return
        
        # Get all members with the verified role
        verified_members = [member for member in ctx.guild.members if role in member.roles]
        
        if not verified_members:
            await self.send_embed(ctx, "📭 **No verified members found.**", discord.Color.blue())
            return
        
        # Sort members by display name (case-insensitive)
        verified_members.sort(key=lambda m: m.display_name.lower())
        
        # Create paginated embed
        embed = discord.Embed(
            title=f"✅ Verified Members ({len(verified_members)})",
            color=discord.Color.green(),
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        
        # Format: displayname `username`
        member_list = []
        for member in verified_members:
            # Escape backticks in usernames to prevent formatting issues
            safe_username = str(member.name).replace('`', "'")
            safe_display = str(member.display_name).replace('`', "'")
            member_list.append(f"**{safe_display}** `{safe_username}`")
        
        # Discord embed field value limit is 1024 characters
        # Let's split into chunks if needed
        chunks = []
        current_chunk = ""
        
        for entry in member_list:
            if len(current_chunk) + len(entry) + 2 > 1024:  # +2 for newline
                chunks.append(current_chunk)
                current_chunk = entry + "\n"
            else:
                current_chunk += entry + "\n"
        
        if current_chunk:
            chunks.append(current_chunk)
        
        # Add first chunk to embed
        if chunks:
            embed.add_field(
                name=f"Members (1/{len(chunks)})",
                value=chunks[0],
                inline=False
            )
        
        embed.set_footer(text="Madâme System | Use buttons below to navigate")
        
        # Create view with navigation buttons if multiple chunks
        if len(chunks) > 1:
            view = self.VerifiedListView(ctx, chunks, embed, role)
            message = await ctx.send(embed=embed, view=view)
        else:
            await ctx.send(embed=embed)
    
    # Pagination view for verify list
    class VerifiedListView(discord.ui.View):
        def __init__(self, ctx, chunks, embed, role, timeout=120):
            super().__init__(timeout=timeout)
            self.ctx = ctx
            self.chunks = chunks
            self.embed = embed
            self.role = role
            self.current_page = 0
            self.total_pages = len(chunks)
            
            # Disable buttons initially based on page
            self.update_buttons()
        
        def update_buttons(self):
            # First page
            self.first_button.disabled = self.current_page == 0
            self.prev_button.disabled = self.current_page == 0
            
            # Last page
            self.last_button.disabled = self.current_page == self.total_pages - 1
            self.next_button.disabled = self.current_page == self.total_pages - 1
        
        @discord.ui.button(label="⏮️", style=discord.ButtonStyle.secondary)
        async def first_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user != self.ctx.author:
                await interaction.response.send_message("❌ Only the command user can navigate this list.", ephemeral=True)
                return
            
            self.current_page = 0
            await self.update_embed(interaction)
        
        @discord.ui.button(label="◀️", style=discord.ButtonStyle.secondary)
        async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user != self.ctx.author:
                await interaction.response.send_message("❌ Only the command user can navigate this list.", ephemeral=True)
                return
            
            if self.current_page > 0:
                self.current_page -= 1
                await self.update_embed(interaction)
        
        @discord.ui.button(label="▶️", style=discord.ButtonStyle.secondary)
        async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user != self.ctx.author:
                await interaction.response.send_message("❌ Only the command user can navigate this list.", ephemeral=True)
                return
            
            if self.current_page < self.total_pages - 1:
                self.current_page += 1
                await self.update_embed(interaction)
        
        @discord.ui.button(label="⏭️", style=discord.ButtonStyle.secondary)
        async def last_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user != self.ctx.author:
                await interaction.response.send_message("❌ Only the command user can navigate this list.", ephemeral=True)
                return
            
            self.current_page = self.total_pages - 1
            await self.update_embed(interaction)
        
        @discord.ui.button(label="🔄", style=discord.ButtonStyle.primary)
        async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user != self.ctx.author:
                await interaction.response.send_message("❌ Only the command user can navigate this list.", ephemeral=True)
                return
            
            # Refresh the list
            verified_members = [member for member in self.ctx.guild.members if self.role in member.roles]
            verified_members.sort(key=lambda m: m.display_name.lower())
            
            # Reformat the list
            member_list = []
            for member in verified_members:
                safe_username = str(member.name).replace('`', "'")
                safe_display = str(member.display_name).replace('`', "'")
                member_list.append(f"**{safe_display}** `{safe_username}`")
            
            # Rechunk
            chunks = []
            current_chunk = ""
            
            for entry in member_list:
                if len(current_chunk) + len(entry) + 2 > 1024:
                    chunks.append(current_chunk)
                    current_chunk = entry + "\n"
                else:
                    current_chunk += entry + "\n"
            
            if current_chunk:
                chunks.append(current_chunk)
            
            self.chunks = chunks
            self.total_pages = len(chunks)
            self.current_page = min(self.current_page, self.total_pages - 1)
            
            await self.update_embed(interaction)
            await interaction.response.send_message("✅ List refreshed!", ephemeral=True)
        
        async def update_embed(self, interaction: discord.Interaction):
            # Clear existing fields
            self.embed.clear_fields()
            
            # Update embed title with page info
            self.embed.title = f"✅ Verified Members ({len([m for m in self.ctx.guild.members if self.role in m.roles])})"
            
            # Add current chunk
            self.embed.add_field(
                name=f"Members ({self.current_page + 1}/{self.total_pages})",
                value=self.chunks[self.current_page],
                inline=False
            )
            
            # Update timestamp
            self.embed.timestamp = datetime.datetime.now(datetime.timezone.utc)
            
            # Update buttons
            self.update_buttons()
            
            # Edit message
            await interaction.response.edit_message(embed=self.embed, view=self)
        
        async def on_timeout(self):
            # Disable all buttons on timeout
            for item in self.children:
                item.disabled = True
            
            try:
                await self.message.edit(view=self)
            except:
                pass

async def setup(bot):
    await bot.add_cog(Verification(bot))