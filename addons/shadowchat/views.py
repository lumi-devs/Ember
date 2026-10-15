# cogs/shadowchat/views.py  —  Components V2 (discord.py master / ≥ 2.7)
# Ultra-interactive, animated-feel UI — NO embeds, only Components V2.
from __future__ import annotations

import discord
from discord import ui

# GUILD_ID removed — guild_id passed as parameter at runtime

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _text_view(text: str, *, accent: str = "#5865F2") -> ui.LayoutView:
    """Wrap a plain text string in a minimal ComponentV2 container."""
    v = ui.LayoutView(timeout=None)
    v.add_item(
        ui.Container(
            ui.TextDisplay(text),
            accent_color=discord.Color.from_str(accent),
        )
    )
    return v


def _info_view(title: str, body: str, *, accent: str = "#5865F2") -> ui.LayoutView:
    """Two-line container: bold title + body text."""
    v = ui.LayoutView(timeout=None)
    v.add_item(
        ui.Container(
            ui.TextDisplay(f"## {title}"),
            ui.Separator(spacing=discord.SeparatorSpacing.small),
            ui.TextDisplay(body),
            accent_color=discord.Color.from_str(accent),
        )
    )
    return v


def _progress_bar(filled: int, total: int = 10) -> str:
    """Render a text-based progress/activity bar."""
    filled = max(0, min(filled, total))
    return "▓" * filled + "░" * (total - filled)


# ─────────────────────────────────────────────────────────────────────────────
# Entry card — with live stats
# ─────────────────────────────────────────────────────────────────────────────


class FindMatchButton(ui.Button):
    def __init__(self):
        super().__init__(
            label="Find Match",
            style=discord.ButtonStyle.success,
            emoji="🔍",
            custom_id="sc_find_match",
        )

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("ShadowChat")
        if cog:
            await cog.handle_find_match(interaction)


class MyStatsButton(ui.Button):
    def __init__(self):
        super().__init__(
            label="My Stats",
            style=discord.ButtonStyle.secondary,
            emoji="📊",
            custom_id="sc_my_stats",
        )

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("ShadowChat")
        if cog:
            await cog.handle_my_stats(interaction)


class LeaveQueueButton(ui.Button):
    def __init__(self):
        super().__init__(
            label="Leave Queue",
            style=discord.ButtonStyle.danger,
            emoji="🚶",
            custom_id="sc_leave_queue",
        )

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("ShadowChat")
        if cog:
            await cog.handle_leave_queue(interaction)


class EntryActionRow(ui.ActionRow):
    def __init__(self):
        super().__init__()
        self.add_item(FindMatchButton())
        self.add_item(MyStatsButton())
        self.add_item(LeaveQueueButton())


class EntryLayoutView(ui.LayoutView):
    """Persistent entry card with live stats for the ShadowChat entry channel."""

    def __init__(self, stats: dict | None = None):
        super().__init__(timeout=None)

        # Build live stats section
        if stats:
            active = stats.get("active_sessions", 0)
            queue = stats.get("queue_size", 0)
            chatting = stats.get("users_chatting", 0)
            total_sessions = stats.get("total_sessions", 0)
            total_users = stats.get("total_users", 0)

            # Activity level bar (based on active sessions, max ~10)
            activity = min(active, 10)
            bar = _progress_bar(activity)

            stats_text = (
                f"```\n"
                f" LIVE   {bar}\n"
                f"```\n"
                f"💬 **{active}** active session{'s' if active != 1 else ''}  "
                f"·  👥 **{chatting}** chatting now\n"
                f"⏳ **{queue}** in queue  "
                f"·  🌐 **{total_users}** total users  "
                f"·  📈 **{total_sessions}** sessions all-time"
            )
        else:
            stats_text = (
                "```\n"
                " LIVE   ░░░░░░░░░░\n"
                "```\n"
                "💬 **—** active sessions  ·  👥 **—** chatting  ·  ⏳ **—** in queue"
            )

        self.add_item(
            ui.Container(
                ui.TextDisplay("# 🌑 ShadowChat\n" "-# anonymous · gender-matched · relay chat"),
                ui.Separator(spacing=discord.SeparatorSpacing.small),
                ui.TextDisplay(stats_text),
                ui.Separator(spacing=discord.SeparatorSpacing.small),
                ui.TextDisplay(
                    "**» How it works**\n"
                    "1️⃣  Hit **Find Match** — paired by gender preference\n"
                    "2️⃣  Private channel created — only you and the bot can see it\n"
                    "3️⃣  Messages relay anonymously under a codename\n"
                    "4️⃣  Ready? Use **Reveal** to unmask when both consent\n\n"
                    "-# 🔒 *Your identity stays hidden until you both agree to reveal.*"
                ),
                ui.Separator(spacing=discord.SeparatorSpacing.small),
                EntryActionRow(),
                accent_color=discord.Color.from_str("#131313"),
            )
        )


