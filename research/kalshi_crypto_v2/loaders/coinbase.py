"""S3.2 Coinbase 1m OHLCV loader -> lab_bars_coinbase.

Mechanism note: the codebase crypto_spot_provider uses ccxt.coinbase (Advanced
Trade), but that path's since-pagination stalls on historical 1m depth (verified
2026-08-02: it returned only ~5 days then a short page). We use the Coinbase
Exchange public candles REST (api.exchange.coinbase.com) instead — SAME venue
(Coinbase spot), deterministic windowed pulls, 300 candles/req. Keyless,
read-only. Candle order is [time, low, high, open, close, volume], DESCENDING.

Gaps do NOT stop the walk (advance by window like the bitunix fetcher).

Usage: python research/kalshi_crypto_v2/loaders/coinbase.py [--start-ms N]
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

BASE = "https://api.exchange.coinbase.com/products/{pid}/candles"
PRODUCT = {"BTC": "BTC-USD", "ETH": "ETH-USD", "SOL": "SOL-USD", "XRP": "XRP-USD"}
WIN = 300  # candles per request (Coinbase cap)


def fetch_asset(conn, asset: str, start_ms: int, end_ms: int) -> int:
    url = BASE.format(pid=PRODUCT[asset])
    cursor = start_ms // 1000                 # epoch seconds
    end_s = end_ms // 1000
    total = 0
    while cursor <= end_s:
        win_end = min(cursor + WIN * 60, end_s)
        rows = common.http_get(url, params={
            "granularity": 60, "start": cursor, "end": win_end}, throttle=0.16)
        if rows:
            recs = [(asset, int(c[0]) * 1000, float(c[3]), float(c[2]),
                     float(c[1]), float(c[4]), float(c[5] or 0.0)) for c in rows]
            conn.executemany(
                "INSERT OR REPLACE INTO lab_bars_coinbase"
                "(asset,ts_ms,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?)", recs)
            conn.commit()
            total += len(recs)
        # advance by the full window regardless of gaps/short pages
        cursor = win_end + 60
        if total and total % 30000 < WIN:
            print(f"  {asset}: {total:>7} rows, through {common.iso(cursor * 1000)}", flush=True)
    return total


def main() -> int:
    start_ms = common.PERIOD_START_MS
    if "--start-ms" in sys.argv:
        start_ms = int(sys.argv[sys.argv.index("--start-ms") + 1])
    end_ms = common.now_ms()
    conn = common.connect()
    conn.execute("DELETE FROM lab_bars_coinbase")   # single consistent source (Exchange REST)
    conn.commit()
    print(f"Coinbase Exchange OHLCV 1m  {common.iso(start_ms)} -> {common.iso(end_ms)}")
    try:
        for asset in common.ASSETS:
            n = fetch_asset(conn, asset, start_ms, end_ms)
            ts = [r[0] for r in conn.execute(
                "SELECT ts_ms FROM lab_bars_coinbase WHERE asset=?", (asset,))]
            cov = common.minute_coverage(ts, start_ms, end_ms)
            common.write_coverage(conn, "coinbase", asset, cov["rows"],
                                  cov["min_ts"], cov["max_ts"], len(cov["gaps"]),
                                  f"missing={cov['missing']} gap_frac={cov['gap_frac']:.4%}")
            print(f"{asset}: rows={cov['rows']} span={common.iso(cov['min_ts'])}"
                  f"..{common.iso(cov['max_ts'])} missing={cov['missing']}"
                  f" ({cov['gap_frac']:.3%}) gap_runs={len(cov['gaps'])}", flush=True)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
