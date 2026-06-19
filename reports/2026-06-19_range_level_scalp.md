# Range / level mean-reversion scalp — "bounce between the boxes", mechanized & honestly tested

**Date:** 2026-06-19
**Branch:** `range-scalp-2026-06-19` (off origin/main `1c12d5c`)
**Mode:** READ-ONLY research. NO prod/deploy/live/config. §4.
**Harness:** `scripts/range_scalp/range_scalp.py` (unconditional fade), `range_scalp_regime.py` (efficiency-ratio range gate).
**Corpus:** `btc_scalping.db` `bars_3m`, 38,899 bars Mar30–Jun19 2026 (one **bear/neutral** regime). Split TRAIN ≤May15 / VALIDATE ≤Jun1 / LOCKBOX ≥Jun1. Corrected effective fees (entry 0.0243% / maker-TP 0.0140% / taker-SL 0.0400% / slip 0.005%/leg).

> # VERDICT: NULL on this corpus — but a *trustworthy, non-repainting* null, with the failure mechanism measured exactly.
> The mechanical "fade the box edges, stop on the break" loses because **the range breaks ~58% of the time and bounces only ~39%** — the break (BOS) happens *more often* than the bounce. Adding a real-time range-state filter (Kaufman Efficiency Ratio) **does help** (win 35%→48%, break 58%→49% on the buy side — the SMC instinct *don't fade a trend* is correct and was correctly identified), **but still clears 0/24 configs**: even in the calmest 64% of bars a 3m "range" breaks ~49–60% of the time, too often to beat fees. **This is the empirical proof of the hindsight illusion** — the eye remembers the bounces and forgets that the breaks are more frequent. Unlike the divergence/SFP studies this is **NOT a look-ahead artifact** (levels are built only from confirmed past pivots), so the result is more trustworthy, not less. **Honest scope: mean-reversion is the most regime-dependent strategy there is, and this is one bear/neutral window — a ranging month could flip it. The engine is built and ready to re-test the moment we have (a) real levels and (b) a ranging regime.**

---

## What was built (real-time, non-repainting by construction)
The key discipline that makes this a *fair* test of your chart (and the fix vs the earlier SFP-alone null):
- **Levels = clusters of CONFIRMED past pivots only.** A pivot at bar p (local extreme over ±3) is *known* only at bar p+3; at decision bar i we use pivots with `confirm_idx ≤ i`. No future leak.
- **A "level" needs ≥2 touches within a tolerance** (your EQH/EQL / supply-demand box) — a real multi-touch S/R, not a lone noise wick. (38,899 bars → 3,720 confirmed pivot-highs, 3,732 lows.)
- **Setup:** between nearest support S (≥2-touch) and resistance R, width filter 0.25–2.0%. Fade the edge.
- **Entry:** `sweep` (wick through the level, close back inside = your SFP reclaim — proven real-time-clean) or `touch` (tag the level, close holds).
- **Stop = just beyond the level (the range break = BOS invalidation)** — the real loss, *not* capped.
- **Target:** opposite edge (mean reversion) or fixed R2.
- Swept tol∈{0.08%,0.15%}, buf∈{0.05%,0.10%}, entry∈{sweep,touch}, target∈{opp,R2} → 32 configs.

## Step 1 — unconditional range-fade: 0 / 32 positive
Every config net-negative (−0.02 to −0.77 R) on both train and validate. The diagnostic is the whole story (representative config, sweep/opp):

| side | n | bounce (win) | **break/stop** | timeout | net |
|---|---|---|---|---|---|
| buy  | 1601 | 619 (39%) | **931 (58%)** | 51 | −0.317 |
| sell | 1535 | 599 (39%) | **890 (58%)** | 46 | −0.336 |

**The range breaks 58% of the time, bounces 39%.** The "easy money" picture is selective memory: on this tape the box edge fails more often than it holds.

## Step 2 — add a real-time range-state gate (don't fade a trend)
Kaufman Efficiency Ratio over the prior 20 closes: `ER = |Δclose over K| / Σ|bar-to-bar Δ|` (0–1, past-only). Low ER = choppy/ranging; high ER = trending. Gate entries to `ER ≤ thr`.

Ranging fraction: ER≤0.25 → 64% of bars, ≤0.35 → 81%, ≤0.50 → 95%.

**The gate works (mechanism confirmed) but doesn't rescue it — 0 / 24 positive:**

| config | TRAIN | VALIDATE | break-rate |
|---|---|---|---|
| buy sweep opp ER≤0.25 | −0.089 (w48%) | −0.403 | 49% → 57% |
| buy sweep R2 ER≤0.25 | **−0.076** (w39%) | −0.363 | 55% → 62% |
| buy sweep opp ER≤0.50 | −0.142 | −0.448 | 53% |
| sell sweep opp ER≤0.25 (tol0.08) | −0.361 | −0.105 (w46%) | 58% → 46% |

Filtering to the calmest 64% of bars lifts buy win-rate 35%→48% and trims the break-rate 58%→49% — real, correct, and exactly what "only trade when it's actually ranging" should do. But the best config is still only −0.08 on train and worse on validate, because **even genuine low-ER ranges break ~49–60% of the time.** No threshold makes the bounce reliably win. (Buy > sell throughout — counter-trend dip-buys held a touch better than rally-fades on the bear tape — but both fail.)

## Why your eye sees an edge the mechanics don't
Three things separate your discretionary success from this engine, and they are the real forward path:
1. **The levels.** I used 2-touch 3m pivot clusters. Your **real boxes** are AlexO's hand-drawn, HTF-informed, multi-touch zones — far more selective and higher quality. A better level → fewer, better setups → lower break-rate. *This is the Pink Box backlog item* (screenshot/JSON → levels extractor, captured forward).
2. **HTF nesting.** You read whether the 3m range sits inside a *higher-timeframe* range (mean-revert) or a HTF trend (the 3m "range" is just a pause before continuation = the break). The ER gate is a crude proxy; real HTF structure is better.
3. **The break filter.** You discretionarily skip the breaks that this engine eats. The 58% break-rate is exactly the population your judgment culls.

## Honest scope & the regime point (important)
- **Mean-reversion is the single most regime-dependent strategy type.** This corpus is one **bear/neutral** window — close to the *worst case* for range-fading (trends and sharp relief rallies run the edges). In a genuinely **range-bound** month the break-rate could fall below 50% and *this same engine could flip positive*. A null here is genuinely not a null forever.
- **The engine is reusable and ready.** The ER range-detector also gives you a live "are we ranging right now" signal — useful regardless.

## Recommendation
The mechanical "fade every box" is a net loser on this tape, and now we know precisely why (break > bounce). The honest next step is **not** a smarter model — it's **better inputs**: (1) build the real-levels capture (Pink Box item) so we test *your* boxes, not a swing proxy; (2) re-run this exact engine on a **range-bound** window; (3) add HTF range-nesting as the gate instead of (or with) ER. If those don't flip it, the edge is genuinely discretionary and not bot-able from this data.

**This is a rigorous, non-repainting null — the most trustworthy of the four studies.** Nothing applied; not live-blessed.

**Hard stops honored:** research only, nothing deployed/traded/configured; LOCKBOX reserved (not reached — no positive-both candidate to confirm); levels built from confirmed past pivots only (no repaint); no cross-regime/live verdict; corrected effective fees (not all-taker); no git stash; no signed/live API; no polymarket; no Cypher.