# ─────────────────────────────────────────────────────────────────────────────
# Session channel control buttons
# ─────────────────────────────────────────────────────────────────────────────


class EndChatButton(ui.Button):
    def __init__(self, session_id: str):
        super().__init__(
            label="End",
            style=discord.ButtonStyle.danger,
            emoji="🚪",
            custom_id=f"sc_end_{session_id}",
        )

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("ShadowChat")
        if cog:
            await cog.handle_end(interaction, self.custom_id.removeprefix("sc_end_"), skip=False)


class SkipButton(ui.Button):
    def __init__(self, session_id: str):
        super().__init__(
            label="Skip",
            style=discord.ButtonStyle.secondary,
            emoji="⏭️",
            custom_id=f"sc_skip_{session_id}",
        )

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("ShadowChat")
        if cog:
            await cog.handle_end(interaction, self.custom_id.removeprefix("sc_skip_"), skip=True)


class ReportButton(ui.Button):
    def __init__(self, session_id: str):
        super().__init__(
            label="Report",
            style=discord.ButtonStyle.danger,
            emoji="🚨",
            custom_id=f"sc_report_{session_id}",
        )

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("ShadowChat")
        if cog:
            await cog.handle_report(interaction, self.custom_id.removeprefix("sc_report_"))


class RevealButton(ui.Button):
    def __init__(self, session_id: str):
        super().__init__(
            label="Reveal",
            style=discord.ButtonStyle.success,
            emoji="✨",
            custom_id=f"sc_reveal_{session_id}",
        )

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("ShadowChat")
        if cog:
            await cog.handle_reveal_request(interaction, self.custom_id.removeprefix("sc_reveal_"))


class BookDateButton(ui.Button):
    """Replaces the Reveal button once a reveal is already done."""

    def __init__(self, session_id: str):
        super().__init__(
            label="Book a Date",
            style=discord.ButtonStyle.success,
            emoji="📅",
            custom_id=f"sc_bookdate_{session_id}",
        )

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("ShadowChat")
        if cog:
            await cog.handle_reveal_book_date(
                interaction, self.custom_id.removeprefix("sc_bookdate_")
            )


class SessionStatsButton(ui.Button):
    def __init__(self, session_id: str):
        super().__init__(
            label="Stats",
            style=discord.ButtonStyle.secondary,
            emoji="📊",
            custom_id=f"sc_stats_{session_id}",
        )

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("ShadowChat")
        if cog:
            await cog.handle_my_stats(interaction)


class SessionActionRow(ui.ActionRow):
    def __init__(self, session_id: str, reveal_done: bool = False):
        super().__init__()
        self.add_item(EndChatButton(session_id))
        self.add_item(SkipButton(session_id))
        self.add_item(ReportButton(session_id))
        if reveal_done:
            self.add_item(BookDateButton(session_id))
        else:
            self.add_item(RevealButton(session_id))
        self.add_item(SessionStatsButton(session_id))


# ─────────────────────────────────────────────────────────────────────────────
# Session welcome card (pinned in each private channel)
# ─────────────────────────────────────────────────────────────────────────────


