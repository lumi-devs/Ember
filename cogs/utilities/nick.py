import discord
from discord.ext import commands
import asyncio

class NicknameChanger(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Replace these with your actual role IDs
        self.MODERATOR_ROLE_ID = 1406324000736542911  
        self.HELPER_ROLE_ID = 1410302930485121106     
        self.CHAT_MANAGER_ROLE_ID = 1408709182856630332  
        
        # Define dangerous permissions to check for
        self.DANGEROUS_PERMISSIONS = [
            'administrator',
            'manage_guild',
            'manage_roles',
            'manage_channels',
            'manage_webhooks',
        ]

    def has_dangerous_permissions(self, member):
        """Check if a member has any dangerous permissions."""
        permissions = member.guild_permissions
        for perm in self.DANGEROUS_PERMISSIONS:
            if getattr(permissions, perm):
                return True
        return False

    def has_admin_permissions(self, member):
        """Check if a member has administrator permissions."""
        return member.guild_permissions.administrator

    def get_hierarchy_level(self, member):
        """Get the hierarchy level of a member based on the 3 special roles."""
        role_ids = [role.id for role in member.roles]
        
        if self.MODERATOR_ROLE_ID in role_ids:
            return 3  # Highest
        elif self.HELPER_ROLE_ID in role_ids:
            return 2  # Middle
        elif self.CHAT_MANAGER_ROLE_ID in role_ids:
            return 1  # Lowest
        else:
            return 0  # No special role

    def can_change_nickname(self, executor, target):
        """Check if executor can change target's nickname based on custom rules."""
        
        # Prevent changing bot's nickname
        if target.bot:
            return False, "❌ You can't change a **bot's nickname** with this command."
        
        # Prevent changing own nickname (except through normal means)
        if target == executor:
            return False, "⚠️ You can't change your **own nickname** with this command."
        
        # Get hierarchy levels
        executor_level = self.get_hierarchy_level(executor)
        target_level = self.get_hierarchy_level(target)
        
        # If executor has administrator permission, allow (bypass all other checks)
        if self.has_admin_permissions(executor):
            # Still check if target has admin perms
            if self.has_admin_permissions(target):
                return False, "❌ You can't change the nickname of another **administrator**."
            return True, ""
        
        # If executor doesn't have any of the 3 special roles AND not admin, deny
        if executor_level == 0:
            return False, "🚫 You don't have permission to use this command."
        
        # SPECIAL RULE: Check hierarchy between the 3 roles
        if target_level > 0:  # Target has one of the 3 special roles
            if target_level >= executor_level:
                # Can't change someone equal or higher in our custom hierarchy
                if target_level == 3 and executor_level < 3:
                    return False, "❌ You can't change a **Moderator's** nickname."
                elif target_level == 2 and executor_level < 2:
                    return False, "❌ You can't change a **Helper's** nickname."
                elif target_level == 1 and executor_level < 1:
                    return False, "❌ You can't change a **Chat Manager's** nickname."
        
        # SECONDARY RULE: Check if target has dangerous permissions
        if self.has_dangerous_permissions(target):
            return False, "❌ You can't change the nickname of someone with **admin/manage permissions**."
        
        return True, ""

    async def send_temp_message(self, ctx, embed, delete_after=3):
        """Send a message that will be deleted after specified seconds."""
        message = await ctx.reply(embed=embed, mention_author=False)
        await asyncio.sleep(delete_after)
        try:
            await message.delete()
        except (discord.NotFound, discord.Forbidden):
            pass  # Message already deleted or bot lacks permissions

    @commands.command(name='nick')
    async def change_nickname(self, ctx, member: discord.Member, *, new_nick: str = None):
        """Change or reset a member's nickname based on custom hierarchy rules."""
        
        # Check if executor can change target's nickname
        can_change, reason = self.can_change_nickname(ctx.author, member)
        if not can_change:
            embed = discord.Embed(description=reason, color=discord.Color.red())
            return await self.send_temp_message(ctx, embed)
        
        # Check bot's ability to change the nickname
        if member.top_role.position >= ctx.guild.me.top_role.position:
            embed = discord.Embed(
                description="❌ I can't change the nickname of someone **equal to or higher** than my role.",
                color=discord.Color.red()
            )
            return await self.send_temp_message(ctx, embed)
        
        try:
            old_nick = member.nick or member.name
            if new_nick:
                await member.edit(nick=new_nick)
                embed = discord.Embed(
                    title="✅ Nickname Changed",
                    description=f"**{old_nick}**'s nickname changed to **{new_nick}**",
                    color=discord.Color.green()
                )
            else:
                await member.edit(nick=None)
                embed = discord.Embed(
                    title="✅ Nickname Reset",
                    description=f"**{old_nick}**'s nickname has been **reset** to their username.",
                    color=discord.Color.green()
                )
            
            # Add who changed the nickname
            executor_level = self.get_hierarchy_level(ctx.author)
            role_names = {3: "Moderator", 2: "Helper", 1: "Chat Manager", 0: "Member"}
            
            # Check if executor has admin permissions
            executor_title = role_names[executor_level]
            if self.has_admin_permissions(ctx.author):
                executor_title = "Administrator"
            
            embed.set_footer(
                text=f"Changed by {ctx.author} ({executor_title})", 
                icon_url=ctx.author.avatar.url if ctx.author.avatar else None
            )
            await self.send_temp_message(ctx, embed)
            
        except discord.Forbidden:
            embed = discord.Embed(
                description="❌ I don't have permission to change that user's nickname.",
                color=discord.Color.red()
            )
            await self.send_temp_message(ctx, embed)
            
        except discord.HTTPException:
            embed = discord.Embed(
                description="⚠️ Something went wrong while trying to change the nickname.",
                color=discord.Color.red()
            )
            await self.send_temp_message(ctx, embed)

    @change_nickname.error
    async def nick_error(self, ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            embed = discord.Embed(
                description="❗ Usage:\n`.nick @user` → Reset nickname\n`.nick @user new_nick` → Change nickname",
                color=discord.Color.orange()
            )
            await self.send_temp_message(ctx, embed)
            
        elif isinstance(error, commands.BadArgument):
            embed = discord.Embed(
                description="❗ Couldn't find that user. Make sure you **mention them properly**.",
                color=discord.Color.orange()
            )
            await self.send_temp_message(ctx, embed)
            
        else:
            embed = discord.Embed(
                description="⚠️ An unexpected error occurred.",
                color=discord.Color.red()
            )
            await self.send_temp_message(ctx, embed)

async def setup(bot):
    await bot.add_cog(NicknameChanger(bot))