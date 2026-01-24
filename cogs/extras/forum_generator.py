import discord
from discord.ext import commands
import asyncio


class ForumPostCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_creations = {}  # user_id: creation_data
        self.multi_mode_users = {}  # user_id: remaining_posts_count

    @commands.command(name="forum")
    @commands.has_permissions(manage_guild=True)
    async def create_forum_post(self, ctx, count: int = 1):
        """Start creating forum posts interactively

        Parameters
        ----------
        count : int, optional
            Number of forum posts to create (1-10), by default 1
        """
        user_id = ctx.author.id

        # Check if user already has an active creation
        if user_id in self.active_creations:
            await ctx.send("You already have a forum post in creation! Use `.cancel` to cancel it.")
            return

        # Validate count
        if count < 1 or count > 10:
            await ctx.send("You can create 1-10 forum posts at once. Please use `.forum 1` to `.forum 10`.")
            return

        # Initialize creation data
        self.active_creations[user_id] = {
            "step": 0,
            "data": {},
            "channel": ctx.channel,
            "forum_channel": None,
            "message": None,
            "post_count": count,
            "current_post": 1,
            "created_posts": [],  # Store created post links
            "skip_tags": False  # Track if user wants to skip tags
        }

        # Store multi-mode info
        if count > 1:
            self.multi_mode_users[user_id] = {
                "total_posts": count,
                "created_posts": []
            }

        # Start the interactive creation
        await self.start_forum_creation(ctx)

    async def start_forum_creation(self, ctx):
        user_id = ctx.author.id
        data = self.active_creations[user_id]

        # Show multi-post info if applicable
        title = "📝 Forum Post Creator"
        if data["post_count"] > 1:
            title = f"📝 Forum Post Creator ({data['current_post']}/{data['post_count']})"

        embed = discord.Embed(
            title=title,
            description="I'll guide you through creating a forum post!",
            color=discord.Color.blue()
        )

        steps = "1. Choose forum channel\n2. Set post title\n3. Add tags (or skip)\n4. Write content\n5. Add attachments"
        if data["post_count"] > 1:
            steps += f"\n\n**Creating {data['post_count']} posts** - After each post, you'll be asked if you want to continue."

        embed.add_field(
            name="Steps:",
            value=steps,
            inline=False
        )

        embed.add_field(
            name="Commands during creation:",
            value="• Type `next` to continue\n• Type `back` to go back\n• Type `cancel` to stop\n• Type `skip` to skip current step (if applicable)",
            inline=False
        )

        msg = await ctx.send(embed=embed)
        data["message"] = msg
        data["step"] = 1
        await self.ask_forum_channel(ctx)

    async def ask_forum_channel(self, ctx):
        user_id = ctx.author.id
        data = self.active_creations[user_id]

        title = "Step 1: Choose Forum Channel"
        if data["post_count"] > 1:
            title = f"Step 1: Choose Forum Channel ({data['current_post']}/{data['post_count']})"

        embed = discord.Embed(
            title=title,
            description="Please **mention or type the name** of the forum channel where you want to create the post.",
            color=discord.Color.blue()
        )

        forum_channels = self.get_forum_channels(ctx.guild)
        embed.add_field(
            name="Available Forum Channels:",
            value=forum_channels if forum_channels else "No forum channels found",
            inline=False
        )

        if data["post_count"] > 1:
            embed.set_footer(text=f"Post {data['current_post']} of {data['post_count']}")

        await data["message"].edit(embed=embed)

    async def ask_post_title(self, ctx):
        user_id = ctx.author.id
        data = self.active_creations[user_id]

        title = "Step 2: Post Title"
        if data["post_count"] > 1:
            title = f"Step 2: Post Title ({data['current_post']}/{data['post_count']})"

        embed = discord.Embed(
            title=title,
            description="What should be the **title** of your forum post?",
            color=discord.Color.blue()
        )

        embed.add_field(
            name="Tips:",
            value="• Keep it clear and descriptive\n• 5-10 words is ideal\n• Make it searchable",
            inline=False
        )

        if data["post_count"] > 1:
            embed.set_footer(text=f"Post {data['current_post']} of {data['post_count']}")

        await data["message"].edit(embed=embed)

    async def ask_tags(self, ctx):
        user_id = ctx.author.id
        data = self.active_creations[user_id]

        # Get available tags from the forum channel
        forum_channel = data["forum_channel"]
        available_tags = ""

        if forum_channel and hasattr(forum_channel, 'available_tags'):
            tags_list = [tag.name for tag in forum_channel.available_tags[:10]]  # First 10 tags
            if tags_list:
                available_tags = f"**Available tags:** {', '.join(tags_list)}\n"
                if len(forum_channel.available_tags) > 10:
                    available_tags += f"...and {len(forum_channel.available_tags) - 10} more\n"

        title = "Step 3: Tags"
        if data["post_count"] > 1:
            title = f"Step 3: Tags ({data['current_post']}/{data['post_count']})"

        embed = discord.Embed(
            title=title,
            description=(
                f"{available_tags}\n"
                f"Enter tags for your post (separated by commas).\n"
                f"**Example:** `question, help, bug`\n\n"
                f"💡 Type `skip` to **skip tags** for this post\n"
                f"💡 New tags will be created automatically!"
            ),
            color=discord.Color.blue()
        )

        embed.add_field(
            name="Note:",
            value="• Up to 5 tags\n• Type `skip` to skip tags\n• New tags will be created if they don't exist\n• Tag names max 20 characters",
            inline=False
        )

        if data["post_count"] > 1:
            embed.set_footer(text=f"Post {data['current_post']} of {data['post_count']}")

        await data["message"].edit(embed=embed)

    async def ask_content(self, ctx):
        user_id = ctx.author.id
        data = self.active_creations[user_id]

        title = "Step 4: Post Content"
        if data["post_count"] > 1:
            title = f"Step 4: Post Content ({data['current_post']}/{data['post_count']})"

        embed = discord.Embed(
            title=title,
            description="Write the main content of your forum post.",
            color=discord.Color.blue()
        )

        embed.add_field(
            name="Formatting Tips:",
            value="• Use `**bold**` for emphasis\n• Use code blocks for code: ```python\ncode here\n```\n• Keep paragraphs short",
            inline=False
        )

        embed.add_field(
            name="Note:",
            value="You can use Discord markdown and mentions.",
            inline=False
        )

        if data["post_count"] > 1:
            embed.set_footer(text=f"Post {data['current_post']} of {data['post_count']}")

        await data["message"].edit(embed=embed)

    async def ask_attachments(self, ctx):
        user_id = ctx.author.id
        data = self.active_creations[user_id]

        title = "Step 5: Attachments (Optional)"
        if data["post_count"] > 1:
            title = f"Step 5: Attachments (Optional) ({data['current_post']}/{data['post_count']})"

        embed = discord.Embed(
            title=title,
            description="Do you want to attach any files to your post?",
            color=discord.Color.blue()
        )

        embed.add_field(
            name="Options:",
            value="• Upload images/files now (they'll be attached)\n• Type `skip` to continue without attachments\n• Type `done` when finished uploading",
            inline=False
        )

        embed.add_field(
            name="Supported files:",
            value="Images, text files, code files, PDFs (up to 25MB total)",
            inline=False
        )

        if data["post_count"] > 1:
            embed.set_footer(text=f"Post {data['current_post']} of {data['post_count']}")

        await data["message"].edit(embed=embed)

    async def show_preview(self, ctx):
        user_id = ctx.author.id
        data = self.active_creations[user_id]

        title = "📋 Post Preview"
        if data["post_count"] > 1:
            title = f"📋 Post Preview ({data['current_post']}/{data['post_count']})"

        embed = discord.Embed(
            title=title,
            color=discord.Color.gold()
        )

        # Build preview
        preview_text = f"**Channel:** {data['forum_channel'].mention if data['forum_channel'] else 'Not set'}\n"
        preview_text += f"**Title:** {data['data'].get('title', 'Not set')}\n"

        # Handle tags preview
        tags = data['data'].get('tags', [])
        if tags:
            preview_text += f"**Tags:** {', '.join(tags)}\n"
        elif data.get('skip_tags', False):
            preview_text += f"**Tags:** Skipped\n"
        else:
            preview_text += f"**Tags:** Not set\n"

        preview_text += f"**Content Preview:** {data['data'].get('content', 'Not set')[:100]}..." if data['data'].get(
            'content') else "**Content:** Not set"

        embed.description = preview_text

        embed.add_field(
            name="Options:",
            value="• Type `post` to create the forum post\n• Type `edit` to modify something\n• Type `cancel` to discard",
            inline=False
        )

        if data["post_count"] > 1:
            embed.set_footer(text=f"Post {data['current_post']} of {data['post_count']}")

        await data["message"].edit(embed=embed)

    async def ask_create_more(self, ctx):
        """Ask if user wants to create another post"""
        user_id = ctx.author.id
        data = self.active_creations[user_id]

        if data["current_post"] < data["post_count"]:
            embed = discord.Embed(
                title="🎉 Post Created!",
                description=f"✅ Post #{data['current_post']} has been created!\n\n"
                            f"Do you want to create the next post ({data['current_post'] + 1}/{data['post_count']})?",
                color=discord.Color.green()
            )

            embed.add_field(
                name="Options:",
                value="• Type `yes` to create another post\n• Type `no` to finish\n• Type `same` to use same settings as previous post",
                inline=False
            )

            if data["current_post"] > 1:
                embed.add_field(
                    name="Quick Settings:",
                    value="Type `same` to use:\n- Same forum channel\n- Same tags (if any)\n- Same attachments (if any)",
                    inline=False
                )

            await data["message"].edit(embed=embed)

    def get_forum_channels(self, guild):
        """Get list of forum channels in the server"""
        forum_channels = []
        for channel in guild.channels:
            if isinstance(channel, discord.ForumChannel):
                forum_channels.append(f"• #{channel.name}")

        if forum_channels:
            return "\n".join(forum_channels[:5])  # Show first 5
        return ""

    @commands.Cog.listener()
    async def on_message(self, message):
        """Listen for user responses during forum creation"""
        if message.author.bot:
            return

        user_id = message.author.id

        if user_id not in self.active_creations:
            return

        # Check if message is in the same channel as the creation
        data = self.active_creations[user_id]
        if message.channel != data["channel"]:
            return

        content = message.content.lower()

        # Handle control commands
        if content == "cancel":
            await self.cancel_creation(message)
            return
        elif content == "next":
            await self.next_step(message)
            return
        elif content == "back":
            await self.previous_step(message)
            return
        elif content in ["yes", "no", "same"] and data.get("step") == 7:  # Create more step
            await self.handle_create_more_response(message, content)
            return

        # Handle step-specific inputs
        await self.process_step_input(message)

        # Delete user's message to keep chat clean
        try:
            await message.delete()
        except:
            pass

    async def process_step_input(self, message):
        user_id = message.author.id
        data = self.active_creations[user_id]

        if data["step"] == 1:  # Forum channel selection
            await self.handle_forum_channel_input(message, message.content)
        elif data["step"] == 2:  # Post title
            data["data"]["title"] = message.content
            await self.next_step(message)
        elif data["step"] == 3:  # Tags
            content = message.content.lower()
            if content == "skip":
                data["skip_tags"] = True
                data["data"]["tags"] = []
                await message.channel.send("✅ Skipped tags for this post.", delete_after=3)
                await self.next_step(message)
            else:
                tags = [tag.strip() for tag in message.content.split(",") if tag.strip()]
                data["data"]["tags"] = tags[:5]  # Limit to 5 tags
                data["skip_tags"] = False
                await self.next_step(message)
        elif data["step"] == 4:  # Content
            data["data"]["content"] = message.content
            await self.next_step(message)
        elif data["step"] == 5:  # Attachments
            if message.content.lower() == "skip":
                data["data"]["attachments"] = []
                await self.next_step(message)
            elif message.content.lower() == "done":
                # Collect attachments from recent messages
                attachments = []
                async for msg in message.channel.history(limit=10):
                    if msg.author.id == user_id and msg.attachments:
                        attachments.extend(msg.attachments)

                data["data"]["attachments"] = attachments[:10]  # Limit to 10 attachments
                await self.next_step(message)
            elif message.attachments:
                # User uploaded files
                await message.channel.send(
                    f"✅ Added {len(message.attachments)} file(s). Type `done` when finished or `skip` to continue without attachments.",
                    delete_after=5)
            else:
                await message.channel.send("Please upload files or type `skip`/`done`.", delete_after=5)
        elif data["step"] == 6:  # Preview actions
            if message.content.lower() == "post":
                await self.create_forum_post_final(message)
            elif message.content.lower() == "edit":
                await self.go_to_edit_step(message)

    async def handle_forum_channel_input(self, message, input_text):
        user_id = message.author.id
        data = self.active_creations[user_id]

        # Try to find forum channel by mention, name, or ID
        forum_channel = None

        # Check if it's a channel mention
        if message.channel_mentions:
            channel = message.channel_mentions[0]
            if isinstance(channel, discord.ForumChannel):
                forum_channel = channel

        # If not a mention, search by name
        if not forum_channel:
            for channel in message.guild.channels:
                if isinstance(channel, discord.ForumChannel) and channel.name.lower() == input_text.lower().replace("#",
                                                                                                                    ""):
                    forum_channel = channel
                    break

        if forum_channel:
            data["forum_channel"] = forum_channel
            await message.channel.send(f"✅ Selected forum: {forum_channel.mention}", delete_after=3)
            await self.next_step(message)
        else:
            await message.channel.send("❌ Couldn't find that forum channel. Please try again or mention the channel.",
                                       delete_after=5)

    async def handle_create_more_response(self, message, response):
        """Handle yes/no/same response for creating more posts"""
        user_id = message.author.id
        data = self.active_creations[user_id]

        if response == "no":
            # Finish creation
            await self.finish_multi_creation(message)
            return
        elif response == "same":
            # Use same settings for next post
            data["current_post"] += 1
            data["step"] = 2  # Go to title step (keep same channel, tags, etc.)

            # Clear only title and content, keep other settings
            previous_data = data["data"].copy()
            data["data"] = {
                "title": "",
                "content": "",
                "tags": previous_data.get("tags", []),  # Keep same tags
                "attachments": previous_data.get("attachments", [])  # Keep same attachments
            }

            # Keep forum channel and skip_tags setting
            data["skip_tags"] = data.get("skip_tags", False)

            await self.ask_post_title(message)
        elif response == "yes":
            # Start fresh for next post
            data["current_post"] += 1
            data["step"] = 1  # Start from forum channel selection
            data["data"] = {}  # Clear all data
            data["forum_channel"] = None  # Clear forum channel
            data["skip_tags"] = False  # Reset skip tags

            await self.ask_forum_channel(message)

    async def next_step(self, message):
        user_id = message.author.id
        data = self.active_creations[user_id]

        data["step"] += 1

        if data["step"] == 2:
            await self.ask_post_title(message)
        elif data["step"] == 3:
            await self.ask_tags(message)
        elif data["step"] == 4:
            await self.ask_content(message)
        elif data["step"] == 5:
            await self.ask_attachments(message)
        elif data["step"] == 6:
            await self.show_preview(message)
        elif data["step"] == 7:
            # This step is handled by create_forum_post_final
            pass
        else:
            # Creation complete or invalid step
            if data["step"] > 7:
                await self.cleanup_creation(user_id)

    async def previous_step(self, message):
        user_id = message.author.id
        data = self.active_creations[user_id]

        if data["step"] > 1:
            data["step"] -= 1

            if data["step"] == 1:
                await self.ask_forum_channel(message)
            elif data["step"] == 2:
                await self.ask_post_title(message)
            elif data["step"] == 3:
                await self.ask_tags(message)
            elif data["step"] == 4:
                await self.ask_content(message)
            elif data["step"] == 5:
                await self.ask_attachments(message)

    async def go_to_edit_step(self, message):
        user_id = message.author.id
        data = self.active_creations[user_id]

        title = "Edit Which Part?"
        if data["post_count"] > 1:
            title = f"Edit Which Part? ({data['current_post']}/{data['post_count']})"

        embed = discord.Embed(
            title=title,
            description="Type the number of what you want to edit:\n1. Forum Channel\n2. Title\n3. Tags\n4. Content\n5. Attachments",
            color=discord.Color.orange()
        )

        msg = await message.channel.send(embed=embed, delete_after=30)

        def check(m):
            return m.author.id == user_id and m.channel == message.channel and m.content.isdigit() and 1 <= int(
                m.content) <= 5

        try:
            response = await self.bot.wait_for('message', timeout=30.0, check=check)
            choice = int(response.content)

            # Set step to the chosen edit point
            data["step"] = choice

            # Go to that step
            if choice == 1:
                await self.ask_forum_channel(message)
            elif choice == 2:
                await self.ask_post_title(message)
            elif choice == 3:
                await self.ask_tags(message)
            elif choice == 4:
                await self.ask_content(message)
            elif choice == 5:
                await self.ask_attachments(message)

            await response.delete()
            await msg.delete()

        except asyncio.TimeoutError:
            await msg.edit(content="Edit timed out. Returning to preview...", embed=None)
            await self.show_preview(message)

    async def create_forum_post_final(self, message):
        user_id = message.author.id
        data = self.active_creations[user_id]

        try:
            # Prepare tags - only if not skipped
            tags = []
            if not data.get("skip_tags", False) and data["data"].get("tags"):
                forum_channel = data["forum_channel"]
                if hasattr(forum_channel, 'available_tags'):
                    for tag_name in data["data"]["tags"][:5]:  # Limit to 5 tags
                        tag_found = False

                        # First, try to find existing tag
                        for available_tag in forum_channel.available_tags:
                            if tag_name.lower() == available_tag.name.lower():
                                tags.append(available_tag)
                                tag_found = True
                                break

                        # If tag doesn't exist, CREATE IT
                        if not tag_found:
                            try:
                                # Create new tag
                                new_tag = await forum_channel.create_tag(
                                    name=tag_name[:20],  # Discord limits tag names to 20 chars
                                    reason=f"Created via forum post by {message.author.name}"
                                )
                                tags.append(new_tag)
                                print(f"Created new tag: {tag_name}")
                            except Exception as e:
                                print(f"Failed to create tag '{tag_name}': {e}")
                                await message.channel.send(
                                    f"⚠️ Couldn't create tag '{tag_name}': {str(e)[:100]}",
                                    delete_after=5
                                )
                else:
                    # Forum channel doesn't support tags
                    await message.channel.send(
                        "ℹ️ This forum channel doesn't support tags.",
                        delete_after=5
                    )

            # Create the forum post
            thread = await data["forum_channel"].create_thread(
                name=data["data"].get("title", "Untitled Post")[:100],  # Discord limit
                content=data["data"].get("content", ""),
                applied_tags=tags[:5] if tags else None  # Only apply tags if there are any
            )

            # Add attachments if any
            if data["data"].get("attachments"):
                attachment_count = 0
                for attachment in data["data"]["attachments"][:10]:  # Limit to 10 attachments
                    try:
                        await thread.thread.send(
                            f"📎 {attachment.filename}",
                            file=await attachment.to_file()
                        )
                        attachment_count += 1
                        await asyncio.sleep(1)  # Avoid rate limits
                    except Exception as e:
                        print(f"Failed to upload attachment: {e}")

                if attachment_count > 0:
                    await thread.thread.send(f"📁 **{attachment_count} attachment(s) uploaded**")

            # Store created post info for multi-mode
            post_info = {
                "title": data["data"].get("title", "Untitled"),
                "link": thread.thread.jump_url,
                "channel": data["forum_channel"].name
            }

            # Store in multi-mode if applicable
            if user_id in self.multi_mode_users:
                self.multi_mode_users[user_id]["created_posts"].append(post_info)

            # Success message
            if data["post_count"] == 1:
                # Single post - show success and cleanup
                embed = discord.Embed(
                    title="✅ Forum Post Created!",
                    description=f"Your post has been created in {data['forum_channel'].mention}",
                    color=discord.Color.green()
                )
                embed.add_field(name="Post Title", value=data["data"].get("title", "Untitled"), inline=False)

                if tags:
                    embed.add_field(name="Tags", value=", ".join([tag.name for tag in tags]), inline=False)

                embed.add_field(name="Post Link", value=f"[Go to post]({thread.thread.jump_url})", inline=False)

                await data["message"].edit(embed=embed)

                # Clean up after delay
                await asyncio.sleep(5)
                await self.cleanup_creation(user_id)
            else:
                # Multi-post mode - ask if user wants to create more
                data["step"] = 7  # Set to "create more" step
                await self.ask_create_more(message)

        except Exception as e:
            error_msg = f"❌ Failed to create forum post: {str(e)}"
            # Give more specific error messages
            if "Maximum number of tags" in str(e):
                error_msg = "❌ Too many tags! Discord allows maximum 5 tags per post."
            elif "Maximum length" in str(e):
                error_msg = "❌ Title too long! Maximum 100 characters."

            await message.channel.send(error_msg)
            await self.cleanup_creation(user_id)

    async def finish_multi_creation(self, message):
        """Finish multi-post creation and show summary"""
        user_id = message.author.id
        data = self.active_creations[user_id]

        if user_id in self.multi_mode_users:
            multi_data = self.multi_mode_users[user_id]

            if multi_data["created_posts"]:
                embed = discord.Embed(
                    title="🎉 Multiple Forum Posts Created!",
                    description=f"Successfully created {len(multi_data['created_posts'])} posts:",
                    color=discord.Color.green()
                )

                # List all created posts
                for i, post in enumerate(multi_data["created_posts"], 1):
                    embed.add_field(
                        name=f"Post #{i}: {post['title']}",
                        value=f"[Go to post]({post['link']}) in #{post['channel']}",
                        inline=False
                    )

                # Add summary
                embed.set_footer(text=f"Created {len(multi_data['created_posts'])}/{multi_data['total_posts']} posts")

                await data["message"].edit(embed=embed)

            # Clean up multi-mode data
            del self.multi_mode_users[user_id]

        # Clean up creation
        await self.cleanup_creation(user_id)

    async def cancel_creation(self, message):
        user_id = message.author.id

        # Clean up multi-mode if exists
        if user_id in self.multi_mode_users:
            del self.multi_mode_users[user_id]

        if user_id in self.active_creations:
            await message.channel.send("❌ Forum post creation cancelled.", delete_after=5)
            await self.cleanup_creation(user_id)

    async def cleanup_creation(self, user_id):
        if user_id in self.active_creations:
            # Delete the creation message after a delay
            data = self.active_creations[user_id]
            if data.get("message"):
                try:
                    # Wait a bit before deleting to let user see final message
                    await asyncio.sleep(10)
                    await data["message"].delete()
                except:
                    pass

            # Remove from active creations
            del self.active_creations[user_id]

    @commands.command(name="cancel")
    async def cancel_command(self, ctx):
        """Cancel any active forum post creation"""
        if ctx.author.id in self.active_creations:
            await ctx.send("✅ Cancelled forum post creation.", delete_after=5)
            await self.cancel_creation(ctx.message)
        else:
            await ctx.send("You don't have any active forum post creation.", delete_after=5)

    @commands.command(name="forumbulk")
    @commands.has_permissions(manage_guild=True)
    async def forum_bulk_help(self, ctx):
        """Show help for bulk forum post creation"""
        embed = discord.Embed(
            title="📝 Bulk Forum Post Creation",
            description="Create multiple forum posts quickly!",
            color=discord.Color.blue()
        )

        embed.add_field(
            name="Usage:",
            value="`.forum 5` - Create 5 forum posts\n`.forum 10` - Create 10 forum posts",
            inline=False
        )

        embed.add_field(
            name="Features:",
            value="• Create 1-10 posts at once\n• Skip tags for individual posts\n• Use same settings for multiple posts\n• Cancel anytime with `.cancel`",
            inline=False
        )

        embed.add_field(
            name="During Creation:",
            value="• Type `skip` to skip tags\n• Type `same` to use previous post's settings\n• Type `yes`/`no` to create more posts",
            inline=False
        )

        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(ForumPostCog(bot))