# Scoring inventory — per-signal × per-TF edge measurement

**Source data:** synthesized ledger from `data/btc_scalping.db` bar tables (3m / 15m / 30m), rising-edge of each TradingView indicator column → factor mapping (see `reports/scoring_decision_log.md` for mapping appendix). Window: 2026-03-30 → 2026-05-16 (47 days). 3m bars used for SL/TP resolution.

**Edge measurement:**
- `mean_pct_Nm` = mean direction-adjusted % close-to-close return at horizon N min
- `hit_pct_Nm` = % of fires where direction-adjusted return > 0
- `mean_r` = mean R-multiple of a 2R-target trade, stop = max(1.5×ATR(14), 0.3%×price), 24h timeout, NET of 9 bps round-trip cost
- `tp_rate / sl_rate / timeout_rate` = % of fires resolving as TP / SL / timeout

**Quality flags:** ★ = positive net R AND ≥45% positive-R fires; △ = breakeven (|mean_r| < 0.05); ✗ = negative mean R.

## TF = 3m

| Signal | Side | Wt | TTL | N | /day | mean_r | +R% | TP% | SL% | TO% | hit_60m% | mean_60m% | flag |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|
| water_buy_large | buy | 2 | 30 | 15 | 0.32 | 0.1 | 46.7 | 46.7 | 53.3 | 0.0 | 73.3 | 0.0819 | ★ |
| cvd_bull_flip | buy | 2 | 30 | 542 | 11.49 | -0.048 | 41.3 | 41.3 | 57.9 | 0.7 | 53.3 | 0.0493 | △ |
| mc_a_bluetriangle | buy | 3 | 30 | 548 | 11.62 | -0.056 | 41.2 | 41.2 | 58.2 | 0.5 | 52.1 | 0.0263 | ✗ |
| mc_a_longema | buy | 2 | 30 | 214 | 4.54 | -0.069 | 40.7 | 40.7 | 57.9 | 1.4 | 49.8 | 0.0324 | ✗ |
| spoon_bull | buy | 2 | 30 | 168 | 3.56 | -0.095 | 39.3 | 39.3 | 58.3 | 2.4 | 57.5 | 0.077 | ✗ |
| mc_b_buy_circle_div | buy | 4 | 15 | 202 | 4.28 | -0.139 | 38.6 | 38.6 | 61.4 | 0.0 | 55.9 | 0.0434 | ✗ |
| mc_b_buy_circle | buy | 3 | 15 | 557 | 11.81 | -0.156 | 37.9 | 37.9 | 61.8 | 0.4 | 52.2 | 0.0135 | ✗ |
| water_sell_large | sell | 2 | 30 | 27 | 0.57 | -0.183 | 37.0 | 37.0 | 63.0 | 0.0 | 44.4 | -0.0888 | ✗ |
| mc_b_gold_buy | buy | 5 | 15 | 19 | 0.4 | -0.186 | 36.8 | 36.8 | 63.2 | 0.0 | 42.1 | -0.0595 | ✗ |
| money_bag_bottom | buy | 2 | 30 | 22 | 0.47 | -0.203 | 36.4 | 36.4 | 63.6 | 0.0 | 59.1 | 0.0715 | ✗ |
| spoon_bear | sell | 2 | 30 | 156 | 3.31 | -0.277 | 34.0 | 33.3 | 65.4 | 1.3 | 52.6 | -0.0048 | ✗ |
| water_buy_small | buy | 1 | 30 | 3 | 0.06 | -0.3 | 33.3 | 33.3 | 66.7 | 0.0 | 66.7 | 0.1943 | ✗ |
| bias_bull | buy | 2 | 90 | 79 | 1.68 | -0.308 | 32.9 | 32.9 | 67.1 | 0.0 | 54.4 | 0.0543 | ✗ |
| mc_b_sell_circle_div | sell | 4 | 15 | 235 | 4.98 | -0.377 | 30.2 | 30.2 | 68.9 | 0.9 | 54.0 | 0.0134 | ✗ |
| otter_buy | buy | 3 | 15 | 40 | 0.85 | -0.397 | 30.0 | 30.0 | 70.0 | 0.0 | 57.5 | 0.0218 | ✗ |
| mc_a_yellow_x | buy | 2 | 30 | 44 | 0.93 | -0.41 | 29.5 | 29.5 | 70.5 | 0.0 | 61.4 | 0.0254 | ✗ |
| bias_bear | sell | 2 | 90 | 82 | 1.74 | -0.413 | 29.3 | 29.3 | 70.7 | 0.0 | 52.4 | -0.0179 | ✗ |
| mc_b_sell_circle | sell | 3 | 15 | 564 | 11.96 | -0.417 | 29.3 | 29.1 | 70.6 | 0.4 | 52.1 | -0.009 | ✗ |
| mc_a_red_diamond | sell | 4 | 30 | 2229 | 47.27 | -0.43 | 28.7 | 28.6 | 70.6 | 0.9 | 48.0 | -0.0183 | ✗ |
| mc_a_redx | sell | 2 | 30 | 985 | 20.89 | -0.463 | 27.5 | 27.4 | 71.5 | 1.1 | 47.2 | -0.0149 | ✗ |
| money_bag_top | sell | 2 | 30 | 49 | 1.04 | -0.488 | 26.5 | 26.5 | 73.5 | 0.0 | 44.9 | 0.0015 | ✗ |
| cvd_bear_flip | sell | 2 | 30 | 542 | 11.49 | -0.53 | 25.5 | 25.1 | 74.0 | 0.9 | 48.1 | -0.0336 | ✗ |
| mc_a_blood_diamond | sell | 5 | 30 | 272 | 5.77 | -0.538 | 25.0 | 25.0 | 73.9 | 1.1 | 43.0 | -0.0456 | ✗ |
| otter_sell | sell | 3 | 15 | 82 | 1.74 | -0.636 | 22.0 | 22.0 | 78.0 | 0.0 | 48.8 | -0.0381 | ✗ |
| water_sell_small | sell | 1 | 30 | 2 | 0.04 | -1.3 | 0.0 | 0.0 | 100.0 | 0.0 | 50.0 | 0.0255 | ✗ |

