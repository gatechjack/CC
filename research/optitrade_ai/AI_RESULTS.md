# OptiTrade AI -- signal-transplant probe (entries into optitrade_bt bracket)

## Vendor-methodology note (source-pending line refs)

The vendor's own backtest/label layer is NOT reproduced here, by design. Per the decoded logic it **repaints** -- entry markers are computed from ribbon-stack and crossover conditions that are only final at bar close yet are drawn on the forming bar, and its reported "wins" are labelled by comparing a later bar's **close to the signal bar's close** (a directional close-vs-close check), not by simulating a stop-loss / take-profit bracket with intrabar fills. That inflates apparent hit-rate (no stop can be hit between signal and evaluation, and favourable intrabar excursions are ignored) and is non-actionable. **NOTE:** exact line references are pending -- the vendor OptiTrade AI Pine source was not located on the box (searched the pine folder, Downloads, Desktop, Documents, paste-cache; only the in-house 5-EMA `seed1_ribbon_smabias.pine` was present). Provide the source file and I will cite precise lines. Here we transplant only the ENTRY signals into the honest bracket.

## Protocol

Binance perp corpus. Cells = 4 coins x {15m,1h,4h} (3m fee-dead, 1d undersampled per the TP-SL study). Bracket: SL-first, SL=2.5*ATR(14), 4 scaled TP rungs each closing 1/4, RR in {1.5,2.5,3.5}. 8 signal variants x 3 RR = 24 configs/cell (no optimization). Warmup=400 bars; post-warmup history tiled into 5 equal contiguous windows. GROSS primary; net06/net04 = 0.06%/0.04% taker per side (both sides), in R. Best config per cell = highest TOTAL OOS net06 across the 5 windows (fee-aware; gross shown alongside).

## Best config per cell (by total net06) + windows-positive counts

| cell | best config | gross+ | net06+ | net04+ | win n>=30 | tot n | tot gross | tot net06 | tot net04 |
|---|---|--:|--:|--:|--:|--:|--:|--:|--:|
| BTCUSDT 15m | VeryHigh/reversal/macd1/RR3.5 | 3/5 | 1/5 | 1/5 | 5/5 | 248 | +11.2 | -33.4 | -18.6 |
| BTCUSDT 1h | VeryHigh/reversal/macd1/RR3.5 | 4/5 | 4/5 | 4/5 | 0/5 | 49 | +21.4 | +17.6 | +18.9 |
| BTCUSDT 4h | VeryHigh/reversal/macd0/RR3.5 | 5/5 | 4/5 | 4/5 | 0/5 | 27 | +19.9 | +18.8 | +19.2 |
| ETHUSDT 15m | VeryHigh/reversal/macd1/RR2.5 | 4/5 | 0/5 | 2/5 | 5/5 | 239 | +16.3 | -15.6 | -5.0 |
| ETHUSDT 1h | Normal/continuation/macd0/RR3.5 | 3/5 | 3/5 | 3/5 | 5/5 | 352 | +43.8 | +24.1 | +30.7 |
| ETHUSDT 4h | VeryHigh/continuation/macd0/RR1.5 | 4/5 | 4/5 | 4/5 | 0/5 | 121 | +13.8 | +10.4 | +11.6 |
| SOLUSDT 15m | Normal/reversal/macd0/RR3.5 | 5/5 | 4/5 | 5/5 | 5/5 | 874 | +102.5 | +29.5 | +53.8 |
| SOLUSDT 1h | VeryHigh/reversal/macd0/RR3.5 | 5/5 | 5/5 | 5/5 | 3/5 | 150 | +38.6 | +32.4 | +34.4 |
| SOLUSDT 4h | Normal/reversal/macd1/RR3.5 | 4/5 | 4/5 | 4/5 | 0/5 | 27 | +13.8 | +13.2 | +13.4 |
| XRPUSDT 15m | Normal/continuation/macd1/RR3.5 | 5/5 | 2/5 | 3/5 | 5/5 | 1176 | +96.8 | -28.1 | +13.6 |
| XRPUSDT 1h | Normal/reversal/macd1/RR3.5 | 4/5 | 4/5 | 4/5 | 2/5 | 144 | +40.6 | +33.0 | +35.5 |
| XRPUSDT 4h | VeryHigh/reversal/macd1/RR1.5 | 2/5 | 2/5 | 2/5 | 0/5 | 22 | -0.1 | -0.7 | -0.5 |

