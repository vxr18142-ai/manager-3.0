# utils/checks.py
from __future__ import annotations

import os

import discord
from discord import app_commands


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw.strip())
    except Exception:
        return default


EU_TICKET_STAFF_ROLE_ID = _env_int("EU_TICKET_STAFF_ROLE_ID", 0)
AU_TICKET_STAFF_ROLE_ID = _env_int("AU_TICKET_STAFF_ROLE_ID", 0)


def has_any_staff_role(member: discord.Member) -> bool:
    member_role_ids = {role.id for role in member.roles}
    staff_ids = {EU_TICKET_STAFF_ROLE_ID, AU_TICKET_STAFF_ROLE_ID} - {0}
    if not staff_ids:
        return False
    return bool(member_role_ids.intersection(staff_ids))


def is_admin(interaction: discord.Interaction) -> bool:
    if not interaction.guild or not interaction.user:
        return False
    member = interaction.user
    if isinstance(member, discord.Member):
        return member.guild_permissions.administrator
    return False


async def has_staff_scope(bot, guild_id: int, member: discord.Member, scope: str) -> bool:
    if member.guild_permissions.administrator:
        return True
    return has_any_staff_role(member)


def require_staff(scope: str):
    async def predicate(interaction: discord.Interaction) -> bool:
        if not interaction.guild or not interaction.user:
            return False
        if not isinstance(interaction.user, discord.Member):
            return False
        ok = await has_staff_scope(interaction.client, interaction.guild.id, interaction.user, scope)
        return ok

    return app_commands.check(predicate)
