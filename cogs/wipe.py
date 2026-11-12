"""
wipe.py — Wipe Schedule Cog for Twix

Drop this file AND twix.png into your bot's cogs/ folder, then load with:
    await bot.load_extension("cogs.wipe")

Commands:
    !wipe        — shows the wipe schedule (everyone)
    /setwipe     — set the next wipe date & time (Administrator only)
"""

import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import json
import os

# ─── Data helpers ──────────────────────────────────────────────────────────────

DATA_FILE = os.path.join(os.path.dirname(__file__), "wipe_data.json")
TWIX_IMAGE = os.path.join(os.path.dirname(__file__), "twix.png")

DEFAULT_DATA = {
    "next_wipe": None,
    "last_wipe": None,
    "server_info": "TWIX 2X | 4 MAN MAX | EU | NO BPS | discord.gg/Pw2YEuRpD2",
    "reminder": "❤️ favourite the server before wipe!",
}


def load_data() -> dict:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return json.loads(json.dumps(DEFAULT_DATA))


def save_data(data: dict) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ─── Embed builder ─────────────────────────────────────────────────────────────

def build_wipe_embed(data: dict, has_image: bool) -> discord.Embed:
    now_ts = int(datetime.now(timezone.utc).timestamp())
    next_wipe: int | None = data.get("next_wipe")
    last_wipe: int | None = data.get("last_wipe")

    embed = discord.Embed(title="🗓️ Twix Wipe Schedule", color=0x5865F2)

    # ── Next wipe field ────────────────────────────────────────────────────────
    if next_wipe and next_wipe > now_ts:
        embed.add_field(
            name="🔵 Twix",
            value=f"<t:{next_wipe}:F>\n<t:{next_wipe}:R>",
            inline=False,
        )
    else:
        embed.add_field(
            name="🔵 Twix",
            value="No upcoming wipe scheduled.\nAn admin can set one with `/setwipe`.",
            inline=False,
        )

    # ── Server info ────────────────────────────────────────────────────────────
    embed.add_field(
        name="🎮 Server",
        value=f"`{data['server_info']}`",
        inline=False,
    )

    # ── Server information ─────────────────────────────────────────────────────
    embed.add_field(
        name="📋 Server Information",
        value=(
            "⚡ 2X Gather Rates\n"
            "🔨 Instant Crafting\n"
            "💨 Fast Respawn\n"
            "🎯 Automatic Events\n"
            "👥 100+ Players"
        ),
        inline=False,
    )

    # ── Reminder ──────────────────────────────────────────────────────────────
    embed.add_field(name="\u200b", value=data["reminder"], inline=False)

    # ── Latest wipe — ONLY shown after next_wipe has passed ───────────────────
    # Logic: if next_wipe exists and is in the past, show it as "Latest Wipe"
    # If next_wipe doesn't exist or is future, never show latest wipe
    if next_wipe and next_wipe <= now_ts:
        embed.add_field(
            name="⏮️ Latest Wipe",
            value=f"<t:{next_wipe}:F>",
            inline=False,
        )
    elif not next_wipe and last_wipe:
        # Only show last_wipe if a new next_wipe has already been set
        # (i.e. there's something upcoming to compare against)
        # — don't show it when there's nothing scheduled
        pass

    embed.set_footer(text="All times are displayed in your local timezone")

    # Large image at the bottom (bigger than thumbnail)
    if has_image:
        embed.set_image(url="attachment://twix.png")

    return embed


# ─── Choices ───────────────────────────────────────────────────────────────────

MONTH_CHOICES = [
    app_commands.Choice(name="January",   value=1),
    app_commands.Choice(name="February",  value=2),
    app_commands.Choice(name="March",     value=3),
    app_commands.Choice(name="April",     value=4),
    app_commands.Choice(name="May",       value=5),
    app_commands.Choice(name="June",      value=6),
    app_commands.Choice(name="July",      value=7),
    app_commands.Choice(name="August",    value=8),
    app_commands.Choice(name="September", value=9),
    app_commands.Choice(name="October",   value=10),
    app_commands.Choice(name="November",  value=11),
    app_commands.Choice(name="December",  value=12),
]

PERIOD_CHOICES = [
    app_commands.Choice(name="AM", value="AM"),
    app_commands.Choice(name="PM", value="PM"),
]

