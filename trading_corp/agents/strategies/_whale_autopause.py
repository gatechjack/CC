"""Per-whale auto-pause helper for copy-trading strategies.

If a selected whale's resolved round-trips trip the threshold
(WR<MAX_WIN_RATE_PCT over MIN_RESOLVED_TRADES+ trades AND total
realized PnL<MAX_TOTAL_PNL), the caller removes them from
`agent_state.selected_whales` and emits an audit event so future
scans skip them.

Codifies the 2026-05-14 manual drops (tom14cat14 + 0xE9Ba...) as a
circuit breaker: a single bad whale can smear $50-$100 of paper
drawdown before a human notices — the 0xE9Ba case caused -$76 across
79 stale RTs once the multi-leg resolver fix flushed its backlog.

Thresholds are conjunctive on purpose: a streaky-but-net-profitable
whale shouldn't get paused, nor should a small-sample whale on a cold
streak. Catches the egregious case (0xE9Ba pre-drop: 36 RT / 11.1% WR
/ -$20.56 → would have triggered) while leaving tom14cat14 (87 RT /
39.1% WR / -$1.58 at drop time → pnl above threshold, did NOT
trigger) as a judgment call for the operator.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

MIN_RESOLVED_TRADES = 30
MAX_WIN_RATE_PCT = 40.0
MAX_TOTAL_PNL = -5.0

# Kalshi copy-trading go-live epoch — the forward-window boundary the operator
# dashboard uses for the Paper/Live split (entry_ts >= epoch = LIVE). Mirror of
# trading_corp/web/data.py:KALSHI_COPY_LIVE_EPOCH — KEEP THE TWO IN SYNC. Used
# as the default autopause window for Kalshi when no agent_state override is set,
# so the circuit breaker evaluates the SAME rows the operator sees rather than
# full, paper-contaminated history. See resolve_epoch().
KALSHI_COPY_LIVE_EPOCH = "2026-07-01T14:08:58+00:00"


def resolve_epoch(
    conn: sqlite3.Connection, agent: str, *, default: str | None = None,
) -> str | None:
    """Return agent_state(<agent>, 'metrics_epoch') as an ISO-8601 string, else
    `default` — the operator-visible forward window.

    This reads the exact slot web/data.py `_get_metrics_epoch` reads and the
    dashboard scopes per-whale P&L to (`entry_ts >= epoch`). Validation mirrors
    `_get_metrics_epoch`: the value is bound as a SQL parameter downstream, and
    we ISO-round-trip it as cheap defense so a garbage row can't silently scope
    the window to nothing. Any read/parse failure falls back to `default`.
    """
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT value_json FROM agent_state "
            "WHERE agent = ? AND key = 'metrics_epoch'",
            (agent,),
        )
        row = cur.fetchone()
    except sqlite3.Error:
        return default
    if not row or row[0] is None:
        return default
    raw = row[0]
    try:
        val = json.loads(raw)
    except (TypeError, ValueError):
        val = raw
    if not isinstance(val, str) or not val:
        return default
    try:
        datetime.fromisoformat(val)
    except (TypeError, ValueError):
        return default
    return val


def _query_whale_stats(
    conn: sqlite3.Connection,
    *,
    table: str,
    name_field: str,
    division: str,
    whale_name: str,
    since_ts: str | None = None,
) -> dict[str, Any]:
    # `since_ts` scopes the aggregate to the operator's forward window
    # (entry_ts >= epoch) so the breaker sees exactly the rows the dashboard
    # shows per whale. None = all-time (pre-fix behavior / no epoch set).
    # Bound as a parameter, never interpolated.
    since_clause = "AND entry_ts >= ?" if since_ts else ""
    params: list[Any] = [division]
    if since_ts:
        params.append(since_ts)
    params.append(whale_name)
    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT
          COUNT(*),
          COALESCE(SUM(CASE WHEN won=1 THEN 1 ELSE 0 END), 0),
          COALESCE(SUM(CASE WHEN won=0 THEN 1 ELSE 0 END), 0),
          COALESCE(SUM(realized_pnl), 0.0)
        FROM {table}
        WHERE division = ?
          AND won IS NOT NULL
          {since_clause}
          AND json_extract(extra_json, '$.{name_field}') = ?
        """,
        params,
    )
    row = cur.fetchone() or (0, 0, 0, 0.0)
    n_resolved = int(row[0] or 0)
    n_wins = int(row[1] or 0)
    n_losses = int(row[2] or 0)
    total_pnl = float(row[3] or 0.0)
    wr_pct = (100.0 * n_wins / n_resolved) if n_resolved > 0 else None
    return {
        "n_resolved": n_resolved,
        "n_wins": n_wins,
        "n_losses": n_losses,
        "win_rate_pct": round(wr_pct, 2) if wr_pct is not None else None,
        "total_realized_pnl": round(total_pnl, 4),
    }


def should_autopause(
    conn: sqlite3.Connection,
    *,
    whale_name: str,
    table: str,
    name_field: str,
    division: str,
    since_ts: str | None = None,
    min_trades: int = MIN_RESOLVED_TRADES,
    max_wr_pct: float = MAX_WIN_RATE_PCT,
    max_pnl: float = MAX_TOTAL_PNL,
) -> tuple[bool, dict[str, Any]]:
    """Decide whether `whale_name` qualifies for auto-pause.

    `table` = "polymarket_round_trips" or "kalshi_round_trips".
    `name_field` = JSON key in `extra_json` carrying the whale handle
    ("whale_user_name" for PCT, "whale_handle" for K3).
    `since_ts` = ISO-8601 forward-window boundary (entry_ts >= since_ts). When
    set, the breaker evaluates the SAME window the operator dashboard shows;
    None = all-time. Resolve it with `resolve_epoch(conn, agent)`.

    Returns (triggered, stats). Stats are returned regardless so callers
    can log them when desired.
    """
    stats = _query_whale_stats(
        conn,
        table=table,
        name_field=name_field,
        division=division,
        whale_name=whale_name,
        since_ts=since_ts,
    )
    triggered = (
        stats["n_resolved"] >= min_trades
        and stats["win_rate_pct"] is not None
        and stats["win_rate_pct"] < max_wr_pct
        and stats["total_realized_pnl"] < max_pnl
    )
    return triggered, stats


def sqlite_path_from_db_url(db_url: str) -> str | None:
    """Convert a SQLAlchemy-style 'sqlite:///path' URL to a raw path.

    Returns None for non-sqlite URLs (Postgres in some future migration).
    """
    prefix = "sqlite:///"
    if isinstance(db_url, str) and db_url.startswith(prefix):
        return db_url[len(prefix):]
    return None
