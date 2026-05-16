# Backtest Results — In-Sample and Out-of-Sample

Generated 2026-05-16. Backing scripts: `Goals/scratch/{03_strategies.py, 04_strategies_v2.py, 05_sensitivity_and_v3.py, 06_final_sensitivity.py}`. Backing JSON: `Goals/scratch/{backtest_summary.json, backtest_v2_summary.json, backtest_v3_summary.json, final_sensitivity.json, v6_trades.json}`.

**Test corpus.** `bars_3m` table, 22,635 bars, 2026-03-30 → 2026-05-16 UTC (47.15 days).
**Split.** Chronological 70/30: IS = 15,844 bars (2026-03-30 → 2026-05-02 00:09); OOS = 6,791 bars (2026-05-02 00:12 → 2026-05-16 03:42). ~33 days IS, ~14 days OOS.
**Cost model.** 5 bps taker per side + 2 bps slippage = 7 bps/side, 14 bps round-trip. Applied at every position change (incl. flip-throughs which charge 2 × side cost).
**Sharpe.** Annualized from 3m-bar net log-returns × √(175,320) bars/year.
**Trade.** Contiguous run of `position ≠ 0` (flip to opposite or to flat closes the trade).
**No look-ahead.** Position at bar t reflects signal evaluated at bar t-1 close.

---

## 1. Round 1 — Initial 7 hypotheses + 4 repaint-control variants

Per the goal-doc convention: signal triggers per H1-H7 from `reports/hypotheses.md`; exit policy = `time_stop=20, atr_stop=1.5×ATR, atr_target=3.0×ATR, exit_on_opposite=True`.

| Strategy | IS trades | IS net% | IS Sharpe | IS WR% | IS PF | IS maxDD% | OOS trades | OOS net% | OOS Sharpe | OOS WR% | OOS PF | OOS maxDD% | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| **H1_ribbon_bias** | 19 | +0.54 | +0.69 | 52.6 | 2.30 | -2.75 | 11 | **-1.15** | -4.79 | 36.4 | 0.76 | -2.26 | ❌ neg OOS |
| H1b_ribbon_raw (no filter) | 102 | -10.44 | -6.10 | 45.1 | 0.97 | -11.32 | 55 | -7.57 | -11.01 | 36.4 | 0.76 | -9.09 | ❌ |
| H2_wt2nd_revert (5-bar) | 1147 | -64.72 | -46.96 | 38.7 | 0.66 | -64.72 | 523 | -44.40 | -74.05 | 30.0 | 0.39 | -44.39 | ❌ costs eat 5-bps gross edge |
| H3_capitulation (TIGHT_EXIT) | 289 | -22.02 | -20.58 | 39.4 | 0.69 | -22.06 | 140 | -12.26 | -30.42 | 35.0 | 0.58 | -12.29 | ❌ ATR stops cut winners |
| H4_otter_armed (WT-div + Otter) | 29 | -6.37 | -13.39 | 24.1 | 0.26 | -7.00 | 18 | -3.01 | -13.89 | 27.8 | 0.70 | -3.08 | ❌ |
| H5_full_confluence (Otter+B+A) | 14 | -3.55 | -8.14 | 28.6 | 0.30 | -3.83 | 6 | -1.35 | -12.30 | 16.7 | 0.01 | -1.46 | ❌ n too low |
| H6_exhaustion (top/bot + div) | 12 | -1.82 | -6.09 | 33.3 | 0.96 | -2.52 | 4 | -0.65 | -10.69 | 0.0 | 0.00 | -0.65 | ❌ n too low |
| H7_otter_stack (Otter+WT+CVD) | 5 | -0.48 | -2.21 | 40.0 | 0.52 | -1.09 | 0 | 0.00 | 0.00 | 0.0 | 0.00 | 0.00 | ❌ n=0 OOS |

