# cogs/welcomer.py
from __future__ import annotations

import json
import random
import discord
from discord.ext import commands

from config.branding import EMBED_COLOR, BRAND_NAME, FOOTER_TEXT


def _loads(v: str):
    try:
        return json.loads(v)
    except Exception:
        return v


def ordinal(n: int) -> str:
    if 10 <= (n % 100) <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def setting_key(name: str) -> str:
    return f"welcomer.{name}"


LEAVE_MESSAGES = [
    "Looks like {user} decided the grass is greener elsewhere!",
    "{user} left the server. Good luck out there!",
    "{user} has left the building... literally.",
    "And then there were {count}. {user} is gone.",
    "{user} ghosted us. RIP.",
    "One by one they leave... {user} just did.",
    "{user} hit the eject button!",
    "Farewell, {user}. May your new server be glorious.",
    "{user} nope'd out of here!",
    "See you later, {user}. Thanks for the memories!",
    "The server population just went down. {user} left.",
    "Off you go, {user}! Don't forget us!",
]

KICK_MESSAGES = [
    "{user} got the boot! Kicked into oblivion.",
    "KICKED! {user} didn't follow the rules.",
    "{user} has been yeeted from the server!",
    "Looks like {user} got kicked out harder than a door!",
    "{user} got sent packing! No second chances.",
    "And that's a yikes from us. {user} is gone.",
    "{user} picked a fight with the wrong admin.",
    "The kick heard 'round the world... {user} is out!",
    "{user} got booted! Better luck next time.",
    "Ouch! {user} just got the delete key treatment.",
    "{user} is OUT! No returns, no refunds!",
    "Goodbye, {user}. Try not to do that again somewhere else.",
]

BAN_MESSAGES = [
    "{user} has been BANNED! *mic drop*",
    "The ban hammer has spoken. {user} is gone forever.",
    "Into the shadow realm you go, {user}!",
    "{user} is now swimming with the fishes... BANNED!",
    "Well, {user} has been permanently exiled.",
    "{user} committed the ultimate sin. BANNED.",
    "Adios, {user}! You've been put on the ban list!",
    "BANNED! {user} won't be coming back anytime soon.",
    "{user} has entered the server gulag. PERMANENTLY.",
    "The banhammer just fell on {user}. RIP.",
    "{user} tried to play by their own rules. BANNED!",
    "Server blacklist just got an update: {user}!",
]


class Welcomer(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def get_setting(self, guild_id: int, name: str, default=None):
        row = await self.bot.db.fetchone(
            "SELECT value FROM guild_settings WHERE guild_id = ? AND key = ?;",
            (str(guild_id), setting_key(name)),
        )
        if not row:
            return default
        return _loads(row["value"])

    async def get_channels(self, guild: discord.Guild):
        welcome_id = await self.get_setting(guild.id, "welcome_channel_id")
        link_id = await self.get_setting(guild.id, "link_channel_id")
        cmds_id = await self.get_setting(guild.id, "bot_commands_channel_id")
        shop_id = await self.get_setting(guild.id, "shop_channel_id")
        selector_id = await self.get_setting(guild.id, "server_selector_channel_id")

        welcome_ch = guild.get_channel(int(welcome_id)) if welcome_id else None
        link_ch = guild.get_channel(int(link_id)) if link_id else None
        cmds_ch = guild.get_channel(int(cmds_id)) if cmds_id else None
        shop_ch = guild.get_channel(int(shop_id)) if shop_id else None
        selector_ch = guild.get_channel(int(selector_id)) if selector_id else None

        return welcome_ch, link_ch, cmds_ch, shop_ch, selector_ch

    def build_message(
        self,
        *,
        member: discord.Member,
        human_count: int,
        link_ch: discord.abc.GuildChannel | None,
        cmds_ch: discord.abc.GuildChannel | None,
        shop_ch: discord.abc.GuildChannel | None,
        selector_ch: discord.abc.GuildChannel | None,
    ) -> str:
        link_ref = link_ch.mention if link_ch else "Link channel not set"
        cmds_ref = cmds_ch.mention if cmds_ch else "Commands channel not set"
        shop_ref = shop_ch.mention if shop_ch else "Shop channel not set"
        selector_ref = selector_ch.mention if selector_ch else "Server selector not set"

        return (
            f"Hey {member.mention} You are our **{ordinal(human_count)}** member!\n\n"
            f"🖥️ **Server Selector**\n"
            f"Head to {selector_ref} to get access to the server and the right channels!\n\n"
            f"🔗 **Link Your Account**\n"
            f"Click here {link_ref} to link your acc and get access to commands!\n\n"
            f"🔗 **Bot Commands**\n"
            f"{cmds_ref} to access bot commands and information.\n\n"
            f"🛒 **Visit Our Store**\n"
            f"{shop_ref} for exclusive packages and deals!"
        )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot or not member.guild:
            return

        guild = member.guild
        if not await self.bot.is_guild_approved(guild.id):
            return
        welcome_ch, link_ch, cmds_ch, shop_ch, selector_ch = await self.get_channels(guild)

        if not isinstance(welcome_ch, discord.TextChannel):
            return

        human_count = sum(1 for m in guild.members if not m.bot)

        msg = self.build_message(
            member=member,
            human_count=human_count,
            link_ch=link_ch,
            cmds_ch=cmds_ch,
            shop_ch=shop_ch,
            selector_ch=selector_ch,
        )

        embed = discord.Embed(
            title=f"💫 Welcome to {BRAND_NAME}",
            description=msg,
            color=EMBED_COLOR,
        )
        embed.set_footer(text=FOOTER_TEXT)

        try:
            await welcome_ch.send(embed=embed)
        except Exception:
            try:
                await welcome_ch.send(msg)
            except Exception:
                pass

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if member.bot or not member.guild:
            return

        guild = member.guild
        if not await self.bot.is_guild_approved(guild.id):
            return
        welcome_ch, _, _, _, _ = await self.get_channels(guild)

        if not isinstance(welcome_ch, discord.TextChannel):
            return

        try:
            async for entry in guild.audit_logs(limit=5):
                if entry.action == discord.AuditLogAction.kick and entry.target.id == member.id:
                    msg = random.choice(KICK_MESSAGES).format(user=member.mention, count=len([m for m in guild.members if not m.bot]))
                    embed = discord.Embed(description=msg, color=0xFF6B6B)
                    embed.set_footer(text="Kicked")
                    await welcome_ch.send(embed=embed)
                    return
                elif entry.action == discord.AuditLogAction.ban and entry.target.id == member.id:
                    msg = random.choice(BAN_MESSAGES).format(user=member.mention, count=len([m for m in guild.members if not m.bot]))
                    embed = discord.Embed(description=msg, color=0x2C2C2C)
                    embed.set_footer(text="Banned")
                    await welcome_ch.send(embed=embed)
                    return
        except Exception:
            pass

        msg = random.choice(LEAVE_MESSAGES).format(user=member.mention, count=len([m for m in guild.members if not m.bot]))
        embed = discord.Embed(description=msg, color=0xFFA500)
        embed.set_footer(text="Member Left")
        try:
            await welcome_ch.send(embed=embed)
        except Exception:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Welcomer(bot))
