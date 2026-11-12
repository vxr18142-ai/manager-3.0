# cogs/staff.py
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from utils.checks import is_admin

PANEL_COLOR = 0x008B8B
PANEL_FOOTER = "Vertex"


class Staff(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    staff = app_commands.Group(
        name="staff",
        description="Staff role management. Admin only.",
        guild_only=True,
    )

    @staff.command(name="add", description="Add a staff role.")
    @app_commands.describe(role="Role to grant staff command access.")
    async def staff_add(self, interaction: discord.Interaction, role: discord.Role):
        if not is_admin(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)

        try:
            await self.bot.db.execute(
                "INSERT OR REPLACE INTO staff_roles (guild_id, scope, role_id) VALUES (?, 'tickets', ?);",
                (str(interaction.guild_id), str(role.id)),
            )
        except Exception as e:
            return await interaction.response.send_message(
                f"❌ Database error: `{e}`",
                ephemeral=True,
            )

        await interaction.response.send_message(
            f"✅ Added {role.mention} as a staff role.",
            ephemeral=True,
        )

    @staff.command(name="remove", description="Remove a staff role.")
    @app_commands.describe(role="Role to remove staff access from.")
    async def staff_remove(self, interaction: discord.Interaction, role: discord.Role):
        if not is_admin(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)

        try:
            await self.bot.db.execute(
                "DELETE FROM staff_roles WHERE guild_id = ? AND scope = 'tickets' AND role_id = ?;",
                (str(interaction.guild_id), str(role.id)),
            )
        except Exception as e:
            return await interaction.response.send_message(
                f"❌ Database error: `{e}`",
                ephemeral=True,
            )

        await interaction.response.send_message(
            f"✅ Removed {role.mention} from staff roles.",
            ephemeral=True,
        )

    @staff.command(name="list", description="List all staff roles.")
    async def staff_list(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)

        try:
            rows = await self.bot.db.fetchall(
                "SELECT role_id FROM staff_roles WHERE guild_id = ? AND scope = 'tickets' ORDER BY role_id;",
                (str(interaction.guild_id),),
            )
        except Exception as e:
            return await interaction.response.send_message(
                f"❌ Database error: `{e}`",
                ephemeral=True,
            )

        guild = interaction.guild
        role_lines = []
        for r in rows:
            rid = int(r["role_id"])
            role = guild.get_role(rid) if guild else None
            role_lines.append(role.mention if role else f"<@&{rid}>")

        embed = discord.Embed(
            title="Staff Roles",
            description="These roles can use Close, Claim, and Add People on tickets.",
            color=PANEL_COLOR,
        )
        embed.add_field(
            name="Roles",
            value="\n".join(role_lines) if role_lines else "—  *(none added yet)*",
            inline=False,
        )
        embed.set_footer(text=PANEL_FOOTER)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Staff(bot))
