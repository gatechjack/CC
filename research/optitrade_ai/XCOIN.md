# OptiTrade AI -- pre-registered cross-coin falsification (1h)

**One config, fixed a priori, zero selection freedom:** emission-clock continuation, Normal preset, MACD off, SL=2.5*ATR(14), RR=3.5, SL-first, 1h. Selected originally on ETH 1h; run unchanged on BTC/SOL/XRP (never touched) + ETH restated. Both venues. 5 equal windows, WARMUP=400. GROSS + net06/net04 (0.06%/0.04% per side). p = drift-controlled magnitude null (200 perms, per-window direction counts preserved, times shuffled). **Counts only. No optimization.**

## Rollup -- all 8 cells (4 coins x 2 venues)

| coin | venue | n | gross | net06 | net04 | net06+/5 | LONG net06 | SHORT net06 | p_overall |
|---|---|--:|--:|--:|--:|--:|--:|--:|--:|
| BTC | Binance | 316 | +26.7 | +2.5 | +10.6 | 2/5 | +2.6 | -0.1 | 0.260 |
| BTC | Bybit | 139 | +16.9 | +6.6 | +10.0 | 2/5 | -0.1 | +6.7 | 0.340 |
| ETH | Binance | 352 | +43.8 | +24.1 | +30.7 | 3/5 | +8.1 | +16.0 | 0.035 |
| ETH | Bybit | 202 | +53.3 | +43.0 | +46.4 | 5/5 | +12.0 | +30.9 | 0.005 |
| SOL | Binance | 335 | -4.9 | -17.3 | -13.1 | 1/5 | -8.7 | -8.5 | 0.660 |
| SOL | Bybit | 221 | -5.3 | -13.8 | -10.9 | 1/5 | -16.9 | +3.1 | 0.640 |
| XRP | Binance | 330 | +4.9 | -10.9 | -5.6 | 2/5 | -12.1 | +1.3 | 0.620 |
| XRP | Bybit | 203 | +11.8 | +1.9 | +5.2 | 3/5 | -7.0 | +8.9 | 0.575 |

## Per-window net06 (sumR per window)

| coin | venue | w0 | w1 | w2 | w3 | w4 |
|---|---|--:|--:|--:|--:|--:|
| BTC | Binance | +9.2 | -11.9 | +13.2 | -7.5 | -0.5 |
| BTC | Bybit | +12.4 | -3.3 | -2.9 | +4.7 | -4.3 |
| ETH | Binance | -7.9 | -14.4 | +14.3 | +6.0 | +26.1 |
| ETH | Bybit | +10.7 | +6.1 | +3.1 | +2.3 | +20.8 |
| SOL | Binance | -7.2 | +5.2 | -6.4 | -4.5 | -4.4 |
| SOL | Bybit | -2.3 | -3.4 | -5.3 | -4.0 | +1.2 |
| XRP | Binance | -14.5 | -8.8 | +11.2 | +2.1 | -0.9 |
| XRP | Bybit | +2.9 | +2.1 | -5.3 | +2.5 | -0.3 |

_Full per-window n/gross/net06/net04 in `xcoin_results.csv`. Counts only._

## Reproduce
`python run_xcoin.py` -> xcoin_results.csv + this file.
