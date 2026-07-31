# STUDY C -- regime attribution + gating (frozen STUDY B config, counts only)

**PRE-REGISTERED HYPOTHESIS:** net R concentrates in TREND regimes (RD non-range; micro trend_up/down; macro60 bull/bear), flat-to-negative in range/neutral; long R in up-regimes, short R in down-regimes. Binance 1h, 4 coins (ETH incl). GROSS primary (net06 shown); existing classifiers only; no parameter/threshold changes. RD & ps_trail30 causal; micro & macro60 use the SFP-harness entry-bucket/day convention (marginally forward at entry).

## Part 1 -- ATTRIBUTION (pooled; per-coin in study_c_attribution.csv)

### RD state

| bucket | n | gross | net06 | avgR |
|---|--:|--:|--:|--:|
| RD_up | 434 | +46.0 | +27.3 | +0.106 |
| RD_range | 73 | +39.0 | +33.9 | +0.534 |
| RD_down | 362 | +30.9 | +14.8 | +0.085 |

### micro_regime direction

| bucket | n | gross | net06 | avgR |
|---|--:|--:|--:|--:|
| trend_up | 294 | +66.0 | +52.4 | +0.224 |
| trend_down | 355 | +41.9 | +26.7 | +0.118 |
| range | 154 | +18.0 | +10.0 | +0.117 |
| ambiguous | 66 | -10.0 | -13.0 | -0.152 |
| warmup | 0 | +0.0 | +0.0 | +0.000 |

### micro_regime vol_state

| bucket | n | gross | net06 | avgR |
|---|--:|--:|--:|--:|
| low | 60 | +8.0 | +4.9 | +0.133 |
| normal | 589 | +55.9 | +29.0 | +0.095 |
| high | 220 | +52.0 | +42.2 | +0.236 |
| warmup | 0 | +0.0 | +0.0 | +0.000 |

### macro60 regime

| bucket | n | gross | net06 | avgR |
|---|--:|--:|--:|--:|
| bull | 415 | +57.0 | +37.4 | +0.137 |
| bear | 450 | +58.0 | +38.0 | +0.129 |
| neutral | 4 | +0.9 | +0.7 | +0.220 |

### Directional cross-tab (side x regime-direction) -- pooled net06 / avgR / n

| classifier | long in UP | long in DOWN | short in UP | short in DOWN |
|---|---|---|---|---|
| RD | +22.5/+0.13/n273 | -16.2/-0.12/n100 | +4.9/+0.07/n161 | +31.0/+0.16/n262 |
| micro | +50.5/+0.22/n292 | -2.1/-1.00/n2 | +1.9/+1.00/n2 | +28.8/+0.12/n353 |
| macro60 | +130.4/+0.73/n192 | -105.3/-0.43/n223 | -93.0/-0.37/n223 | +143.2/+0.67/n227 |

## Part 2 -- GATED RE-RUN (ablation, one gate at a time; null on GROSS R, 200x, pinned)

