# realizedPnl Semantics Probe — RESULT (pre-build, read-only)

> **★ CORRECTED 2026-08-22 — see `QUARANTINE_RECONCILE_2026-08-22.md`. Two claims below were WRONG,
> found by running the ACTUAL §3A ingest code on live rows:**
> 1. **"Fed proven clean / Fed rollups are safe" (verdict, Probe D, blast radius) is OVERSTATED.** Probe D's
>    `mirror_events` counter only detects >=2 cids sharing an identical realized (the -$574k echo) and rightly
>    found none in Fed — but the §3A clause (b) is stricter and flags Kickstand7's `fed-interest-rates-january-2025`
>    zero-cost dust leg (tb=0, rp=-0.50), propagating to 2 winner legs = **3/83 Fed rows quarantined**. pako Fed
>    IS clean (0). Cleanliness is WHALE-DEPENDENT; the quarantine is load-bearing on Fed.
> 2. **"The defect does NOT touch the four P1 categories" is WRONG.** The §3A **clause (a)** (loss-exceeds-cost)
>    FALSE-POSITIVES on ordinary single-game **MLB** losses for BOTH live MLB whales (SDTrading 5/462,
>    xifutloong3 **17/201**) because `/closed-positions total_bought` understates cost on scale-in rows. As
>    written, the quarantine **excludes real losses -> biases the scoreboard UP.** This is a blocking §3A design
>    finding (proposals in the reconcile doc; clause (b) is sound, clause (a) needs rework). NOT yet fixed.


**Run:** 2026-08-22 via `pk_realizedpnl_probe_ro.ps1` (then `pk_rpp_fetch_ro.ps1` chunked retrieval). Reuses `PolymarketDataAPIClient`. Read-only, public API, zero DB/engine/writes. Full output = 315 lines / 22.6 KB (raw appended below).

## ONE-LINE VERDICT (scoped — the central question)

**`realized_pnl` is NOT uniformly per-leg-real.** It is **DECOUPLED from cost basis for true negRisk winner-take-all markets** (Politics/"who will win X": `total_bought=0` legs carry `realized_pnl=-$574,604.31`) — a real, confirmed data-semantics defect that makes naive `Σ realized_pnl` wrong for those markets. It is **PER-LEG REAL for binary markets, including all four live P1 categories** — **Fed proven clean** (Probe D: 0 mirror events across Kickstand7+pako, realized tracks total_bought), MLB/NBA/UFC are binary with no pathology observed. **UFC cross-source reconciliation is INCONCLUSIVE** (Probe A: activity reconstruction under-counts redemptions; no negRisk pathology, but not positively verified).

## The decisive evidence (Probe B)

5 rows with `realized_pnl == -574604.31` to the cent, all in ONE event `presidential-election-winner-2024`, DISTINCT conditionIds:

```
slug=will-bernie-sanders-win-...   avg_price=0.5  total_bought=0.00  realized_pnl=-574604.31  cur_price=0.00
slug=will-vivek-ramaswamy-win-...  avg_price=0.5  total_bought=0.00  realized_pnl=-574604.31  cur_price=0.00
slug=will-elizabeth-warren-win-... avg_price=0.5  total_bought=0.00  realized_pnl=-574604.31  cur_price=0.00
slug=will-kanye-west-win-...       avg_price=0.5  total_bought=0.00  realized_pnl=-574604.31  cur_price=0.00
slug=will-chris-christie-win-...   avg_price=0.5  total_bought=0.00  realized_pnl=-574604.31  cur_price=0.00
EXTRA (all unmapped raw fields incl negRisk*): {}   <-- no negRisk flag in /closed-positions
```

Full event = 21 legs / 17 distinct cids. **The impossibility:** `realized_pnl < -total_bought` (loss exceeds cost) on many legs — `bought=0.00 realized=-574604.31`, ..., `bought=15750.78 realized=-535322.95`. This is negRisk NO-conversion / event-level P&L attribution, not per-leg economic loss. Summing these poisons both the ROI numerator (huge phantom losses) and the denominator (`total_bought=0`).

