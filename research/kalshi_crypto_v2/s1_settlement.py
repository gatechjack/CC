"""S1 (Phase 2): settlement-mechanics verification — GATES all backtests.

Pulls the actual rules text + settlement_sources per target series (per cadence,
per asset), quotes them verbatim, and re-verifies 3 settled markets to the cent
under the stated rule (incl. one that settled CLOSE to strike). The literature
review flagged possible BRTI(real-time)-vs-BRR(daily) conflation and
simple-vs-trimmed averaging; this reports the exact wording so the operator can
rule it out. READ-ONLY signed GET, in-memory creds.

Usage: run_capped python research/kalshi_crypto_v2/s1_settlement.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _kalshi_auth import KalshiAuthError, KalshiRest  # noqa: E402

SERIES = [
    ("KXBTC15M", "15m up/down", "BTC"), ("KXETH15M", "15m up/down", "ETH"),
    ("KXSOL15M", "15m up/down", "SOL"), ("KXXRP15M", "15m up/down", "XRP"),
    ("KXBTC", "hourly ladder", "BTC"), ("KXETH", "hourly ladder", "ETH"),
    ("KXSOLE", "hourly ladder", "SOL"), ("KXXRP", "hourly ladder", "XRP"),
]


def latest_settled(rest, s):
    r = rest.get("/markets", {"series_ticker": s, "status": "settled", "limit": 1})
    ms = r.get("markets", [])
    return ms[0] if ms else None


def verify(rest, series, m, label):
    print(f"\n--- HAND-VERIFY [{label}] {m['ticker']} ---")
    fl, ev, res = m.get("floor_strike"), m.get("expiration_value"), m.get("result")
    for k in ("event_ticker", "strike_type", "floor_strike", "open_time", "close_time",
              "expiration_value", "settlement_value_dollars", "result", "last_price_dollars"):
        print(f"    {k:24} = {m.get(k)}")
    try:
        evf, flf = float(ev), float(fl)
        move = (evf - flf) / flf * 100
        implied = "yes" if evf >= flf else "no"
        ok = "MATCH" if implied == res else "MISMATCH"
        print(f"    settle check: index {evf} vs strike {flf}  (move {move:+.4f}%) -> "
              f"'{implied}' vs result '{res}'  [{ok}]")
    except (TypeError, ValueError):
        print("    settle check: non-numeric (skipped)")


def main() -> int:
    try:
        rest = KalshiRest()
    except KalshiAuthError as e:
        print(f"STOP - creds: {e}")
        return 2
    print(f"creds source={rest.source}\n")
    print("=" * 78)
    print("SETTLEMENT RULES per series (verbatim rules_primary + settlement_sources)")
    print("=" * 78)
    for s, cad, asset in SERIES:
        try:
            sd = rest.get(f"/series/{s}").get("series", {})
        except KalshiAuthError as e:
            print(f"\n{s} ({cad}, {asset}): series fetch ERR {e}")
            sd = {}
        srcs = sd.get("settlement_sources") or []
        src_names = ", ".join(f"{x.get('name')}" for x in srcs) if srcs else "(none listed)"
        m = latest_settled(rest, s)
        print(f"\n### {s}  [{cad} / {asset}]   frequency={sd.get('frequency')} "
              f"fee_type={sd.get('fee_type')}")
        print(f"  settlement_sources: {src_names}")
        if m:
            print(f"  rules_primary  : {m.get('rules_primary')}")
            if m.get("rules_secondary"):
                print(f"  rules_secondary: {m.get('rules_secondary')}")
        else:
            print("  (no settled market found)")

    print("\n" + "=" * 78)
    print("HAND-VERIFICATIONS (3, incl. one CLOSE to strike)")
    print("=" * 78)
    # two recent settled across assets
    m_btc = latest_settled(rest, "KXBTC15M")
    if m_btc:
        verify(rest, "KXBTC15M", m_btc, "recent BTC 15m")
    m_eth = latest_settled(rest, "KXETH15M")
    if m_eth:
        verify(rest, "KXETH15M", m_eth, "recent ETH 15m")
    # closest-to-strike: scan a batch of recent settled BTC 15m for min |move|
    batch = rest.paginated("/markets", "markets",
                           {"series_ticker": "KXBTC15M", "status": "settled", "limit": 1000}, 2)
    cand = []
    for m in batch:
        try:
            fl, ev = float(m["floor_strike"]), float(m["expiration_value"])
            if fl:
                cand.append((abs((ev - fl) / fl), m))
        except (TypeError, ValueError, KeyError):
            pass
    if cand:
        cand.sort(key=lambda x: x[0])
        verify(rest, "KXBTC15M", cand[0][1], f"CLOSEST-to-strike (|move|={cand[0][0]*100:.5f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
