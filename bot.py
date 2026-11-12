from __future__ import annotations

import logging
import os
from pathlib import Path

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands

from utils.database import Database
from utils.approval import LOCKED_MESSAGE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("bot")

TOKEN    = os.environ.get("DISCORD_TOKEN")
DB_PATH  = os.environ.get("DB_PATH", "eclipse.db")
COMMAND_USER_ID = int(os.environ.get("DEVELOPER_USER_ID", "1405780303745777749"))
SUPPORT_DISCORD_INVITE = "https://discord.gg/vNa2czEnJ"

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN is not set.")


class ApprovalCommandTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        data = interaction.data or {}
        options = data.get("options", [])
        is_approval_queue = data.get("name") == "dev" and any(
            option.get("name") == "approval-queue" for option in options
        )
        is_developer_command = data.get("name") in {"approve", "unapprove", "sync"} or is_approval_queue
        if is_developer_command and interaction.user.id != COMMAND_USER_ID:
            await interaction.response.send_message(
                "Only the authorized developer can use this command.",
                ephemeral=True,
            )
            return False
        if is_developer_command or interaction.guild is None:
            return True
        if await self.client.is_guild_approved(interaction.guild.id):
            return True

        await interaction.response.send_message(
            self.client.approval_notice(),
            ephemeral=True,
        )
        return False


intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True


