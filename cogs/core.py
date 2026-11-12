# cogs/core.py
from __future__ import annotations

import json
import os
import asyncio
from typing import Any, Dict, Optional

import discord
from discord import app_commands
from discord.ext import commands

from config.branding import EMBED_COLOR


def _json_dumps(v: Any) -> str:
    return json.dumps(v, separators=(",", ":"), ensure_ascii=False)


def _json_loads(s: str) -> Any:
    return json.loads(s)


def key_for(module: str, name: str) -> str:
    return f"{module}.{name}"


class Core(commands.Cog):
    """Core: settings table + unified /setup command."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._defaults_loaded_once = False
        self._defaults_lock = asyncio.Lock()

    async def ensure_schema(self) -> None:
        await self.bot.db.execute(
            """
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                PRIMARY KEY (guild_id, key)
            );
            """
        )

    async def set_setting(self, guild_id: int, key: str, value: Any) -> None:
        await self.bot.db.execute(
            "INSERT OR REPLACE INTO guild_settings (guild_id, key, value) VALUES (?, ?, ?);",
            (str(guild_id), key, _json_dumps(value)),
        )

    async def get_setting(self, guild_id: int, key: str, default: Any = None) -> Any:
        row = await self.bot.db.fetchone(
            "SELECT value FROM guild_settings WHERE guild_id = ? AND key = ?;",
            (str(guild_id), key),
        )
        if not row:
            return default
        try:
            return _json_loads(row["value"])
        except Exception:
            return default

    async def get_all_settings(self, guild_id: int) -> Dict[str, Any]:
        rows = await self.bot.db.fetchall(
            "SELECT key, value FROM guild_settings WHERE guild_id = ? ORDER BY key ASC;",
            (str(guild_id),),
        )
        out: Dict[str, Any] = {}
        for r in rows or []:
            k = r["key"]
            try:
                out[k] = _json_loads(r["value"])
            except Exception:
                out[k] = r["value"]
        return out

    async def _load_defaults_if_needed(self) -> None:
        defaults_path = os.path.join(os.path.dirname(__file__), "..", "config", "setup_defaults.json")
        if not os.path.exists(defaults_path):
            return

        try:
            with open(defaults_path, "r", encoding="utf-8") as f:
                defaults = json.load(f)
        except Exception:
            return

        for guild in self.bot.guilds:
            rows = await self.bot.db.fetchall(
                "SELECT key FROM guild_settings WHERE guild_id = ?;",
                (str(guild.id),),
            )
            existing_keys = {r["key"] for r in rows}
            missing_rows = [
                (str(guild.id), key, _json_dumps(value))
                for key, value in defaults.items()
                if key not in existing_keys
            ]
            await self.bot.db.executemany(
                "INSERT OR IGNORE INTO guild_settings (guild_id, key, value) VALUES (?, ?, ?);",
                missing_rows,
            )

    @commands.Cog.listener()
    async def on_ready(self):
        await self.ensure_schema()
        if self._defaults_loaded_once:
            return
        async with self._defaults_lock:
            if self._defaults_loaded_once:
                return
            await self._load_defaults_if_needed()
            self._defaults_loaded_once = True

    setup = app_commands.Group(
        name="setup",
        description="Setup Vandal Manager systems (tickets, welcomer, giveaways, etc).",
    )

    @setup.command(name="tickets", description="Configure the ticket panel, logs, category and inactivity.")
    @app_commands.describe(
        panel_channel="Channel where the ticket panel is posted",
        logs_channel="Channel where ticket logs/transcripts are sent",
        category="Category where new ticket channels will be created",
        inactivity_hours="Auto-close tickets after inactivity (hours)",
    )
    async def setup_tickets(
        self,
        interaction: discord.Interaction,
        panel_channel: discord.TextChannel,
        logs_channel: discord.TextChannel,
        category: discord.CategoryChannel,
        inactivity_hours: app_commands.Range[int, 1, 168] = 24,
    ):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)

        await interaction.response.defer(ephemeral=True, thinking=True)

        gid = interaction.guild.id
        await self.set_setting(gid, key_for("tickets", "panel_channel_id"), panel_channel.id)
        await self.set_setting(gid, key_for("tickets", "logs_channel_id"), logs_channel.id)
        await self.set_setting(gid, key_for("tickets", "category_id"), category.id)
        await self.set_setting(gid, key_for("tickets", "inactivity_hours"), int(inactivity_hours))

        panel_status = "Not posted"
        tickets_cog = self.bot.get_cog("Tickets")
        if tickets_cog and hasattr(tickets_cog, "refresh_panel_now"):
            try:
                await tickets_cog.refresh_panel_now(interaction.guild)
                panel_status = f"Posted in {panel_channel.mention}"
            except Exception as e:
                panel_status = f"⚠️ Failed to post: `{e}`"

        embed = discord.Embed(
            title="✅ Tickets Configured",
            color=EMBED_COLOR,
            description=(
                f"**Panel Channel:** {panel_channel.mention}\n"
                f"**Logs Channel:** {logs_channel.mention}\n"
                f"**Category:** `{category.name}`\n"
                f"**Inactivity Auto-Close:** `{inactivity_hours}h`\n"
                f"**Panel:** {panel_status}"
            ),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @setup.command(name="welcomer", description="Configure welcome + info channels (link/commands/shop).")
    @app_commands.describe(
        welcome_channel="Where welcome messages are sent",
        link_channel="Channel used for the 'Link your account' section in the welcome message",
        bot_commands_channel="Channel used for the 'Bot Commands' section in the welcome message",
        shop_channel="Channel used for the 'Store' section in the welcome message",
        verify_channel="Channel members use to select a server and get access to the right channels",
    )
    async def setup_welcomer(
        self,
        interaction: discord.Interaction,
        welcome_channel: discord.TextChannel,
        link_channel: discord.TextChannel,
        bot_commands_channel: discord.TextChannel,
        shop_channel: discord.TextChannel,
        verify_channel: discord.TextChannel,
    ):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)

        gid = interaction.guild.id
        await self.set_setting(gid, key_for("welcomer", "welcome_channel_id"), welcome_channel.id)
        await self.set_setting(gid, key_for("welcomer", "link_channel_id"), link_channel.id)
        await self.set_setting(gid, key_for("welcomer", "bot_commands_channel_id"), bot_commands_channel.id)
        await self.set_setting(gid, key_for("welcomer", "shop_channel_id"), shop_channel.id)
        await self.set_setting(gid, key_for("welcomer", "server_selector_channel_id"), verify_channel.id)

        embed = discord.Embed(
            title="✅ Welcomer Configured",
            color=EMBED_COLOR,
            description=(
                f"**Welcome:** {welcome_channel.mention}\n"
                f"**Link:** {link_channel.mention}\n"
                f"**Bot Commands:** {bot_commands_channel.mention}\n"
                f"**Store:** {shop_channel.mention}\n"
                f"**Verify:** {verify_channel.mention}\n"
            ),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @setup.command(name="giveaways", description="Configure giveaway logs + default requirements.")
    @app_commands.describe(
        logs_channel="Where giveaway logs are posted",
        min_account_age_days="Default minimum account age required to enter (days)",
    )
    async def setup_giveaways(
        self,
        interaction: discord.Interaction,
        logs_channel: discord.TextChannel,
        min_account_age_days: app_commands.Range[int, 0, 365] = 7,
    ):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)

        gid = interaction.guild.id
        await self.set_setting(gid, key_for("giveaways", "logs_channel_id"), logs_channel.id)
        await self.set_setting(gid, key_for("giveaways", "default_min_account_age_days"), int(min_account_age_days))

        embed = discord.Embed(
            title="✅ Giveaways Configured",
            color=EMBED_COLOR,
            description=(
                f"**Logs:** {logs_channel.mention}\n"
                f"**Default Min Account Age:** {min_account_age_days} days"
            ),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @setup.command(name="show", description="Show your current saved setup settings.")
    async def setup_show(self, interaction: discord.Interaction):
        if not interaction.guild:
            return
        data = await self.get_all_settings(interaction.guild.id)
        if not data:
            return await interaction.response.send_message("No setup saved yet. Use **/setup** commands first.", ephemeral=True)

        embed = discord.Embed(title="📦 Current Setup", color=EMBED_COLOR)

        sections: Dict[str, list] = {}
        for k, v in data.items():
            module = k.split(".")[0] if "." in k else "other"
            sections.setdefault(module, []).append((k, v))

        for module in sorted(sections.keys()):
            lines = []
            for k, v in sections[module]:
                if "channel_id" in k and isinstance(v, int):
                    lines.append(f"**{k}**: <#{v}>")
                else:
                    lines.append(f"**{k}**: `{v}`")
            if lines:
                embed.add_field(
                    name=f"• {module.upper()}",
                    value="\n".join(lines[:10]),
                    inline=False,
                )

        embed.set_footer(text="Use /setup commands to modify these settings.")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Core(bot))
