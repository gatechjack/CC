"""Phase-1 (kalshi_crypto_v2) read-only depth probe of bitunix_bar_history.

Reports, per (symbol, timeframe): row count, earliest/latest bar (UTC),
span in days, and coverage vs. the ideal contiguous bar count for that
timeframe. Coverage < 100% means gaps (venue downtime, archiver misses),
which matters for the SFP retro-test (PIVOT_LEN=50 needs 50 contiguous
bars each side of a pivot).

READ-ONLY: opens the DB with mode=ro; no writes, no project imports.
Usage:  run_capped python research/kalshi_crypto_v2/probe_bar_depth.py [DB_PATH]
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timezone

DEFAULT_DB = r"C:\Users\AA Incorporado\cc\data\trading_corp.db"

TF_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
    "30m": 1_800_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000,
}


def _iso(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def main() -> int:
    db_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DB
    if not os.path.exists(db_path):
        print(f"DB NOT FOUND: {db_path}")
        return 1
    mtime = datetime.fromtimestamp(os.path.getmtime(db_path), tz=timezone.utc)
    size_mb = os.path.getsize(db_path) / 1e6
    print(f"DB: {db_path}")
    print(f"    size={size_mb:.0f} MB   file_mtime(UTC)={mtime:%Y-%m-%d %H:%M}")

    uri = "file:" + db_path.replace("\\", "/").replace(" ", "%20") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    cur = conn.cursor()

    exists = cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='bitunix_bar_history'"
    ).fetchone()
    if not exists:
        print("\nTABLE bitunix_bar_history: ABSENT in this DB.")
        return 2

    total = cur.execute("SELECT COUNT(*) FROM bitunix_bar_history").fetchone()[0]
    print(f"\nbitunix_bar_history total rows: {total:,}")

    rows = cur.execute(
        "SELECT symbol, timeframe, COUNT(*), MIN(ts_ms), MAX(ts_ms) "
        "FROM bitunix_bar_history GROUP BY symbol, timeframe "
        "ORDER BY symbol, timeframe"
    ).fetchall()

    hdr = f"{'symbol':10} {'tf':5} {'rows':>8} {'earliest(UTC)':16} {'latest(UTC)':16} {'span_d':>7} {'cov%':>6}"
    print("\n" + hdr)
    print("-" * len(hdr))
    for sym, tf, n, mn, mx in rows:
        span_ms = mx - mn
        span_d = span_ms / 86_400_000
        step = TF_MS.get(tf)
        if step:
            ideal = span_ms // step + 1
            cov = 100.0 * n / ideal if ideal else 0.0
            cov_s = f"{cov:5.1f}"
        else:
            cov_s = "  n/a"
        print(f"{sym:10} {tf:5} {n:>8,} {_iso(mn):16} {_iso(mx):16} {span_d:>7.1f} {cov_s:>6}")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
