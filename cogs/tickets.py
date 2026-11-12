from __future__ import annotations

import io
import re
import sqlite3
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple, List, Dict, Any

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config.branding import EMBED_COLOR, FOOTER_TEXT
from config.tickets import TICKET_TYPES
from utils.checks import EU_TICKET_STAFF_ROLE_ID
from utils.transcripts import create_html_transcript
from utils.approval import ApprovalModal, ApprovalView

EU_STAFF_ROLE_ID = EU_TICKET_STAFF_ROLE_ID
STATUS_EMOJIS = ("🟢", "🟠", "🔴")
log = logging.getLogger("Tickets")


# Different questions per ticket type. Discord modals allow up to 5 fields.
TICKET_QUESTIONS: Dict[str, List[Dict[str, Any]]] = {
    "general": [
        {"label": "What is your IGN?", "max": 32, "style": "short", "required": True},
        {"label": "How can we help you?", "max": 700, "style": "paragraph", "required": True},
    ],
    "base": [
        {"label": "What is your IGN?", "max": 32, "style": "short", "required": True},
        {"label": "Where is your base? (grid/landmark)", "max": 100, "style": "short", "required": True},
        {"label": "What is the issue with your base?", "max": 700, "style": "paragraph", "required": True},
    ],
    "clan": [
        {"label": "What is your IGN?", "max": 32, "style": "short", "required": True},
        {"label": "Clan name / tag", "max": 50, "style": "short", "required": True},
        {"label": "What do you need help with?", "max": 700, "style": "paragraph", "required": True},
    ],
    "shop": [
        {"label": "What is your IGN?", "max": 32, "style": "short", "required": True},
        {"label": "Order / Transaction ID", "max": 100, "style": "short", "required": True},
        {"label": "What is the issue with your purchase?", "max": 700, "style": "paragraph", "required": True},
    ],
    "raid": [
        {"label": "What is your team name?", "max": 32, "style": "short", "required": True},
        {"label": "What bounty team you are wanting to raid?", "max": 100, "style": "short", "required": True},
    ],
    "bug": [
        {"label": "What is your IGN?", "max": 32, "style": "short", "required": True},
        {"label": "Steps to reproduce", "max": 500, "style": "paragraph", "required": True},
        {"label": "What happens / expected vs actual", "max": 500, "style": "paragraph", "required": True},
    ],
}

DEFAULT_QUESTIONS = TICKET_QUESTIONS["general"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def humanize_minutes(mins: int) -> str:
    mins = max(1, int(mins))
    if mins < 60:
        return f"{mins} min" if mins == 1 else f"{mins} mins"
    h = mins // 60
    m = mins % 60
    return f"{h}h {m}m" if m else f"{h}h"


def get_ticket_status_emoji(created_at: datetime) -> str:
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    age = utcnow() - created_at
    if age < timedelta(minutes=30):
        return "🟢"
    if age < timedelta(hours=1):
        return "🟠"
    return "🔴"


def setting_key(name: str) -> str:
    return f"tickets.{name}"


def slugify_text(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9-]+", "-", (value or "").strip().lower())
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    return cleaned or "player"


# ---------------- Modals & Views ----------------

class TicketQuestionsModal(ApprovalModal):
    """Dynamic modal — questions depend on which ticket type was picked."""

    def __init__(self, ticket_type: str):
        label = TICKET_TYPES.get(ticket_type, {}).get("label", ticket_type)
        super().__init__(title=f"{label}"[:45])
        self.ticket_type = ticket_type
        self.inputs: List[Tuple[str, discord.ui.TextInput]] = []

        questions = TICKET_QUESTIONS.get(ticket_type, DEFAULT_QUESTIONS)
        for q in questions[:5]:
            style = discord.TextStyle.paragraph if q.get("style") == "paragraph" else discord.TextStyle.short
            ti = discord.ui.TextInput(
                label=q["label"][:45],
                style=style,
                required=bool(q.get("required", True)),
                max_length=int(q.get("max", 200)),
            )
            self.add_item(ti)
            self.inputs.append((q["label"], ti))

    async def on_submit(self, interaction: discord.Interaction):
        cog: Optional["Tickets"] = interaction.client.get_cog("Tickets")
        if not cog:
            return await interaction.response.send_message("Tickets system not loaded.", ephemeral=True)

        answers = [(label, inp.value.strip()) for label, inp in self.inputs]
        await cog.create_ticket(interaction, self.ticket_type, answers)


class AddPeopleSelectView(ApprovalView):
    """Ephemeral view with a user-select dropdown — no typing required."""

    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.select(
        cls=discord.ui.UserSelect,
        placeholder="Pick members to add to this ticket...",
        min_values=1,
        max_values=10,
    )
    async def user_select(
        self, interaction: discord.Interaction, select: discord.ui.UserSelect
    ):
        cog = interaction.client.get_cog("Tickets")
        if not cog:
            return await interaction.response.send_message(
                "Tickets system not loaded.", ephemeral=True
            )
        await cog.add_people_to_ticket(interaction, select.values)


class TicketTypeSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="Select a support option...",
            options=[
                discord.SelectOption(label=v["label"], description=v["description"], value=k)
                for k, v in TICKET_TYPES.items()
            ],
            custom_id="ticket:type",
        )

    async def callback(self, interaction: discord.Interaction):
        selected = self.values[0]
        await interaction.response.send_modal(TicketQuestionsModal(selected))


