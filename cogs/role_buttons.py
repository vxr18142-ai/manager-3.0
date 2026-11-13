# cogs/role_buttons.py
from __future__ import annotations

import json
import os
from typing import Optional, Dict, Any, List, Tuple

import discord
from discord import app_commands
from discord.ext import commands

from config.branding import EMBED_COLOR
from utils.approval import ApprovalView

SETTINGS_PREFIX = "roles."
KEY_CHANNEL_ID = SETTINGS_PREFIX + "channel_id"
KEY_PANEL_MESSAGE_ID = SETTINGS_PREFIX + "panel_message_id"
KEY_BUTTONS = SETTINGS_PREFIX + "buttons"

MAX_BUTTONS = 5

_domain = os.getenv("REPLIT_DEV_DOMAIN", "")
THUMB_OK = (
    f"https://{_domain}/api/assets/raze-r.gif"
    if _domain
    else "https://imgur.com/a/wpY4y25#R0GWHg5"
)


def _safe_json_load(s: Optional[str], default):
    if not s:
        return default
    try:
        return json.loads(s)
    except Exception:
        return default


class RolePanelView(ApprovalView):
    def __init__(self, cog: "RoleButtons", guild_id: int, buttons: List[Dict[str, Any]]):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id
        self.buttons_cfg = buttons

        for idx, cfg in enumerate(buttons[:MAX_BUTTONS], start=1):
            label = str(cfg.get("label") or f"Role {idx}")[:80]
            role_id = int(cfg.get("role_id") or 0)
            emoji = cfg.get("emoji")

            if role_id <= 0:
                continue

            self.add_item(RoleButton(
                cog=cog,
                guild_id=guild_id,
                index=idx,
                label=label,
                role_id=role_id,
                emoji=emoji,
            ))


class RoleButton(discord.ui.Button):
    def __init__(
        self,
        cog: "RoleButtons",
        guild_id: int,
        index: int,
        label: str,
        role_id: int,
        emoji: Optional[str],
    ):
        super().__init__(
            label=label,
            emoji=emoji if emoji else None,
            style=discord.ButtonStyle.primary,
            custom_id=f"roles:pick:{guild_id}:{index}",
        )
        self.cog = cog
        self.guild_id = guild_id
        self.index = index
        self.role_id = role_id

    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return

        if interaction.guild.id != self.guild_id:
            return await interaction.response.send_message("Wrong server.", ephemeral=True)

        ok, msg = await self.cog.toggle_panel_role(interaction.guild, interaction.user, self.role_id)
        return await interaction.response.send_message(msg, ephemeral=True)