class SessionWelcomeLayoutView(ui.LayoutView):
    """Pinned in the user's private session channel. Ultra-interactive feel."""

    def __init__(
        self,
        session_id: str,
        your_codename: str = "",
        partner_codename: str = "",
        reveal_done: bool = False,
    ):
        super().__init__(timeout=None)

        if your_codename:
            body = (
                f"You are **`{your_codename}`**\n"
                f"Your partner is **`{partner_codename}`**\n\n"
                f"Just type here — messages relay anonymously.\n"
                f"React to any message to send an emoji to your partner!"
            )
            tip = (
                "-# 💡 *Reply to messages for context · "
                "React with emoji to express yourself · "
                "Use the buttons below to control your session*"
            )
        else:
            body = "Session controls"
            tip = ""

        components: list = [
            ui.TextDisplay("# 🌑 Match Found!"),
            ui.Separator(spacing=discord.SeparatorSpacing.small),
            ui.TextDisplay(body),
        ]

        if tip:
            components.append(ui.Separator(spacing=discord.SeparatorSpacing.small))
            components.append(ui.TextDisplay(tip))

        components.append(ui.Separator(spacing=discord.SeparatorSpacing.small))
        components.append(SessionActionRow(session_id, reveal_done=reveal_done))

        self.add_item(
            ui.Container(
                *components,
                accent_color=discord.Color.from_str("#5865F2"),
            )
        )


# ─────────────────────────────────────────────────────────────────────────────
# Reveal request — sent into partner's channel
# ─────────────────────────────────────────────────────────────────────────────


class RevealAcceptButton(ui.Button):
    def __init__(self, session_id: str):
        super().__init__(
            label="Accept Reveal",
            style=discord.ButtonStyle.success,
            emoji="✨",
            custom_id=f"sc_reveal_accept_{session_id}",
        )

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("ShadowChat")
        if cog:
            await cog.handle_reveal_accept(
                interaction, self.custom_id.removeprefix("sc_reveal_accept_")
            )


class RevealDeclineButton(ui.Button):
    def __init__(self, session_id: str):
        super().__init__(
            label="Decline",
            style=discord.ButtonStyle.danger,
            emoji="❌",
            custom_id=f"sc_reveal_decline_{session_id}",
        )

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("ShadowChat")
        if cog:
            await cog.handle_reveal_decline(
                interaction, self.custom_id.removeprefix("sc_reveal_decline_")
            )


class RevealResponseActionRow(ui.ActionRow):
    def __init__(self, session_id: str):
        super().__init__()
        self.add_item(RevealAcceptButton(session_id))
        self.add_item(RevealDeclineButton(session_id))


class RevealRequestLayoutView(ui.LayoutView):
    """Sent into the partner's channel to ask for consent."""

    def __init__(self, session_id: str, timeout_secs: int = 60):
        super().__init__(timeout=None)
        self.add_item(
            ui.Container(
                ui.TextDisplay("# ✨ Reveal Request"),
                ui.Separator(spacing=discord.SeparatorSpacing.small),
                ui.TextDisplay(
                    "Your partner wants to **unmask** and reveal their real identity!\n\n"
                    "**✨ Accept** — real names & avatars replace codenames\n"
                    "**❌ Decline** — chat continues anonymously\n\n"
                    f"-# ⏱️ You have **{timeout_secs}s** to decide. No response = declined."
                ),
                ui.Separator(spacing=discord.SeparatorSpacing.small),
                RevealResponseActionRow(session_id),
                accent_color=discord.Color.from_str("#FAA61A"),
            )
        )


# ─────────────────────────────────────────────────────────────────────────────
# Reveal accept follow-up — ephemeral choice card
# ─────────────────────────────────────────────────────────────────────────────


class RevealHereButton(ui.Button):
    def __init__(self, session_id: str):
        super().__init__(
            label="Reveal Here",
            style=discord.ButtonStyle.success,
            emoji="👁️",
            custom_id=f"sc_reveal_here_{session_id}",
        )

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("ShadowChat")
        if cog:
            await cog.handle_reveal_here(
                interaction, self.custom_id.removeprefix("sc_reveal_here_")
            )


class RevealBookDateButton(ui.Button):
    def __init__(self, session_id: str):
        super().__init__(
            label="Book a Date",
            style=discord.ButtonStyle.success,
            emoji="📅",
            custom_id=f"sc_reveal_book_{session_id}",
        )

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("ShadowChat")
        if cog:
            await cog.handle_reveal_book_date(
                interaction, self.custom_id.removeprefix("sc_reveal_book_")
            )


class RevealChoiceActionRow(ui.ActionRow):
    def __init__(self, session_id: str):
        super().__init__()
        self.add_item(RevealHereButton(session_id))
        self.add_item(RevealBookDateButton(session_id))