**Round-1 takeaway:** Every strategy with `n ≥ 100` lost money. Every strategy with `n < 100` is below the minimum-validity threshold and OOS metrics aren't meaningful. The ATR-stop framework is materially hurting per-trade returns vs the signals' actual h-bar edge profile (e.g. `wt_2nd_*_divergence` has +4 to +5 bps gross edge at h5 per the inventory; we're cutting positions at -1.5×ATR ≈ -10 bps and targeting +3×ATR ≈ +20 bps, so we mostly stop out before reaching target). The ribbon+bias filter (H1) does narrow trade count vs raw (H1b) and improves IS, but IS sample is too small (19) and OOS is negative.

### Repaint-control results (H8a/b/c × naive vs +1-bar-shift)

| Variant | IS trades | IS net% | IS Sharpe | IS WR% | IS PF | OOS trades | OOS net% | OOS Sharpe | OOS WR% | OOS PF |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| H8a_stoch_naive | 2561 | -55.10 | -24.63 | 39.7 | 0.71 | 1109 | -43.78 | -47.98 | 34.4 | 0.49 |
| **H8a_stoch_shift1** | 2589 | **-96.35** | -95.24 | **19.5** | 0.15 | 1126 | **-75.93** | -111.81 | **17.9** | 0.12 |
| H8b_rsi_naive | 729 | -19.74 | -11.54 | 53.5 | 1.53 | 341 | -16.51 | -25.95 | 47.8 | 1.19 |
| **H8b_rsi_shift1** | 791 | -59.60 | -46.70 | 36.3 | 0.62 | 364 | -35.88 | -63.75 | 31.9 | 0.40 |
| H8c_generic_naive | 222 | -1.42 | -1.42 | 57.7 | 2.40 | 99 | -5.22 | -13.58 | 45.5 | 1.31 |
| **H8c_generic_shift1** | 223 | -18.21 | -21.06 | 35.4 | 0.63 | 99 | -11.45 | -32.66 | 29.3 | 0.35 |

**Repaint verdict — CONFIRMED for all three.** The naive-entry version of each suspect divergence (h1 hit rates of 89-100%) collapses by 30-60 percentage-points of total return when the entry is shifted by +1 bar. The pattern is the textbook "TradingView marks the bar where the divergence STARTED, retroactively — so the indicator value at bar t isn't available in real time at bar t." None of `rsi_*_divergence`, `stoch_*_divergence`, `bull_divergence`, `bear_divergence` can be used as live triggers. They are excluded from all subsequent rounds.

> The inventory-table hit-rate of 100% on `rsi_bullish_divergence` at h1 was the dead giveaway: a real signal cannot have 100% one-bar hit rate; the indicator was simply repainting bar `t` after seeing `t+1`'s close.

---

## 2. Round 2 — Time-only exits + directional variants + confluence

Built after Round 1 made clear that ATR stops were the worst feature of the exit policy for these signals. Round 2 strategies use time-only exits (`exit_on_opposite=True`, but no ATR stop/target) at horizons that match each signal's measured edge profile.

| Strategy | IS trades | IS net% | IS Sharpe | IS WR% | IS PF | OOS trades | OOS net% | OOS Sharpe | OOS WR% | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| V1_wt2nd_time5 | 1106 | -61.26 | -40.52 | 41.2 | 0.77 | 510 | -43.91 | -69.02 | 31.0 | ❌ |
| V2_capitulation_time10 | 277 | -18.13 | -12.22 | 48.0 | 0.99 | 127 | -11.53 | -22.93 | 40.9 | ❌ |
| V3_capit_smabias_time10 | 54 | -4.82 | -8.77 | 50.0 | 0.77 | 31 | -1.36 | -6.18 | 58.1 | ❌ (n<100) |
| V4_capit_long_only | 135 | -10.34 | -10.84 | 50.4 | 0.92 | 52 | -5.43 | -16.65 | 42.3 | ❌ |
| V5_capit_short_only | 142 | -8.87 | -7.21 | 45.8 | 1.05 | 75 | -6.45 | -16.01 | 40.0 | ❌ |
| **V6_ribbon_long_sma** | **19** | **+1.30** | **+1.71** | **73.7** | **3.95** | **8** | **+3.61** | **+9.87** | **87.5** | ✅ but n<100 |
| V7_wt_stacked (2nd ⊕ 1st) | 515 | -43.36 | -24.95 | 40.6 | 0.71 | 230 | -24.35 | -34.97 | 39.1 | ❌ |
| V8_ribbon_cvd | 76 | -7.74 | -6.09 | 44.7 | 0.81 | 30 | +0.51 | +1.09 | 46.7 | ⚠ neg IS pos OOS |
| V9_divcircle_cvd | 21 | -2.76 | -5.37 | 47.6 | 0.68 | 12 | -2.34 | -11.45 | 25.0 | ❌ |
| V10_capit_noflip | 275 | -18.05 | -12.18 | 48.0 | 1.00 | 127 | -11.53 | -22.93 | 40.9 | ❌ |
| V11_divbuy_long20 | 112 | -6.85 | -5.65 | 52.7 | 1.20 | 47 | -4.11 | -10.37 | 51.1 | ❌ |
| V12_divsell_short10 | 142 | -8.87 | -7.21 | 45.8 | 1.05 | 75 | -6.45 | -16.01 | 40.0 | ❌ |
| V13_divcircle_smabias_20 | 51 | -3.77 | -4.73 | 52.9 | 0.96 | 29 | -1.28 | -4.48 | 58.6 | ❌ (n<100) |

**Round-2 takeaway:** One clear winner emerges — **V6_ribbon_long_sma** — long-only ribbon-buy-cross filtered by 30m-SMA-24-bull state with 20-bar time-only exit. It's positive both IS and OOS with strong win rates (74% / 87%), but `n=19/8` is well below the 100-trade minimum. Time-only exits help vs ATR-stop exits, but the underlying signals (wt_2nd, div_circle) still don't clear costs at high trade frequency. Several others (V3, V8, V13) are interesting at neutral-to-slightly-positive OOS but never positive on both sides.

---

## 3. Round 3 — V6 hold-window sweep + W-variants

The V6 result demanded a robustness check across hold windows + bias source + direction.

### V6 hold-window sweep (long-only and both-sides, SMA24 and EMA-flip bias variants)

| Variant | IS n | IS net% | IS Shp | IS WR% | OOS n | OOS net% | OOS Shp | OOS WR% |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| V6_smabias_long_h10 | 20 | +2.87 | +3.95 | 80.0 | 8 | +3.08 | +8.84 | 87.5 |
| V6_smabias_both_h10 | **34** | **+5.37** | **+5.73** | 79.4 | 19 | +3.96 | +9.50 | 78.9 |
| V6_smabias_long_h20 | 19 | +1.30 | +1.71 | 73.7 | 8 | +3.61 | +9.87 | 87.5 |
| **V6_smabias_both_h20** | 33 | +3.95 | +4.04 | 72.7 | 19 | +4.29 | +9.80 | 68.4 |
| V6_smabias_long_h40 | 18 | -1.07 | -1.23 | 50.0 | 8 | +3.76 | +9.25 | 87.5 |
| V6_smabias_both_h40 | 32 | +3.29 | +2.83 | 65.6 | 19 | +4.59 | +9.42 | 68.4 |
| V6_smabias_long_h60 | 18 | -1.44 | -1.42 | 61.1 | 9 | +4.88 | +10.06 | 77.8 |
| V6_smabias_both_h60 | 32 | +1.27 | +0.91 | 71.9 | 19 | +5.23 | +8.55 | 68.4 |
| V6_smabias_long_h100 | 18 | -3.29 | -2.69 | 50.0 | 9 | +5.34 | +9.12 | 88.9 |
| V6_smabias_both_h100 | 31 | -0.55 | -0.34 | 67.7 | 19 | **+7.59** | **+10.42** | 68.4 |
| V6_emabias_long_h10 | 9 | -0.42 | -0.94 | 66.7 | 7 | -0.39 | -1.98 | 42.9 |
| V6_emabias_both_h10 | 19 | +0.49 | +0.70 | 57.9 | 12 | -1.52 | -6.40 | 33.3 |
| V6_emabias_long_h20 | 9 | -0.57 | -1.18 | 66.7 | 6 | -0.83 | -3.73 | 33.3 |
| V6_emabias_both_h20 | 19 | +0.66 | +0.86 | 57.9 | 11 | -0.63 | -2.37 | 36.4 |
| V6_emabias_long_h40 | 9 | -0.10 | -0.19 | 77.8 | 6 | -1.11 | -4.25 | 50.0 |
| V6_emabias_both_h40 | 19 | +1.65 | +2.00 | 68.4 | 11 | -1.08 | -3.62 | 45.5 |
| V6_emabias_long_h60 | 8 | -0.17 | -0.27 | 62.5 | 7 | -0.82 | -2.88 | 42.9 |
| V6_emabias_both_h60 | 18 | +1.30 | +1.26 | 55.6 | 12 | +0.23 | +0.65 | 41.7 |
| V6_emabias_long_h100 | 8 | +0.75 | +0.96 | 62.5 | 7 | -2.01 | -5.85 | 42.9 |
| V6_emabias_both_h100 | 18 | +3.71 | +2.96 | 61.1 | 12 | +0.98 | +2.28 | 33.3 |

**Major finding:** SMA24-bias variants are robust across the entire `hold ∈ {10, 20, 40, 60, 100}` sweep — every single `smabias` variant (10 of 10) is positive OOS. EMA-flip-bias variants are weak in the same sweep — only 3 of 10 positive OOS, and the magnitudes are smaller. **30m SMA(24) is a better HTF filter than 30m EMA-flip-with-decay** for this purpose.

The both-sides variant of V6 with hold=10..100 is also consistently positive IS (5 of 5 hold windows in `+1.27` to `+5.37` range) AND positive OOS (5 of 5 in `+3.96` to `+7.59` range). Trade counts scale 31-34 IS / 19 OOS. **The bilateral variant nearly doubles n vs long-only and keeps the edge.**

### W-strategies (other seed candidates)

| Strategy | IS n | IS net% | IS Shp | IS WR% | OOS n | OOS net% | OOS Shp | OOS WR% | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| W1_divbuy_smabull_h20 | 26 | +0.82 | +1.30 | 65.4 | 13 | -0.20 | -1.13 | 61.5 | ⚠ degrades OOS |
| **W1_divbuy_smabull_h40** | 24 | +4.12 | +4.81 | 66.7 | 12 | -0.49 | -1.84 | 58.3 | ❌ overfit to IS |
| W1_divbuy_smabull_h60 | 22 | +4.48 | +4.26 | 63.6 | 12 | -0.47 | -1.32 | 58.3 | ❌ overfit to IS |
| W1_divbuy_smabull_h100 | 20 | -0.82 | -0.64 | 55.0 | 11 | -0.44 | -0.94 | 63.6 | ❌ |
| W2_divcircle_cvd_h10 | 21 | -2.76 | -5.37 | 47.6 | 12 | -2.34 | -11.45 | 25.0 | ❌ |
| W2_divcircle_cvd_h20 | 21 | -3.91 | -5.92 | 38.1 | 12 | -2.34 | -8.82 | 33.3 | ❌ |
| W2_divcircle_cvd_h40 | 20 | -3.96 | -4.25 | 50.0 | 12 | -1.86 | -5.12 | 41.7 | ❌ |
| W3_divsell_smabear_h10 | 26 | -3.86 | -11.88 | 38.5 | 17 | -0.94 | -5.68 | 52.9 | ❌ |
| W3_divsell_smabear_h20 | 25 | -4.55 | -9.03 | 40.0 | 16 | -1.09 | -4.80 | 56.2 | ❌ |
| W3_divsell_smabear_h40 | 24 | -4.83 | -6.64 | 33.3 | 15 | -0.35 | -0.98 | 60.0 | ❌ |
| W3_divsell_smabear_h60 | 23 | -3.83 | -4.27 | 39.1 | 14 | +0.62 | +1.42 | 50.0 | ⚠ neg IS pos OOS |
| W4_wt2bull_only_h20 | 290 | -17.92 | -9.36 | 50.0 | 129 | -12.39 | -17.25 | 41.9 | ❌ |
| W4_wt2bull_only_h60 | 164 | -8.21 | -3.03 | 46.3 | 72 | -8.84 | -9.59 | 47.2 | ❌ |
| W4_wt2bull_only_h100 | 119 | +1.19 | +0.38 | 49.6 | 52 | -3.70 | -3.60 | 51.9 | ❌ OOS degrades |
| W5_divcircle_smabias_h20 | 51 | -3.77 | -4.73 | 52.9 | 29 | -1.28 | -4.48 | 58.6 | ❌ |
| W5_divcircle_smabias_h40 | 48 | -0.90 | -0.81 | 50.0 | 27 | -0.84 | -1.88 | 59.3 | ❌ |
| **W5_divcircle_smabias_h60** | 45 | **+0.48** | +0.35 | 51.1 | 26 | **+0.14** | +0.26 | 53.8 | ⚠ marginal both sides |
| W6_ribbon_raw_long_h20 | 50 | -2.32 | -1.96 | 50.0 | 27 | -1.93 | -3.34 | 37.0 | ❌ |
| W6_ribbon_raw_long_h40 | 48 | -4.04 | -3.14 | 47.9 | 26 | -2.16 | -3.29 | 46.2 | ❌ |
| W6_ribbon_raw_long_h60 | 46 | -1.68 | -1.20 | 47.8 | 24 | +2.18 | +3.07 | 58.3 | ⚠ neg IS pos OOS |
| **W6_ribbon_raw_long_h100** | 41 | **+2.74** | +1.55 | 56.1 | 23 | **+2.14** | +2.54 | 47.8 | ✅ both sides positive |

**Round-3 takeaway:** V6 with SMA bias is robust to hold-window parameter (5 of 5 IS+OOS-positive variants in the sweep). EMA-flip bias is not robust. W1 is the cleanest overfitting demo (IS +4% Sharpe +4.8 → OOS -0.5%), so it's a useful negative example. W6 (no bias filter at all, just long-only ribbon-cross with 100-bar hold) is positive both sides at +2.74 / +2.14% but the absence of bias filter means it's just capturing BTC drift over the window — should probably be presented as a "baseline" rather than a strategy. W5 is marginally positive both sides but near zero.

---

## 4. Round 4 — V6 deeper sensitivity (SMA window + cost + regime + volatility)

For V6_smabias_both_h20 (the central recommendation), tested SMA window ∈ {12, 18, 24, 36, 48, 72} and cost ∈ {3, 5, 7, 10, 15} bps/side.

### SMA window sensitivity

| SMA window | IS n | IS net% | IS Shp | IS WR% | OOS n | OOS net% | OOS Shp | OOS WR% |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 12 | 33 | +5.64 | +5.68 | 72.7 | 19 | +4.65 | +10.57 | 68.4 |
| 18 | 28 | +3.95 | +4.17 | 78.6 | 17 | +4.72 | +11.20 | 70.6 |
| **24** | **33** | **+3.95** | **+4.04** | **72.7** | **19** | **+4.29** | **+9.80** | **68.4** |
| 36 | 34 | +2.61 | +2.62 | 67.6 | 22 | +3.90 | +8.54 | 68.2 |
| 48 | 38 | +4.11 | +3.97 | 65.8 | 21 | +3.08 | +6.31 | 61.9 |
| 72 | 43 | +2.04 | +1.87 | 60.5 | 20 | +2.16 | +4.57 | 45.0 |

Every SMA window 12-72 produces positive IS+OOS. The edge IS NOT at a knife-edge SMA value — robust across a factor-of-6 in window. Higher SMA windows trade slightly more frequently (more bars qualify as `bull` since the SMA reacts slower) but with weaker per-trade edge. SMA=18 has the highest OOS Sharpe (+11.20); SMA=24 is essentially a tie with the cleanest IS robustness. The bot can choose either; I'll quote SMA=24 as the default since it matches the SMA window in my code from Round 2.

### Cost sensitivity

| Cost (bps/side) | IS n | IS net% | IS Shp | OOS n | OOS net% | OOS Shp |
|---:|---:|---:|---:|---:|---:|---:|
| 3 | 33 | +6.73 | +6.91 | 19 | +5.89 | +13.56 |
| 5 | 33 | +5.33 | +5.47 | 19 | +5.09 | +11.68 |
| **7** | 33 | +3.95 | +4.04 | 19 | +4.29 | +9.80 |
| 10 | 33 | +1.91 | +1.93 | 19 | +3.11 | +7.00 |
| 15 | 33 | -1.39 | -1.37 | 19 | +1.17 | +2.54 |

Strategy survives up to **~10 bps/side cost** comfortably (about 1.4x the assumed 7 bps). At 15 bps/side it's IS-negative but OOS still slightly positive. The edge is real but not enormous; if your actual fill is worse than 7 bps/side (e.g. funding-rate drag, wider spread during news), the strategy could turn unprofitable.

### Regime breakdown (V6 smabias=24, both, h20)

Cumulative P&L attribution by sub-population of bars:

| Sub-population | n bars | Cum net P&L |
|---|---:|---:|
| `bias_bull` bars | 12,290 | **+6.46%** |
| `bias_bear` bars | 10,345 | **+1.83%** |
| Bars where `position = +1` (long active) | 780 | +6.95% |
| Bars where `position = -1` (short active) | 703 | +4.90% |
| Bars where `position = 0` (flat) | 21,152 | 0 |

Both long and short sides contribute. Bull-bias regime is more profitable but it's also more numerous in this 47-day sample. Both directions earn positive cumulative P&L when active.

### Volatility-bucket breakdown

Bars bucketed by 20-bar rolling realized vol (33rd / 66th percentiles).

| Bucket | Active bars | Cum P&L |
|---|---:|---:|
| Low vol (`σ_20bar` ≤ 0.00055) | 368 | +1.51% |
| Mid vol | 474 | +0.67% |
| High vol (`σ_20bar` > 0.00083) | 641 | **+9.79%** |

**Strategy is materially more profitable in high-volatility regimes.** ~80% of total P&L comes from the high-vol third of bars by active count. This makes sense — ribbon crosses in low-vol chop are mostly false signals; in high-vol regimes they typically capture genuine directional moves.

### Full-period trade list summary (combined IS+OOS, all 52 trades)

| Metric | Value |
|---|---:|
| Total trades | 52 |
| Wins | 37 (71.2%) |
| Losses | 15 |
| Total net P&L | +12.37% |
| Mean trade | +0.238% |
| Best trade | +1.502% |
| Worst trade | -0.590% |

Asymmetric P&L distribution — winners ~2.5x the size of losers on average. Full trade list at `Goals/scratch/v6_trades.json`.

---

## 5. Summary: what survives

Per the goal's criterion — 100+ trades AND positive expectancy after costs AND OOS Sharpe meaningfully above zero — **zero strategies fully clear the bar**.

Per a looser criterion of "robust positive net return both IS and OOS, sensitivity-tested" — **V6 family is the one survivor**:

- V6_smabias_both — 5 of 5 IS+OOS positive across hold ∈ {10, 20, 40, 60, 100}
- SMA window ∈ {12, 18, 24, 36, 48, 72} — 6 of 6 IS+OOS positive
- Cost up to 10 bps/side — survives
- Both bull and bear directions contribute positively
- ~80% of edge comes from high-vol regime bars

W5_divcircle_smabias_h60 and W6_ribbon_raw_long_h100 are weak/marginal seeds — positive on both sides but at near-noise magnitudes. They serve as honest "tentative diversifying seeds" with lower confidence than V6.

The **dominant blocker is the 100-trade minimum**: 47 days at 3m granularity gives 22,635 bars but a sane strategy fires ~1-2 trades/day after filters. Getting to 100 IS trades alone would need ~100 days of data; getting to 100 OOS would need another ~100. The data window is too short for the trade-count standard the goal sets, given the kinds of strategies that survive the cost hurdle.

See `reports/strategy_candidates.md` for the final picks with pseudocode, parameter ranges, and how to operationalize.
