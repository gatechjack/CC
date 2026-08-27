# Stage 1 — RUNG 3 STEP 1: ADJUDICATOR DRY RUN (gamma, non-mutating) — 2026-08-27

**Authorization (Jack):** "RUNG 3 — STEP 1 OF 2: DRY-RUN THE ADJUDICATOR. No live writes … NON-MUTATING adjudicator
evaluation ONLY." The live adjudicator run + cadence install are **step 2, unauthorized**. Executed at **17:10 UTC**.

## Mechanism — why it is genuinely non-mutating
Live was opened **read-only** (`file:…?mode=ro`) to take a SQLite online **`.backup` copy** (`/tmp/pm_dryrun_copy_*.db`).
ALL mutation happened on the copy: flipped the 8 active-pinned OPEN trades → `pending_adjudication`, then ran the
**real `paper.adjudicate()`** with **real gamma resolutions** (`PolymarketDataAPIClient.fetch_market_resolutions()`,
a GET). Copy deleted after. Gamma calls are reads. **Confirmed live-unchanged after:** schema 9, pm_paper_trade
**102 (102 open, 0 pending, 0 closed)**, pm_paper_category_stats **0 rows**, /farm **129578 B**, pm_web PID **13102**,
engine PID **676**. Copy removed. **Nothing was written to live.**

## ★ Structural finding first: adjudicate() only touches `pending_adjudication`
`adjudicate()` (and `collect_pending_condition_ids`) select **`WHERE status='pending_adjudication'`**. Live has
**0** such rows (all 102 paper trades are `open` — the poller has never run to mark exits `pending`). **A literal
live `paper-adjudicate` today would process 0 rows.** Real Rung-3 order = **poller first** (marks vanished positions
`pending`) **then** adjudicate. To exercise the load-bearing gamma logic on real data now, the dry run **forced the
8 eligible open trades → pending on the copy** (only `status`/`exit_observed_ts` changed; every field adjudicate reads
— condition_id, outcome_index, market_end_date, size_basis, cost_basis — is the real live value).

## Result
`adjudicate()` → **pending_in=8, closed=2, voided=0, staled=0, still_pending=6**, grace 259200.
**C2.3 subset assertion PASSED** (n_pinned=14 wallets, n_refreshed=14, unrefreshed=[]) — a live run won't fail loud.

## Per-trade (all 8)
Gamma decode: winner = index whose `outcomePrices` value **≥ 0.9**; `adjudicate` sets `won = (our outcome_index ==
winning_index)`; paper P&L = `won ? size_basis−cost_basis : −cost_basis` (size_basis=100 fixed stake).

| # | cat | wallet | slug | our idx / outcome | gamma status | prices | win idx | VERDICT | P&L |
|--|--|--|--|--|--|--|--|--|--|
|1|fed|0x71edffd0|will-fed-increase-25bps-sep|0 / Yes|pending|0.305/0.695|—|pending|—|
|2|fed|0x71edffd0|fed-rate-hike-in-2026|1 / No|pending|0.565/0.435|—|pending|—|
|3|fed|0x71edffd0|fed-rate-hike-by-october-2026|1 / No|pending|0.445/0.555|—|pending|—|
|4|fed|0x71edffd0|fed-rate-hike-by-september-2026|1 / No|pending|0.305/0.695|—|pending|—|
|5|fed|0x71edffd0|fed-rate-cut-by-december-2026|0 / Yes|pending|0.11/0.89|—|pending|—|
|6|mlb|0x16bb9951 (SDTrading)|mlb-sf-atl-2026-06-18|1 / Atlanta Braves|pending|0.37/0.63|—|pending|—|
|7|ufc|0x43e0f84f (evanng)|ufc-ronhum-alemir-2026-08-25|0 / Ronald Humphrey|**resolved**|**1.0/0.0**|**0**|**closed WON**|**+11.39**|
|8|ufc|0x52f454c4 (Kh4mz4t)|ufc-garbal-seacla-2026-08-25|1 / Sean Clancy Jr.|**resolved**|**0.0/1.0**|**1**|**closed WON**|**+89.54**|

The 6 pending are all `closed=False`, `umaResolutionStatus=None`, and **long-dated** (end 2026-09-07 … 2027-01-08) →
correctly stay pending (within the 72h grace — measured: all end+72h are in the future, e.g. mlb −1,147,788s, fed −1.9M…−11.8M s).

## Answers

