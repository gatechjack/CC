# STUDY B deepening (items 1-3) -- same frozen config, counts only

Wide-stop trend-cross: EMA30/66 + RSI bias5 minSep6 signal, single 3.0*ATR stop + single 3R TP, SL-first, 1h. 5 equal windows over post-warmup history. GROSS + net06/net04 (0.06%/0.04% per side). Per-coin drift p = 200-perm within-window direction-preserving shuffle. NO optimization; ETH retained (no post-hoc exclusion).

## 1. Binance per-window (all 4 coins)

### Per-window n / gross / net06 -- Binance 1h (window 4 = most recent)

| coin | metric | w0 | w1 | w2 | w3 | w4 | total |
|---|---|--:|--:|--:|--:|--:|--:|
| SOL | n | 30 | 28 | 45 | 53 | 53 | 209 |
| SOL | gross | +14.0 | +4.0 | +19.0 | +7.0 | -9.0 | +35.0 |
| SOL | net06 | +13.3 | +3.2 | +17.6 | +5.3 | -11.1 | +28.4 |
| BTC | n | 41 | 44 | 34 | 59 | 42 | 220 |
| BTC | gross | +15.0 | -4.0 | +30.0 | +1.0 | +14.0 | +56.0 |
| BTC | net06 | +12.6 | -6.6 | +28.1 | -2.9 | +11.0 | +42.2 |
| ETH | n | 49 | 53 | 48 | 56 | 44 | 250 |
| ETH | gross | +3.0 | +7.0 | +4.0 | -4.0 | -15.1 | -5.1 |
| ETH | net06 | +1.0 | +3.5 | +1.8 | -6.0 | -16.9 | -16.8 |
| XRP | n | 36 | 44 | 29 | 33 | 48 | 190 |
| XRP | gross | +0.0 | -4.0 | -1.0 | +23.0 | +12.0 | +30.0 |
| XRP | net06 | -1.2 | -6.1 | -2.1 | +22.0 | +9.7 | +22.2 |

## 2. Pooled significance -- pre-registered ALL FOUR coins (ETH included)

| venue | pooled net06 | drift p (pooled) |
|---|--:|--:|
| Binance | +76.1 | 0.035 |
| Bybit | +43.5 | 0.135 |

## 3. Bybit cross-venue replay (1h, all 4 coins)

| coin | n | gross | net06 | net04 | drift p |
|---|--:|--:|--:|--:|--:|
| SOL | 163 | +1.0 | -4.5 | -2.6 | 0.495 |
| BTC | 123 | +34.0 | +25.8 | +28.5 | 0.120 |
| ETH | 152 | -8.0 | -14.2 | -12.2 | 0.810 |
| XRP | 115 | +41.0 | +36.3 | +37.9 | 0.045 |

### Bybit per-window

### Per-window n / gross / net06 -- Bybit 1h (window 4 = most recent)

| coin | metric | w0 | w1 | w2 | w3 | w4 | total |
|---|---|--:|--:|--:|--:|--:|--:|
| SOL | n | 31 | 31 | 36 | 30 | 35 | 163 |
| SOL | gross | -3.0 | +5.0 | +4.0 | +6.0 | -11.0 | +1.0 |
| SOL | net06 | -3.9 | +4.1 | +2.9 | +5.0 | -12.6 | -4.5 |
| BTC | n | 24 | 31 | 28 | 17 | 23 | 123 |
| BTC | gross | +8.0 | +13.0 | +0.0 | +11.0 | +1.9 | +34.0 |
| BTC | net06 | +6.7 | +11.2 | -2.2 | +9.8 | +0.4 | +25.8 |
| ETH | n | 28 | 35 | 39 | 25 | 25 | 152 |
| ETH | gross | +8.0 | +1.0 | +5.0 | -9.0 | -13.0 | -8.0 |
| ETH | net06 | +6.7 | -0.6 | +3.6 | -9.9 | -14.1 | -14.2 |
| XRP | n | 20 | 21 | 18 | 19 | 37 | 115 |
| XRP | gross | -4.0 | +7.0 | +18.0 | +9.0 | +11.0 | +41.0 |
| XRP | net06 | -4.8 | +6.3 | +17.4 | +8.3 | +9.1 | +36.3 |

_Counts only. Full per-window in study_b_deep_results.csv._

## Reproduce
`python study_b_deep.py` -> study_b_deep_results.csv + this file.
