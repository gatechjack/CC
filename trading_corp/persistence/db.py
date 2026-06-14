"""SQLite engine + schema migration.

Schema is portable to Postgres (no SQLite-specific syntax beyond AUTOINCREMENT,
which we avoid). Phase 4 cloud deployment swaps the connection factory.
"""
from __future__ import annotations

import json
import logging
import random
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

log = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path("data/trading_corp.db")

# ── Stage-1 N+2 Phase 3 Session B Commit 2 (Decision 6.2): DB-lock retry ──
# Mirrors the schedule in `agents/logger.py:_DB_LOCK_RETRY_DELAYS_SEC` —
# duplicated here (3 floats, 1 tuple) rather than imported to keep the
# dependency arrow correct (persistence/db is foundational; agents/logger
# imports from it). 3 entries → up to 4 total attempts (1 initial + 3
# retries). Tests monkeypatch this to near-zero. Keep the two copies in
# sync; if you change one, change the other.
_DB_LOCK_RETRY_DELAYS_SEC: tuple[float, ...] = (0.1, 0.3, 0.7)

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
    extra_json   TEXT,
    execution_mode TEXT NOT NULL DEFAULT 'paper'  -- E2·5: 'paper' | 'live'; written at placement time
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

-- Structured paper-trade record. Written on every `would_have_placed`
-- emission (Otter + Cypher today, future TV-driven divisions when added).
-- Phase B of the would_have_placed enrichment (BACKLOG.md 2026-05-01):
-- captures the full trade specifics at emit time so a Phase C replay job
-- can join against minute-bar history and populate result_* columns.
--
-- Result columns (result, result_ts, result_price, actual_pnl_dollars,
-- actual_r_multiple, bars_to_resolution) are NULL until the replay job
-- writes them. `result` values: 'win' (TP hit first), 'loss' (SL hit
-- first), 'open' (neither hit yet), 'expired' (max_hold_seconds elapsed
-- without resolution).
--
-- Why a separate table vs. squeezing into audit_event.payload: replay
-- analysis is a JOIN on price history, and the dashboard panels filter
-- on (strategy, tier, result) — awkward against JSON LIKE queries.
CREATE TABLE IF NOT EXISTS paper_trade_record (
    order_id              TEXT PRIMARY KEY,        -- = proposed_order.id
    ts                    TEXT NOT NULL,           -- alert/emit ts (ISO UTC)
    strategy              TEXT NOT NULL,           -- 'lord_otter' | 'market_cypher'
    division              TEXT NOT NULL,           -- division slug
    symbol                TEXT NOT NULL,
    side                  TEXT NOT NULL,
    qty                   REAL NOT NULL,
    tier                  TEXT,
    source_signal         TEXT,
    entry_reference_price REAL,
    stop_price            REAL,
    tp_price              REAL,
    tp_r_multiple         REAL,
    expected_loss         REAL,                    -- = -max_dollar_risk
    expected_gain         REAL,
    rr_ratio              REAL,                    -- expected_gain / max_dollar_risk
    max_hold_seconds      INTEGER,                 -- frozen at write-time from strategy config
    -- Phase C fields, NULL until replay populates
    result                TEXT,                    -- 'win' | 'loss' | 'open' | 'expired'
    result_ts             TEXT,
    result_price          REAL,
    actual_pnl_dollars    REAL,
    actual_r_multiple     REAL,
    bars_to_resolution    INTEGER,
    extra_json            TEXT,
    execution_mode        TEXT NOT NULL DEFAULT 'paper'  -- E2·5: 'paper' | 'live'
);
CREATE INDEX IF NOT EXISTS ix_paper_trade_record_strategy_ts
    ON paper_trade_record(strategy, ts);
CREATE INDEX IF NOT EXISTS ix_paper_trade_record_result
    ON paper_trade_record(result);

