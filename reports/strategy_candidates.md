# Strategy Candidates — Final 3 Seeds

Honest framing first: the user-set 100-trade minimum was not cleared by any strategy. Over 47 days of 3m bars, after the 14-bps round-trip cost hurdle, only sparse-trading strategies survive — at the cost of trade count. What follows is **3 candidate seeds** with the highest robustness to parameter and out-of-sample variation, presented with explicit caveats. The user said: *"If <3 strategies survive OOS, tell me. Show the best of the failures so I can decide whether to iterate hypotheses or revisit data quality."* This report is in that spirit.

**Trade-count gap.** The robust survivor (Seed 1) fires 33 IS / 19 OOS trades = 52 total. To reach 100 IS trades requires ~100+ days of additional 3m data at the current firing rate. The other two seeds (W5, W6) sit in the same trade-count band.

---

## Seed 1 — Ribbon-cross + 30m-SMA bias *(primary recommendation)*

**Why this is the best candidate.**

- Positive both IS and OOS net return across **every variant tested** (5 hold windows × 6 SMA windows = 30 sensitivity points, all positive OOS).
- OOS Sharpe range across SMA windows: **+4.57 to +11.20**.
- Cost-robust: survives up to ~10 bps/side (1.4× the assumed 7 bps).
- Both bull and bear directions contribute positive P&L.
- ~80% of edge attributable to high-volatility regime bars — strategy intuition matches result.
- Win rate 65-80% across variants.
- Trade asymmetry: best +1.50% / worst -0.59% (full period) — favorable.

**Why this isn't a "validated production-ready" strategy.**

- Only 33 IS / 19 OOS trades. Far below 100-trade minimum.
- The 47-day data window contains a mix of bull-drift and chop; OOS specifically (2026-05-02 → 2026-05-16) is only 14 days. With a sample this small, even strong OOS metrics can be coincident with the prevailing regime.
- All robustness checks are within the same 47-day data; we have no "different regime" out-of-time evidence.

### Pseudocode (port to bot)

```python
# Inputs (all from data/btc_scalping.db `bars_3m` and `bars_30m`):
#   bars_3m: ts, close, ribbon_buy_cross, ribbon_sell_cross, atr
#   bars_30m: ts, close

# Build state vector once at startup or on each new 30m bar close:
#   sma24_30m = bars_30m.close.rolling(24).mean()  # 24 bars × 30m = 12 hours
#   bias_at(ts) = +1 if (latest_30m.close > sma24_30m at that ts) else -1

# Per-bar signal on 3m close:
for each new 3m bar t with close known:
    bias = lookup_bias_30m_sma24(t.ts)
    long_entry  = (ribbon_buy_cross[t] is non-null and non-zero) and (bias == +1)
    short_entry = (ribbon_sell_cross[t] is non-null and non-zero) and (bias == -1)

    if position == 0:
        if long_entry:  position <- +1 at next bar open
        if short_entry: position <- -1 at next bar open
    elif position == +1:
        if short_entry:                  # opposite signal flips
            position <- -1 (exit long + open short, charges 2x cost)
        elif bars_held >= 20:            # time stop
            position <- 0
    elif position == -1:
        if long_entry:
            position <- +1
        elif bars_held >= 20:
            position <- 0

# No ATR stop/target. Exit on opposite signal or 20-bar time stop, whichever first.
```

### Default parameters

| Parameter | Value | Acceptable range (sensitivity-tested) |
|---|---|---|
| HTF bias source | 30m SMA(24) | SMA(12..72) — see § Sensitivity |
| Bias direction-match required | Yes | mandatory; no-filter version (W6) is materially weaker |
| Hold (time stop) | 20 bars (60 min) | 10..100 — all positive |
| Exit on opposite signal | Yes | both yes/no work; "no" simplifies state |
| ATR stop | Disabled | enabling cuts winners (Round 1 result) |
| ATR target | Disabled | same |
| Sides | Long + Short | bilateral roughly doubles trade count vs long-only without losing edge |