class RoleButtons(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _get_setting_raw(self, guild_id: int, key: str) -> Optional[str]:
        row = await self.bot.db.fetchone(
            "SELECT value FROM guild_settings WHERE guild_id = ? AND key = ?;",
            (str(guild_id), key),
        )
        return row["value"] if row else None

    async def _set_setting(self, guild_id: int, key: str, value: Any) -> None:
        await self.bot.db.execute(
            "INSERT OR REPLACE INTO guild_settings (guild_id, key, value) VALUES (?, ?, ?);",
            (str(guild_id), key, json.dumps(value)),
        )

    async def get_buttons(self, guild_id: int) -> List[Dict[str, Any]]:
        raw = await self._get_setting_raw(guild_id, KEY_BUTTONS)
        buttons = _safe_json_load(raw, [])
        if not isinstance(buttons, list):
            return []
        out: List[Dict[str, Any]] = []
        for b in buttons[:MAX_BUTTONS]:
            if not isinstance(b, dict):
                continue
            out.append({
                "label": str(b.get("label") or "").strip(),
                "role_id": int(b.get("role_id") or 0),
                "emoji": (str(b.get("emoji")).strip() if b.get("emoji") else None),
            })
        return out

    async def get_channel_id(self, guild_id: int) -> Optional[int]:
        raw = await self._get_setting_raw(guild_id, KEY_CHANNEL_ID)
        if not raw:
            return None
        try:
            v = _safe_json_load(raw, None)
            return int(v) if v is not None else None
        except Exception:
            return None

    async def get_panel_message_id(self, guild_id: int) -> Optional[int]:
        raw = await self._get_setting_raw(guild_id, KEY_PANEL_MESSAGE_ID)
        if not raw:
            return None
        try:
            v = _safe_json_load(raw, None)
            return int(v) if v is not None else None
        except Exception:
            return None

    async def toggle_panel_role(self, guild: discord.Guild, member: discord.Member, picked_role_id: int) -> Tuple[bool, str]:
        picked = guild.get_role(int(picked_role_id))
        if not picked:
            return False, "That role no longer exists."

        if picked in member.roles:
            try:
                await member.remove_roles(picked, reason="Role panel selection (toggle remove)")
            except discord.Forbidden:
                return False, "I don't have permission to edit your roles."
            except Exception:
                return False, "Failed to remove role."
            return True, f"Removed role **{picked.name}**."

        try:
            await member.add_roles(picked, reason="Role panel selection (toggle add)")
        except discord.Forbidden:
            return False, "I don't have permission to edit your roles."
        except Exception:
            return False, "Failed to add role."

        return True, f"Added role **{picked.name}**."

    def build_panel_embed(self, guild: discord.Guild, buttons: List[Dict[str, Any]]) -> discord.Embed:
        embed = discord.Embed(
            title="Sind - Server Roles",
            description="Pick any server roles you want. Click a button again to remove that role.",
            color=EMBED_COLOR,
        )
        embed.set_thumbnail(url=THUMB_OK)

        lines = []
        for i, b in enumerate(buttons[:MAX_BUTTONS], start=1):
            rid = int(b.get("role_id") or 0)
            if rid <= 0:
                continue
            role = guild.get_role(rid)
            label = (b.get("label") or (role.name if role else f"Role {i}")).strip()
            emoji = (b.get("emoji") or "").strip()
            role_txt = role.mention if role else f"`{rid}`"
            lines.append(f"{emoji + ' ' if emoji else ''}**{label}** -> {role_txt}")

        if not lines:
            lines = ["No buttons configured yet. Use `/roles setbutton` to add them."]

        embed.add_field(name="Available", value="\n".join(lines), inline=False)
        embed.set_footer(text="VOIDEX HQ Core")
        return embed

    async def post_or_update_panel(self, guild: discord.Guild) -> Optional[discord.Message]:
        channel_id = await self.get_channel_id(guild.id)
        if not channel_id:
            return None

        ch = guild.get_channel(int(channel_id))
        if not isinstance(ch, discord.TextChannel):
            return None

        buttons = await self.get_buttons(guild.id)
        embed = self.build_panel_embed(guild, buttons)
        view = RolePanelView(self, guild.id, buttons)

        msg_id = await self.get_panel_message_id(guild.id)
        if msg_id:
            try:
                msg = await ch.fetch_message(int(msg_id))
                await msg.edit(embed=embed, view=view)
                return msg
            except Exception:
                pass

        msg = await ch.send(embed=embed, view=view)
        await self._set_setting(guild.id, KEY_PANEL_MESSAGE_ID, msg.id)
        return msg

    @commands.Cog.listener()
    async def on_ready(self):
        for g in self.bot.guilds:
            try:
                buttons = await self.get_buttons(g.id)
                if buttons:
                    self.bot.add_view(RolePanelView(self, g.id, buttons))
            except Exception:
                pass

    roles = app_commands.Group(name="roles", description="Role buttons setup.", guild_only=True)

    def _admin_only(self, inter: discord.Interaction) -> bool:
        return bool(inter.user and isinstance(inter.user, discord.Member) and inter.user.guild_permissions.administrator)

    @roles.command(name="setchannel", description="Set the channel where the role panel is posted.")
    async def roles_setchannel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not interaction.guild:
            return
        if not self._admin_only(interaction):
            return await interaction.response.send_message("Admin only.", ephemeral=True)

        await self._set_setting(interaction.guild.id, KEY_CHANNEL_ID, int(channel.id))
        await interaction.response.send_message(f"Role panel channel set to {channel.mention}", ephemeral=True)

    @roles.command(name="setbutton", description="Set one of the role buttons (1-5).")
    @app_commands.describe(
        slot="Button number (1-5)",
        label="Button label (what people see)",
        role="Role to give when clicked",
        emoji="Optional emoji (example: blue_square)",
    )
    async def roles_setbutton(
        self,
        interaction: discord.Interaction,
        slot: app_commands.Range[int, 1, 5],
        label: str,
        role: discord.Role,
        emoji: Optional[str] = None,
    ):
        if not interaction.guild:
            return
        if not self._admin_only(interaction):
            return await interaction.response.send_message("Admin only.", ephemeral=True)

        buttons = await self.get_buttons(interaction.guild.id)

        while len(buttons) < MAX_BUTTONS:
            buttons.append({"label": "", "role_id": 0, "emoji": None})

        buttons[int(slot) - 1] = {
            "label": label.strip()[:80],
            "role_id": int(role.id),
            "emoji": (emoji.strip()[:32] if emoji else None),
        }

        await self._set_setting(interaction.guild.id, KEY_BUTTONS, buttons)
        await interaction.response.send_message(
            f"Set button **{slot}** -> **{label.strip()}** ({role.mention})",
            ephemeral=True,
        )

    @roles.command(name="clear", description="Clear all role buttons.")
    async def roles_clear(self, interaction: discord.Interaction):
        if not interaction.guild:
            return
        if not self._admin_only(interaction):
            return await interaction.response.send_message("Admin only.", ephemeral=True)

        await self._set_setting(interaction.guild.id, KEY_BUTTONS, [])
        await self._set_setting(interaction.guild.id, KEY_PANEL_MESSAGE_ID, None)
        await interaction.response.send_message("Cleared role buttons.", ephemeral=True)

    @roles.command(name="post", description="Post/update the role panel now.")
    async def roles_post(self, interaction: discord.Interaction):
        if not interaction.guild:
            return
        if not self._admin_only(interaction):
            return await interaction.response.send_message("Admin only.", ephemeral=True)

        # Defer immediately — posting/editing the panel involves Discord API calls
        # that can exceed the 3-second interaction timeout.
        await interaction.response.defer(ephemeral=True)

        msg = await self.post_or_update_panel(interaction.guild)
        if not msg:
            return await interaction.followup.send(
                "Set a channel first with `/roles setchannel`.", ephemeral=True
            )

        await interaction.followup.send(
            f"Panel posted/updated in {msg.channel.mention}", ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(RoleButtons(bot))
