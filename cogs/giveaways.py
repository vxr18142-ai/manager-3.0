# cogs/giveaways.py
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from typing import Optional, List

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config.branding import EMBED_COLOR, FOOTER_TEXT
from utils.checks import has_any_staff_role
from utils.approval import ApprovalView


# --------------------
# Helpers
# --------------------

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def fmt_ts(dt: datetime) -> str:
    return f"<t:{int(dt.timestamp())}:F>"


def fmt_rel(dt: datetime) -> str:
    return f"<t:{int(dt.timestamp())}:R>"


def setting_key(name: str) -> str:
    return f"giveaways.{name}"


def parse_message_link(link: str) -> Optional[tuple[int, int]]:
    try:
        parts = link.strip().split("/")
        msg_id = int(parts[-1])
        ch_id = int(parts[-2])
        return ch_id, msg_id
    except Exception:
        return None


def build_giveaway_embed(
    *,
    prize: str,
    host_id: int,
    ends_at: datetime,
    entries: int,
    winners: int,
    required_role_id: Optional[int],
    min_days: int,
    ended: bool = False,
) -> discord.Embed:
    title = prize.strip() if prize.strip() else "Giveaway"

    embed = discord.Embed(title=title, color=EMBED_COLOR)

    if ended:
        embed.add_field(name="Ends", value="✅ Ended", inline=False)
    else:
        embed.add_field(name="Ends", value=f"{fmt_rel(ends_at)} ({fmt_ts(ends_at)})", inline=False)

    embed.add_field(name="Hosted by", value=f"<@{host_id}>", inline=True)
    embed.add_field(name="Entries", value=str(entries), inline=True)
    embed.add_field(name="Winners", value=str(winners), inline=True)

    embed.set_footer(text=FOOTER_TEXT)
    return embed


# --------------------
# UI
# --------------------

class ConfirmLeaveGiveaway(ApprovalView):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="Yes", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not interaction.message:
            return
        guild = interaction.guild
        msg_id = str(interaction.message.id)

        await interaction.client.db.execute(
            "DELETE FROM giveaway_entries WHERE guild_id = ? AND message_id = ? AND user_id = ?;",
            (str(guild.id), msg_id, str(interaction.user.id)),
        )

        count_row = await interaction.client.db.fetchone(
            "SELECT COUNT(*) AS c FROM giveaway_entries WHERE guild_id = ? AND message_id = ?;",
            (str(guild.id), msg_id),
        )
        entries = int(count_row["c"] if count_row else 0)

        row = await interaction.client.db.fetchone(
            "SELECT * FROM giveaways WHERE guild_id = ? AND message_id = ?;",
            (str(guild.id), msg_id),
        )
        if row:
            ends_at = datetime.fromisoformat(row["ends_at"])
            embed = build_giveaway_embed(
                prize=row["prize"],
                host_id=int(row["host_id"]),
                ends_at=ends_at,
                entries=entries,
                winners=int(row["winner_count"]),
                required_role_id=int(row["required_role_id"]) if row["required_role_id"] else None,
                min_days=int(row["min_account_age_days"] or 7),
                ended=bool(int(row["ended"])),
            )
            try:
                await interaction.message.edit(embed=embed, view=GiveawayView())
            except Exception:
                pass

        await interaction.response.send_message("Left giveaway.", ephemeral=True)

    @discord.ui.button(label="No", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Cancelled.", ephemeral=True)


class GiveawayEnterButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Enter Giveaway",
            style=discord.ButtonStyle.primary,
            custom_id="giveaway:enter",
        )

    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild or not interaction.message:
            return
        if not isinstance(interaction.user, discord.Member):
            return

        guild = interaction.guild
        msg_id = str(interaction.message.id)

        row = await interaction.client.db.fetchone(
            "SELECT * FROM giveaways WHERE guild_id = ? AND message_id = ?;",
            (str(guild.id), msg_id),
        )
        if not row:
            return await interaction.response.send_message("This giveaway is not tracked.", ephemeral=True)

        if int(row["ended"]) == 1:
            return await interaction.response.send_message("This giveaway has ended.", ephemeral=True)

        existing = await interaction.client.db.fetchone(
            "SELECT 1 FROM giveaway_entries WHERE guild_id = ? AND message_id = ? AND user_id = ?;",
            (str(guild.id), msg_id, str(interaction.user.id)),
        )
        if existing:
            return await interaction.response.send_message(
                "Do you want to leave this giveaway?",
                view=ConfirmLeaveGiveaway(),
                ephemeral=True,
            )

        bl = await interaction.client.db.fetchone(
            "SELECT reason FROM giveaway_blacklist WHERE guild_id = ? AND user_id = ?;",
            (str(guild.id), str(interaction.user.id)),
        )
        if bl:
            return await interaction.response.send_message(
                f"You are not eligible to enter.\nReason: {bl['reason'] or 'No reason provided.'}",
                ephemeral=True,
            )

        req_role_id = row["required_role_id"]
        if req_role_id:
            role = guild.get_role(int(req_role_id))
            if role and role not in interaction.user.roles:
                return await interaction.response.send_message(
                    f"You must have {role.mention} to enter this giveaway.",
                    ephemeral=True,
                )

        min_days = int(row["min_account_age_days"] or 7)
        if (utcnow() - interaction.user.created_at) < timedelta(days=min_days):
            return await interaction.response.send_message(
                f"Your account must be at least **{min_days} days** old.",
                ephemeral=True,
            )

        await interaction.client.db.execute(
            "INSERT INTO giveaway_entries (guild_id, message_id, user_id) VALUES (?, ?, ?);",
            (str(guild.id), msg_id, str(interaction.user.id)),
        )

        count_row = await interaction.client.db.fetchone(
            "SELECT COUNT(*) AS c FROM giveaway_entries WHERE guild_id = ? AND message_id = ?;",
            (str(guild.id), msg_id),
        )
        entries = int(count_row["c"] if count_row else 0)

        ends_at = datetime.fromisoformat(row["ends_at"])

        embed = build_giveaway_embed(
            prize=row["prize"],
            host_id=int(row["host_id"]),
            ends_at=ends_at,
            entries=entries,
            winners=int(row["winner_count"]),
            required_role_id=int(row["required_role_id"]) if row["required_role_id"] else None,
            min_days=int(row["min_account_age_days"] or 7),
            ended=False,
        )

        try:
            await interaction.message.edit(embed=embed, view=GiveawayView())
        except Exception:
            pass

        return await interaction.response.send_message("Entered!", ephemeral=True)