class RevealChoiceLayoutView(ui.LayoutView):
    """Ephemeral: acceptor picks how to reveal after clicking Accept."""

    def __init__(self, session_id: str):
        super().__init__(timeout=60)
        self.add_item(
            ui.Container(
                ui.TextDisplay("# 🎭 How do you want to reveal?"),
                ui.Separator(spacing=discord.SeparatorSpacing.small),
                ui.TextDisplay(
                    "**👁️ Reveal Here**\n"
                    "Real names & avatars replace codenames in these channels.\n"
                    "The chat continues — just unmasked.\n\n"
                    "**📅 Book a Date**\n"
                    "A new private channel is created for both of you.\n"
                    "Fresh start, no anonymity walls."
                ),
                ui.Separator(spacing=discord.SeparatorSpacing.small),
                RevealChoiceActionRow(session_id),
                accent_color=discord.Color.from_str("#57F287"),
            )
        )

    async def on_timeout(self):
        self.stop()


# ─────────────────────────────────────────────────────────────────────────────
# Reveal announcement — posted in both session channels
# ─────────────────────────────────────────────────────────────────────────────


class RevealAnnouncementView(ui.LayoutView):
    """Rich reveal card with Gallery showing both users' avatars."""

    def __init__(
        self,
        codename1: str,
        codename2: str,
        name1: str,
        name2: str,
        avatar1_url: str | None = None,
        avatar2_url: str | None = None,
    ):
        super().__init__(timeout=None)

        gallery_items: list[discord.MediaGalleryItem] = []
        if avatar1_url:
            gallery_items.append(
                discord.MediaGalleryItem(
                    url=avatar1_url,
                    description=f"{name1} ({codename1})",
                )
            )
        if avatar2_url:
            gallery_items.append(
                discord.MediaGalleryItem(
                    url=avatar2_url,
                    description=f"{name2} ({codename2})",
                )
            )

        components: list = [
            ui.TextDisplay("# ✨ The Masks Come Off"),
            ui.Separator(spacing=discord.SeparatorSpacing.small),
            ui.TextDisplay(
                f"**{codename1}** → **{name1}**\n"
                f"**{codename2}** → **{name2}**\n\n"
                f"*Messages will now show real names and avatars.*"
            ),
        ]

        if gallery_items:
            components.append(ui.Separator(spacing=discord.SeparatorSpacing.small))
            components.append(ui.MediaGallery(*gallery_items))

        self.add_item(
            ui.Container(
                *components,
                accent_color=discord.Color.from_str("#FAA61A"),
            )
        )


# ─────────────────────────────────────────────────────────────────────────────
# Stats display (ephemeral)
# ─────────────────────────────────────────────────────────────────────────────


class StatsLayoutView(ui.LayoutView):
    def __init__(self, member: discord.Member, stats: dict | None):
        super().__init__(timeout=None)
        if not stats:
            total_chats, total_seconds, total_msgs = 0, 0, 0
        else:
            total_chats = stats["total_chats"]
            total_seconds = stats["total_seconds"]
            total_msgs = stats["total_messages"]

        hours, rem = divmod(total_seconds, 3600)
        minutes = rem // 60
        avg_session = total_seconds // total_chats if total_chats > 0 else 0
        avg_mins = avg_session // 60

        # Activity level bar
        level = min(total_chats, 20)
        bar = _progress_bar(level, 20)

        self.add_item(
            ui.Container(
                ui.TextDisplay(f"# 📊 {member.display_name}"),
                ui.Separator(spacing=discord.SeparatorSpacing.small),
                ui.TextDisplay(
                    f"```\n"
                    f" LEVEL  {bar}  {total_chats} sessions\n"
                    f"```\n"
                    f"🗨️ **{total_chats}** total sessions\n"
                    f"⏱️ **{hours}h {minutes}m** time chatting\n"
                    f"💬 **{total_msgs}** messages sent\n"
                    f"📏 **{avg_mins}m** avg session length"
                ),
                accent_color=discord.Color.from_str("#5865F2"),
            )
        )


# ─────────────────────────────────────────────────────────────────────────────
# End confirmation (ephemeral, short-lived)
# ─────────────────────────────────────────────────────────────────────────────


