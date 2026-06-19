# BitUnix bull-signal starvation diagnostic — why no long has ever fired

**Date:** 2026-06-19
**Branch:** `bull-signal-starvation-diagnostic-2026-06-19` (off origin/main `1c12d5c`)
**Mode:** READ-ONLY. NO code, NO scoring change, NO deploy, NO prod write. Prod read via read-only SSH (`mode=ro`, CLAUDE.md 82fda13). Per §4.
**Operator concern:** bitunix has NEVER fired a buy/long — it only shorts, sitting idle through bull moves. Floated hypothesis: bull (esp. Lord Otter) signals aren't consumed by the scorer, or the threshold is bear-biased.

> **VERDICT (per-stage trace, not assumption): the bull side does NOT die at the webhook, the scorer, or the threshold. The scorer is symmetric and bull clears the score gate 1,469 times. Bull dies DOWNSTREAM, at the directional PA-validation gate (primary) and HTF-regime gate (secondary) — both of which were facing a sustained bear HTF regime for the entire live window. The "Otter gap" hypothesis is REFUTED.**

---

## The per-side funnel (the headline)

Live window 2026-05-11 → 2026-06-18. Counts are per scoring-evaluation unless noted.

| Stage | Bull (buy) | Bear (sell) | bull death? |
|---|---:|---:|---|
| Webhook messages received (ledger) | 2,428 | 5,003 | no — bull arrives (~64/day) |
| Scoring evaluations emitted | 2,633 | 16,622 | no |
| **Cleared the score gate** (net_score ≥ fire) | **1,469 (55.8%)** | 13,636 (82.0%) | **no — bull alive here** |
| Survived PA validation | ~89 | ~1,005 | **← PRIMARY KILL: −1,380 (93.9%)** |
| Survived HTF regime gate | ~29 | ~760 | secondary kill: −54 `regime_forbids_side` |
| Proposed orders | 18 paper / **0 live** | 162 paper / 4 live | |
| paper_trade_record | 10 | 147 | |
| **Live fires (net)** | **0** (1 attempted → rejected by halted reconciler) | 4 | |

**Last stage bull is alive: the score gate (1,469 cleared).** From PA validation onward it collapses.

---

## Stage 1 — Webhook: bull is NOT starved

From the prod `bitunix_signal_ledger` (7,434 rows, 2026-05-11→06-18):
- **Bull 2,428 (32.7%) vs Bear 5,003 (67.3%)** — a ~1:2 skew, but bull arrives in material volume (~64/day).
- The skew is **entirely Market Cypher**: `mc_a_red_diamond` (2,278) + `mc_a_redx` (996) = 44% of all rows, with no comparable-frequency bull analog.
- **Lord Otter is essentially symmetric**: 681 bull vs 650 bear. `cvd_bull_flip` 450 ≈ `cvd_bear_flip` 466; `spoon_bull` 151 ≥ `spoon_bear` 124; `otter_buy` 40 ≥ `otter_sell` 34; `money_bag_bottom` 27 ≥ `money_bag_top` 23.
- Skew is consistent across 3m/15m/30m (~31–33% bull each).

**→ Bull is not starved at the webhook. The Otter bull stream in particular is near-1:1 with bear.**

---

## Stage 2 — Scorer consumption: every bull signal IS consumed (Otter gap REFUTED)

Live scorer = `trading_corp/agents/strategies/bitunix_confluence.py`, config = `config/strategies.yaml [bitunix_futures]` (prod byte-identical to repo, SSH-confirmed).

- **All bull signals are in the `factors` map and weighted** — none are silently dropped by name. The resolver matches lowercase + strips `_bull/_bear/_buy/_sell` suffixes.
- Every Lord Otter bull signal is **consumed**: `otter_buy` (wt 3), `spoon_bull` (2), `cvd_bull_flip` (2), `money_bag_bottom` (2), `water_buy_large` (2), `water_buy_small` (1), `bias_bull` (2), `pink_box_bull` (1). Every Cypher bull too: `mc_b_gold_buy` (5), `mc_b_buy_circle_div` (4), `mc_b_buy_circle` (3), `mc_a_bluetriangle` (3), `mc_a_longema` (2), `mc_b_buy_dot` (2).
- **Weights are symmetric**: max theoretical buy score = 36, max sell = 35.
- Empirical proof bull confluence works: representative score-cleared bull payloads — `trigger=money_bag_bottom net_score=10 (buy 21 / sell 11)` and `trigger=spoon_bull net_score=10 (buy 14 / sell 4)` — both **PREMIUM-tier longs**. Otter bull signals demonstrably push bull over the bar.

**→ The Otter-gap hypothesis is REFUTED. Otter bull is consumed, weighted, and reaches PREMIUM.**

Minor config notes (not the cause): `mc_a_yellow_x` is configured `side: buy` (wt 2) but is a *bear* bias-setter in the observer (`CYPHER_BIAS_BEAR`) — a real miscategorization, but it *favors* bull. `score_timeframes: [3m,15m,30m]` drops 4h/1D signals (symmetric). WEAK tier (3) is unreachable below `min_score_to_fire`.

