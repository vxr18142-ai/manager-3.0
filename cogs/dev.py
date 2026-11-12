# cogs/dev.py
from __future__ import annotations

from discord.ext import commands


class Dev(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.is_owner()
    @commands.command(name="reload")
    async def reload_ext(self, ctx: commands.Context, ext: str):
        """Reload an extension: e.g. !reload cogs.welcomer"""
        await self.bot.reload_extension(ext)
        await ctx.reply(f"✅ Reloaded `{ext}`")


async def setup(bot: commands.Bot):
    await bot.add_cog(Dev(bot))