> **Selection caveat:** the 'best config' is the max-net06 of 24 configs chosen on the *same* data shown -- an in-sample pick across 288 config-cells, so these rows are optimistic and some will look good by chance. The unbiased read is the signal-family rollup below (no per-cell cherry-pick), where **every family is net06-negative in aggregate**. Treat per-cell winners as leads, weighted by their `win n>=30` column.


## Per-cell best-config, per-window detail

**BTCUSDT 15m -- VeryHigh/reversal/macd1/RR3.5**

| window | n | gross | net06 | net04 | flag |
|--:|--:|--:|--:|--:|---|
| 0 | 41 | -4.59 | -11.18 | -8.98 |  |
| 1 | 47 | +3.94 | -7.11 | -3.43 |  |
| 2 | 44 | -5.56 | -11.49 | -9.51 |  |
| 3 | 62 | +2.19 | -8.72 | -5.08 |  |
| 4 | 54 | +15.22 | +5.06 | +8.45 |  |

**BTCUSDT 1h -- VeryHigh/reversal/macd1/RR3.5**

| window | n | gross | net06 | net04 | flag |
|--:|--:|--:|--:|--:|---|
| 0 | 8 | +4.81 | +4.16 | +4.38 | n<30 |
| 1 | 10 | +8.5 | +7.66 | +7.94 | n<30 |
| 2 | 10 | +6.91 | +6.25 | +6.47 | n<30 |
| 3 | 10 | -1.06 | -1.78 | -1.54 | n<30 |
| 4 | 11 | +2.22 | +1.36 | +1.64 | n<30 |

**BTCUSDT 4h -- VeryHigh/reversal/macd0/RR3.5**

| window | n | gross | net06 | net04 | flag |
|--:|--:|--:|--:|--:|---|
| 0 | 5 | +3.91 | +3.72 | +3.78 | n<30 |
| 1 | 2 | +3.25 | +3.17 | +3.2 | n<30 |
| 2 | 8 | +0.03 | -0.2 | -0.13 | n<30 |
| 3 | 5 | +5.5 | +5.31 | +5.37 | n<30 |
| 4 | 7 | +7.16 | +6.85 | +6.95 | n<30 |

**ETHUSDT 15m -- VeryHigh/reversal/macd1/RR2.5**

| window | n | gross | net06 | net04 | flag |
|--:|--:|--:|--:|--:|---|
| 0 | 36 | -0.12 | -5.35 | -3.61 |  |
| 1 | 54 | +8.59 | -1.51 | +1.86 |  |
| 2 | 48 | +4.09 | -1.48 | +0.38 |  |
| 3 | 63 | +2.94 | -3.47 | -1.34 |  |
| 4 | 38 | +0.78 | -3.78 | -2.26 |  |

**ETHUSDT 1h -- Normal/continuation/macd0/RR3.5**

| window | n | gross | net06 | net04 | flag |
|--:|--:|--:|--:|--:|---|
| 0 | 75 | -3.97 | -7.86 | -6.56 |  |
| 1 | 79 | -8.56 | -14.45 | -12.48 |  |
| 2 | 71 | +18.12 | +14.29 | +15.57 |  |
| 3 | 52 | +8.22 | +6.05 | +6.77 |  |
| 4 | 75 | +30.01 | +26.07 | +27.39 |  |

**ETHUSDT 4h -- VeryHigh/continuation/macd0/RR1.5**

| window | n | gross | net06 | net04 | flag |
|--:|--:|--:|--:|--:|---|
| 0 | 27 | -2.28 | -3.03 | -2.78 | n<30 |
| 1 | 21 | +6.97 | +6.16 | +6.43 | n<30 |
| 2 | 26 | +3.19 | +2.54 | +2.75 | n<30 |
| 3 | 19 | +3.34 | +2.88 | +3.04 | n<30 |
| 4 | 28 | +2.62 | +1.85 | +2.11 | n<30 |

**SOLUSDT 15m -- Normal/reversal/macd0/RR3.5**

| window | n | gross | net06 | net04 | flag |
|--:|--:|--:|--:|--:|---|
| 0 | 168 | +18.56 | +6.07 | +10.24 |  |
| 1 | 180 | +18.16 | +2.37 | +7.63 |  |
| 2 | 169 | +26.09 | +12.98 | +17.35 |  |
| 3 | 190 | +28.78 | +13.15 | +18.36 |  |
| 4 | 167 | +10.91 | -5.06 | +0.26 |  |

