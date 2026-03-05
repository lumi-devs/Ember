import discord
from discord.ui import Modal, TextInput, View, Button, Select
from .debate_config import COLORS, MOD_ROLE_ID, FORUM_CHANNEL_ID
from datetime import datetime


class DebateSubmissionModal(Modal, title="Propose New Debate"):
    topic = TextInput(
        label="Debate Topic",
        placeholder="e.g., Is AI a threat to humanity?",
        max_length=100,
        required=True,
    )

    opening_statement = TextInput(
        label="Opening Statement",
        placeholder="Present your initial argument...",
        style=discord.TextStyle.paragraph,
        max_length=1000,
        required=True,
    )

    why_topic = TextInput(
        label="Why this topic? (Optional)",
        placeholder="Why is this worth debating?",
        style=discord.TextStyle.paragraph,
        max_length=500,
        required=False,
    )

    def __init__(self, bot):
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction):
        # Save to database
        await self.bot.db.save_submission(
            str(interaction.user.id),
            self.topic.value,
            self.opening_statement.value,
            self.why_topic.value,
        )

        # Send to mod channel
        mod_channel = self.bot.get_channel(self.bot.mod_channel_id)
        if mod_channel:
            embed = discord.Embed(
                title="📝 New Debate Proposal",
                color=COLORS["warning"],
                timestamp=datetime.now(),
            )
            embed.add_field(name="Topic", value=self.topic.value, inline=False)
            embed.add_field(
                name="Opening Statement",
                value=self.opening_statement.value[:500],
                inline=False,
            )
            if self.why_topic.value:
                embed.add_field(
                    name="Why this topic?", value=self.why_topic.value, inline=False
                )
            embed.set_author(
                name=interaction.user.display_name,
                icon_url=interaction.user.display_avatar.url,
            )
            embed.set_footer(text=f"User ID: {interaction.user.id}")

            # Create persistent view with custom IDs
            view = ModApprovalView(self.bot, interaction.user.id)

            # Send the message with the view - Discord.py automatically registers it
            mod_message = await mod_channel.send(embed=embed, view=view)

            # Update submission with message ID
            await self.bot.db.save_submission(
                str(interaction.user.id),
                self.topic.value,
                self.opening_statement.value,
                self.why_topic.value,
                str(mod_message.id),
            )

            # NO NEED to manually store in persistent_views - Discord handles this!

        await interaction.response.send_message(
            "✅ Your debate proposal has been sent to moderators!", ephemeral=True
        )


