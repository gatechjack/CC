# Wick-Test v5 — Runner-Capture (breakout-continuation) — GROSS — VERDICT: FAIL (but first non-beta signal)

**Date:** 2026-07-02 · read-only, no prod/live/SFP writes · GROSS only · k=1 causal · 3m · 4 coins.
Branch `wick-test-spike-2026-07-02` (unpushed). Spec `spike_wick_test/PRE_REGISTRATION_v5.md` (locked before
code). Harness `spike_wick_test/wick_test_v5.py`; run `spike_wick_test/wick_v5_run.log`.

Tests whether a **stop entry that captures continuation (runners)** beats the DR **limit entry that captured
pullbacks** (the v3/v4 loser). Head-to-head on identical setups. Step-0 confirmed **no multi-regime 3m data**
(bars_1m is *shorter* than bars_3m), so this runs on the 47–81d bear window with the **long-alpha tell +
drift-embedding null** as the bear-proof (a long beating a drift-null in a bear window cannot be beta).

## VERDICT — FAIL (pre-registered gate), but qualitatively different from v1–v4
The gate needs ≥3 cells pass (n≥100), ≥2 coins, **both sides**, **≥1 long**, pooled avgR ≥ +0.15R.
Result: **4 cells pass across 3 coins, ≥1 long ✓ — but both-sides ✗ (longs only) and pooled avgR +0.059 ✗.**
**→ FAIL.** *However*, unlike v1–v4 (which failed as pure bear-beta / no signal), v5 produced the arc's **first
genuine, bear-proof (long-side) alpha** — small, one-sided, and sub-threshold, but real.

## The real finding — runner-capture flips LONGS from beta-loss to (tiny) alpha
Longs were negative in **every** prior version (v1–v4). The BC stop-entry flips them positive and **null-beating
on 3 coins**:
| cell | n | BODY avgR | de-trended α-R | null p95 | pass |
|---|---|---|---|---|---|
| BTC long BC atr2.0 none | 849 | +0.046 | **+0.054** | +0.032 | ✔ |
| SOL long BC atr1.5 strength | 175 | +0.065 | **+0.115** | +0.020 | ✔ |
| SOL long BC atr2.0 strength | 167 | +0.028 | **+0.090** | +0.018 | ✔ |
| XRP long BC atr1.0 strength | 223 | +0.097 | **+0.149** | +0.052 | ✔ |
Two corroborations that this is **alpha, not beta**: (a) it's on the **long** side in a bear window (drift works
*against* it); (b) the **de-trended α-R is *higher* than gross** — removing the adverse drift *strengthens* the
edge. A random long with the same geometry (the null) does *worse*.

## Why it still fails — tiny, one-sided, sub-fee
- **One-sided:** no SHORT cell passes. Under BC the shorts don't beat their drift-inflated nulls — the null
  correctly discounts them as beta. So the pattern is: BC-longs = small real alpha; BC-shorts = beta. The
  both-sides gate (a bear-beta guard) correctly fails it.
- **Tiny:** pooled passer avgR **+0.059R**, well under the +0.15R bar; best single cell +0.097R. The R-unit is
  1.0–2.0·ATR (~0.16–0.53% of price), so a ~0.04% round-trip fee is ~0.1–0.3R — **almost certainly larger than
  the gross edge.** Not tradeable net.

## The strength filter EARNED its place (unlike every trend filter)
3 of 4 passers are **BC + strength** (bar3 body ≥ 1·ATR AND close in the extreme third). It separates for BC
longs (SOL long none −0.122 → strength +0.065; XRP long none −0.008 → strength +0.097). **Break-strength
predicts continuation** — the first filter in the arc to add value, consistent with the runner thesis (strong
breaks run; weak breaks chop). (v1 15m-EMA200, v3 displacement, v4 20-bar-slope all failed to separate.)

## Fill-matrix diagnostic — the edge is in the BC-only runners
On the **both-filled** subset (identical setups that both retested L *and* made a new high — i.e., chop),
**DR (limit@L) beats BC (stop@high) everywhere** (BTC long +0.082 vs −0.302; SOL short +0.251 vs −0.112):
entering higher on a two-sided bar is worse. BC wins *only* via the **BC-only runners** (continuation without
retest), which outweigh the chop for longs. This confirms the DR-skip insight causally: **the edge lives in the
breaks that don't come back**, and a stop-entry is the causal way to preferentially fill them.

## Regime split (informational) & context
BC longs positive mainly in the 15m **up** regime (BTC long up +0.094) — the local continuation edge shows where
the HTF isn't fighting it. Shorts scattered positive (bear) but null-discounted. Drift BTC −4.5% / ETH −33.4% /
SOL −24.4% / XRP −29.3%.

## Recommendation — a genuine fork for you
Per the pre-registered gate this is a **FAIL** (not tradeable: one-sided, +0.06R pooled, sub-fee). But it is
**not the clean null of v1–v4** — it's the first bear-proof long-continuation signal, strength-gated, on 3 coins.
Two honest paths:
1. **Close the wick-test book.** The tradeable bar isn't met and the edge is likely sub-fee. Five constructions,
   one sub-threshold long-only micro-signal — reasonable to retire.
2. **One focused follow-up** on the *specific* lead only: **BC-long + strength filter**, validated on a
   **longer/multi-regime window** (only available at ≥15m — accept the coarser TF) **with a fee model**, to see
   whether the +0.06–0.15R long-continuation α survives out-of-sample and above costs. Do NOT re-sweep shorts or
   other entries — the signal is narrow and specific.

My lean: **(2) but only if a ≥15m multi-regime + fee test is cheap** — the long-side, de-trend-positive,
strength-gated, 3-coin consistency is the first thing in this arc that behaves like alpha; it deserves one clean
out-of-sample look before retirement. Otherwise (1).

## Caveats
47–81d one-bear-regime (no multi-regime 3m exists); GROSS only (fees likely exceed the edge); fill-bar TARGET
excluded for both entries (conservative — understates BC same-bar continuation wins, so BC's true edge may be
marginally larger); thin strength cells flagged (*) though the 3 passers are n=167–223. No lookahead:
setup/level/ATR/regime from closed bars; DR/BC fills and sims strictly forward, stop-first.