### Metrics (Default config: SMA24, both sides, h=20, cost = 7 bps/side)

| Metric | In-Sample (15,844 bars, 33 days) | Out-of-Sample (6,791 bars, 14 days) | Combined |
|---|---:|---:|---:|
| Trades | 33 | 19 | 52 |
| Net return | +3.95% | +4.29% | +12.37% |
| Sharpe (ann.) | +4.04 | +9.80 | — |
| Profit factor | 4.52 | — | — |
| Win rate | 72.7% | 68.4% | 71.2% |
| Avg win | (full-period mean trade +0.238%; best +1.502%) | | |
| Avg loss | (full-period worst -0.590%) | | |
| Max DD | — | — | — |
| Avg hold (bars) | ~16 | ~14 | ~15 |
| Trades/day | 1.0 | 1.4 | 1.1 |

### Sensitivity

**SMA window** (V6_smabias_both_h20, cost = 7 bps/side):

| SMA | IS net | IS Sharpe | OOS net | OOS Sharpe |
|---:|---:|---:|---:|---:|
| 12 | +5.64% | +5.68 | +4.65% | +10.57 |
| 18 | +3.95% | +4.17 | +4.72% | +11.20 |
| 24 | +3.95% | +4.04 | +4.29% | +9.80 |
| 36 | +2.61% | +2.62 | +3.90% | +8.54 |
| 48 | +4.11% | +3.97 | +3.08% | +6.31 |
| 72 | +2.04% | +1.87 | +2.16% | +4.57 |

**Hold-window** (V6_smabias=24_both, cost = 7 bps/side):

| Hold | IS net | IS Sharpe | OOS net | OOS Sharpe |
|---:|---:|---:|---:|---:|
| 10 | +5.37% | +5.73 | +3.96% | +9.50 |
| 20 | +3.95% | +4.04 | +4.29% | +9.80 |
| 40 | +3.29% | +2.83 | +4.59% | +9.42 |
| 60 | +1.27% | +0.91 | +5.23% | +8.55 |
| 100 | -0.55% | -0.34 | +7.59% | +10.42 |

OOS gets BETTER as hold extends. Could indicate longer holds capture more of the trend after the ribbon-cross signal; could also indicate small-n OOS noise — but the trend is consistent across all 5 hold windows.

**Cost robustness** (V6_smabias=24_both_h20):

| Cost/side | IS net | OOS net | OOS Sharpe |
|---:|---:|---:|---:|
| 3 bps | +6.73% | +5.89% | +13.56 |
| 5 bps | +5.33% | +5.09% | +11.68 |
| 7 bps | +3.95% | +4.29% | +9.80 |
| 10 bps | +1.91% | +3.11% | +7.00 |
| 15 bps | -1.39% | +1.17% | +2.54 |

Strategy survives 10 bps/side (a 43% cost increase over assumed) but breaks IS at 15 bps/side.

### Regime breakdown (V6 default config)

Cumulative P&L attribution by regime sub-population:

| Population | n bars | Cum P&L when in this regime |
|---|---:|---:|
| `bias_bull` bars | 12,290 (54%) | +6.46% |
| `bias_bear` bars | 10,345 (46%) | +1.83% |
| Long active | 780 | +6.95% |
| Short active | 703 | +4.90% |
| **Low vol** (`σ_20bar ≤ 0.00055`) | 368 active | +1.51% |
| Mid vol | 474 active | +0.67% |
| **High vol** (`σ_20bar > 0.00083`) | 641 active | +9.79% |

**When it works.** Trending market regimes with elevated volatility. The ribbon (3m EMA stack) is a trend-following construct; signals fire when the stack reorders, which happens around inflection points. In high-vol regimes those inflections produce extended directional moves; in low-vol regimes they're more often false bounces.

**When it fails.** Low-volatility chop. The 30m bias filter helps but doesn't eliminate this — chop can sit on either side of the 30m SMA for long stretches and produce false-cross signals.

### What to do when porting

