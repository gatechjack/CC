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
        total_bought    REAL,                         -- NOTIONAL (share count = payout at $1), NOT USDC cost (§13A(g))
        cost_basis      REAL,                         -- = total_bought * avg_price = real USDC cost (the ROI denominator, §13 dec 11)
        realized_pnl    REAL,                         -- CAN BE NEGATIVE
        cur_price       REAL,
        won             INTEGER,                      -- cur_price >= 0.9 (stored at ingest)
        pnl_suspect     INTEGER NOT NULL DEFAULT 0,   -- §3A FINAL group-aware quarantine flag (clause (b) only)
        suspect_reason  TEXT,                         -- NULL | row_invariant | event_group | no_cost_basis
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
        total_bought      REAL,                       -- NOTIONAL sum (payout@$1), NOT cost
        cost_basis        REAL,                       -- SUM(total_bought*avg_price) scoreable = real cost denominator (§13 dec 11)
        roi               REAL,                       -- RANKED metric: net_realized_pnl / cost_basis (COST-based, §13 dec 11)
        roi_notional      REAL,                       -- NOT ranked: net_realized_pnl / total_bought (legacy/scout comparison ONLY)
        avg_bet           REAL,                       -- cost-based: cost_basis / n_resolved
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

# migration 002 (2026-08-22): widen the PK to preserve TWO-SIDED holdings. A whale can hold BOTH outcomes
# of one binary market (same condition_id, distinct outcome_index) -- e.g. Kickstand7 held Yes AND No on
# 489 markets. PK (wallet, condition_id) silently collapsed them via INSERT OR REPLACE (489/1803 rows lost).
# Fix: add outcome_index to the PK on pm_closed_position AND pm_open_position (same latent bug: /positions
# can hold both sides too). SQLite cannot ALTER a PK -> rebuild the table. NO in-place data recovery (the
# old table already dropped the rows); the checkpoint DB is DROPPED + re-backfilled. Idempotent (runs once).
MIGRATION_002: list[str] = [
    """
    CREATE TABLE pm_closed_position_v2 (
        wallet          TEXT NOT NULL,
        condition_id    TEXT NOT NULL,
        slug            TEXT,
        event_slug      TEXT,
        title           TEXT,
        category        TEXT,
        category_source TEXT,
        outcome         TEXT,
        outcome_index   INTEGER NOT NULL DEFAULT 0,   -- now part of the PK (two-sided holdings, migration 002)
        avg_price       REAL,
        total_bought    REAL,
        cost_basis      REAL,
        realized_pnl    REAL,
        cur_price       REAL,
        won             INTEGER,
        pnl_suspect     INTEGER NOT NULL DEFAULT 0,
        suspect_reason  TEXT,
        pnl_anomaly     INTEGER NOT NULL DEFAULT 0,
        anomaly_reason  TEXT,
        shares_derived  REAL,
        end_date        TEXT,
        resolved_ts     INTEGER,
        ingested_ts     INTEGER,
        updated_ts      INTEGER,
        PRIMARY KEY (wallet, condition_id, outcome_index)
    )
    """,
    """
    INSERT OR IGNORE INTO pm_closed_position_v2
        (wallet, condition_id, slug, event_slug, title, category, category_source, outcome, outcome_index,
         avg_price, total_bought, cost_basis, realized_pnl, cur_price, won, pnl_suspect, suspect_reason,
         pnl_anomaly, anomaly_reason, shares_derived, end_date, resolved_ts, ingested_ts, updated_ts)
    SELECT
         wallet, condition_id, slug, event_slug, title, category, category_source, outcome, outcome_index,
         avg_price, total_bought, cost_basis, realized_pnl, cur_price, won, pnl_suspect, suspect_reason,
         pnl_anomaly, anomaly_reason, shares_derived, end_date, resolved_ts, ingested_ts, updated_ts
    FROM pm_closed_position
    """,
    "DROP TABLE pm_closed_position",
    "ALTER TABLE pm_closed_position_v2 RENAME TO pm_closed_position",
    "CREATE INDEX IF NOT EXISTS ix_pm_cp_wallet          ON pm_closed_position(wallet)",
    "CREATE INDEX IF NOT EXISTS ix_pm_cp_category        ON pm_closed_position(category)",
    "CREATE INDEX IF NOT EXISTS ix_pm_cp_wallet_resolved ON pm_closed_position(wallet, resolved_ts DESC)",
    "CREATE INDEX IF NOT EXISTS ix_pm_cp_wallet_won_cat  ON pm_closed_position(wallet, won, category)",
    "CREATE INDEX IF NOT EXISTS ix_pm_cp_scoreable       ON pm_closed_position(wallet, category, pnl_suspect)",
    "CREATE INDEX IF NOT EXISTS ix_pm_cp_wallet_event    ON pm_closed_position(wallet, event_slug)",
    """
    CREATE TABLE pm_open_position_v2 (
        wallet        TEXT NOT NULL,
        condition_id  TEXT NOT NULL,
        slug          TEXT,
        event_slug    TEXT,
        title         TEXT,
        category      TEXT,
        outcome       TEXT,
        outcome_index INTEGER NOT NULL DEFAULT 0,     -- now part of the PK (two-sided holdings, migration 002)
        size          REAL,
        avg_price     REAL,
        initial_value REAL,
        current_value REAL,
        cash_pnl      REAL,
        refreshed_ts  INTEGER,
        PRIMARY KEY (wallet, condition_id, outcome_index)
    )
    """,
    """
    INSERT OR IGNORE INTO pm_open_position_v2
        (wallet, condition_id, slug, event_slug, title, category, outcome, size, avg_price,
         initial_value, current_value, cash_pnl, refreshed_ts)
    SELECT
         wallet, condition_id, slug, event_slug, title, category, outcome, size, avg_price,
         initial_value, current_value, cash_pnl, refreshed_ts
    FROM pm_open_position
    """,
    "DROP TABLE pm_open_position",
    "ALTER TABLE pm_open_position_v2 RENAME TO pm_open_position",
    "CREATE INDEX IF NOT EXISTS ix_pm_op_wallet ON pm_open_position(wallet)",
]

