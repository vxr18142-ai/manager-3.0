# cogs/polls.py
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config.branding import EMBED_COLOR, FOOTER_TEXT
from utils.checks import has_any_staff_role
from utils.approval import ApprovalView


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def fmt_rel(dt: datetime) -> str:
    return f"<t:{int(dt.timestamp())}:R>"


def fmt_ts(dt: datetime) -> str:
    return f"<t:{int(dt.timestamp())}:F>"


class PollVoteButton(discord.ui.Button):
    def __init__(self, choice_index: int, label: str):
        super().__init__(
            label=label[:80],
            style=discord.ButtonStyle.primary,
            custom_id=f"poll:vote:{choice_index}",
        )
        self.choice_index = choice_index

    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild or not interaction.message:
            return

        guild = interaction.guild
        msg_id = str(interaction.message.id)

        row = await interaction.client.db.fetchone(
            "SELECT * FROM polls WHERE guild_id = ? AND message_id = ?;",
            (str(guild.id), msg_id),
        )
        if not row:
            return await interaction.response.send_message("Poll not found.", ephemeral=True)

        if int(row["ended"]) == 1:
            return await interaction.response.send_message("This poll has ended.", ephemeral=True)

        existing = await interaction.client.db.fetchone(
            "SELECT 1 FROM poll_votes WHERE guild_id = ? AND message_id = ? AND user_id = ?;",
            (str(guild.id), msg_id, str(interaction.user.id)),
        )
        if existing:
            await interaction.client.db.execute(
                "DELETE FROM poll_votes WHERE guild_id = ? AND message_id = ? AND user_id = ?;",
                (str(guild.id), msg_id, str(interaction.user.id)),
            )
            await interaction.response.send_message("Vote updated.", ephemeral=True)
        else:
            await interaction.response.send_message("Vote recorded.", ephemeral=True)

        await interaction.client.db.execute(
            "INSERT INTO poll_votes (guild_id, message_id, user_id, choice_index) VALUES (?, ?, ?, ?);",
            (str(guild.id), msg_id, str(interaction.user.id), self.choice_index),
        )

        embed, view = await build_poll_embed_and_view(
            interaction.client.db,
            guild,
            row,
            show_results=True,
        )
        try:
            await interaction.message.edit(embed=embed, view=view)
        except Exception:
            pass


class PollView(ApprovalView):
    def __init__(self, choices: List[str]):
        super().__init__(timeout=None)
        for idx, choice in enumerate(choices[:10]):
            self.add_item(PollVoteButton(idx, choice))


async def build_poll_embed_and_view(
    db,
    guild: discord.Guild,
    poll_row,
    show_results: bool = False,
) -> tuple[discord.Embed, discord.ui.View]:
    msg_id = poll_row["message_id"]
    choices = json.loads(poll_row["choices"])
    title = poll_row["title"]
    host_id = int(poll_row["host_id"])
    ends_at = datetime.fromisoformat(poll_row["ends_at"])
    ended = bool(int(poll_row["ended"]))

    embed = discord.Embed(
        title=f"Poll: {title}",
        color=EMBED_COLOR,
    )

    votes_rows = await db.fetchall(
        "SELECT choice_index FROM poll_votes WHERE guild_id = ? AND message_id = ?;",
        (str(guild.id), msg_id),
    )
    vote_counts: Dict[int, int] = {i: 0 for i in range(len(choices))}
    for vrow in votes_rows:
        idx = int(vrow["choice_index"])
        if idx in vote_counts:
            vote_counts[idx] += 1

    total_votes = sum(vote_counts.values())

    if show_results or ended:
        embed.add_field(name="Status", value="Ended" if ended else f"Ends {fmt_rel(ends_at)}", inline=False)
        embed.add_field(name="Total Votes", value=str(total_votes), inline=False)

        results = []
        for idx, choice in enumerate(choices[:10]):
            count = vote_counts.get(idx, 0)
            pct = (count / total_votes * 100) if total_votes > 0 else 0
            bar = build_bar(pct)
            results.append(f"**{choice}** {count} ({pct:.1f}%)\n{bar}")

        embed.add_field(name="Results", value="\n\n".join(results), inline=False)
    else:
        embed.add_field(name="Ends", value=f"{fmt_rel(ends_at)} ({fmt_ts(ends_at)})", inline=False)
        embed.add_field(name="Host", value=f"<@{host_id}>", inline=True)
        embed.add_field(name="Votes", value=str(total_votes), inline=True)

    embed.set_footer(text=FOOTER_TEXT)
    view = PollView(choices)
    return embed, view


def build_bar(percentage: float, width: int = 20) -> str:
    filled = int(percentage / 100 * width)
    empty = width - filled
    bar = "█" * filled + "░" * empty
    return f"`{bar}`"


