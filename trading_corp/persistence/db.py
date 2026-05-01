"""SQLite engine + schema migration.

Schema is portable to Postgres (no SQLite-specific syntax beyond AUTOINCREMENT,
which we avoid). Phase 4 cloud deployment swaps the connection factory.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

DEFAULT_DB_PATH = Path("data/trading_corp.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_event (
    id           INTEGER PRIMARY KEY,
    ts           TEXT    NOT NULL,            -- ISO-8601 UTC
    actor        TEXT    NOT NULL,            -- agent name or 'board'
    kind         TEXT    NOT NULL,            -- e.g. 'proposed_order','approved','rejected','filled','halt'
    payload_json TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_audit_event_ts ON audit_event(ts);

CREATE TABLE IF NOT EXISTS proposed_order (
    id           TEXT PRIMARY KEY,            -- uuid
    ts           TEXT NOT NULL,
    strategy     TEXT NOT NULL,
    symbol       TEXT NOT NULL,
    side         TEXT NOT NULL,
    qty          REAL NOT NULL,
    order_type   TEXT NOT NULL,
    limit_price  REAL,
    rationale    TEXT,
    status       TEXT NOT NULL,               -- proposed|risk_approved|risk_rejected|board_approved|board_rejected|filled|cancelled
    risk_reason  TEXT,
    board_reason TEXT,
    fill_price   REAL,
    fill_ts      TEXT,
    extra_json   TEXT
);
CREATE INDEX IF NOT EXISTS ix_proposed_order_status ON proposed_order(status);

CREATE TABLE IF NOT EXISTS position (
    id           INTEGER PRIMARY KEY,
    account      TEXT NOT NULL,
    symbol       TEXT NOT NULL,
    qty          REAL NOT NULL,
    avg_price    REAL NOT NULL,
    opened_ts    TEXT NOT NULL,
    extra_json   TEXT
);
CREATE INDEX IF NOT EXISTS ix_position_account_symbol ON position(account, symbol);

CREATE TABLE IF NOT EXISTS daily_brief (
    id           INTEGER PRIMARY KEY,
    trading_day  TEXT NOT NULL,
    kind         TEXT NOT NULL,                -- 'morning'|'eod_debate'
    body_md      TEXT NOT NULL,
    created_ts   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_daily_brief_day ON daily_brief(trading_day);

CREATE TABLE IF NOT EXISTS strategy_state (
    strategy        TEXT PRIMARY KEY,
    halted          INTEGER NOT NULL DEFAULT 0,    -- 0/1
    halt_reason     TEXT,
    realized_pnl    REAL    NOT NULL DEFAULT 0.0,
    realized_pnl_day TEXT,                          -- date string; resets at trading_day rollover
    updated_ts      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS account_state (
    account            TEXT PRIMARY KEY,
    equity             REAL NOT NULL,
    peak_equity        REAL NOT NULL,
    halted             INTEGER NOT NULL DEFAULT 0,
    halt_reason        TEXT,
    updated_ts         TEXT NOT NULL
);

-- Generic key/value JSON store for agent state that needs to survive
-- process restarts (e.g. Lord Otter's bias latch, future regime
-- snapshots, PMCC roll history). Scoped by (agent, key) so each agent
-- owns its own keyspace; key naming is the agent's choice (we use
-- 'bias:<symbol>' for Lord Otter).
--
-- Why this isn't in `strategy_state`: that table has strategy-global
-- columns (halted, realized_pnl_day) and is keyed per-strategy. Lord
-- Otter's bias is per-symbol, and other agents will want to persist
-- different shapes. Generic JSON blob keeps the schema stable as
-- agents evolve.
CREATE TABLE IF NOT EXISTS agent_state (
    agent       TEXT NOT NULL,           -- e.g. 'lord_otter'
    key         TEXT NOT NULL,           -- agent-defined; e.g. 'bias:BTC/USD'
    value_json  TEXT NOT NULL,           -- arbitrary JSON blob
    updated_ts  TEXT NOT NULL,           -- ISO-8601 UTC
    PRIMARY KEY (agent, key)
);
"""


def resolve_db_path(db_url: str) -> Path:
    """Accept either a sqlite:/// URL or a bare filesystem path."""
    if db_url.startswith("sqlite:///"):
        return Path(db_url[len("sqlite:///"):])
    if db_url.startswith("sqlite://"):
        return Path(db_url[len("sqlite://"):])
    return Path(db_url)


def init_db(db_url: str = "sqlite:///data/trading_corp.db") -> Path:
    """Ensure data dir exists, create/upgrade schema, return DB path."""
    path = resolve_db_path(db_url)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)
        conn.commit()
    return path


@contextmanager
def connect(db_url: str = "sqlite:///data/trading_corp.db") -> Iterator[sqlite3.Connection]:
    path = resolve_db_path(db_url)
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        yield conn
    finally:
        conn.close()


# ── agent_state helpers ─────────────────────────────────────────────────
# Generic key/value JSON store for agent state that needs to survive
# process restarts. See the `agent_state` table comment in SCHEMA above
# for design rationale.
#
# Use:
#   set_agent_state("lord_otter", "bias:BTC/USD", {"bias": "bull"})
#   load_agent_state("lord_otter", "bias:BTC/USD")
#       → ({"bias": "bull"}, datetime(2026, 4, 30, 17, 57, 8, tzinfo=UTC))


def set_agent_state(
    agent: str,
    key: str,
    value: Any,
    db_url: str = "sqlite:///data/trading_corp.db",
) -> None:
    """Upsert a JSON-serializable value at (agent, key).

    `updated_ts` is ALWAYS refreshed to now-UTC, even if the value is
    identical — callers can rely on `updated_ts` reflecting the most
    recent write attempt, which matters for staleness checks downstream.
    """
    payload = json.dumps(value, separators=(",", ":"), default=str)
    ts = datetime.now(timezone.utc).isoformat()
    with connect(db_url) as conn:
        conn.execute(
            "INSERT INTO agent_state (agent, key, value_json, updated_ts) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(agent, key) DO UPDATE SET "
            "  value_json=excluded.value_json, updated_ts=excluded.updated_ts",
            (agent, key, payload, ts),
        )


def load_agent_state(
    agent: str,
    key: str,
    db_url: str = "sqlite:///data/trading_corp.db",
) -> tuple[Any, datetime] | None:
    """Return (value, updated_at) for (agent, key), or None if absent.

    `updated_at` is parsed as a timezone-aware UTC datetime — callers
    typically compare against `datetime.now(timezone.utc)` for
    staleness checks (e.g. "discard if older than 12h").

    Caller is responsible for the staleness policy. We don't bake one
    in here because different keys want different windows (bias maybe
    12h, regime snapshots maybe 1h).
    """
    with connect(db_url) as conn:
        row = conn.execute(
            "SELECT value_json, updated_ts FROM agent_state "
            "WHERE agent = ? AND key = ?",
            (agent, key),
        ).fetchone()
    if row is None:
        return None
    return json.loads(row["value_json"]), datetime.fromisoformat(row["updated_ts"])


def delete_agent_state(
    agent: str,
    key: str,
    db_url: str = "sqlite:///data/trading_corp.db",
) -> None:
    """Remove an entry. No-op if absent."""
    with connect(db_url) as conn:
        conn.execute(
            "DELETE FROM agent_state WHERE agent = ? AND key = ?",
            (agent, key),
        )