## TF = 15m

| Signal | Side | Wt | TTL | N | /day | mean_r | +R% | TP% | SL% | TO% | hit_60m% | mean_60m% | flag |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|
| water_buy_large | buy | 2 | 30 | 4 | 0.08 | 1.7 | 100.0 | 100.0 | 0.0 | 0.0 | 100.0 | 0.3619 | ★ |
| otter_buy | buy | 3 | 15 | 18 | 0.38 | 1.1 | 80.0 | 80.0 | 20.0 | 0.0 | 100.0 | 0.1943 | ★ |
| spoon_bear | sell | 2 | 30 | 93 | 1.97 | 0.676 | 65.4 | 65.4 | 34.6 | 0.0 | 92.3 | 0.3947 | ★ |
| bias_bull | buy | 2 | 90 | 44 | 0.93 | 0.367 | 55.6 | 55.6 | 44.4 | 0.0 | 55.6 | -0.2629 | ★ |
| money_bag_top | sell | 2 | 30 | 11 | 0.23 | 0.213 | 50.0 | 50.0 | 50.0 | 0.0 | 50.0 | 0.0417 | ★ |
| bias_bear | sell | 2 | 90 | 48 | 1.02 | 0.211 | 50.0 | 50.0 | 50.0 | 0.0 | 55.0 | 0.0867 | ★ |
| money_bag_bottom | buy | 2 | 30 | 2 | 0.04 | 0.2 | 50.0 | 50.0 | 50.0 | 0.0 | 50.0 | -0.0112 | ★ |
| spoon_bull | buy | 2 | 30 | 110 | 2.33 | 0.196 | 48.7 | 48.7 | 48.7 | 2.6 | 71.8 | 0.2327 | ★ |
| mc_b_buy_circle_div | buy | 4 | 45 | 136 | 2.88 | 0.158 | 48.6 | 48.6 | 51.4 | 0.0 | 65.7 | 0.1438 | ★ |
| mc_a_bluetriangle | buy | 3 | 90 | 381 | 8.08 | 0.073 | 45.4 | 45.4 | 53.7 | 0.9 | 57.9 | -0.0029 | ★ |
| mc_b_buy_circle | buy | 3 | 45 | 366 | 7.76 | 0.067 | 45.1 | 45.1 | 53.8 | 1.1 | 51.6 | 0.029 | ★ |
| mc_a_longema | buy | 2 | 90 | 138 | 2.93 | -0.076 | 40.4 | 40.4 | 59.6 | 0.0 | 44.7 | 0.0196 | ✗ |
| otter_sell | sell | 3 | 15 | 41 | 0.87 | -0.094 | 40.0 | 40.0 | 60.0 | 0.0 | 60.0 | 0.0361 | ✗ |
| cvd_bull_flip | buy | 2 | 30 | 173 | 3.67 | -0.129 | 38.8 | 38.8 | 61.2 | 0.0 | 50.0 | 0.007 | ✗ |
| mc_a_blood_diamond | sell | 5 | 90 | 190 | 4.03 | -0.349 | 31.6 | 31.6 | 68.4 | 0.0 | 45.6 | -0.0149 | ✗ |
| mc_b_sell_circle | sell | 3 | 45 | 391 | 8.29 | -0.38 | 30.4 | 30.4 | 69.6 | 0.0 | 52.0 | 0.008 | ✗ |
| mc_a_red_diamond | sell | 4 | 90 | 1525 | 32.34 | -0.386 | 30.2 | 30.0 | 69.1 | 0.9 | 50.1 | -0.0018 | ✗ |
| mc_a_redx | sell | 2 | 90 | 682 | 14.46 | -0.413 | 29.6 | 29.1 | 70.0 | 1.0 | 50.2 | -0.003 | ✗ |
| cvd_bear_flip | sell | 2 | 30 | 173 | 3.67 | -0.46 | 27.8 | 27.8 | 72.2 | 0.0 | 49.6 | 0.0029 | ✗ |
| mc_a_yellow_x | buy | 2 | 90 | 31 | 0.66 | -0.547 | 25.0 | 25.0 | 75.0 | 0.0 | 62.5 | 0.0419 | ✗ |
| mc_b_sell_circle_div | sell | 4 | 45 | 147 | 3.12 | -0.594 | 23.3 | 23.3 | 76.7 | 0.0 | 67.4 | 0.0371 | ✗ |
| mc_b_gold_buy | buy | 5 | 45 | 15 | 0.32 | -1.3 | 0.0 | 0.0 | 100.0 | 0.0 | 0.0 | -0.1023 | ✗ |
| water_sell_large | sell | 2 | 30 | 4 | 0.08 | -1.3 | 0.0 | 0.0 | 100.0 | 0.0 | 0.0 | -0.1257 | ✗ |

