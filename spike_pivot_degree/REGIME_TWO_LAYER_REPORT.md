# Two-Layer Regime-Aware SFP — Deploy-Candidate Backtest (live 15m SFP → 3m BOS)

*2026-07-01. Read-only. Harness `spike_pivot_degree/regime_two_layer.py`. Vendored
detector byte-unchanged. The EXACT live mechanism (not the 15m proxy): live
`SfpModeBDetector`, 15m SFP → 3m BOS → enter next 3m open, stop = swept_wick −
0.001·entry. Data = `bars_3m` entry window (BTC 81d, ETH/SOL/XRP 47d, **0 gaps**);
regime + HTF context on the full native 15m (~230d) for EMA-200 warmup, looked up at
the last CLOSED 15m/HTF bar before each 3m entry (strict k=1). Costs net (taker
0.019%×2 + 2bp slip×2 = 0.078%/round-trip). Pivots {5,8,10} union, one position/coin.*

## HEADLINE
- **Gross, the strategy has a real edge (+0.15–0.20R/trade). Net of fees it is
  ~breakeven** — fees eat **~0.16R/trade** because the 3m SFP stops are tight
  (~0.5% of price → fee-in-R is large). Fees are the dominant deploy risk.
- **Layer 2 (HTF R:R tiering) as specified makes it WORSE, not better.** It turns the
  positive fixed-2R baseline into a **net loss** in all three HTF timeframes.
- **The tier thesis fails the test:** not monotonic (moderate-1.5R ≥ strong-2R in
  every config; 4H strong is negative) and **strong-2R beats the null in none**.
- **Recommendation: deploy Layer 1 only, with a FIXED 2R target — do NOT ship Layer 2.**

## P&L illustration (sum net R × risk, pooled over the 4 windows)
| config | overall net/trade | gross/trade | total R | @0.05 risk | @0.10 risk |
|---|---|---|---|---|---|
| **BASELINE-2R (no Layer 2)** | **+0.038R** (n=237) | +0.198R | **+9.1** | **+45.5%** | **+91.0%** |
| 1H tier | −0.011R (n=248) | +0.149R | −2.6 | −13.2% | −26.4% |
| 4H tier | −0.010R (n=245) | +0.150R | −2.4 | −11.9% | −23.9% |
| 1D tier | −0.015R (n=244) | +0.145R | −3.6 | −18.2% | −36.4% |

*(10× lev enables the position size; it does not change the R math. Illustrative only —
pooled across coins' unequal ~7–12wk windows, single bear-ish regime.)*

## Side × regime (Layer 1, net R) — consistent across configs
| | UP | RANGE | DOWN |
|---|---|---|---|
| **Long** (baseline) | −0.156R (n=83) | −0.089R (n=28) | — (gated) |
| **Short** (baseline) | — (gated) | +0.058R (n=30) | **+0.237R (n=96)** |

Layer 1 gates out counter-trend (long-down, short-up). **The only clearly positive
cell is SHORT-DOWN (+0.237R @2R)** — bear-aligned shorts running to 2R. **Longs bleed
even when aligned** (long-up −0.156R). So the edge is essentially "short the bear at
2R" = bear-beta; it will need re-evaluation if the regime turns bullish (the same
unresolved risk as the prior spikes).

**Aligned-edge null:** aligned (long-up + short-down, ungated, @2R) = +0.005R net,
null p95 −0.024 → **BEATS** (the 15m regime label is informative), but the level is
economically ~breakeven after fees.

## Layer 2 — R:R tier by HTF strength (the thing under test)
Expectancy by tier (net R), per HTF timeframe:

