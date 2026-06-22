# Fee-gate flip verdict — does the corrected fee admit a net-positive cohort?

**Date:** 2026-06-22
**Branch:** `bitunix-redeem-sim-2026-06-22` (worktree `cc-tpsl-rebuild-wt`)
**Status:** READ-ONLY analysis. No strategy/config/prod change. Clean corpus only.
**Question (Fee-vs-edge Step 2):** Step 1 corrects the bitunix taker fee 0.0004 → 0.00019
(venue-actual VIP3 Fee-Discount-Card; the model over-bills ~2.1×). That correction is
*true* regardless. The `fees_too_high_for_risk` plan_skip gate keys off the fee, so a lower
rate ADMITS trades it currently skips. **The decision question is not whether to correct the
rate — it is whether to ACCEPT the resulting looser gate, i.e. is the newly-admitted
("flipped") cohort net-positive?**

---

## VERDICT — CASE (c): corrected fee is right, but the gate should STILL skip these

The flipped cohort is **net-NEGATIVE in every window and on both sides of the lockbox.**
Correcting the rate is accurate accounting; admitting the trades it un-gates **loses money on
more trades.** The `fees_too_high_for_risk` gate should remain in force at the lower threshold.

- **Flipped cohort (all 6 windows): 183 trades, net −0.368 R/trade, total −67.4 R.**
- TRAIN: 89 trades, **−0.462 R/trade**, −41.1 R.
- VALIDATE: 94 trades, **−0.280 R/trade**, −26.3 R.
- Negative in **6 / 6** windows. Lockbox holds (negative on both train AND validate) → not
  an in-sample artifact.

**Confidence: HIGH.** N is modest but adequate (183 flipped trades; 460 fee-skips total), the
sign is unanimous across windows and across the train/validate split, and the *mechanism* is
unambiguous (below) — these trades have ~zero gross edge, so no fee level rescues them.

---

## Method

- **Tool:** `scripts/run_redeem_sim.py` → `run_redeem_cap_backtest` (the validated, look-ahead-
  honest, real-fee redeem-aware sim). Clean corpus `btc_scalping.db` `bars_3m` (refuses
  `trading_corp.db`). Synth rising-edge signal stream, prod `strategies.yaml` config, v2
  trade-plan incl. the `fees_too_high_for_risk` plan_skip gate.
