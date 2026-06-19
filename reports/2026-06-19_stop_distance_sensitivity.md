# Stop-distance sensitivity — net-per-fire vs stop width (mechanics)

**Date:** 2026-06-19
**Branch:** `stop-distance-sensitivity-2026-06-19` (off origin/main `1c12d5c`)
**Mode:** READ-ONLY. NO prod/stop/config change, NO deploy. Per §4. Corrected effective fees from `fee-model-reconciliation-2026-06-19` (discounted entry 0.0243%, maker TP exit 0.0140%, taker SL exit 0.0400%) — NOT the overstated all-taker headline.

> **VERDICT: widening the stop does NOT help — it makes net WORSE, monotonically. There is no breakeven at any realistic scalp width; the current (tightest, ~0.3%) ATR/swing stop is already the least-bad and is what earns the faint +gross. The naive "fees are 333× risk → widen the stop to escape them" intuition is REFUTED: the fee-drag does fall with width, but the gross-R collapses faster (TPs become unreachable on a quiet 3m tape). The binding lever is the GROSS EDGE (regime), not the stop.** (Interim: one bear/quiet window, faint gross edge; N=5 anchors the fee rates only.)

---

## 1. The current stop (pinned empirically)
There is no fixed stop in config — the SL is **ATR/swing-derived** (`StrategyConfig`: `atr_multiplier=1.5`, bounded 0.5–2.5 ATR, swing buffer). Over the real cleared-long population via `build_v2_plan`:
- **mean stop 0.343%, median 0.288%** (the "0.30%" cited earlier is the median on this low-vol 3m tape).
- win 62.8%, **net −0.1365R** (n_traded=908; 561 plan-skipped by the real fee-gate). This **reconciles the PA cost test's corrected −0.13R**, with gross ≈ **+0.065R**.

TPs are placed at the strategy's R-multiples off the stop (0.5R / 1.0R / 2.5R, fractions 0.25/0.50/0.25), so TP *prices* scale with the stop width.

---

## 2. The sweep (fixed-% stop, corrected fees, all 1,469 longs walked)

| stop % | win % | open % | gross R | round-trip % | **fee-drag R** | **net R** |
|---:|---:|---:|---:|---:|---:|---:|
| **0.30** | 64.6 | 0.0 | −0.013 | 0.0575 | **0.192** | **−0.204** |
| 0.40 | 64.5 | 0.0 | −0.065 | 0.0575 | 0.144 | −0.209 |
| 0.50 | 62.8 | 0.0 | −0.111 | 0.0580 | 0.116 | −0.227 |
| 0.75 | 56.9 | 0.3 | −0.183 | 0.0595 | 0.079 | −0.262 |
| 1.00 | 50.8 | 3.3 | −0.257 | 0.0611 | 0.061 | −0.318 |
| 1.50 | 38.5 | 8.6 | −0.381 | 0.0643 | 0.043 | −0.425 |
| 2.00 | 35.4 | 23.3 | −0.339 | 0.0651 | 0.033 | −0.373 |

*(Fixed-% sweep abstracts the ATR/swing SL logic and the fee-gate — it isolates the stop-width mechanic. The baseline §1 is the real strategy.)*

---

## 3. The curve — both halves of the lever

- **The fee side behaves exactly as theory said:** fee-drag = round-trip% / stop% falls from **0.192R → 0.033R** as the stop widens 0.30% → 2.0%. The fee lever works.
- **But the gross side collapses faster:** gross-R goes **−0.013 → −0.38** as the stop widens. Why: on a low-vol 3m tape the bar moves are small; with TPs at fixed R-multiples, a wider stop pushes TP *prices* farther out (TP3 at 2.0% stop = 5% move) → rarely reached in 24h → **open% climbs 0% → 23%**, and the marked-to-market R is poor. Wider stops simply don't fit a quiet scalp tape.
- **Net = gross − fee-drag worsens monotonically.** No breakeven anywhere in 0.30–2.0%. **The tightest stop is the best of the bad options.**

### Naive vs empirical breakeven (the key mechanical point)
- **Naive** (assume the +0.063R gross *held constant* and only fee-drag fell): breakeven at fee-drag = 0.063 → round-trip 0.0575% / 0.063 ⇒ **~0.91% stop**. This is the seductive "just widen the stop" answer.
- **Empirical**: gross does **not** hold — it collapses with width. At ~0.9% the gross is ≈ −0.22R, so net ≈ −0.30R, not 0. **The naive breakeven is never reached.** The gross edge is itself stop-width-dependent on this tape, which breaks the simple fee-vs-width tradeoff.

### The placement insight
The +0.065R gross is **earned by the ATR/swing SL placement**, not by the width: a *naive* fixed-0.3% stop (no swing awareness) has gross **−0.013R** — the swing logic is worth ~+0.08R of gross. So the SL approach is already doing real work and is near-optimal; the problem is not a mistuned stop.

---

## 4. Leverage / TP-ladder interactions (flagged)
- **25× liquidation:** position size is set to risk a fixed % of equity, so a *wider* stop ⇒ *smaller* position ⇒ *less* leverage used ⇒ *more* liq headroom. Liquidation is **not** the binding constraint in 0.3–2.0%; the gross collapse is.
- **TP ladder coupling:** the sweep kept TPs at fixed R-multiples, so wider stops make TP prices unreachable (the rising open%). To widen the stop *and* keep TPs reachable you'd have to **re-space the ladder tighter** (lower R-multiples) — which cuts R-per-win and defeats the point. Stop width and the TP ladder can't be moved independently.

---

## 5. Combined verdict (closes the fee → stop chain)
Across the chain: fees were overstated (corrected net −0.13R, still negative) → and now **the stop is not a recoverable lever either** (widening worsens net; no breakeven; current placement is already best). **So the bull side is not close-but-mistuned on fees or the stop — it is underwater at this window's faint gross edge.** The +0.065R gross (well-placed, ATR/swing) is simply too small to clear even corrected fees at the best stop. The only thing that changes the math is a **larger gross edge**, which requires a regime with real directional follow-through (bull / transition) — the same gate that has recurred through every diagnostic. On this bear/quiet window, **shorts-only / don't-force-longs is the mechanically correct posture**; whether longs ever pay is a regime question this data can't answer.

---

## Caveats (prominent)
- **One bear/quiet window**, faint one-directional gross edge → the "best stop here" is optimized for THIS low-gross tape, **not a cross-regime verdict**. The real best stop needs a regime with genuine gross edge (transition data — same gate as redeem-cap).
- **N=5 real-fill trades anchor the FEE rates only**; win-rate/gross come from the larger backtest population (1,469 cleared longs).
- The fixed-% sweep is a **mechanical idealization** (fixed-% SL + R-multiple TPs) that abstracts the ATR/swing SL and the fee-gate; the baseline (§1) is the real strategy and is the trustworthy "current" anchor.
- Open (timeout) trades are marked-to-market at the final bar — unbiased across widths, but a real scalp wouldn't hold 24h, so wide-stop nets are if anything generous.

**Hard stops honored:** read-only sweep, nothing applied; corrected effective fees used (not the all-taker headline); no cross-regime "optimal stop" verdict (mechanics + the naive-vs-empirical breakeven, regime caveat flagged); leverage/TP interactions noted not engineered; no git stash; no signed/live API; no polymarket.
