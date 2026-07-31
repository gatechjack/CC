# STUDY D -- RD_range breakout anomaly (frozen STUDY B, 1h; counts only)

**PRE-REGISTERED HYPOTHESIS:** among trend-cross trades ENTERED during RD_range (os==0), R concentrates where the RD break-state flips WITH the trade direction during the trade's lifetime (a), and evaporates where it flips AGAINST (b) or the range HOLDS to exit (c). Bucket = the FIRST os change over [entry, exit]. GROSS primary (net06 shown). Total RD_range-entry n is small (~73) -> every cell is thin; n<30 flagged throughout.

Total RD_range-entry trades: **73** (this is the whole population; treat all splits as thin).

**STRUCTURAL NOTE (not a verdict):** bucket (a) is defined by a POST-ENTRY event -- the RD os flipping WITH the trade direction means price broke the range in the trade's favour, which is the same move that makes the trade win. So the a/b/c split conditions on the trade's own outcome and is DESCRIPTIVE, not a predictive/tradeable signal (a near-tautology: 'trades that went my way went my way'). Reported for shape only. n<30 on every per-coin cell.

## Bucket x coin: n, net06, avgR  (flag n<30)

| bucket | coin | n | net06 | avgR | flag |
|---|---|--:|--:|--:|---|
| a_with | SOL | 4 | -0.2 | +0.000 | n<30 |
| a_with | BTC | 8 | +11.1 | +1.500 | n<30 |
| a_with | ETH | 18 | +12.7 | +0.778 | n<30 |
| a_with | XRP | 14 | +17.0 | +1.286 | n<30 |
| a_with | POOLED | 44 | +40.6 | +1.000 |  |
| | | | | | |
| b_against | SOL | 2 | -2.1 | -1.000 | n<30 |
| b_against | BTC | 0 | +0.0 | +0.000 | EMPTY |
| b_against | ETH | 1 | -1.1 | -1.000 | n<30 |
| b_against | XRP | 0 | +0.0 | +0.000 | EMPTY |
| b_against | POOLED | 3 | -3.1 | -1.000 | n<30 |
| | | | | | |
| c_holds | SOL | 4 | +3.9 | +1.000 | n<30 |
| c_holds | BTC | 5 | +2.6 | +0.600 | n<30 |
| c_holds | ETH | 9 | -1.5 | -0.111 | n<30 |
| c_holds | XRP | 8 | -8.5 | -1.000 | n<30 |
| c_holds | POOLED | 26 | -3.5 | -0.077 | n<30 |
| | | | | | |

## Per-window counts (n) by bucket (pooled)

| bucket | w0 | w1 | w2 | w3 | w4 | total |
|---|--:|--:|--:|--:|--:|--:|
| a_with | 4 | 13 | 10 | 6 | 11 | 44 |
| b_against | 1 | 1 | 1 | 0 | 0 | 3 |
| c_holds | 13 | 5 | 1 | 2 | 5 | 26 |

## Per-coin consistency of bucket (a) = os-flips-WITH

| coin | a_with n | a_with net06 | a_with avgR | flag |
|---|--:|--:|--:|---|
| SOL | 4 | -0.2 | +0.000 | n<30 |
| BTC | 8 | +11.1 | +1.500 | n<30 |
| ETH | 18 | +12.7 | +0.778 | n<30 |
| XRP | 14 | +17.0 | +1.286 | n<30 |

_Counts only. Every cell here is below n=30; the split is reported for shape, not significance._
