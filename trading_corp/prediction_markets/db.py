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
        last_observed_size              REAL,                        -- most-recent /positions size for this leg -> scale-in/reduction diff vs prior poll
        last_observed_ts                INTEGER,                     -- most-recent poll that saw this leg OPEN (bounds the exit window)
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
# pm_watchlist (per-(wallet,category) farm status: 'candidate' = Analyze-able, NOT paper [CP3b-0 vocab
# rename 2026-08-25 'watchlist'->'candidate']; 'pinned' = forward paper-trading -- the poller polls pinned
# rows). Keyed (wallet, category). >>> TWO IMMUTABLE ODDITIES BELOW -- do NOT "tidy" either into a migration:
#   (1) the TABLE keeps the name pm_watchlist (renaming = a rebuild of 114 board-locked rows; not worth it).
#   (2) the CREATE has DEFAULT 'watchlist' -- VESTIGIAL. The vocabulary is 'candidate'|'pinned'; this default
#       is preserved because 006 is APPLIED on live (sqlite_master stores `DEFAULT 'watchlist'` verbatim
#       forever) so the source stays BYTE-IDENTICAL to the ledgered 006 (2fc9173). It never materializes
#       (every insert writes an explicit status; 006 never re-runs). A CP3b-0 edit to 'candidate' was
#       REVERTED for exactly this reason -- an applied migration is history, not code. Do NOT re-apply it,
#       and do NOT "normalize" it via a later migration (that would rebuild the 114 rows to fix a value that
#       never appears). Seeding is
# EVERY (wallet,category) in pm_category_stats for the migrated whales (Ruling B; advisor ruling C2.4 was
# REVERSED 2026-08-25 -- see CP3A_CONTAMINATION_GATE.md). search_run_id is a nullable seam for CP3b search
# -- pm_search_run is a LATER migration, NOT this one (P2_PLAN §5.3 amended: 006 = roster + watchlist only).
MIGRATION_006: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS pm_roster (
        wallet      TEXT NOT NULL,
        category    TEXT NOT NULL,
        user_name   TEXT,
        source      TEXT,                          -- how the (wallet,category) pair was seeded (e.g. 'pm_category_stats (Ruling B)')
        added_ts    INTEGER,
        active      INTEGER NOT NULL DEFAULT 1,     -- INTENDED weekly refresh source per Ruling B; the flip is NOT yet
                                                    -- implemented (refresh still reads legacy agent_state) -- CP3a open item
        last_polled_ts INTEGER,                     -- last poll that actually polled this (wallet,category); NULL = never polled
                                                    -- (distinguishes 'polled, found nothing' from 'not polled at all' -- Ruling G)
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

# migration 007 (2026-08-25, CP3b-2): on-demand ANALYZE cache + cost ledger for the forked whale narrator.
# NUMBERED ON LANDING -- 007 is simply the next integer after 006; it is NOT reserved. Analyze (CP3b-2) adds
# its table before Search (pm_search_run) and the paper scoreboard (pm_paper_category_stats), so the analyze
# cache genuinely IS 007; those deferred tables become 008/009+ WHENEVER THEY land (never pre-assigned).
#   pm_analysis_cache: keyed (wallet, category, skill_version). skill_version REPLACES the legacy 24h TTL as
#     the ONLY invalidation axis -- bump PM_ANALYZE_SKILL_VERSION (analyze.py) on any prompt/model/report-shape
#     change and every prior row misses -> re-narrates. A cache HIT returns the stored row and spends NOTHING
#     (no LLM call, no cost-ledger write). Only a SUCCESSFUL verdict is ever cached: reasoned-nulls
#     (llm_unavailable / daily_cap_hit / disabled_by_flag / llm_error / no_resolved_positions) are NOT stored,
#     so the moment the ANTHROPIC key is wired (e3, Jack's hands) the next analyze narrates fresh instead of
#     serving a stale "llm_unavailable" from cache.
#   pm_analysis_cost: ONE visible per-UTC-day counter (day_utc PK). The engine tracks this narrator's spend in
#     agent_state; PM must NEVER write agent_state (isolation) -> its OWN ledger here. The $20/day cap
#     (PM_ANALYZE_DAILY_CAP_USD) is read from code, the SPEND is recorded here so the accounting is auditable
#     with a plain SELECT.
MIGRATION_007: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS pm_analysis_cache (
        wallet         TEXT NOT NULL,
        category       TEXT NOT NULL,
        skill_version  TEXT NOT NULL,               -- replaces the legacy 24h TTL: the ONLY invalidation axis
        verdict        TEXT,                         -- narration text (only successful verdicts are cached)
        null_reason    TEXT,                         -- provenance of a cached non-verdict (in practice NULL: nulls aren't cached)
        report_json    TEXT NOT NULL,                -- the deterministic PMAnalysisReport snapshot (rendered on a hit)
        model          TEXT,                         -- LLM model id used (NULL on a reasoned-null)
        cost_usd       REAL NOT NULL DEFAULT 0,      -- this call's spend (0 on cache hit / reasoned-null)
        tokens_in      INTEGER NOT NULL DEFAULT 0,
        tokens_out     INTEGER NOT NULL DEFAULT 0,
        n_resolved     INTEGER,                       -- denormalized: at-a-glance thinness in a cache listing
        created_ts     INTEGER NOT NULL,
        PRIMARY KEY (wallet, category, skill_version)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_pm_ac_created ON pm_analysis_cache(created_ts)",
    """
    CREATE TABLE IF NOT EXISTS pm_analysis_cost (
        day_utc     TEXT PRIMARY KEY,                 -- 'YYYY-MM-DD' (UTC) -- ONE row per day, the whole ledger
        usd         REAL NOT NULL DEFAULT 0,          -- accumulated Analyze LLM spend this UTC day
        n_calls     INTEGER NOT NULL DEFAULT 0,       -- narrations that actually hit the API this day
        updated_ts  INTEGER
    )
    """,
]

# migration 008 (2026-08-26, CP3b Stage 0): reversible off-funnel removal for pm_watchlist pairs.
# NUMBERED ON LANDING -- 008 is simply the next integer after 007 (live is at schema 7); NOT reserved.
#
# WHY A FLAG (`active`), NOT A STATUS VALUE -- do NOT "simplify" this into the status enum:
#   Removal must be REVERSIBLE and must restore the pair's PRIOR status automatically. A boolean flip
#   (active 1<->0) leaves `status` UNTOUCHED, so a removed 'pinned' pair returns as 'pinned' with its
#   record intact -- no bookkeeping of "what was it before". Overloading status='removed' would DESTROY the
#   value it overwrites (restore then means remembering candidate-vs-pinned by hand -- discipline that
#   eventually goes wrong); deleting the pm_roster row would lose the pair entirely. The flag is the only
#   one of the three that delivers reversibility STRUCTURALLY, not by discipline. (RULED 2026-08-26.)
#
#   removal_reason carries the THREE DISTINCT exclusion STATES in the DATA (not just a doc), because two of
#   them return and one never does, and that difference must be readable from the row without a doc:
#     'not_probed'       -- pending analysis, expected to return  (e.g. cbb: keyword never searched NCAAB)
#     'dormant_calendar' -- measured dormant, returns next cycle  (e.g. fifwc: World Cup concluded)
#     'structural'       -- permanent, never a subject            (e.g. unknown: tier-1 slug-derivation fail)
#   removal_ts = when the pair was flipped to active=0.
#
# active DEFAULT 1: every existing row (the 114 board-locked pairs) and every future insert is IN-FUNNEL
# until explicitly removed -- `ADD COLUMN NOT NULL DEFAULT 1` backfills the 114 existing rows to 1 at ALTER
# time. Consumers gate `AND active=1` (Stage 0 gated the poller, the pinned-subset assertion, the
# seeded-pairs review, the farm tile/list/candidate-count reads, and stats.query_scoreboard [the F-4
# prospects ranker]; the Stage-1 paper rollup MUST gate it too when it lands). THIS MIGRATION ONLY BUILDS
# THE MECHANISM -- it flips NOTHING. The 22-row removal write (active=0 for cbb/fifwc/unknown) is a SEPARATE
# authorization.
MIGRATION_008: list[str] = [
    "ALTER TABLE pm_watchlist ADD COLUMN active INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE pm_watchlist ADD COLUMN removal_reason TEXT",
    "ALTER TABLE pm_watchlist ADD COLUMN removal_ts INTEGER",
    "CREATE INDEX IF NOT EXISTS ix_pm_watchlist_active ON pm_watchlist(active)",
]

# migration 009 (2026-08-27, Stage 1): paper-trading category stats.
# Migration 008 was consumed by Stage 0 (active/removal columns on pm_watchlist); this is 009.
# pm_paper_category_stats aggregates pm_paper_trade per (wallet, category) -- the forward paper-trading
# scoreboard. Populated by paper.paper_rollup() (mirror of stats.rollup's INSERT OR REPLACE discipline).
# R1 gate: only active=1 pinned pairs are aggregated (deactivated pairs' rows survive in pm_paper_trade).
# ** e5 (load-bearing): paper.paper_rollup() uses _PAPER_STATS_COLS lock-step with these columns.
#    Any column added here MUST also be added to _PAPER_STATS_COLS AND computed in paper_rollup's SELECT,
#    or INSERT OR REPLACE resets it to its DEFAULT every run -> silent zeros forever (same trap as mig-004).
MIGRATION_009: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS pm_paper_category_stats (
        wallet            TEXT NOT NULL,
        category          TEXT NOT NULL,
        n_closed          INTEGER NOT NULL DEFAULT 0,   -- COUNT of closed paper trades (status='closed')
        wins              INTEGER NOT NULL DEFAULT 0,
        losses            INTEGER NOT NULL DEFAULT 0,
        win_rate          REAL,                          -- wins/(wins+losses); NULL when 0 decided
        net_paper_pnl     REAL NOT NULL DEFAULT 0,       -- SUM(realized_pnl) of closed rows
        cost_basis        REAL NOT NULL DEFAULT 0,       -- SUM(cost_basis) of closed rows (= SUM(size_basis*entry_price))
        roi               REAL,                          -- net_paper_pnl/cost_basis; NULL when cost_basis<=0
        avg_entry_price   REAL,                          -- AVG(entry_price_avg_at_observation) of closed rows
        n_open            INTEGER NOT NULL DEFAULT 0,    -- COUNT of open paper trades
        n_stale           INTEGER NOT NULL DEFAULT 0,    -- COUNT of stale (whale_exit, excluded from win/loss)
        n_void            INTEGER NOT NULL DEFAULT 0,    -- COUNT of void (market_void, excluded from win/loss)
        last_resolved_ts  INTEGER,
        updated_ts        INTEGER,
        PRIMARY KEY (wallet, category)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_pm_pcs_category_roi ON pm_paper_category_stats(category, roi DESC)",
    # ** PURE DDL ONLY (Jack RULED 2026-08-27, FIX-2 option ii). ** Migration 009 does ONE thing: create
    # pm_paper_category_stats + its index. It writes NO config/data row. The 72h grace re-tune (Jack's ruling
    # stands) is NOT done here -- a schema migration doing a config write was the entanglement that failed the
    # box-scratch on a partial-upgrade DB. The live grace value is set by an explicit, separately-verifiable
    # Stage-1 rung step (a Jack-authorized `UPDATE pm_paper_config` -- see PM_REBUILD_PLAN Stage-1 rung ladder);
    # paper.CONFIG_DEFAULTS carries the matching 259200 code default. Migration-005's 172800 seed stays history.
]

# migration 010 (2026-08-28, Stage 3 R1): the MONEY-LAYER schema -- pm_account + the sub-division
# (account, category) entity + a per-sub-division live-order LOG. NUMBERED ON LANDING (next after 009).
# ** PURE DDL ONLY (Jack RULED, mirroring 009's FIX-2 option ii). ** NO config/data writes here: sizing/risk
# DEFAULTS are DDL column defaults + code CONFIG_DEFAULTS; any LIVE config value is a SEPARATE authorized
# write. All three tables are created EMPTY and read by NO live code path at R1 -> the deploy is
# behaviour-neutral. Every statement is a CREATE (no INSERT/UPDATE) so init_db 9->10 adds no config row.
#
# ARMING/KILL is NOT a new table -- it REUSES the platform's persistent agent_state halt row
# (StrategyState.persist_halt), so nothing arming-related is added here (the plan's ruling #3; do not invent
# a second mechanism).
#
# NO pm_user / pm_role / pm_grant -- identity is Authelia's (allow/deny at the proxy); the app owns ONLY the
# login->account mapping, carried as pm_account.owner_identity (NULLABLE, empty until family logins arrive;
# owner-filtering later becomes a WHERE clause on this column -- no new access model). RULED (P2_PLAN §11/§15).
# secret_ref is a credential REFERENCE (the secrets.py / KeyVault NAME, e.g. 'KALSHI'), NEVER a secret value.
#
# ** e5 discipline (load-bearing, mirrors 004/009): pm_subdivision_order is written by the central execution
#    engine (R4+). It is shaped to what brokers/kalshi_live.py ACTUALLY returns -- the submitted V2 body
#    (build_v2_event_order: ticker/side/count/price/tif/reduce_only) + the outcome (FillEvent |
#    KalshiNoFill[benign] | OrderPlacementError[loud]) + fill facts (order_id/fill_count/average_fill_price/
#    average_fee_paid/remaining_count) -- NOT invented fields. If a later rung needs another persisted field,
#    add it via a NEW migration; do not INSERT-OR-REPLACE over a partial column set. **
MIGRATION_010: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS pm_account (
        account_id      TEXT PRIMARY KEY,             -- stable slug, e.g. 'kalshi_jack' (NOT a secret)
        venue           TEXT NOT NULL DEFAULT 'kalshi',
        secret_ref      TEXT,                         -- credential REFERENCE only (secrets.py/KeyVault NAME, e.g. 'KALSHI'); NEVER a value
        owner_identity  TEXT,                         -- NULLABLE: Authelia login -> account mapping (empty until family logins); owner-filter = later WHERE clause. NO pm_user/role/grant.
        label           TEXT,
        active          INTEGER NOT NULL DEFAULT 1,   -- enable/disable the account (structural flag, not config)
        created_ts      INTEGER,
        updated_ts      INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pm_subdivision (
        account_id          TEXT NOT NULL,            -- (account, category) = the sub-division
        category            TEXT NOT NULL,
        label               TEXT,
        active              INTEGER NOT NULL DEFAULT 1,   -- created-visible tile (ruling #4: tile on CREATE; armed-state is separate = agent_state)
        -- MLB market types this sub-division copies (Jack's scope ruling: moneyline + totals + spreads).
        -- ONE text list expresses any subset/superset WITHOUT a future migration -- so 'spread' is carried
        -- even while Kalshi may list no MLB run-line (R2's matcher skips unlisted markets at runtime).
        market_types        TEXT NOT NULL DEFAULT 'moneyline,total,spread',
        -- SIZING: FIXED for first-live (ruling #1); the column shape carries Kelly later with NO migration.
        sizing_mode         TEXT NOT NULL DEFAULT 'fixed',   -- 'fixed' | 'kelly' (kelly NOT built)
        fixed_stake_usd     REAL,                            -- per-copy USD stake when sizing_mode='fixed'
        kelly_fraction      REAL,                            -- carried for later Kelly; NULL/unused at R1
        -- RISK caps read by the central chokepoint. DDL default NULL = 'fall back to code CONFIG_DEFAULTS'
        -- (no config value is WRITTEN here -- PURE DDL):
        per_order_usd_cap   REAL,
        daily_usd_cap       REAL,
        max_open_usd        REAL,
        max_orders_per_day  INTEGER,
        max_slippage_cents  INTEGER,
        created_ts          INTEGER,
        updated_ts          INTEGER,
        PRIMARY KEY (account_id, category)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pm_subdivision_order (
        id                 INTEGER PRIMARY KEY,        -- rowid alias; append-only live-order journal
        account_id         TEXT NOT NULL,              -- the sub-division (account_id, category) this order belongs to
        category           TEXT NOT NULL,
        wallet             TEXT,                       -- the copied whale
        condition_id       TEXT,                       -- the Polymarket market copied
        outcome_index      INTEGER,                    -- the whale's leg
        signal_id          TEXT,                       -- STABLE idempotency source (e.g. whale tx_hash) -> feeds client_order_id
        client_order_id    TEXT,                       -- deterministic UUID5 idempotency key sent to Kalshi (kalshi_live.client_order_id); dedupe key
        -- submitted V2 body identifying fields (kalshi_live.build_v2_event_order):
        ticker             TEXT,                       -- Kalshi market ticker
        order_side         TEXT,                       -- V2 'bid' | 'ask' (YES-centric single book)
        outcome_leg        TEXT,                       -- 'yes' | 'no' (booked leg price = 1 - yes_price for 'no')
        is_exit            INTEGER NOT NULL DEFAULT 0, -- reduce_only exit vs entry
        submitted_count    INTEGER,                    -- contracts submitted
        submitted_price    REAL,                       -- yes-side limit price submitted (4-dec dollars)
        time_in_force      TEXT,                       -- 'immediate_or_cancel' | 'fill_or_kill' | 'good_till_canceled'
        -- outcome (kalshi_live: FillEvent | KalshiNoFill[benign] | OrderPlacementError[loud]):
        outcome_status     TEXT,                       -- 'filled' | 'no_fill' | 'rejected' | 'error'
        broker_order_id    TEXT,                       -- Kalshi order_id from the create response
        fill_count         REAL,                       -- contracts actually filled (V2 fill_count)
        fill_price         REAL,                       -- OUTCOME-leg per-contract fill price (yes, or 1-yes for 'no'), NOT the yes-side quote
        remaining_count    REAL,                       -- unfilled (IOC/FOK)
        fee                REAL,                       -- total fee (average_fee_paid * fill_count)
        error_detail       TEXT,                       -- reject/error message (NULL when filled/no_fill)
        dry_run            INTEGER NOT NULL DEFAULT 0, -- 1 = R4 logged-not-placed dry-run; 0 = real order
        submitted_ts       INTEGER,
        response_ts        INTEGER
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_pm_subord_subdiv ON pm_subdivision_order(account_id, category)",
    "CREATE INDEX IF NOT EXISTS ix_pm_subord_coid   ON pm_subdivision_order(client_order_id)",
    "CREATE INDEX IF NOT EXISTS ix_pm_subord_wallet ON pm_subdivision_order(wallet, condition_id)",
]

# migration 011 (2026-08-28, Stage 3 R6): the WHALE->SUB-DIVISION attachment -- the farm->money bridge.
# Promote-to-LIVE attaches a PINNED (wallet, category) farm pair to a (account, category) sub-division,
# JOINED ON CATEGORY (a ufc whale-category can NEVER attach to an mlb sub-division; the category is shared).
# The SAME (wallet, category) may attach to MULTIPLE sub-divisions independently (distinct account_id) -> the
# PK is (account_id, category, wallet), which allows N whales per sub-division AND M sub-divisions per whale.
# NUMBERED ON LANDING (next after 010).
# ** PURE DDL ONLY (Jack RULED, mirroring 009/010). ** No config/data writes. Created EMPTY, read by NO live
# code path until an attachment is written (R6 promote-to-live) -> behaviour-neutral deploy.
#
# PURE INDEX, no config: the attachment is ownership/linkage ONLY. A sub-division's sizing/risk config lives
# on pm_subdivision and governs EVERY attached whale (ruling #1: FIXED stake); an attachment NEVER overrides
# it. `active` (DEFAULT 1) makes DETACH reversible WITHOUT deleting the row (mirrors migration-008): detach =
# active=0 + removed_ts; re-attach reactivates (active=1) with the record intact. No FK (app-layer validates
# the sub-division exists + the pair is pinned before INSERT) -- keeps the migration a lightweight, idempotent
# CREATE on any deploy tier. Writing an attachment CANNOT reach pm_subdivision_order (the order journal is
# written only by the execution engine at placement time) -- promote-to-live creates a mapping, not an order.
MIGRATION_011: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS pm_subdivision_attachment (
        account_id   TEXT NOT NULL,               -- the sub-division's account (pm_subdivision.account_id)
        category     TEXT NOT NULL,               -- == the sub-division category AND the pinned pair's category (JOIN ON CATEGORY)
        wallet       TEXT NOT NULL,               -- the pinned whale this sub-division copies (the attachment)
        active       INTEGER NOT NULL DEFAULT 1,  -- detach = active=0 (reversible, mirrors migration-008); re-attach reactivates
        source       TEXT,                        -- provenance (e.g. 'promote_to_live')
        added_ts     INTEGER,
        removed_ts   INTEGER,                     -- when active last flipped to 0 (detach)
        PRIMARY KEY (account_id, category, wallet)  -- one attachment per (sub-division, whale); a whale-category may attach to MANY sub-divisions
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_pm_subattach_subdiv ON pm_subdivision_attachment(account_id, category, active)",
    "CREATE INDEX IF NOT EXISTS ix_pm_subattach_wallet ON pm_subdivision_attachment(wallet, category, active)",
]

# migration 012 (2026-08-29, Stage 3 R7.f-prep): per-sub-division LIQUIDITY RATIO.
# Jack RULED (2026-08-29): the gate-3 liquidity floor is a RATIO of THE ORDER'S OWN notional (default 0.75x),
# NOT a fixed $ and NOT bound to per_order_usd_cap. A 1-contract ~$0.50 order was demanding $25 of depth (50x its
# own size) and skipping every real match. The ratio is CONFIG (per sub-division, so different subs can differ),
# READ PER CYCLE (execution.sub_config_from_row -> a value change takes effect with NO engine restart, exactly like
# fixed_stake_usd), and DEFAULTED IN CODE (execution.CONFIG_DEFAULTS['liquidity_ratio']=0.75 so a NULL column reads
# as 0.75, never zero-depth-required). ** PURE DDL. ** Behaviour-neutral: existing rows get NULL -> code default;
# read by NO path until the new gate-3 code deploys. NUMBERED ON LANDING (next after 011). Idempotent via the
# schema_version guard (init_db runs a version's SQL exactly once; ADD COLUMN is not IF-NOT-EXISTS-able in SQLite).
MIGRATION_012: list[str] = [
    "ALTER TABLE pm_subdivision ADD COLUMN liquidity_ratio REAL",
]

MIGRATIONS: list[tuple[int, list[str]]] = [
    (1, MIGRATION_001),
    (2, MIGRATION_002),
    (3, MIGRATION_003),
    (4, MIGRATION_004),
    (5, MIGRATION_005),
    (6, MIGRATION_006),
    (7, MIGRATION_007),
    (8, MIGRATION_008),
    (9, MIGRATION_009),
    (10, MIGRATION_010),
    (11, MIGRATION_011),
    (12, MIGRATION_012),
]

# The head schema version = the highest migration number. Reference THIS from any "is the DB fully migrated?"
# check (tests, /healthz) so a new migration bumps ONE place instead of a manual sweep. Migration-SPECIFIC
# guards stay explicit (they assert behaviour at a fixed version, e.g. the migration-008 partial-DB test);
# ONLY is-at-head checks track this constant. (Earned 2026-08-28 after three migrations of manual head-pin
# sweeps -- Stage 3 R2.)
SCHEMA_HEAD: int = max(v for v, _ in MIGRATIONS)


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
