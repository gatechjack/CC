# 5-Factor Gate — Factor-Contribution Analysis

**Window:** 2026-04-30 → 2026-05-17  ·  **5f-fired trades analyzed:** 33  ·  **PA-only trades analyzed:** 24  ·  **Alerts used for correlation:** 1601

## Q1 — Per-factor WR / avg-R (5f-fired trades only)

Restricted to the 5f-arm round-trips. Each row = trades where
that factor passed (regardless of which other factors passed).

| Factor | n | Win rate | Avg R | Total R |
|---|---|---|---|---|
| ema_alignment | 15 | 53.3% | +0.600 | +9.00 |
| vwap | 27 | 48.1% | +0.487 | +13.16 |
| volatility | 23 | 56.5% | +0.746 | +17.16 |
| cvd | 28 | 46.4% | +0.434 | +12.16 |
| volume_z | 17 | 47.1% | +0.412 | +7.00 |

## Q1 (continued) — Pairwise factor WR / avg-R

Restricted to trades where BOTH factors in the pair passed.
Sorted by total R (highest first).

| Factor pair | n | Win rate | Avg R | Total R |
|---|---|---|---|---|
| vwap + volatility | 17 | 58.8% | +0.833 | +14.16 |
| volatility + cvd | 18 | 55.6% | +0.731 | +13.16 |
| ema_alignment + volatility | 11 | 63.6% | +0.909 | +10.00 |
| vwap + cvd | 22 | 45.5% | +0.416 | +9.16 |
| ema_alignment + vwap | 15 | 53.3% | +0.600 | +9.00 |
| volatility + volume_z | 10 | 60.0% | +0.800 | +8.00 |
| cvd + volume_z | 17 | 47.1% | +0.412 | +7.00 |
| ema_alignment + cvd | 10 | 50.0% | +0.500 | +5.00 |
| vwap + volume_z | 11 | 45.5% | +0.364 | +4.00 |
| ema_alignment + volume_z | 2 | 50.0% | +0.500 | +1.00 |

## Q2 — PA-only trade outcomes (PA fired, 5f rejected)

**n = 24** trades. Outcomes drawn from the PA arm's
`trades.json` — these are the actual trades the PA arm placed,
filtered to those where the 5f gate would have rejected.

- TP hits: **13**
- SL hits: 9
- Win rate: **54.2%**
- Total R: **+16.92**
- Avg R: +0.705

### Did the 5f gate save money or cost money?

**The 5f gate COST money on these rejects.** Total R = +16.92 across 24 trades means the PA arm captured profit that the 5f arm gave up.

### Which factors did the rejecting on these 24 trades?

| Factor | Rejected count |
|---|---|
| ema_alignment | 20 |
| vwap | 5 |
| volatility | 14 |
| cvd | 11 |
| volume_z | 19 |

### Per-trade detail

