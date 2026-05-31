# HTF proximity hard-zero — historical audit (Phase 2)

**Window:** 2026-05-16 → 2026-05-31 UTC | **N:** 157 rejections (155 proximity_to_support sell + 2 proximity_to_resistance buy)

## Convention

- All deltas are signed so that **positive = the block was wrong** (price moved in the rejected direction).
- `delta_30m_pct` / `delta_60m_pct` = (decision_price - close_at_+Nm) / decision_price × 100, with sign flipped for buy-rejections.
- `mfe_sell_pct` = max favorable excursion for a sell entry (i.e., how far price dropped below decision_price within 60min). For buy-rejections this is repurposed as MFE_buy.
- `mae_sell_pct` = max adverse excursion for a sell entry (i.e., how far price rose above decision_price within 60min).
- **block_wrong_60m_loose** = price moved ≥0.10% in the rejected direction by +60min close. **strong** = ≥0.30% (would have cleared the typical fee floor with margin).

## Overall — proximity_to_support sells (N=155)

- N with valid +60m close: **155**
- Mean Δ+30m: **0.0042%** (median -0.0086%)
- Mean Δ+60m: **0.0158%** (median -0.0051%)
- Mean MFE (sell win available): **0.2556%**
- Mean MAE (sell adverse): **0.2119%**
- **% block-wrong @ +60m, loose (≥0.10%):** 26.5%
- **% block-wrong @ +60m, strong (≥0.30%):** 9.0%

## By reason × regime × side × tier (matches Probe B)

| reason | regime | side | tier | N | Δ+30m | Δ+60m | MFE | MAE | wrong@60m loose | wrong@60m strong |
|---|---|---|---|---|---|---|---|---|---|---|
| proximity_to_support | NEUTRAL | sell | STANDARD | 94 | -0.0212% | -0.0072% | 0.2409% | 0.235% | 24.5% | 8.5% |
| proximity_to_support | NEUTRAL | sell | PREMIUM | 25 | 0.0441% | 0.0438% | 0.2317% | 0.1206% | 32.0% | 4.0% |
| proximity_to_support | BEAR | sell | STANDARD | 25 | 0.0359% | -0.0227% | 0.2535% | 0.2099% | 24.0% | 4.0% |
| proximity_to_support | BEAR | sell | PREMIUM | 11 | 0.058% | 0.2365% | 0.4399% | 0.2264% | 36.4% | 36.4% |
| proximity_to_resistance | NEUTRAL | buy | STANDARD | 2 | -0.0077% | -0.0934% | 0.2756% | 0.3072% | 50.0% | 0.0% |

## By distance bucket (proximity_to_support sells)

| dist bucket | N | Δ+30m | Δ+60m | MFE | MAE | wrong@60m loose | wrong@60m strong |
|---|---|---|---|---|---|---|---|
| <0.10% | 40 | 0.0151% | -0.0516% | 0.268% | 0.2696% | 20.0% | 7.5% |
| 0.10-0.20% | 39 | -0.0318% | 0.0396% | 0.2597% | 0.2421% | 25.6% | 15.4% |
| 0.20-0.30% | 76 | 0.0169% | 0.0391% | 0.247% | 0.166% | 30.3% | 6.6% |

## By regime (proximity_to_support sells, collapsing tier)

| regime | N | Δ+30m | Δ+60m | MFE | MAE | wrong@60m loose | wrong@60m strong |
|---|---|---|---|---|---|---|---|
| BEAR | 36 | 0.0427% | 0.0565% | 0.3105% | 0.2149% | 27.8% | 13.9% |
| NEUTRAL | 119 | -0.0075% | 0.0035% | 0.239% | 0.2109% | 26.1% | 7.6% |

## By trigger_signal (proximity_to_support sells)

| trigger | N | Δ+60m | MFE | MAE | wrong@60m loose |
|---|---|---|---|---|---|
| mc_a_red_diamond | 54 | 0.0126% | 0.2542% | 0.2072% | 29.6% |
| mc_a_redx | 31 | -0.0301% | 0.2424% | 0.243% | 19.4% |
| mc_b_buy_circle | 16 | -0.0682% | 0.1526% | 0.213% | 12.5% |
| cvd_bear_flip | 11 | 0.026% | 0.3152% | 0.1902% | 36.4% |
| mc_a_blood_diamond | 10 | 0.1182% | 0.2835% | 0.1035% | 30.0% |
| cvd_bull_flip | 8 | 0.3044% | 0.5253% | 0.1523% | 50.0% |
| mc_b_sell_circle_div | 6 | 0.029% | 0.2896% | 0.2095% | 16.7% |
| mc_b_sell_circle | 5 | 0.0738% | 0.2648% | 0.1954% | 40.0% |
| spoon_bear | 4 | -0.0302% | 0.1692% | 0.3162% | 25.0% |
| mc_a_bluetriangle | 3 | -0.2488% | 0.0426% | 0.5473% | 0.0% |
| mc_a_longema | 2 | -0.0644% | 0.0606% | 0.2494% | 0.0% |
| mc_b_buy_circle_div | 2 | 0.1105% | 0.3395% | 0.1212% | 50.0% |
| spoon_bull | 2 | -0.055% | 0.2528% | 0.1831% | 0.0% |
| otter_sell | 1 | 0.2356% | 0.2593% | 0.1156% | 100.0% |

## Resistance rejections (buys, N=2)

Total: 2. Too few to analyze; row dump:

- 2026-05-18T01:57:42+00:00 NEUTRAL buy STANDARD dist_resist=0.102474091763155% Δ+60m=-0.36064319026522845%
- 2026-05-31T00:28:25+00:00 NEUTRAL buy STANDARD dist_resist=0.0464908214414509% Δ+60m=0.1739165714363072%

## Data quality

- Rows with no decision bar: 0
- Rows with no +60m close: 0