class ConfirmEndButton(ui.Button):
    def __init__(self, session_id: str, skip: bool):
        label = "Yes, skip" if skip else "Yes, end"
        super().__init__(
            label=label,
            style=discord.ButtonStyle.danger,
            emoji="✅",
            custom_id=f"sc_confirm_end_{session_id}",
        )
        self.session_id = session_id
        self.skip = skip

    async def callback(self, interaction: discord.Interaction):
        self.view.stop()
        await interaction.response.defer()
        cog = interaction.client.get_cog("ShadowChat")
        if cog:
            await cog.end_session(
                self.session_id,
                reason="skipped" if self.skip else "ended",
                initiator_id=interaction.user.id,
                requeue_initiator=self.skip,
            )


class CancelEndButton(ui.Button):
    def __init__(self):
        super().__init__(
            label="Cancel",
            style=discord.ButtonStyle.secondary,
            emoji="↩️",
            custom_id="sc_confirm_cancel",
        )

    async def callback(self, interaction: discord.Interaction):
        self.view.stop()
        cancelled_view = _info_view("Cancelled", "Nothing changed.", accent="#4f545c")
        await interaction.response.edit_message(view=cancelled_view)


class EndConfirmActionRow(ui.ActionRow):
    def __init__(self, session_id: str, skip: bool):
        super().__init__()
        self.add_item(ConfirmEndButton(session_id, skip))
        self.add_item(CancelEndButton())


class EndConfirmLayoutView(ui.LayoutView):
    def __init__(self, session_id: str, skip: bool = False):
        super().__init__(timeout=30)
        self.session_id = session_id
        self.skip = skip
        action = "skip this chat and re-queue" if skip else "end this chat"
        icon = "⏭️ Skip" if skip else "🚪 End Chat"
        self.add_item(
            ui.Container(
                ui.TextDisplay(f"## {icon}"),
                ui.Separator(spacing=discord.SeparatorSpacing.small),
                ui.TextDisplay(f"Are you sure you want to **{action}**?"),
                ui.Separator(spacing=discord.SeparatorSpacing.small),
                EndConfirmActionRow(session_id, skip),
                accent_color=discord.Color.from_str("#ED4245"),
            )
        )

    async def on_timeout(self):
        self.stop()


# ─────────────────────────────────────────────────────────────────────────────
# DM match notification
# ─────────────────────────────────────────────────────────────────────────────


class DmMuteButton(ui.Button):
    def __init__(self):
        super().__init__(
            label="Mute match DMs (12h)",
            style=discord.ButtonStyle.secondary,
            emoji="🔕",
            custom_id="sc_dm_mute",
        )

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("ShadowChat")
        if cog:
            await cog.db.dm_mute_set(interaction.user.id, hours=12)
        muted_view = _info_view(
            "🔕 DMs Muted",
            "Match DMs silenced for **12 hours**.\nYou'll still be matched — just no DM ping.",
            accent="#4f545c",
        )
        await interaction.response.send_message(view=muted_view, ephemeral=True)


class DmNotificationView(ui.LayoutView):
    """Persistent Components V2 view for the match DM notification."""

    def __init__(self, channel_id: int | None = None, guild_id: int | None = None):
        super().__init__(timeout=None)
        action_row = ui.ActionRow()
        if channel_id and guild_id:
            action_row.add_item(
                ui.Button(
                    label="Go to Channel",
                    style=discord.ButtonStyle.link,
                    emoji="💬",
                    url=f"https://discord.com/channels/{guild_id}/{channel_id}",
                )
            )
        action_row.add_item(DmMuteButton())
        self.add_item(
            ui.Container(
                ui.TextDisplay("# 🌑 You've been matched!"),
                ui.Separator(spacing=discord.SeparatorSpacing.small),
                ui.TextDisplay(
                    "Head to your **private channel** in the server to start chatting.\n\n"
                    "💡 **Tips:**\n"
                    "· Reply to messages for context\n"
                    "· React with emoji — your partner will see it!\n"
                    "· Use **Reveal** when you're ready to unmask\n\n"
                    "-# *Mute match DMs if you'd rather not get pinged.*"
                ),
                ui.Separator(spacing=discord.SeparatorSpacing.small),
                action_row,
                accent_color=discord.Color.from_str("#131313"),
            )
        )


# ─────────────────────────────────────────────────────────────────────────────
# Date channel welcome card
# ─────────────────────────────────────────────────────────────────────────────


