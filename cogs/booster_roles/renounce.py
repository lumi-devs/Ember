import discord
from discord import app_commands, Embed, Color, Interaction
from discord.ext import commands

from .database import get_db  # Your SQLite database

class RenounceRoleCog(commands.Cog):
    """Simple cog for users to renounce their gifted role"""
    
    def __init__(self, bot):
        self.bot = bot
    
    @app_commands.command(name="renounce", description="Remove your gifted booster role")
    async def renounce(self, interaction: Interaction):
        """Remove a bestowed role from yourself"""
        await interaction.response.defer(ephemeral=True)
        
        db = await get_db()
        
        # Check if user has any accepted bestowed roles
        user_bestowed = await db.get_bestowed_by_user(interaction.user.id)
        accepted_roles = [b for b in user_bestowed if b.get('accepted')]
        
        if not accepted_roles:
            embed = Embed(
                title="❌ No Gifted Role",
                description="You don't have any gifted roles to renounce.",
                color=Color.red()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        
        # User can only have one gifted role, so take the first one
        role_data = accepted_roles[0]
        role = interaction.guild.get_role(role_data.get('role_id'))
        
        if not role:
            # Role doesn't exist, clean up database
            await db.remove_bestowed(role_data.get('owner_id'), interaction.user.id)
            embed = Embed(
                title="❌ Role Not Found",
                description="Your gifted role no longer exists in the server.",
                color=Color.orange()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        
        # Ask for confirmation
        class ConfirmView(discord.ui.View):
            def __init__(self, role, owner_id):
                super().__init__(timeout=60.0)
                self.role = role
                self.owner_id = owner_id
                self.confirmed = False
            
            @discord.ui.button(label="✅ Yes, Remove It", style=discord.ButtonStyle.danger)
            async def confirm(self, inter: Interaction, button: discord.ui.Button):
                if inter.user.id != interaction.user.id:
                    await inter.response.send_message("❌ This is not for you!", ephemeral=True)
                    return
                
                self.confirmed = True
                self.stop()
                
                # Remove role
                try:
                    await inter.user.remove_roles(self.role, reason="User renounced gifted role")
                    
                    # Update database
                    await db.remove_bestowed(self.owner_id, inter.user.id)
                    
                    embed = Embed(
                        title="✅ Role Renounced",
                        description=f"Successfully removed **{self.role.name}** from your profile.",
                        color=Color.green()
                    )
                    await inter.response.edit_message(embed=embed, view=None)
                    
                except Exception as e:
                    embed = Embed(
                        title="❌ Failed",
                        description=f"Could not remove role: {str(e)}",
                        color=Color.red()
                    )
                    await inter.response.edit_message(embed=embed, view=None)
            
            @discord.ui.button(label="❌ No, Keep It", style=discord.ButtonStyle.secondary)
            async def cancel(self, inter: Interaction, button: discord.ui.Button):
                if inter.user.id != interaction.user.id:
                    await inter.response.send_message("❌ This is not for you!", ephemeral=True)
                    return
                
                self.confirmed = False
                self.stop()
                embed = Embed(
                    title="✅ Cancelled",
                    description="Your role has been kept.",
                    color=Color.blue()
                )
                await inter.response.edit_message(embed=embed, view=None)
            
            async def on_timeout(self):
                if not self.confirmed:
                    for child in self.children:
                        child.disabled = True
                    try:
                        embed = Embed(
                            title="⏰ Timeout",
                            description="Renounce request timed out.",
                            color=Color.greyple()
                        )
                        await self.message.edit(embed=embed, view=self)
                    except:
                        pass
        
        # Create confirmation embed
        embed = Embed(
            title="⚠️ Renounce Role",
            description=f"Are you sure you want to remove **{role.name}** from yourself?",
            color=Color.orange()
        )
        
        embed.add_field(name="Role", value=role.name, inline=True)
        embed.add_field(name="Color", value=str(role.color), inline=True)
        
        owner = interaction.guild.get_member(role_data.get('owner_id'))
        if owner:
            embed.add_field(name="Gifted By", value=owner.mention, inline=True)
        
        embed.add_field(
            name="Effects",
            value="• Role will be removed from you\n• You will be removed from owner's bestowed list",
            inline=False
        )
        
        view = ConfirmView(role, role_data.get('owner_id'))
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        view.message = await interaction.original_response()

async def setup(bot):
    await bot.add_cog(RenounceRoleCog(bot))