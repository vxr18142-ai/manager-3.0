# cogs/antinuke.py  v5
#
# Anti-nuke protection cog:
#   - Access role     — only members with this role (or the hard-coded owner)
#                       can use ANY /antinuke command
#   - Whitelist roles  — members with ANY of these roles bypass every check
#   - Blacklist roles  — receiving a blacklisted role (or one with dangerous
#                        perms) immediately quarantines the member
#   - Auto-quarantine  — strips ALL roles + applies the quarantine role
#                        (the antinuke access role is preserved on the target)
#   - Anti-link        — deletes any message containing a URL/invite
#   - Anti-spam        — detects fast spam (5 msgs in 5 s) or repeated messages
#                        (same message 5×); deletes all spam msgs + times out
#   - Censor words     — blocks specific words; deletes message + 5-min timeout
#   - Logs channel     — every action logged as a rich embed
#
# Slash commands:
#   /antinuke access   set <role>   ← OWNER ONLY (hard-coded user ID)
#   /antinuke access   remove       ← OWNER ONLY
#   /antinuke status
#   /antinuke whitelist-role add | remove | list
#   /antinuke whitelist-user  add | remove
#   /antinuke blacklist-role  add | remove | list
#   /antinuke quarantine-role <role>
#   /antinuke logs            <channel>
#   /antinuke antilink        <enabled>
#   /antinuke antispam        <enabled>
#   /antinuke censor          add | remove | list
#   /antinuke unquarantine    <member>
#
# All command responses are visible to everyone in the channel.
#
# Requires: aiosqlite, discord.py 2.x

from __future__ import annotations

import asyncio
import collections
import datetime
import json
import logging
import os
import re
import time
from typing import Optional

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger("VertexBot")

DB_PATH: str = os.environ.get("DB_PATH", "eclipse.db")

# ── Constants ──────────────────────────────────────────────────────────────────

THEME_COLOR = int(os.getenv("THEME_COLOR", str(0xFF0000)))
SERVER_NAME = os.getenv("SERVER_NAME", "Twix")

# The only user ID that can run /antinuke access set / remove
OWNER_ID: int = 1127835258839113768

# How old an audit log entry can be and still be matched (seconds)
AUDIT_LOG_WINDOW = 8

# No automatic dangerous-perm scanning — left empty intentionally
DANGEROUS_PERMS: tuple = ()

# ── Anti-spam settings ─────────────────────────────────────────────────────────
SPAM_WINDOW_SECONDS  = 5    # rolling window to count messages in
SPAM_MAX_MESSAGES    = 5    # max messages allowed in that window
SPAM_SAME_MSG_COUNT  = 5    # trigger if the same text is sent this many times
SPAM_TIMEOUT_MINUTES = 2    # how long to time out the spammer

# {(guild_id, user_id): deque[(sent_at, content, message_id, channel_id)]}
_spam_tracker: dict[tuple[str, str], collections.deque] = {}

# ── Censor settings ────────────────────────────────────────────────────────────
CENSOR_TIMEOUT_MINUTES = 5  # timeout length for using a censored word

LINK_RE = re.compile(
    r"(?<!\w)"
    r"("
    r"https?://[^\s<>\"']+"
    r"|www\.[a-zA-Z0-9\-]+\.[a-zA-Z]{2,}(?:[/?#][^\s]*)?"
    r"|discord(?:app)?\.(?:com|gg)/[^\s<>\"']+"
    r"|[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.[a-zA-Z]{2,}(?:[/?#][^\s]*)?"
    r")"
    r"(?!\w)",
    re.IGNORECASE,
)


# ── DB bootstrap ───────────────────────────────────────────────────────────────