## TF = 30m

| Signal | Side | Wt | TTL | N | /day | mean_r | +R% | TP% | SL% | TO% | hit_60m% | mean_60m% | flag |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|
| water_sell_large | sell | 2 | 30 | 6 | 0.13 | 1.758 | 100.0 | 100.0 | 0.0 | 0.0 | 100.0 | 1.181 | ★ |
| spoon_bear | sell | 2 | 30 | 159 | 3.37 | 0.796 | 69.2 | 69.2 | 30.8 | 0.0 | 100.0 | 0.4759 | ★ |
| water_buy_large | buy | 2 | 30 | 17 | 0.36 | 0.7 | 66.7 | 66.7 | 33.3 | 0.0 | 100.0 | 0.3011 | ★ |
| spoon_bull | buy | 2 | 30 | 130 | 2.76 | 0.636 | 64.3 | 64.3 | 35.7 | 0.0 | 71.4 | 0.3128 | ★ |
| bias_bear | sell | 2 | 90 | 24 | 0.51 | 0.367 | 55.6 | 55.6 | 44.4 | 0.0 | 55.6 | 0.1808 | ★ |
| mc_a_bluetriangle | buy | 3 | 180 | 459 | 9.73 | 0.102 | 46.7 | 45.0 | 53.3 | 1.7 | 60.0 | 0.0639 | ★ |
| mc_b_sell_circle_div | sell | 4 | 90 | 190 | 4.03 | 0.083 | 45.8 | 45.8 | 54.2 | 0.0 | 70.8 | 0.1707 | ★ |
| mc_b_buy_circle | buy | 3 | 90 | 434 | 9.2 | 0.017 | 43.9 | 43.9 | 56.1 | 0.0 | 68.3 | 0.0671 | △ |
| mc_b_buy_circle_div | buy | 4 | 90 | 148 | 3.14 | -0.014 | 42.9 | 42.9 | 57.1 | 0.0 | 78.6 | 0.1758 | △ |
| cvd_bear_flip | sell | 2 | 30 | 83 | 1.76 | -0.128 | 38.9 | 38.9 | 61.1 | 0.0 | 42.6 | 0.0109 | ✗ |
| otter_sell | sell | 3 | 15 | 49 | 1.04 | -0.175 | 37.5 | 37.5 | 62.5 | 0.0 | 62.5 | 0.0584 | ✗ |
| cvd_bull_flip | buy | 2 | 30 | 82 | 1.74 | -0.274 | 34.0 | 34.0 | 66.0 | 0.0 | 39.6 | -0.0034 | ✗ |
| mc_a_longema | buy | 2 | 180 | 162 | 3.44 | -0.427 | 28.6 | 28.6 | 71.4 | 0.0 | 38.1 | 0.0108 | ✗ |
| mc_b_sell_circle | sell | 3 | 90 | 462 | 9.8 | -0.44 | 28.6 | 28.6 | 71.4 | 0.0 | 47.1 | -0.0505 | ✗ |
| mc_a_red_diamond | sell | 4 | 180 | 1813 | 38.45 | -0.474 | 27.0 | 27.0 | 71.7 | 1.3 | 46.5 | -0.0267 | ✗ |
| mc_a_redx | sell | 2 | 180 | 785 | 16.65 | -0.544 | 25.0 | 25.0 | 75.0 | 0.0 | 52.1 | -0.0043 | ✗ |
| money_bag_top | sell | 2 | 30 | 4 | 0.08 | -0.55 | 25.0 | 25.0 | 75.0 | 0.0 | 25.0 | 0.0371 | ✗ |
| mc_a_blood_diamond | sell | 5 | 180 | 209 | 4.43 | -0.567 | 24.0 | 24.0 | 76.0 | 0.0 | 56.0 | 0.0214 | ✗ |
| bias_bull | buy | 2 | 90 | 47 | 1.0 | -0.8 | 16.7 | 16.7 | 83.3 | 0.0 | 83.3 | 0.1253 | ✗ |
| mc_a_yellow_x | buy | 2 | 180 | 48 | 1.02 | -1.267 | 0.0 | 0.0 | 100.0 | 0.0 | 50.0 | -0.08 | ✗ |
| otter_buy | buy | 3 | 15 | 33 | 0.7 | -1.3 | 0.0 | 0.0 | 100.0 | 0.0 | 25.0 | -0.0846 | ✗ |
| mc_b_gold_buy | buy | 5 | 90 | 16 | 0.34 | -1.3 | 0.0 | 0.0 | 100.0 | 0.0 | 100.0 | 0.2926 | ✗ |