class ApprovalQueueView(discord.ui.View):
    def __init__(self, bot: EclipseBot, requests: list[aiosqlite.Row]):
        super().__init__(timeout=300)
        self.bot = bot
        self.requests = {int(request["guild_id"]): request for request in requests}
        self.guild_ids = list(self.requests)
        self.page = 0
        self._refresh_items()

    @property
    def page_count(self) -> int:
        return max(1, (len(self.guild_ids) + 3) // 4)

    def build_embed(self, notice: str | None = None) -> discord.Embed:
        start = self.page * 4
        page_ids = self.guild_ids[start:start + 4]
        description = notice or f"{len(self.guild_ids)} server(s) are waiting for developer review."
        if not self.guild_ids:
            description = notice or "There are no pending server approvals."
        embed = discord.Embed(
            title="Server Approval Queue",
            description=description,
            color=discord.Color.orange(),
        )
        for guild_id in page_ids:
            request = self.requests[guild_id]
            guild = self.bot.get_guild(guild_id)
            name = guild.name if guild else (request["guild_name"] or f"Server {guild_id}")
            owner_id = int(request["owner_id"])
            embed.add_field(
                name=f"{name[:200]} (`{guild_id}`)",
                value=(
                    f"Owner: <@{owner_id}> (`{owner_id}`)\n"
                    f"Status: `pending`\n"
                    f"Added: `{request['requested_at']}`"
                ),
                inline=False,
            )
        if self.guild_ids:
            embed.set_footer(text=f"Page {self.page + 1} of {self.page_count}")
        return embed

    def _refresh_items(self) -> None:
        self.clear_items()
        start = self.page * 4
        for row, guild_id in enumerate(self.guild_ids[start:start + 4]):
            self.add_item(ApprovalDecisionButton(guild_id, approved=True, row=row))
            self.add_item(ApprovalDecisionButton(guild_id, approved=False, row=row))
        if self.page_count > 1:
            self.add_item(ApprovalQueuePageButton(direction=-1, row=4))
            self.add_item(ApprovalQueuePageButton(direction=1, row=4))

    async def change_page(self, interaction: discord.Interaction, direction: int) -> None:
        if interaction.user.id != COMMAND_USER_ID:
            await interaction.response.send_message("Only the authorized user can review approvals.", ephemeral=True)
            return
        self.page = (self.page + direction) % self.page_count
        self._refresh_items()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def decide(self, interaction: discord.Interaction, guild_id: int, approved: bool) -> None:
        if interaction.user.id != COMMAND_USER_ID:
            await interaction.response.send_message("Only the authorized user can review approvals.", ephemeral=True)
            return
        request = self.requests.get(guild_id)
        if request is None:
            await interaction.response.send_message("That approval request is no longer available.", ephemeral=True)
            return

        await interaction.response.defer()
        owner_id = int(request["owner_id"])
        try:
            await self.bot.db.decide_guild_approval(
                guild_id,
                approved,
                interaction.user.id,
            )
        except ValueError:
            self.requests.pop(guild_id, None)
            if guild_id in self.guild_ids:
                self.guild_ids.remove(guild_id)
            if self.page >= self.page_count:
                self.page = self.page_count - 1
            self._refresh_items()
            await interaction.edit_original_response(
                content="This approval request is no longer pending.",
                embed=self.build_embed(),
                view=self,
            )
            return
        except aiosqlite.Error:
            log.exception("Failed to update approval for guild %s", guild_id)
            await interaction.edit_original_response(
                content="The approval database could not be updated. Please try again.",
                view=self,
            )
            return

        await self.bot.notify_guild_owner_of_decision(
            guild_id,
            owner_id,
            approved,
        )
        guild = self.bot.get_guild(guild_id)
        name = guild.name if guild else (request["guild_name"] or f"Server {guild_id}")
        decision = "approved" if approved else "declined"
        self.requests.pop(guild_id)
        self.guild_ids.remove(guild_id)
        if self.page >= self.page_count:
            self.page = self.page_count - 1
        self._refresh_items()
        await interaction.edit_original_response(
            content=f"**{name}** (`{guild_id}`) was {decision}.",
            embed=self.build_embed(),
            view=self,
        )


class ApprovalDecisionButton(discord.ui.Button):
    def __init__(self, guild_id: int, approved: bool, row: int):
        label = "Accept" if approved else "Decline"
        style = discord.ButtonStyle.success if approved else discord.ButtonStyle.danger
        action = "accept" if approved else "decline"
        super().__init__(
            label=label,
            style=style,
            custom_id=f"approval:{action}:{guild_id}",
            row=row,
        )
        self.guild_id = guild_id
        self.approved = approved

    async def callback(self, interaction: discord.Interaction) -> None:
        if isinstance(self.view, ApprovalQueueView):
            await self.view.decide(interaction, self.guild_id, self.approved)


class ApprovalQueuePageButton(discord.ui.Button):
    def __init__(self, direction: int, row: int):
        label = "Previous" if direction < 0 else "Next"
        super().__init__(
            label=label,
            style=discord.ButtonStyle.secondary,
            custom_id=f"approval:page:{'previous' if direction < 0 else 'next'}",
            row=row,
        )
        self.direction = direction

    async def callback(self, interaction: discord.Interaction) -> None:
        if isinstance(self.view, ApprovalQueueView):
            await self.view.change_page(interaction, self.direction)


class EclipseBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(
            command_prefix="!",
            intents=intents,
            tree_cls=ApprovalCommandTree,
            owner_id=COMMAND_USER_ID,
        )
        self.db = Database(DB_PATH)
        self._synced_guild_command_ids: set[int] = set()
        self._global_commands_cleared = False
        self.add_check(self._check_guild_approval)

    async def is_guild_approved(self, guild_id: int) -> bool:
        return await self.db.is_guild_approved(guild_id)

    def approval_notice(self) -> str:
        return LOCKED_MESSAGE

    async def notify_guild_owner_of_pending_approval(self, guild: discord.Guild) -> None:
        if not await self.db.claim_guild_approval_notice(guild.id):
            log.info("Approval notice already claimed for guild %s", guild.id)
            return

        try:
            owner = self.get_user(guild.owner_id) or await self.fetch_user(guild.owner_id)
            await owner.send(self.approval_notice())
            log.info("Sent approval instructions to owner %s for guild %s", owner.id, guild.id)
        except discord.Forbidden:
            log.warning("Could not DM owner %s for guild %s; owner DMs are unavailable", guild.owner_id, guild.id)
        except discord.HTTPException:
            log.exception("Could not DM owner %s for guild %s", guild.owner_id, guild.id)

    async def notify_guild_owner_of_decision(
        self,
        guild_id: int,
        owner_id: int,
        approved: bool,
    ) -> None:
        status = "approved" if approved else "declined"
        if approved:
            message = (
                "✅ **Access Granted!**\n\n"
                "The developer has approved your server's bot access request.\n\n"
                f"**Support Discord:** [{SUPPORT_DISCORD_INVITE}] \n\n"
                "All bot commands are now fully unlocked. Enjoy! 🎉"
            )
        else:
            message = (
                "The developer declined your server's bot access request. "
                "You can open a ticket in the support Discord to appeal."
                f"\nSupport Discord: {SUPPORT_DISCORD_INVITE}"
            )

        try:
            owner = self.get_user(owner_id) or await self.fetch_user(owner_id)
            await owner.send(message)
            log.info("Notified owner %s that guild %s was %s", owner_id, guild_id, status)
        except discord.Forbidden:
            log.warning("Could not DM owner %s about guild %s decision", owner_id, guild_id)
        except discord.HTTPException:
            log.exception("Could not DM owner %s about guild %s decision", owner_id, guild_id)

    async def sync_guild_commands(self, guild: discord.Guild) -> int:
        guild_object = discord.Object(id=guild.id)
        self.tree.clear_commands(guild=guild_object)
        self.tree.copy_global_to(guild=guild_object)
        synced = await self.tree.sync(guild=guild_object)
        self._synced_guild_command_ids.add(guild.id)
        command_names = ", ".join(sorted(command.name for command in synced))
        log.info(
            "Synced %d instant guild commands to %s (id=%s): %s",
            len(synced),
            guild.name,
            guild.id,
            command_names,
        )
        return len(synced)

    async def clear_global_commands(self) -> None:
        commands_to_restore = self.tree.get_commands()
        self.tree.clear_commands(guild=None)
        try:
            await self.tree.sync()
        finally:
            for command in commands_to_restore:
                self.tree.add_command(command)
        self._global_commands_cleared = True
        log.info("Cleared global commands; commands are registered per server")

    async def _check_guild_approval(self, ctx: commands.Context) -> bool:
        if ctx.guild is None or await self.is_guild_approved(ctx.guild.id):
            return True
        if (
            ctx.command
            and ctx.command.qualified_name in {"approve", "unapprove"}
            and await self.is_owner(ctx.author)
        ):
            return True

        await ctx.reply(
            self.approval_notice(),
            mention_author=False,
        )
        return False

    async def setup_hook(self) -> None:
        await self.db.connect()
        log.info("Database connected: %s", DB_PATH)
        # ── Load all cogs ──────────────────────────────────────────────────────
        cogs_dir = Path(__file__).parent / "cogs"
        if cogs_dir.is_dir():
            for f in sorted(cogs_dir.glob("*.py")):
                # rules.py is the maintained Rules cog; rules_cog.py duplicates its name.
                if f.name.startswith("_") or f.stem == "rules_cog":
                    continue
                ext = f"cogs.{f.stem}"
                try:
                    await self.load_extension(ext)
                    log.info("Loaded %s", ext)
                except Exception:
                    log.exception("Failed to load %s", ext)

    async def on_ready(self) -> None:
        log.info("Logged in as %s (id=%s)", self.user, self.user.id if self.user else "?")
        for guild in self.guilds:
            await self.db.ensure_guild_pending(guild.id)
            request = await self.db.get_guild_approval_request(guild.id)
            if request is None:
                await self.db.queue_guild_approval(guild.id, guild.name, guild.owner_id)
                await self.notify_guild_owner_of_pending_approval(guild)
            elif not request["guild_name"]:
                await self.db.update_guild_approval_name(guild.id, guild.name)
            if guild.id not in self._synced_guild_command_ids:
                try:
                    await self.sync_guild_commands(guild)
                except Exception:
                    log.exception("Failed to sync commands to guild %s (id=%s)", guild.name, guild.id)
        if not self._global_commands_cleared:
            try:
                await self.clear_global_commands()
            except Exception:
                log.exception("Failed to clear global commands")

    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self.db.queue_guild_approval(guild.id, guild.name, guild.owner_id)
        log.info("Joined and queued guild %s for approval (id=%s)", guild.name, guild.id)
        await self.notify_guild_owner_of_pending_approval(guild)
        try:
            await self.sync_guild_commands(guild)
        except Exception:
            log.exception("Failed to sync commands to new guild %s (id=%s)", guild.name, guild.id)

    async def close(self) -> None:
        try:
            await self.db.close()
        except Exception:
            log.exception("Error closing database")
        await super().close()


bot = EclipseBot()


@bot.command(name="approve")
@commands.is_owner()
async def approve_guild(ctx: commands.Context, guild_id: int) -> None:
    guild = bot.get_guild(guild_id)
    if guild is None:
        await ctx.reply(f"I am not currently in a server with ID `{guild_id}`.", mention_author=False)
        return
    await bot.db.set_guild_approval(guild_id, True, ctx.author.id)
    await bot.notify_guild_owner_of_decision(guild_id, guild.owner_id, approved=True)
    await ctx.reply(f"Approved {guild.name} (`{guild_id}`).", mention_author=False)


@bot.command(name="unapprove")
@commands.is_owner()
async def unapprove_guild(ctx: commands.Context, guild_id: int) -> None:
    await bot.db.set_guild_approval(guild_id, False, ctx.author.id)
    guild = bot.get_guild(guild_id)
    label = f"{guild.name} (`{guild_id}`)" if guild else f"`{guild_id}`"
    await ctx.reply(f"Approval revoked for {label}.", mention_author=False)


@bot.tree.command(name="ping", description="Check if the bot is alive")
async def ping(interaction: discord.Interaction):
    latency_ms = round(bot.latency * 1000)
    await interaction.response.send_message(f"Pong! `{latency_ms}ms`")


@bot.tree.command(name="approve", description="Approve this server for bot commands")
async def approve_current_guild(interaction: discord.Interaction):
    if interaction.user.id != COMMAND_USER_ID:
        await interaction.response.send_message("Only the authorized user can approve servers.", ephemeral=True)
        return
    if interaction.guild is None:
        await interaction.response.send_message("Run this command inside the server you want to approve.", ephemeral=True)
        return

    await bot.db.set_guild_approval(interaction.guild.id, True, interaction.user.id)
    await bot.notify_guild_owner_of_decision(
        interaction.guild.id,
        interaction.guild.owner_id,
        approved=True,
    )
    await interaction.response.send_message(
        f"Approved **{interaction.guild.name}**. Commands are now enabled here.",
        ephemeral=True,
    )


@bot.tree.command(name="unapprove", description="Disable bot commands in this server")
async def unapprove_current_guild(interaction: discord.Interaction):
    if interaction.user.id != COMMAND_USER_ID:
        await interaction.response.send_message("Only the authorized user can revoke approval.", ephemeral=True)
        return
    if interaction.guild is None:
        await interaction.response.send_message("Run this command inside the server to lock.", ephemeral=True)
        return

    await bot.db.set_guild_approval(interaction.guild.id, False, interaction.user.id)
    await interaction.response.send_message(
        f"Approval revoked for **{interaction.guild.name}**. Commands are now locked here.",
        ephemeral=True,
    )


dev_group = app_commands.Group(name="dev", description="Developer tools")


@dev_group.command(name="approval-queue", description="Review pending server approval requests")
async def approval_queue(interaction: discord.Interaction):
    if interaction.user.id != COMMAND_USER_ID:
        await interaction.response.send_message("Only the authorized user can review approvals.", ephemeral=True)
        return

    requests = await bot.db.get_pending_guild_approvals()
    if not requests:
        await interaction.response.send_message("There are no pending server approvals.", ephemeral=True)
        return

    view = ApprovalQueueView(bot, requests)
    await interaction.response.send_message(
        "Each server has its own Accept and Decline buttons.",
        embed=view.build_embed(),
        view=view,
        ephemeral=True,
    )


bot.tree.add_command(dev_group)


@bot.tree.command(name="sync", description="Owner-only: re-sync slash commands")
async def sync_cmd(interaction: discord.Interaction):
    if interaction.user.id != COMMAND_USER_ID:
        await interaction.response.send_message("Only the authorized user can run this.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    for guild in bot.guilds:
        await bot.sync_guild_commands(guild)
    await bot.clear_global_commands()
    await interaction.followup.send(
        f"Refreshed instant commands in {len(bot.guilds)} servers.",
        ephemeral=True,
    )


if __name__ == "__main__":
    bot.run(TOKEN, log_handler=None)
