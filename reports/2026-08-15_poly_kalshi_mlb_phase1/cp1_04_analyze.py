#!/usr/bin/env python3
"""CP1 analysis — validate the run output + guard against silent bugs. READ-ONLY.

(a) spot-check matched pairs; (b) prove out_of_window are genuinely pre-window
dates (not a date-match bug hiding real matches); (c) inspect the lone
no_kalshi_contract; (d) show the 0.97 (nickname-resolved) cases.
Also re-fetches KXMLBGAME and rigorously scans parse-coverage + any same
date+teams multiplicity, so the '0 doubleheaders' claim is airtight.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT))
os.environ.setdefault("KEY_VAULT_URI", "https://kv-tc-vtwbowt3wtkpy.vault.azure.net/")
ENV_FILE = Path(r"C:\Users\AA Incorporado\cc\.env")
J = json.loads((WT / "reports/2026-08-15_poly_kalshi_mlb_phase1/cp1_validation_out.json").read_text())

WIN_LO, WIN_HI = "2026-06-09", "2026-08-18"


def analyze_json():
    print("=== 12 SPOT-CHECK MATCHED PAIRS (Poly -> Kalshi) ===")
    for r in J["spot_checks"]:
        print(f"  {r['whale'][:12]:12} {r['slug']:34} outcome={r['outcome']!r:24}")
        print(f"      -> {r['kalshi_ticker']}  conf={r['confidence']} ({r['reason']})")

    res = J["results"]
    oow = [r for r in res if r["status"] == "out_of_window"]
    oow_dates = [r["date"] for r in oow if r["date"]]
    inband = [d for d in oow_dates if WIN_LO <= d <= WIN_HI]
    print("\n=== OUT_OF_WINDOW date guard ===")
    print(f"  out_of_window ML: {len(oow)}; date span {min(oow_dates)}..{max(oow_dates)}")
    print(f"  *** in-band [{WIN_LO}..{WIN_HI}] but flagged out_of_window: {len(inband)} "
          f"(should be 0; >0 = date-match bug) ***")
    if inband:
        for r in [x for x in oow if x["date"] in inband][:8]:
            print(f"      BUG? {r['slug']} date={r['date']}")

    print("\n=== no_kalshi_contract_in_window cases ===")
    for r in [r for r in res if r["status"] == "no_kalshi_contract"]:
        print(f"  {r['whale']} {r['slug']} {r['away']} @ {r['home']} {r['date']} outcome={r['outcome']!r}")

    print("\n=== 0.97 (nickname/substring-resolved) matched cases ===")
    for r in [r for r in res if r["status"] == "matched" and r["confidence"] == 0.97][:12]:
        print(f"  {r['slug']:34} outcome={r['outcome']!r:22} -> {r['kalshi_ticker']}")

    print("\n=== matched by whale ===")
    by = Counter(r["whale"] for r in res if r["status"] == "matched")
    for w, n in by.most_common():
        print(f"  {w:24} {n}")


async def kalshi_scan():
    from trading_corp.utils.secrets import load_secrets
    from trading_corp.brokers.kalshi import KalshiBroker
    from trading_corp.data.sports_team_mapping import parse_sports_ticker
    s = load_secrets(ENV_FILE)
    b = KalshiBroker(api_key_id=s.kalshi_api_key_id, private_key_pem=s.kalshi_private_key_pem)
    await b.connect()
    from pykalshi import MarketStatus
    min_ts = int(time.time()) - 160 * 86400
    tickers = []
    for st, ex in ((MarketStatus.OPEN, {}), (MarketStatus.SETTLED, {"min_close_ts": min_ts})):
        ms = await b._client.get_markets(series_ticker="KXMLBGAME", status=st, limit=1000,
                                         fetch_all=(st == MarketStatus.SETTLED), **ex)
        tickers += [getattr(m, "ticker", "") or "" for m in ms]
    tickers = list(set(tickers))
    parsed_ok, parse_fail = 0, []
    game_times = defaultdict(set)   # (date, teams) -> {HHMM}
    for t in tickers:
        p = parse_sports_ticker(t)
        if p is None or p.team_a_name is None or p.team_b_name is None:
            parse_fail.append(t)
            continue
        parsed_ok += 1
        game_times[(p.date_str, frozenset({p.team_a_name, p.team_b_name}))].add(p.time_str)
    dh = {k: v for k, v in game_times.items() if len(v) > 1}
    print("\n=== KALSHI PARSE/DOUBLEHEADER SCAN (airtight) ===")
    print(f"  unique tickers: {len(tickers)}  parsed_ok: {parsed_ok}  parse_FAIL: {len(parse_fail)}")
    for t in parse_fail[:20]:
        print(f"      FAIL: {t}")
    print(f"  distinct (date,teams) games: {len(game_times)}")
    print(f"  same date+teams with >1 start-time (doubleheaders): {len(dh)}")
    for k, v in list(dh.items())[:10]:
        print(f"      {k[0]} {sorted(k[1])} times={sorted(v)}")


async def main():
    analyze_json()
    await kalshi_scan()

if __name__ == "__main__":
    asyncio.run(main())