---

## Stage 3 — Threshold: symmetric

- `min_score_to_fire` currently **5** (STANDARD ≥5, PREMIUM ≥10), applied to `net_score = buy_raw − sell_raw`. (Empirically the effective gate was **8** earlier in the window, loosened to 5 on 2026-06-16 — bull cleared it either way.)
- **The threshold is identical for bull and bear.** Bull cleared it **1,469 times** (55.8% of bull evaluations). Not the cause.

---

## Stage 4 — Where bull actually dies (empirical, prod)

Of the 1,469 score-cleared bull evaluations, terminal outcomes:

| Outcome | Bull count | % of score-cleared |
|---|---:|---:|
| **Killed at PA validation** | **1,380** | **93.9%** |
| Killed at HTF regime gate | 54 | 3.7% |
| Killed at trade-plan | 6 | 0.4% |
| Placed / HTF-aligned | 28 | 1.9% |

1. **PRIMARY — PA validation (93.9%).** The PA validators (`vwap_alignment`, `volume_confirmation`, `structure_alignment`, `min_validators_passed: 2`) reject bull. Most common failures: `vwap_alignment` + `structure_alignment` (price below VWAP, no bullish structure). NB: the *rate* (93.9%) ≈ bear's 92.6% — PA is not rate-biased against bull; its *failure mode* is directional (bull can't satisfy "above VWAP / bullish structure" in a downtrend).
2. **SECONDARY — HTF regime gate.** Of 54 hard-blocked bull, **44 = `regime_forbids_side`** — the H1/H4/D1 composite is net-bearish and explicitly forbids longs (size_multiplier=0). Another 10 = `proximity_to_resistance`.
3. **The single live bull attempt** (2026-06-16 18:06, order `c7e33759`) was mechanically rejected: `"BitunixBroker halted… position_state_reconciler_divergence"` — the known reconciler-halt bug, not signal logic. **Net live bull fills: 0.**

**Root condition:** the strategy is HTF-trend-aligned, and BTC ran a **sustained bear HTF regime the entire window** (~$80k mid-May → ~$62k mid-June). The scorer kept saying "bull confluence is strong" (1,469×), but the PA/HTF gates correctly (by design) refused to fade the higher-timeframe downtrend. The "bull moves" it sat through were intra-day bounces inside a bear macro trend.

---

## The verdict — ranked

The bull/bear asymmetry is **NOT in the confluence scorer** (symmetric: consumed-set, weights, threshold all balanced; bull clears 1,469×). It lives entirely in the **directional post-score gates**:

1. **PA validation gate** (primary): kills 93.9% of score-cleared bull; `vwap_alignment`/`structure_alignment` fail for longs in a bear regime.
2. **HTF regime gate** (secondary): `regime_forbids_side` hard-zeros bull size when the HTF composite is bearish.
3. **Amplifier**: the live window was a persistent HTF downtrend, so the gates suppressed essentially all longs by design; the one that slipped through was killed by the reconciler-halt bug.

Not the webhook (bull arrives), not the scorer (bull consumed), not the threshold (bull clears).

---

## Live vs backtest scorer — shared, with a critical caveat for the redesign

- **The SCORER is fully shared.** The live observer (`bitunix_futures_observer.py`) and the backtest (`scripts/backtest_bitunix_confluence.py`) both import `evaluate_confluence_futures` / `filter_live_alerts_with_dedupe` from the same `bitunix_confluence.py` and both build `BitUnixConfluenceConfig.from_dict(strategies.yaml["bitunix_futures"])`. **Tuning the scoring config tunes both simultaneously.**
- **⚠ But the gates where bull actually dies are NOT exercised by the redeem-cap backtest path.** `run_redeem_cap_backtest` is called with `pa_config=None`, and the HTF regime gate is a live-side construct. So the recent synth backtests (and the redeem-cap arms) reflect the **symmetric scorer**, not the **directional PA/HTF gates**. **To study or validate any bull-side fix, the backtest must be run through the PA-validation + HTF-regime gates** (`--gate pa_validation` with PA/HTF config wired) — otherwise the backtest will happily "fire" longs the live system would gate out, and won't reproduce the starvation.

---

## Where the lever is (diagnosis only — NOT applied)

If the operator wants bitunix to take longs: the change is in the **PA-validation / HTF-regime gate behavior for counter-HTF-trend longs**, not the scorer. Candidate levers (for a separate, gated redesign — none applied here): relax `structure_alignment`/`vwap_alignment` strictness for longs; reconsider `regime_forbids_side` (whether a strong bull *score* should be allowed to override a bearish HTF regime, or trade smaller); revisit how sticky the HTF regime classification is. Also worth fixing regardless: the `mc_a_yellow_x` buy/bear miscategorization, and the reconciler-halt bug that killed the one live bull attempt. **No fix is applied in this diagnostic.**

**Hard stops honored:** read-only throughout; no code/scoring/deploy/prod write; verdict derived from the per-stage trace (webhook vs scorer vs threshold vs gates), not pre-concluded as the Otter gap; no signed/live API; no polymarket.
