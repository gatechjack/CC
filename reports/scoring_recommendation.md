# BitUnix scoring re-tune — recommendation

**Date:** 2026-05-16
**Status:** Three candidates surface, each with honest forward-test numbers.
Cost model: **9 bps round-trip** (0.04% × 2 taker + 0.005% × 2 slippage —
BitUnix VIP3 + Experience Card).

## The honest headline first

**No variant tested produces positive expectancy on the 47-day replay window.**
Every candidate — including baseline (current PR 3c YAML) — has negative mean
R per trade and negative sum R. The replay overstates the absolute trade rate
by ~10–15× vs live (it lacks the PA validation gate and HTF regime gate, both
out of scope per goal directive), so absolute numbers shouldn't be read as
live-trade forecasts. But the *ranking* across variants is meaningful, and
the ranking says: **the score engine itself is not a strong lever to improve
trade outcomes** on this dataset.

What variants DO change is **which candidates reach the downstream gates**.
A scoring engine whose PREMIUM tier is meaningfully cleaner than its STANDARD
tier is a *better-calibrated feeder* for the PA / HTF stack — even if every
score-tier loses money in isolation. Two of the three finalists below ship
that improvement; the third trades it for aggressive filtering.

## Recommendation rank order

### 🥇 Recommendation 1 — H2 (Re-weight + Otter precision up)

**What changes:** Five `weight:` edits in `bitunix_futures.scoring.factors`.
No formula change, no threshold change, no new code path.

```yaml
# Edit in config/strategies.yaml, bitunix_futures.scoring.factors block:

# ── Cypher A: cap heavy diamonds (measured edge does not support wt 5/4) ──
mc_a_blood_diamond:
  weight: 3     # was 5
  side: sell
  ttl_minutes: 30
  ttl_per_tf: {"3m": 30, "15m": 90, "30m": 180}
mc_a_red_diamond:
  weight: 3     # was 4
  side: sell
  ttl_minutes: 30
  ttl_per_tf: {"3m": 30, "15m": 90, "30m": 180}

# ── Cypher B: cap heavy circles (same reason) ──
mc_b_gold_buy:
  weight: 3     # was 5
  side: buy
  ttl_minutes: 15
  ttl_per_tf: {"3m": 15, "15m": 45, "30m": 90}
mc_b_buy_circle_div:
  weight: 3     # was 4
  side: buy
  ttl_minutes: 15
  ttl_per_tf: {"3m": 15, "15m": 45, "30m": 90}
mc_b_sell_circle_div:
  weight: 3     # was 4
  side: sell
  ttl_minutes: 15
  ttl_per_tf: {"3m": 15, "15m": 45, "30m": 90}

# ── Otter precision: up-weight (measured edge supports it on 15m/30m) ──
water_buy_large:       {weight: 3, side: buy,  ttl_minutes: 30}   # was 2
water_sell_large:      {weight: 3, side: sell, ttl_minutes: 30}   # was 2
spoon_bull:            {weight: 3, side: buy,  ttl_minutes: 30}   # was 2
spoon_bear:            {weight: 3, side: sell, ttl_minutes: 30}   # was 2
money_bag_bottom:      {weight: 3, side: buy,  ttl_minutes: 30}   # was 2
money_bag_top:         {weight: 3, side: sell, ttl_minutes: 30}   # was 2

# Thresholds: UNCHANGED. PR 3c calibration stands.
# min_score_to_fire: 5, premium: 10, standard: 5, weak: 3.
```

**Why this is the primary recommendation:**

| Metric (full window) | Baseline | H2 | Improvement |
|---|---:|---:|---|
| sum_R | -610.2 | -536.0 | **+74.2R** (12% less lossy) |
| mean R per trade | -0.421 | -0.400 | +0.021 |
| Fire count | 1449 | 1339 | -7.6% |
| **PREMIUM mean R** | **-0.381** | **-0.300** | **+0.081 (best in field)** |
| STANDARD mean R | -0.432 | -0.414 | +0.018 |
| Quality gap (PREM - STAND) | +0.051 | +0.114 | **2.2× wider** |
| Sharpe (R-units) | -11.79 | -10.71 | +1.08 |