class TicketPanelView(ApprovalView):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketTypeSelect())


class CloseConfirmView(ApprovalView):
    def __init__(self):
        super().__init__(timeout=3600)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, custom_id="ticket:confirm_close")
    async def close(self, interaction: discord.Interaction, _):
        cog = interaction.client.get_cog("Tickets")
        if cog:
            try:
                await cog.close_ticket(interaction.channel, interaction.user)
            except sqlite3.DatabaseError:
                msg = "Ticket database is currently unavailable. Please ask an admin to repair the database and try again."
                if interaction.response.is_done():
                    await interaction.followup.send(msg, ephemeral=True)
                else:
                    await interaction.response.send_message(msg, ephemeral=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, custom_id="ticket:confirm_cancel")
    async def cancel(self, interaction: discord.Interaction, _):
        await interaction.response.send_message("Close cancelled.", ephemeral=True)


class TicketActionsView(ApprovalView):
    def __init__(self, ticket_id: int):
        super().__init__(timeout=None)
        self.ticket_id = ticket_id

    async def staff_allowed(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return False
        if interaction.user.guild_permissions.administrator:
            return True
        if EU_STAFF_ROLE_ID and any(r.id == EU_STAFF_ROLE_ID for r in interaction.user.roles):
            return True
        try:
            rows = await interaction.client.db.fetchall(
                "SELECT role_id FROM staff_roles WHERE guild_id = ?;",
                (str(interaction.guild.id),),
            )
            staff_role_ids = {int(r["role_id"]) for r in rows}
            if any(r.id in staff_role_ids for r in interaction.user.roles):
                return True
        except Exception:
            pass
        return False

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, custom_id="ticket:action_close")
    async def close(self, interaction: discord.Interaction, _):
        if not await self.staff_allowed(interaction):
            return await interaction.response.send_message("Staff only.", ephemeral=True)
        cog = interaction.client.get_cog("Tickets")
        try:
            await cog.close_ticket(interaction.channel, interaction.user, staff_closed=True)
        except sqlite3.DatabaseError:
            msg = "Ticket database is currently unavailable. Please ask an admin to repair the database and try again."
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)

    @discord.ui.button(label="Request Close", style=discord.ButtonStyle.secondary, custom_id="ticket:action_request_close")
    async def request_close(self, interaction: discord.Interaction, _):
        if not await self.staff_allowed(interaction):
            return await interaction.response.send_message("Staff only.", ephemeral=True)
        cog = interaction.client.get_cog("Tickets")
        try:
            await cog.request_close(interaction, interaction.user)
        except sqlite3.DatabaseError:
            msg = "Ticket database is currently unavailable. Please ask an admin to repair the database and try again."
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.success, custom_id="ticket:action_claim")
    async def claim(self, interaction: discord.Interaction, _):
        if not await self.staff_allowed(interaction):
            return await interaction.response.send_message("Staff only.", ephemeral=True)
        cog = interaction.client.get_cog("Tickets")
        if cog:
            await cog.claim_ticket(interaction, interaction.user)

    @discord.ui.button(label="Add People", style=discord.ButtonStyle.primary, custom_id="ticket:action_add_people")
    async def add_people(self, interaction: discord.Interaction, _):
        if not await self.staff_allowed(interaction):
            return await interaction.response.send_message("Staff only.", ephemeral=True)
        await interaction.response.send_message(
            "Select members to add to this ticket:",
            view=AddPeopleSelectView(),
            ephemeral=True,
        )


# ---------------- Cog ----------------

