# §3A quarantine reconciliation vs LIVE data (2026-08-22, read-only)

Ordered by Jack (reconciliation block). Runner `runners/pm_fed_quarantine_probe.py` +
`cc\pk_fed_quarantine_ro.ps1`: pulls live `/closed-positions` for 4 roster whales and runs the
**actual committed** ingest path (`derive_category_from_slug` -> `cp_to_record` [row invariant] ->
tier-2 on suspect-unknowns -> `apply_event_group_quarantine`). No DB, no writes, isolated scratch
removed. This is exactly what a Step-3 backfill will flag. **Do not soften: two prior claims of record
were wrong.**

## Per-whale results (actual ingest code on live rows)

| whale | role | total | SUSPECT | by clause | FED rows / susp |
|---|---|---|---|---|---|
| Kickstand7 `0xd1acd3…05` | Fed | 1803 | **104** | (a)=3, (b)=72, propagated=29 | 83 / **3** |
| pako `0x71edffd0…d338` | Fed | 369 | **0** | (a)=0, (b)=0 | 46 / 0 |
| SDTrading `0x16bb…8492` | live MLB | 505 | **7** | (a)=6, (b)=0, propagated=1 | mlb 462 / **5** |
| xifutloong3 `0x2dc1…b33c` | live MLB | 201 | **18** | (a)=17, (b)=0, propagated=1 | mlb 201 / **18 (8.5%)** |

Per-category suspect (Kickstand7): unknown 89/1325, nba 6/156, ufc 6/96, **fed 3/83**, nfl 0, mlb 0, nhl 0, cbb 0.

## Verdict 1 — the two clauses are NOT the same detector

- **Clause (b) `total_bought<=0 AND realized_pnl!=0` = a CLEAN negRisk-phantom detector.** Fires only on
  true negRisk winner-take-all zero-cost legs: Kickstand7's politics (`which-party-will-control-the-us-senate`,
  `will-russia-use-a-nuclear-weapon`, …), `nba-mvp-694` futures, `ufc-281` card. **Zero clause-(b) fires on
  either MLB whale or on pako.** No false positives observed. This clause is sound.