async def _ensure_tables() -> None:
    db_dir = os.path.dirname(os.path.abspath(DB_PATH))
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS antinuke_config (
                guild_id            TEXT NOT NULL PRIMARY KEY,
                quarantine_role_id  TEXT,
                logs_channel_id     TEXT,
                antilink_enabled    INTEGER NOT NULL DEFAULT 1,
                antispam_enabled    INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS antinuke_access_config (
                guild_id       TEXT NOT NULL PRIMARY KEY,
                access_role_id TEXT
            );
            CREATE TABLE IF NOT EXISTS antinuke_whitelist_roles (
                guild_id  TEXT NOT NULL,
                role_id   TEXT NOT NULL,
                PRIMARY KEY (guild_id, role_id)
            );
            CREATE TABLE IF NOT EXISTS antinuke_blacklist_roles (
                guild_id  TEXT NOT NULL,
                role_id   TEXT NOT NULL,
                PRIMARY KEY (guild_id, role_id)
            );
            CREATE TABLE IF NOT EXISTS antinuke_whitelist_users (
                guild_id  TEXT NOT NULL,
                user_id   TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS antinuke_quarantine_history (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id        TEXT NOT NULL,
                member_id       TEXT NOT NULL,
                role_ids_json   TEXT NOT NULL,
                quarantined_at  TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS antinuke_censor_words (
                guild_id  TEXT NOT NULL,
                word      TEXT NOT NULL,
                PRIMARY KEY (guild_id, word)
            );
        """)
        await db.commit()
        # Add antispam_enabled column to existing databases that predate it
        try:
            await db.execute(
                "ALTER TABLE antinuke_config ADD COLUMN antispam_enabled INTEGER NOT NULL DEFAULT 1"
            )
            await db.commit()
        except Exception:
            pass


# ── DB helpers ─────────────────────────────────────────────────────────────────

async def _fetchone(q: str, p: tuple = ()):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(q, p)
        return await cur.fetchone()

async def _fetchall(q: str, p: tuple = ()):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(q, p)
        return await cur.fetchall()

async def _execute(q: str, p: tuple = ()):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(q, p)
        await db.commit()

async def _get_config(guild_id: str):
    return await _fetchone(
        "SELECT * FROM antinuke_config WHERE guild_id=?", (guild_id,)
    )

async def _upsert_config(guild_id: str, **kwargs) -> None:
    if not kwargs:
        await _execute(
            "INSERT OR IGNORE INTO antinuke_config (guild_id) VALUES (?)",
            (guild_id,),
        )
        return
    cols   = ", ".join(kwargs.keys())
    blanks = ", ".join("?" * len(kwargs))
    sets   = ", ".join(f"{c}=excluded.{c}" for c in kwargs)
    params = (guild_id, *kwargs.values())
    await _execute(
        f"INSERT INTO antinuke_config (guild_id, {cols})"
        f" VALUES (?, {blanks})"
        f" ON CONFLICT(guild_id) DO UPDATE SET {sets}",
        params,
    )

# — access role —

async def _get_access_role_id(guild_id: str) -> Optional[str]:
    row = await _fetchone(
        "SELECT access_role_id FROM antinuke_access_config WHERE guild_id=?",
        (guild_id,),
    )
    return row["access_role_id"] if row else None

async def _set_access_role_id(guild_id: str, role_id: str) -> None:
    await _execute(
        "INSERT INTO antinuke_access_config (guild_id, access_role_id)"
        " VALUES (?,?)"
        " ON CONFLICT(guild_id) DO UPDATE SET access_role_id=excluded.access_role_id",
        (guild_id, role_id),
    )

async def _remove_access_role(guild_id: str) -> None:
    await _execute(
        "DELETE FROM antinuke_access_config WHERE guild_id=?",
        (guild_id,),
    )

# — whitelist roles —
async def _get_whitelist_roles(guild_id: str) -> list[str]:
    rows = await _fetchall(
        "SELECT role_id FROM antinuke_whitelist_roles WHERE guild_id=?", (guild_id,)
    )
    return [r["role_id"] for r in rows]

async def _add_whitelist_role(guild_id: str, role_id: str) -> None:
    await _execute(
        "INSERT OR IGNORE INTO antinuke_whitelist_roles (guild_id, role_id) VALUES (?,?)",
        (guild_id, role_id),
    )

async def _remove_whitelist_role(guild_id: str, role_id: str) -> None:
    await _execute(
        "DELETE FROM antinuke_whitelist_roles WHERE guild_id=? AND role_id=?",
        (guild_id, role_id),
    )

# — blacklist roles —
async def _get_blacklist_roles(guild_id: str) -> list[str]:
    rows = await _fetchall(
        "SELECT role_id FROM antinuke_blacklist_roles WHERE guild_id=?", (guild_id,)
    )
    return [r["role_id"] for r in rows]

async def _add_blacklist_role(guild_id: str, role_id: str) -> None:
    await _execute(
        "INSERT OR IGNORE INTO antinuke_blacklist_roles (guild_id, role_id) VALUES (?,?)",
        (guild_id, role_id),
    )

async def _remove_blacklist_role(guild_id: str, role_id: str) -> None:
    await _execute(
        "DELETE FROM antinuke_blacklist_roles WHERE guild_id=? AND role_id=?",
        (guild_id, role_id),
    )

async def _is_blacklisted_role(guild_id: str, role_id: int) -> bool:
    row = await _fetchone(
        "SELECT 1 FROM antinuke_blacklist_roles WHERE guild_id=? AND role_id=?",
        (guild_id, str(role_id)),
    )
    return row is not None

# — whitelist users —
async def _is_whitelisted_user(guild_id: str, user_id: str) -> bool:
    row = await _fetchone(
        "SELECT 1 FROM antinuke_whitelist_users WHERE guild_id=? AND user_id=?",
        (guild_id, user_id),
    )
    return row is not None

# — censor words —
async def _get_censor_words(guild_id: str) -> list[str]:
    rows = await _fetchall(
        "SELECT word FROM antinuke_censor_words WHERE guild_id=?", (guild_id,)
    )
    return [r["word"] for r in rows]

async def _add_censor_word(guild_id: str, word: str) -> None:
    await _execute(
        "INSERT OR IGNORE INTO antinuke_censor_words (guild_id, word) VALUES (?,?)",
        (guild_id, word.lower()),
    )

async def _remove_censor_word(guild_id: str, word: str) -> None:
    await _execute(
        "DELETE FROM antinuke_censor_words WHERE guild_id=? AND word=?",
        (guild_id, word.lower()),
    )


# ── Access check ───────────────────────────────────────────────────────────────

async def _has_access(inter: discord.Interaction) -> bool:
    """
    Returns True if the interaction user is allowed to run antinuke commands.

    Allowed if:
      - User is the hard-coded OWNER_ID, OR
      - The guild has an access role set and the user has that role.
    """
    if inter.user.id == OWNER_ID:
        return True
    guild_id = str(inter.guild.id)
    access_role_id = await _get_access_role_id(guild_id)
    if not access_role_id:
        return False
    member = inter.guild.get_member(inter.user.id)
    if member is None:
        return False
    return any(str(r.id) == access_role_id for r in member.roles)


async def _deny(inter: discord.Interaction) -> None:
    """Send a public 'no access' error."""
    guild_id = str(inter.guild.id)
    access_role_id = await _get_access_role_id(guild_id)
    if access_role_id:
        role = inter.guild.get_role(int(access_role_id))
        role_str = role.mention if role else f"`{access_role_id}`"
        desc = f"You need the {role_str} role to use antinuke commands."
    else:
        desc = "No antinuke access role has been set yet. The owner must run `/antinuke access set`."
    await inter.response.send_message(
        embed=_err("🚫 Access Denied", desc),
        ephemeral=False,
    )


# ── Embed helpers ──────────────────────────────────────────────────────────────

def _log_embed(title: str, desc: str, color: Optional[discord.Color] = None) -> discord.Embed:
    e = discord.Embed(
        title=title,
        description=desc,
        color=color or THEME_COLOR,
        timestamp=discord.utils.utcnow(),
    )
    e.set_footer(text=f"{SERVER_NAME} · Anti-Nuke")
    return e

def _err(title: str, desc: str = "") -> discord.Embed:
    return discord.Embed(
        title=title, description=desc or None,
        color=discord.Color.red(), timestamp=discord.utils.utcnow(),
    ).set_footer(text=f"{SERVER_NAME} · Anti-Nuke")

def _ok(title: str, desc: str = "") -> discord.Embed:
    return discord.Embed(
        title=title, description=desc or None,
        color=discord.Color.green(), timestamp=discord.utils.utcnow(),
    ).set_footer(text=f"{SERVER_NAME} · Anti-Nuke")


# — quarantine history —

async def _save_quarantine(guild_id: str, member_id: str, roles: list[discord.Role]) -> None:
    role_ids = json.dumps([str(r.id) for r in roles])
    now = datetime.datetime.utcnow().isoformat()
    await _execute(
        "INSERT INTO antinuke_quarantine_history (guild_id, member_id, role_ids_json, quarantined_at)"
        " VALUES (?,?,?,?)",
        (guild_id, member_id, role_ids, now),
    )

async def _get_latest_quarantine(guild_id: str, member_id: str) -> Optional[list[str]]:
    row = await _fetchone(
        "SELECT role_ids_json FROM antinuke_quarantine_history"
        " WHERE guild_id=? AND member_id=? ORDER BY id DESC LIMIT 1",
        (guild_id, member_id),
    )
    if row is None:
        return None
    return json.loads(row["role_ids_json"])

async def _clear_quarantine(guild_id: str, member_id: str) -> None:
    await _execute(
        "DELETE FROM antinuke_quarantine_history WHERE guild_id=? AND member_id=?",
        (guild_id, member_id),
    )


# ── Internal helpers ───────────────────────────────────────────────────────────

async def _send_log(guild: discord.Guild, config, embed: discord.Embed) -> None:
    if not config or not config["logs_channel_id"]:
        return
    ch = guild.get_channel(int(config["logs_channel_id"]))
    if not isinstance(ch, discord.TextChannel):
        return
    try:
        await ch.send(embed=embed)
    except Exception:
        log.warning("antinuke: could not post log in channel %s", config["logs_channel_id"])

async def _quarantine_member(
    guild: discord.Guild,
    member: discord.Member,
    config,
    reason: str,
) -> tuple[bool, list[discord.Role]]:
    """
    Strip all roles from member and apply the quarantine role.
    The antinuke access role (if any) is preserved on the member.
    """
    roles_had = [r for r in member.roles if not r.is_default()]
    if not config or not config["quarantine_role_id"]:
        log.warning("antinuke: quarantine_role_id not set for guild %s", guild.id)
        return False, roles_had
    q_role = guild.get_role(int(config["quarantine_role_id"]))
    if not q_role:
        log.warning("antinuke: quarantine role not found in guild %s", guild.id)
        return False, roles_had

    # Build the role list to assign: quarantine role + preserve access role
    new_roles: list[discord.Role] = [q_role]
    access_role_id = await _get_access_role_id(str(guild.id))
    if access_role_id:
        access_role = guild.get_role(int(access_role_id))
        # If the member currently has the access role, keep it
        if access_role and any(r.id == access_role.id for r in member.roles):
            new_roles.append(access_role)

    try:
        await member.edit(roles=new_roles, reason=f"Anti-Nuke: {reason}")
        # Save original roles so unquarantine can restore them
        await _save_quarantine(str(guild.id), str(member.id), roles_had)
        return True, roles_had
    except discord.Forbidden:
        log.warning("antinuke: no permission to quarantine %s in guild %s", member, guild.id)
        return False, roles_had
    except discord.HTTPException as exc:
        log.warning("antinuke: HTTPException quarantining %s: %s", member, exc)
        return False, roles_had

async def _is_bypassed(guild: discord.Guild, member: discord.Member, guild_id: str) -> bool:
    """Returns True if this member is exempt from all anti-nuke actions."""
    if member.bot:
        return True
    # Check per-user whitelist
    if await _is_whitelisted_user(guild_id, str(member.id)):
        return True
    # Check whitelist roles — member is bypassed if they have ANY whitelisted role
    wl_role_ids = await _get_whitelist_roles(guild_id)
    if wl_role_ids:
        member_role_ids = {str(r.id) for r in member.roles}
        if member_role_ids & set(wl_role_ids):
            return True
    return False


# ── Cog ────────────────────────────────────────────────────────────────────────

class AntiNukeCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        guild    = after.guild
        if not await self.bot.is_guild_approved(guild.id):
            return
        guild_id = str(guild.id)

        added_roles = set(after.roles) - set(before.roles)
        if not added_roles:
            return

        # Only trigger on roles that are explicitly blacklisted by the admin.
        # No automatic dangerous-perm scanning — the blacklist is the sole control.
        blacklisted: list[discord.Role] = []
        for role in added_roles:
            if await _is_blacklisted_role(guild_id, role.id):
                blacklisted.append(role)

        if not blacklisted:
            return

        config = await _get_config(guild_id)

        # ── Find who assigned the role via audit log ───────────────────────────
        # Wait briefly so Discord has time to write the entry.
        await asyncio.sleep(1)

        cutoff = discord.utils.utcnow() - datetime.timedelta(seconds=AUDIT_LOG_WINDOW)
        giver: Optional[discord.Member] = None
        try:
            async for entry in guild.audit_logs(
                limit=15,
                action=discord.AuditLogAction.member_role_update,
            ):
                # Only consider entries that happened within the last AUDIT_LOG_WINDOW seconds
                if entry.created_at < cutoff:
                    break
                if entry.target and entry.target.id == after.id:
                    if entry.user and not entry.user.bot:
                        giver = guild.get_member(entry.user.id)
                    break
        except discord.Forbidden:
            log.warning("antinuke: missing View Audit Log permission in guild %s", guild.id)
        except Exception as exc:
            log.warning("antinuke: audit log lookup failed: %s", exc)

        role_names = ", ".join(f"**{r.name}**" for r in blacklisted)

        # ── Always strip the blacklisted role(s) from the RECEIVER ────────────
        # Remove only the blacklisted roles; leave all other roles intact.
        # The access role is never in the blacklist so it is naturally preserved.
        receiver_stripped = False
        try:
            safe_roles = [r for r in after.roles if r not in blacklisted]
            await after.edit(roles=safe_roles, reason="Anti-Nuke: removed blacklisted role(s)")
            receiver_stripped = True
        except discord.Forbidden:
            log.warning("antinuke: no permission to remove blacklisted role from receiver %s", after)
        except discord.HTTPException as exc:
            log.warning("antinuke: failed to remove blacklisted role from receiver %s: %s", after, exc)

        if giver is None:
            await _send_log(guild, config, _log_embed(
                "⚠️ Blacklisted Role Assigned — Giver Unknown",
                f"**Receiver:** {after.mention}\n"
                f"**Blacklisted role(s):** {role_names}\n"
                f"**Role stripped from receiver:** {'✅ Yes' if receiver_stripped else '❌ Failed — check bot perms'}\n"
                f"**Note:** Could not identify who assigned the role within the "
                f"{AUDIT_LOG_WINDOW}s window. Ensure the bot has **View Audit Log** permission.",
                color=discord.Color.orange(),
            ))
            return

        # ── Bypass check on the GIVER ──────────────────────────────────────────
        if await _is_bypassed(guild, giver, guild_id):
            log.info("antinuke: giver %s is whitelisted — skipping quarantine", giver)
            await _send_log(guild, config, _log_embed(
                "ℹ️ Blacklisted Role Stripped — Giver Whitelisted",
                f"**Receiver:** {after.mention} (`{after.id}`)\n"
                f"**Blacklisted role(s):** {role_names}\n"
                f"**Role stripped from receiver:** {'✅ Yes' if receiver_stripped else '❌ Failed — check bot perms'}\n"
                f"**Giver:** {giver.mention} (`{giver.id}`) — whitelisted, not quarantined.",
                color=discord.Color.blurple(),
            ))
            return

        reason  = f"Assigned blacklisted role(s) to {after}: {role_names}"
        success, roles_had = await _quarantine_member(guild, giver, config, reason)

        embed = _log_embed(
            "🔒 Role Giver Quarantined" if success else "⚠️ Quarantine Failed",
            f"**Quarantined:** {giver.mention} (`{giver}` · `{giver.id}`)\n"
            f"**Gave blacklisted role(s):** {role_names}\n"
            f"**To member:** {after.mention} (`{after.id}`)\n"
            f"**Role stripped from receiver:** {'✅ Yes' if receiver_stripped else '❌ Failed — check bot perms'}\n"
            f"**Roles stripped from giver:** {', '.join(r.mention for r in roles_had) if roles_had else '—'}\n"
            f"**Quarantine role:** {'✅ Applied' if success else '❌ Failed — check bot perms & role hierarchy'}",
            color=discord.Color.orange() if success else discord.Color.red(),
        )
        await _send_log(guild, config, embed)
        log.info(
            "antinuke: quarantined giver=%s receiver=%s role(s)=%s guild=%s success=%s receiver_stripped=%s",
            giver, after, role_names, guild.id, success, receiver_stripped,
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return
        guild    = message.guild
        if not await self.bot.is_guild_approved(guild.id):
            return
        guild_id = str(guild.id)
        member   = message.author
        if not isinstance(member, discord.Member):
            return

        config = await _get_config(guild_id)
        bypassed = await _is_bypassed(guild, member, guild_id)

        # ── Anti-link ──────────────────────────────────────────────────────────
        if config and config["antilink_enabled"] and not bypassed:
            if LINK_RE.search(message.content):
                deleted = False
                try:
                    await message.delete()
                    deleted = True
                except discord.NotFound:
                    deleted = True
                except (discord.Forbidden, discord.HTTPException) as exc:
                    log.warning("antinuke: could not delete link msg %s: %s", message.id, exc)

                safe = discord.utils.escape_markdown(message.content[:400])
                if deleted:
                    try:
                        await message.channel.send(
                            f"{member.mention} ❌ Links are not allowed in this server.",
                            delete_after=5,
                        )
                    except Exception:
                        pass
                    embed = _log_embed(
                        "🔗 Link Deleted",
                        f"**Author:** {member.mention} (`{member}` · `{member.id}`)\n"
                        f"**Channel:** {message.channel.mention}\n"
                        f"**Message:** ||{safe}||",
                        color=discord.Color.yellow(),
                    )
                else:
                    embed = _log_embed(
                        "⚠️ Link Detected — Delete Failed",
                        f"**Author:** {member.mention} (`{member}` · `{member.id}`)\n"
                        f"**Channel:** {message.channel.mention}\n"
                        f"**Message:** ||{safe}||\n"
                        f"**Note:** Bot lacks permission to delete in this channel.",
                        color=discord.Color.red(),
                    )
                await _send_log(guild, config, embed)
                return  # already handled this message

        # ── Anti-spam ──────────────────────────────────────────────────────────
        if config and config["antispam_enabled"] and not bypassed:
            key = (guild_id, str(member.id))
            if key not in _spam_tracker:
                _spam_tracker[key] = collections.deque()

            now = time.monotonic()
            _spam_tracker[key].append((now, message.content, message.id, message.channel.id))

            # Drop entries outside the rolling window
            while _spam_tracker[key] and now - _spam_tracker[key][0][0] > SPAM_WINDOW_SECONDS:
                _spam_tracker[key].popleft()

            bucket = _spam_tracker[key]
            msg_count  = len(bucket)
            same_count = sum(1 for _, c, _, _ in bucket if c == message.content)
            triggered  = msg_count >= SPAM_MAX_MESSAGES or same_count >= SPAM_SAME_MSG_COUNT

            if triggered:
                # Snapshot and clear tracker so we don't double-trigger
                spam_entries = list(bucket)
                _spam_tracker[key].clear()

                # Delete all tracked spam messages
                deleted_count = 0
                for _, _, mid, cid in spam_entries:
                    ch = guild.get_channel(cid)
                    if ch is None:
                        continue
                    try:
                        msg_obj = await ch.fetch_message(mid)
                        await msg_obj.delete()
                        deleted_count += 1
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        pass

                # Timeout the member for SPAM_TIMEOUT_MINUTES
                timed_out = False
                timeout_until = discord.utils.utcnow() + datetime.timedelta(minutes=SPAM_TIMEOUT_MINUTES)
                try:
                    await member.timeout(timeout_until, reason="Anti-Nuke: spam detected")
                    timed_out = True
                except discord.Forbidden:
                    log.warning("antinuke: no permission to timeout %s in guild %s", member, guild.id)
                except discord.HTTPException as exc:
                    log.warning("antinuke: timeout failed for %s: %s", member, exc)

                # Notify in channel
                try:
                    await message.channel.send(
                        f"{member.mention} ⛔ Spam detected — your messages have been deleted "
                        f"and you have been timed out for **{SPAM_TIMEOUT_MINUTES} minute(s)**.",
                        delete_after=8,
                    )
                except Exception:
                    pass

                reason_str = (
                    f"{'Same message' if same_count >= SPAM_SAME_MSG_COUNT else 'Fast spam'} — "
                    f"{msg_count} message(s) in {SPAM_WINDOW_SECONDS}s window"
                )
                embed = _log_embed(
                    "🚫 Spam Detected",
                    f"**Member:** {member.mention} (`{member}` · `{member.id}`)\n"
                    f"**Reason:** {reason_str}\n"
                    f"**Messages deleted:** {deleted_count}/{len(spam_entries)}\n"
                    f"**Timeout:** {'✅ ' + str(SPAM_TIMEOUT_MINUTES) + ' min' if timed_out else '❌ Failed — check bot perms'}",
                    color=discord.Color.orange(),
                )
                await _send_log(guild, config, embed)
                log.info(
                    "antinuke: spam from %s in guild %s — %s msgs, timed_out=%s",
                    member, guild.id, msg_count, timed_out,
                )

        # ── Censor words ───────────────────────────────────────────────────────
        if not bypassed:
            censor_words = await _get_censor_words(guild_id)
            if censor_words:
                content_lower = message.content.lower()
                # Strip all whitespace so "n i g g e r" → "nigger" still matches
                content_squished = re.sub(r'\s+', '', content_lower)
                hit = next(
                    (w for w in censor_words
                     if w in content_lower or w.replace(' ', '') in content_squished),
                    None,
                )
                if hit:
                    # Delete the message
                    try:
                        await message.delete()
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        pass

                    # Timeout the member for CENSOR_TIMEOUT_MINUTES
                    timed_out = False
                    timeout_until = discord.utils.utcnow() + datetime.timedelta(minutes=CENSOR_TIMEOUT_MINUTES)
                    try:
                        await member.timeout(timeout_until, reason=f"Anti-Nuke: used censored word \"{hit}\"")
                        timed_out = True
                    except discord.Forbidden:
                        log.warning("antinuke: no permission to timeout %s for censor in guild %s", member, guild.id)
                    except discord.HTTPException as exc:
                        log.warning("antinuke: censor timeout failed for %s: %s", member, exc)

                    # Notify in channel
                    try:
                        await message.channel.send(
                            f"{member.mention} 🤐 Your message was removed for containing a banned word. "
                            f"You have been timed out for **{CENSOR_TIMEOUT_MINUTES} minutes**.",
                            delete_after=8,
                        )
                    except Exception:
                        pass

                    embed = _log_embed(
                        "🤐 Censored Word Used",
                        f"**Member:** {member.mention} (`{member}` · `{member.id}`)\n"
                        f"**Channel:** {message.channel.mention}\n"
                        f"**Matched word:** ||{discord.utils.escape_markdown(hit)}||\n"
                        f"**Timeout:** {'✅ ' + str(CENSOR_TIMEOUT_MINUTES) + ' min' if timed_out else '❌ Failed — check bot perms'}",
                        color=discord.Color.dark_red(),
                    )
                    await _send_log(guild, config, embed)
                    log.info(
                        "antinuke: censor hit word=%r member=%s guild=%s timed_out=%s",
                        hit, member, guild.id, timed_out,
                    )


# ── Slash command groups ───────────────────────────────────────────────────────

antinuke_grp = app_commands.Group(
    name="antinuke",
    description="Anti-nuke protection configuration.",
)
# No default_member_permissions — access is controlled manually via _has_access()

# Sub-groups
access_grp = app_commands.Group(
    name="access",
    description="Set which role can use antinuke commands (owner only).",
)
antinuke_grp.add_command(access_grp)

wl_role_grp = app_commands.Group(
    name="whitelist-role",
    description="Manage roles that bypass all anti-nuke checks.",
)
antinuke_grp.add_command(wl_role_grp)

bl_role_grp = app_commands.Group(
    name="blacklist-role",
    description="Manage roles that always trigger quarantine when assigned.",
)
antinuke_grp.add_command(bl_role_grp)

wl_user_grp = app_commands.Group(
    name="whitelist-user",
    description="Manage per-user anti-nuke exemptions.",
)
antinuke_grp.add_command(wl_user_grp)

censor_grp = app_commands.Group(
    name="censor",
    description="Manage words that are banned in this server.",
)
antinuke_grp.add_command(censor_grp)


# ── /antinuke access set / remove  (OWNER ONLY) ───────────────────────────────

@access_grp.command(
    name="set",
    description="Set the role that grants access to all antinuke commands. (Owner only)",
)
@app_commands.describe(role="Role to grant antinuke command access")
async def _access_set(inter: discord.Interaction, role: discord.Role):
    if inter.user.id != OWNER_ID:
        await inter.response.send_message(
            embed=_err("🚫 Owner Only", "Only the bot owner can change the antinuke access role."),
            ephemeral=False,
        )
        return

    await _set_access_role_id(str(inter.guild.id), str(role.id))
    await inter.response.send_message(
        embed=_ok(
            "✅ Access Role Set",
            f"{role.mention} now has access to all `/antinuke` commands.\n"
            f"Only members with this role (or the owner) can configure antinuke.",
        ),
        ephemeral=False,
    )
    config = await _get_config(str(inter.guild.id))
    await _send_log(inter.guild, config, _log_embed(
        "⚙️ Config — Access Role Set",
        f"{role.mention} set as antinuke access role by {inter.user.mention}.",
        color=discord.Color.blurple(),
    ))


@access_grp.command(
    name="remove",
    description="Remove the antinuke access role (locks commands to owner only). (Owner only)",
)
async def _access_remove(inter: discord.Interaction):
    if inter.user.id != OWNER_ID:
        await inter.response.send_message(
            embed=_err("🚫 Owner Only", "Only the bot owner can change the antinuke access role."),
            ephemeral=False,
        )
        return

    await _remove_access_role(str(inter.guild.id))
    await inter.response.send_message(
        embed=_ok(
            "✅ Access Role Removed",
            "The antinuke access role has been cleared.\n"
            "Only the owner can now use `/antinuke` commands.",
        ),
        ephemeral=False,
    )
    config = await _get_config(str(inter.guild.id))
    await _send_log(inter.guild, config, _log_embed(
        "⚙️ Config — Access Role Removed",
        f"Antinuke access role cleared by {inter.user.mention}.",
        color=discord.Color.orange(),
    ))


# ── /antinuke status ───────────────────────────────────────────────────────────

@antinuke_grp.command(name="status", description="Show the current anti-nuke configuration.")
async def _status(inter: discord.Interaction):
    if not await _has_access(inter):
        await _deny(inter)
        return

    guild_id = str(inter.guild.id)
    config   = await _get_config(guild_id)

    def _ch_str(cid):
        if not cid:
            return "*Not set*"
        c = inter.guild.get_channel(int(cid))
        return c.mention if c else f"*Deleted channel (`{cid}`)*"

    def _role_str(rid):
        if not rid:
            return "*Not set*"
        r = inter.guild.get_role(int(rid))
        return r.mention if r else f"*Deleted role (`{rid}`)*"

    wl_ids = await _get_whitelist_roles(guild_id)
    bl_ids = await _get_blacklist_roles(guild_id)
    access_role_id = await _get_access_role_id(guild_id)

    wl_mentions = (
        " ".join(_role_str(rid) for rid in wl_ids) if wl_ids else "*None*"
    )
    bl_mentions = (
        " ".join(_role_str(rid) for rid in bl_ids) if bl_ids else "*None*"
    )

    embed = discord.Embed(
        title="🛡️ Anti-Nuke Status",
        color=THEME_COLOR,
        timestamp=discord.utils.utcnow(),
    ).set_footer(text=f"{SERVER_NAME} · Anti-Nuke")

    q_role      = _role_str(config["quarantine_role_id"]) if config else "*Not set*"
    logs_ch     = _ch_str(config["logs_channel_id"]) if config else "*Not set*"
    antilink    = ("✅ Enabled" if config and config["antilink_enabled"] else "❌ Disabled")
    antispam    = ("✅ Enabled" if config and config["antispam_enabled"]  else "❌ Disabled")
    access_role = _role_str(access_role_id) if access_role_id else "*Not set (owner only)*"

    embed.add_field(name="🔑 Access Role",    value=access_role,  inline=True)
    embed.add_field(name="Quarantine Role",   value=q_role,       inline=True)
    embed.add_field(name="Logs Channel",      value=logs_ch,      inline=True)
    censor_words   = await _get_censor_words(guild_id)
    censor_count   = f"{len(censor_words)} word(s)" if censor_words else "*None set*"

    embed.add_field(name="Anti-Link",         value=antilink,     inline=True)
    embed.add_field(name="Anti-Spam",         value=antispam,     inline=True)
    embed.add_field(name="🤐 Censor Words",   value=censor_count, inline=True)
    embed.add_field(name="✅ Whitelist Roles", value=wl_mentions,  inline=False)
    embed.add_field(name="🚫 Blacklist Roles", value=bl_mentions,  inline=False)
    await inter.response.send_message(embed=embed, ephemeral=False)


# ── /antinuke whitelist-role add / remove / list ───────────────────────────────

@wl_role_grp.command(name="add", description="Add a role that bypasses all anti-nuke checks.")
@app_commands.describe(role="Role to whitelist")
async def _wl_role_add(inter: discord.Interaction, role: discord.Role):
    if not await _has_access(inter):
        await _deny(inter)
        return
    await _add_whitelist_role(str(inter.guild.id), str(role.id))
    await inter.response.send_message(
        embed=_ok("✅ Whitelist Role Added", f"{role.mention} now bypasses all anti-nuke checks."),
        ephemeral=False,
    )
    config = await _get_config(str(inter.guild.id))
    await _send_log(inter.guild, config, _log_embed(
        "⚙️ Config — Whitelist Role Added",
        f"{role.mention} added by {inter.user.mention}.",
    ))

@wl_role_grp.command(name="remove", description="Remove a role from the whitelist.")
@app_commands.describe(role="Role to remove")
async def _wl_role_remove(inter: discord.Interaction, role: discord.Role):
    if not await _has_access(inter):
        await _deny(inter)
        return
    await _remove_whitelist_role(str(inter.guild.id), str(role.id))
    await inter.response.send_message(
        embed=_ok("✅ Whitelist Role Removed", f"{role.mention} is no longer whitelisted."),
        ephemeral=False,
    )
    config = await _get_config(str(inter.guild.id))
    await _send_log(inter.guild, config, _log_embed(
        "⚙️ Config — Whitelist Role Removed",
        f"{role.mention} removed by {inter.user.mention}.",
    ))

@wl_role_grp.command(name="list", description="Show all whitelisted roles.")
async def _wl_role_list(inter: discord.Interaction):
    if not await _has_access(inter):
        await _deny(inter)
        return
    guild_id = str(inter.guild.id)
    role_ids = await _get_whitelist_roles(guild_id)

    if not role_ids:
        embed = _err("No Whitelist Roles", "Add roles with `/antinuke whitelist-role add`.")
    else:
        lines = []
        for rid in role_ids:
            r = inter.guild.get_role(int(rid))
            lines.append(f"• {r.mention}" if r else f"• *Deleted role (`{rid}`)*")
        embed = discord.Embed(
            title=f"✅ Whitelist Roles ({len(role_ids)})",
            description="\n".join(lines),
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        ).set_footer(text=f"{SERVER_NAME} · Anti-Nuke")

    await inter.response.send_message(embed=embed, ephemeral=False)


# ── /antinuke blacklist-role add / remove / list ───────────────────────────────

@bl_role_grp.command(name="add", description="Add a role that triggers quarantine when assigned.")
@app_commands.describe(role="Role to blacklist — any member given this role will be quarantined")
async def _bl_role_add(inter: discord.Interaction, role: discord.Role):
    if not await _has_access(inter):
        await _deny(inter)
        return
    await _add_blacklist_role(str(inter.guild.id), str(role.id))
    await inter.response.send_message(
        embed=_ok(
            "🚫 Blacklist Role Added",
            f"{role.mention} — any member given this role will be immediately quarantined.",
        ),
        ephemeral=False,
    )
    config = await _get_config(str(inter.guild.id))
    await _send_log(inter.guild, config, _log_embed(
        "⚙️ Config — Blacklist Role Added",
        f"{role.mention} added by {inter.user.mention}.",
        color=discord.Color.red(),
    ))

@bl_role_grp.command(name="remove", description="Remove a role from the blacklist.")
@app_commands.describe(role="Role to remove from the blacklist")
async def _bl_role_remove(inter: discord.Interaction, role: discord.Role):
    if not await _has_access(inter):
        await _deny(inter)
        return
    await _remove_blacklist_role(str(inter.guild.id), str(role.id))
    await inter.response.send_message(
        embed=_ok("✅ Blacklist Role Removed", f"{role.mention} is no longer blacklisted."),
        ephemeral=False,
    )
    config = await _get_config(str(inter.guild.id))
    await _send_log(inter.guild, config, _log_embed(
        "⚙️ Config — Blacklist Role Removed",
        f"{role.mention} removed by {inter.user.mention}.",
    ))

@bl_role_grp.command(name="list", description="Show all blacklisted roles.")
async def _bl_role_list(inter: discord.Interaction):
    if not await _has_access(inter):
        await _deny(inter)
        return
    guild_id = str(inter.guild.id)
    role_ids = await _get_blacklist_roles(guild_id)

    if not role_ids:
        embed = _err("No Blacklist Roles", "Add roles with `/antinuke blacklist-role add`.")
    else:
        lines = []
        for rid in role_ids:
            r = inter.guild.get_role(int(rid))
            lines.append(f"• {r.mention}" if r else f"• *Deleted role (`{rid}`)*")
        embed = discord.Embed(
            title=f"🚫 Blacklist Roles ({len(role_ids)})",
            description="\n".join(lines),
            color=discord.Color.red(),
            timestamp=discord.utils.utcnow(),
        ).set_footer(text=f"{SERVER_NAME} · Anti-Nuke")

    await inter.response.send_message(embed=embed, ephemeral=False)


# ── /antinuke quarantine-role ──────────────────────────────────────────────────

@antinuke_grp.command(
    name="quarantine-role",
    description="Set the role given to quarantined members (give it very limited perms).",
)
@app_commands.describe(role="Role applied when a member is quarantined")
async def _quarantine_role(inter: discord.Interaction, role: discord.Role):
    if not await _has_access(inter):
        await _deny(inter)
        return
    await _upsert_config(str(inter.guild.id), quarantine_role_id=str(role.id))
    await inter.response.send_message(
        embed=_ok("✅ Quarantine Role Set", f"{role.mention} will be given to quarantined members."),
        ephemeral=False,
    )
    config = await _get_config(str(inter.guild.id))
    await _send_log(inter.guild, config, _log_embed(
        "⚙️ Config — Quarantine Role",
        f"Set to {role.mention} by {inter.user.mention}.",
    ))


# ── /antinuke logs ─────────────────────────────────────────────────────────────

@antinuke_grp.command(
    name="logs",
    description="Set the channel where anti-nuke actions are logged.",
)
@app_commands.describe(channel="Text channel for anti-nuke log embeds")
async def _logs(inter: discord.Interaction, channel: discord.TextChannel):
    if not await _has_access(inter):
        await _deny(inter)
        return
    await _upsert_config(str(inter.guild.id), logs_channel_id=str(channel.id))
    await inter.response.send_message(
        embed=_ok("✅ Logs Channel Set", f"Anti-nuke logs will be sent to {channel.mention}."),
        ephemeral=False,
    )
    config = await _get_config(str(inter.guild.id))
    await _send_log(inter.guild, config, _log_embed(
        "⚙️ Config — Logs Channel",
        f"Set to {channel.mention} by {inter.user.mention}.",
    ))


# ── /antinuke antilink ─────────────────────────────────────────────────────────

@antinuke_grp.command(
    name="antilink",
    description="Enable or disable the anti-link filter.",
)
@app_commands.describe(enabled="True = delete links, False = allow links")
async def _antilink(inter: discord.Interaction, enabled: bool):
    if not await _has_access(inter):
        await _deny(inter)
        return
    await _upsert_config(str(inter.guild.id), antilink_enabled=int(enabled))
    state = "enabled" if enabled else "disabled"
    await inter.response.send_message(
        embed=_ok(f"✅ Anti-Link {state.title()}", f"Link deletion is now **{state}**."),
        ephemeral=False,
    )
    config = await _get_config(str(inter.guild.id))
    await _send_log(inter.guild, config, _log_embed(
        "⚙️ Config — Anti-Link",
        f"**{state.title()}** by {inter.user.mention}.",
    ))


# ── /antinuke antispam ─────────────────────────────────────────────────────────

@antinuke_grp.command(
    name="antispam",
    description="Enable or disable the anti-spam filter (5 msgs in 5 s or same msg 5× → 2-min timeout).",
)
@app_commands.describe(enabled="True = detect spam, False = allow spam")
async def _antispam(inter: discord.Interaction, enabled: bool):
    if not await _has_access(inter):
        await _deny(inter)
        return
    await _upsert_config(str(inter.guild.id), antispam_enabled=int(enabled))
    state = "enabled" if enabled else "disabled"
    await inter.response.send_message(
        embed=_ok(
            f"✅ Anti-Spam {state.title()}",
            f"Spam detection is now **{state}**.\n"
            f"Trigger: **{SPAM_MAX_MESSAGES} messages in {SPAM_WINDOW_SECONDS}s** "
            f"or **same message {SPAM_SAME_MSG_COUNT}×** → "
            f"messages deleted + **{SPAM_TIMEOUT_MINUTES}-min timeout**.",
        ),
        ephemeral=False,
    )
    config = await _get_config(str(inter.guild.id))
    await _send_log(inter.guild, config, _log_embed(
        "⚙️ Config — Anti-Spam",
        f"**{state.title()}** by {inter.user.mention}.",
    ))


# ── /antinuke censor add / remove / list ──────────────────────────────────────

@censor_grp.command(name="add", description="Add a word to the censor list (non-whitelisted users get 5-min timeout).")
@app_commands.describe(word="Word or phrase to ban (case-insensitive)")
async def _censor_add(inter: discord.Interaction, word: str):
    if not await _has_access(inter):
        await _deny(inter)
        return
    word = word.strip().lower()
    if not word:
        await inter.response.send_message(embed=_err("❌ Invalid", "Word cannot be empty."), ephemeral=False)
        return
    await _add_censor_word(str(inter.guild.id), word)
    await inter.response.send_message(
        embed=_ok("🤐 Censor Word Added", f"||{discord.utils.escape_markdown(word)}|| is now banned."),
        ephemeral=False,
    )
    config = await _get_config(str(inter.guild.id))
    await _send_log(inter.guild, config, _log_embed(
        "⚙️ Config — Censor Word Added",
        f"Word added by {inter.user.mention}.",
        color=discord.Color.dark_red(),
    ))

@censor_grp.command(name="remove", description="Remove a word from the censor list.")
@app_commands.describe(word="Word or phrase to unban")
async def _censor_remove(inter: discord.Interaction, word: str):
    if not await _has_access(inter):
        await _deny(inter)
        return
    word = word.strip().lower()
    await _remove_censor_word(str(inter.guild.id), word)
    await inter.response.send_message(
        embed=_ok("✅ Censor Word Removed", f"||{discord.utils.escape_markdown(word)}|| is no longer banned."),
        ephemeral=False,
    )
    config = await _get_config(str(inter.guild.id))
    await _send_log(inter.guild, config, _log_embed(
        "⚙️ Config — Censor Word Removed",
        f"Word removed by {inter.user.mention}.",
    ))

@censor_grp.command(name="list", description="Show all censored words.")
async def _censor_list(inter: discord.Interaction):
    if not await _has_access(inter):
        await _deny(inter)
        return
    guild_id = str(inter.guild.id)
    words = await _get_censor_words(guild_id)
    if not words:
        embed = _err("No Censor Words", "Add words with `/antinuke censor add`.")
    else:
        lines = [f"• ||{discord.utils.escape_markdown(w)}||" for w in sorted(words)]
        embed = discord.Embed(
            title=f"🤐 Censor Words ({len(words)})",
            description="\n".join(lines),
            color=discord.Color.dark_red(),
            timestamp=discord.utils.utcnow(),
        ).set_footer(text=f"{SERVER_NAME} · Anti-Nuke")
    await inter.response.send_message(embed=embed, ephemeral=False)


# ── /antinuke whitelist-user add / remove ─────────────────────────────────────

@wl_user_grp.command(name="add", description="Exempt a specific user from all anti-nuke checks.")
@app_commands.describe(user="User to whitelist")
async def _wl_user_add(inter: discord.Interaction, user: discord.Member):
    if not await _has_access(inter):
        await _deny(inter)
        return
    await _execute(
        "INSERT OR IGNORE INTO antinuke_whitelist_users (guild_id, user_id) VALUES (?,?)",
        (str(inter.guild.id), str(user.id)),
    )
    await inter.response.send_message(
        embed=_ok("✅ User Whitelisted", f"{user.mention} is now exempt from all anti-nuke checks."),
        ephemeral=False,
    )
    config = await _get_config(str(inter.guild.id))
    await _send_log(inter.guild, config, _log_embed(
        "⚙️ Config — User Whitelist",
        f"{user.mention} **added** by {inter.user.mention}.",
    ))

@wl_user_grp.command(name="remove", description="Remove a user's anti-nuke exemption.")
@app_commands.describe(user="User to remove from the whitelist")
async def _wl_user_remove(inter: discord.Interaction, user: discord.Member):
    if not await _has_access(inter):
        await _deny(inter)
        return
    await _execute(
        "DELETE FROM antinuke_whitelist_users WHERE guild_id=? AND user_id=?",
        (str(inter.guild.id), str(user.id)),
    )
    await inter.response.send_message(
        embed=_ok("✅ User Removed", f"{user.mention} is no longer exempt."),
        ephemeral=False,
    )
    config = await _get_config(str(inter.guild.id))
    await _send_log(inter.guild, config, _log_embed(
        "⚙️ Config — User Whitelist",
        f"{user.mention} **removed** by {inter.user.mention}.",
    ))


# ── /antinuke unquarantine ────────────────────────────────────────────────────

@antinuke_grp.command(
    name="unquarantine",
    description="Remove a member's quarantine and restore their original roles.",
)
@app_commands.describe(member="The member to release from quarantine")
async def _unquarantine(inter: discord.Interaction, member: discord.Member):
    if not await _has_access(inter):
        await _deny(inter)
        return

    await inter.response.defer(ephemeral=False)

    guild_id = str(inter.guild.id)
    config   = await _get_config(guild_id)

    # Remove quarantine role if present
    q_role: Optional[discord.Role] = None
    if config and config["quarantine_role_id"]:
        q_role = inter.guild.get_role(int(config["quarantine_role_id"]))

    # Fetch saved original roles
    saved_ids = await _get_latest_quarantine(guild_id, str(member.id))

    restore: list[discord.Role] = []
    missing_ids: list[str] = []
    if saved_ids:
        for rid in saved_ids:
            r = inter.guild.get_role(int(rid))
            if r:
                restore.append(r)
            else:
                missing_ids.append(rid)

    # Build new role list: saved roles minus the quarantine role
    new_roles = [r for r in restore if q_role is None or r.id != q_role.id]

    try:
        await member.edit(roles=new_roles, reason=f"Anti-Nuke: unquarantined by {inter.user}")
        await _clear_quarantine(guild_id, str(member.id))
        restored_str = ", ".join(r.mention for r in new_roles) if new_roles else "*none saved*"
        lines = [f"✅ Quarantine removed from {member.mention}."]
        if new_roles:
            lines.append(f"**Roles restored:** {restored_str}")
        if missing_ids:
            lines.append(f"**Roles deleted (could not restore):** {len(missing_ids)}")
        if not saved_ids:
            lines.append("*No role history found — roles were not automatically restored.*")
        await inter.followup.send(
            embed=_ok("🔓 Member Unquarantined", "\n".join(lines)),
            ephemeral=False,
        )
        await _send_log(inter.guild, config, _log_embed(
            "🔓 Member Unquarantined",
            f"**Member:** {member.mention} (`{member}` · `{member.id}`)\n"
            f"**Unquarantined by:** {inter.user.mention}\n"
            f"**Roles restored:** {restored_str}",
            color=discord.Color.green(),
        ))
    except discord.Forbidden:
        await inter.followup.send(
            embed=_err("❌ Missing Permissions", "Bot cannot edit this member's roles. Check role hierarchy."),
            ephemeral=False,
        )
    except discord.HTTPException as exc:
        await inter.followup.send(
            embed=_err("❌ Error", str(exc)),
            ephemeral=False,
        )


# ── Setup / teardown ───────────────────────────────────────────────────────────

async def setup(bot: commands.Bot) -> None:
    await _ensure_tables()
    await bot.add_cog(AntiNukeCog(bot))
    if bot.tree.get_command("antinuke") is None:
        bot.tree.add_command(antinuke_grp)
    log.info("antinuke: cog loaded (v5 — access role, antispam, censor words, public responses)")


async def teardown(bot: commands.Bot) -> None:
    bot.tree.remove_command("antinuke")
    log.info("antinuke: cog unloaded")
