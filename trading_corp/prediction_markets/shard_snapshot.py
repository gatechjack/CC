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