**SOLUSDT 1h -- VeryHigh/reversal/macd0/RR3.5**

| window | n | gross | net06 | net04 | flag |
|--:|--:|--:|--:|--:|---|
| 0 | 35 | +1.88 | +0.56 | +1.0 |  |
| 1 | 26 | +4.16 | +3.15 | +3.48 | n<30 |
| 2 | 34 | +10.12 | +8.76 | +9.21 |  |
| 3 | 24 | +15.91 | +14.87 | +15.22 | n<30 |
| 4 | 31 | +6.5 | +5.04 | +5.53 |  |

**SOLUSDT 4h -- Normal/reversal/macd1/RR3.5**

| window | n | gross | net06 | net04 | flag |
|--:|--:|--:|--:|--:|---|
| 0 | 2 | -0.84 | -0.86 | -0.86 | n<30 |
| 1 | 5 | +2.31 | +2.22 | +2.25 | n<30 |
| 2 | 8 | +5.25 | +5.09 | +5.14 | n<30 |
| 3 | 5 | +1.84 | +1.75 | +1.78 | n<30 |
| 4 | 7 | +5.19 | +5.03 | +5.08 | n<30 |

**XRPUSDT 15m -- Normal/continuation/macd1/RR3.5**

| window | n | gross | net06 | net04 | flag |
|--:|--:|--:|--:|--:|---|
| 0 | 240 | +7.19 | -15.38 | -7.86 |  |
| 1 | 204 | +28.72 | +4.56 | +12.62 |  |
| 2 | 257 | +30.81 | +0.83 | +10.82 |  |
| 3 | 241 | +16.88 | -4.65 | +2.52 |  |
| 4 | 234 | +13.22 | -13.41 | -4.54 |  |

**XRPUSDT 1h -- Normal/reversal/macd1/RR3.5**

| window | n | gross | net06 | net04 | flag |
|--:|--:|--:|--:|--:|---|
| 0 | 34 | -8.56 | -10.3 | -9.72 |  |
| 1 | 27 | +10.69 | +9.1 | +9.63 | n<30 |
| 2 | 27 | +5.69 | +4.36 | +4.8 | n<30 |
| 3 | 23 | +20.38 | +19.34 | +19.68 | n<30 |
| 4 | 33 | +12.44 | +10.46 | +11.12 |  |

**XRPUSDT 4h -- VeryHigh/reversal/macd1/RR1.5**

| window | n | gross | net06 | net04 | flag |
|--:|--:|--:|--:|--:|---|
| 0 | 8 | -0.19 | -0.37 | -0.31 | n<30 |
| 1 | 5 | +2.75 | +2.63 | +2.67 | n<30 |
| 2 | 2 | -0.06 | -0.1 | -0.09 | n<30 |
| 3 | 5 | -2.91 | -3.02 | -2.98 | n<30 |
| 4 | 2 | +0.28 | +0.21 | +0.23 | n<30 |

## Signal-family rollup (totals across all 12 cells, summed over RR & windows)

| preset | mode | macd | tot n | tot gross | tot net06 | tot net04 | cells net06+ |
|---|---|--:|--:|--:|--:|--:|--:|
| Normal | continuation | 0 | 27452 | +611.5 | -2295.1 | -1326.2 | 1/12 |
| Normal | continuation | 1 | 23639 | +445.5 | -2143.1 | -1280.2 | 2/12 |
| Normal | reversal | 0 | 15888 | +454.2 | -1326.1 | -732.6 | 4/12 |
| Normal | reversal | 1 | 9552 | +596.5 | -539.1 | -160.6 | 5/12 |
| VeryHigh | continuation | 0 | 23386 | +516.9 | -1947.5 | -1126.0 | 2/12 |
| VeryHigh | continuation | 1 | 20287 | +508.0 | -1773.6 | -1013.1 | 3/12 |
| VeryHigh | reversal | 0 | 9766 | +369.4 | -697.0 | -341.5 | 8/12 |
| VeryHigh | reversal | 1 | 3644 | +179.7 | -231.9 | -94.7 | 6/12 |

_Counts/verdicts intentionally minimal -- evidence only. Full 24-config x 5-window detail per cell is in `ai_results.csv`._

## Reproduce
`python run_ai_transplant.py` -> ai_results.csv + this file.
