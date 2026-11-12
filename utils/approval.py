
from __future__ import annotations

import discord

SUPPORT_DISCORD = "https://discord.gg/vNa2czEnJ"

LOCKED_MESSAGE = (
    "🔒 **VOIDEX HQ | Bot Locked**\n\n"
    "Your server has not been approved by the VOIDEX HQ Developers yet.\n\n"
    "🎫 **How to unlock your bot**\n"
    "Open a ticket in our Support Discord and request server approval.\n\n"
    "⏳ Once your server is approved, your bot's features will unlock.\n\n"
    f"**Support Discord:** {SUPPORT_DISCORD}"
)


async def require_guild_approval(
    interaction: discord.Interaction,
) -> bool:
    guild_id = getattr(interaction, "guild_id", None)
    if guild_id is None and interaction.guild is not None:
        guild_id = interaction.guild.id

    if guild_id is None:
        return True

    client = getattr(interaction, "client", None)
    check = getattr(client, "is_guild_approved", None)

    if not callable(check):
        raise RuntimeError(
            "Missing is_guild_approved(guild_id) on the bot client."
        )

    approved = await check(guild_id)

    if approved:
        return True

    try:
        if interaction.response.is_done():
            await interaction.followup.send(
                LOCKED_MESSAGE,
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                LOCKED_MESSAGE,
                ephemeral=True,
            )
    except discord.HTTPException:
        pass

    return False


class ApprovalView(discord.ui.View):
    def __init__(self, *, timeout: float | None = 180):
        super().__init__(timeout=timeout)

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        return await require_guild_approval(interaction)


class ApprovalModal(discord.ui.Modal):
    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        return await require_guild_approval(interaction)