-- Polymarket round-trips (resolved paper-mode and, in Phase 3+, live trades).
-- One row per `would_have_placed` audit event whose market has resolved on
-- gamma-api. Source of truth for the History tab + hit-rate / best-trade
-- stats + daily P&L aggregation on the betmoar.fun-style dashboard.
--
-- UNIQUE(order_id) so the hourly resolver can re-run safely (INSERT OR
-- IGNORE keyed on the proposed_order id).
--
-- Why a separate table vs. extending audit_event: the dashboard joins
-- against polymarket_equity_history + filters on (won, category, ts) —
-- awkward against JSON LIKE. Same rationale as paper_trade_record vs.
-- audit_event payloads.
CREATE TABLE IF NOT EXISTS polymarket_round_trips (
    id              INTEGER PRIMARY KEY,
    order_id        TEXT    NOT NULL UNIQUE,
    condition_id    TEXT    NOT NULL,
    slug            TEXT,
    market_question TEXT,
    category        TEXT,
    series          TEXT,
    -- `division` lets the same table serve both polymarket_arbitrage and
    -- polymarket_copy_trading round-trips. Added 2026-05-11 (Polymarket
    -- copy-trader division). Existing rows (created before the migration)
    -- default to 'polymarket_arbitrage' since that was the only producer.
    division        TEXT    NOT NULL DEFAULT 'polymarket_arbitrage',
    outcome_bet     TEXT    NOT NULL,           -- 'yes' | 'no'
    qty             REAL    NOT NULL,
    entry_price     REAL    NOT NULL,
    notional        REAL    NOT NULL,
    entry_ts        TEXT    NOT NULL,           -- audit-row ts
    resolved_ts     TEXT    NOT NULL,           -- when WE recorded the resolution
    yes_won         INTEGER NOT NULL,           -- 0|1 actual market outcome
    won             INTEGER NOT NULL,           -- 0|1 our side won
    realized_pnl    REAL    NOT NULL,
    roi_pct         REAL    NOT NULL,
    implied_at_entry REAL,
    llm_prob        REAL,
    divergence_pct  REAL,
    -- `entry_order_id` links a SELL-side round-trip row back to its
    -- entry's audit-event order_id when the resolver pairs a copy-
    -- trader's whale-exit with the prior whale-entry (vs. the existing
    -- market-settlement path where the same order_id is BOTH the entry
    -- and the resolution). Open-trade queries exclude audit rows whose
    -- order_id appears here so the matching entry doesn't double-show.
    entry_order_id  TEXT,
    extra_json      TEXT
);
-- ix_polymarket_round_trips_division — created in init_db() AFTER the
-- division-column migration, so old DBs upgrading from the pre-division
-- schema get the column added before the index references it.
CREATE INDEX IF NOT EXISTS ix_polymarket_round_trips_resolved_ts
    ON polymarket_round_trips(resolved_ts);
CREATE INDEX IF NOT EXISTS ix_polymarket_round_trips_category
    ON polymarket_round_trips(category);

