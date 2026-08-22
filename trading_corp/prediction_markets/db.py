"""Prediction Markets datastore (P1). Separate SQLite file; ZERO engine coupling.

A separate DB file means P1 ingestion writes can NEVER contend with the live
engine's writes on trading_corp.db (P1 §3 isolation guarantee). All access goes
through this thin module. Migrations are clean + versioned via a `schema_version`
table — deliberately NOT legacy's `_maybe_add_column` pattern (persistence/db.py).

The canonical scoreable-row predicate (§3A) lives HERE, defined ONCE, because
db.py is the lowest layer both ingest.py and stats.py import — so the rollup,
BOTH ranking routines, and query_scoreboard all filter through the same string
and it can never be re-derived divergently per call site.

Spec: reports/prediction_markets/P1_PLAN.md §3, §3A, §6.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

_DEFAULT_PM_DB_PATH = "data/prediction_markets.db"
# Basenames we must never open from the PM package — opening the legacy DB would
# collapse the isolation guarantee (§3). Fail loud at connect time.
_LEGACY_DB_BASENAMES = {"trading_corp.db"}

# ── §3A canonical scoreable-row predicate — the ONE definition ──────────────
SCOREABLE_PREDICATE_SQL = "pnl_suspect = 0"


def scoreable_where(table_alias: str = "") -> str:
    """The canonical scoreable-row SQL predicate (§3A), optionally table-qualified.

    Every consumer (pm_category_stats rollup, net_roi + recency_weighted routines,
    query_scoreboard) MUST build its WHERE clause from this — never inline
    `pnl_suspect = 0` by hand elsewhere.
    """
    col = f"{table_alias}.pnl_suspect" if table_alias else "pnl_suspect"
    return f"{col} = 0"


def pm_db_path() -> str:
    """Resolved PM DB path: `PM_DB_PATH` env override, else the default separate file."""
    return os.environ.get("PM_DB_PATH", _DEFAULT_PM_DB_PATH)


def _assert_not_legacy(path: str) -> None:
    """Fail loud if the resolved path is the legacy trading_corp.db (isolation guard, §3).

    Basename check: catches both the default relative path and any absolute path that
    points at the legacy file by name. A different file in the same directory is fine
    (WAL locks are per-file).
    """
    base = os.path.basename(str(path)).lower()
    if base in _LEGACY_DB_BASENAMES:
        raise RuntimeError(
            f"PM DB path resolves to the legacy DB ({path!r}); refusing to open. "
            "Set PM_DB_PATH to a separate file (default: data/prediction_markets.db)."
        )


@contextmanager
def connect(db_path: str | None = None) -> Iterator[sqlite3.Connection]:
    """Open the PM DB with the legacy prod pragmas (mirrors persistence/db.py:636):
    WAL, busy_timeout=5000, synchronous=NORMAL, foreign_keys=ON. Autocommit
    (isolation_level=None); migrations manage transactions explicitly.
    """
    path = db_path or pm_db_path()
    _assert_not_legacy(path)
    parent = os.path.dirname(path)
    if parent:
        Path(parent).mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        yield conn
    finally:
        conn.close()


# ── migrations (numbered, idempotent) ───────────────────────────────────────
# Each migration = (version:int, [SQL statements]). init_db() applies every
# migration with version > current schema_version, in order, each inside one
# explicit transaction, then records the version. Re-running init_db() is a
# no-op. All DDL is `IF NOT EXISTS` so a crash between statements self-heals on
# the next run (and schema_version is only bumped after the whole migration
# commits).

MIGRATION_001: list[str] = [
    # migration bookkeeping is bootstrapped in _current_version(); tables below:
    """
    CREATE TABLE IF NOT EXISTS pm_whale (
        wallet            TEXT PRIMARY KEY,   -- lowercase proxy wallet
        user_name         TEXT,
        first_seen_ts     INTEGER,
        last_backfill_ts  INTEGER,
        last_refresh_ts   INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pm_closed_position (
        wallet          TEXT NOT NULL,
        condition_id    TEXT NOT NULL,
        slug            TEXT,
        event_slug      TEXT,
        title           TEXT,
        category        TEXT,
        category_source TEXT,                        -- slug_prefix | gamma_tags | unknown
        outcome         TEXT,
        outcome_index   INTEGER,
        avg_price       REAL,
        total_bought    REAL,
        realized_pnl    REAL,                         -- CAN BE NEGATIVE
        cur_price       REAL,
        won             INTEGER,                      -- cur_price >= 0.9 (stored at ingest)
        pnl_suspect     INTEGER NOT NULL DEFAULT 0,   -- §3A FINAL group-aware quarantine flag (clause (b) only)
        suspect_reason  TEXT,                         -- NULL | row_invariant | event_group
        pnl_anomaly     INTEGER NOT NULL DEFAULT 0,   -- §3A clause (a) DEMOTED 2026-08-22 (§13A(f)): RECORDED, NOT excluded/propagated
        anomaly_reason  TEXT,                         -- NULL | loss_exceeds_cost
        shares_derived  REAL,                         -- total_bought / avg_price (NULL-safe)
        end_date        TEXT,
        resolved_ts     INTEGER,
        ingested_ts     INTEGER,
        updated_ts      INTEGER,
        PRIMARY KEY (wallet, condition_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_pm_cp_wallet          ON pm_closed_position(wallet)",
    "CREATE INDEX IF NOT EXISTS ix_pm_cp_category        ON pm_closed_position(category)",
    "CREATE INDEX IF NOT EXISTS ix_pm_cp_wallet_resolved ON pm_closed_position(wallet, resolved_ts DESC)",
    "CREATE INDEX IF NOT EXISTS ix_pm_cp_wallet_won_cat  ON pm_closed_position(wallet, won, category)",
    # serves the §3A scoreable predicate (WHERE pnl_suspect = 0, per wallet+category):
    "CREATE INDEX IF NOT EXISTS ix_pm_cp_scoreable       ON pm_closed_position(wallet, category, pnl_suspect)",
    # event-group quarantine (§3A) groups on (wallet, event_slug); index it so the
    # ingest group-propagation pass + any rollup grouping never full-scans the table:
    "CREATE INDEX IF NOT EXISTS ix_pm_cp_wallet_event    ON pm_closed_position(wallet, event_slug)",
    """
    CREATE TABLE IF NOT EXISTS pm_category_stats (
        wallet            TEXT NOT NULL,
        category          TEXT NOT NULL,
        n_resolved        INTEGER,                    -- COUNT of SCOREABLE rows (pnl_suspect=0) only;
                                                       -- n_resolved + n_excluded == every row in the (wallet,category) slice
        wins              INTEGER,
        losses            INTEGER,
        win_rate          REAL,
        net_realized_pnl  REAL,
        total_bought      REAL,
        roi               REAL,
        avg_bet           REAL,
        avg_win_price     REAL,
        last_resolved_ts  INTEGER,
        n_excluded        INTEGER NOT NULL DEFAULT 0, -- §3A visibility: quarantined (clause (b)) rows (full group)
        excluded_pnl      REAL    NOT NULL DEFAULT 0, -- summed realized_pnl of quarantined rows
        n_anomaly         INTEGER NOT NULL DEFAULT 0, -- §3A clause (a) flag count (NOT excluded; investigable)
        dq_count_pct      REAL    NOT NULL DEFAULT 0, -- n_excluded / total rows (fraction)
        dq_dollar_pct     REAL    NOT NULL DEFAULT 0, -- SUM|realized| excluded / SUM|realized| all ($-weighted)
        data_quality      TEXT,                       -- NULL | 'contaminated' (count OR $ fraction > threshold)
        updated_ts        INTEGER,
        PRIMARY KEY (wallet, category)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_pm_cs_category_roi ON pm_category_stats(category, roi DESC)",
    """
    CREATE TABLE IF NOT EXISTS pm_open_position (
        wallet        TEXT NOT NULL,
        condition_id  TEXT NOT NULL,
        slug          TEXT,
        event_slug    TEXT,
        title         TEXT,
        category      TEXT,
        outcome       TEXT,
        size          REAL,
        avg_price     REAL,
        initial_value REAL,
        current_value REAL,
        cash_pnl      REAL,
        refreshed_ts  INTEGER,
        PRIMARY KEY (wallet, condition_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_pm_op_wallet ON pm_open_position(wallet)",
    """
    CREATE TABLE IF NOT EXISTS pm_score_snapshot (
        wallet       TEXT NOT NULL,
        category     TEXT NOT NULL,
        routine      TEXT NOT NULL,                   -- net_roi | recency_weighted
        score        REAL,
        wilson_lcb   REAL,
        edge_factor  REAL,
        params_json  TEXT,                            -- recency_basis + {excludes_suspect, n_excluded} (§3A)
        computed_ts  INTEGER,
        PRIMARY KEY (wallet, category, routine)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_pm_ss_cat_routine_score ON pm_score_snapshot(category, routine, score DESC)",
]

MIGRATIONS: list[tuple[int, list[str]]] = [
    (1, MIGRATION_001),
]


def _current_version(conn: sqlite3.Connection) -> int:
    # version is PRIMARY KEY: prevents duplicate/accumulating rows so MAX(version) is
    # never ambiguous, and re-inserting an already-applied version would fail loudly
    # (init_db already skips applied versions, so this is a schema-level idempotency guard).
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)")
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    return int(row["v"]) if row is not None and row["v"] is not None else 0


def init_db(db_path: str | None = None) -> str:
    """Create/upgrade the PM DB to the latest schema. Idempotent. Returns the resolved path."""
    path = db_path or pm_db_path()
    with connect(path) as conn:
        current = _current_version(conn)
        for version, statements in sorted(MIGRATIONS, key=lambda m: m[0]):
            if version <= current:
                continue
            conn.execute("BEGIN")
            try:
                for stmt in statements:
                    conn.execute(stmt)
                conn.execute("INSERT INTO schema_version(version) VALUES (?)", (version,))
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
    return path
