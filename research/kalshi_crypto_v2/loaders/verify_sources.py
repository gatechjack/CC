"""S3 hand-verify: one stored row per source re-fetched from its ORIGIN and
compared field-by-field (to the cent/satoshi). Catches unit/field-order/parse
errors. Read-only; runs safely alongside an in-progress pull.

Usage: python research/kalshi_crypto_v2/loaders/verify_sources.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _kalshi_auth import KalshiRest  # noqa: E402

os.environ.setdefault("KEY_VAULT_URI", "https://kv-tc-vtwbowt3wtkpy.vault.azure.net/")
T = int(datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc).timestamp())  # fixed probe minute
TMS = T * 1000


def close(a, b, tol=1e-6):
    return a is not None and b is not None and abs(float(a) - float(b)) <= tol


def verify_binance(conn) -> None:
    print("\n[Binance] BTC @ 2026-07-01 12:00 UTC")
    row = conn.execute("SELECT open,high,low,close,volume FROM lab_bars_binance"
                       " WHERE asset='BTC' AND ts_ms=?", (TMS,)).fetchone()
    k = common.http_get("https://data-api.binance.vision/api/v3/klines",
                        params={"symbol": "BTCUSDT", "interval": "1m",
                                "startTime": TMS, "endTime": TMS, "limit": 1})[0]
    origin = (float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]))
    print(f"  stored : {row}")
    print(f"  origin : {origin}")
    ok = all(close(a, b) for a, b in zip(row, origin))
    print(f"  MATCH  : {ok}")


def verify_coinbase(conn) -> None:
    print("\n[Coinbase] BTC @ 2026-07-01 12:00 UTC")
    row = conn.execute("SELECT open,high,low,close,volume FROM lab_bars_coinbase"
                       " WHERE asset='BTC' AND ts_ms=?", (TMS,)).fetchone()
    arr = common.http_get("https://api.exchange.coinbase.com/products/BTC-USD/candles",
                          params={"granularity": 60, "start": T, "end": T})
    c = next((x for x in arr if int(x[0]) == T), arr[0] if arr else None)
    # Exchange order: [time, low, high, open, close, volume]
    origin = (float(c[3]), float(c[2]), float(c[1]), float(c[4]), float(c[5]))
    print(f"  stored : {row}")
    print(f"  origin : {origin}  (remapped from [t,low,high,open,close,vol])")
    ok = all(close(a, b) for a, b in zip(row, origin))
    print(f"  MATCH  : {ok}")
    # cross-source sanity: Binance vs Coinbase close within a few bps
    bc = conn.execute("SELECT close FROM lab_bars_binance WHERE asset='BTC' AND ts_ms=?",
                      (TMS,)).fetchone()
    if bc and row:
        bps = abs(row[3] - bc[0]) / bc[0] * 1e4
        print(f"  x-check: coinbase close {row[3]} vs binance {bc[0]} = {bps:.1f} bps")


def verify_coinalyze(conn) -> None:
    print("\n[Coinalyze] BTC 1hour @ 2026-07-01 12:00 UTC")
    stored = {m: v for m, v in conn.execute(
        "SELECT metric,value FROM lab_coinalyze WHERE asset='BTC' AND interval='1hour'"
        " AND ts_ms=? AND metric IN ('price_c','buy_vol','vol','oi_c')", (TMS,))}
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient
    key = SecretClient(vault_url=os.environ["KEY_VAULT_URI"],
                       credential=DefaultAzureCredential()).get_secret("coinalyze-api-key").value
    data = common.http_get("https://api.coinalyze.net/v1/ohlcv-history",
                           headers={"api_key": key}, throttle=1.0,
                           params={"symbols": "BTCUSDT_PERP.A", "interval": "1hour",
                                   "from": T, "to": T})
    pt = next((p for p in (data[0].get("history") or []) if int(p["t"]) == T), None)
    print(f"  stored : {stored}")
    print(f"  origin : t={T} c={pt.get('c')} bv={pt.get('bv')} v={pt.get('v')}" if pt else "  origin: none")
    if pt:
        print(f"  MATCH  : price_c={close(stored.get('price_c'), pt['c'])}"
              f" buy_vol={close(stored.get('buy_vol'), pt['bv'])}"
              f" vol={close(stored.get('vol'), pt['v'])}")


def verify_kalshi(conn) -> None:
    print("\n[Kalshi] one 15m candle re-fetched from origin")
    row = conn.execute(
        "SELECT series,market_ticker,end_period_ts,yes_bid_close,price_mean,volume"
        " FROM lab_kalshi_candles LIMIT 1").fetchone()
    if not row:
        print("  (no candles pulled yet - skip; re-run after the 15m pull)")
        return
    series, tkr, ep, yb_c, pm, vol = row
    rest = KalshiRest()
    resp = rest.get(f"/series/{series}/markets/{tkr}/candlesticks",
                    {"period_interval": 1, "start_ts": ep - 60, "end_ts": ep + 60})
    o = next((c for c in (resp.get("candlesticks") or []) if c.get("end_period_ts") == ep), None)
    print(f"  stored : {tkr} ep={ep} yes_bid_close={yb_c} price_mean={pm} vol={vol}")
    if o:
        print(f"  origin : yb_close={o['yes_bid'].get('close_dollars')}"
              f" price_mean={o['price'].get('mean_dollars')} vol={o.get('volume_fp')}")
        print(f"  MATCH  : yb_close={close(yb_c, o['yes_bid'].get('close_dollars'))}"
              f" mean={close(pm, o['price'].get('mean_dollars'))}"
              f" vol={close(vol, o.get('volume_fp'), tol=0.01)}")


def main() -> int:
    import sqlite3
    conn = sqlite3.connect(common.labdb.LAB_DB)   # read-only path; no migrate/write
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        verify_binance(conn)
        verify_coinbase(conn)
        verify_coinalyze(conn)
        verify_kalshi(conn)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