class DateEndButton(ui.Button):
    def __init__(self, session_id: str):
        super().__init__(
            label="End / Delete Channel",
            style=discord.ButtonStyle.danger,
            emoji="🗑️",
            custom_id=f"sc_date_end_{session_id}",
        )

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("ShadowChat")
        if cog:
            await cog.handle_date_channel_delete(
                interaction, self.custom_id.removeprefix("sc_date_end_")
            )


class DateReportButton(ui.Button):
    def __init__(self, session_id: str):
        super().__init__(
            label="Report",
            style=discord.ButtonStyle.secondary,
            emoji="🚨",
            custom_id=f"sc_date_report_{session_id}",
        )

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("ShadowChat")
        if cog:
            await cog.handle_report(interaction, self.custom_id.removeprefix("sc_date_report_"))


class DateChannelActionRow(ui.ActionRow):
    def __init__(self, session_id: str):
        super().__init__()
        self.add_item(DateEndButton(session_id))
        self.add_item(DateReportButton(session_id))


class DateChannelWelcomeView(ui.LayoutView):
    """Pinned welcome card for the shared date channel."""

    def __init__(
        self,
        session_id: str,
        name1: str,
        name2: str,
        avatar1_url: str | None = None,
        avatar2_url: str | None = None,
    ):
        super().__init__(timeout=None)
        names = f"{name1} & {name2}" if name1 and name2 else "you two"

        components: list = [
            ui.TextDisplay("# 🎉 Connection Made!"),
            ui.Separator(spacing=discord.SeparatorSpacing.small),
            ui.TextDisplay(
                f"Hey **{names}** — the masks are off.\n\n"
                f"**This channel is yours.** It stays until you delete it.\n\n"
                f"⏰ No messages for **12 hours** = auto-close.\n\n"
                f"-# *Made something cool here? Share your story!*"
            ),
        ]

        gallery_items: list[discord.MediaGalleryItem] = []
        if avatar1_url:
            gallery_items.append(discord.MediaGalleryItem(url=avatar1_url, description=name1))
        if avatar2_url:
            gallery_items.append(discord.MediaGalleryItem(url=avatar2_url, description=name2))
        if gallery_items:
            components.append(ui.Separator(spacing=discord.SeparatorSpacing.small))
            components.append(ui.MediaGallery(*gallery_items))

        components.append(ui.Separator(spacing=discord.SeparatorSpacing.small))
        components.append(DateChannelActionRow(session_id))

        self.add_item(ui.Container(*components, accent_color=discord.Color.from_str("#FEE75C")))


# ─────────────────────────────────────────────────────────────────────────────
# Session Rating View — seamless in-place Components V2
# ─────────────────────────────────────────────────────────────────────────────

_STAR_LABELS = {1: "😞", 2: "😕", 3: "😐", 4: "🙂", 5: "😍"}
_STAR_STYLES = {
    1: discord.ButtonStyle.secondary,
    2: discord.ButtonStyle.secondary,
    3: discord.ButtonStyle.secondary,
    4: discord.ButtonStyle.secondary,
    5: discord.ButtonStyle.secondary,
}


class RatingStarButton(ui.Button):
    def __init__(self, session_id: str, rating: int):
        super().__init__(
            label=f"{'★' * rating}{'☆' * (5 - rating)}",
            style=_STAR_STYLES[rating],
            custom_id=f"sc_rate_{session_id}_{rating}",
        )

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("ShadowChat")
        if cog:
            parts = self.custom_id.split("_")
            await cog.handle_rating(interaction, "_".join(parts[2:-1]), int(parts[-1]))


class SkipRatingButton(ui.Button):
    def __init__(self, session_id: str):
        super().__init__(
            label="Skip (10 chats)",
            style=discord.ButtonStyle.secondary,
            emoji="🚫",
            custom_id=f"sc_skip_rating_{session_id}",
        )

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("ShadowChat")
        if cog:
            parts = self.custom_id.split("_", 3)
            session_id = parts[3] if len(parts) > 3 else ""
            await cog.handle_rating_skip(interaction, session_id)


class RatingActionRow(ui.ActionRow):
    def __init__(self, session_id: str):
        super().__init__()
        for i in range(1, 6):
            self.add_item(RatingStarButton(session_id, i))


class RatingSkipActionRow(ui.ActionRow):
    def __init__(self, session_id: str):
        super().__init__()
        self.add_item(SkipRatingButton(session_id))


