# OptiTrade AI item 3 -- ETH 1h Normal/continuation/RR3.5: drift-control + long/short split

Two signal sets side by side: **vendor** (spec-exact `barssince(buy2[1])>30`, clock on every fresh event) and **emission** (clock on emission -- the looser set we own). Bracket SL=2.5*ATR, RR3.5, sl-first, 5 equal windows, WARMUP=400. GROSS shown; net06 = 0.06%/side both sides. Null = matched random-direction (per window keep observed #long & #short, random times, same one-position bracket, 200 draws). `pctile` = share of 200 draws with net06 BELOW observed (higher = observed above random). `p_overall` = P(null total net06 >= observed). Counts only.

## Binance -- vendor spacing

| window | L n | L gross | L net06 | S n | S gross | S net06 |
|--:|--:|--:|--:|--:|--:|--:|
| 0 | 39 | +4.72 | +2.51 | 33 | -5.94 | -7.49 |
| 1 | 35 | -8.44 | -11.08 | 38 | -3.22 | -5.99 |
| 2 | 36 | +7.66 | +5.66 | 32 | +1.81 | +0.02 |
| 3 | 23 | +1.53 | +0.49 | 26 | +5.34 | +4.34 |
| 4 | 35 | +6.56 | +4.65 | 38 | +18.39 | +16.4 |

_Totals: **LONG** n=168 net06=+2.2 (null median -9.3, observed pctile **0.79**) | **SHORT** n=167 net06=+7.3 (null median -7.5, observed pctile **0.84**) | **OVERALL** net06=+9.5 (pctile 0.88, **p_overall=0.125**)._

## Binance -- emission spacing

| window | L n | L gross | L net06 | S n | S gross | S net06 |
|--:|--:|--:|--:|--:|--:|--:|
| 0 | 41 | +2.03 | -0.27 | 34 | -6.0 | -7.59 |
| 1 | 39 | -6.06 | -9.02 | 40 | -2.5 | -5.43 |
| 2 | 37 | +11.47 | +9.47 | 34 | +6.66 | +4.81 |
| 3 | 25 | +3.88 | +2.73 | 27 | +4.34 | +3.32 |
| 4 | 36 | +7.19 | +5.17 | 39 | +22.82 | +20.91 |

_Totals: **LONG** n=178 net06=+8.1 (null median -9.1, observed pctile **0.88**) | **SHORT** n=174 net06=+16.0 (null median -6.2, observed pctile **0.96**) | **OVERALL** net06=+24.1 (pctile 0.97, **p_overall=0.030**)._

## Bybit -- vendor spacing

| window | L n | L gross | L net06 | S n | S gross | S net06 |
|--:|--:|--:|--:|--:|--:|--:|
| 0 | 21 | +10.28 | +9.11 | 22 | +2.91 | +1.56 |
| 1 | 21 | -0.59 | -1.8 | 18 | +0.78 | -0.17 |
| 2 | 15 | -1.91 | -2.53 | 19 | +5.22 | +4.5 |
| 3 | 22 | -1.44 | -2.54 | 18 | +4.66 | +3.76 |
| 4 | 17 | +5.38 | +4.42 | 23 | +12.54 | +11.36 |

_Totals: **LONG** n=96 net06=+6.7 (null median -2.2, observed pctile **0.79**) | **SHORT** n=100 net06=+21.0 (null median -0.9, observed pctile **0.97**) | **OVERALL** net06=+27.7 (pctile 0.96, **p_overall=0.035**)._

## Bybit -- emission spacing

| window | L n | L gross | L net06 | S n | S gross | S net06 |
|--:|--:|--:|--:|--:|--:|--:|
| 0 | 22 | +9.28 | +8.03 | 24 | +4.09 | +2.67 |
| 1 | 22 | +3.22 | +2.01 | 17 | +4.97 | +4.13 |
| 2 | 16 | +0.28 | -0.39 | 20 | +4.22 | +3.47 |
| 3 | 23 | -2.44 | -3.62 | 19 | +6.84 | +5.93 |
| 4 | 17 | +7.0 | +6.01 | 22 | +15.79 | +14.75 |

_Totals: **LONG** n=100 net06=+12.0 (null median -1.6, observed pctile **0.92**) | **SHORT** n=102 net06=+30.9 (null median -1.8, observed pctile **1.00**) | **OVERALL** net06=+43.0 (pctile 0.99, **p_overall=0.005**)._

## Reproduce
`python run_item3.py` -> item3_results.csv + this file.