| ts (UTC) | tier | side | actual R | outcome | factors that failed |
|---|---|---|---|---|---|
| 2026-04-30T17:57:01+00:00 | STANDARD | buy | +2.000 | tp | ema_alignment, vwap, volatility, volume_z |
| 2026-05-04T23:33:02+00:00 | STANDARD | buy | +2.000 | tp | ema_alignment, volatility, cvd |
| 2026-05-11T00:06:01+00:00 | PREMIUM | sell | +2.000 | tp | ema_alignment, vwap, cvd, volume_z |
| 2026-05-11T12:15:02+00:00 | STANDARD | sell | -1.000 | sl | ema_alignment, volatility, cvd, volume_z |
| 2026-05-11T13:30:01+00:00 | STANDARD | sell | -0.364 | flipped | ema_alignment |
| 2026-05-11T14:06:02+00:00 | STANDARD | buy | -1.000 | sl | ema_alignment, vwap, cvd, volume_z |
| 2026-05-11T22:00:10+00:00 | STANDARD | buy | -1.000 | sl | ema_alignment, volatility, cvd, volume_z |
| 2026-05-12T05:12:01+00:00 | PREMIUM | sell | +2.000 | tp | ema_alignment, volatility, cvd, volume_z |
| 2026-05-12T12:51:01+00:00 | STANDARD | sell | +2.000 | tp | ema_alignment, volume_z |
| 2026-05-12T14:51:02+00:00 | STANDARD | sell | +2.000 | tp | volume_z |
| 2026-05-12T23:06:00+00:00 | STANDARD | sell | -1.000 | sl | ema_alignment, volatility, volume_z |
| 2026-05-13T13:27:01+00:00 | STANDARD | sell | +2.000 | tp | volume_z |
| 2026-05-13T15:06:00+00:00 | STANDARD | sell | +2.000 | tp | volume_z |
| 2026-05-13T17:48:02+00:00 | STANDARD | sell | -1.000 | sl | ema_alignment, volatility, cvd |
| 2026-05-13T20:18:00+00:00 | STANDARD | sell | +2.000 | tp | ema_alignment, volatility, volume_z |
| 2026-05-14T21:00:01+00:00 | PREMIUM | buy | -1.000 | sl | ema_alignment, volatility, cvd, volume_z |
| 2026-05-15T01:36:11+00:00 | STANDARD | sell | +2.000 | tp | ema_alignment, vwap, cvd, volume_z |
| 2026-05-15T02:33:00+00:00 | STANDARD | sell | -1.000 | sl | ema_alignment |
| 2026-05-15T03:09:02+00:00 | STANDARD | sell | +2.000 | tp | ema_alignment, vwap, cvd, volume_z |
| 2026-05-15T08:30:04+00:00 | STANDARD | sell | -1.000 | sl | ema_alignment, volatility |
| 2026-05-15T18:09:02+00:00 | PREMIUM | sell | +2.000 | tp | ema_alignment, volatility, volume_z |
| 2026-05-16T00:03:02+00:00 | STANDARD | sell | +2.000 | tp | ema_alignment, volatility, volume_z |
| 2026-05-16T13:15:00+00:00 | STANDARD | sell | -1.000 | sl | volatility, cvd, volume_z |
| 2026-05-16T14:45:01+00:00 | PREMIUM | sell | +0.281 | timeout | ema_alignment, volatility, volume_z |

## Q3 — Factor correlation matrix (full 1,796-alert dataset)

Phi correlation between binary factor-pass series across 1601 alerts where the gate could be evaluated.

| | ema_alignment | vwap | volatility | cvd | volume_z |
|---|---|---|---|---|---|
| ema_alignment | 1.00 | 0.58 | 0.01 | -0.01 | 0.02 |
| vwap | 0.58 | 1.00 | -0.19 | 0.02 | 0.04 |
| volatility | 0.01 | -0.19 | 1.00 | -0.01 | -0.02 |
| cvd | -0.01 | 0.02 | -0.01 | 1.00 | 0.05 |
| volume_z | 0.02 | 0.04 | -0.02 | 0.05 | 1.00 |

### High-correlation pairs (|phi| > 0.6)

None. All factor pairs are below the |phi|=0.6 threshold; no clear evidence of redundancy. The 5-factor structure is carrying 5 distinct signals.

## Q5 — Directional asymmetry check

Hypothesis to test: are the gate's rejections concentrated on
one side? If shorts are overrepresented in the PA-only set
(or longs overrepresented in the 5f-fired set) relative to the
alert-population baseline, the gate has asymmetric directional
behaviour and the per-factor analysis above is contaminated.

### Side baseline — alert population (scorer-fire-eligible)

- Buy intent: 99 (18.3%)
- Sell intent: 441 (81.7%)
- (SKIPped: 1061)

### 5f-fired trades by side

| Side | n | % of 5f fires | Win rate | Avg R | Total R |
|---|---|---|---|---|---|
| buy | 2 | 6.1% | 100.0% | +2.000 | +4.00 |
| sell | 31 | 93.9% | 45.2% | +0.392 | +12.16 |

### PA-only trades by side (PA fired, 5f rejected)

| Side | n | % of PA-only | Win rate | Avg R | Total R |
|---|---|---|---|---|---|
| buy | 5 | 20.8% | 40.0% | +0.200 | +1.00 |
| sell | 19 | 79.2% | 57.9% | +0.838 | +15.92 |

### Code audit — side-conditional logic

Reviewed each `_factor_*` function in
`trading_corp/agents/strategies/bitunix_confluence_gate.py` plus
the input-builder logic in
`trading_corp/data/bitunix_price_context.py`.

**Factor 1 (EMA alignment).** Lines 395–398. Side-conditional logic:
```
if side == 'buy':
    passed = (e8 > e21 > e50) and slope > 0
elif side == 'sell':
    passed = (e8 < e21 < e50) and slope < 0
```
- The inequality is correctly flipped for sell.
- ONLY the EMA8 slope is checked, NOT all three slopes. This
  deviates from the mental model 'all three slopes negative for
  sell' — it's a documented design choice in my Phase A impl,
  but worth flagging because the user expected the stronger check.
  No asymmetry between sides; both rely on slope_8 only.

