"""SQLite persistence layer for the Telegram bot SaaS platform.

This module is intentionally independent from Telegram handlers so its data rules
can be tested without contacting Telegram or any payment provider.
"""
from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS tenants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_key_hash TEXT NOT NULL UNIQUE,
    bot_username TEXT NOT NULL DEFAULT '',
    owner_username TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS tenant_admins (
    tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    telegram_user_id INTEGER NOT NULL,
    username TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'admin' CHECK (role IN ('owner', 'admin')),
    PRIMARY KEY (tenant_id, telegram_user_id)
);
CREATE TABLE IF NOT EXISTS target_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    chat_id INTEGER NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_id, chat_id)
);
CREATE TABLE IF NOT EXISTS player_group_bindings (
    tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    player_user_id INTEGER NOT NULL,
    group_id INTEGER NOT NULL REFERENCES target_groups(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (tenant_id, player_user_id, group_id)
);
CREATE INDEX IF NOT EXISTS idx_player_bindings_player
    ON player_group_bindings(tenant_id, player_user_id);
CREATE TABLE IF NOT EXISTS bets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    player_user_id INTEGER NOT NULL,
    group_id INTEGER REFERENCES target_groups(id) ON DELETE SET NULL,
    game_code TEXT NOT NULL,
    issue TEXT NOT NULL,
    selection TEXT NOT NULL,
    amount REAL NOT NULL CHECK (amount > 0),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'won', 'lost', 'cancelled')),
    profit REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_id, game_code, issue, player_user_id, selection)
);
CREATE TABLE IF NOT EXISTS payment_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    player_user_id INTEGER NOT NULL,
    amount_usdt REAL NOT NULL CHECK (amount_usdt > 0),
    tx_reference TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected')),
    reviewed_by INTEGER,
    reviewed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS subscriptions (
    tenant_id INTEGER PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
    starts_at TEXT,
    expires_at TEXT,
    daily_price_usdt REAL NOT NULL DEFAULT 68 CHECK (daily_price_usdt > 0),
    status TEXT NOT NULL DEFAULT 'inactive'
        CHECK (status IN ('inactive', 'active', 'expired'))
);
CREATE TRIGGER IF NOT EXISTS enforce_player_two_groups
BEFORE INSERT ON player_group_bindings
WHEN (SELECT COUNT(*) FROM player_group_bindings
      WHERE tenant_id = NEW.tenant_id
        AND player_user_id = NEW.player_user_id) >= 2
BEGIN
    SELECT RAISE(ABORT, 'player_group_limit_exceeded');
END;
"""


def token_fingerprint(token: str) -> str:
    """Return a one-way identifier; never store the raw Telegram bot token here."""
    if not token or ":" not in token:
        raise ValueError("A valid-looking bot token is required")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class SQLiteStore:
    def __init__(self, path: str | Path = "junshi.sqlite3"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL")
        return conn

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def create_tenant(self, bot_token: str, bot_username: str = "",
                      owner_username: str = "") -> int:
        key = token_fingerprint(bot_token)
        with self._db() as conn:
            conn.execute(
                "INSERT INTO tenants(bot_key_hash, bot_username, owner_username) "
                "VALUES (?, ?, ?) ON CONFLICT(bot_key_hash) DO UPDATE SET "
                "bot_username=excluded.bot_username, owner_username=excluded.owner_username",
                (key, bot_username, owner_username.lstrip("@").lower()),
            )
            row = conn.execute("SELECT id FROM tenants WHERE bot_key_hash=?", (key,)).fetchone()
            conn.execute("INSERT OR IGNORE INTO subscriptions(tenant_id) VALUES (?)", (row["id"],))
            return int(row["id"])

    def add_admin(self, tenant_id: int, user_id: int, username: str, role: str = "admin") -> None:
        with self._db() as conn:
            conn.execute(
                "INSERT INTO tenant_admins(tenant_id, telegram_user_id, username, role) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(tenant_id, telegram_user_id) "
                "DO UPDATE SET username=excluded.username, role=excluded.role",
                (tenant_id, user_id, username.lstrip("@").lower(), role),
            )

    def add_group(self, tenant_id: int, chat_id: int, title: str = "") -> int:
        with self._db() as conn:
            conn.execute(
                "INSERT INTO target_groups(tenant_id, chat_id, title) VALUES (?, ?, ?) "
                "ON CONFLICT(tenant_id, chat_id) DO UPDATE SET title=excluded.title, enabled=1",
                (tenant_id, chat_id, title),
            )
            row = conn.execute(
                "SELECT id FROM target_groups WHERE tenant_id=? AND chat_id=?",
                (tenant_id, chat_id),
            ).fetchone()
            return int(row["id"])

    def bind_player_group(self, tenant_id: int, player_user_id: int, group_id: int) -> None:
        with self._db() as conn:
            group = conn.execute(
                "SELECT 1 FROM target_groups WHERE id=? AND tenant_id=? AND enabled=1",
                (group_id, tenant_id),
            ).fetchone()
            if not group:
                raise ValueError("group_not_available_for_tenant")
            conn.execute(
                "INSERT OR IGNORE INTO player_group_bindings(tenant_id, player_user_id, group_id) "
                "VALUES (?, ?, ?)",
                (tenant_id, player_user_id, group_id),
            )

    def list_player_groups(self, tenant_id: int, player_user_id: int) -> list[int]:
        with self._db() as conn:
            rows = conn.execute(
                "SELECT b.group_id FROM player_group_bindings b "
                "JOIN target_groups g ON g.id=b.group_id "
                "WHERE b.tenant_id=? AND b.player_user_id=? AND g.enabled=1 ORDER BY b.created_at",
                (tenant_id, player_user_id),
            ).fetchall()
            return [int(row["group_id"]) for row in rows]

    def create_payment_request(self, tenant_id: int, player_user_id: int,
                               amount_usdt: float, tx_reference: str = "") -> int:
        with self._db() as conn:
            cur = conn.execute(
                "INSERT INTO payment_requests(tenant_id, player_user_id, amount_usdt, tx_reference) "
                "VALUES (?, ?, ?, ?)",
                (tenant_id, player_user_id, amount_usdt, tx_reference.strip()),
            )
            return int(cur.lastrowid)

    def review_payment(self, request_id: int, reviewer_id: int, approved: bool) -> None:
        status = "approved" if approved else "rejected"
        with self._db() as conn:
            cur = conn.execute(
                "UPDATE payment_requests SET status=?, reviewed_by=?, reviewed_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND status='pending'",
                (status, reviewer_id, request_id),
            )
            if cur.rowcount != 1:
                raise ValueError("payment_request_not_pending")
