"""S3.4 Kalshi per-settled-market 1m candles loader.

Two phases, both resumable:
  enumerate : paginate settled markets for the 4x 15m + 4x hourly-ladder series
              -> lab_kalshi_markets (raw metadata; drives candles + S4 labels).
  candles   : per market not yet pulled, GET .../candlesticks?period_interval=1
              chunked at the 5000-candle cap -> lab_kalshi_candles; mark pulled.

Signed read-only REST (KAREN acct), creds in-memory from KV. Reports row counts
per series + progress. The ladder pull is the big one (many strike markets/hour).

Usage:
  python research/kalshi_crypto_v2/loaders/kalshi.py --probe-one   # dump 1 candle JSON
  python research/kalshi_crypto_v2/loaders/kalshi.py --enumerate
  python research/kalshi_crypto_v2/loaders/kalshi.py --candles [--series KXBTC15M]
  python research/kalshi_crypto_v2/loaders/kalshi.py                # enumerate then candles
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _kalshi_auth import KalshiAuthError, KalshiRest  # noqa: E402

os.environ.setdefault("KEY_VAULT_URI", "https://kv-tc-vtwbowt3wtkpy.vault.azure.net/")

SERIES = [
    ("KXBTC15M", "BTC", "15m"), ("KXETH15M", "ETH", "15m"),
    ("KXSOL15M", "SOL", "15m"), ("KXXRP15M", "XRP", "15m"),
    ("KXBTC", "BTC", "ladder"), ("KXETH", "ETH", "ladder"),
    ("KXSOLE", "SOL", "ladder"), ("KXXRP", "XRP", "ladder"),
]
CANDLE_CAP = 5000
CHUNK_S = CANDLE_CAP * 60  # 5000 one-min candles worth of seconds
CANDLE_THROTTLE = 0.16     # ~6 signed req/s, respectful of rate limits


def _series_for(kind: str | None):
    return [s for s in SERIES if not kind or s[2] == kind]


def epoch(iso: str | None) -> int | None:
    if not iso:
        return None
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


def _num(m: dict, *keys):
    for k in keys:
        v = m.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return None


def enumerate_markets(rest: KalshiRest, conn, kind_filter: str | None = None) -> None:
    print(f"== enumerate settled markets (kind={kind_filter or 'all'}) ==", flush=True)
    for series, asset, kind in _series_for(kind_filter):
        markets = rest.paginated("/markets", "markets",
                                 {"series_ticker": series, "status": "settled", "limit": 1000},
                                 max_pages=300)
        recs = []
        for m in markets:
            recs.append((
                series, m.get("ticker"), m.get("event_ticker"), asset, kind,
                m.get("market_type"), m.get("strike_type"),
                _num(m, "floor_strike"), _num(m, "cap_strike"),
                epoch(m.get("open_time")), epoch(m.get("close_time")),
                epoch(m.get("expected_expiration_time") or m.get("expiration_time")),
                m.get("result"),
                # settle = the close-60s-avg RTI (expiration_value), NOT the 0/1
                # binary payout (settlement_value_dollars). Needed for move_pct.
                _num(m, "expiration_value", "settlement_value"),
                _num(m, "last_price_dollars", "last_price"), m.get("status")))
        conn.executemany(
            "INSERT OR IGNORE INTO lab_kalshi_markets"
            "(series,market_ticker,event_ticker,asset,kind,market_type,strike_type,"
            "floor_strike,cap_strike,open_ts,close_ts,expiration_ts,result,"
            "settlement_value,last_price,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", recs)
        conn.commit()
        print(f"  {series:10} {kind:6} settled markets: {len(markets)}", flush=True)


def _candle_row(series: str, tkr: str, c: dict) -> tuple:
    # Kalshi candles report prices as *_dollars strings and vol/oi as *_fp
    # strings (with plain keys as an older-API fallback). Store raw dollars.
    yb, ya, pr = c.get("yes_bid") or {}, c.get("yes_ask") or {}, c.get("price") or {}
    return (series, tkr, c.get("end_period_ts"),
            _num(yb, "open_dollars", "open"), _num(yb, "high_dollars", "high"),
            _num(yb, "low_dollars", "low"), _num(yb, "close_dollars", "close"),
            _num(ya, "open_dollars", "open"), _num(ya, "high_dollars", "high"),
            _num(ya, "low_dollars", "low"), _num(ya, "close_dollars", "close"),
            _num(pr, "mean_dollars", "mean"),
            _num(c, "volume_fp", "volume"), _num(c, "open_interest_fp", "open_interest"))


def pull_candles(rest: KalshiRest, conn, only_series: str | None,
                 kind_filter: str | None = None) -> None:
    print(f"== pull candles (series={only_series or kind_filter or 'all'}) ==", flush=True)
    for series, asset, kind in _series_for(kind_filter):
        if only_series and series != only_series:
            continue
        rows = list(conn.execute(
            "SELECT market_ticker,open_ts,expiration_ts,close_ts FROM lab_kalshi_markets"
            " WHERE series=? AND candles_pulled=0", (series,)))
        total_c, done, errs = 0, 0, 0
        for tkr, ot, et, ct in rows:
            st = (ot or ct or 0) - 120
            en = (et or ct or 0) + 120
            got = 0
            cur = st
            while cur <= en:
                chunk_end = min(cur + CHUNK_S, en)
                try:
                    resp = rest.get(f"/series/{series}/markets/{tkr}/candlesticks",
                                    {"period_interval": 1, "start_ts": cur, "end_ts": chunk_end})
                except KalshiAuthError as e:
                    errs += 1
                    if errs <= 3:
                        print(f"    {tkr}: ERR {str(e)[-50:]}", flush=True)
                    break
                time.sleep(CANDLE_THROTTLE)
                arr = resp.get("candlesticks", []) or []
                if arr:
                    conn.executemany(
                        "INSERT OR REPLACE INTO lab_kalshi_candles(series,market_ticker,"
                        "end_period_ts,yes_bid_open,yes_bid_high,yes_bid_low,yes_bid_close,"
                        "yes_ask_open,yes_ask_high,yes_ask_low,yes_ask_close,price_mean,"
                        "volume,open_interest) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        [_candle_row(series, tkr, c) for c in arr])
                    got += len(arr)
                cur = chunk_end + 60
            conn.execute("UPDATE lab_kalshi_markets SET candles_pulled=1 WHERE market_ticker=?", (tkr,))
            total_c += got
            done += 1
            if done % 500 == 0:
                conn.commit()
                print(f"  {series}: {done}/{len(rows)} markets, {total_c} candles", flush=True)
        conn.commit()
        print(f"  {series:10} DONE {done} markets, {total_c} candles, {errs} errs", flush=True)


def fix_settle(rest: KalshiRest, conn, kind_filter: str | None = None) -> None:
    """One-time correction: UPDATE settlement_value to expiration_value (close RTI)
    for already-enumerated markets, preserving candles_pulled. Fixes rows written
    before the enumerate mapping was corrected. Run AFTER the candle pull."""
    print(f"== fix settle (kind={kind_filter or 'all'}) ==", flush=True)
    for series, asset, kind in _series_for(kind_filter):
        markets = rest.paginated("/markets", "markets",
                                 {"series_ticker": series, "status": "settled", "limit": 1000},
                                 max_pages=300)
        upd = [(_num(m, "expiration_value", "settlement_value"), m.get("ticker")) for m in markets]
        upd = [(v, t) for v, t in upd if v is not None and t]
        conn.executemany("UPDATE lab_kalshi_markets SET settlement_value=? WHERE market_ticker=?", upd)
        conn.commit()
        print(f"  {series:10} updated {len(upd)} settle values", flush=True)


def probe_one(rest: KalshiRest, conn) -> None:
    row = conn.execute(
        "SELECT series,market_ticker,open_ts,expiration_ts,close_ts FROM lab_kalshi_markets"
        " ORDER BY close_ts DESC LIMIT 1").fetchone()
    if not row:
        print("no markets enumerated yet; run --enumerate first")
        return
    series, tkr, ot, et, ct = row
    resp = rest.get(f"/series/{series}/markets/{tkr}/candlesticks",
                    {"period_interval": 1, "start_ts": (ot or ct) - 120, "end_ts": (et or ct) + 120})
    arr = resp.get("candlesticks", []) or []
    print(f"market {tkr} ({series}): {len(arr)} candles")
    if arr:
        print("first candle raw JSON:\n" + json.dumps(arr[0], indent=2)[:900])
        print("\nparsed row:", _candle_row(series, tkr, arr[0]))


def main() -> int:
    try:
        rest = KalshiRest()
    except KalshiAuthError as e:
        print(f"STOP creds: {e}")
        return 2
    conn = common.connect()
    try:
        if "--probe-one" in sys.argv:
            probe_one(rest, conn)
            return 0
        kind = None
        if "--kind" in sys.argv:
            kind = sys.argv[sys.argv.index("--kind") + 1]
        if "--fix-settle" in sys.argv:
            fix_settle(rest, conn, kind)
            return 0
        do_enum = "--enumerate" in sys.argv or "--candles" not in sys.argv
        do_cand = "--candles" in sys.argv or "--enumerate" not in sys.argv
        if do_enum:
            enumerate_markets(rest, conn, kind)
        if do_cand:
            only = None
            if "--series" in sys.argv:
                only = sys.argv[sys.argv.index("--series") + 1]
            pull_candles(rest, conn, only, kind)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
