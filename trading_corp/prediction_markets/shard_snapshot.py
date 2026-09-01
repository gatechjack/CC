"""Per-account PER-SHARD balance snapshots -- M3 (2026-09-01). The ENGINE writes (it holds each account's keypair);
pm_web READS the latest (credential-free -- it never touches the venue). The snapshot stores EVERY shard, not the
masked total, because the total hides an empty funding shard -- the exact state that silently killed Karen's
division (a healthy ~$515 total while the MLB funding shard held ~$2). The read carries the AGE + a staleness BAND:
a stale balance shown as current is that same failure shape, so the age travels WITH the number (same discipline as
the thin-sample caveat). Accumulating snapshots are a balance HISTORY -> the shard-proceeds direction (proven
return-to-3 by arithmetic) becomes CONTINUOUSLY verifiable, and a CHANGE would be visible, not just a one-off.

Pure sqlite + json + stdlib -- imports NO broker (pm_web-safe). The ShardBalances it persists comes from the
engine's shard_balance.parse_balance read; the reader re-hydrates it. Position-indexed reads (no row_factory
dependency), and every read tolerates the table being absent (pre-migration-016) -> honest-empty, never a 500.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass

from .shard_balance import ShardBalances

_TABLE = "pm_shard_balance_snapshot"

# staleness bands (seconds) for the display -- snapshot cadence is ~5 min, so >3 cadences amber, >1h red (writer down?).
FRESH_MAX_SEC = 15 * 60
STALE_MAX_SEC = 60 * 60


def _table_exists(conn) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (_TABLE,)).fetchone() is not None


def write_snapshot(conn, account_id: str, sb: ShardBalances, snapshot_ts: int) -> None:
    """ENGINE-side: persist one per-account shard-balance snapshot. by_shard is stored as JSON with STRING keys
    (JSON has no int keys); the reader converts back to int. `total_dollars` is kept only to cross-check the split
    vs the sum. Commits (the cadence task owns the connection). Raises if the table is absent -- the engine has run
    migrations at boot, so a missing table is a real fault, not a silent skip."""
    by_shard = {str(int(k)): float(v) for k, v in (sb.by_shard or {}).items()}
    conn.execute(
        "INSERT INTO %s (account_id, snapshot_ts, total_dollars, by_shard_json, has_breakdown, updated_ts) "
        "VALUES (?,?,?,?,?,?)" % _TABLE,
        (account_id, int(snapshot_ts), float(sb.total_dollars), json.dumps(by_shard),
         1 if sb.has_breakdown else 0, sb.updated_ts))
    conn.commit()


@dataclass(frozen=True)
class SnapshotView:
    account_id: str
    snapshot_ts: int
    total_dollars: float
    by_shard: dict                # {exchange_index:int -> dollars:float}
    has_breakdown: bool           # False = the split is UNKNOWN (subaccount-restricted key) -> show 'unknown', never $0
    age_sec: int
    age_band: str                 # 'fresh' | 'stale' | 'very_stale' -- the display bands the AGE
    updated_ts: int | None = None


def age_band(age_sec: int) -> str:
    if age_sec <= FRESH_MAX_SEC:
        return "fresh"
    if age_sec <= STALE_MAX_SEC:
        return "stale"
    return "very_stale"


def table_present(conn) -> bool:
    """Whether pm_shard_balance_snapshot EXISTS. The page needs to distinguish TWO honest-empty states: the table
    ABSENT (migration 016 not applied -> 'arrives with the engine writer') vs PRESENT-but-empty (016 applied,
    engine has not written yet -> 'no snapshot yet, the engine writes every 5 min'). A blank reads the same for
    both; these do not."""
    return _table_exists(conn)


@dataclass(frozen=True)
class ShardDirection:
    verdict: str                  # 'returning' | 'rising' | 'building' (building = not enough history to judge)
    n_snapshots: int
    shard0_first: float
    shard0_last: float
    span_sec: int


def shard_direction(conn, account_id: str, *, rise_dollars: float = 0.50, min_span_sec: int = 3600):
    """Is shard-0 RISING (proceeds sweeping to shard 0 -- Karen's silent-death shape) or FLAT (return-to-3, proven
    by arithmetic 2026-09-01)? The one LINE the page shows so the standing check is read daily, not only via the
    runner. Reads the account's snapshot HISTORY. 'building' when <2 snapshots or the span is < ~1h (too short to
    judge). None if the table is absent. Read-only, defensive."""
    if not _table_exists(conn):
        return None
    rows = conn.execute(
        "SELECT snapshot_ts, by_shard_json FROM %s WHERE account_id = ? ORDER BY snapshot_ts" % _TABLE,
        (account_id,)).fetchall()
    def _s0(r):
        try:
            return float((json.loads(r[1]) or {}).get("0", 0.0))
        except (TypeError, ValueError):
            return 0.0
    if len(rows) < 2:
        return ShardDirection("building", len(rows), _s0(rows[0]) if rows else 0.0, _s0(rows[-1]) if rows else 0.0, 0)
    f0, l0 = _s0(rows[0]), _s0(rows[-1])
    span = int(rows[-1][0]) - int(rows[0][0])
    if span < min_span_sec:
        return ShardDirection("building", len(rows), f0, l0, span)
    return ShardDirection("rising" if (l0 - f0) > rise_dollars else "returning", len(rows), f0, l0, span)


def read_latest(conn, account_id: str, *, now_ts: int | None = None) -> "SnapshotView | None":
    """pm_web-side: the LATEST snapshot for one account, with its AGE + staleness band, or None if none exists / the
    table is absent (pre-migration-016 -> honest-empty). Never touches the venue. Position-indexed so it works with
    or without a Row factory."""
    if not _table_exists(conn):
        return None
    r = conn.execute(
        "SELECT account_id, snapshot_ts, total_dollars, by_shard_json, has_breakdown, updated_ts "
        "FROM %s WHERE account_id = ? ORDER BY snapshot_ts DESC, id DESC LIMIT 1" % _TABLE, (account_id,)).fetchone()
    if r is None:
        return None
    ts = int(r[1])
    try:
        by_shard = {int(k): float(v) for k, v in (json.loads(r[3]) or {}).items()}
    except (TypeError, ValueError):
        by_shard = {}
    now = int(now_ts if now_ts is not None else time.time())
    age = max(0, now - ts)
    return SnapshotView(account_id=r[0], snapshot_ts=ts, total_dollars=float(r[2]), by_shard=by_shard,
                        has_breakdown=bool(r[4]), age_sec=age, age_band=age_band(age),
                        updated_ts=(int(r[5]) if r[5] is not None else None))
