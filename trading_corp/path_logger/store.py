"""SQLite store for the Kalshi BTC bucket order-book path logger.

Manages the separate path_logger.db file (NEVER trading_corp.db). All writes
are append-only; no UPDATE or DELETE is issued by this module. Timestamps are
Unix milliseconds (INTEGER) throughout — cheaper arithmetic and no ISO-string
precision drift.

Key constraints:
  - Separate db file: data/path_logger.db
  - WAL mode + isolation_level=None (autocommit at the Python level)
  - Batch inserts use BEGIN IMMEDIATE / COMMIT for atomic per-cycle writes
  - Checkpoint every 500 commits OR 5 minutes, whichever comes first
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# DDL ─────────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS market_ladder (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    event_ticker  TEXT    NOT NULL,
    ticker        TEXT    NOT NULL,
    intended_ts   INTEGER NOT NULL,
    captured_ts   INTEGER NOT NULL,
    yes_bid       REAL,
    yes_ask       REAL,
    no_bid        REAL,
    no_ask        REAL,
    last_trade    REAL,
    implied_prob  REAL,
    cb_spot_mid   REAL,
    cb_spot_bid   REAL,
    cb_spot_ask   REAL
);
CREATE INDEX IF NOT EXISTS idx_ml_captured_ts ON market_ladder(captured_ts);
CREATE INDEX IF NOT EXISTS idx_ml_ticker_ts   ON market_ladder(ticker, captured_ts);

CREATE TABLE IF NOT EXISTS logger_jitter (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type   TEXT    NOT NULL,
    ticker       TEXT,
    intended_ts  INTEGER,
    captured_ts  INTEGER NOT NULL,
    gap_ms       INTEGER,
    payload_json TEXT
);
"""

# Column lists used by batch_insert_ladder — order must match the INSERT.
_LADDER_COLS = (
    "event_ticker", "ticker", "intended_ts", "captured_ts",
    "yes_bid", "yes_ask", "no_bid", "no_ask", "last_trade",
    "implied_prob", "cb_spot_mid", "cb_spot_bid", "cb_spot_ask",
)


# Connection ──────────────────────────────────────────────────────────────────

def connect(db_path: str) -> sqlite3.Connection:
    """Open a long-lived WAL-mode connection to the path logger DB.

    Mirrors the connect() pattern in trading_corp/persistence/db.py:
      - isolation_level=None  → Python-level autocommit; manual BEGIN/COMMIT
        required for multi-statement atomic batches (see batch_insert_ladder).
      - WAL journal mode       → allows concurrent readers during writes.
      - foreign_keys=ON        → defensive; no FK in this schema but costs nothing.
      - check_same_thread=False→ asyncio tasks share one connection safely when
        all writes are serialised through the single logger coroutine.
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Run DDL idempotently (CREATE TABLE IF NOT EXISTS + indexes)."""
    conn.executescript(_SCHEMA)
    log.info("path_logger.store: schema initialised")


# Write helpers ───────────────────────────────────────────────────────────────

def batch_insert_ladder(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> None:
    """Atomically write one capture cycle's ladder rows.

    Uses BEGIN IMMEDIATE so no concurrent writer can interleave between our
    rows. isolation_level=None means there is no implicit transaction — we must
    manage BEGIN/COMMIT explicitly.

    Each dict in `rows` must contain keys matching _LADDER_COLS. Missing keys
    default to None (NULLs are valid for optional quote sides).
    """
    if not rows:
        return

    placeholders = ", ".join("?" for _ in _LADDER_COLS)
    sql = (
        f"INSERT INTO market_ladder ({', '.join(_LADDER_COLS)}) "
        f"VALUES ({placeholders})"
    )
    params = [
        tuple(r.get(col) for col in _LADDER_COLS)
        for r in rows
    ]

    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.executemany(sql, params)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def insert_jitter(
    conn: sqlite3.Connection,
    event_type: str,
    ticker: str | None,
    intended_ts: int | None,
    captured_ts: int,
    gap_ms: int | None,
    payload_json: str | None,
) -> None:
    """Insert one logger_jitter row. Runs outside any batch transaction."""
    conn.execute(
        "INSERT INTO logger_jitter "
        "(event_type, ticker, intended_ts, captured_ts, gap_ms, payload_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (event_type, ticker, intended_ts, captured_ts, gap_ms, payload_json),
    )


def checkpoint_if_needed(
    conn: sqlite3.Connection,
    commits_since_last: int,
    *,
    max_commits: int = 500,
    max_seconds: float = 300.0,
    last_checkpoint_ts: float,
) -> tuple[bool, float]:
    """Run PRAGMA wal_checkpoint(PASSIVE) if commit count or time threshold reached.

    Returns (did_checkpoint, new_last_checkpoint_ts). The caller should reset
    commits_since_last to 0 when did_checkpoint is True, and always replace
    last_checkpoint_ts with the returned value.

    PASSIVE mode: flushes WAL frames to the main DB file only when no readers
    hold a shared lock on those frames — never blocks. Appropriate for a
    long-running append-only workload.
    """
    now = time.monotonic()
    elapsed = now - last_checkpoint_ts
    if commits_since_last < max_commits and elapsed < max_seconds:
        return False, last_checkpoint_ts

    try:
        result = conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        # result = (busy, log, checkpointed)
        log.info(
            "path_logger.store: WAL checkpoint: busy=%s log=%s checkpointed=%s "
            "(after %d commits, %.0fs)",
            result[0], result[1], result[2], commits_since_last, elapsed,
        )
    except sqlite3.OperationalError as exc:
        log.warning("path_logger.store: WAL checkpoint failed: %s", exc)

    return True, now
