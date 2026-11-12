# cogs/role_selector.py
from __future__ import annotations

import discord
from discord.ext import commands

from config.panels import ROLE_SELECTOR
from config.branding import EMBED_COLOR, FOOTER_TEXT
from utils.approval import ApprovalView


class RoleToggleButton(discord.ui.Button):
    def __init__(self, label: str, emoji: str, role_id: int):
        super().__init__(
            label=label,
            emoji=emoji,
            style=discord.ButtonStyle.primary,
            custom_id=f"role_toggle:{role_id}",
        )
        self.role_id = role_id

    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return

        role = interaction.guild.get_role(self.role_id)
        if not role:
            return await interaction.response.send_message(
                "❌ This role no longer exists.",
                ephemeral=True,
            )

        member = interaction.user

        if role in member.roles:
            await member.remove_roles(role, reason="Role selector toggle")
            await interaction.response.send_message(
                f"❌ Removed **{role.name}**.",
                ephemeral=True,
            )
        else:
            await member.add_roles(role, reason="Role selector toggle")
            await interaction.response.send_message(
                f"✅ You now have **{role.name}**.",
                ephemeral=True,
            )


class RoleSelectorView(ApprovalView):
    def __init__(self):
        super().__init__(timeout=None)

        for btn in ROLE_SELECTOR["buttons"]:
            self.add_item(
                RoleToggleButton(
                    label=btn["label"],
                    emoji=btn["emoji"],
                    role_id=btn["role_id"],
                )
            )


class RoleSelector(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        self.bot.add_view(RoleSelectorView())

        channel_id = ROLE_SELECTOR["channel_id"]
        if not channel_id:
            return
        channel = self.bot.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        if not await self.bot.is_guild_approved(channel.guild.id):
            return

        embed = discord.Embed(
            title=ROLE_SELECTOR["embed"]["title"],
            description=ROLE_SELECTOR["embed"]["description"],
            color=EMBED_COLOR,
        )
        embed.set_footer(text=FOOTER_TEXT)

        async for msg in channel.history(limit=25):
            if msg.author == self.bot.user and msg.components:
                await msg.edit(embed=embed, view=RoleSelectorView())
                return

        await channel.send(embed=embed, view=RoleSelectorView())


async def setup(bot: commands.Bot):
    await bot.add_cog(RoleSelector(bot))
