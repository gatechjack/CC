"""S3.1 Binance 1m klines loader -> lab_bars_binance.

api.binance.com is HTTP-451 (US geoblock); data-api.binance.vision serves the
SAME klines keyless. Store raw OHLCV (open-time keyed). Coverage-instrumented.

Usage: python research/kalshi_crypto_v2/loaders/binance.py [--start-ms N]
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

BASE = "https://data-api.binance.vision/api/v3/klines"
SYMBOL = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "XRP": "XRPUSDT"}
LIMIT = 1000  # max klines per request


def fetch_asset(conn, asset: str, start_ms: int, end_ms: int) -> int:
    sym = SYMBOL[asset]
    cursor = start_ms
    total = 0
    while cursor <= end_ms:
        rows = common.http_get(BASE, params={
            "symbol": sym, "interval": "1m", "startTime": cursor,
            "endTime": end_ms, "limit": LIMIT}, throttle=0.15)
        if not rows:
            break
        recs = [(asset, int(k[0]), float(k[1]), float(k[2]), float(k[3]),
                 float(k[4]), float(k[5])) for k in rows]
        conn.executemany(
            "INSERT OR REPLACE INTO lab_bars_binance"
            "(asset,ts_ms,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?)", recs)
        conn.commit()
        total += len(recs)
        last_open = int(rows[-1][0])
        nxt = last_open + common.MINUTE_MS
        if nxt <= cursor:            # no forward progress -> stop
            break
        cursor = nxt
        if len(rows) < LIMIT:        # reached the live edge
            break
        print(f"  {asset}: {total:>7} rows, through {common.iso(last_open)}", flush=True)
    return total


def main() -> int:
    start_ms = common.PERIOD_START_MS
    if "--start-ms" in sys.argv:
        start_ms = int(sys.argv[sys.argv.index("--start-ms") + 1])
    end_ms = common.now_ms()
    conn = common.connect()
    print(f"Binance klines 1m  {common.iso(start_ms)} -> {common.iso(end_ms)}")
    try:
        for asset in common.ASSETS:
            n = fetch_asset(conn, asset, start_ms, end_ms)
            # coverage from what actually landed
            ts = [r[0] for r in conn.execute(
                "SELECT ts_ms FROM lab_bars_binance WHERE asset=?", (asset,))]
            cov = common.minute_coverage(ts, start_ms, end_ms)
            common.write_coverage(conn, "binance", asset, cov["rows"],
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
