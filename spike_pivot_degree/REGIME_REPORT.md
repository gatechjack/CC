# Regime-Conditional SFP — Proof of Concept (2026-07-01)

**Goal (operator):** not a long-only bot, not a short-only bot — a bot that identifies the
**regime** and trades the SFP side that fits it. Reviewed weekly; validated across regimes as
data accumulates.

**Method:** 46 days (2026-05-15 → 07-01, a strong BEAR) of BTC/ETH/SOL/XRP 3m data. Long SFPs =
real bars; short SFPs = price reflected around midpoint (validated long-only `SfpModeBDetector`).
tp_r=2.0 fixed (isolate regime from R:R). Regime computed on REAL 15m prices from a trailing MA
(no material look-ahead), tagged at each trade's entry. Pooled pivots {5,8,10} × all coins.
Harness: `regime_filter.py` (reuses the validated `backtest.py` + `rr_sweep.py` + `short_sfp_sweep.py`).

## Result — regime conditioning WORKS (clearest on the long side)

EMA-200 + slope (3-state), pooled expectancy R by (side × regime):

| | UP | RANGE | DOWN |
|---|---|---|---|
| **Long SFP**  | −0.01R (n=97)  | +0.03R (n=38) | **−0.32R** (n=317) |
| **Short SFP** | +0.55R (n=188) | +0.26R (n=55) | +0.46R (n=171) |

- **Trend-aligned** (long-up + short-down): **+0.29R** (n=268)
- **Counter-trend** (long-down + short-up): **+0.00R** (n=505)
- Unconditional long: −0.22R · Unconditional short: +0.47R

**Robust across formulas** — 5-day momentum, SMA-100 slope (12h), and EMA-200±slope all show the
same pattern (long-up ≈ breakeven, long-down ≈ −0.31R; short positive throughout). Not overfit to
one definition.

**Longs bleed ONLY in downtrends.** A regime rule that blocks longs when the HTF trend is down
recovers the −0.32R. That is the proof-of-concept: **the side must match the regime.**

## The honest limitation — one regime only (bear)

Short SFPs are positive in **every** sub-regime, and **best in "up" (+0.55R)**. Shorting into an
uptrend being the *best* short bucket is the tell: in a 46-day bear, the detected "up regimes" are
multi-day **bounces**, and fading a bear-bounce is a premium short entry — **not a real bull**.

- **PROVEN:** regime conditioning rescues longs (down-regime → don't go long); trend-aligned ≫
  counter-trend / unconditional.
- **UNPROVEN:** whether shorts bleed in a *genuine* bull. No bull exists in this data.
  `short-in-up` winning here is bounce-fading, not bull-shorting. **This is the exact gap that
  forward data across regimes will close.**

## Recommended reviewable regime formula

**15m EMA-200 + slope:**
- **UP**   = close > EMA200 **and** EMA200 rising (slope over ~8h / 32 bars)
- **DOWN** = close < EMA200 **and** EMA200 falling
- **RANGE**= otherwise

**Side rule:** long SFP only in UP (or RANGE); short SFP only in DOWN (RANGE too, *for now* — it's
a bear-range). Never take counter-trend (long-in-DOWN was the −0.32R bleed).

## Weekly review procedure (operator)

1. Recompute the regime series on the trailing ~few weeks of 15m (`regime_filter.py`).
2. Confirm trend-aligned expectancy still > counter-trend (edge intact).
3. **Watch for a regime FLIP** — when EMA200 turns up and price reclaims it, we may be leaving the
   bear. At that point the *untested* risk goes live: "does short bleed in a real bull?" → shift to
   long-primary and re-check the short bucket on the fresh up-regime data.
4. Re-run this harness monthly as data grows. **The first true bull / extended range is the key
   validation** — it's the missing regime.

## Caveats
- 46 days, ONE regime (bear). Short edge is partly bear-beta; the long-side rescue is the cleaner proof.
- No fees/slippage modeled.
- Regime from trailing MA — no material look-ahead, but the exact MA/threshold choices are
  preliminary (3 formulas agree on direction; none is tuned/validated).
- n is thin in the rare up/range buckets — read those directionally, not precisely.

*Research only. Validate across regimes before any live change.*
