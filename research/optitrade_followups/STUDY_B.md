# STUDY B -- wide-stop trend-cross, SOL-anchored (counts only)

**Pre-registration:** signal = OptiTrade study-A EMA-cross (L=30 -> EMA30/EMA66) + RSI(14) bias 5, minSep 6, entry at signal close, one position at a time. Geometry REPLACED with a single wide stop 3.0*ATR(14) + single TP at 3R (no rungs, no management), SL-first. **SOL 1h = selection coin; BTC/ETH/XRP 1h = pre-registered falsification.** ONE fixed config, NO optimization. 5 equal windows over post-warmup history (all out-of-sample). Binance-perp. GROSS primary; net06/net04 = 0.06%/0.04% per side. p = drift-controlled permutation (200 perms, per-window direction counts preserved, times shuffled).

## Rollup (per coin)

| coin | role | n | gross | net06 | net04 | drift p |
|---|---|--:|--:|--:|--:|--:|
| SOL | SELECTION | 209 | +35.0 | +28.4 | +30.6 | 0.085 |
| BTC | travel | 220 | +56.0 | +42.2 | +46.8 | 0.065 |
| ETH | travel | 250 | -5.1 | -16.8 | -12.9 | 0.635 |
| XRP | travel | 190 | +30.0 | +22.2 | +24.8 | 0.090 |

## Per-window (5 windows): n / gross / net06 / net04

**SOL (SELECTION)**

| window | n | gross | net06 | net04 |
|--:|--:|--:|--:|--:|
| 0 | 30 | +14.0 | +13.32 | +13.54 |
| 1 | 28 | +4.0 | +3.19 | +3.46 |
| 2 | 45 | +19.0 | +17.63 | +18.09 |
| 3 | 53 | +7.0 | +5.3 | +5.87 |
| 4 | 53 | -9.0 | -11.06 | -10.37 |

**BTC (travel)**

| window | n | gross | net06 | net04 |
|--:|--:|--:|--:|--:|
| 0 | 41 | +15.0 | +12.6 | +13.4 |
| 1 | 44 | -4.0 | -6.63 | -5.76 |
| 2 | 34 | +30.0 | +28.12 | +28.74 |
| 3 | 59 | +1.0 | -2.86 | -1.57 |
| 4 | 42 | +14.0 | +10.98 | +11.99 |

**ETH (travel)**

| window | n | gross | net06 | net04 |
|--:|--:|--:|--:|--:|
| 0 | 49 | +3.0 | +0.97 | +1.65 |
| 1 | 53 | +7.0 | +3.47 | +4.65 |
| 2 | 48 | +4.0 | +1.77 | +2.51 |
| 3 | 56 | -4.0 | -6.02 | -5.35 |
| 4 | 44 | -15.12 | -16.94 | -16.33 |

**XRP (travel)**

| window | n | gross | net06 | net04 |
|--:|--:|--:|--:|--:|
| 0 | 36 | +0.0 | -1.22 | -0.81 |
| 1 | 44 | -4.0 | -6.12 | -5.42 |
| 2 | 29 | -1.0 | -2.15 | -1.77 |
| 3 | 33 | +23.0 | +22.04 | +22.36 |
| 4 | 48 | +12.0 | +9.7 | +10.46 |

_Counts only, no verdicts. Full per-window in study_b_results.csv._

## Reproduce
`python study_b_widestop.py` -> study_b_results.csv + this file.
