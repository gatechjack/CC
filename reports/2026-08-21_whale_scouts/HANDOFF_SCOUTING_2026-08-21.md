# HANDOFF — whale scouting + closed-positions characterization (2026-08-21 night)

Tonight = READ-ONLY research + paper-farm roster adds. **No prod code changed, live loop untouched.**

## STATE (confirmed read-only at wrap)
- **Live: `poly_kalshi_mlb` ARMED but geo-blocked (known, DEFERRED — no action).** Engine PID **809127**,
  `auto_execute=True/dry_run=False/halted=False`, **live_whales (2) = SDTrading + xifutloong3** (unchanged).
  Geoblock still firing (366×403/15min, last 2026-08-22 00:35 UTC) — Kalshi city-string "Washington" (Dulles VA egress) issue, root-caused earlier, deferred.
- **PCT PAPER farm = 10 whales, all pinned** (`polymarket_copy_trader/selected_whales` + `pinned_whales`):
  UFC(5): Kh4mz4t, STC14, 000why000, 4751346, kutsumiakia · NFL(2): FordBronco, **AIisTheNewWD\*** · NBA(1): BetMechanic · FED(2): Kickstand7, pako.
  **\*AIisTheNewWD = the TRUNCATED-MIRAGE name — its scouted +$103k/39-0 baseline is NOT trustworthy** (partial record); farmed only to observe forward.
- **No live_whales / prod mutation tonight beyond paper-roster agent_state adds.** Confirmed.

## SCOUT SUMMARY (one line each; detail in SCOUT_RESULTS.md)
- **UFC** = CLEAN (low-freq, complete records). 5 farmed (Kh4mz4t/STC14 strongest).
- **NFL** = 1 real (**FordBronco** +5.6% ROI, but 2024-25-season/stale) + 1 MIRAGE (AIisTheNewWD, truncated). Re-scout in-season.
- **NBA** = method BROKE (universal 5,000-row truncation; even S-Works n=0). Only **BetMechanic** maybe (+11% ROI but PARTIAL) — farmed to observe forward.
- **Fed** = mostly CHALK (scanner 18-0 but +1.3% ROI; d1k21 91%-win/−29% ROI). **Kickstand7** (win_px 0.77, the only real forecasting-edge profile, but n=3); **pako** intriguing (+$629k) but truncated.

## ⭐ KEY ARTIFACT — closed-positions API characterization (CLOSED_POSITIONS_API_FINDINGS.md)
`data-api.polymarket.com/closed-positions?user=<wallet>` gives **COMPLETE cross-category per-whale history with
DIRECT `realizedPnl`, no truncation** (≥3,050 positions/wallet, all categories in one call, same-day fresh) —
**solves the scout-method ceiling.** Wallets that returned n=0 under `/activity` return full histories here.
- **A-vs-B: B (closed-positions) is the record-keeping backbone; A (reconstruct-from-trades) is DROPPED.**
- **Architecture: `/closed-positions` for the historical record DB + `/activity` for live copy-signal detection.**
  Entry-timing is NOT a requirement (the old ~15m lag was a Kalshi-native/Apify artifact, not a Poly concern).
- DB schema sketch (whale_closed_position core + whale_category_stats rollup + open + activity) in the findings doc.

## NEXT WORKSTREAM (NOT started — Jack's call)
Scope a `/closed-positions` → DB ingestion job (per-whale backfill + `whale_category_stats` rollup) as the
maturing all-categories platform foundation. Schema sketch is in the findings doc + memory anchors.

## FORWARD OBSERVATIONS accruing on paper (via resolver → `polymarket_round_trips`)
UFC 5 + FordBronco + BetMechanic + Kickstand7 + pako (+ AIisTheNewWD*). PCT is category-agnostic → papers
whatever they bet in any category (not category-scoped). Watch net records build with fresh, complete data.

## AUTHORITATIVE STATE
Memory anchor `poly-kalshi-mlb-live-2026-08-16` (division) + `ufc-scout-and-paper-add-2026-08-21` (all scouts +
paper adds) + `poly-closed-positions-data-foundation-2026-08-22` (the API finding + schema sketch).

## Ops (runners archived under runners/)
State confirm: `pk_session_wrap_ro.ps1`. Rollbacks: `pk_add_{ufc,nfl,betmechanic,fed}_paper.ps1 -Reverse`
(newest backup `pk_paper_roster_bak_20260822_001726.json` restores the pre-Fed 8-state; each add's backup chains).
