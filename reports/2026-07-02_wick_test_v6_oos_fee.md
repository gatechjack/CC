# Wick-Test v6 — OOS + fee validation of the v5 lead — VERDICT: FAIL → wick test retired for good

**Date:** 2026-07-02 · read-only, no prod/live/SFP writes · GROSS + NET · k=1 causal · 15m · 4 coins.
Branch `wick-test-spike-2026-07-02` (unpushed). Spec `spike_wick_test/PRE_REGISTRATION_v6.md` (locked before
code). Harness `spike_wick_test/wick_test_v6.py`; run `spike_wick_test/wick_v6_run.log`.

Confirmatory test of the **only** non-beta signal from v5 — breakout-continuation (BC stop) **LONG + strength
filter** — on a longer **15m multi-regime** window (~230–238d), with a **fee model** and an **IS/OOS split**.
Mechanism fixed, no re-sweeping.

## VERDICT — FAIL (decisive). The v5 3m signal was window/TF-specific.
Gate needs ≥2 coins net-positive-and-null-beating, IS&OOS sign-stable, pooled NET avgR ≥ +0.05R.
Result: **0 coins pass · 0 cells beat the net-null · no IS/OOS stability anywhere · pooled n/a. → FAIL.**
Per the pre-registration: **WICK TEST RETIRED FOR GOOD** — the runner-capture long lead did not survive
timeframe transfer + fees + out-of-sample.

## The evidence, plainly
- **The 15m window is a fair out-of-bear-concentration test:** still net-bear (drift BTC −42% / ETH −59% /
  SOL −60% / XRP −58%) but with materially more up-regime bars than the 3m slice (e.g. BTC up 8,811 / down
  10,517). A real long-continuation edge would show here; it doesn't.
- **Fees are decisive.** The gross long edges are tiny and the fee (~0.05–0.10R on a k·ATR stop) erases them:
  best case ETH strength k1.5 **gross +0.120 → net@base +0.017** (net-null +0.056 → no). Every net-positive cell
  (only ETH k1.5 +0.017, SOL k2.0 +0.012) **fails its net-null.** **0/24 BC-long cells beat the null.**
- **No IS/OOS stability.** Every coin×k fails the "both halves net>0" test — signs flip or stay negative (XRP
  k2.0: IS +0.011 → OOS −0.239; SOL k2.0: IS −0.114 → OOS +0.171). The signal is not temporally consistent.
- **Negative even in UP regimes** (BTC up −0.210, SOL up −0.182, XRP up −0.196 net; only ETH up +0.029) — the
  v5 3m up-regime positives **do not replicate** at 15m. This directly refutes "a real up-continuation edge."
- **Shorts confirmed pure beta:** BC-short+strength is net-negative on every coin and fails the null — exactly
  as the null was designed to expose.

## What killed the v5 lead — a textbook overfit-death
v5's BC-long+strength was **+0.06R gross, one-sided, sub-fee, on a 47–81d bear window.** It *passed the 3m null*
but died the moment it faced (1) a different timeframe, (2) a fee model, and (3) a temporal OOS split — all
three independently. This is the canonical "a small in-sample positive that clears one null but does not
generalize." The disciplined call in v5 (FAIL the tradeable gate; flag as a narrow lead needing OOS) was
correct: option 2 was the cheap confirmatory test, and it returned a clean negative before any capital was at
risk.

## Wick-test arc — final ledger (v1–v6, all GROSS unless noted)
| ver | construction | verdict |
|---|---|---|
| v1 | retest-fill entry, 15m-EMA200 trend filter, fixed 0.1% stop | no robust edge; with-trend filter refuted; positives = geometry + bear-beta |
| v2 | pre-positioned limit @ level, body-close vs hard | gross-negative everywhere; 100% vacuous fill; body-close > hard only because stop too tight |
| v3 | DR retest entry + k·ATR stop + body-close + earn-its-place | 0/48 beat null; DR-skip proves the entry discards the *runners* |
| v4 | v3 + 20-bar-slope trend filter | 0/48; trend-filter timeframe was never the constraint |
| v5 | runner-capture (BC stop) + strength filter | FAIL gate but **first non-beta signal** — BC-long+strength, tiny (+0.06R), long-only, sub-fee |
| v6 | v5 lead → 15m OOS + fees + IS/OOS | **FAIL** — signal is TF/window-specific; dies net + OOS |

**Conclusion:** six independent constructions; no tradeable, net-positive, OOS-stable, both-sides edge on
BTC/ETH/SOL/XRP. The wick test — retest *or* breakout-continuation — has **no demonstrated alpha** on this data.
Reusable learnings that outlive the setup: (1) a retest-into-the-wick entry is anti-selective for continuation
(discards runners); (2) a constant-%/too-tight stop makes a strategy fee-dominated and noise-stopped; (3) short
"edges" in a bear window must clear a drift-embedding null (they never did); (4) the long side in a bear window
is the bear-proof alpha tell — and it did not hold up net/OOS. **Recommend: close the wick-test book.**

## Caveats
15m ≠ the author's 3m scalp (TF transfer is part of the test, and a negative here doesn't prove the 3m setup is
tradeable — but combined with v5's sub-fee, one-sided 3m result, the weight is clearly negative); fee/slippage
are modeled estimates (sensitivity shown: gross, net@taker 0.00038, net@base 0.00058); one 230d sample, IS/OOS
are correlated halves. No lookahead: all reads from closed 15m bars; fills/sims forward, stop-first.