class GiveawayView(ApprovalView):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(GiveawayEnterButton())


# --------------------
# Cog
# --------------------

DURATION_CHOICES = [
    ("10 minutes", 600),
    ("30 minutes", 1800),
    ("1 hour", 3600),
    ("2 hours", 7200),
    ("6 hours", 21600),
    ("12 hours", 43200),
    ("1 day", 86400),
    ("3 days", 259200),
    ("7 days", 604800),
]
DURATION_CHOICES_APP = [app_commands.Choice(name=n, value=v) for n, v in DURATION_CHOICES]


class Giveaways(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bot.add_view(GiveawayView())
        self.ensure_schema_task.start()
        self.end_watcher.start()

    giveaway = app_commands.Group(name="giveaway", description="Giveaway system.", guild_only=True)

    # --------------------
    # Settings helpers
    # --------------------

    async def get_setting(self, guild_id: int, name: str, default=None):
        row = await self.bot.db.fetchone(
            "SELECT value FROM guild_settings WHERE guild_id = ? AND key = ?;",
            (str(guild_id), setting_key(name)),
        )
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except Exception:
            return row["value"]

    async def log_to_channel(self, guild: discord.Guild, text: str) -> None:
        log_channel_id = await self.get_setting(guild.id, "log_channel_id", None)
        if not log_channel_id:
            return
        ch = guild.get_channel(int(log_channel_id))
        if isinstance(ch, discord.TextChannel):
            try:
                await ch.send(text)
            except Exception:
                pass

    # --------------------
    # Permissions
    # --------------------

    async def can_manage(self, guild: discord.Guild, member: discord.Member) -> bool:
        if member.guild_permissions.administrator:
            return True
        return has_any_staff_role(member)

    # --------------------
    # DB schema
    # --------------------

    @tasks.loop(count=1)
    async def ensure_schema_task(self):
        await self.bot.db.execute(
            """
            CREATE TABLE IF NOT EXISTS giveaways (
                guild_id TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                host_id TEXT NOT NULL,
                server_key TEXT NOT NULL DEFAULT '',
                prize TEXT NOT NULL,
                winner_count INTEGER NOT NULL,
                required_role_id TEXT,
                min_account_age_days INTEGER NOT NULL DEFAULT 7,
                ends_at TEXT NOT NULL,
                ended INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, message_id)
            );
            """
        )
        await self.bot.db.execute(
            """
            CREATE TABLE IF NOT EXISTS giveaway_entries (
                guild_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                PRIMARY KEY (guild_id, message_id, user_id)
            );
            """
        )
        await self.bot.db.execute(
            """
            CREATE TABLE IF NOT EXISTS giveaway_blacklist (
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                reason TEXT,
                added_by TEXT,
                added_at TEXT,
                PRIMARY KEY (guild_id, user_id)
            );
            """
        )

    @ensure_schema_task.before_loop
    async def _before_schema(self):
        await self.bot.wait_until_ready()

    # --------------------
    # Commands
    # --------------------

    @giveaway.command(name="create", description="Create a giveaway (auto picks winners at end).")
    @app_commands.describe(
        channel="Where to post the giveaway.",
        prize="Prize name (shows in title).",
        winners="How many winners (1-10).",
        duration="How long until it ends.",
        required_role="Optional role required to enter.",
        min_account_age_days="Minimum account age (days). Default: from /setup (or 7)",
    )
    @app_commands.choices(duration=DURATION_CHOICES_APP)
    async def giveaway_create(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        prize: str,
        winners: app_commands.Range[int, 1, 10],
        duration: app_commands.Choice[int],
        required_role: Optional[discord.Role] = None,
        min_account_age_days: Optional[app_commands.Range[int, 0, 365]] = None,
    ):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return

        if not await self.can_manage(interaction.guild, interaction.user):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

        default_days = await self.get_setting(interaction.guild.id, "default_min_account_age_days", 7)
        min_days = int(min_account_age_days if min_account_age_days is not None else int(default_days or 7))

        ends_at = utcnow() + timedelta(seconds=int(duration.value))

        embed = build_giveaway_embed(
            prize=prize,
            host_id=interaction.user.id,
            ends_at=ends_at,
            entries=0,
            winners=int(winners),
            required_role_id=required_role.id if required_role else None,
            min_days=min_days,
            ended=False,
        )

        msg = await channel.send(embed=embed, view=GiveawayView())

        await self.bot.db.execute(
            "INSERT OR REPLACE INTO giveaways "
            "(guild_id, channel_id, message_id, host_id, server_key, prize, winner_count, required_role_id, min_account_age_days, ends_at, ended) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0);",
            (
                str(interaction.guild.id),
                str(channel.id),
                str(msg.id),
                str(interaction.user.id),
                "",
                prize,
                int(winners),
                str(required_role.id) if required_role else None,
                min_days,
                ends_at.isoformat(),
            ),
        )

        await self.log_to_channel(
            interaction.guild,
            f"✅ Giveaway created by {interaction.user.mention} | Prize: **{prize}** | Winners: **{int(winners)}** | Ends: {fmt_ts(ends_at)}",
        )

        await interaction.response.send_message(f"✅ Giveaway posted: {msg.jump_url}", ephemeral=True)

    @giveaway.command(name="reroll", description="Reroll winners (ended giveaways only).")
    @app_commands.describe(message_link="Paste the giveaway message link.", count="How many winners (1-10).")
    async def giveaway_reroll(self, interaction: discord.Interaction, message_link: str, count: app_commands.Range[int, 1, 10]):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return

        parsed = parse_message_link(message_link)
        if not parsed:
            return await interaction.response.send_message("❌ Invalid message link.", ephemeral=True)

        ch_id, msg_id = parsed

        row = await self.bot.db.fetchone(
            "SELECT * FROM giveaways WHERE guild_id = ? AND message_id = ?;",
            (str(interaction.guild.id), str(msg_id)),
        )
        if not row:
            return await interaction.response.send_message("❌ Giveaway not found.", ephemeral=True)

        if not await self.can_manage(interaction.guild, interaction.user):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

        if int(row["ended"]) != 1:
            return await interaction.response.send_message("❌ Giveaway hasn't ended yet.", ephemeral=True)

        winners_list = await self.pick_winners(interaction.guild, str(msg_id), int(count))
        if not winners_list:
            return await interaction.response.send_message("❌ Not enough entries.", ephemeral=True)

        channel = interaction.guild.get_channel(int(ch_id))
        mentions = ", ".join(f"<@{u}>" for u in winners_list)

        if isinstance(channel, discord.TextChannel):
            await channel.send(f"🔁 **Reroll Winners:** {mentions}\n🎁 **Prize:** {row['prize']}")

        await self.log_to_channel(
            interaction.guild,
            f"🔁 Reroll by {interaction.user.mention} | Prize: **{row['prize']}** | Winners: {mentions}",
        )

        await interaction.response.send_message("✅ Rerolled.", ephemeral=True)

    # --------------------
    # Blacklist commands
    # --------------------

    @giveaway.command(name="blacklist_add", description="Blacklist a user from entering giveaways.")
    @app_commands.describe(user="User to blacklist", reason="Reason (optional)")
    async def giveaway_blacklist_add(self, interaction: discord.Interaction, user: discord.Member, reason: Optional[str] = None):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)

        await self.bot.db.execute(
            "INSERT OR REPLACE INTO giveaway_blacklist (guild_id, user_id, reason, added_by, added_at) VALUES (?, ?, ?, ?, ?);",
            (str(interaction.guild.id), str(user.id), (reason or "").strip(), str(interaction.user.id), utcnow().isoformat()),
        )
        await interaction.response.send_message(f"✅ Blacklisted {user.mention} from giveaways.", ephemeral=True)

    @giveaway.command(name="blacklist_remove", description="Remove a user from giveaway blacklist.")
    @app_commands.describe(user="User to unblacklist")
    async def giveaway_blacklist_remove(self, interaction: discord.Interaction, user: discord.Member):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)

        await self.bot.db.execute(
            "DELETE FROM giveaway_blacklist WHERE guild_id = ? AND user_id = ?;",
            (str(interaction.guild.id), str(user.id)),
        )
        await interaction.response.send_message(f"✅ Removed {user.mention} from giveaway blacklist.", ephemeral=True)

    @giveaway.command(name="blacklist_list", description="List giveaway blacklisted users.")
    async def giveaway_blacklist_list(self, interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)

        rows = await self.bot.db.fetchall(
            "SELECT user_id, reason, added_at FROM giveaway_blacklist WHERE guild_id = ? ORDER BY added_at DESC;",
            (str(interaction.guild.id),),
        )
        if not rows:
            return await interaction.response.send_message("✅ Giveaway blacklist is empty.", ephemeral=True)

        lines = []
        for r in rows[:25]:
            uid = int(r["user_id"])
            reason = r["reason"] or "—"
            lines.append(f"<@{uid}> — {reason}")

        embed = discord.Embed(title="🚫 Giveaway Blacklist", description="\n".join(lines), color=EMBED_COLOR)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # --------------------
    # Auto end + winners
    # --------------------

    async def pick_winners(self, guild: discord.Guild, msg_id: str, count: int) -> Optional[List[int]]:
        entries = await self.bot.db.fetchall(
            "SELECT user_id FROM giveaway_entries WHERE guild_id = ? AND message_id = ?;",
            (str(guild.id), msg_id),
        )
        users = [int(r["user_id"]) for r in entries]
        if len(users) < 1:
            return None
        k = min(int(count), len(users))
        return random.sample(users, k=k)

    @tasks.loop(seconds=15)
    async def end_watcher(self):
        now_iso = utcnow().isoformat()
        rows = await self.bot.db.fetchall(
            "SELECT * FROM giveaways WHERE ended = 0 AND ends_at <= ?;",
            (now_iso,),
        )

        for row in rows:
            guild = self.bot.get_guild(int(row["guild_id"]))
            if not guild or not await self.bot.is_guild_approved(guild.id):
                continue

            channel = guild.get_channel(int(row["channel_id"]))
            if not isinstance(channel, discord.TextChannel):
                await self.bot.db.execute(
                    "UPDATE giveaways SET ended = 1 WHERE guild_id = ? AND message_id = ?;",
                    (row["guild_id"], row["message_id"]),
                )
                continue

            try:
                msg = await channel.fetch_message(int(row["message_id"]))
            except Exception:
                await self.bot.db.execute(
                    "UPDATE giveaways SET ended = 1 WHERE guild_id = ? AND message_id = ?;",
                    (row["guild_id"], row["message_id"]),
                )
                continue

            await self.bot.db.execute(
                "UPDATE giveaways SET ended = 1 WHERE guild_id = ? AND message_id = ?;",
                (row["guild_id"], row["message_id"]),
            )

            count_row = await self.bot.db.fetchone(
                "SELECT COUNT(*) AS c FROM giveaway_entries WHERE guild_id = ? AND message_id = ?;",
                (row["guild_id"], row["message_id"]),
            )
            entries = int(count_row["c"] if count_row else 0)

            ends_at = datetime.fromisoformat(row["ends_at"])
            embed = build_giveaway_embed(
                prize=row["prize"],
                host_id=int(row["host_id"]),
                ends_at=ends_at,
                entries=entries,
                winners=int(row["winner_count"]),
                required_role_id=int(row["required_role_id"]) if row["required_role_id"] else None,
                min_days=int(row["min_account_age_days"] or 7),
                ended=True,
            )
            try:
                await msg.edit(embed=embed, view=GiveawayView())
            except Exception:
                pass

            winners_list = await self.pick_winners(guild, row["message_id"], int(row["winner_count"]))
            if not winners_list:
                await channel.send(f"⏳ Giveaway ended with **no valid entries**.\n🎁 **Prize:** {row['prize']}")
                await self.log_to_channel(guild, f"⚠️ Giveaway ended (no entries) | Prize: **{row['prize']}**")
                continue

            mentions = ", ".join(f"<@{u}>" for u in winners_list)
            await channel.send(f"🏆 **Winners:** {mentions}\n🎁 **Prize:** {row['prize']}")

            await self.log_to_channel(
                guild,
                f"🏆 Giveaway ended | Prize: **{row['prize']}** | Winners: {mentions}",
            )

    @end_watcher.before_loop
    async def _before_watcher(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(Giveaways(bot))