class Polls(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bot.add_view(PollView([]))
        self.poll_ender.start()

    poll = app_commands.Group(name="poll", description="Create and manage polls.", guild_only=True)

    def can_manage(self, member: discord.Member) -> bool:
        return member.guild_permissions.administrator or has_any_staff_role(member)

    @poll.command(name="create", description="Create a poll for people to vote on.")
    @app_commands.describe(
        title="Poll title/question.",
        choice1="First choice.",
        choice2="Second choice.",
        choice3="Third choice (optional).",
        choice4="Fourth choice (optional).",
        choice5="Fifth choice (optional).",
        choice6="Sixth choice (optional).",
        choice7="Seventh choice (optional).",
        choice8="Eighth choice (optional).",
        choice9="Ninth choice (optional).",
        choice10="Tenth choice (optional).",
        duration="How long until poll closes (minutes).",
        channel="Channel to post poll in (default: current).",
    )
    async def poll_create(
        self,
        interaction: discord.Interaction,
        title: str,
        choice1: str,
        choice2: str,
        choice3: Optional[str] = None,
        choice4: Optional[str] = None,
        choice5: Optional[str] = None,
        choice6: Optional[str] = None,
        choice7: Optional[str] = None,
        choice8: Optional[str] = None,
        choice9: Optional[str] = None,
        choice10: Optional[str] = None,
        duration: app_commands.Range[int, 1, 1440] = 60,
        channel: Optional[discord.TextChannel] = None,
    ):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return

        if not self.can_manage(interaction.user):
            return await interaction.response.send_message("Staff only.", ephemeral=True)

        choices = [choice1.strip(), choice2.strip()]
        for c in [choice3, choice4, choice5, choice6, choice7, choice8, choice9, choice10]:
            if c and c.strip():
                choices.append(c.strip())

        if len(choices) < 2:
            return await interaction.response.send_message("At least 2 choices required.", ephemeral=True)

        if len(choices) > 10:
            choices = choices[:10]

        post_channel = channel or interaction.channel
        if not isinstance(post_channel, discord.TextChannel):
            return await interaction.response.send_message("Invalid channel.", ephemeral=True)

        ends_at = utcnow() + timedelta(minutes=int(duration))

        embed = discord.Embed(
            title=f"Poll: {title}",
            description=f"Click a button to vote. Poll ends {fmt_rel(ends_at)}.",
            color=EMBED_COLOR,
        )
        embed.add_field(name="Host", value=f"<@{interaction.user.id}>", inline=True)
        embed.add_field(name="Votes", value="0", inline=True)
        embed.set_footer(text=FOOTER_TEXT)

        view = PollView(choices)
        msg = await post_channel.send(embed=embed, view=view)

        await self.bot.db.execute(
            "INSERT INTO polls (guild_id, message_id, channel_id, host_id, title, choices, ends_at, ended) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0);",
            (
                str(interaction.guild.id),
                str(msg.id),
                str(post_channel.id),
                str(interaction.user.id),
                title.strip(),
                json.dumps(choices),
                ends_at.isoformat(),
            ),
        )

        await interaction.response.send_message(
            f"Poll created: {msg.jump_url}",
            ephemeral=True,
        )

    @poll.command(name="end", description="End a poll immediately and show results.")
    @app_commands.describe(message_link="Paste the poll message link.")
    async def poll_end(self, interaction: discord.Interaction, message_link: str):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return

        if not self.can_manage(interaction.user):
            return await interaction.response.send_message("Staff only.", ephemeral=True)

        try:
            parts = message_link.strip().split("/")
            msg_id = int(parts[-1])
            ch_id = int(parts[-2])
        except Exception:
            return await interaction.response.send_message("Invalid message link.", ephemeral=True)

        row = await self.bot.db.fetchone(
            "SELECT * FROM polls WHERE guild_id = ? AND message_id = ?;",
            (str(interaction.guild.id), str(msg_id)),
        )
        if not row:
            return await interaction.response.send_message("Poll not found.", ephemeral=True)

        await self.bot.db.execute(
            "UPDATE polls SET ended = 1 WHERE guild_id = ? AND message_id = ?;",
            (str(interaction.guild.id), str(msg_id)),
        )

        channel = interaction.guild.get_channel(int(ch_id))
        if isinstance(channel, discord.TextChannel):
            try:
                msg = await channel.fetch_message(msg_id)
                embed, view = await build_poll_embed_and_view(
                    self.bot.db,
                    interaction.guild,
                    row,
                    show_results=True,
                )
                await msg.edit(embed=embed, view=view)
            except Exception:
                pass

        await interaction.response.send_message("Poll ended.", ephemeral=True)

    @tasks.loop(seconds=30)
    async def poll_ender(self):
        now_iso = utcnow().isoformat()
        rows = await self.bot.db.fetchall(
            "SELECT * FROM polls WHERE ended = 0 AND ends_at <= ?;",
            (now_iso,),
        )

        for row in rows:
            guild = self.bot.get_guild(int(row["guild_id"]))
            if not guild or not await self.bot.is_guild_approved(guild.id):
                continue

            channel = guild.get_channel(int(row["channel_id"]))
            if not isinstance(channel, discord.TextChannel):
                await self.bot.db.execute(
                    "UPDATE polls SET ended = 1 WHERE guild_id = ? AND message_id = ?;",
                    (row["guild_id"], row["message_id"]),
                )
                continue

            await self.bot.db.execute(
                "UPDATE polls SET ended = 1 WHERE guild_id = ? AND message_id = ?;",
                (row["guild_id"], row["message_id"]),
            )

            try:
                msg = await channel.fetch_message(int(row["message_id"]))
                embed, view = await build_poll_embed_and_view(
                    self.bot.db,
                    guild,
                    row,
                    show_results=True,
                )
                await msg.edit(embed=embed, view=view)
            except Exception:
                pass

    @poll_ender.before_loop
    async def before_poll_ender(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(Polls(bot))
