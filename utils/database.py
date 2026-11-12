from __future__ import annotations

import aiosqlite
from typing import Optional, Iterable, Any
import logging
import os
import sqlite3
import tempfile

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (guild_id, key)
);

CREATE TABLE IF NOT EXISTS guild_access (
    guild_id TEXT PRIMARY KEY,
    approved INTEGER NOT NULL DEFAULT 0,
    approved_by TEXT,
    approved_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS guild_approval_requests (
    guild_id TEXT PRIMARY KEY,
    guild_name TEXT NOT NULL DEFAULT '',
    owner_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    notice_sent_at TIMESTAMP,
    decided_by TEXT,
    decided_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS staff_roles (
    guild_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    role_id TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (guild_id, scope, role_id)
);

CREATE INDEX IF NOT EXISTS idx_staff_roles_guild_scope
ON staff_roles (guild_id, scope);

CREATE TABLE IF NOT EXISTS ticket_categories (
    guild_id TEXT NOT NULL,
    server_key TEXT NOT NULL,
    ticket_type TEXT NOT NULL,
    category_id TEXT NOT NULL,
    PRIMARY KEY (guild_id, server_key, ticket_type)
);

CREATE TABLE IF NOT EXISTS ticket_staff_roles (
    guild_id TEXT NOT NULL,
    ticket_type TEXT NOT NULL,
    role_id TEXT NOT NULL,
    PRIMARY KEY (guild_id, ticket_type, role_id)
);

CREATE TABLE IF NOT EXISTS tickets (
    guild_id TEXT NOT NULL,
    ticket_id INTEGER NOT NULL,
    channel_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    server_key TEXT NOT NULL,
    ticket_type TEXT NOT NULL,
    opened_by TEXT,
    closed_by TEXT,
    claimed_by TEXT,
    close_requested_by TEXT,
    close_requested_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_closed INTEGER DEFAULT 0,
    PRIMARY KEY (guild_id, ticket_id)
);

CREATE TABLE IF NOT EXISTS ticket_panel_state (
    guild_id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS giveaways (
    guild_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    host_id TEXT NOT NULL,
    server_key TEXT NOT NULL,
    prize TEXT NOT NULL,
    winner_count INTEGER NOT NULL,
    required_role_id TEXT,
    min_account_age_days INTEGER NOT NULL DEFAULT 7,
    ends_at TIMESTAMP NOT NULL,
    ended INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (guild_id, message_id)
);

CREATE TABLE IF NOT EXISTS giveaway_entries (
    guild_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    entered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (guild_id, message_id, user_id)
);

CREATE TABLE IF NOT EXISTS giveaway_blacklist (
    guild_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    reason TEXT,
    added_by TEXT NOT NULL,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS polls (
    guild_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    host_id TEXT NOT NULL,
    title TEXT NOT NULL,
    choices TEXT NOT NULL,
    ends_at TIMESTAMP NOT NULL,
    ended INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (guild_id, message_id)
);

CREATE TABLE IF NOT EXISTS poll_votes (
    guild_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    choice_index INTEGER NOT NULL,
    voted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (guild_id, message_id, user_id)
);
"""


class Database:
    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = os.environ.get("DB_PATH", "eclipse.db")
        if not db_path.strip():
            raise ValueError("DB_PATH must be a non-empty database file path.")
        self.db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        if self._conn:
            return
        absolute_path = os.path.abspath(self.db_path)
        parent_dir = os.path.dirname(absolute_path)
        try:
            os.makedirs(parent_dir, exist_ok=True)
        except OSError as exc:
            raise RuntimeError(
                f"Cannot create the database directory {parent_dir!r} for DB_PATH "
                f"{self.db_path!r}. Check the path and directory permissions."
            ) from exc

        if os.path.isdir(absolute_path):
            raise RuntimeError(
                f"DB_PATH {self.db_path!r} points to a directory, not a database file."
            )
        if (
            os.name == "posix"
            and (absolute_path == "/data" or absolute_path.startswith("/data/"))
            and not os.path.ismount("/data")
        ):
            raise RuntimeError(
                f"DB_PATH {self.db_path!r} is under /data, but /data is not a mounted "
                "volume. Attach a Railway Volume at /data or change DB_PATH to match "
                "the volume's mount path."
            )

        try:
            with tempfile.TemporaryFile(dir=parent_dir):
                pass
        except OSError as exc:
            raise RuntimeError(
                f"The database directory {parent_dir!r} for DB_PATH {self.db_path!r} "
                "is not writable. Check that the Railway Volume is mounted and writable."
            ) from exc

        try:
            self._conn = await aiosqlite.connect(self.db_path)
        except sqlite3.OperationalError as exc:
            raise RuntimeError(
                f"SQLite could not open DB_PATH {self.db_path!r}. Verify the database "
                f"file and parent directory {parent_dir!r} are accessible and writable."
            ) from exc
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SCHEMA_SQL)
        await self._conn.commit()

        cur = await self._conn.execute("PRAGMA table_info('guild_approval_requests');")
        approval_columns = {row["name"] for row in await cur.fetchall()}
        await cur.close()
        if "guild_name" not in approval_columns:
            await self._conn.execute(
                "ALTER TABLE guild_approval_requests ADD COLUMN guild_name TEXT NOT NULL DEFAULT '';"
            )
            await self._conn.commit()
        if "notice_sent_at" not in approval_columns:
            await self._conn.execute(
                "ALTER TABLE guild_approval_requests ADD COLUMN notice_sent_at TIMESTAMP;"
            )
            await self._conn.commit()

        # --- guild_settings schema migration ---
        try:
            cur = await self._conn.execute("PRAGMA table_info('guild_settings');")
            rows = await cur.fetchall()
            await cur.close()
            cols = [r['name'] for r in rows]
        except Exception:
            cols = []

        expected = {'guild_id', 'key', 'value'}
        existing = set(cols)
        if not expected.issubset(existing):
            logging.warning("Detected old guild_settings schema, attempting migration: %s", existing)
            await self._conn.execute("ALTER TABLE guild_settings RENAME TO guild_settings_old;")
            await self._conn.execute(
                "CREATE TABLE IF NOT EXISTS guild_settings (guild_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY (guild_id, key));"
            )
            if 'value' in existing:
                try:
                    await self._conn.execute(
                        "INSERT INTO guild_settings (guild_id, key, value) SELECT guild_id, 'default', value FROM guild_settings_old;"
                    )
                except Exception:
                    logging.exception("Failed to migrate guild_settings_old -> guild_settings")
            await self._conn.commit()

        # --- tickets column migrations ---
        try:
            cur = await self._conn.execute("PRAGMA table_info('tickets');")
            ticket_rows = await cur.fetchall()
            await cur.close()
            ticket_cols = {r['name'] for r in ticket_rows}
        except Exception:
            ticket_cols = set()

        if ticket_cols:
            if 'close_requested_at' not in ticket_cols:
                try:
                    await self._conn.execute("ALTER TABLE tickets ADD COLUMN close_requested_at TIMESTAMP;")
                    await self._conn.commit()
                except Exception:
                    pass

            if 'claimed_by' not in ticket_cols:
                try:
                    await self._conn.execute("ALTER TABLE tickets ADD COLUMN claimed_by TEXT;")
                    await self._conn.commit()
                    logging.info("Migrated tickets table: added claimed_by column")
                except Exception:
                    logging.exception("Failed to add claimed_by column to tickets")

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if not self._conn:
            raise RuntimeError("Database not connected")
        return self._conn

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        await self.conn.execute(sql, params)
        await self.conn.commit()

    async def executemany(self, sql: str, params_seq: Iterable[tuple[Any, ...]]) -> None:
        params_list = list(params_seq)
        if not params_list:
            return
        await self.conn.executemany(sql, params_list)
        await self.conn.commit()

    async def fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[aiosqlite.Row]:
        cur = await self.conn.execute(sql, params)
        rows = await cur.fetchall()
        await cur.close()
        return rows

    async def fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> Optional[aiosqlite.Row]:
        cur = await self.conn.execute(sql, params)
        row = await cur.fetchone()
        await cur.close()
        return row

    async def queue_guild_approval(self, guild_id: int, guild_name: str, owner_id: int) -> None:
        await self.conn.execute(
            """
            INSERT INTO guild_approval_requests
                (guild_id, guild_name, owner_id, status, requested_at, notice_sent_at, decided_by, decided_at)
            VALUES (?, ?, ?, 'pending', CURRENT_TIMESTAMP, NULL, NULL, NULL)
            ON CONFLICT(guild_id) DO UPDATE SET
                guild_name = excluded.guild_name,
                owner_id = excluded.owner_id,
                status = 'pending',
                requested_at = CASE
                    WHEN guild_approval_requests.status = 'pending'
                    THEN guild_approval_requests.requested_at
                    ELSE CURRENT_TIMESTAMP
                END,
                notice_sent_at = CASE
                    WHEN guild_approval_requests.status = 'pending'
                    THEN guild_approval_requests.notice_sent_at
                    ELSE NULL
                END,
                decided_by = CASE
                    WHEN guild_approval_requests.status = 'pending'
                    THEN guild_approval_requests.decided_by
                    ELSE NULL
                END,
                decided_at = CASE
                    WHEN guild_approval_requests.status = 'pending'
                    THEN guild_approval_requests.decided_at
                    ELSE NULL
                END;
            """,
            (str(guild_id), guild_name, str(owner_id)),
        )
        await self.conn.execute(
            """
            INSERT INTO guild_access (guild_id, approved, approved_by, approved_at)
            VALUES (?, 0, NULL, NULL)
            ON CONFLICT(guild_id) DO UPDATE SET
                approved = 0,
                approved_by = NULL,
                approved_at = NULL;
            """,
            (str(guild_id),),
        )
        await self.conn.commit()

    async def get_guild_approval_request(self, guild_id: int) -> Optional[aiosqlite.Row]:
        return await self.fetchone(
            "SELECT guild_id, guild_name, owner_id, status, requested_at "
            "FROM guild_approval_requests WHERE guild_id = ?;",
            (str(guild_id),),
        )

    async def get_pending_guild_approvals(self) -> list[aiosqlite.Row]:
        return await self.fetchall(
            """
            SELECT guild_id, guild_name, owner_id, requested_at
            FROM guild_approval_requests
            WHERE status = 'pending'
            ORDER BY requested_at ASC;
            """
        )

    async def update_guild_approval_name(self, guild_id: int, guild_name: str) -> None:
        await self.execute(
            "UPDATE guild_approval_requests SET guild_name = ? WHERE guild_id = ?;",
            (guild_name, str(guild_id)),
        )

    async def claim_guild_approval_notice(self, guild_id: int) -> bool:
        cur = await self.conn.execute(
            """
            UPDATE guild_approval_requests
            SET notice_sent_at = CURRENT_TIMESTAMP
            WHERE guild_id = ? AND status = 'pending' AND notice_sent_at IS NULL;
            """,
            (str(guild_id),),
        )
        claimed = cur.rowcount == 1
        await cur.close()
        await self.conn.commit()
        return claimed

    async def decide_guild_approval(self, guild_id: int, approved: bool, decided_by: int) -> None:
        status = "approved" if approved else "declined"
        cur = await self.conn.execute(
            """
            UPDATE guild_approval_requests
            SET status = ?, decided_by = ?, decided_at = CURRENT_TIMESTAMP
            WHERE guild_id = ? AND status = 'pending';
            """,
            (status, str(decided_by), str(guild_id)),
        )
        updated = cur.rowcount
        await cur.close()
        if updated != 1:
            raise ValueError(f"Guild {guild_id} does not have a pending approval request.")

        await self.conn.execute(
            """
            INSERT INTO guild_access (guild_id, approved, approved_by, approved_at)
            VALUES (?, ?, ?, CASE WHEN ? = 1 THEN CURRENT_TIMESTAMP ELSE NULL END)
            ON CONFLICT(guild_id) DO UPDATE SET
                approved = excluded.approved,
                approved_by = excluded.approved_by,
                approved_at = excluded.approved_at;
            """,
            (str(guild_id), int(approved), str(decided_by), int(approved)),
        )
        await self.conn.commit()

    async def ensure_guild_pending(self, guild_id: int) -> None:
        await self.execute(
            "INSERT OR IGNORE INTO guild_access (guild_id, approved) VALUES (?, 0);",
            (str(guild_id),),
        )

    async def is_guild_approved(self, guild_id: int) -> bool:
        row = await self.fetchone(
            "SELECT approved FROM guild_access WHERE guild_id = ?;",
            (str(guild_id),),
        )
        return bool(row and row["approved"])

    async def set_guild_approval(self, guild_id: int, approved: bool, approved_by: int) -> None:
        await self.execute(
            """
            INSERT INTO guild_access (guild_id, approved, approved_by, approved_at)
            VALUES (?, ?, ?, CASE WHEN ? = 1 THEN CURRENT_TIMESTAMP ELSE NULL END)
            ON CONFLICT(guild_id) DO UPDATE SET
                approved = excluded.approved,
                approved_by = excluded.approved_by,
                approved_at = excluded.approved_at;
            """,
            (str(guild_id), int(approved), str(approved_by), int(approved)),
        )
        await self.execute(
            """
            UPDATE guild_approval_requests
            SET status = ?, decided_by = ?, decided_at = CURRENT_TIMESTAMP
            WHERE guild_id = ? AND status = 'pending';
            """,
            ("approved" if approved else "declined", str(approved_by), str(guild_id)),
        )
