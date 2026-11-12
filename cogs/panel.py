from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from config.branding import EMBED_COLOR, FOOTER_TEXT
from config.tickets import TICKET_TYPES
from utils.approval import ApprovalView


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
        cog = interaction.client.get_cog("Tickets")
        if not cog:
            return await interaction.response.send_message(
                "Ticket system is currently unavailable.", ephemeral=True
            )
        from cogs.tickets import TicketQuestionsModal
        await interaction.response.send_modal(TicketQuestionsModal(self.values[0]))


class TicketPanelView(ApprovalView):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketTypeSelect())


class Panel(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        self.bot.add_view(TicketPanelView())

    panel = app_commands.Group(
        name="panel",
        description="Ticket panel management. Admin only.",
        guild_only=True,
    )

    @panel.command(name="post", description="Post the ticket panel in a channel.")
    @app_commands.describe(channel="Channel to post the panel in (defaults to current channel)")
    async def panel_post(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ):
        if not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)

        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            return await interaction.response.send_message("Please specify a text channel.", ephemeral=True)

        me = interaction.guild.me
        perms = target.permissions_for(me)
        missing = [p for p, ok in [("View Channel", perms.view_channel), ("Send Messages", perms.send_messages), ("Embed Links", perms.embed_links)] if not ok]
        if missing:
            return await interaction.response.send_message(
                f"❌ Missing permissions in {target.mention}: **{', '.join(missing)}**", ephemeral=True
            )

        await interaction.response.defer(ephemeral=True, thinking=True)

        open_tickets = 0
        try:
            row = await self.bot.db.fetchone(
                "SELECT COUNT(*) AS c FROM tickets WHERE guild_id = ? AND is_closed = 0;",
                (str(interaction.guild.id),),
            )
            open_tickets = int(row["c"]) if row else 0
        except Exception:
            pass

        response_speed = "High" if open_tickets >= 20 else "Fast"
        eta_minutes = min(55, 12 + (open_tickets * 2))
        eta = f"{eta_minutes} mins" if eta_minutes < 60 else f"{eta_minutes // 60}h {eta_minutes % 60}m"

        embed = discord.Embed(
            title="Support Tickets",
            description="Select a support option below to open a ticket.",
            color=EMBED_COLOR,
        )
        embed.add_field(
            name="Support Status",
            value=(
                f"**Open Tickets:** {open_tickets}\n"
                f"**Response Speed:** {response_speed}\n"
                f"**Estimated Help Time:** {eta}"
            ),
            inline=False,
        )
        embed.set_footer(text=FOOTER_TEXT)

        msg = await target.send(embed=embed, view=TicketPanelView())

        try:
            await self.bot.db.execute(
                "INSERT OR REPLACE INTO ticket_panel_state (guild_id, channel_id, message_id) VALUES (?, ?, ?);",
                (str(interaction.guild.id), str(target.id), str(msg.id)),
            )
        except Exception:
            pass

        await interaction.followup.send(f"✅ Panel posted in {target.mention}.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Panel(bot))