## Per-family aggregate (3m fires only — primary execution TF)

| Family | N (3m fires) | weighted mean_r |
|---|---:|---:|
| Otter precision | 442 | -0.214 |
| Cypher B | 1577 | -0.28 |
| CVD | 1084 | -0.289 |
| Bias / ribbon | 161 | -0.361 |
| Cypher A | 4292 | -0.378 |
| Otter trigger | 122 | -0.558 |

## Current weight vs measured edge (3m primary, sorted by gap)

Higher absolute gap = weight is mis-calibrated to measured edge. The gap column is `(measured_mean_r × 4) - current_weight` — i.e. a signal with mean_r=+0.5 'argues for' weight ~2 in a 0–5 scale; comparing to current weight surfaces miscalibration.

| Signal | Side | Wt | mean_r (3m) | implied_wt | gap |
|---|---|---:|---:|---:|---:|
| mc_a_blood_diamond | sell | 5 | -0.538 | 0 | -5 |
| mc_b_gold_buy | buy | 5 | -0.186 | 2 | -3 |
| mc_b_sell_circle_div | sell | 4 | -0.377 | 1 | -3 |
| mc_a_red_diamond | sell | 4 | -0.43 | 1 | -3 |
| otter_sell | sell | 3 | -0.636 | 0 | -3 |
| mc_b_buy_circle_div | buy | 4 | -0.139 | 2 | -2 |
| otter_buy | buy | 3 | -0.397 | 1 | -2 |
| mc_b_sell_circle | sell | 3 | -0.417 | 1 | -2 |
| cvd_bear_flip | sell | 2 | -0.53 | 0 | -2 |
| water_buy_large | buy | 2 | 0.1 | 3 | +1 |
| mc_a_bluetriangle | buy | 3 | -0.056 | 2 | -1 |
| mc_b_buy_circle | buy | 3 | -0.156 | 2 | -1 |
| spoon_bear | sell | 2 | -0.277 | 1 | -1 |
| bias_bull | buy | 2 | -0.308 | 1 | -1 |
| mc_a_yellow_x | buy | 2 | -0.41 | 1 | -1 |
| bias_bear | sell | 2 | -0.413 | 1 | -1 |
| mc_a_redx | sell | 2 | -0.463 | 1 | -1 |
| money_bag_top | sell | 2 | -0.488 | 1 | -1 |
| water_sell_small | sell | 1 | -1.3 | 0 | -1 |
| cvd_bull_flip | buy | 2 | -0.048 | 2 | +0 |
| mc_a_longema | buy | 2 | -0.069 | 2 | +0 |
| spoon_bull | buy | 2 | -0.095 | 2 | +0 |
| water_sell_large | sell | 2 | -0.183 | 2 | +0 |
| money_bag_bottom | buy | 2 | -0.203 | 2 | +0 |
| water_buy_small | buy | 1 | -0.3 | 1 | +0 |