**(a) Winning-index derivation vs hand-read — WON walk-throughs.** Both resolved cases verified against RAW gamma:
- **#7 ufc-ronhum:** raw `closed=True, uma=resolved, outcomes=["Ronald Humphrey","Alexis Miranda"], outcomePrices=["1","0"]`.
  Hand: winner = the ≈$1 side = index **0** = Ronald Humphrey. Code winning_index=**0**. We hold index 0 (Ronald
  Humphrey) → `won=(0==0)=1`. **Matches.** P&L = 100−88.61 = **+11.39** (bought the favourite at 0.8861 → small win).
- **#8 ufc-garbal:** raw `outcomePrices=["0","1"]`, outcomes=["Gary Balletto","Sean Clancy Jr."] → winner = index **1**
  = Sean Clancy Jr. Code winning_index=**1**. We hold index 1 → `won=(1==1)=1`. **Matches.** P&L = 100−10.46 = **+89.54**
  (bought the underdog at 0.1046 → big win). **The two winning indices are DIFFERENT (0 and 1)** — the derivation
  tracks the ≥0.9 price to the correct position, it is not hard-coded to a fixed index.
- **No live LOST case exists in this sample** (both resolved trades happened to be on the winning side). Flagged in (e).

**(b) Orientation — could a won/lost inversion produce these results? NO.** An inverted rule (`won = our_index !=
winning_index`) would have scored **both** resolved trades as LOSSES (#7: 0≠0 false→lost −88.61; #8: 1≠1 false→lost
−10.46). The actual result is **both WON**. Independently, the raw gamma shows we held the **≈$1.00 winning fighter**
in both (Ronald Humphrey won; Sean Clancy Jr. won). So the correct verdict is WON, the code produced WON → orientation
is right, not inverted. *Counterfactual LOST (to show the loss path):* had #7 held index 1 (Alexis Miranda, price 0.0),
`won=(1==0)=0` → status closed, `realized_pnl = −cost_basis = −88.61`. Correct sign.

**(c) Ambiguous / missing / disputed?** None. The 2 resolved markets carry `umaResolutionStatus=resolved` (no
dispute/challenge). The 6 pending are cleanly open. No `not_found`, no `void`, no missing field the decoder needs.
**One data curiosity (not adjudication-affecting):** #6 mlb's paper slug is `mlb-sf-atl-2026-06-18` but its stored
`market_end_date` and gamma `endDate` are **2026-09-07** for that condition_id (still open) — a slug/date label
mismatch on the paper row; the adjudicator keys off `condition_id`, so it correctly holds it pending.

**(d) P&L sanity.** Both wins are **positive** (no won-with-negative red flag); magnitudes = `100·(1−entry)` exactly
(#7 100·(1−0.8861)=11.39; #8 100·(1−0.1046)=89.54). The 6 pending have P&L `None` (not computed). Signs/magnitudes correct.

**(e) Confidence + caveats.** **High** confidence a live run books these correctly for the **resolved-won** and
**pending** paths — both resolved cases hand-verified end-to-end against raw gamma, orientation proven, P&L exact,
subset assertion green. **Caveats to carry into step 2:**
1. **LOST, VOID, and STALE were NOT exercised on live data** — only WON + PENDING appeared. Orientation-for-LOST is
   proven by construction + counterfactual, not by a live booking; void/stale remain fixture-only-tested. The first
   real losing/void/stale resolution should be spot-checked.
2. **The realistic Rung-3-today outcome is small:** only the **2 ufc games** have resolved; a live poller→adjudicate
   would likely close just those 2 (their positions vanished post-settlement) as wins (+11.39, +89.54) and leave the
   rest open/pending. The 6 fed/mlb markets are long-dated → the paper scoreboard stays near-empty for weeks. This is
   correct behaviour, but sets cadence expectations.
3. Nothing needs changing before step 2. **No STOP condition.** The gamma re-base maps correctly.

## Invariants (post — nothing written to live)
schema **9** · pm_paper_trade **102 (all open, 0 pending, 0 closed)** · pm_paper_category_stats **0** · /farm **129578 B** ·
pm_web **13102** · engine **676** · dry-run copy removed.

## Still unauthorized
Step 2 = the **live** adjudicator run + the poller run + the cadence install (RULED path (b), `*/30`,
poll→adjudicate→rollup — the ruling, not the go).

Runner: `cc\pm_rung3_dryrun.sh` (read-only to live; works on a `.backup` copy + gamma GETs).