The "quality gap" (PREMIUM mean R minus STANDARD mean R) is what tells you
the score engine is **doing its job of putting cleaner trades in the higher
tier**. H2 more than doubles this gap relative to baseline.

**In-sample vs out-of-sample (no overfit):**
- IS: fires 934 / mean R -0.418 / Sharpe -9.36
- OOS: fires 404 / mean R -0.357 / Sharpe -5.22
- OOS improvement is *regime drift* (less hostile chop), not overfit.

**When the config works:**
- Choppy / ranging markets where Otter precision triggers (water, spoon,
  money_bag) catch the in-bar reversals cleanly.
- Reduces "single mc_a_blood_diamond + mc_a_red_diamond = PREMIUM" false
  PREMIUM fires.

**When the config fails:**
- Strong trending markets where Cypher A panel signals are actually right —
  capping their weight at 3 means the score engine takes longer to escalate to
  PREMIUM. Two mc_a_red_diamonds + one mc_a_blood_diamond = 9 now (was 13),
  still a clean STANDARD but no longer auto-PREMIUM.
- The Cypher A "diamonds" still drive plenty of activity at wt 3 — they're
  the highest-volume signals on the chart.

**Falsification criteria (what would tell us H2 was wrong in live):**

1. **PREMIUM mean R in live (post-PA/HTF gates) is NOT better than STANDARD
   mean R** after ≥30 PREMIUM fires. The replay says PREMIUM should be
   ~0.11R cleaner; if live data shows PREMIUM is the same or worse than
   STANDARD, the re-weighting failed to put cleaner trades in the higher tier.
2. **Trade count drops by more than 30% in live** (replay predicted only -7.6%).
   If PA/HTF gates interact unfavorably with the new weight distribution, we
   could lose disproportionately many candidates.
3. **Otter precision family signals start contributing to ≥80% of fires** —
   means we've over-weighted them and the engine is now monothematic.

### 🥈 Recommendation 2 — H7 (H2 + unified cooldown)

