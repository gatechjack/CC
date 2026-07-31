"""earnings_watch_db.py — the PEAD Upcoming-Earnings watcher's OWN database + schema + connections.

ISOLATION CORNERSTONE (mirrors market_context/context_db.py):
  * The watcher writes ONLY to its OWN separate file `~/pead_earnings/earnings_watch.db` — NEVER to
    the engine's `trading_corp.db`. A SEPARATE file = zero write-lock contention with the engine
    (single-writer WAL). This process is the ONLY writer to earnings_watch.db.
  * The engine DB is opened strictly READ-ONLY (`file:...?mode=ro`) — the ONLY place this process
    touches engine data (to learn which PEAD names are already held) and it physically cannot write it.

DISPLAY/CAPTURE-ONLY. Never imports the engine trade path / never restarts the engine.

One row per (code, report_date). `phase` = 'upcoming' (pre-report; actual NULL; screen + SUE
PLAUSIBILITY) or 'reported' (post-report; actual printed; EXACT computed SUE). Adding fields later =
add a column with a migration guard here; the dashboard reads this file mode=ro.
"""
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


# ── paths (env-overridable so tests / local dry-runs point at temp files) ─────
def _home() -> Path:
    return Path.home()


def watch_db_path() -> Path:
    return Path(os.environ.get(
        "PEAD_WATCH_DB", str(_home() / "pead_earnings" / "earnings_watch.db"))).expanduser()


def engine_db_path() -> Path:
    return Path(os.environ.get(
        "PEAD_WATCH_ENGINE_DB", str(_home() / "trading_corp" / "data" / "trading_corp.db"))).expanduser()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_DDL = (
    """CREATE TABLE IF NOT EXISTS earnings_watch (
        code                  TEXT NOT NULL,   -- ticker WITHOUT the .US suffix (e.g. AAPL)
        report_date           TEXT NOT NULL,   -- YYYY-MM-DD announcement (calendar) date
        report_time           TEXT,            -- 'BeforeMarket' | 'AfterMarket' | NULL  (BMO/AMC)
        fiscal_period_end     TEXT,            -- EODHD 'date' (fiscal period end)
        estimate              REAL,            -- consensus EPS estimate (NULL if none)
        actual                REAL,            -- printed EPS (NULL pre-report)
        difference            REAL,            -- actual - estimate (post-report)
        surprise_pct          REAL,            -- EODHD 'percent' (post-report)
        in_universe           INTEGER NOT NULL,-- 1 if in config/nasdaq_composite.txt (engine parse)
        already_held          INTEGER,         -- 1 if we currently hold this PEAD name (engine RO)
        screen_ok             INTEGER,         -- 1 pass / 0 fail / NULL not-evaluated
        screen_reason         TEXT,            -- 'ok' or the pead_signal machine tag of the failing gate
        price                 REAL,            -- last daily close (yfinance) used by the screen
        avg_vol_30d           REAL,            -- 30d avg share volume used by the screen
        market_cap            REAL,            -- EODHD Highlights market cap used by the screen
        sector                TEXT,            -- EODHD General sector used by the screen
        days_to_next_earnings INTEGER,         -- trading days to the earnings AFTER this report (NULL=unknown/lenient)
        n_quarters            INTEGER,         -- depth of actual EPS history available
        sue_latest            REAL,            -- latest REALIZED SUE (last printed quarter) — plausibility context
        sue_stdev             REAL,            -- stdev(UE, trailing `lookback`) — the own-noise denominator
        sue_hitrate           REAL,            -- fraction of trailing quarters with SUE > threshold
        sue_plausible         INTEGER,         -- 1 if this name plausibly prints SUE > threshold (own-history)
        computed_sue          REAL,            -- EXACT SUE once reported (post-announcement only)
        phase                 TEXT NOT NULL,   -- 'upcoming' | 'reported'
        note                  TEXT,            -- per-row note (e.g. 'no_bars', 'insufficient_eps_history')
        fetched_ts            TEXT NOT NULL,   -- when this row was last refreshed (ISO UTC)
        PRIMARY KEY (code, report_date)
    )""",
    "CREATE INDEX IF NOT EXISTS ix_ew_report_date ON earnings_watch(report_date)",
    "CREATE INDEX IF NOT EXISTS ix_ew_phase ON earnings_watch(phase)",
    "CREATE INDEX IF NOT EXISTS ix_ew_inuni ON earnings_watch(in_universe, report_date)",
    """CREATE TABLE IF NOT EXISTS watch_meta (
        key        TEXT PRIMARY KEY,
        value      TEXT,
        updated_ts TEXT NOT NULL
    )""",
)

# column order for upsert (must match _UPSERT_SQL)
COLUMNS = (
    "code", "report_date", "report_time", "fiscal_period_end", "estimate", "actual",
    "difference", "surprise_pct", "in_universe", "already_held", "screen_ok", "screen_reason",
    "price", "avg_vol_30d", "market_cap", "sector", "days_to_next_earnings", "n_quarters",
    "sue_latest", "sue_stdev", "sue_hitrate", "sue_plausible", "computed_sue", "phase",
    "note", "fetched_ts",
)
_UPSERT_SQL = (
    "INSERT OR REPLACE INTO earnings_watch (" + ",".join(COLUMNS) + ") "
    "VALUES (" + ",".join("?" * len(COLUMNS)) + ")"
)


@contextmanager
def connect_rw():
    """Read-WRITE connection to the watcher's OWN db (creates the dir if needed). WAL +
    synchronous=NORMAL mirror the engine's pragmas; SEPARATE file so it never contends with the
    engine's writer."""
    p = watch_db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), isolation_level=None, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        yield conn
    finally:
        conn.close()


@contextmanager
def connect_engine_ro():
    """STRICTLY READ-ONLY connection to the engine DB (`mode=ro`). A write on this handle raises
    sqlite3.OperationalError — this is the ONLY place the watcher touches engine data."""
    uri = f"file:{engine_db_path().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_schema() -> None:
    with connect_rw() as conn:
        for stmt in _DDL:
            conn.execute(stmt)


def upsert_rows(conn, rows) -> int:
    """Upsert earnings_watch rows. `rows` = iterable of dicts keyed by COLUMNS (missing -> NULL).
    `conn` is a connect_rw() handle. Returns count."""
    payload = [tuple(r.get(c) for c in COLUMNS) for r in rows]
    conn.executemany(_UPSERT_SQL, payload)
    return len(payload)


def set_meta(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO watch_meta (key, value, updated_ts) VALUES (?,?,?)",
        (key, str(value), now_iso()),
    )


def prune_before(conn, cutoff_report_date: str) -> int:
    """Drop rows whose report_date is strictly older than the cutoff (keeps the table bounded to the
    rolling window + a short post-report tail). Returns rows deleted."""
    cur = conn.execute("DELETE FROM earnings_watch WHERE report_date < ?", (cutoff_report_date,))
    return cur.rowcount if cur.rowcount is not None else 0


def held_pead_symbols(conn_ro, division: str = "robinhood_pead") -> set:
    """READ-ONLY: symbols with an OPEN PEAD position (result IS NULL). Ledger-based (no hardcoded
    names). Returns an uppercased set. Fails soft to empty on any schema surprise."""
    try:
        rows = conn_ro.execute(
            "SELECT DISTINCT symbol FROM paper_trade_record WHERE division=? AND result IS NULL",
            (division,),
        ).fetchall()
        return {str(r[0]).upper() for r in rows if r and r[0]}
    except sqlite3.Error:
        return set()
