# Scoring config hypotheses (predictions BEFORE backtest)

Written 2026-05-16 after reading `reports/scoring_inventory.md` but BEFORE
running `reports/scoring_backtest_results.md`. Honest predictions go here so
we can later check whether the data supports or refutes each thesis.

Baseline = current `config/strategies.yaml` `bitunix_futures.scoring` block:
PR 3c calibration, `min_score_to_fire=5`, `premium=10 / standard=5 / weak=3`,
subtractive `net = winner − loser`, per-side cooldown 1800s, dedupe within
TTL, PA/guards moved out of score (`pa_factors_in_score=false /
guards_in_score=false`).

## Cross-cutting context from inventory

- **Live-rate inflation in replay** (~10–15×): the replay does not model PA
  validation or HTF regime gates. So when a variant shows "8 trades/day" the
  honest read is "~0.7 trades/day post-gates". This is the rate comparison
  metric I lean on, not absolute counts.
- **Bear-side over-fires in the window**: BTC was net-up across the 47 days
  but Cypher A panel pumps out 47 red_diamonds/day on 3m. The subtractive
  formula lets a single mc_a_blood_diamond (wt 5) + mc_a_red_diamond (wt 4)
  fire reach 9 → STANDARD. Family confluence + asymmetric formulas are
  designed to break exactly this.
- **Heavy-weight signals don't pull their weight**: blood_diamond (wt 5) and
  gold_buy (wt 5) have measured -0.5R and -0.2R per fire. Capping weights at
  3-4 should not hurt and may help by reducing solo-fire false PREMIUMs.

## H1 — Re-weighted-to-measured-edge (thresholds unchanged)

**Change:** Cap all weights at 3. Demote heavy signals (5→3, 4→3) since
measured edge does not support them; preserve 1-2-3 ladder for relative
strength. No threshold changes.

**Why:** The inventory shows blood_diamond, gold_buy, sell_circle_div all
underperform their 4-5 weights. The current weights were intuited from
"rarity = importance" rather than measured. Removing the heavy weights
should make PREMIUM (≥10) genuinely require *multi-signal confluence*
instead of "one A-panel diamond + one Otter trigger ≥ 10".

**Prediction:** PREMIUM fire count drops sharply (≥40%). STANDARD count
roughly stable. Mean R on PREMIUM rises (selection effect — fewer
single-signal PREMIUMs). Mean R on STANDARD roughly flat.

## H2 — Up-weight Otter precision family

**Change:** water_buy_large / water_sell_large / spoon_bull / spoon_bear /
money_bag_top / money_bag_bottom from weight 2 → 3. Cap heavy weights as
in H1 (blood_diamond 5→3, gold_buy 5→3, mc_b_*_circle_div 4→3,
mc_a_red_diamond 4→3). Thresholds unchanged.

**Why:** Otter precision family has the LEAST-bad weighted mean R per the
inventory (-0.21R on 3m, vs Cypher A at -0.38R). On 15m and 30m these are
the strongest performers (spoon_bear 30m = +0.80R; water_buy_large 30m =
+0.70R). They're under-weighted relative to measured edge.

**Prediction:** Standard fire rate similar or slightly higher (precision
signals fire less frequently than Cypher A pumps, but contribute more
weight when they do). Mean R slightly better than H1.

## H3 — Asymmetric net-score (α=1.5)

**Change:** `net = winner − 1.5 × loser`. Keeps current YAML weights but
penalizes opposing-side signal noise more heavily. Same thresholds.

**Why:** The inventory shows alerts fire densely (47 red_diamonds/day, 12
buy_circles/day). Whichever side has marginally more confluence wins under
subtractive formula even when the OPPOSING side has substantial signal
too. α=1.5 requires the winning side to outpace the loser by 1.5×, not
1×. Should suppress fires in noisy / chop regimes where both sides have
signal.

**Prediction:** Fire count drops 40-60%. Win rate up. Mean R per trade
goes from -0.42 toward 0 (less noise). Sum_R likely still negative
(directional regime issue), but per-trade quality up.

## H4 — Conviction ratio (≥0.7)

**Change:** Replace subtractive formula entirely. `score_ratio = winner /
(winner + loser)`; fire when ≥ 0.7. Tier still derived from raw winner
score against current thresholds.