**Factor 2 (VWAP).** Lines 423–426. Correctly flipped for sell:
```
if side == 'buy':
    passed = (px > sv) and (px > pv)
elif side == 'sell':
    passed = (px < sv) and (px < pv)
```
Symmetric. No bug.

**Factor 3 (Volatility).** Line 453.
`passed = (a > asm) and (bpr >= threshold)` — no `side` reference.
Confirmed direction-agnostic.

**Factor 4 (CVD).** Side check is correctly flipped
(`slope > 0` for buy, `slope < 0` for sell). HOWEVER, the
underlying slope is suspect:

`cvd_from_bars_tick_rule` in
`bitunix_price_context.py:260-310` computes
`linregress_slope(deltas)` where `deltas[i] = sign_i * volume_i`
(per-bar signed volume). The module docstring describes CVD as
'cumulative volume delta = running sum of signed volume', but
the implementation does NOT cumsum the deltas. So the slope
measures whether per-bar deltas are increasing over time,
NOT whether the cumulative CVD curve is sloping.

Real-world impact: in a sustained one-direction move with
roughly constant volume, per-bar deltas look like
`[-v, -v, -v, -v, -v]` (constant negatives) → slope ≈ 0 →
NEITHER buy NOR sell passes. True cumulative CVD would be
`[-v, -2v, -3v, -4v, -5v]` → slope = -v → sell would pass.

Direction implication: this isn't asymmetric per se (both sides
lose the same way during sustained trends), but it makes F4
systematically miss sustained-trend signals. In the 17-day
window the BTC tape was a sustained down-move; that bias would
appear as Factor 4 under-firing on the sell side specifically
because sells outnumber buys in the alert population.

**Factor 5 (Volume z-score).** Side-agnostic in the factor
function. `build_gate_inputs` lines 451-461 computes volume_z
from `b.volume` (unsigned) over the 20 prior 3m bars. No
directional logic. No bug.

### Audit findings summary

- **No asymmetric side-conditional bug** in factor pass/fail
  logic — F1, F2, F4 all correctly flip the inequality for sell.
- **F4 has a semantic mismatch**: docstring says cumulative CVD,
  implementation uses per-bar delta slope. Systematic under-
  firing of F4 in sustained-trend windows on BOTH sides. The
  17-day window was a sell-dominant tape, so the absolute impact
  on sells is larger than on buys (more alerts in the sell
  population) but the per-alert mechanism is direction-agnostic.
- **F1 EMA only checks slope_8**, not all three slopes. Symmetric.
  Deviation from the Board's mental model; not a bug.


## Q4 — Should the 5% fire-rate floor itself be revisited?

(Independent question from whether any factor should be loosened.)

The pre-committed floor (5%) was chosen as a typical sanity
threshold: any gate firing on fewer than 1 in 20 alerts could be
signal-free coincidence rather than a real edge.

This run: **fire rate = 1.84%** (well below floor) yet **profit factor = 2.01** (well above the 1.20 floor), and **win rate = 48.5%** (above the 45% floor). The gate is unusually selective AND unusually profitable.

**Flag for Board:** the floor was set as a *general* sanity check.
A gate that fires rarely but compounds high PF over many windows
could be a legitimate edge — but it could also be statistical
coincidence on 33 trades. Two avenues to disambiguate:

1. **Re-run on a longer window** (3–6 months) and check whether
   PF holds. If it does, the floor was wrong for this gate's
   risk profile and should be re-justified separately.
2. **Compare to a random-fires control** at the same fire rate.
   If random PF >> 1.0 on the same trades, this window is just
   easy market and the 5f gate isn't adding value.

This report does NOT recommend overriding the floor — that's
Board judgment. It flags that the floor decision needs its own
treatment, not bundled into the factor-loosening discussion.

## Methodology

- Factor decisions re-computed from the same 1m Coinbase OHLCV +
  resampled 3m/5m/15m caches the backtest harness uses. CVD
  tick-rule fallback in 100% of evals (same as backtest).
- PA-only outcomes pulled from the existing PA arm's
  `trades.json` — these are the trades the PA arm actually
  placed in-simulation, not isolated re-simulations.
- Pairwise WR/avg-R uses unordered factor pairs; a trade where
  factors A + B both passed counts for the (A, B) row regardless
  of which other factors also passed.
- Phi correlation = Pearson r computed on the 0/1 pass series.