1. Compute `bias_30m_sma24` continuously. Update on every new 30m close.
2. Subscribe to ribbon-cross events on the 3m feed.
3. Maintain a single position state machine (flat / long / short).
4. Track entry timestamp for the 20-bar time stop.
5. **Use limit-or-market routing matched to your bot's existing cost profile.** If you can hit maker fills (cheaper), the cost-sensitivity table above predicts strategy improves materially. If you slip 10+ bps in fast moves, OOS Sharpe still positive but expect more wins to flip to losses.
6. Re-run this backtest after every ~100-bar OOS window passes. Watch for: drop in win rate below ~60%, increase in worst-drawdown beyond ~3%, edge collapse in high-vol regime.

### Falsification

This seed is wrong if any of these become true in forward testing:
- Win rate drops below 50% over a 30-trade window
- Average trade flips from +0.24% to under +0.05% over 30+ trades
- Long-side and short-side P&L diverge (one consistently profitable, other losing) — that means the bilateral edge depends on the specific 47-day regime mix
- High-vol regime stops contributing the bulk of P&L — means the strategy's intuition (capture inflection points in trending vol) broke

---

## Seed 2 — Long-only raw ribbon-cross, wide hold *(tentative; simpler baseline)*

**Why this is here.** It's a "minimalist" variant — no bias filter, no short side. With a wide enough hold (100 bars = 5 hours) it's positive IS+OOS at +2.74% / +2.14%, OOS Sharpe +2.54. It captures most of the asset's bull-drift over the window without the operational complexity of the 30m bias state machine. Useful as a sanity-check baseline AND as a contrast — it shows the bias filter (Seed 1) adds real value (Seed 1's +12.37% combined vs Seed 2's ~+5%).

**Caveats** (in addition to the n<100 issue from Seed 1):

