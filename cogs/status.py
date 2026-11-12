# cogs/status.py
from __future__ import annotations

import os
import discord
from discord.ext import commands, tasks


class Status(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.interval = int(os.getenv("STATUS_INTERVAL", "180"))
        self.update_status.change_interval(seconds=self.interval)

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.update_status.is_running():
            self.update_status.start()

    def _best_effort_human_count(self, guilds: list[discord.Guild]) -> int:
        total_humans = 0
        for g in guilds:
            if g.chunked and g.members:
                humans = sum(1 for m in g.members if not m.bot)
                total_humans += humans
            else:
                total_humans += (g.member_count or 0)
        return total_humans

    @tasks.loop(seconds=180)
    async def update_status(self):
        approved_guilds = [
            guild
            for guild in self.bot.guilds
            if await self.bot.is_guild_approved(guild.id)
        ]
        count = self._best_effort_human_count(approved_guilds)
        activity = discord.Activity(type=discord.ActivityType.watching, name=f"👥 {count:,} members")
        await self.bot.change_presence(activity=activity)

    @update_status.before_loop
    async def before_update_status(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(Status(bot))