- **Fee override (added this task, additive, default-off):** `--taker-pct` / `taker_pct=` on
  the driver. When set, it rebinds the engine's four fee globals (`_FEES_TK/_FEES_MK/_RT_TK/
  _RT_MK`) for the run so the corrected rate flows CONSISTENTLY to BOTH (a) the
  `fees_too_high_for_risk` GATE (`build_v2_plan` reads `_FEES_TK.round_trip_cost_pct()` for the
  TP1 fee-floor) and (b) the net-R cost. Default `None` = engine 0.0004, byte-for-byte
  unchanged. Unit-tested (no-op at default, restore-on-exit, restore-on-exception, reject
  negative, gate-loosens + net-R-lifts on corpus). Full `test_run_redeem_sim.py` green (17/17).
- **Cap:** fixed at **cap = 2** (3m bars). The cap question is already closed NULL — this run
  isolates the FEE. (cap held constant across both fee runs so the only moving variable is the
  rate.)
- **Flip isolation:** for each window, run the sim at 0.0004 and at 0.00019 on the *same*
  preloaded inputs. The FLIPPED cohort = signals that are `plan_skip` with reason
  `fees_too_high_for_risk` at 0.0004 **and** become walked (R-resolved) trades at 0.00019,
  matched by `(signal_ts, entry_ts, side)`.
- **Rails:** look-ahead-honest (engine, retained), real corrected fees, clean corpus, lockbox
  train/validate, regime-noted (window open→close drift), repaint-clean (causal engine).
  Win-rate is DIAGNOSTIC only; net-R of the admitted cohort is the sole decision metric.
- **Regime note:** mixed tape — TRAIN windows bull/neutral (+8.7%, +2.8%, +6.1%), VALIDATE
  windows bear/bear/neutral (−9.3%, −13.2%, −1.7%). The flip verdict is negative across BOTH
  regimes, so it is not regime-contingent.

## Per-window results (cap=2, taker mode)

| lockbox | window | regime (drift) | fee-skips@0.0004 | **flip** | **flip %** | **flip net-R/trade** | flip total-R | flip win% (diag) | whole book@0.00019 net-R/trade |
|---|---|---|---|---|---|---|---|---|---|
| TRAIN | 04-01..04-15 | bull (+8.7%) | 64 | 21 | 32.8% | **−0.367** | −7.71 | 61.9% | −0.327 (n=52) |
| TRAIN | 04-15..04-29 | neutral (+2.8%) | 87 | 39 | 44.8% | **−0.515** | −20.10 | 48.7% | −0.380 (n=67) |
| TRAIN | 05-01..05-15 | bull (+6.1%) | 74 | 29 | 39.2% | **−0.458** | −13.28 | 51.7% | −0.262 (n=50) |
| VALIDATE | 05-15..05-29 | bear (−9.3%) | 113 | 50 | 44.2% | **−0.304** | −15.20 | 62.0% | −0.230 (n=83) |
| VALIDATE | 05-20..06-03 | bear (−13.2%) | 104 | 38 | 36.5% | **−0.255** | −9.67 | 63.2% | −0.195 (n=77) |
| VALIDATE | 06-03..06-17 | neutral (−1.7%) | 18 | 6 | 33.3% | **−0.235** | −1.41 | 66.7% | −0.152 (n=38) |

## Roll-up

| split | fee-skips | flip | flip % | **flip net-R/trade** | flip total-R | flip win% (diag) |
|---|---|---|---|---|---|---|
| TRAIN | 225 | 89 | 39.6% | **−0.462** | −41.09 | 52.8% |
| VALIDATE | 235 | 94 | 40.0% | **−0.280** | −26.28 | 62.8% |
| **ALL** | **460** | **183** | **39.8%** | **−0.368** | **−67.37** | 57.9% |

**Flip count & %: 183 of 460 fee-too-high skips flip to trades (~39.8%) under the corrected
rate.** The other ~60% of fee-skips stay skipped even at 0.00019 (their TP1 fee-floor still
exceeds TP2 at the lower rate, or the redeem expires / score-decays before firing).

## WHY (mechanism — this is what makes the verdict robust, not just an N-bound sign)

Decomposing all 183 flipped trades at the CORRECTED fee:

- **Gross-R per trade ≈ +0.004 R** (total +0.81 R over 183). The flipped cohort is
  **~breakeven *before fees*.** There is essentially no gross edge to harvest.
- **Fee drag ≈ 0.37 R per trade even at the corrected 0.00019 rate.** The gate flips exactly the
  trades whose stop is so tight that the round-trip cost is a large fraction of 1R. Halving the
  fee halves the drag — but ~0 gross edge cannot cover even the halved drag.
- **Payoff is skewed against them:** wins net only **+0.36 R** (TP1 sits at/just above the fee
  floor → clipped small) while losses net **−1.37 R** (full −1R stop + fee). A 57.9% win rate
  cannot overcome that asymmetry → negative expectancy.

This is precisely the condition the `fees_too_high_for_risk` gate is designed to catch: trades
whose stop is too tight relative to fees have no fee level at which they become attractive,
because the tight stop also caps the reward. The correction makes the *number* on the books
accurate; it does not create edge where there is none.

(Side mix of the flipped cohort: 178 sell / 5 buy — consistent with the prior bull-starvation
finding; the gate-flips are almost entirely shorts. 85/183 were redeemed entries, 98 first-pass.)

## Caveats / N

- **N modest:** 183 flipped trades across ~12 corpus-weeks; the smallest window (06-03..06-17,
  neutral) contributes only 6 flips. But the sign is unanimous (6/6 windows, both lockbox
  halves) and the gross-edge≈0 mechanism explains it, so the verdict is not N-fragile.
- **cap=2 chosen** (finite, fast, prod-plausible). The flip verdict is a property of the gate +
  the trades' RR, not the cap; cap only changes WHICH late entries exist, and ~half the flips
  are first-pass anyway. A cap=inf re-run is not expected to change the sign (gross≈0 holds).
- **Whole admitted book at corrected fee is also net-negative every window** (−0.15 to −0.38
  R/trade) — consistent with the standing "bull side underwater on this faint-gross-edge tape /
  shorts-only" finding. The flipped cohort sits at the worse end of that book and drags it.
- Synth signal stream + 3m resolution (same honest harness as the /goal); not live fills.

## Bottom line

Correct the fee rate (Step 1) — it is simply true. But **do NOT loosen the
`fees_too_high_for_risk` gate to admit the flipped trades.** They are ~breakeven gross,
net −0.37 R/trade after the *corrected* fee, negative across every window and both lockbox
halves. Admitting them trades "more signals taken" for a −67 R drag over the corpus. **Case (c).**
Keep the gate; the right follow-up is to re-derive the gate THRESHOLD at the corrected rate
(it should skip fewer than at the inflated rate, but these 183 are not the ones to let in).