class ModApprovalView(View):
    def __init__(self, bot, proposer_id):
        # Set custom_id for persistence
        super().__init__(timeout=None)
        self.bot = bot
        self.proposer_id = proposer_id

    @discord.ui.button(
        label="Approve",
        style=discord.ButtonStyle.success,
        emoji="✅",
        custom_id="debate_approve",
    )
    async def approve_button(self, interaction: discord.Interaction, button: Button):
        if not any(role.id == MOD_ROLE_ID for role in interaction.user.roles):
            await interaction.response.send_message(
                "❌ You don't have permission to approve debates.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        forum = self.bot.get_channel(FORUM_CHANNEL_ID)
        if not forum or not isinstance(forum, discord.ForumChannel):
            await interaction.followup.send(
                "❌ Forum channel not found!", ephemeral=True
            )
            return

        submission = await self.bot.db.get_submission(str(self.proposer_id))
        if not submission:
            await interaction.followup.send(
                "❌ Submission data expired! Please submit again.", ephemeral=True
            )
            return

        # Create tag selection
        options = []
        for tag in forum.available_tags[:25]:
            options.append(
                discord.SelectOption(
                    label=tag.name,
                    value=str(tag.id),
                    emoji=tag.emoji if tag.emoji else None,
                    description=f"Select to add {tag.name} tag",
                )
            )

        select = Select(
            placeholder="Choose tags for this debate (max 5)...",
            options=options,
            min_values=1,
            max_values=min(5, len(options)),
            custom_id=f"tag_select_{self.proposer_id}",
        )

        async def select_callback(select_interaction):
            selected_tag_ids = [int(tag_id) for tag_id in select.values]
            selected_tags = []

            for tag_id in selected_tag_ids:
                tag = discord.utils.get(forum.available_tags, id=tag_id)
                if tag:
                    selected_tags.append(tag)

            await select_interaction.response.defer(ephemeral=True)

            try:
                thread, message = await forum.create_thread(
                    name=f"⭐ Debate: {submission['topic'][:90]}",
                    content=f"Welcome to the debate! <@{submission['user_id']}>",
                    applied_tags=selected_tags,
                )

                # Post embeds
                await self.post_debate_embeds(
                    thread, submission, select_interaction.user
                )

                tag_names = ", ".join([tag.name for tag in selected_tags])

                await self.bot.db.create_debate(
                    str(thread.id),
                    submission["topic"],
                    str(self.proposer_id),
                    str(select_interaction.user.id),
                    tag_names,
                )

                await self.bot.db.add_weekly_score(
                    str(self.proposer_id), 5, debate_created=True
                )

                await self.bot.db.delete_submission(str(self.proposer_id))

                # Check if debate is highly active and apply hot tag
                await self.check_and_apply_hot_tag(thread)

                tag_display = ", ".join([f"#{tag.name}" for tag in selected_tags])

                await select_interaction.followup.send(
                    f"✅ Debate created in {thread.mention} with tags: {tag_display}",
                    ephemeral=True,
                )

                # Disable buttons
                self.approve_button.disabled = True
                self.reject_button.disabled = True
                await interaction.message.edit(view=self)

                if hasattr(self.bot, "persistent_views") and isinstance(
                    self.bot.persistent_views, dict
                ):
                    if interaction.message.id in self.bot.persistent_views:
                        del self.bot.persistent_views[interaction.message.id]

            except Exception as e:
                await select_interaction.followup.send(
                    f"❌ Error creating debate: {str(e)}", ephemeral=True
                )

        select.callback = select_callback
        view = View(timeout=None)
        view.add_item(select)
        await interaction.followup.send(
            "Select tags for this debate (you can select up to 5):",
            view=view,
            ephemeral=True,
        )

    async def post_debate_embeds(self, thread, submission, moderator):
        """Post the initial embeds in the new debate thread"""
        rules_embed = discord.Embed(
            title="📜 Debate Rules",
            description="""
            • **No personal attacks** — Argue the idea, not the person
            • **Stay on topic** — Keep discussions relevant to the debate
            • **Cite sources** — Back up claims with evidence
            • **No spam** — Quality over quantity
            • **Respect all participants** — Civil discourse only
            • **Follow server guidelines** — Standard rules apply
            """,
            color=COLORS["rules"],
        )
        rules_embed.set_footer(text="Violations may result in removal from debates")
        await thread.send(embed=rules_embed)

        info_embed = discord.Embed(
            title="📋 Debate Information",
            color=COLORS["info"],
            timestamp=datetime.now(),
        )

        info_embed.description = f"## {submission['topic']}"
        info_embed.add_field(
            name="👤 Proposed by", value=f"<@{submission['user_id']}>", inline=True
        )
        info_embed.add_field(name="🛡 Approved by", value=moderator.mention, inline=True)
        info_embed.add_field(
            name="⏰ Started",
            value=f"<t:{int(datetime.now().timestamp())}:R>",
            inline=True,
        )
        info_embed.set_footer(
            text="📢 For any issues, please contact the approving moderator"
        )

        await thread.send(embed=info_embed)

        opening_embed = discord.Embed(
            title="🎯 Opening Statement",
            description=submission["opening"],
            color=COLORS["success"],
        )
        user = self.bot.get_user(int(submission["user_id"]))
        if user:
            opening_embed.set_author(
                name=user.display_name, icon_url=user.display_avatar.url
            )
        await thread.send(embed=opening_embed)

        if submission["why"]:
            why_embed = discord.Embed(
                title="💭 Why this topic?",
                description=submission["why"],
                color=COLORS["info"],
            )
            await thread.send(embed=why_embed)

    async def check_and_apply_hot_tag(self, thread):
        """Check if debate should have hot tag and apply it"""

        # This will be checked by the automation loop
        # Just initialize the tracking
        await self.bot.db.update_debate_activity(str(thread.id))

    @discord.ui.button(
        label="Reject",
        style=discord.ButtonStyle.danger,
        emoji="❌",
        custom_id="debate_reject",
    )
    async def reject_button(self, interaction: discord.Interaction, button: Button):
        if not any(role.id == MOD_ROLE_ID for role in interaction.user.roles):
            await interaction.response.send_message(
                "❌ You don't have permission.", ephemeral=True
            )
            return

        modal = RejectionReasonModal(self.bot, self.proposer_id)
        await interaction.response.send_modal(modal)

        self.approve_button.disabled = True
        self.reject_button.disabled = True
        await interaction.message.edit(view=self)

        # Remove from persistent views
        if (
            hasattr(self.bot, "persistent_views")
            and str(interaction.message.id) in self.bot.persistent_views
        ):
            del self.bot.persistent_views[str(interaction.message.id)]


class RejectionReasonModal(Modal, title="Reject Debate Proposal"):
    reason = TextInput(
        label="Reason for rejection",
        placeholder="Explain why this debate was rejected...",
        style=discord.TextStyle.paragraph,
        max_length=500,
        required=True,
    )

    def __init__(self, bot, proposer_id):
        super().__init__()
        self.bot = bot
        self.proposer_id = proposer_id

    async def on_submit(self, interaction: discord.Interaction):
        user = self.bot.get_user(int(self.proposer_id))
        if user:
            embed = discord.Embed(
                title="❌ Debate Proposal Rejected",
                description="Your debate proposal was rejected.",
                color=COLORS["warning"],
            )
            embed.add_field(name="Reason", value=self.reason.value)
            try:
                await user.send(embed=embed)
            except discord.HTTPException:
                pass  # Can't DM user

        await self.bot.db.delete_submission(self.proposer_id)
        await interaction.response.send_message(
            f"✅ Rejection sent to <@{self.proposer_id}>", ephemeral=True
        )


class CreateDebateView(View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Create New Debate",
        style=discord.ButtonStyle.primary,
        emoji="🆕",
        custom_id="create_debate_persistent",
    )
    async def create_button(self, interaction: discord.Interaction, button: Button):
        # Check if interaction is already responded to
        if not interaction.response.is_done():
            try:
                modal = DebateSubmissionModal(self.bot)
                await interaction.response.send_modal(modal)
            except Exception as e:
                print(f"Error in create_button: {e}")
                try:
                    await interaction.response.send_message(
                        "❌ An error occurred. Please try again.", ephemeral=True
                    )
                except Exception:
                    pass
        else:
            # If response was already done, try to send a followup
            try:
                await interaction.followup.send(
                    "⏳ Processing your request...", ephemeral=True
                )
                # You might want to create the modal differently here
                # But modals can only be sent via response, not followup
            except Exception:
                pass