**Why:** Conviction ratio is the cleanest expression of "winning side
dominates". 0.7 = winner must have at least 70% of total weight. Examples:
score 7 vs 3 → ratio 0.70 fires; score 5 vs 3 → ratio 0.625 SKIPS. Removes
the "winner barely beats loser" failure mode that subtractive allows.

**Prediction:** Fire count drops 50-70%. Win rate noticeably up.
Per-trade mean R approaches 0 from below.

## H5 — Family confluence required for PREMIUM (≥3 distinct families)

**Change:** PREMIUM tier additionally requires ≥3 distinct factor-families
contributing to the winning side. Families = {cypher_a, cypher_b,
otter_trigger, otter_precision, cvd, ribbon}. STANDARD unchanged.

**Why:** Current PREMIUM (≥10) can fire from two A-panel diamonds alone
(red+blood = 4+5 = 9, plus any single +1 like cvd_bear_flip = 10). That's
"one chart pattern" not "multi-source confluence". Requiring 3 families
forces genuinely diverse signal.

**Prediction:** PREMIUM fire count drops 60-80%. PREMIUM per-trade mean R
rises substantially (selection effect: surviving PREMIUMs have broad
confluence). STANDARD unchanged.

## H6 — Higher min_score (5 → 7)

**Change:** Raise `min_score_to_fire` from 5 to 7. Other thresholds same.

**Why:** Minimum-score-5 currently allows two weight-3 signals to fire,
or one weight-3 + one weight-2 (5pts). These are the marginal "two
single-bar fires" trades. Inventory suggests even 2-signal confluence is
weak; raising the floor to 7 requires substantially more weight.

**Prediction:** Fire count drops 30-50%. Mean R rises modestly. May
co-improve with H5.

## H7 — Unified cooldown (paired with H2)

**Change:** Add unified cooldown — after ANY fire, both sides paused for
1800s. Combined with H2's re-weighting.

**Why:** Today buy and sell have independent cooldowns. A buy can fire,
SL out, and an opposite sell can fire one second later if the score
flips. Unified cooldown prevents "score flipped twice in 30 min = two
trades in 30 min" which is usually whipsaw noise.

**Prediction:** Fire count drops 20-40% relative to H2. Mean R slightly
up (avoiding immediate flip-trade pairs). Sharpe up disproportionately
(reduced variance from avoided flip trades).

---

## Summary table

| ID | Change | Predicted fire count vs baseline | Predicted mean R direction | Hypothesis "wins" if |
|---|---|---:|---|---|
| H1 | Cap weights at 3 (re-weight) | -10% | flat to +slight | PREMIUM mean_r rises ≥0.1R |
| H2 | H1 + up-weight Otter precision | -5% | flat to +slight | sum_R less negative than baseline AND H1 |
| H3 | Asymmetric net (α=1.5) | -40 to -60% | rises toward 0 | mean_r > baseline by ≥0.15R |
| H4 | Conviction ratio ≥0.7 | -50 to -70% | rises toward 0 | mean_r > baseline by ≥0.20R |
| H5 | PREMIUM requires ≥3 families | PREMIUM -60–80% | PREMIUM mean_r rises sharply | PREMIUM mean_r > STANDARD mean_r by ≥0.2R |
| H6 | min_score 5→7 | -30 to -50% | rises modestly | mean_r > baseline by ≥0.10R |
| H7 | H2 + unified cooldown | -25 to -45% vs baseline | rises | Sharpe > H2's Sharpe |

---

## Falsification criteria (what would *refute* the overall thesis that scoring can be improved)

If **every** variant produces a Sharpe / mean R worse than baseline,
the conclusion is "the current PR 3c config is at least locally optimal,
and the lever to improve is downstream (PA / HTF gates, position
construction)". This is a legitimate outcome — we'd document it and
recommend no scoring change in `scoring_recommendation.md`.

If one or more variants improve IS but **all** degrade sharply OOS, the
conclusion is "we have an overfit-prone tuning surface; recommend
no-change pending more data". Phase 3.2 paper-trade data is genuinely
thin (5 days post-ship); a 47-day signal-replay tells us about the
*scoring engine*, not about *production capital outcomes*.