# migration 003 (2026-08-22): per-wallet backfill completeness (Step-4 429 safety). A 429-truncated or
# cap-hit wallet lands with PARTIAL history -> looks like a DIFFERENT whale, not a broken one. Record
# whether the last pull ran to a genuinely short/empty page (backfill_complete=1) + the pulled/stored
# counts, so PARTIAL wallets are visibly marked and EXCLUDED from ranking (treated as FAILED) until re-run.
MIGRATION_003: list[str] = [
    "ALTER TABLE pm_whale ADD COLUMN backfill_complete INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE pm_whale ADD COLUMN last_pulled INTEGER",
    "ALTER TABLE pm_whale ADD COLUMN last_stored INTEGER",
]

# migration 004 (2026-08-23, P2 CP1): CAVEAT ANALYTICS as first-class pm_category_stats columns + the
# one-sided directional-slice companion (P2_PLAN §5.1). Additive; the NEXT rollup() backfills every row.
# ** e5 (load-bearing): rollup() is extended in the SAME change. stats.rollup() does INSERT OR REPLACE over
# stats._STATS_COLS, so ANY column added here that is not ALSO added to _STATS_COLS AND computed in the
# rollup SELECT is reset to its DEFAULT on every run -> the caveat columns would read ZERO on the product
# page FOREVER, silently. Migration without the wiring is worse than no migration. **
MIGRATION_004: list[str] = [
    "ALTER TABLE pm_category_stats ADD COLUMN n_condition_ids    INTEGER NOT NULL DEFAULT 0",  # COUNT(DISTINCT condition_id), ALL rows
    "ALTER TABLE pm_category_stats ADD COLUMN n_two_sided        INTEGER NOT NULL DEFAULT 0",  # condition_ids held on >1 outcome_index
    "ALTER TABLE pm_category_stats ADD COLUMN two_sided_pct      REAL    NOT NULL DEFAULT 0",  # n_two_sided/n_condition_ids (hedge/MM tell, §13A(j))
    "ALTER TABLE pm_category_stats ADD COLUMN n_single_game      INTEGER NOT NULL DEFAULT 0",  # rows classified single_game (dated, not futures)
    "ALTER TABLE pm_category_stats ADD COLUMN n_futures_like     INTEGER NOT NULL DEFAULT 0",  # rows classified futures (champion|mvp|...)
    "ALTER TABLE pm_category_stats ADD COLUMN single_game_pct    REAL",                        # n_single_game/total; HEURISTIC; NULL for non-sports (Fed) -- OQ-2
    "ALTER TABLE pm_category_stats ADD COLUMN market_type_source TEXT DEFAULT 'slug_heuristic'",  # seam: slug_heuristic(P2) | gamma_market_type(later) -- §13A(d)
    # one-sided directional slice = the copyable signal, but an UPPER BOUND (survivorship-caveated, §13A(f)).
    # Companion table keyed 1:1 so query_scoreboard LEFT JOINs it (avoids doubling pm_category_stats width).
    """
    CREATE TABLE IF NOT EXISTS pm_category_onesided_stats (
        wallet            TEXT NOT NULL,
        category          TEXT NOT NULL,
        n_resolved        INTEGER NOT NULL DEFAULT 0,   -- scoreable rows on condition_ids the whale held ONE-SIDED
        wins              INTEGER NOT NULL DEFAULT 0,
        losses            INTEGER NOT NULL DEFAULT 0,
        win_rate          REAL,
        net_realized_pnl  REAL NOT NULL DEFAULT 0,
        total_bought      REAL NOT NULL DEFAULT 0,      -- NOTIONAL sum
        cost_basis        REAL NOT NULL DEFAULT 0,      -- SUM(total_bought*avg_price) = real USDC cost denominator
        roi               REAL,                          -- net/cost_basis (cost-based, §13 dec 11); NULL if cost_basis<=0
        avg_bet           REAL,
        avg_win_price     REAL,
        last_resolved_ts  INTEGER,
        is_upper_bound    INTEGER NOT NULL DEFAULT 1,   -- ALWAYS 1: excludes hedged markets => optimistic (§13A(f))
        updated_ts        INTEGER,
        PRIMARY KEY (wallet, category)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_pm_cos_category_roi ON pm_category_onesided_stats(category, roi DESC)",
]

# migration 005 (2026-08-24, CP3a): paper-trading forward record. pm_paper_trade carries the COMPLETE
# open -> pending_adjudication -> closed|stale|void lifecycle in ONE migration (the _STATS_COLS lesson: a
# lifecycle column that lands later than the code deriving it is the trap we already hit). Entry columns
# are OBSERVATION-provenance, NOT fills -- the poller reads /positions (which carries NO fill ts), so
# entry_observed_ts is observation time +/- poll_interval_sec and entry_price_avg_at_observation is the
# whale's avgPrice at observation (scale-ins collapsed), NOT a paper fill price. size_basis = OUR fixed
# paper stake = FIXED CONTRACT/SHARE COUNT (e7; NOT the whale's size, NOT dollars); then cost_basis =
# size_basis * entry_price parallels the external side's total_bought(NOTIONAL) * avg_price (ROI-
# denominator parity, dec 11 -- storing dollars would make cost_basis mean something DIFFERENT on the two
# halves of the same scoreboard, do NOT). whale_size_at_observation is DISPLAY-ONLY (mirroring the whale's
# size would import its bankroll into the signal). A vanished position is NOT classified on the
# disappearance (a row vanishes on BOTH whale-exit AND settle) -> it goes to pending_adjudication, and the
# weekly /closed-positions adjudicator (paper.py) decides closed(resolution) vs stale(whale_exit) by
# whether a pm_closed_position row exists by market_end_date + grace_window -- biases DOWN (a whale exit
# never books paper P&L). PK includes entry_observed_ts so a full-exit-then-re-enter on the same leg is a
# NEW paper trade; paper.py's open guard enforces at most one OPEN row per (wallet, condition_id, outcome_index).
MIGRATION_005: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS pm_paper_trade (
        wallet                          TEXT NOT NULL,
        category                        TEXT NOT NULL,
        condition_id                    TEXT NOT NULL,
        outcome_index                   INTEGER NOT NULL DEFAULT 0,   -- two-sided legs preserved (migration-002 parity)
        slug                            TEXT,
        event_slug                      TEXT,
        title                           TEXT,
        outcome                         TEXT,
        side                            TEXT NOT NULL DEFAULT 'BUY',  -- /positions holdings are long the outcome
        entry_observed_ts               INTEGER NOT NULL,            -- OBSERVATION time (+/- poll_interval_sec), NOT a fill ts; in PK
        entry_price_avg_at_observation  REAL,                        -- whale avgPrice at observation (scale-ins collapsed), NOT a fill price
        whale_size_at_observation       REAL,                        -- whale /positions.size at observation -- DISPLAY-ONLY, never a sizing input
        size_basis                      REAL,                        -- OUR fixed paper stake: FIXED CONTRACT/SHARE COUNT (e7), NOT whale size, NOT dollars
        cost_basis                      REAL,                        -- size_basis * entry_price_avg_at_observation (ROI-denominator parity, dec 11)
        poll_interval_sec               INTEGER,                     -- poll interval at capture -> the +/- observation bound self-documents
        entry_basis                     TEXT,                        -- machine-readable provenance seam (e.g. 'positions_observation')
        market_end_date                 TEXT,                        -- /positions.endDate -> reason "vanished BEFORE resolution"
        n_observed_adds                 INTEGER NOT NULL DEFAULT 0,  -- observed size INCREASES on this open row (NOT new entries) -- diagnostic only
        last_add_observed_ts            INTEGER,
        n_observed_reductions           INTEGER NOT NULL DEFAULT 0,  -- observed size DECREASES (partial whale exit) -- no status change in CP3a
        last_reduction_observed_ts      INTEGER,
        status                          TEXT NOT NULL DEFAULT 'open',-- open | pending_adjudication | closed | stale | void
        exit_observed_ts                INTEGER,                     -- when the position vanished from /positions -> pending_adjudication
        resolved_ts                     INTEGER,                     -- from pm_closed_position at adjudication (close_source='resolution')
        won                             INTEGER,                     -- paper won from resolution (NOT the whale's)
        realized_pnl                    REAL,                        -- paper realized from resolution (NOT the whale's pnl)
        close_source                    TEXT,                        -- resolution | whale_exit | manual -- provenance, not inferred
        stale_ts                        INTEGER,
        stale_reason                    TEXT,
        mark_price                      REAL,                        -- weekly informational mark
        mark_pnl                        REAL,
        mark_ts                         INTEGER,
        pnl_suspect                     INTEGER NOT NULL DEFAULT 0,  -- 3A parity: imported from the matched pm_closed_position at adjudication
        suspect_reason                  TEXT,
        source                          TEXT,                        -- e.g. 'poller'
        pinned_ts                       INTEGER,
        opened_ts                       INTEGER NOT NULL,            -- row-creation ts
        updated_ts                      INTEGER,
        PRIMARY KEY (wallet, condition_id, outcome_index, entry_observed_ts)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_pm_pt_wallet_cat  ON pm_paper_trade(wallet, category)",
    "CREATE INDEX IF NOT EXISTS ix_pm_pt_status      ON pm_paper_trade(status)",
    "CREATE INDEX IF NOT EXISTS ix_pm_pt_open_leg    ON pm_paper_trade(wallet, condition_id, outcome_index, status)",
    "CREATE INDEX IF NOT EXISTS ix_pm_pt_pending_end ON pm_paper_trade(status, market_end_date)",
    # config: tunable operational params. Key-value so it is trivially extensible; code (paper.get_config)
    # carries DEFAULTS so a missing table/row degrades honestly to the default rather than erroring.
    """
    CREATE TABLE IF NOT EXISTS pm_paper_config (
        key         TEXT PRIMARY KEY,
        value       TEXT NOT NULL,
        updated_ts  INTEGER
    )
    """,
    # seed defaults; INSERT OR IGNORE never clobbers a tuned value (migration is version-gated anyway)
    "INSERT OR IGNORE INTO pm_paper_config(key, value) VALUES ('poll_interval_sec', '300')",
    "INSERT OR IGNORE INTO pm_paper_config(key, value) VALUES ('grace_window_sec', '172800')",
    "INSERT OR IGNORE INTO pm_paper_config(key, value) VALUES ('size_basis', '100')",
]

# migration 006 (2026-08-24, CP3a): farm roster + watchlist. pm_farm (P1, documented-only) is SUPERSEDED,
# split into pm_roster (the universal (wallet, category) roster the weekly refresh + poller read) and
# pm_watchlist (per-(wallet,category) farm status: 'watchlist' = candidate/Analyze-able, NOT paper;
# 'pinned' = forward paper-trading -- the poller polls pinned rows). Keyed (wallet, category) so a whale's
# PINNING CATEGORY is explicit provenance (C2.4: it is NOT derivable from pm_category_stats, which is
# cross-category). search_run_id is a nullable seam for CP3b search -- pm_search_run is a LATER migration,
# NOT this one (P2_PLAN §5.3 amended: 006 = roster + watchlist only).
MIGRATION_006: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS pm_roster (
        wallet      TEXT NOT NULL,
        category    TEXT NOT NULL,
        user_name   TEXT,
        source      TEXT,                          -- provenance of the (wallet,category) pair (e.g. 'scout_ufc_2026-08-21')
        added_ts    INTEGER,
        active      INTEGER NOT NULL DEFAULT 1,     -- the weekly refresh source is pm_roster WHERE active=1 (Ruling B)
        notes       TEXT,
        PRIMARY KEY (wallet, category)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_pm_roster_active ON pm_roster(active)",
    """
    CREATE TABLE IF NOT EXISTS pm_watchlist (
        wallet         TEXT NOT NULL,
        category       TEXT NOT NULL,
        added_ts       INTEGER,
        source         TEXT,
        status         TEXT NOT NULL DEFAULT 'watchlist',  -- watchlist (candidate, NOT paper) | pinned (forward paper)
        pinned_ts      INTEGER,
        search_run_id  INTEGER,                            -- nullable seam for CP3b search (pm_search_run = later migration)
        updated_ts     INTEGER,
        PRIMARY KEY (wallet, category)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_pm_watchlist_status ON pm_watchlist(status)",
]

MIGRATIONS: list[tuple[int, list[str]]] = [
    (1, MIGRATION_001),
    (2, MIGRATION_002),
    (3, MIGRATION_003),
    (4, MIGRATION_004),
    (5, MIGRATION_005),
    (6, MIGRATION_006),
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