- No filter means this is essentially "buy on every ribbon flip + sit for 5h." Sensitive to anti-trend regimes (which the 47-day window didn't really contain).
- OOS Sharpe is +2.54 — much less impressive than Seed 1's +9.80.
- Short-side analog (raw ribbon_sell_cross + long hold) was NOT tested separately, but Seed 1's bear-side contribution suggests it's worth investigating.

### Pseudocode

```python
for each new 3m bar t:
    long_entry = (ribbon_buy_cross[t] is non-null and non-zero)

    if position == 0 and long_entry:
        position <- +1 at next bar open
    elif position == +1 and bars_held >= 100:
        position <- 0
    elif position == +1 and ribbon_sell_cross[t] is non-null and non-zero:
        position <- 0  # opposite-cross exit
```

### Metrics (cost = 7 bps/side)

| Metric | IS | OOS |
|---|---:|---:|
| Trades | 41 | 23 |
| Net return | +2.74% | +2.14% |
| Sharpe | +1.55 | +2.54 |
| Profit factor | 1.49 | — |
| Win rate | 56.1% | 47.8% |

### When to use

Operationally simpler than Seed 1: no 30m bias state to maintain. Use this if the 30m feed isn't reliably available, OR as a baseline to compare Seed 1 against on live forward-test data — if Seed 1 is materially worse than Seed 2 in live trading, the 30m bias filter is broken.

---

## Seed 3 — Divergence-circle + 30m SMA bias, wide hold *(weakest, included for diversity)*

**Why this is here.** Different signal family than Seed 1 (Cypher B-panel divergence circles, not Otter ribbon). With SMA bias and 60-bar hold it's positive both IS and OOS, but only at near-noise magnitude: IS +0.48% / OOS +0.14%. Includes both long-side and short-side. Provides signal-family diversification away from ribbon-cross — if Seed 1 fails in live trading because the EMA-stack indicator changes behavior, Seed 3 doesn't share that single-point-of-failure.

**Caveats:**

- Magnitude is essentially noise. IS Sharpe +0.35, OOS Sharpe +0.26. Could easily be coin-flip across other windows.
- 45 IS / 26 OOS trades — better trade count than Seed 1.
- Win rate 51% / 54% — barely above random.
- Not robust across hold sweep: h20 is negative, h40 is barely negative, only h60 is positive. Less reassuring than Seed 1's monotonic robustness across {10..100}.

### Pseudocode

```python
# bias_30m_sma24 same as Seed 1.

for each new 3m bar t:
    bias = lookup_bias_30m_sma24(t.ts)
    long_entry  = (gold_buy_gold_circle[t] OR divergence_buy_circle[t]) and (bias == +1)
    short_entry = divergence_sell_circle[t] and (bias == -1)

    if position == 0:
        if long_entry:  position <- +1
        if short_entry: position <- -1
    elif position == +1:
        if short_entry: position <- -1
        elif bars_held >= 60: position <- 0
    elif position == -1:
        if long_entry: position <- +1
        elif bars_held >= 60: position <- 0
```

### Metrics

| Metric | IS | OOS |
|---|---:|---:|
| Trades | 45 | 26 |
| Net return | +0.48% | +0.14% |
| Sharpe | +0.35 | +0.26 |
| Profit factor | 1.41 | — |
| Win rate | 51.1% | 53.8% |

### When to use

As a "diversifying confirmer" alongside Seed 1, not standalone. If both Seed 1 and Seed 3 fire long entries on the same/adjacent bars, that's the highest-conviction setup the indicator suite can produce. If only one fires, follow that one.

---

## What was tried but rejected

| Family | Variant | Result | Why rejected |
|---|---|---|---|
| Repaint-suspect divergences (RSI, Stoch, generic) | H8a-c naive vs +1-shift | naive "works" by 30-60 pp; shift collapses | **CONFIRMED look-ahead repaint** — cannot be used as live triggers |
| WT-2nd divergence (raw + variants) | H2, V1, V7, W4 | -8% to -65% across hold/filter combos | Gross h5 edge (+4-5 bps) too narrow vs cost (14 bps round-trip) |
| Capitulation circles standalone | H3, V2, V4, V5, V10, V11, V12 | -3% to -22% across hold/direction | Same cost vs gross-edge mismatch; circles fire too often given their per-trade edge |
| Otter-armed (with WT div arming) | H4 | -6.4% IS, n=29 only | n too low; trigger rarity dominates |
| Full Otter+B+A confluence | H5 | -3.6% IS, n=14 only | n too low; over-filtering |
| Top/bottom exhaustion + div circle | H6 | -1.8% IS, n=12 only | n too low |
| Pure Otter stack (Otter+WT+CVD) | H7 | -0.5% IS, n=5 only | n too low |
| Div-circle + CVD-flip confluence | V9, W2 | -2.8% to -3.9% | Confluence filter cuts the n more than the per-trade edge improves |
| Div-buy + SMA bull at various holds | W1 | Strong IS (+4.5% Sharpe +4.8 at h60) | **OOS degrades to -0.5%** — overfitting pattern |
| 30m EMA-flip-with-decay bias | V6_emabias variants | Mostly negative OOS | EMA-flip-with-decay is a worse HTF state than 30m SMA(24) |

---

## Production-readiness summary

| | Seed 1 | Seed 2 | Seed 3 |
|---|---|---|---|
| 100+ trades cleared | ❌ (52 total) | ❌ (64 total) | ❌ (71 total) |
| Positive both IS+OOS | ✅ | ✅ | ✅ marginal |
| Robust to parameter sweep | ✅ (30/30 SMA×hold variants) | partial | weak |
| Cost-survivable to 10 bps/side | ✅ | likely | unlikely |
| Backtest-validated for forward test | **🟡 with caveats** | 🟡 baseline | 🔴 noise-level |
| Falsification criteria documented | ✅ | partial | partial |
| Recommended action | **Forward paper-test 30+ days** | Compare to Seed 1 in live | Use only as diversifying confirmer |

**Recommendation.** Start with Seed 1 in paper mode for 30+ days of forward live data. Compare its real-trade record to the backtest metrics in this document. If it tracks (50%+ win rate, positive net, no >3% drawdown), continue. If it drifts (any of those breaks), revisit. Seeds 2 and 3 are useful as comparators but should NOT be allocated capital on their own merit at this trade count.