TIMEZONE_CHOICES = [
    app_commands.Choice(name="UTC",                      value="UTC"),
    app_commands.Choice(name="Europe/London (GMT/BST)",  value="Europe/London"),
    app_commands.Choice(name="Europe/Amsterdam (CET)",   value="Europe/Amsterdam"),
    app_commands.Choice(name="Europe/Paris (CET)",       value="Europe/Paris"),
    app_commands.Choice(name="Europe/Berlin (CET)",      value="Europe/Berlin"),
    app_commands.Choice(name="Europe/Madrid (CET)",      value="Europe/Madrid"),
    app_commands.Choice(name="Europe/Rome (CET)",        value="Europe/Rome"),
    app_commands.Choice(name="Europe/Stockholm (CET)",   value="Europe/Stockholm"),
    app_commands.Choice(name="Europe/Helsinki (EET)",    value="Europe/Helsinki"),
    app_commands.Choice(name="Europe/Athens (EET)",      value="Europe/Athens"),
    app_commands.Choice(name="America/New_York (ET)",    value="America/New_York"),
    app_commands.Choice(name="America/Chicago (CT)",     value="America/Chicago"),
    app_commands.Choice(name="America/Denver (MT)",      value="America/Denver"),
    app_commands.Choice(name="America/Los_Angeles (PT)", value="America/Los_Angeles"),
    app_commands.Choice(name="America/Toronto (ET)",     value="America/Toronto"),
    app_commands.Choice(name="Australia/Sydney (AEST)",  value="Australia/Sydney"),
    app_commands.Choice(name="Asia/Dubai (GST)",         value="Asia/Dubai"),
]


def to_24h(hour12: int, period: str) -> int:
    """Convert 12-hour + AM/PM to 24-hour."""
    if period == "AM":
        return 0 if hour12 == 12 else hour12
    else:  # PM
        return 12 if hour12 == 12 else hour12 + 12


# ─── Cog ───────────────────────────────────────────────────────────────────────

class Wipe(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── !wipe prefix command ───────────────────────────────────────────────────

    @commands.command(name="wipe", help="Show the Twix wipe schedule.")
    async def wipe_prefix(self, ctx: commands.Context):
        data = load_data()
        has_image = os.path.exists(TWIX_IMAGE)
        embed = build_wipe_embed(data, has_image)

        if has_image:
            file = discord.File(TWIX_IMAGE, filename="twix.png")
            await ctx.send(file=file, embed=embed)
        else:
            await ctx.send(embed=embed)

    # ── /setwipe slash command ─────────────────────────────────────────────────

    @app_commands.command(
        name="setwipe",
        description="Set the next wipe date and time (Administrator only).",
    )
    @app_commands.describe(
        day="Day of the month (1–31)",
        month="Month",
        year="Year e.g. 2026",
        hour="Hour (1–12)",
        minute="Minute (0–59)",
        period="AM or PM",
        tz="Timezone",
    )
    @app_commands.choices(month=MONTH_CHOICES, period=PERIOD_CHOICES, tz=TIMEZONE_CHOICES)
    @app_commands.default_permissions(administrator=True)
    async def setwipe(
        self,
        interaction: discord.Interaction,
        day: app_commands.Range[int, 1, 31],
        month: int,
        year: app_commands.Range[int, 2024, 2030],
        hour: app_commands.Range[int, 1, 12],
        minute: app_commands.Range[int, 0, 59],
        period: str,
        tz: str = "UTC",
    ):
        hour24 = to_24h(hour, period)
        zone = ZoneInfo(tz)

        try:
            dt = datetime(year, month, day, hour24, minute, tzinfo=zone)
        except ValueError as e:
            await interaction.response.send_message(f"❌ Invalid date: {e}", ephemeral=True)
            return

        ts = int(dt.timestamp())
        now_ts = int(datetime.now(ZoneInfo("UTC")).timestamp())

        # Reject dates already in the past
        if ts <= now_ts:
            month_name = next(c.name for c in MONTH_CHOICES if c.value == month)
            embed = discord.Embed(
                title="❌ That date is already in the past",
                description=(
                    f"You entered: **{day} {month_name} {year} "
                    f"{hour}:{minute:02d} {period} {tz}**\n\n"
                    "That time has already passed. Please set a **future** date and time."
                ),
                color=0xED4245,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        data = load_data()

        # Archive old next_wipe as last_wipe only if it has passed
        if data.get("next_wipe") and data["next_wipe"] <= now_ts:
            data["last_wipe"] = data["next_wipe"]

        data["next_wipe"] = ts
        save_data(data)

        month_name = next(c.name for c in MONTH_CHOICES if c.value == month)
        embed = discord.Embed(
            title="✅ Wipe Schedule Updated",
            description=(
                f"**Next wipe set to:**\n"
                f"<t:{ts}:F>\n"
                f"<t:{ts}:R>\n\n"
                f"*{day} {month_name} {year} · {hour}:{minute:02d} {period} · {tz}*"
            ),
            color=0x23A55A,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── Day autocomplete ───────────────────────────────────────────────────────

    @setwipe.autocomplete("day")
    async def day_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        days = list(range(1, 32))
        if current:
            days = [d for d in days if str(d).startswith(current)]
        return [app_commands.Choice(name=str(d), value=d) for d in days[:25]]

    # ── Minute autocomplete ────────────────────────────────────────────────────

    @setwipe.autocomplete("minute")
    async def minute_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        common = [0, 15, 30, 45]
        rest = [m for m in range(0, 60) if m not in common]
        all_mins = common + rest
        if current:
            all_mins = [m for m in all_mins if str(m).startswith(current)]
        return [
            app_commands.Choice(name=f":{m:02d}", value=m)
            for m in all_mins[:25]
        ]


async def setup(bot: commands.Bot):
    await bot.add_cog(Wipe(bot))
