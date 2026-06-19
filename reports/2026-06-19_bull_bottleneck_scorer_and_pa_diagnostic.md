# The two upstream bull bottlenecks — scorer 9:1 skew + PA-validation: correct or costly?

**Date:** 2026-06-19
**Branch:** `bull-bottleneck-scorer-pa-2026-06-19` (off origin/main `1c12d5c`)
**Mode:** READ-ONLY. NO prod/scoring/PA/config change, NO deploy. Prod read via read-only SSH (`mode=ro`, 82fda13). Per §4.
**Framing (operator-set):** the question is NOT "is it too strict" but **"is the suppression CORRECT (killing losers / regime-appropriate) or COSTLY (killing winners)?"** — settled with a gated trade-walk, not opinion.

> **INTERIM — one mostly-NEUTRAL-daily window (2026-05-11 → 06-19). NOT a cross-regime verdict.** A real answer needs regime-transition / a bull window where longs might actually be profitable.

> **COMBINED VERDICT: neither bottleneck is a bug, and neither is costing money — because there is no profitable long cohort to recover on this data.** (1) The 9:1 scorer skew is **REGIME**, not structural — the market printed bear, a symmetric scorer amplified it. (2) PA kills ~87% of cleared longs, but the gated cost test shows **the PA-killed longs are net-LOSERS, statistically indistinguishable from the PA-passed longs** (which also lose). So PA isn't killing winners (not costly) and isn't usefully filtering (no edge). **The bull side's real problem is fee/stop economics, not the gates: a +0.06R gross edge is swamped by a ~0.30R fee drag from the 0.30% stop.** Don't loosen the scorer or PA to "recover longs" — it would only fire more net-losing longs.

---

## Part 1 — the scorer 9:1 buy/sell skew: REGIME, not structural

Stage-by-stage decomposition of the cleared 1,469 buy : 13,669 sell:

| Stage | buy | sell | ratio |
|---|---:|---:|---:|
| Raw webhook signal count | 2,502 | 4,929 | 1 : 1.97 |
| Realized weighted-volume (count × factor weight) | 6,882 | 16,252 | 1 : 2.36 |
| Score-eval winning side | 2,633 | 16,660 | 1 : 6.33 |
| **Cleared (tier ≥ STANDARD)** | **1,469** | **13,669** | **1 : 9.30** |
| Clear rate | 55.8% | 82.0% | |

