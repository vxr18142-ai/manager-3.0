from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta

import discord
from discord import app_commands
from discord.ext import commands


class Purge(commands.Cog):
    """Cog for purging messages from a channel."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="purge", description="Delete messages from this channel")
    @app_commands.describe(
        amount="Number of messages to delete (1–1000)",
        member="Only delete messages from this user (optional)",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def purge(
        self,
        interaction: discord.Interaction,
        amount: int,
        member: discord.Member | None = None,
    ) -> None:
        if amount < 1 or amount > 1000:
            await interaction.response.send_message(
                "Please provide a number between 1 and 1000.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        cutoff = datetime.now(timezone.utc) - timedelta(days=14)

        messages: list[discord.Message] = []
        async for msg in interaction.channel.history(limit=amount):
            if member and msg.author != member:
                continue
            messages.append(msg)

        if not messages:
            await interaction.followup.send("No messages found to delete.", ephemeral=True)
            return

        recent = [m for m in messages if m.created_at > cutoff]
        old    = [m for m in messages if m.created_at <= cutoff]

        deleted = 0

        if recent:
            for i in range(0, len(recent), 100):
                batch = recent[i:i + 100]
                if len(batch) == 1:
                    await batch[0].delete()
                else:
                    await interaction.channel.delete_messages(batch)
                deleted += len(batch)
                await asyncio.sleep(0.5)

        if old:
            for msg in old:
                try:
                    await msg.delete()
                    deleted += 1
                except discord.NotFound:
                    pass
                await asyncio.sleep(0.75)

        target = f" from {member.mention}" if member else ""
        await interaction.followup.send(
            f"Deleted {deleted} message(s){target}.", ephemeral=True
        )

    @purge.error
    async def purge_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need the **Administrator** permission to use this command.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"An error occurred: {error}", ephemeral=True
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Purge(bot))