- **Clause (a) `realized_pnl < -(total_bought + max($1,1%))` = FALSE-POSITIVE-PRONE on binary markets.**
  It fires on ordinary **single-game MLB moneyline LOSSES** for BOTH live MLB whales — 17 of xifutloong3's
  18 suspects, 5-6 of SDTrading's, and 3 of Kickstand7's. Examples (real losing game bets, NOT phantoms):
  - `mlb-bal-cle-2026-04-17` tb=4068.07 rp=-4679.62  (lost 15% MORE than recorded cost)
  - `mlb-min-nym-2026-04-21` tb=11522.00 rp=-13505.40
  - `mlb-nyy-bos-2026-04-23` tb=9000.00 rp=-9112.99  (1.25% over — fee/rounding sized)
  - `cbb-ill-iowa-2026-03-28` tb=17456.74 rp=-19574.60
  You cannot lose more than cost on an honest binary long -> **`/closed-positions total_bought understates
  the true cost basis on scale-in / round-trip rows** (the known "scale-ins collapsed" trait); `realized_pnl`
  is the true net. Clause (a)'s premise ("a long's worst case is losing its cost") is therefore FALSE given
  the API's `total_bought` semantics, so it flags real binary losses as "impossible."

- **Event-group propagation COMPOUNDS the clause-(a) misfire on games.** A single clause-(a) fire taints the
  event's other leg: `mlb-min-tor-2026-04-12` (SDTrading) taints a sibling loss; `mlb-tb-cle-2026-04-27`
  (xifutloong3) a clause-(a) leg propagated to a **+$6,411 WINNING** leg of the same game. Propagation is
  correct for negRisk winner-take-all; on a 2-outcome game it spreads a false positive.

## Verdict 2 — impact: the quarantine, as written, CORRUPTS the live-category scoreboard
Clause (a) + propagation **exclude real losing (and sibling winning) single-game bets** from MLB/CBB.
Dropping losses biases a whale's win-rate/ROI **upward**. For xifutloong3, 17 excluded rows are real losses
-> its scoreboard record would be materially better than reality. This is the OPPOSITE of the "safe" claim.

## Corrections to the record (both were wrong)
1. **"Fed empirically proven CLEAN (Kickstand7+pako, zero mirror events); Fed rollups are safe"**
   (`REALIZEDPNL_PROBE_RESULT.md` verdict + Probe D + blast radius) — **WRONG for Kickstand7.** Reconciles
   as: Probe D's `mirror_events` counter only counts **>=2 condition_ids sharing an identical realized_pnl**
   (the -$574k winner-take-all echo) and correctly found none in Fed — but the doc EXTRAPOLATED that to
   "clean/safe." The actual §3A clause (b) is stricter (any single zero-cost leg) and flags Kickstand7's
   `fed-interest-rates-january-2025` dust leg (tb=0.00 rp=-0.50), which event-group propagation escalates to
   two large winner legs = **3/83 Fed rows quarantined, $20,121 (9% of Fed $) excluded.** pako Fed IS clean
   (0). Cleanliness is WHALE-DEPENDENT, and the quarantine is load-bearing on Fed, not decorative.
2. **"The defect does NOT touch the four P1 categories' rollups; MLB/NBA expected clean (binary)."** —
   **WRONG.** Clause (a) touches MLB and CBB single-game rows on both live whales (real losses excluded), and
   clause (b) touches Kickstand7's NBA/UFC FUTURES/CARDS (`nba-mvp`, `ufc-281`). Single-game moneylines have
   no clause-(b) phantom, but clause (a) misfires on them. No live category is invariant-clean.

## Blast radius (which categories are evidenced clean, on what evidence)
- **Nothing is structurally clean.** Contamination type depends on market structure, not category label:
  - negRisk multi-outcome (Fed rate bands, NBA/UFC/politics futures & cards): clause-(b) phantoms — quarantine
    correct here.
  - single-game moneylines (MLB/CBB/…): clause-(a) FALSE POSITIVES on real losses — quarantine wrong here.
- pako Fed is the only slice observed at 0 suspect. SDTrading/xifutloong3 MLB are the copy-relevant slices and
  BOTH carry clause-(a) misfires (1.1% and 8.5%).

## Proposals (NOT implemented — Jack's call; §3A is load-bearing)
1. **Split the invariant: keep clause (b), rework/retire clause (a).** Clause (b) is the sound negRisk-phantom
   detector. Clause (a) as an "impossible loss" test is invalid under the API's understated `total_bought`.
   Options for (a): (i) drop it entirely and rely on clause (b) + a repeated-realized-across-cids detector
   (the true -$574k mirror signature, Probe B/D style); (ii) widen EPS drastically (the misfires run to ~25%
   over cost, so any %-based EPS that clears them also blinds the real -$574k case — argues for (i)); (iii)
   gate clause (a) to negRisk markets only (needs the gamma `negRisk` enrichment, §13A(c)).
2. **Scope event-group propagation to negRisk events** (else it spreads a clause-(a) misfire across a 2-outcome
   game). Also needs the negRisk/market-type dimension (§13A(c)/(d)).
3. **data_quality flag is by COUNT (`n_excluded/total > 10%`), $-blind** (`stats.rollup`): Kickstand7 Fed
   3/83=3.6% does NOT flag despite $20,121 (9% of $) excluded. Consider a $-weighted flag too.

## First live exercise of the quarantine (Jack's question)
It is no longer fixture-only: the ACTUAL ingest code (row invariant + event-group propagation) ran on live
rows in THIS read-only probe. Event-group propagation fired 29× on Kickstand7 + 1× each on the MLB whales.
No contrived wallet needed — Kickstand7 (negRisk) and the MLB whales (clause-a) exercise it naturally. But the
first exercise reveals a DEFECT (clause a), so it should not be trusted for ranking until Proposal 1 is decided.