class Tickets(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.panel_refresher.add_exception_type(sqlite3.DatabaseError)
        self.inactivity_watcher.add_exception_type(sqlite3.DatabaseError)
        self.close_confirmation_watcher.add_exception_type(sqlite3.DatabaseError)
        self.emoji_status_updater.add_exception_type(sqlite3.DatabaseError)
        self.panel_refresher.start()
        self.inactivity_watcher.start()
        self.close_confirmation_watcher.start()
        self.emoji_status_updater.start()

    def cog_unload(self) -> None:
        self.panel_refresher.cancel()
        self.inactivity_watcher.cancel()
        self.close_confirmation_watcher.cancel()
        self.emoji_status_updater.cancel()

    # ---- settings ----

    async def get_setting(self, guild_id: int, name: str, default=None):
        row = await self.bot.db.fetchone(
            "SELECT value FROM guild_settings WHERE guild_id = ? AND key = ?;",
            (str(guild_id), setting_key(name)),
        )
        if not row:
            return default
        raw = row["value"]
        try:
            return int(raw)
        except Exception:
            try:
                import json
                return int(json.loads(raw))
            except Exception:
                return default

    async def send_ephemeral(self, interaction: discord.Interaction, content: str):
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=True)
        else:
            await interaction.response.send_message(content, ephemeral=True)

    async def owner_allowed(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return False
        if interaction.guild.owner_id == interaction.user.id:
            return True
        try:
            return await self.bot.is_owner(interaction.user)
        except Exception:
            return False

    async def get_logs_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        logs_id = await self.get_setting(guild.id, "logs_channel_id")
        if not logs_id:
            return None
        logs = guild.get_channel(int(logs_id))
        return logs if isinstance(logs, discord.TextChannel) else None

    async def get_ticket_category(self, guild: discord.Guild) -> Optional[discord.CategoryChannel]:
        cat_id = await self.get_setting(guild.id, "category_id")
        if not cat_id:
            return None
        cat = guild.get_channel(int(cat_id))
        return cat if isinstance(cat, discord.CategoryChannel) else None

    # ---- maintenance ----

    async def reconcile_open_tickets(self, guild: discord.Guild):
        rows = await self.bot.db.fetchall(
            "SELECT channel_id FROM tickets WHERE guild_id = ? AND is_closed = 0;",
            (str(guild.id),),
        )
        stale_channels = []
        for row in rows:
            channel_id = int(row["channel_id"])
            if not guild.get_channel(channel_id):
                stale_channels.append(str(channel_id))

        for channel_id in stale_channels:
            await self.bot.db.execute(
                "UPDATE tickets SET is_closed = 1 WHERE guild_id = ? AND channel_id = ?;",
                (str(guild.id), channel_id),
            )

    async def count_open_tickets(self, guild_id: int) -> int:
        row = await self.bot.db.fetchone(
            "SELECT COUNT(*) AS c FROM tickets WHERE guild_id = ? AND is_closed = 0;",
            (str(guild_id),),
        )
        return int(row["c"] if row else 0)

    # ---- panel ----

    async def build_panel_embed(self, guild: discord.Guild) -> discord.Embed:
        await self.reconcile_open_tickets(guild)
        open_total = await self.count_open_tickets(guild.id)

        response_speed = "High" if open_total >= 20 else "Fast"
        eta_minutes = min(55, 12 + (open_total * 2))

        embed = discord.Embed(
            title="Support Tickets",
            description="Select a support option below to open a ticket.",
            color=EMBED_COLOR,
        )
        embed.add_field(
            name="Support Status",
            value=(
                f"**Open Tickets:** {open_total}\n"
                f"**Response Speed:** {response_speed}\n"
                f"**Estimated Help Time:** {humanize_minutes(eta_minutes)}"
            ),
            inline=False,
        )
        embed.set_footer(text=FOOTER_TEXT)
        return embed

    async def refresh_panel_now(self, guild: discord.Guild):
        if not await self.bot.is_guild_approved(guild.id):
            return
        panel_channel_id = await self.get_setting(guild.id, "panel_channel_id")
        if not panel_channel_id:
            return
        channel = guild.get_channel(int(panel_channel_id))
        if not isinstance(channel, discord.TextChannel):
            return

        embed = await self.build_panel_embed(guild)
        row = await self.bot.db.fetchone(
            "SELECT message_id FROM ticket_panel_state WHERE guild_id = ?;",
            (str(guild.id),),
        )

        if row:
            try:
                msg = await channel.fetch_message(int(row["message_id"]))
                await msg.edit(embed=embed, view=TicketPanelView())
                return
            except (discord.NotFound, discord.HTTPException):
                pass

        async for msg in channel.history(limit=30):
            if msg.author.id != self.bot.user.id or not msg.embeds:
                continue
            if (msg.embeds[0].title or "").strip().lower() == "support tickets":
                try:
                    await msg.edit(embed=embed, view=TicketPanelView())
                    await self.bot.db.execute(
                        "INSERT OR REPLACE INTO ticket_panel_state (guild_id, channel_id, message_id) VALUES (?, ?, ?);",
                        (str(guild.id), str(channel.id), str(msg.id)),
                    )
                    return
                except Exception:
                    pass

        msg = await channel.send(embed=embed, view=TicketPanelView())
        await self.bot.db.execute(
            "INSERT OR REPLACE INTO ticket_panel_state (guild_id, channel_id, message_id) VALUES (?, ?, ?);",
            (str(guild.id), str(channel.id), str(msg.id)),
        )

    @tasks.loop(minutes=1)
    async def panel_refresher(self):
        for guild in self.bot.guilds:
            if not await self.bot.is_guild_approved(guild.id):
                continue
            try:
                await self.refresh_panel_now(guild)
            except Exception:
                log.exception("panel_refresher failed for guild %s", guild.id)

    @panel_refresher.before_loop
    async def before_panel(self):
        await self.bot.wait_until_ready()
        self.bot.add_view(TicketPanelView())
        self.bot.add_view(TicketActionsView(0))
        self.bot.add_view(CloseConfirmView())

    # ---- ticket creation ----

    async def create_ticket(
        self,
        interaction: discord.Interaction,
        ticket_type: str,
        answers: List[Tuple[str, str]],
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)

        guild = interaction.guild
        owner = interaction.user
        if not guild or not isinstance(owner, discord.Member):
            return await interaction.followup.send("Could not create ticket outside a server.", ephemeral=True)

        category = await self.get_ticket_category(guild)
        if not category:
            return await interaction.followup.send(
                "Tickets aren't configured yet. An admin needs to run **/setup tickets**.",
                ephemeral=True,
            )

        existing = await self.bot.db.fetchone(
            "SELECT channel_id FROM tickets WHERE guild_id = ? AND owner_id = ? AND is_closed = 0 LIMIT 1;",
            (str(guild.id), str(owner.id)),
        )
        if existing:
            ch = guild.get_channel(int(existing["channel_id"]))
            if isinstance(ch, discord.TextChannel):
                return await interaction.followup.send(
                    f"You already have an open ticket: {ch.mention}", ephemeral=True
                )

        row = await self.bot.db.fetchone(
            "SELECT MAX(ticket_id) AS m FROM tickets WHERE guild_id = ?;",
            (str(guild.id),),
        )
        ticket_id = int((row["m"] or 0) + 1)

        initial_emoji = "🟢"
        type_slug = slugify_text(ticket_type)
        channel_name = f"{initial_emoji}-{type_slug}-{ticket_id}"[:100]

        try:
            channel = await guild.create_text_channel(name=channel_name, category=category)
        except discord.Forbidden:
            return await interaction.followup.send(
                "I don't have permission to create channels in that category.", ephemeral=True
            )
        except Exception as e:
            log.exception("Failed to create ticket channel")
            return await interaction.followup.send(f"Failed to create ticket: `{e}`", ephemeral=True)

        staff_roles_to_ping: List[discord.Role] = []
        try:
            staff_role_rows = await self.bot.db.fetchall(
                "SELECT role_id FROM staff_roles WHERE guild_id = ?;",
                (str(guild.id),),
            )
            for sr in staff_role_rows:
                r = guild.get_role(int(sr["role_id"]))
                if r:
                    staff_roles_to_ping.append(r)
        except Exception:
            pass

        if not staff_roles_to_ping and EU_STAFF_ROLE_ID:
            r = guild.get_role(EU_STAFF_ROLE_ID)
            if r:
                staff_roles_to_ping.append(r)

        if not staff_roles_to_ping:
            r = discord.utils.find(lambda r: "staff" in r.name.lower(), guild.roles)
            if r:
                staff_roles_to_ping.append(r)

        try:
            await channel.set_permissions(guild.default_role, view_channel=False)
            await channel.set_permissions(owner, view_channel=True, send_messages=True, read_messages=True)
            for sr in staff_roles_to_ping:
                await channel.set_permissions(sr, view_channel=True, send_messages=True, read_messages=True)
        except Exception:
            log.exception("Failed to set ticket permissions")

        await self.bot.db.execute(
            "INSERT INTO tickets (guild_id, ticket_id, channel_id, owner_id, server_key, ticket_type, opened_by, is_closed) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0);",
            (
                str(guild.id),
                ticket_id,
                str(channel.id),
                str(owner.id),
                "EU",
                ticket_type,
                str(owner.id),
            ),
        )

        ticket_row = await self.bot.db.fetchone(
            "SELECT created_at FROM tickets WHERE guild_id = ? AND ticket_id = ?;",
            (str(guild.id), ticket_id),
        )
        created_at = utcnow()
        if ticket_row and ticket_row["created_at"]:
            try:
                created_at = datetime.fromisoformat(ticket_row["created_at"])
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
            except Exception:
                created_at = utcnow()

        status_emoji = get_ticket_status_emoji(created_at)
        ticket_type_label = TICKET_TYPES.get(ticket_type, {}).get("label", ticket_type)

        embed = discord.Embed(
            title=f"{status_emoji} Ticket #{ticket_id}",
            description=(
                f"**Type:** {ticket_type_label}\n"
                f"**Owner:** {owner.mention}"
            ),
            color=EMBED_COLOR,
        )
        for label, value in answers:
            if not value:
                continue
            embed.add_field(name=label[:256], value=value[:1024], inline=False)
        embed.set_footer(text=FOOTER_TEXT)

        staff_pings = " ".join(r.mention for r in staff_roles_to_ping)
        content = f"{owner.mention} {staff_pings}".strip() if staff_pings else owner.mention
        await channel.send(content=content, embed=embed, view=TicketActionsView(ticket_id))

        await self.log_ticket_action(guild, f"Ticket #{ticket_id} ({ticket_type_label}) opened by {owner.mention}")

        try:
            await self.refresh_panel_now(guild)
        except Exception:
            pass

        await interaction.followup.send(f"Ticket created: {channel.mention}", ephemeral=True)

    async def claim_ticket(self, interaction: discord.Interaction, staff_user: discord.Member):
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message("Not a ticket channel.", ephemeral=True)

        try:
            row = await self.bot.db.fetchone(
                "SELECT claimed_by FROM tickets WHERE channel_id = ?;",
                (str(channel.id),),
            )
        except Exception:
            row = None

        if row and row["claimed_by"]:
            existing_claimer_id = int(row["claimed_by"])
            if existing_claimer_id != staff_user.id:
                claimer = channel.guild.get_member(existing_claimer_id)
                name = claimer.display_name if claimer else f"<@{existing_claimer_id}>"
                return await interaction.response.send_message(
                    f"❌ This ticket has already been claimed by **{name}**.",
                    ephemeral=True,
                )

        try:
            await self.bot.db.execute(
                "UPDATE tickets SET claimed_by = ? WHERE channel_id = ?;",
                (str(staff_user.id), str(channel.id)),
            )
        except Exception:
            pass

        await interaction.response.send_message(
            f"✅ Ticket claimed by {staff_user.mention}",
        )
        await self.log_ticket_action(
            channel.guild,
            f"Ticket {channel.mention} claimed by {staff_user.mention}",
        )

    async def add_people_to_ticket(
        self,
        interaction: discord.Interaction,
        members: list,
    ):
        channel = interaction.channel
        guild = interaction.guild

        if not guild or not isinstance(channel, discord.TextChannel):
            return

        added = []
        failed = []

        for member in members:
            if not isinstance(member, discord.Member):
                member = guild.get_member(member.id)
            if not member:
                failed.append(str(member))
                continue
            try:
                await channel.set_permissions(
                    member,
                    view_channel=True,
                    send_messages=True,
                    read_messages=True,
                )
                added.append(member.mention)
            except Exception:
                failed.append(member.display_name)

        parts = []
        if added:
            parts.append("Added: " + ", ".join(added))
        if failed:
            parts.append("Failed: " + ", ".join(failed))

        await interaction.response.send_message(
            "\n".join(parts) if parts else "No users added.",
            ephemeral=True,
        )

    # ---- request close ----

    async def request_close(self, interaction: discord.Interaction, staff_user: discord.Member):
        channel = interaction.channel
        guild = interaction.guild

        if not guild or not isinstance(channel, discord.TextChannel):
            return

        try:
            row = await self.bot.db.fetchone(
                "SELECT * FROM tickets WHERE channel_id = ?;",
                (str(channel.id),),
            )
        except sqlite3.DatabaseError:
            log.exception("request_close database error for channel %s", channel.id)
            await self.send_ephemeral(
                interaction,
                "Ticket database is currently unavailable. Please ask an admin to repair the database and try again.",
            )
            return
        if not row:
            return

        owner_id = int(row["owner_id"])
        owner = guild.get_member(owner_id)

        try:
            await self.bot.db.execute(
                "UPDATE tickets SET close_requested_by = ?, close_requested_at = ? WHERE channel_id = ?;",
                (str(staff_user.id), utcnow().isoformat(), str(channel.id)),
            )
        except sqlite3.DatabaseError:
            log.exception("request_close update database error for channel %s", channel.id)
            await self.send_ephemeral(
                interaction,
                "Ticket database is currently unavailable. Please ask an admin to repair the database and try again.",
            )
            return

        if owner:
            embed = discord.Embed(
                title="Close Confirmation",
                description=f"Staff {staff_user.mention} has finished helping. Please confirm to close this ticket or it will auto-close in 1 hour.",
                color=EMBED_COLOR,
            )
            await channel.send(content=owner.mention, embed=embed, view=CloseConfirmView())

        await self.send_ephemeral(interaction, "Close confirmation sent to owner.")
        await self.log_ticket_action(
            guild,
            f"Close requested by {staff_user.mention} in ticket {channel.mention}",
        )

    # ---- close ----

    async def close_ticket(self, channel: discord.TextChannel, closed_by: discord.Member, staff_closed: bool = False):
        try:
            row = await self.bot.db.fetchone(
                "SELECT * FROM tickets WHERE channel_id = ?;",
                (str(channel.id),),
            )
        except sqlite3.DatabaseError:
            log.exception("close_ticket database error for channel %s", channel.id)
            try:
                await channel.send("Ticket database is currently unavailable. Please ask an admin to repair the database.")
            except Exception:
                pass
            return
        if not row:
            return

        guild = channel.guild
        logs = await self.get_logs_channel(guild)

        try:
            html = await create_html_transcript(channel)
            if logs:
                await logs.send(file=discord.File(io.BytesIO(html.encode()), f"{channel.name}.html"))
        except Exception:
            pass

        credited_closer = None
        if staff_closed:
            credited_closer = str(closed_by.id)
        elif row["close_requested_by"]:
            credited_closer = row["close_requested_by"]

        try:
            await self.bot.db.execute(
                "UPDATE tickets SET is_closed = 1, closed_by = ?, close_requested_at = NULL WHERE channel_id = ?;",
                (credited_closer, str(channel.id)),
            )
        except sqlite3.DatabaseError:
            log.exception("close_ticket update database error for channel %s", channel.id)
            try:
                await channel.send("Ticket database is currently unavailable. Please ask an admin to repair the database.")
            except Exception:
                pass
            return

        if staff_closed:
            await self.log_ticket_action(guild, f"Ticket {channel.mention} closed by {closed_by.mention}")
        elif row["close_requested_by"]:
            staff_id = int(row["close_requested_by"])
            try:
                staff_member = await guild.fetch_member(staff_id)
                await self.log_ticket_action(
                    guild,
                    f"Ticket {channel.mention} closed by {staff_member.mention} (owner confirmed)",
                )
            except Exception:
                await self.log_ticket_action(
                    guild,
                    f"Ticket {channel.mention} closed (owner confirmed after staff request)",
                )
        else:
            await self.log_ticket_action(guild, f"Ticket {channel.mention} closed by {closed_by.mention}")

        try:
            await channel.delete()
        except Exception:
            pass

        try:
            await self.refresh_panel_now(guild)
        except Exception:
            pass

    async def log_ticket_action(self, guild: discord.Guild, message: str):
        logs = await self.get_logs_channel(guild)
        if logs:
            try:
                embed = discord.Embed(
                    description=message,
                    color=EMBED_COLOR,
                    timestamp=utcnow(),
                )
                embed.set_footer(text=FOOTER_TEXT)
                await logs.send(embed=embed)
            except Exception:
                pass

    # ---- background loops ----

    @tasks.loop(minutes=5)
    async def inactivity_watcher(self):
        try:
            for guild in self.bot.guilds:
                if not await self.bot.is_guild_approved(guild.id):
                    continue
                hours = await self.get_setting(guild.id, "inactivity_hours", 24)
                cutoff = utcnow() - timedelta(hours=hours)
                rows = await self.bot.db.fetchall(
                    "SELECT channel_id FROM tickets WHERE guild_id = ? AND is_closed = 0;",
                    (str(guild.id),),
                )
                for row in rows:
                    channel = guild.get_channel(int(row["channel_id"]))
                    if channel and channel.last_message and channel.last_message.created_at < cutoff:
                        await self.close_ticket(channel, guild.me)
        except sqlite3.DatabaseError:
            log.exception("inactivity_watcher skipped due to database error")

    @inactivity_watcher.before_loop
    async def before_inactivity(self):
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=60)
    async def close_confirmation_watcher(self):
        try:
            cutoff = utcnow() - timedelta(hours=1)
            rows = await self.bot.db.fetchall(
                "SELECT * FROM tickets WHERE is_closed = 0 AND close_requested_by IS NOT NULL AND close_requested_at IS NOT NULL AND close_requested_at <= ?;",
                (cutoff.isoformat(),),
            )
            for row in rows:
                guild = self.bot.get_guild(int(row["guild_id"]))
                if not guild or not await self.bot.is_guild_approved(guild.id):
                    continue
                channel = guild.get_channel(int(row["channel_id"]))
                if isinstance(channel, discord.TextChannel):
                    await self.close_ticket(channel, guild.me)
        except sqlite3.DatabaseError:
            log.exception("close_confirmation_watcher skipped due to database error")

    @close_confirmation_watcher.before_loop
    async def before_close_watcher(self):
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=1)
    async def emoji_status_updater(self):
        try:
            rows = await self.bot.db.fetchall(
                "SELECT guild_id, channel_id, ticket_id, created_at FROM tickets WHERE is_closed = 0;",
                (),
            )
            for row in rows:
                try:
                    guild = self.bot.get_guild(int(row["guild_id"]))
                    if not guild or not await self.bot.is_guild_approved(guild.id):
                        continue
                    channel = guild.get_channel(int(row["channel_id"]))
                    if not isinstance(channel, discord.TextChannel):
                        continue

                    created_at = datetime.fromisoformat(row["created_at"])
                    new_emoji = get_ticket_status_emoji(created_at)

                    current_name = channel.name
                    if current_name.startswith(STATUS_EMOJIS):
                        current_emoji = current_name[0]
                        if current_emoji != new_emoji:
                            new_name = (new_emoji + current_name[1:])[:100]
                            try:
                                await channel.edit(name=new_name)
                            except Exception:
                                pass

                    async for msg in channel.history(limit=10, oldest_first=True):
                        if msg.embeds and msg.author.id == self.bot.user.id:
                            embed = msg.embeds[0]
                            if "Ticket #" in (embed.title or "") and not (embed.title or "").startswith(new_emoji):
                                title = (embed.title or "").lstrip("🟢🟠🔴 ")
                                embed.title = f"{new_emoji} {title}"
                                try:
                                    await msg.edit(embed=embed)
                                except Exception:
                                    pass
                            break
                except Exception:
                    pass
        except sqlite3.DatabaseError:
            log.exception("emoji_status_updater skipped due to database error")

    @emoji_status_updater.before_loop
    async def before_emoji_updater(self):
        await self.bot.wait_until_ready()

    # ---- slash commands ----

    tickets = app_commands.Group(name="ticket", description="Ticket management.", guild_only=True)

    async def staff_allowed_in_ticket(self, interaction: discord.Interaction) -> Tuple[bool, Optional[str]]:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return False, "Not in a guild"

        if interaction.user.guild_permissions.administrator:
            return True, None

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return False, "Not in a text channel"

        row = await self.bot.db.fetchone(
            "SELECT 1 FROM tickets WHERE channel_id = ?;",
            (str(channel.id),),
        )
        if not row:
            return False, "Not a ticket channel"

        if EU_STAFF_ROLE_ID and any(role.id == EU_STAFF_ROLE_ID for role in interaction.user.roles):
            return True, None
        try:
            rows = await self.bot.db.fetchall(
                "SELECT role_id FROM staff_roles WHERE guild_id = ?;",
                (str(interaction.guild.id),),
            )
            staff_role_ids = {int(r["role_id"]) for r in rows}
            if any(role.id in staff_role_ids for role in interaction.user.roles):
                return True, None
        except Exception:
            pass
        return False, "Staff only"

    @tickets.command(name="add", description="Add a user to the ticket")
    async def ticket_add(self, interaction: discord.Interaction, user: discord.User):
        allowed, error = await self.staff_allowed_in_ticket(interaction)
        if not allowed:
            return await interaction.response.send_message(error or "Staff only.", ephemeral=True)

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message("Not a ticket channel.", ephemeral=True)

        guild = interaction.guild
        if not guild:
            return

        member = guild.get_member(user.id)
        if not member:
            return await interaction.response.send_message("User not in guild.", ephemeral=True)

        try:
            await channel.set_permissions(member, view_channel=True, send_messages=True, read_messages=True)
            await interaction.response.send_message(f"Added {member.mention} to ticket.", ephemeral=True)
            await channel.send(f"{member.mention} has been added to the ticket.")
        except Exception as e:
            await interaction.response.send_message(f"Error adding user: {str(e)}", ephemeral=True)

    @tickets.command(name="remove", description="Remove a user from the ticket")
    async def ticket_remove(self, interaction: discord.Interaction, user: discord.User):
        allowed, error = await self.staff_allowed_in_ticket(interaction)
        if not allowed:
            return await interaction.response.send_message(error or "Staff only.", ephemeral=True)

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message("Not a ticket channel.", ephemeral=True)

        guild = interaction.guild
        if not guild:
            return

        member = guild.get_member(user.id)
        if not member:
            return await interaction.response.send_message("User not in guild.", ephemeral=True)

        try:
            await channel.set_permissions(member, overwrite=None)
            await interaction.response.send_message(f"Removed {member.mention} from ticket.", ephemeral=True)
            await channel.send(f"{member.mention} has been removed from the ticket.")
        except Exception as e:
            await interaction.response.send_message(f"Error removing user: {str(e)}", ephemeral=True)

    @tickets.command(name="close", description="Close the current ticket")
    async def ticket_close(self, interaction: discord.Interaction, reason: Optional[str] = None):
        _ = reason
        allowed, error = await self.staff_allowed_in_ticket(interaction)
        if not allowed:
            return await interaction.response.send_message(error or "Staff only.", ephemeral=True)

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message("Not a ticket channel.", ephemeral=True)

        await interaction.response.defer(thinking=True)
        await self.close_ticket(channel, interaction.user, staff_closed=True)

    @tickets.command(name="requestclose", description="Request the ticket owner to close the ticket")
    async def ticket_requestclose(self, interaction: discord.Interaction, reason: Optional[str] = None):
        _ = reason
        allowed, error = await self.staff_allowed_in_ticket(interaction)
        if not allowed:
            return await interaction.response.send_message(error or "Staff only.", ephemeral=True)

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message("Not a ticket channel.", ephemeral=True)

        await self.request_close(interaction, interaction.user)

    @tickets.command(name="rename", description="Rename the ticket channel")
    async def ticket_rename(self, interaction: discord.Interaction, new_name: str):
        allowed, error = await self.staff_allowed_in_ticket(interaction)
        if not allowed:
            return await interaction.response.send_message(error or "Staff only.", ephemeral=True)

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message("Not a ticket channel.", ephemeral=True)

        new_name = slugify_text(new_name)[:100]

        try:
            await channel.edit(name=new_name)
            await interaction.response.send_message(f"Ticket renamed to {new_name}.", ephemeral=True)
            await channel.send(f"Channel renamed to {new_name} by {interaction.user.mention}")
        except Exception as e:
            await interaction.response.send_message(f"Error renaming ticket: {str(e)}", ephemeral=True)

    @tickets.command(name="closers", description="Show how many tickets each staff member has closed")
    async def ticket_closers(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("Guild only.", ephemeral=True)

        rows = await self.bot.db.fetchall(
            "SELECT closed_by, COUNT(*) AS c FROM tickets WHERE guild_id = ? AND is_closed = 1 AND closed_by IS NOT NULL GROUP BY closed_by ORDER BY c DESC;",
            (str(interaction.guild.id),),
        )

        if not rows:
            return await interaction.response.send_message("No closed tickets with credited staff yet.", ephemeral=True)

        lines = []
        total_closed = 0
        for row in rows:
            staff_id = row["closed_by"]
            count = int(row["c"])
            total_closed += count
            try:
                lines.append(f"<@{int(staff_id)}> - {count}")
            except Exception:
                lines.append(f"`{staff_id}` - {count}")

        embed = discord.Embed(
            title="Ticket Closer Stats",
            description="\n".join(lines[:20]),
            color=EMBED_COLOR,
        )
        embed.set_footer(text=f"Total credited closes: {total_closed} | Unique closers: {len(rows)}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @tickets.command(name="purgeall", description="Owner only: purge all open tickets")
    async def ticket_purgeall(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("Guild only.", ephemeral=True)

        if not await self.owner_allowed(interaction):
            return await interaction.response.send_message("Owner only.", ephemeral=True)

        await interaction.response.defer(ephemeral=True, thinking=True)

        rows = await self.bot.db.fetchall(
            "SELECT channel_id FROM tickets WHERE guild_id = ? AND is_closed = 0;",
            (str(interaction.guild.id),),
        )

        if not rows:
            return await interaction.followup.send("No open tickets to purge.", ephemeral=True)

        closed_count = 0
        deleted_channels = 0

        for row in rows:
            channel_id = int(row["channel_id"])
            channel = interaction.guild.get_channel(channel_id)

            try:
                await self.bot.db.execute(
                    "UPDATE tickets SET is_closed = 1, closed_by = ?, close_requested_at = NULL WHERE guild_id = ? AND channel_id = ?;",
                    (str(interaction.user.id), str(interaction.guild.id), str(channel_id)),
                )
                closed_count += 1
            except sqlite3.DatabaseError:
                continue

            if isinstance(channel, discord.TextChannel):
                try:
                    await channel.delete(reason=f"Ticket purgeall by {interaction.user}")
                    deleted_channels += 1
                except Exception:
                    pass

        try:
            await self.refresh_panel_now(interaction.guild)
        except Exception:
            pass

        await interaction.followup.send(
            f"Purge complete: marked closed {closed_count}, deleted channels {deleted_channels}.",
            ephemeral=True,
        )

    @tickets.command(name="forcepanel", description="Owner only: force-post the ticket panel now")
    async def ticket_forcepanel(self, interaction: discord.Interaction):
        if not await self.owner_allowed(interaction):
            return await interaction.response.send_message("Owner only.", ephemeral=True)

        if not interaction.guild:
            return await interaction.response.send_message("Guild only.", ephemeral=True)

        await interaction.response.defer(ephemeral=True, thinking=True)

        panel_channel_id = await self.get_setting(interaction.guild.id, "panel_channel_id")
        if not panel_channel_id:
            return await interaction.followup.send(
                "❌ No panel channel configured. Run `/setup tickets` first.",
                ephemeral=True,
            )

        channel = interaction.guild.get_channel(int(panel_channel_id))
        if not isinstance(channel, discord.TextChannel):
            return await interaction.followup.send(
                f"❌ Panel channel `{panel_channel_id}` not found or is not a text channel.",
                ephemeral=True,
            )

        me = interaction.guild.me
        perms = channel.permissions_for(me)
        missing = []
        if not perms.view_channel:
            missing.append("View Channel")
        if not perms.send_messages:
            missing.append("Send Messages")
        if not perms.embed_links:
            missing.append("Embed Links")
        if missing:
            return await interaction.followup.send(
                f"❌ Bot is missing permissions in {channel.mention}: **{', '.join(missing)}**",
                ephemeral=True,
            )

        try:
            embed = await self.build_panel_embed(interaction.guild)
        except Exception as e:
            return await interaction.followup.send(
                f"❌ Failed to build panel embed: `{e}`",
                ephemeral=True,
            )

        try:
            await self.bot.db.execute(
                "DELETE FROM ticket_panel_state WHERE guild_id = ?;",
                (str(interaction.guild.id),),
            )
            msg = await channel.send(embed=embed, view=TicketPanelView())
            await self.bot.db.execute(
                "INSERT OR REPLACE INTO ticket_panel_state (guild_id, channel_id, message_id) VALUES (?, ?, ?);",
                (str(interaction.guild.id), str(channel.id), str(msg.id)),
            )
            await interaction.followup.send(
                f"✅ Panel posted in {channel.mention}.",
                ephemeral=True,
            )
        except discord.Forbidden:
            await interaction.followup.send(
                f"❌ Bot was denied permission to send in {channel.mention}.",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.followup.send(
                f"❌ Unexpected error: `{e}`",
                ephemeral=True,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(Tickets(bot))