**This corrects plan §7's premise** ("realizedPnl is direct per position, SELL+REDEEM-BUY equivalent") — TRUE for binary markets, FALSE for negRisk winner-take-all.

## Findings table

| Probe | Question | Finding | Evidence |
|---|---|---|---|
| A | Does evanng's closed UFC slice reconcile to scout −$13,706.51 & to per-cid activity(S+R−B)? | **DOES NOT reconcile.** closed UFC Σ=+$9,778.97 (n=75); activity(S+R−B) Σ=+$1,141.72 (matched 75, Δ=+$8,637.25); scout=−$13,706.51 (3rd value). Likely cause: activity reconstruction under-counts redemption/settlement proceeds (activity < closed for winners) + per-cid vs per-asset grain confound. **No negRisk pathology in UFC.** Not a smoking gun; not positively verified either. | raw §Probe A |
| B | Are the −$574,604.31 rows mirrored/equal/real? | **DECOUPLED.** 1 event, 5 distinct cids, `total_bought=0.00`, `realized=-574604.31`. Loss on $0 cost = impossible per-leg → negRisk event-level attribution. `negRisk` keys absent from /closed-positions. | raw §Probe B |
| C | Do independent BUY fills ~$574k exist per cid in /activity? | **INCONCLUSIVE.** d1k21 /activity truncates at 5,000 rows (`truncated=True`) and returns 0 rows for the 2024 cids (too old). Cannot reconstruct. (Probe B's `total_bought=0` is already decisive.) | raw §Probe C |
| D | Does the pathology reach the live Fed category (Kickstand7, pako)? | **NO.** Kickstand7: 79 fed rows / 24 events / **0 mirror events**. pako: 39 / 19 / **0**. realized_pnl tracks total_bought on every Fed leg; no `total_bought=0` phantom; `negRisk` keys absent. Fed "band" markets behave as independent binary markets. | raw §Probe D |

## Blast radius / impact on `pm_category_stats`

- **Politics / winner-take-all negRisk events: AFFECTED** (the −$17M artifact). NOT one of the 4 P1 categories. In the 12-wallet seed, this lives in cross-category history of mega-whales (d1k21 especially).
- **Fed (LIVE; Kickstand7, pako): CLEAN — proven.** Fed rollups are safe.
- **UFC (LIVE; 5 UFC whales): no pathology; reconciliation inconclusive** → yellow flag, close via the acceptance-checklist independent net-verify against a binary-market whale.
- **MLB / NBA (LIVE): binary game markets → expected clean** (same structure as Fed).
- Net: **the defect does NOT touch the four P1 categories' rollups** — but if P1 ingests all-category history (per the seed design), Politics rows would corrupt any all-category or Politics view, and a `total_bought=0` row breaks `roi = Σrealized/Σtotal_bought`.

## Handling OPTIONS (NOT implemented — for Jack's decision)

A clean, category-agnostic **invariant** detects the pathology from fields already in the row (no gamma call, no negRisk flag needed):
> a long position's worst case is losing its cost, so **`realized_pnl < -total_bought`** (equivalently `total_bought==0 & realized_pnl!=0`) is IMPOSSIBLE for honest per-leg PnL → flag it.

1. **(RECOMMENDED) Quarantine-by-invariant at ingest.** Store a `pnl_suspect` flag on rows failing the invariant; EXCLUDE them from `pm_category_stats` (both numerator and denominator). Category-agnostic, protects every rollup, cheap (two fields), self-documenting. The 4 live categories keep 100% of rows; Politics rollups become flagged/sparse.
2. **negRisk-event exclusion via gamma.** Enrich each market with the gamma `negRisk` flag (needs a raw gamma `/markets` field the current `fetch_market_resolutions` decodes away) and drop negRisk winner-take-all markets. More targeted but adds a gamma dependency + the flag isn't in /closed-positions.
3. **Category-scope P1 ingestion to the 4 live categories only** (MLB/UFC/NBA/Fed). Simplest; the provably-clean set; loses cross-category history (which P1 doesn't need). Can combine with (1) as defense-in-depth.
4. **Accept/do-nothing.** Only viable if non-binary categories are never surfaced. Not recommended (a `total_bought=0` row still breaks ROI math).

Independent of choice: the acceptance-checklist "one manually-verified whale's net matches an independent API sum" should target a **binary-market** whale to positively close the Probe-A yellow flag.

---
## RAW OUTPUT (verbatim evidence)

```
PROBE A: evanng closed-positions vs activity reconciliation
evanng closed rows=137  ALL-CATEGORY sum_realized_pnl=+15702.05
PER-CATEGORY: ufc n=75 +9778.97 | fifwc n=20 +2595.38 | nba n=11 +1625.25 | mlb n=11 +277.78 | (lol -16.27, atp +26.24, wnba +505.00, nhl -117.77, wta +488.83, epl +41.67, ucl +31.42, ...)
UFC F1 single-fight: n=75 +9778.97 | UFC F2 broad: n=75 +9778.97 | scout=-13706.51
delta closed-F2 vs scout=+23485.48
evanng activity rows=546 truncated=False
per-cid top deltas (closed vs act S+R-B): +1956.27, +1718.84, -1677.08, +1484.21, +1471.28, +1436.04, +1313.65, +1202.36
matched_cids=75  SUMclosed=+9778.97  SUMactivity=+1141.72  delta=+8637.25
VERDICT-A: DOES NOT reconcile

PROBE B: d1k21 -574604.31 election rows
d1k21 closed rows=3393 | rows==-574604.31: 5 | title 'win the 2024 election': 35
ROW0 cid=0x08f5fe8d... slug=will-bernie-sanders-win-... event=presidential-election-winner-2024 outcome=Yes avg=0.5 bought=0.0 realized=-574604.31 cur=0.0 EXTRA={}
ROW1 vivek | ROW2 warren | ROW3 kanye | ROW4 chris-christie -- all avg=0.5 bought=0.0 realized=-574604.31 cur=0.0 EXTRA={}
ANSWERS: distinct event_slugs=1 [presidential-election-winner-2024]; distinct cids=5; total_bought_set=[0.0]; avg_price_set=[0.5]
FULL EVENT presidential-election-winner-2024: 21 legs, 17 distinct cids, 5 legs share -574604.31
  bought=0.00 realized=-574604.31 (x5) ; -574602.01 ; -573664.79 ; -573196.76 ; -572446.88 ; -572182.76 ; -571273.87 ; -569213.90 ; -568050.27 ; -562537.29
  bought=15750.78 realized=-535322.95 ; bought=87679.76 realized=-57561.26
  bought=106999.80 (No) realized=-106.94 ; bought=1016541.84 (No) realized=+638.11 ; bought=911174.91 (No) realized=+822.91 ; bought=330903.21 (Yes) realized=+24423.34

PROBE C: d1k21 activity rows=5000 truncated=True ; election cids with activity in window: 0/3 -> INCONCLUSIVE

PROBE D: Fed negRisk reach
Kickstand7: total=1803 fed_rows=79 events=24 mirror_events=0 negRiskKeys=NONE (realized tracks total_bought on every leg; no bought=0 phantom)
pako:       total=369  fed_rows=39 events=19 mirror_events=0 negRiskKeys=NONE (same)

SUMMARY:
  A: DOES NOT reconcile (F2 closed sum=+9778.97 vs scout -13706.51; per-cid matched=75 delta=+8637.25)
  B: total_bought=0 with realized=-574604.31 -> DECOUPLED (negRisk event-level attribution), not real per-leg
  C: INCONCLUSIVE (0/3 cids in activity window; activity truncated at 5000)
  D: Kickstand7 mirror_events=0 | pako mirror_events=0 ; Fed CLEAN ; negRisk keys absent from /closed-positions
```