**Where the amplification comes from (1:2 raw → 1:9 cleared):** NOT asymmetric weights (the bull-starvation diagnostic confirmed max buy 36 ≈ max sell 35, and Otter ~symmetric). It traces entirely to **which signals the market printed**:
- The dominant bear signals are the high-frequency **Market Cypher A** cluster: `mc_a_red_diamond` (2,278 × w4 = **9,112 weighted-vol — alone larger than the entire bull side's 6,882**), `mc_a_redx` (996 × w2 = 1,992), `mc_a_blood_diamond` (276 × w5 = 1,380).
- **TTL persistence** (1:2.36 weighted-vol → 1:6.33 eval-winning): once the bear stack leads, it keeps winning the net-score contest across many eval cycles within its 30-min TTL.
- **Clear-margin** (1:6.33 → 1:9.30): when sell wins it wins by more (high-weight bear stack), so it clears the threshold at 82% vs buy's 56%.

This is a **symmetric scorer faithfully amplifying a bear-signal-heavy regime**. In a bull window the same machinery would amplify bull.

**`mc_a_yellow_x` side-bug (config `side: buy`, truly bear):** 74 fires; fixing it shifts only 148 weighted-vol buy→sell, *worsening* the bull skew marginally. Immaterial, and it currently (wrongly) *helps* bull. Not a driver.

**Part 1 verdict: REGIME (appropriate), not a structural over-emission bug.** Caveat: the "symmetric amplification" is symmetric by construction but only *observed* amplifying bear here — confirm on a bull window.

---

## Part 2 — PA-validation on longs: the cost test

**What PA checks for a long** (`evaluate_pa_validation`, live config `require_all=false, min_validators_passed=2` of 3): `vwap_alignment` = price above session VWAP; `structure_alignment` = `higher_highs_4h`; `volume_confirmation` = volume > 20-bar avg; plus a rush/fall hard-reject (≥5% 60-min drop). A long needs ≥2 of the 3.

**The cost test** (read-only harness: real cleared-long population, reconstruct PriceContext from corpus, run the live PA to label PASS/REJECT, then trade-walk **every** long regardless via the engine's `build_v2_plan` + `walk_v2`, split by PA label). Fidelity vs the live PA decision: **87.9%** (1,291/1,469; disagreements are near-boundary VWAP flips, Bybit-vs-BitUnix bars).

| cohort | walked | win % | gross/fire | **net-taker/fire** | net-maker/fire | plan-skip (fee-gate) |
|---|---:|---:|---:|---:|---:|---:|
| PA-PASS (191 longs) | 110 | 61.8 | +0.064 | **−0.284** | −0.183 | 81 |
| PA-REJECT (1,278 longs) | 798 | 62.9 | +0.063 | **−0.240** | −0.153 | 480 |

**The answer: PA is killing LOSERS, not winners — but it has no real discriminating power.**
- The PA-rejected longs are **not worse** than the PA-passed longs — win rates (62.9% ≈ 61.8%), gross (+0.063 ≈ +0.064), and net (−0.240 vs −0.284) are statistically indistinguishable; if anything REJECT is marginally *better*.
- **Both cohorts are net-NEGATIVE** at taker AND maker. Even PA's *approved* longs lose money net-of-fees.
- So PA is **not costly** (it's not suppressing a winning cohort) and **not a useful filter** (it doesn't separate winners from losers — the longs it kills perform the same as the ones it keeps).

**Is PA trend-aligned (the operator's instinct)? Yes, mechanically — but it doesn't matter here.** `structure_alignment` (4h higher-highs) is in **1,175 of 1,278** rejections (688 all-three + 317 vwap+structure + 170 vol+structure) — so PA does demand bullish 4h structure for a long, which fails in a downtrend, exactly as hypothesized. But demanding it earns nothing on this data: the structure-failing longs aren't losers any more than the structure-passing ones.

---

## Combined verdict — where the recoverable bull opportunity is

**There is ~none on this window.** Both upstream "bottlenecks" behave correctly/harmlessly:
- The 9:1 skew is the regime (bear market printed bear signals) through a symmetric scorer — not a bug to fix.
- PA suppression isn't costing money — the longs it kills are net-losers, like the longs it passes.

**The bull side's actual problem is fee/stop economics, not the gates.** Across both cohorts: gross is *slightly positive* (+0.06R, win ~62% — the long signals have a faint directional edge), but the **~0.30R fee drag** (driven by the 0.30% stop → fees are a large multiple of the tiny risk-per-unit) sinks it. The v2 plan's own fee-gate already drops ~40% of longs as not-worth-trading (`plan-skip`), and the survivors still lose net. This converges with the prior fee-gate and native-synth-backtest findings: **fees dominate at the 0.30% stop distance.**

**Implication (diagnosis only — nothing applied):** loosening the scorer or PA to let more longs through would *add net-losing trades*, not recover profit. If there's a bull lever, it's the **fee/stop economics** (stop distance vs fee load), and it can only be tested where longs are actually profitable — i.e. a **bull / regime-transition window**, which this data is not.

---

## Method / fidelity notes
- Gates wired: live `PAValidationConfig.from_dict` (min-2-of-3), not the engine's stricter `None`-default. Trade-walk = the engine's own `build_v2_plan` + `walk_v2` + net-of-cost formulas (taker `_RT_TK` / maker `_RT_MK`), reused not reimplemented.
- **Reconstruction fix (surfaced):** `find_bar_at` hard-codes `COINBASE_GRANULARITY_SEC=60` (1-min windows); fed 3m bars, it dropped 52% of off-boundary signals as "no bar." Fixed by snapping each signal to its in-force 3m bar (the engine's own `_bar_idx_at` convention) → 0 dropped, full 1,469 population.
- PA held the only variable: population fixed (real cleared-longs), bars/walk fixed, only the PA label splits cohorts.

**Hard stops honored:** read-only; no prod/scoring/PA/config change; the "too strict" conclusion was NOT drawn without the cost test (and the cost test refuted it — PA kills losers, not winners); the trade-walk ran with gates wired; no cross-regime verdict (interim, one mostly-NEUTRAL window, transition data flagged); no git stash; no signed/live API; no polymarket.