**What changes:** Recommendation 1's YAML edits, PLUS one new config flag for
unified cooldown. Cooldown structure decision is in scope per goal text ("The
30-min cooldown structure stays (per-side or unified is open — see 'what to
explore' below)").

```yaml
# Add to bitunix_futures.scoring block:
cooldown_mode: "unified"          # new — was implicit "per_side"

# Plus all weight edits from Recommendation 1.
```

**Implementation note:** the current `evaluate_confluence_futures` and
`_score_and_maybe_propose_locked` both track `last_fire_ts_buy` /
`last_fire_ts_sell` separately. To support unified cooldown, the observer's
cooldown read should look at `max(last_buy, last_sell)`. The scorer's cooldown
gate already uses the side-specific timestamp the caller provides, so the
observer can implement unified cooldown by passing the MAX of both timestamps
as both arguments when `cooldown_mode == "unified"`. ~6 LOC change in
`_score_and_maybe_propose_locked`; no scorer change.

**Why second, not first:** the SCHEMA-RISK is non-trivial.

| Metric (full window) | Baseline | H2 | **H7** |
|---|---:|---:|---:|
| sum_R | -610.2 | -536.0 | -516.9 |
| mean R | -0.421 | -0.400 | **-0.391** |
| Win rate | 29.1% | 29.6% | **30.0%** |
| PREMIUM mean R | -0.381 | -0.300 | -0.305 |
| Sharpe | -11.79 | -10.71 | **-10.36** |

H7 beats H2 on every aggregate metric. The reason it's recommendation 2 not 1
is the small additional code-change risk (observer-side cooldown read), the
"cooldown structure" being structural enough that the goal flagged it as open
but worth-careful-thinking, and the fact that the marginal improvement is
modest (+0.009 mean R, +0.0035 win rate).

**When unified cooldown helps:**
- A buy fires, stops out, market chops; an opposing-side sell would have fired
  one minute later under per-side cooldown — almost always whipsaw noise.
  Unified blocks it.
- The cleaner buy → sell flip case where price genuinely reverses 1-2 hours
  later is unaffected (the unified cooldown is still only 30 min).

**When unified cooldown hurts:**
- Genuine reversal-immediate setups (rare). E.g. a STANDARD buy that fills
  and stops out at 5 min, market does an immediate clean reversal to PREMIUM
  sell at 15 min — under unified cooldown the sell is blocked for another 15 min.
  Replay data suggests this is uncommon; could be revisited if observed
  in shadow data.

**Falsification (same as H2 plus):**
4. **Live win rate after unified cooldown is NOT ≥0.5pp better than
   per-side cooldown** after ≥30 trades each. The replay-predicted lift is
   small (29.6% → 30.0%); live variance could swamp it.

### 🥉 Recommendation 3 — H4b (Conviction ratio ≥0.80) — alternative for "trade less, trade cleaner"

**What changes:** Replace the subtractive net-score formula with a conviction
ratio. Keep current YAML weights. New config: `conviction_ratio_threshold: 0.80`.

```yaml
bitunix_futures:
  scoring:
    # Replace: net = winner − loser  +  net ≥ min_score_to_fire
    # With:    ratio = winner / (winner + loser)  +  ratio ≥ threshold
    #          AND winner ≥ min_score_to_fire (preserve absolute floor)
    score_formula: "conviction_ratio"     # NEW — default "subtractive" preserves current
    conviction_ratio_threshold: 0.80      # NEW — 0.0 = always fires; 1.0 = no losing-side signals at all
    min_score_to_fire: 5                  # unchanged — absolute floor still applies
    tier_thresholds: { premium: 10, standard: 5, weak: 3 }   # unchanged
```

Implementation: in `bitunix_confluence.py`, branch on `score_formula` between
the current subtractive path and the new ratio path. ~20 LOC.

**Why third, not first or second:** highest implementation surface area
(formula change + new config knobs + tier-derivation re-think) and the
*per-trade* mean R doesn't actually improve. What it does is **cut the trade
rate roughly in half** (1449 → 856 fires) for similar per-trade outcomes.
That's a different kind of "improvement" than H2/H7.

| Metric (full window) | Baseline | **H4b** |
|---|---:|---:|
| Fire count | 1449 | **856** (-41%) |
| Trades/day | 30.7 | 18.2 |
| Sum R | -610.2 | **-387.2** (-37%) |
| Mean R per trade | -0.421 | -0.452 (slightly worse) |
| OOS Sharpe | -5.53 | **-3.20** (best in field) |
| OOS mean R | -0.361 | **-0.278** (best in field) |
| PREMIUM mean R | -0.381 | -0.454 |
| PREMIUM/STANDARD gap | +0.051 | -0.005 (eliminated) |

H4b's value proposition: **substantially fewer trades hitting the downstream
gates, with the strongest OOS performance**. The per-trade quality is slightly
worse (the variant rejects marginal trades but the survivors are still
predominantly the same noise patterns), but the total at-risk capital
exposure drops sharply.

**When H4b helps:**
- If the downstream PA/HTF gates are *not strongly correlated* with the score
  engine's specific signal mix, then halving the candidate stream halves the
  trade rate proportionally without making any individual trade better or
  worse. Net result: same per-trade outcome, half the position exposure.
- High-volatility regimes where the conviction-ratio formula's symmetric
  filter is rejecting both-sides-firing trades that are pure noise.

**When H4b fails:**
- If the downstream gates have selection effects that improve quality
  disproportionately on the SUBTRACTIVE formula's fires (e.g. high net-score
  trades have strong PA alignment), then losing those high-net-score trades
  in favor of ratio-passing trades that have weaker PA alignment is a net
  negative — H4b's filter cuts the wrong candidates.
- This is testable in shadow mode by computing the conviction ratio on every
  live fire and segmenting outcome by ratio bin BEFORE shipping.

**Falsification criteria specific to H4b:**

5. **Before shipping**: in shadow mode (1 week), record `conviction_ratio` on
   every fire that the current YAML produces. If post-PA/HTF outcomes don't
   stratify by ratio (i.e. fires with ratio 0.85 don't outperform fires with
   ratio 0.65 at the trade-outcome level), the formula change is unmotivated
   and we should ship H2 or H7 instead.

## What did NOT survive

For honesty's sake, the candidates that didn't make the rec list:

- **H1 (Cap weights at 3, no Otter up-weight)** — strictly dominated by H2 on
  every metric except simplicity. If H2's Otter up-weights are too much, fall
  back to H1; it still doubles the PREMIUM quality gap (+0.088 vs baseline's
  +0.051).
- **H3 / H3b (Asymmetric α)** — refuted the hypothesis that requiring cleaner
  consensus would lift mean R. Filtering reduced fires 22-34% without improving
  per-trade quality. Same fires-with-different-loser-weight-deflation.
- **H4 (Conviction ratio ≥0.70)** — too loose to filter meaningfully.
  Conviction ≥0.80 (H4b) is the cliff where filtering bites.
- **H5 (PREMIUM requires 3 families)** — implementation demotes failed-PREMIUM
  to STANDARD, so the trade-count effect washes out. Did widen PREMIUM quality
  gap (+0.041 → +0.092) but the implementation choice is debatable: should
  failed-PREMIUM SKIP entirely? That's a stricter variant worth testing.
- **H5b (Family confluence on BOTH tiers)** — inverted the PREMIUM/STANDARD
  quality gap. Adding family-confluence to STANDARD let weaker signals from
  one family promote into PREMIUM via family-pass alone.
- **H6 (min_score 7)** — predicted +0.10 mean R lift, delivered +0.013. Not
  enough.
- **H6b (min_score 8, premium 12)** — approximately the pre-PR-3c calibration.
  Best OOS Sharpe after H4b. Worth flagging as a "no-formula-change rollback"
  option if the team wants the simplest possible re-tune; but H2 ships more
  improvement at the same implementation simplicity.
- **combo (H2+H4+H5+unified)** — most stable across IS/OOS but PREMIUM/STANDARD
  gap inverted. Too many concurrent changes; impossible to attribute outcomes.

## Final ranking — single-sentence summary

1. **H2** — best calibrated score engine (PREMIUM cleaner than STANDARD), simplest YAML diff, lowest implementation risk. **Ship this first.**
2. **H7** — H2's wins + slight aggregate lift from unified cooldown; 6-line observer change. Ship after H2 if shadow data confirms H2's PREMIUM/STANDARD differentiation.
3. **H4b** — cuts trade rate in half with best OOS Sharpe; biggest implementation surface; requires shadow-mode validation of conviction-ratio stratification before committing.

## What the data does NOT support

- **Claims of live P&L improvement from any of these changes.** The 47-day
  replay shows every variant losing money in isolation. Score engine work is
  *necessary* for trade quality but not *sufficient* to make the BitUnix
  strategy profitable. The trade-outcome lever is the trade-plan v2 work
  (already shipped, dormant behind `trade_plan.enabled: false`), the PA gate
  (live in shadow), and the HTF regime gate (live in shadow).
- **Recommending H4b on the basis of "best OOS Sharpe alone"** — without
  shadow-data validation that the conviction-ratio formula is actually
  selecting better candidates from the live signal stream (rather than just
  rejecting more of them), we'd be shipping a major formula change on the
  strength of one 14-day OOS window.

## When to revisit

After ≥30 live (paper) PREMIUM fires post-H2-ship (~10-14 days at current
~3/day live rate), recompute the PREMIUM/STANDARD mean R gap on production
data. If it's ≥0.05R (replay predicted +0.114R), the recalibration worked.
If it's ≤0, the recalibration over-weighted Otter precision and the diamond
signals should be partially restored. Either outcome is a clear go/no-go for
H7 / H4b follow-ons.