-- Periodic snapshots of polymarket-division equity. 5-min cadence by
-- default. Source for the equity curve + period-over-period delta cards.
-- Append-only; ~100k rows/yr at 5-min cadence ≈ ~10 MB — leave alone
-- and prune offline if it ever gets large.
CREATE TABLE IF NOT EXISTS polymarket_equity_history (
    id              INTEGER PRIMARY KEY,
    ts              TEXT    NOT NULL,           -- ISO UTC
    division        TEXT    NOT NULL,
    equity          REAL    NOT NULL,
    cash_usdc       REAL    NOT NULL,
    positions_value REAL    NOT NULL,
    n_positions     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_polymarket_equity_history_division_ts
    ON polymarket_equity_history(division, ts);

-- Kalshi round-trips (Phase K2.4). Mirrors polymarket_round_trips but a
-- single table covers ALL three Kalshi strategies — tail-price arb, temporal/
-- bucket arb, and LLM-divergence arb. They differ in which audit fields are
-- populated:
--   * tail_price       : arb_type='tail',     edge_cents set, llm_prob NULL
--   * temporal_bucket  : arb_type='temporal'|'bucket', edge_cents set, llm_prob NULL
--   * llm_arbitrage    : arb_type='llm_divergence', llm_prob + divergence set
--
-- `strategy` + `division` columns let the dashboard filter to one strategy
-- or roll up to a division. Kalshi binary contracts settle to $1 (winner) /
-- $0 (loser) — same P&L math as polymarket per-share.
CREATE TABLE IF NOT EXISTS kalshi_round_trips (
    id              INTEGER PRIMARY KEY,
    order_id        TEXT    NOT NULL UNIQUE,
    ticker          TEXT    NOT NULL,
    event_ticker    TEXT,
    event_title     TEXT,
    category        TEXT,
    strategy        TEXT    NOT NULL,
    division        TEXT    NOT NULL,
    arb_type        TEXT,
    arb_set_id      TEXT,
    outcome_bet     TEXT    NOT NULL,           -- 'yes' | 'no'
    qty             REAL    NOT NULL,
    entry_price     REAL    NOT NULL,
    notional        REAL    NOT NULL,
    entry_ts        TEXT    NOT NULL,
    resolved_ts     TEXT    NOT NULL,
    market_result   TEXT    NOT NULL,           -- 'yes' | 'no' | 'void'
    won             INTEGER NOT NULL,           -- 0|1; always 0 for void
    realized_pnl    REAL    NOT NULL,
    roi_pct         REAL    NOT NULL,
    implied_at_entry REAL,
    llm_prob        REAL,
    divergence_pct  REAL,
    edge_cents      REAL,
    -- See polymarket_round_trips.entry_order_id docstring — same purpose
    -- for the Kalshi side (K3 copy-trader whale-exit pairing).
    entry_order_id  TEXT,
    extra_json      TEXT
);
CREATE INDEX IF NOT EXISTS ix_kalshi_round_trips_resolved_ts
    ON kalshi_round_trips(resolved_ts);
CREATE INDEX IF NOT EXISTS ix_kalshi_round_trips_division_ts
    ON kalshi_round_trips(division, resolved_ts);
CREATE INDEX IF NOT EXISTS ix_kalshi_round_trips_arb_type
    ON kalshi_round_trips(arb_type);

-- Kalshi equity snapshots (Phase K2.4). Per-division 5-min snapshots of
-- broker equity. kalshi_arbitrage and kalshi_llm_arbitrage share the same
-- Kalshi account today, so both divisions snapshot the same dollar figure —
-- the division column preserves logical separation for the dashboard and
-- for the day live broker work assigns per-division sub-accounts.
CREATE TABLE IF NOT EXISTS kalshi_equity_history (
    id              INTEGER PRIMARY KEY,
    ts              TEXT    NOT NULL,
    division        TEXT    NOT NULL,
    equity          REAL    NOT NULL,
    cash_usd        REAL    NOT NULL,
    positions_value REAL    NOT NULL,
    n_positions     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_kalshi_equity_history_division_ts
    ON kalshi_equity_history(division, ts);

-- weather_nbm_observations: per-station NBM probabilistic temperature
-- forecasts (deciles + stdev + mean) from NOMADS blend_nbptx text bulletins.
-- Write-only ingestion target for scripts/ingest_nbm.py; the strategy hot
-- path does not read this table until gated-consumption decision lands.
-- See plans/tier1-data-foundation-kalshi-weather.md §C1 for the design
-- and the icao_source / nbm_source drift sentinels.
CREATE TABLE IF NOT EXISTS weather_nbm_observations (
    station_id    TEXT NOT NULL,
    cycle_iso     TEXT NOT NULL,
    valid_iso     TEXT NOT NULL,
    kind          TEXT NOT NULL,
    horizon_hours REAL NOT NULL,
    temp_p10_f    REAL NOT NULL,
    temp_p20_f    REAL NOT NULL,
    temp_p50_f    REAL NOT NULL,
    temp_p70_f    REAL NOT NULL,
    temp_p90_f    REAL NOT NULL,
    temp_sigma_f  REAL NOT NULL,
    temp_mean_f   REAL NOT NULL,
    nbm_source    TEXT NOT NULL,
    icao_source   TEXT NOT NULL,
    ingest_mode   TEXT NOT NULL DEFAULT 'live_cron',
    ingested_at   TEXT NOT NULL,
    PRIMARY KEY (station_id, cycle_iso, valid_iso, kind)
);
CREATE INDEX IF NOT EXISTS ix_weather_nbm_station_valid
    ON weather_nbm_observations(station_id, valid_iso);

-- weather_forecast_residuals: per-station per-source per-target-day
-- residuals (actual_temp_f - forecast_temp_f) where actual_temp_f comes
-- from IEM CLI ground truth. Correction layer on top of NBM (not from-
-- scratch sigma). The logic_era field is a contamination guard: rows
-- with logic_era='pre_station_fix' carry forecasts generated against
-- the wrong-station coords that the 2026-05-22 xref commit corrected.
-- Calibration queries MUST filter WHERE logic_era != 'pre_station_fix'
-- to avoid re-introducing the very bug we fixed.
-- See plans/tier1-data-foundation-kalshi-weather.md §C2.
CREATE TABLE IF NOT EXISTS weather_forecast_residuals (
    station_id      TEXT NOT NULL,
    target_date     TEXT NOT NULL,
    kind            TEXT NOT NULL,
    target_iso      TEXT,
    forecast_temp_f REAL NOT NULL,
    actual_temp_f   REAL NOT NULL,
    forecast_source TEXT NOT NULL,
    horizon_hours   REAL NOT NULL,
    residual_f      REAL NOT NULL,
    cycle_iso       TEXT NOT NULL,
    season          TEXT NOT NULL,
    logic_era       TEXT NOT NULL,
    icao_source     TEXT NOT NULL,
    ingested_at     TEXT NOT NULL,
    PRIMARY KEY (station_id, target_date, kind, forecast_source, cycle_iso)
);
CREATE INDEX IF NOT EXISTS ix_wfr_station_horizon
    ON weather_forecast_residuals(station_id, horizon_hours, season, logic_era);
CREATE INDEX IF NOT EXISTS ix_wfr_target_date
    ON weather_forecast_residuals(target_date, kind);
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
        # Idempotent column-add migrations: SQLite doesn't have
        # ADD COLUMN IF NOT EXISTS, so we probe via PRAGMA table_info.
        _maybe_add_column(
            conn, "polymarket_round_trips", "division",
            "TEXT NOT NULL DEFAULT 'polymarket_arbitrage'",
        )
        # entry_order_id: copy-trader whale-exit pairing (2026-05-12).
        # NULL on legacy/market-settle rows; set on the SELL-side row
        # produced by `_pair_pending_exits` in the resolvers.
        _maybe_add_column(
            conn, "polymarket_round_trips", "entry_order_id", "TEXT",
        )
        _maybe_add_column(
            conn, "kalshi_round_trips", "entry_order_id", "TEXT",
        )
        # ingest_mode: distinguish historical_backfill vs live_cron NBM rows
        # (Tier 1 plan 2026-05-25 — historical S3 backfill alongside live cron poller).
        _maybe_add_column(
            conn, "weather_nbm_observations", "ingest_mode",
            "TEXT NOT NULL DEFAULT 'live_cron'",
        )
        # execution_mode (E2·5): classify each order/record by the execution path
        # actually taken — 'paper' (paper/would_have_placed path) vs 'live' (live
        # broker placement). REQUIRED in E2 because it can't be retrofitted (rows
        # written without it can't be classified after the fact). These tables are
        # paper-era to date, so the DEFAULT 'paper' correctly backfills existing
        # rows (no live placement has ever written here). Dashboard paper/live
        # filtering is BACKLOG — the column ships here, the UI does not.
        _maybe_add_column(
            conn, "proposed_order", "execution_mode",
            "TEXT NOT NULL DEFAULT 'paper'",
        )
        _maybe_add_column(
            conn, "paper_trade_record", "execution_mode",
            "TEXT NOT NULL DEFAULT 'paper'",
        )
        # Indexes that reference columns added by the migration above must
        # be created here (not in SCHEMA) so they apply AFTER the column
        # exists on upgraded DBs.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_polymarket_round_trips_division "
            "ON polymarket_round_trips(division)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_polymarket_round_trips_entry_order_id "
            "ON polymarket_round_trips(entry_order_id) "
            "WHERE entry_order_id IS NOT NULL"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_kalshi_round_trips_entry_order_id "
            "ON kalshi_round_trips(entry_order_id) "
            "WHERE entry_order_id IS NOT NULL"
        )
        conn.commit()
    return path


def _maybe_add_column(
    conn: "sqlite3.Connection", table: str, column: str, decl: str,
) -> None:
    """Idempotent ALTER TABLE ADD COLUMN. No-op if the column already exists.

    Used for forward-compat schema migrations on a long-lived prod DB. The
    canonical `CREATE TABLE IF NOT EXISTS` covers fresh installs; this
    covers upgrades.
    """
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column in existing:
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


@contextmanager
def connect(db_url: str = "sqlite:///data/trading_corp.db") -> Iterator[sqlite3.Connection]:
    path = resolve_db_path(db_url)
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
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


def insert_paper_trade_record(
    record: dict,
    db_url: str = "sqlite:///data/trading_corp.db",
) -> None:
    """INSERT OR IGNORE one paper_trade_record row, with DB-lock retry.

    `record` is the dict produced by `PaperTradeRecord.to_db_row()`. We use
    INSERT OR IGNORE keyed on order_id so the backfill script and the
    write-on-emit path don't collide — whichever wrote first wins.

    Decision 6.2 (Session B): on transient 'database is locked' errors
    retries up to len(`_DB_LOCK_RETRY_DELAYS_SEC`) times with jittered
    backoff (same schedule as `LoggerAgent.log_event`). On retry
    exhaustion: re-raises the OperationalError — the caller's existing
    try/except (already swallowing exceptions per the Path C +
    `_record_placement_outcome` paper-write patterns) handles it.

    INSERT OR IGNORE makes the retry idempotent: a row that landed on
    a previous attempt but timed out the connection becomes a no-op on
    retry. Non-lock OperationalErrors (e.g. schema drift) propagate
    immediately — those are real bugs, not transient contention.
    """
    cols = list(record.keys())
    placeholders = ",".join("?" for _ in cols)
    sql = (
        f"INSERT OR IGNORE INTO paper_trade_record ({','.join(cols)}) "
        f"VALUES ({placeholders})"
    )
    params = [record[c] for c in cols]

    attempt = 0  # 0 = initial; 1..N = retries
    while True:
        try:
            with connect(db_url) as conn:
                conn.execute(sql, params)
            if attempt > 0:
                log.warning(
                    "insert_paper_trade_record: succeeded after %d retries "
                    "(order_id=%s)", attempt, record.get("order_id"),
                )
            return

        except sqlite3.OperationalError as exc:
            if "database is locked" not in str(exc).lower():
                raise

            if attempt >= len(_DB_LOCK_RETRY_DELAYS_SEC):
                total_attempts = attempt + 1
                log.error(
                    "insert_paper_trade_record FAILED after %d attempts "
                    "(database locked): order_id=%s — re-raising for "
                    "caller to handle",
                    total_attempts, record.get("order_id"),
                )
                raise

            delay = _DB_LOCK_RETRY_DELAYS_SEC[attempt] * (0.5 + random.random())
            log.warning(
                "insert_paper_trade_record: database locked on attempt "
                "%d/%d; sleeping %.3fs before retry (order_id=%s)",
                attempt + 1,
                len(_DB_LOCK_RETRY_DELAYS_SEC) + 1,
                delay,
                record.get("order_id"),
            )
            time.sleep(delay)
            attempt += 1


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
