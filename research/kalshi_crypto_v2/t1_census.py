"""T1 census + history-depth probe for Kalshi crypto markets (BTC/ETH/SOL/XRP).

For each target series: cadence, asset, live (open) count, settled count +
earliest/latest settled close, and candle granularity available on a settled
market. Plus ONE hand-verified settled market end-to-end (ticker/strike/expiry/
resolution + a to-the-cent candle read + the BRTI settlement check).

READ-ONLY signed GET, in-memory creds. Prices are dollars (0-1).
Usage: run_capped python research/kalshi_crypto_v2/t1_census.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _kalshi_auth import KalshiAuthError, KalshiRest  # noqa: E402

# Discovered 2026-08-01 (t1_explore). fifteen_min = 15-min up/down;
# hourly "range" = strike ladder; hourly "D" = above/below (secondary).
TARGETS = [
    ("KXBTC15M", "15m up/down", "BTC"), ("KXETH15M", "15m up/down", "ETH"),
    ("KXSOL15M", "15m up/down", "SOL"), ("KXXRP15M", "15m up/down", "XRP"),
    ("KXBTC", "hourly ladder", "BTC"), ("KXETH", "hourly ladder", "ETH"),
    ("KXSOL", "hourly ladder", "SOL"), ("KXSOLE", "hourly ladder", "SOL"),
    ("KXXRP", "hourly ladder", "XRP"),
]
SECONDARY = ["KXBTCD", "KXETHD", "KXSOLD", "KXXRPD"]  # hourly above/below
PAGES = 10
LIMIT = 1000


def epoch(iso: str | None) -> int | None:
    if not iso:
        return None
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


def short(iso: str | None) -> str:
    return (iso or "")[:16].replace("T", " ")


def census_series(rest: KalshiRest, s: str) -> dict:
    d = {"series": s}
    op = rest.get("/markets", {"series_ticker": s, "status": "open", "limit": LIMIT})
    d["n_open"] = len(op.get("markets", []) or [])
    settled = rest.paginated("/markets", "markets",
                             {"series_ticker": s, "status": "settled", "limit": LIMIT}, PAGES)
    d["n_settled"] = len(settled)
    d["capped"] = d["n_settled"] >= PAGES * LIMIT
    closes = [m.get("close_time") for m in settled if m.get("close_time")]
    d["earliest"] = min(closes) if closes else None
    d["latest"] = max(closes) if closes else None
    # candle-granularity probe on the most-recent settled market
    d["candles_1m"] = None
    if settled:
        recent = max(settled, key=lambda m: m.get("close_time") or "")
        tkr = recent["ticker"]
        st = epoch(recent.get("open_time"))
        en = epoch(recent.get("expected_expiration_time") or recent.get("close_time"))
        if st and en:
            try:
                cs = rest.get(f"/series/{s}/markets/{tkr}/candlesticks",
                              {"period_interval": 1, "start_ts": st - 120, "end_ts": en + 120})
                d["candles_1m"] = len(cs.get("candlesticks", []) or [])
            except KalshiAuthError as e:
                d["candles_1m"] = f"err:{str(e)[-40:]}"
        d["_recent"] = recent
    return d


def hand_verify(rest: KalshiRest, series: str, m: dict) -> None:
    print("\n" + "=" * 72)
    print("HAND-VERIFY (one settled market, end-to-end)")
    print("=" * 72)
    tkr, ev = m["ticker"], m["event_ticker"]
    floor = m.get("floor_strike")
    exp_val = m.get("expiration_value")
    result = m.get("result")
    for k in ("ticker", "event_ticker", "market_type", "strike_type", "floor_strike",
              "open_time", "close_time", "expiration_time", "expiration_value",
              "settlement_value_dollars", "result", "last_price_dollars",
              "yes_bid_dollars", "yes_ask_dollars", "no_bid_dollars", "no_ask_dollars"):
        print(f"  {k:26} = {m.get(k)}")
    print(f"  rules_primary(head)        = {(m.get('rules_primary') or '')[:110]}")
    # BRTI settlement check: up/down settles YES iff settled BRTI >= floor_strike
    try:
        ev_f, fl_f = float(exp_val), float(floor)
        implied = "yes" if ev_f >= fl_f else "no"
        ok = "MATCH" if implied == result else "MISMATCH"
        print(f"\n  BRTI check: expiration_value {ev_f} >= floor_strike {fl_f} -> "
              f"'{implied}' vs result '{result}'  [{ok}]")
    except (TypeError, ValueError):
        print("\n  BRTI check: non-numeric strike/expiration_value (skipped)")
    # to-the-cent candle read near close
    st, en = epoch(m.get("open_time")), epoch(m.get("expected_expiration_time") or m.get("close_time"))
    try:
        cs = rest.get(f"/series/{series}/markets/{tkr}/candlesticks",
                      {"period_interval": 1, "start_ts": st - 120, "end_ts": en + 120})
        arr = cs.get("candlesticks", []) or []
        print(f"\n  1m candles fetched: {len(arr)}")
        if arr:
            print("  last candle (verbatim):")
            print("    " + json.dumps(arr[-1])[:400])
    except KalshiAuthError as e:
        print(f"\n  candle note: {e}")
    slug = series.lower()
    print(f"\n  Kalshi page (operator cross-check): https://kalshi.com/markets/{slug}")
    print(f"  event: {ev}   market: {tkr}")


def main() -> int:
    try:
        rest = KalshiRest()
    except KalshiAuthError as e:
        print(f"STOP - creds: {e}")
        return 2
    print(f"creds source={rest.source}\n")

    hdr = f"{'series':10} {'cadence':14} {'asset':5} {'open':>5} {'settled':>8} {'cap':>4} {'earliest':16} {'latest':16} {'1m_candles':>10}"
    print(hdr + "\n" + "-" * len(hdr))
    rows = []
    for s, cad, asset in TARGETS:
        try:
            d = census_series(rest, s)
        except KalshiAuthError as e:
            print(f"{s:10} {cad:14} {asset:5}  ERR {e}")
            continue
        rows.append((s, cad, asset, d))
        print(f"{s:10} {cad:14} {asset:5} {d['n_open']:>5} {d['n_settled']:>8} "
              f"{('Y' if d['capped'] else '-'):>4} {short(d['earliest']):16} "
              f"{short(d['latest']):16} {str(d['candles_1m']):>10}")

    print("\nsecondary hourly above/below series (open counts):")
    for s in SECONDARY:
        try:
            op = rest.get("/markets", {"series_ticker": s, "status": "open", "limit": LIMIT})
            print(f"  {s:10} open={len(op.get('markets', []) or [])}")
        except KalshiAuthError as e:
            print(f"  {s:10} ERR {e}")

    # hand-verify the most-recent settled market of the first series that has one
    for s, cad, asset, d in rows:
        if d.get("_recent"):
            hand_verify(rest, s, d["_recent"])
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
