import discord
from discord.ext import commands
from datetime import datetime
from .debate_config import FORUM_CHANNEL_ID, MOD_ROLE_ID, COLORS
import re
class TagManager(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    @commands.group(name="tags", invoke_without_command=True)
    @commands.has_role(MOD_ROLE_ID)
    async def tags(self, ctx):
        """Manage debate forum tags"""
        embed = discord.Embed(
            title="🏷️ Forum Tag Management",
            description="Manage the available tags for the debate forum.",
            color=COLORS["info"]
        )
        embed.add_field(name="📋 `tags list`", value="List all available tags in the forum", inline=False)
        embed.add_field(name="➕ `tags add \"Name\" [emoji]`", value="Create a new tag for the forum", inline=False)
        embed.add_field(name="➖ `tags remove <name>`", value="Delete a tag from the forum", inline=False)
        embed.add_field(name="🔍 `tags search <keyword>`", value="Search for active debates by tag", inline=False)
        
        await ctx.send(embed=embed)
    
    @tags.command(name="list")
    @commands.has_role(MOD_ROLE_ID)
    async def list_tags(self, ctx):
        """List all available tags in the debate forum"""
        forum = self.bot.get_channel(FORUM_CHANNEL_ID)
        
        if not forum or not isinstance(forum, discord.ForumChannel):
            await ctx.send("❌ Forum channel not found! Check your FORUM_CHANNEL_ID config.")
            return
        
        if not forum.available_tags:
            await ctx.send("❌ No tags have been created for this forum yet.")
            return
        
        embed = discord.Embed(
            title=f"🏷️ Available Tags in #{forum.name}",
            description=f"Total Tags: {len(forum.available_tags)}/20",
            color=COLORS["info"],
            timestamp=datetime.now()
        )
        
        for tag in forum.available_tags:
            emoji = str(tag.emoji) if tag.emoji else "🔖"
            moderated = "🛡️ (Mod Only)" if tag.moderated else ""
            embed.add_field(
                name=f"{emoji} {tag.name}",
                value=f"ID: `{tag.id}`\n{moderated}",
                inline=True
            )
        
        await ctx.send(embed=embed)
    
    @tags.command(name="add")
    @commands.has_role(MOD_ROLE_ID)
    async def add_tag_to_forum(self, ctx, *, tag_data: str):
        """
        Create a new tag for the forum channel.
        Usage: !tags add Alt+F4 specialist <:fofofo:1442567841768280247>
        """
        forum = self.bot.get_channel(FORUM_CHANNEL_ID)
        
        if not forum or not isinstance(forum, discord.ForumChannel):
            await ctx.send("❌ Forum channel not found!")
            return
            
        if len(forum.available_tags) >= 20:
            await ctx.send("❌ The forum has reached Discord's maximum limit of 20 tags!")
            return

        # 1. Separate the multi-word name from the emoji at the end
        name = tag_data.strip()
        emoji_obj = None
        emoji_display = ""
        
        # Regex to detect a custom Discord emoji at the very end of your message
        custom_emoji_match = re.search(r'(<a?:[a-zA-Z0-9_]+:[0-9]+>)$', tag_data)
        
        if custom_emoji_match:
            emoji_str = custom_emoji_match.group(1)
            # Convert the raw string into a Discord PartialEmoji object
            emoji_obj = discord.PartialEmoji.from_str(emoji_str) 
            # Remove the emoji from the name string
            name = tag_data[:custom_emoji_match.start()].strip()
            emoji_display = f"{emoji_str} "
        else:
            # Fallback in case you use a standard Unicode emoji instead
            parts = tag_data.split()
            if len(parts) > 1:
                last_part = parts[-1]
                # If the last word has no letters/numbers, assume it's a unicode emoji
                if not any(c.isalnum() for c in last_part):
                    emoji_obj = last_part
                    name = " ".join(parts[:-1])
                    emoji_display = f"{last_part} "

        # 2. Enforce Discord's 20-character tag name limit
        if len(name) > 20:
            await ctx.send(f"❌ Tag names cannot exceed 20 characters! '{name}' is {len(name)} characters.")
            return

        # 3. Prevent duplicate tags
        for tag in forum.available_tags:
            if tag.name.lower() == name.lower():
                await ctx.send(f"❌ A tag named '{name}' already exists!")
                return
                
        # 4. Create and push the new tag
        new_tag = discord.ForumTag(name=name, emoji=emoji_obj)
        current_tags = list(forum.available_tags)
        current_tags.append(new_tag)
        
        try:
            await forum.edit(available_tags=current_tags)
            await ctx.send(f"✅ Successfully created new tag: {emoji_display}**{name}**")
        except discord.HTTPException as e:
            await ctx.send(f"❌ Failed to create tag due to API error: {e}")
            
    @tags.command(name="remove")
    @commands.has_role(MOD_ROLE_ID)
    async def remove_tag_from_forum(self, ctx, *, tag_name: str):
        """
        Completely delete a tag from the forum channel.
        Usage: !tags remove Tech Debate
        """
        forum = self.bot.get_channel(FORUM_CHANNEL_ID)
        
        if not forum or not isinstance(forum, discord.ForumChannel):
            await ctx.send("❌ Forum channel not found!")
            return
        
        # Find the tag in the forum's available tags
        tag_to_remove = None
        for tag in forum.available_tags:
            if tag.name.lower() == tag_name.lower():
                tag_to_remove = tag
                break
        
        if not tag_to_remove:
            await ctx.send(f"❌ Tag '{tag_name}' not found in the forum's available tags!")
            return
        
        # Create a new list of tags excluding the one we want to remove
        new_tags = [tag for tag in forum.available_tags if tag.id != tag_to_remove.id]
        
        try:
            # Update the forum channel with the new tag list
            await forum.edit(available_tags=new_tags)
            await ctx.send(f"✅ Successfully deleted the **{tag_to_remove.name}** tag from the forum.")
        except discord.HTTPException as e:
            await ctx.send(f"❌ Failed to remove the tag due to an API error: {e}")
            
    @tags.command(name="search")
    @commands.has_role(MOD_ROLE_ID)
    async def search_by_tag(self, ctx, *, keyword: str):
        """Search for active debates by tag keyword"""
        forum = self.bot.get_channel(FORUM_CHANNEL_ID)
        
        if not forum or not isinstance(forum, discord.ForumChannel):
            await ctx.send("❌ Forum channel not found!")
            return
        
        # Find matching tags
        matching_tags = []
        for tag in forum.available_tags:
            if keyword.lower() in tag.name.lower():
                matching_tags.append(tag)
        
        if not matching_tags:
            await ctx.send(f"❌ No tags found matching '{keyword}'")
            return
        
        # Find threads with these tags
        matching_threads = []
        for thread in forum.threads:
            if thread.archived:
                continue
            
            for thread_tag in thread.applied_tags:
                if thread_tag in matching_tags:
                    matching_threads.append(thread)
                    break
        
        if not matching_threads:
            await ctx.send(f"❌ No active debates found with tags matching '{keyword}'")
            return
        
        # Create embed with results
        embed = discord.Embed(
            title=f"🔍 Debates tagged with '{keyword}'",
            color=COLORS["info"],
            timestamp=datetime.now()
        )
        
        for thread in matching_threads[:10]:  # Limit to 10
            tag_names = ", ".join([f"#{tag.name}" for tag in thread.applied_tags])
            embed.add_field(
                name=thread.name,
                value=f"🔗 {thread.mention}\n🏷️ Tags: {tag_names}",
                inline=False
            )
        
        if len(matching_threads) > 10:
            embed.set_footer(text=f"Showing 10 of {len(matching_threads)} active debates")
        
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(TagManager(bot))