_Own-bucket null is stratified by (side, macro60); therefore the macro60 gate (c) is DEGENERATE against this null (it resamples its own strata -> pctl ~50 regardless), and its armed avgR (+0.70..+1.00) reflects the NON-CAUSAL entry-day macro60 label (peeks at the day's own direction). Treat gate (c) as a contaminated reference, not a deployable gate. Gates (a) RD and (d) ps_trail30 are causal; (b) micro uses the entry 15m bucket._

| gate | coin | armed n | armed avgR | armed sumR | armed net06 | blocked avgR | null p5/p50/p95 | pctl | flag |
|---|---|--:|--:|--:|--:|--:|---|--:|---|
| a_RD_nonrange | SOL | 199 | +0.166 | +33.0 | +26.8 | +0.200 | -3.0/+37.0/+77.0 | 38% |  |
| a_RD_nonrange | BTC | 207 | +0.198 | +41.0 | +28.5 | +1.154 | +5.0/+49.0/+97.0 | 36% |  |
| a_RD_nonrange | ETH | 222 | -0.077 | -17.1 | -26.9 | +0.429 | -45.1/-9.1/+30.0 | 36% |  |
| a_RD_nonrange | XRP | 168 | +0.119 | +20.0 | +13.7 | +0.455 | -8.0/+28.0/+64.0 | 33% |  |
| a_RD_nonrange | POOLED | 796 | +0.097 | +76.9 | +42.2 | +0.534 | +20.9/+104.0/+176.9 | 27% |  |

| b_micro_aligned | SOL | 164 | +0.244 | +40.0 | +35.0 | -0.111 | -4.0/+32.0/+60.0 | 61% |  |
| b_micro_aligned | BTC | 164 | +0.220 | +36.0 | +25.7 | +0.357 | +12.0/+48.0/+76.0 | 28% |  |
| b_micro_aligned | ETH | 188 | +0.047 | +8.9 | +0.4 | -0.226 | -35.1/-2.2/+32.9 | 65% |  |
| b_micro_aligned | XRP | 129 | +0.178 | +23.0 | +18.1 | +0.115 | -13.0/+19.0/+51.0 | 54% |  |
| b_micro_aligned | POOLED | 645 | +0.167 | +107.9 | +79.3 | +0.036 | +8.8/+99.9/+163.0 | 58% |  |

| c_macro60_aligned | SOL | 92 | +1.000 | +92.0 | +89.2 | -0.487 | +60.0/+92.0/+120.0 | 45% |  |
| c_macro60_aligned | BTC | 106 | +0.811 | +86.0 | +79.3 | -0.263 | +50.0/+90.0/+114.0 | 45% |  |
| c_macro60_aligned | ETH | 122 | +0.475 | +58.0 | +52.2 | -0.493 | +22.0/+58.0/+94.0 | 47% |  |
| c_macro60_aligned | XRP | 99 | +0.576 | +57.0 | +52.9 | -0.297 | +25.0/+61.0/+89.0 | 44% |  |
| c_macro60_aligned | POOLED | 419 | +0.699 | +293.0 | +273.6 | -0.394 | +233.0/+297.0/+361.0 | 43% |  |

| d_ps_trail30_aligned | SOL | 97 | -0.010 | -1.0 | -4.3 | +0.321 | -9.0/+19.0/+47.0 | 10% |  |
| d_ps_trail30_aligned | BTC | 106 | +0.094 | +10.0 | +3.2 | +0.404 | +2.0/+34.0/+66.0 | 10% |  |
| d_ps_trail30_aligned | ETH | 100 | +0.249 | +24.9 | +20.1 | -0.200 | -15.1/+16.9/+40.9 | 70% |  |
| d_ps_trail30_aligned | XRP | 91 | +0.231 | +21.0 | +17.3 | +0.091 | -3.0/+21.0/+49.0 | 42% |  |
| d_ps_trail30_aligned | POOLED | 394 | +0.139 | +54.9 | +36.3 | +0.128 | +38.9/+98.0/+150.9 | 14% |  |

## Part 3 -- HONESTY PANEL: best-armed gate (by pooled null percentile) vs ungated, per-window

'Best' = highest pooled drift-null percentile (significance-aware, not raw net06 -- raw net06 would pick the contaminated macro60 gate). Best = **b_micro_aligned** (pooled null pctl 58%, pooled armed net06 +79.3). For reference the pooled net06/pctl by gate: a_RD_nonrange=+42/27%, b_micro_aligned=+79/58%, c_macro60_aligned=+274/43%, d_ps_trail30_aligned=+36/14%.

| window | ungated n | ungated net06 | armed n | armed net06 | flag |
|--:|--:|--:|--:|--:|---|
| 0 | 156 | +25.7 | 121 | +26.3 |  |
| 1 | 169 | -6.1 | 127 | -5.4 |  |
| 2 | 156 | +45.4 | 116 | +39.2 |  |
| 3 | 201 | +18.5 | 146 | +12.0 |  |
| 4 | 187 | -7.3 | 135 | +7.1 |  |

_w4 = most recent window. Counts only; no verdicts.