class RatingLayoutView(ui.LayoutView):
    """Rating card shown after session ends. One click → edits to confirmation in-place."""

    def __init__(self, session_id: str):
        super().__init__(timeout=300)
        self.add_item(
            ui.Container(
                ui.TextDisplay("## ⭐ Rate Your Experience"),
                ui.Separator(spacing=discord.SeparatorSpacing.small),
                ui.TextDisplay(
                    "How was your ShadowChat session?\n"
                    "-# *Tap a star — one click, done. Higher ratings help you meet better people!*"
                ),
                ui.Separator(spacing=discord.SeparatorSpacing.small),
                RatingActionRow(session_id),
                RatingSkipActionRow(session_id),
                accent_color=discord.Color.from_str("#FAA61A"),
            )
        )

    async def on_timeout(self):
        self.stop()


class RatingDoneLayoutView(ui.LayoutView):
    """Replaces the rating card in-place once the user has voted."""

    def __init__(self, rating: int):
        super().__init__(timeout=None)
        emoji = _STAR_LABELS.get(rating, "⭐")
        stars = "★" * rating + "☆" * (5 - rating)
        self.add_item(
            ui.Container(
                ui.TextDisplay(f"## {emoji} Rated {stars}"),
                ui.Separator(spacing=discord.SeparatorSpacing.small),
                ui.TextDisplay("Thanks for the feedback — it helps improve match quality!"),
                accent_color=discord.Color.from_str("#57F287"),
            )
        )


class RatingSkipDoneLayoutView(ui.LayoutView):
    """Replaces the rating card when the user skips rating for the next 10 chats."""

    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(
            ui.Container(
                ui.TextDisplay("## 🚫 Rating Skipped"),
                ui.Separator(spacing=discord.SeparatorSpacing.small),
                ui.TextDisplay(
                    "No worries — rating skipped for the next **10 chats**.\n"
                    "-# *Ratings help you meet better people. Give it a try next time!*"
                ),
                accent_color=discord.Color.from_str("#4f545c"),
            )
        )


# ─────────────────────────────────────────────────────────────────────────────
# Mod-log report card
# ─────────────────────────────────────────────────────────────────────────────


class ReportLogView(ui.LayoutView):
    """Posted to the mod/report channel when a user files a report."""

    def __init__(
        self,
        reporter_id: int,
        reported_id: int,
        reason: str,
        session_id: str,
        log_text: str | None = None,
        reveal_status: str = "unknown",
        reporter_label: str | None = None,
        reported_label: str | None = None,
    ):
        super().__init__(timeout=None)
        reporter_str = reporter_label or f"`{reporter_id}`"
        reported_str = reported_label or f"`{reported_id}`"
        body = (
            f"**Reporter:** {reporter_str}\n"
            f"**Reported:** {reported_str}\n"
            f"**Reason:** {reason}\n"
            f"**Session:** `{session_id}`\n"
            f"**Reveal:** {reveal_status}"
        )
        if log_text and len(log_text) <= 1800:
            body += f"\n\n**Chat log:**\n```\n{log_text}\n```"

        self.add_item(
            ui.Container(
                ui.TextDisplay("# 🚨 ShadowChat Report"),
                ui.Separator(spacing=discord.SeparatorSpacing.small),
                ui.TextDisplay(body),
                accent_color=discord.Color.from_str("#ED4245"),
            )
        )


# ─────────────────────────────────────────────────────────────────────────────
# Report modal (modals can't be Components V2)
# ─────────────────────────────────────────────────────────────────────────────


class ReportModal(ui.Modal, title="Report User"):
    def __init__(self, session_id: str):
        super().__init__()
        self.session_id = session_id

        self.reason = ui.TextInput(
            label="Reason",
            placeholder="Describe what happened...",
            style=discord.TextStyle.paragraph,
            max_length=500,
            required=True,
        )
        self.add_item(self.reason)

        self.evidence_upload = ui.FileUpload(required=False)
        self.add_item(ui.Label(text="Attach evidence (optional)", component=self.evidence_upload))

    async def on_submit(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("ShadowChat")
        if not cog:
            await interaction.response.send_message("ShadowChat is unavailable.", ephemeral=True)
            return
        attachment = self.evidence_upload.values[0] if self.evidence_upload.values else None
        await cog.handle_report_submit(
            interaction, self.session_id, self.reason.value, attachment=attachment
        )