| HTF | strong / 2.0R | moderate / 1.5R | mild / 1.25R | weak / 1.0R | monotonic? | strong-2R beats null? |
|---|---|---|---|---|---|---|
| **1H** | +0.081 (n=41) | **+0.130** (n=56) | −0.010 (n=53) | −0.130 (n=98) | **no** (mod>strong) | no (p95 +0.297) |
| **4H** | −0.087 (n=42) | **+0.270** (n=54) | +0.042 (n=34) | −0.128 (n=115) | **no** (strong<mild) | no (p95 +0.283) |
| **1D** | +0.288 (n=58) | +0.286 (n=23) | — (n=0) | −0.165 (n=163) | **no** (mild empty) | no (p95 +0.333) |

**The thesis ("HTF strength predicts how far aligned trades run → give strong trades
2R") is NOT supported:**
- The ordering is **not monotonic** in any timeframe. **Moderate (1.5R) is the best
  tier** everywhere; strong (2R) is inconsistent (negative for 4H).
- **strong-2R beats the null in none** — the HTF-strong label does not select
  better-running trades than a random relabel.
- **Why Layer 2 hurts:** the positive comes from SHORT-DOWN trades that want the *big*
  2R target (trend runs — matches the prior R:R spike). The strength-percentile tiering
  scatters those shorts across moderate/weak and **caps them at 1–1.5R**, removing the
  right-tail winners the baseline keeps. Cutting the target raises win-rate (40%→50%)
  but loses the payoff.
- **The one robust sub-signal:** the **weak tier (flat/counter/unwarmed HTF, 1R) is
  reliably the worst** (−0.13 to −0.17R). "Skip weak-HTF trades" is a real lever; "scale
  target UP to 2R for strong" is not.

**⚠ 1D FLAG (as predicted):** 1D EMA-200 needs 200 daily bars; 230d of 15m warms only
the last ~30d, so **92/244 (38%) of 1D trades fell in the unwarmed region** (forced
weak/1R). The 1D "strong" bucket is the last ~30d only — thin and window-biased. **1D
tiering is not trustworthy on this data.**

## Fire rate (accepted trades/week/coin — what we take live)
BTC ~6.8–7.2 · ETH ~9.1–9.6 · SOL ~7.6 · XRP ~6.9–7.5 → **~30 trades/week across the 4
coins** (pivots {5,8,10} union). Far higher than the live pivot-50's ~5/46d.

**Per-coin net (baseline-2R):** BTC −0.113(79) · ETH −0.113(61) · **SOL +0.159(51)** ·
**XRP +0.366(46)**. The positive is concentrated in SOL/XRP (their bear short-down ran
hardest); BTC/ETH are net-negative even at baseline. No coin is a clean all-regime
winner — pooled is the honest read, and it leans on SOL/XRP bear shorts.

## Recommendation
1. **Ship Layer 1 + fixed 2R. Do NOT ship Layer 2 (HTF tiering).** Baseline is the best
   config tested (+0.038R net, +45.5%eq @0.05 risk); every HTF-tier config is net-
   negative on this data. If Layer 2 is politically required, **1H** is the least-bad
   (fully warmed, tier separation present) and **1D must not be used** (unwarmed).
2. **Treat net as ~breakeven, not the gross.** Fees (~0.16R) nearly eat the +0.20R
   gross; the 2bp slippage stub is optimistic (live SFP-reversal slippage can be
   several× — see stop-slippage memory), so real net skews toward/below zero. Size
   small (the 0.05 risk illustration is the aggressive end).
3. **Know what you're deploying: a bear-short.** The edge is SHORT-DOWN at 2R + gating
   out counter-trend; longs bleed even aligned. It is bear-beta and will degrade if the
   regime flips — monitor the 15m EMA-200 regime and be ready to re-evaluate on a flip.
4. **Better Layer-2 idea for later (not tested here):** the data says "**skip weak/flat-
   HTF trades and target ~1.5R**," not "scale up to 2R for strong." A filter-out-weak +
   1.5R variant is the promising redesign — but that needs its own causal/null run.

*Single bear-ish regime (~8 months incl. bounces); no extended bull in the data. All
numbers net of the modeled fees; causal (k=1) confirmed on every layer. Deploy per
operator call, but with eyes open: the tiering premise did not survive the test.*
