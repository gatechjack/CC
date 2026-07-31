# OptiTrade AI -- validation pass on the 3 candidate cells

> **SPACING CORRECTION (2026-07-31).** The continuation cell here (ETH 1h) used the
> **emission-clock** spacing; the vendor-exact `ta.barssince(buy2[1])>30` is stricter
> (see `SPEC_DIFF.md`). Vendor-exact ETH 1h continuation: net06 Binance +9.5 (was
> +24.1), Bybit +27.7 (was +43.0, 4/5 not 5/5); recomputed drift-controlled p in
> `ITEM3.md` (vendor Binance p=0.125, vendor Bybit 0.035). Reversal cells (SOL/XRP)
> share the same residual class with smaller effect. Read the numbers below as the
> emission-clock variant.

Cross-venue (Bybit replay of the exact Binance-selected config), Binance config-neighborhood (RR2.5 + adjacent preset), and a shuffled-entry permutation null. Bracket SL=2.5*ATR, sl-first, 5 equal windows, WARMUP=400. GROSS primary; net06/net04 = 0.06%/0.04% per side. Config selection never saw Bybit, so the Bybit replay is the true out-of-sample arbiter.

## SOLUSDT 15m Normal/reversal/RR3.5

| variant | note | tot n | tot gross | tot net06 | gross+ | net06+ | net04+ | win n>=30 |
|---|---|--:|--:|--:|--:|--:|--:|--:|
| Binance-selected | SOLUSDT 15m Binance 2022-07..2026-06 | 874 | +102.5 | +29.5 | 5/5 | 4/5 | 5/5 | 5/5 |
| Bybit-replay | SOLUSDT 15m Bybit 2025-11-05..2026-06-26 | 131 | +14.4 | +2.0 | 3/5 | 3/5 | 3/5 | 1/5 |
| nbr RR2.5 | Binance same preset/mode, RR2.5 | 983 | +61.1 | -21.3 | 4/5 | 2/5 | 3/5 | 5/5 |
| nbr VeryHigh | Binance VeryHigh/reversal/RR3.5 | 550 | +74.7 | +28.7 | 4/5 | 4/5 | 4/5 | 5/5 |

_Permutation null (200 shuffled-entry perms, SAME one-position bracket, matched per-window counts): observed total net06=+29.5 (4/5 windows+); p(null total net06 >= observed) = **0.005**; null P(>=4/5 windows net06+) = 0.01._

**Summary (counts, no verdict):** venue transfer -> Bybit net06+ 3/5 windows, tot net06 +2.0 (win n>=30 1/5); neighborhood -> RR2.5 net06+ 2/5 (tot -21.3), adjacent-preset net06+ 4/5 (tot +28.7); permutation p=0.005.

## ETHUSDT 1h Normal/continuation/RR3.5

| variant | note | tot n | tot gross | tot net06 | gross+ | net06+ | net04+ | win n>=30 |
|---|---|--:|--:|--:|--:|--:|--:|--:|
| Binance-selected | ETHUSDT 1h Binance 2022-07..2026-06 | 352 | +43.8 | +24.1 | 3/5 | 3/5 | 3/5 | 5/5 |
| Bybit-replay | ETHUSDT 1h Bybit 2024-01-17..2026-06-26 | 202 | +53.3 | +43.0 | 5/5 | 5/5 | 5/5 | 5/5 |
| nbr RR2.5 | Binance same preset/mode, RR2.5 | 423 | +34.8 | +10.0 | 3/5 | 3/5 | 3/5 | 5/5 |
| nbr VeryHigh | Binance VeryHigh/continuation/RR3.5 | 337 | -19.3 | -38.4 | 1/5 | 0/5 | 1/5 | 5/5 |

_Permutation null (200 shuffled-entry perms, SAME one-position bracket, matched per-window counts): observed total net06=+24.1 (3/5 windows+); p(null total net06 >= observed) = **0.035**; null P(>=4/5 windows net06+) = 0.07._

**Summary (counts, no verdict):** venue transfer -> Bybit net06+ 5/5 windows, tot net06 +43.0 (win n>=30 5/5); neighborhood -> RR2.5 net06+ 3/5 (tot +10.0), adjacent-preset net06+ 0/5 (tot -38.4); permutation p=0.035.

## SOLUSDT 1h VeryHigh/reversal/RR3.5

| variant | note | tot n | tot gross | tot net06 | gross+ | net06+ | net04+ | win n>=30 |
|---|---|--:|--:|--:|--:|--:|--:|--:|
| Binance-selected | SOLUSDT 1h Binance 2022-07..2026-06 | 150 | +38.6 | +32.4 | 5/5 | 5/5 | 5/5 | 3/5 |
| Bybit-replay | SOLUSDT 1h Bybit 2024-01-17..2026-06-26 | 90 | +36.3 | +32.4 | 5/5 | 5/5 | 5/5 | 0/5 |
| nbr RR2.5 | Binance same preset/mode, RR2.5 | 159 | +35.8 | +29.1 | 5/5 | 5/5 | 5/5 | 4/5 |
| nbr Normal | Binance Normal/reversal/RR3.5 | 217 | -0.1 | -8.5 | 3/5 | 2/5 | 3/5 | 4/5 |

_Permutation null (200 shuffled-entry perms, SAME one-position bracket, matched per-window counts): observed total net06=+32.4 (5/5 windows+); p(null total net06 >= observed) = **0.010**; null P(>=4/5 windows net06+) = 0.07._

**Summary (counts, no verdict):** venue transfer -> Bybit net06+ 5/5 windows, tot net06 +32.4 (win n>=30 0/5); neighborhood -> RR2.5 net06+ 5/5 (tot +29.1), adjacent-preset net06+ 2/5 (tot -8.5); permutation p=0.010.

## Multiple-comparisons baseline

- **Analytic (fair coin).** Per config-cell, P(>=4 of 5 windows net06-positive) = C(5,4)+C(5,5) over 2^5 = 6/32 = 0.1875. Because 'best config per cell' is the max over 24 configs, P(the cell's best shows >=4/5) ~ 1-(1-0.1875)^24 ~ 0.99, i.e. almost all **12 of 12** cells would show the sign-pattern by chance. The >=4/5 sign-count is therefore NOT informative after best-of-24 selection.
- **Empirical (shuffled-entry null).** Mean per-config P(>=4/5 windows net06+) across the 3 candidates = 0.05 (fee drag pulls it below the fair-coin 0.19). Best-of-24 selection -> per-cell P ~ 1-(1-0.05)^24; expected cells showing the pattern under the null ~ **8 of 12**.
- **What this means.** The window-sign pattern is expected under the null; the discriminating evidence is (a) the permutation p-value on total net06 magnitude and (b) whether the effect survives the Bybit venue transfer. Both are reported per cell above.

## Reproduce
`python run_validation.py` -> validation_results.csv + this file.
