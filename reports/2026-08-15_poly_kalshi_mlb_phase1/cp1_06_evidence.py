#!/usr/bin/env python3
"""CP1 review evidence dump — raw, no summary. READ-ONLY (reads the run JSON)."""
from __future__ import annotations
import json
from pathlib import Path

WT = Path(__file__).resolve().parents[2]
J = json.loads((WT / "reports/2026-08-15_poly_kalshi_mlb_phase1/cp1_validation_out.json").read_text())
res = J["results"]
matched = [r for r in res if r["status"] == "matched"]

print("############ Q1: DOUBLEHEADER RECONCILIATION ############")
print(f"DH GAMES that existed on Kalshi in-window: {len(J['kalshi']['doubleheader_keys'])}")
for dh in J["kalshi"]["doubleheader_keys"]:
    print(f"\n  GAME: {dh['key']}  start-times={dh['times']}")
    for t in dh["tickers"]:
        print(f"      {t}")
# whale ML bets that landed on ANY of the 3 DH matchup-dates (regardless of status)
dh_keys = set()
for dh in J["kalshi"]["doubleheader_keys"]:
    date, teams = dh["key"].split("|", 1)
    dh_keys.add((date, frozenset(teams.split("/"))))
print("\n  WHALE ML BETS on a DH matchup-date (status shown):")
n_dh_bets = 0
for r in res:
    if r["market_type"] != "moneyline" or not r["away"] or not r["home"]:
        continue
    if (r["date"], frozenset({r["away"], r["home"]})) in dh_keys:
        n_dh_bets += 1
        print(f"      {r['whale']:22} {r['slug']:30} outcome={r['outcome']!r:22} "
              f"status={r['status']} conf={r['confidence']} cands={r['n_candidates']}")
print(f"\n  => DH GAMES existed: {len(J['kalshi']['doubleheader_keys'])} ; "
      f"whale-BETS landing on a DH: {n_dh_bets}")

print("\n############ Q2: 5 LOWEST-CONFIDENCE NON-DH MATCHES (closest to failing) ############")
non_dh = sorted((r for r in matched), key=lambda r: r["confidence"])
for r in non_dh[:5]:
    print(f"  conf={r['confidence']} {r['whale']:22} {r['slug']:30} outcome={r['outcome']!r:22} "
          f"reason={r['reason']}")
    print(f"       -> {r['kalshi_ticker']}")
print(f"  (distinct confidence values among all {len(matched)} matches: "
      f"{sorted(set(r['confidence'] for r in matched))})")

print("\n############ Q3: ALL 0.97 (nickname-resolved) MATCHES: title -> Kalshi ticker ############")
a97 = [r for r in matched if r["confidence"] == 0.97]
print(f"  count = {len(a97)}")
for i, r in enumerate(a97, 1):
    title = f"{r['away']} vs. {r['home']}"   # Poly ML title format
    print(f"  {i:2}. [{r['whale'][:12]:12}] title={title!r:46} outcome={r['outcome']!r:12} "
          f"slug={r['slug']}")
    print(f"       -> {r['kalshi_ticker']}")

print("\n############ Q4: MATCHED = DISTINCT MARKETS, NOT ACTIVITY ROWS ############")
rows_total = sum(v["rows"] for v in J["per_whale"].values())
mkts_total = sum(v["markets"] for v in J["per_whale"].values())
print("  per-whale  activity_rows -> distinct_markets (deduped by slug within whale):")
for w, v in J["per_whale"].items():
    print(f"      {w:22} rows={v['rows']:>6}  markets={v['markets']:>5}  capped={v['capped']}")
print(f"  TOTAL activity rows fetched : {rows_total}")
print(f"  TOTAL distinct markets      : {mkts_total}   (len(results)={len(res)})")
print(f"  matched records             : {len(matched)}")
# each matched record is one distinct (whale, slug); verify no (whale,slug) dup
ws = [(r["whale"], r["slug"]) for r in matched]
print(f"  distinct (whale,slug) among matched : {len(set(ws))}  "
      f"(== matched count? {len(set(ws)) == len(matched)})")
# a slug bet by >1 whale is legitimately >1 copy-trigger, not a double-count:
from collections import Counter
slug_whales = Counter(r["slug"] for r in matched)
multi = {s: n for s, n in slug_whales.items() if n > 1}
print(f"  matched slugs bet by >1 whale (each = separate copy-trigger, not a dup): {len(multi)}")
for s, n in list(multi.items())[:8]:
    print(f"      {s}  ({n} whales)")
