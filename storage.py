"""SQLite persistence layer for the Telegram bot SaaS platform.

This module is intentionally independent from Telegram handlers so its data rules
can be tested without contacting Telegram or any payment provider.
"""
from __future__ import annotations

import hashlib
import sqlite3
from decimal import Decimal, InvalidOperation
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
CREATE TABLE IF NOT EXISTS tenant_pricing (
    tenant_id INTEGER PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
    agreed_price REAL CHECK (agreed_price IS NULL OR agreed_price >= 0),
    currency TEXT NOT NULL DEFAULT 'USDT',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS player_registrations (
    tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    player_user_id INTEGER NOT NULL,
    username TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected')),
    approved_by INTEGER,
    approved_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (tenant_id, player_user_id)
);

CREATE TABLE IF NOT EXISTS renewal_terms (
    tenant_id INTEGER PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
    amount TEXT NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USDT',
    period_seconds INTEGER NOT NULL CHECK (period_seconds >= 3600),
    receive_address TEXT NOT NULL,
    terms_text TEXT NOT NULL DEFAULT '',
    notice_text TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS renewal_agreements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    player_user_id INTEGER NOT NULL,
    terms_version INTEGER NOT NULL,
    amount TEXT NOT NULL,
    currency TEXT NOT NULL,
    period_seconds INTEGER NOT NULL,
    receive_address TEXT NOT NULL,
    terms_snapshot TEXT NOT NULL,
    notice_snapshot TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'offered'
        CHECK (status IN ('offered', 'accepted', 'active', 'cancelled', 'rejected')),
    offered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    accepted_at TEXT,
    activated_at TEXT,
    cancelled_at TEXT,
    wallet_address TEXT NOT NULL DEFAULT '',
    authorization_ref TEXT NOT NULL DEFAULT '',
    next_due_at TEXT,
    UNIQUE (tenant_id, id)
);
CREATE INDEX IF NOT EXISTS idx_renewal_due
    ON renewal_agreements(status, next_due_at);
CREATE TABLE IF NOT EXISTS renewal_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agreement_id INTEGER NOT NULL REFERENCES renewal_agreements(id) ON DELETE CASCADE,
    idempotency_key TEXT NOT NULL UNIQUE,
    due_at TEXT NOT NULL,
    amount TEXT NOT NULL,
    currency TEXT NOT NULL,
    receive_address TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'submitted', 'confirmed', 'failed')),
    txid TEXT UNIQUE,
    error_detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
            conn.execute("INSERT OR IGNORE INTO tenant_pricing(tenant_id) VALUES (?)", (row["id"],))
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

    def set_tenant_price(self, tenant_id: int, amount: float, currency: str = "USDT") -> None:
        """Set a negotiated price for the admin's private records; it never gates player access."""
        if amount < 0:
            raise ValueError("price_must_not_be_negative")
        currency = currency.strip().upper()
        if not currency or len(currency) > 12:
            raise ValueError("invalid_currency")
        with self._db() as conn:
            cur = conn.execute(
                "UPDATE tenant_pricing SET agreed_price=?, currency=?, updated_at=CURRENT_TIMESTAMP "
                "WHERE tenant_id=?",
                (amount, currency, tenant_id),
            )
            if cur.rowcount != 1:
                raise ValueError("tenant_not_found")

    def get_tenant_price(self, tenant_id: int) -> dict | None:
        with self._db() as conn:
            row = conn.execute(
                "SELECT agreed_price, currency, updated_at FROM tenant_pricing WHERE tenant_id=?",
                (tenant_id,),
            ).fetchone()
            return dict(row) if row else None

    def register_player(self, tenant_id: int, player_user_id: int, username: str = "") -> str:
        """Create a registration request. Access is granted only after admin verification."""
        with self._db() as conn:
            conn.execute(
                "INSERT INTO player_registrations(tenant_id, player_user_id, username, status) "
                "VALUES (?, ?, ?, 'pending') ON CONFLICT(tenant_id, player_user_id) "
                "DO UPDATE SET username=excluded.username "
                "WHERE player_registrations.status != 'approved'",
                (tenant_id, player_user_id, username.lstrip("@").lower()),
            )
            row = conn.execute(
                "SELECT status FROM player_registrations WHERE tenant_id=? AND player_user_id=?",
                (tenant_id, player_user_id),
            ).fetchone()
            return str(row["status"])

    def verify_player(self, tenant_id: int, player_user_id: int, admin_user_id: int,
                      approved: bool = True) -> None:
        """Admin verification; approved registrations receive unlimited access."""
        status = "approved" if approved else "rejected"
        with self._db() as conn:
            cur = conn.execute(
                "UPDATE player_registrations SET status=?, approved_by=?, "
                "approved_at=CURRENT_TIMESTAMP WHERE tenant_id=? AND player_user_id=? "
                "AND status='pending'",
                (status, admin_user_id, tenant_id, player_user_id),
            )
            if cur.rowcount != 1:
                raise ValueError("registration_not_pending")

    def player_has_access(self, tenant_id: int, player_user_id: int) -> bool:
        """Approved means unrestricted access; there is deliberately no expiry check."""
        with self._db() as conn:
            row = conn.execute(
                "SELECT 1 FROM player_registrations WHERE tenant_id=? "
                "AND player_user_id=? AND status='approved'",
                (tenant_id, player_user_id),
            ).fetchone()
            return row is not None

    def create_bet(self, tenant_id: int, player_user_id: int, group_id: int | None,
                   game_code: str, issue: str, selection: str, amount: float) -> int:
        with self._db() as conn:
            if group_id is not None:
                group = conn.execute(
                    "SELECT 1 FROM target_groups WHERE id=? AND tenant_id=? AND enabled=1",
                    (group_id, tenant_id),
                ).fetchone()
                if not group:
                    raise ValueError("group_not_available_for_tenant")
            cur = conn.execute(
                "INSERT INTO bets(tenant_id, player_user_id, group_id, game_code, issue, "
                "selection, amount) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (tenant_id, player_user_id, group_id, game_code, issue, selection, amount),
            )
            return int(cur.lastrowid)


    @staticmethod
    def _decimal_amount(amount: str | int | float | Decimal) -> str:
        try:
            value = Decimal(str(amount))
        except (InvalidOperation, ValueError):
            raise ValueError("invalid_amount") from None
        if not value.is_finite() or value <= 0 or value.as_tuple().exponent < -6:
            raise ValueError("invalid_amount")
        return format(value.normalize(), "f")

    @staticmethod
    def _require_admin(conn: sqlite3.Connection, tenant_id: int, admin_user_id: int) -> None:
        row = conn.execute(
            "SELECT 1 FROM tenant_admins WHERE tenant_id=? AND telegram_user_id=?",
            (tenant_id, admin_user_id),
        ).fetchone()
        if not row:
            raise PermissionError("tenant_admin_required")

    def configure_renewal_terms(self, tenant_id: int, admin_user_id: int,
                                amount: str | int | float, period_seconds: int,
                                receive_address: str, terms_text: str = "",
                                notice_text: str = "", currency: str = "USDT") -> int:
        """Save an admin-negotiated recurring plan; changing it increments its version."""
        amount_text = self._decimal_amount(amount)
        currency = currency.strip().upper()
        address = receive_address.strip()
        if period_seconds < 3600 or period_seconds > 31536000:
            raise ValueError("period_seconds_out_of_range")
        if not address or len(address) > 128 or not currency or len(currency) > 12:
            raise ValueError("invalid_renewal_terms")
        with self._db() as conn:
            self._require_admin(conn, tenant_id, admin_user_id)
            conn.execute(
                """INSERT INTO renewal_terms
                   (tenant_id, amount, currency, period_seconds, receive_address,
                    terms_text, notice_text, version, enabled)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1)
                   ON CONFLICT(tenant_id) DO UPDATE SET
                    amount=excluded.amount, currency=excluded.currency,
                    period_seconds=excluded.period_seconds,
                    receive_address=excluded.receive_address,
                    terms_text=excluded.terms_text, notice_text=excluded.notice_text,
                    version=renewal_terms.version+1, enabled=1,
                    updated_at=CURRENT_TIMESTAMP""",
                (tenant_id, amount_text, currency, period_seconds, address,
                 terms_text, notice_text),
            )
            row = conn.execute(
                "SELECT version FROM renewal_terms WHERE tenant_id=?", (tenant_id,)
            ).fetchone()
            return int(row["version"])

    def create_renewal_offer(self, tenant_id: int, admin_user_id: int,
                             player_user_id: int) -> int:
        """Create an immutable snapshot so later price/address edits cannot alter an offer."""
        with self._db() as conn:
            self._require_admin(conn, tenant_id, admin_user_id)
            terms = conn.execute(
                "SELECT * FROM renewal_terms WHERE tenant_id=? AND enabled=1", (tenant_id,)
            ).fetchone()
            if not terms:
                raise ValueError("renewal_terms_not_configured")
            cur = conn.execute(
                """INSERT INTO renewal_agreements
                   (tenant_id, player_user_id, terms_version, amount, currency,
                    period_seconds, receive_address, terms_snapshot, notice_snapshot)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (tenant_id, player_user_id, terms["version"], terms["amount"],
                 terms["currency"], terms["period_seconds"], terms["receive_address"],
                 terms["terms_text"], terms["notice_text"]),
            )
            return int(cur.lastrowid)

    def accept_renewal_offer(self, agreement_id: int, player_user_id: int) -> None:
        """Record the player's Telegram consent; this alone does not authorize a wallet charge."""
        with self._db() as conn:
            cur = conn.execute(
                """UPDATE renewal_agreements SET status='accepted', accepted_at=CURRENT_TIMESTAMP
                   WHERE id=? AND player_user_id=? AND status='offered'""",
                (agreement_id, player_user_id),
            )
            if cur.rowcount != 1:
                raise ValueError("offer_not_available")

    def activate_renewal(self, agreement_id: int, player_user_id: int,
                         wallet_address: str, authorization_ref: str,
                         next_due_at: str) -> None:
        """Activate only after the wallet/contract provider confirms a scoped authorization."""
        if not wallet_address.strip() or not authorization_ref.strip() or not next_due_at.strip():
            raise ValueError("wallet_authorization_required")
        with self._db() as conn:
            cur = conn.execute(
                """UPDATE renewal_agreements SET status='active', activated_at=CURRENT_TIMESTAMP,
                   wallet_address=?, authorization_ref=?, next_due_at=?
                   WHERE id=? AND player_user_id=? AND status='accepted'""",
                (wallet_address.strip(), authorization_ref.strip(), next_due_at.strip(),
                 agreement_id, player_user_id),
            )
            if cur.rowcount != 1:
                raise ValueError("accepted_offer_required")

    def cancel_renewal(self, agreement_id: int, player_user_id: int) -> None:
        with self._db() as conn:
            cur = conn.execute(
                """UPDATE renewal_agreements SET status='cancelled', cancelled_at=CURRENT_TIMESTAMP
                   WHERE id=? AND player_user_id=? AND status IN ('accepted', 'active', 'offered')""",
                (agreement_id, player_user_id),
            )
            if cur.rowcount != 1:
                raise ValueError("agreement_not_cancellable")

    def create_renewal_attempt(self, agreement_id: int, idempotency_key: str,
                               due_at: str) -> int:
        """Create one durable charge attempt; unique key prevents duplicate processing."""
        if not idempotency_key.strip() or not due_at.strip():
            raise ValueError("invalid_renewal_attempt")
        with self._db() as conn:
            agreement = conn.execute(
                """SELECT amount, currency, receive_address FROM renewal_agreements
                   WHERE id=? AND status='active'""", (agreement_id,)
            ).fetchone()
            if not agreement:
                raise ValueError("active_agreement_required")
            conn.execute(
                """INSERT INTO renewal_attempts
                   (agreement_id, idempotency_key, due_at, amount, currency, receive_address)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (agreement_id, idempotency_key, due_at, agreement["amount"],
                 agreement["currency"], agreement["receive_address"]),
            )
            row = conn.execute(
                "SELECT id FROM renewal_attempts WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            return int(row["id"])

    def update_renewal_attempt(self, attempt_id: int, status: str,
                               txid: str | None = None, error_detail: str = "") -> None:
        if status not in {"submitted", "confirmed", "failed"}:
            raise ValueError("invalid_attempt_status")
        with self._db() as conn:
            cur = conn.execute(
                """UPDATE renewal_attempts SET status=?, txid=COALESCE(?, txid),
                   error_detail=?, updated_at=CURRENT_TIMESTAMP
                   WHERE id=? AND status IN ('queued', 'submitted')""",
                (status, txid, error_detail[:1000], attempt_id),
            )
            if cur.rowcount != 1:
                raise ValueError("attempt_not_updatable")

    def settle_bet(self, bet_id: int, won: bool, profit: float = 0) -> None:
        status = "won" if won else "lost"
        with self._db() as conn:
            cur = conn.execute(
                "UPDATE bets SET status=?, profit=? WHERE id=? AND status='pending'",
                (status, profit if won else -abs(profit), bet_id),
            )
            if cur.rowcount != 1:
                raise ValueError("bet_not_pending")
