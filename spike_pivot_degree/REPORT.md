# Pivot-Degree Sensitivity Backtest — SFP Mode-B

**Generated:** 2026-07-01 02:18 UTC  
**Data window:** 2026-05-15 → 2026-07-01 (~46 days, 3m bars)  
**Coins:** BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT  
**pivot_lens tested:** [3, 5, 8, 10, 15, 20, 30, 50]  
**IS/OOS split:** 60% / 40%  
**Null runs:** 200 per config  

---

## VERDICT

**NO pivot_len (including multi-degree union) beats the null baseline OOS**
with adequate n (>=20) at the 95th-percentile threshold.

The 46-day window yields very few SFP+BOS signals at any pivot_len.
Most per-(coin, pivot_len) OOS buckets have n<20 — statistically weak.
The honest result: **data too thin to confirm or deny an edge over null.**
No live changes recommended from this spike alone.

---

## Per-Coin Tables

### BTCUSDT  (3m bars: 22198, IS cutoff: bar 13318)

| pivot_len | n_full | WR% | avgR | totalR | IS_avgR | OOS_n | OOS_avgR | OOS_WR% | null_p50 | null_p95 | null_pct | beats_null |
|-----------|--------|-----|------|--------|---------|-------|----------|--------|----------|----------|----------|------------|
|         3 |     56 |  25.0% | -0.250 | -14.00 | -0.385 |    17 | +0.059 |  35.3% | -0.002 | +0.500 |  56.5% | no (weak_n) |
|         5 |     42 |  19.0% | -0.429 | -18.00 | -0.667 |    15 | +0.000 |  33.3% | -0.100 | +0.601 |  56.0% | no (weak_n) |
|         8 |     30 |  20.0% | -0.400 | -12.00 | -0.167 |    12 | -0.750 |   8.3% | -0.100 | +0.750 |   2.0% | no (weak_n) |
|        10 |     21 |  19.0% | -0.429 |  -9.00 | -0.143 |     7 | -1.000 |   0.0% | +0.000 | +1.000 |   0.0% | no (weak_n) |
|        15 |     15 |  20.0% | -0.400 |  -6.00 | -0.182 |     4 | -1.000 |   0.0% | -0.125 | +1.250 |   0.0% | no (weak_n) |
|        20 |     13 |  15.4% | -0.538 |  -7.00 | -0.400 |     3 | -1.000 |   0.0% | +0.000 | +1.000 |   0.0% | no (weak_n) |
|        30 |      5 |  20.0% | -0.400 |  -2.00 | -0.400 |     0 |   nan |   nan% | -1.000 | +2.000 |   0.0% | no (weak_n) |
|        50 |      5 |  40.0% | +0.200 |  +1.00 | +0.200 |     0 |   nan |   nan% | -1.000 | +2.000 |   0.0% | no (weak_n) |

**Curve shape:** peak at pivot_len=3 (OOS avgR=+0.059), 1/6 configs within 0.05R of peak → LONE SPIKE — likely overfit

### ETHUSDT  (3m bars: 22394, IS cutoff: bar 13436)

| pivot_len | n_full | WR% | avgR | totalR | IS_avgR | OOS_n | OOS_avgR | OOS_WR% | null_p50 | null_p95 | null_pct | beats_null |
|-----------|--------|-----|------|--------|---------|-------|----------|--------|----------|----------|----------|------------|
|         3 |     75 |  18.7% | -0.440 | -33.00 | -0.553 |    28 | -0.250 |  25.0% | -0.033 | +0.429 |  19.5% | no |
|         5 |     51 |  23.5% | -0.294 | -15.00 | -0.520 |    26 | -0.077 |  30.8% | -0.053 | +0.351 |  48.5% | no |
|         8 |     36 |  30.6% | -0.083 |  -3.00 | -0.211 |    17 | +0.059 |  35.3% | -0.062 | +0.504 |  62.0% | no (weak_n) |
|        10 |     32 |  31.2% | -0.062 |  -2.00 | -0.286 |    11 | +0.364 |  45.5% | +0.000 | +0.800 |  74.0% | no (weak_n) |
|        15 |     26 |  34.6% | +0.038 |  +1.00 | +0.000 |    11 | +0.091 |  36.4% | +0.000 | +0.667 |  53.0% | no (weak_n) |
|        20 |     18 |  33.3% | +0.000 |  +0.00 | +0.000 |     9 | +0.000 |  33.3% | +0.000 | +0.717 |  44.5% | no (weak_n) |
|        30 |     13 |  30.8% | -0.077 |  -1.00 | +0.200 |     8 | -0.250 |  25.0% | -0.135 | +0.875 |  23.0% | no (weak_n) |
|        50 |      5 |  20.0% | -0.400 |  -2.00 | +0.500 |     3 | -1.000 |   0.0% | +0.000 | +1.000 |   0.0% | no (weak_n) |

**Curve shape:** peak at pivot_len=10 (OOS avgR=+0.364), 1/8 configs within 0.05R of peak → LONE SPIKE — likely overfit

### SOLUSDT  (3m bars: 22394, IS cutoff: bar 13436)

| pivot_len | n_full | WR% | avgR | totalR | IS_avgR | OOS_n | OOS_avgR | OOS_WR% | null_p50 | null_p95 | null_pct | beats_null |
|-----------|--------|-----|------|--------|---------|-------|----------|--------|----------|----------|----------|------------|
|         3 |     72 |  25.0% | -0.250 | -18.00 | -0.298 |    25 | -0.160 |  28.0% | +0.043 | +0.503 |  25.5% | no |
|         5 |     47 |  29.8% | -0.106 |  -5.00 | -0.300 |    17 | +0.235 |  41.2% | +0.059 | +0.500 |  73.5% | no (weak_n) |
|         8 |     36 |  30.6% | -0.083 |  -3.00 | -0.250 |    16 | +0.125 |  37.5% | +0.000 | +0.615 |  59.5% | no (weak_n) |
|        10 |     34 |  29.4% | -0.118 |  -4.00 | -0.250 |    14 | +0.071 |  35.7% | +0.000 | +0.690 |  53.0% | no (weak_n) |
|        15 |     22 |  18.2% | -0.455 | -10.00 | -0.500 |    10 | -0.400 |  20.0% | +0.000 | +0.800 |   9.5% | no (weak_n) |
|        20 |     19 |  15.8% | -0.526 | -10.00 | -0.625 |    11 | -0.455 |  18.2% | -0.100 | +0.669 |  15.5% | no (weak_n) |
|        30 |     13 |  15.4% | -0.538 |  -7.00 | -0.571 |     6 | -0.500 |  16.7% | +0.000 | +1.251 |  10.5% | no (weak_n) |
|        50 |      6 |  16.7% | -0.500 |  -3.00 | -1.000 |     3 | +0.000 |  33.3% | +0.000 | +1.000 |  29.0% | no (weak_n) |

**Curve shape:** peak at pivot_len=5 (OOS avgR=+0.235), 1/8 configs within 0.05R of peak → LONE SPIKE — likely overfit

### XRPUSDT  (3m bars: 22394, IS cutoff: bar 13436)

| pivot_len | n_full | WR% | avgR | totalR | IS_avgR | OOS_n | OOS_avgR | OOS_WR% | null_p50 | null_p95 | null_pct | beats_null |
|-----------|--------|-----|------|--------|---------|-------|----------|--------|----------|----------|----------|------------|
|         3 |     75 |  24.0% | -0.280 | -21.00 | -0.308 |    23 | -0.217 |  26.1% | -0.096 | +0.500 |  29.5% | no |
|         5 |     55 |  25.5% | -0.236 | -13.00 | -0.250 |    15 | -0.200 |  26.7% | -0.077 | +0.600 |  29.5% | no (weak_n) |
|         8 |     38 |  23.7% | -0.252 |  -9.59 | -0.591 |    16 | +0.213 |  37.5% | -0.062 | +0.500 |  79.5% | no (weak_n) |
|        10 |     30 |  23.3% | -0.253 |  -7.59 | -0.550 |    10 | +0.341 |  40.0% | -0.100 | +0.667 |  86.5% | no (weak_n) |
|        15 |     25 |  16.0% | -0.463 | -11.59 | -0.526 |     6 | -0.264 |  16.7% | +0.000 | +0.800 |  40.5% | no (weak_n) |
|        20 |     20 |  10.0% | -0.629 | -12.59 | -0.786 |     6 | -0.264 |  16.7% | +0.000 | +1.000 |  44.5% | no (weak_n) |
|        30 |     15 |   6.7% | -0.706 | -10.59 | -0.727 |     4 | -0.647 |   0.0% | -0.250 | +1.250 |  23.5% | no (weak_n) |
|        50 |      5 |   0.0% | -1.000 |  -5.00 | -1.000 |     0 |   nan |   nan% | -1.000 | +2.000 |   0.0% | no (weak_n) |

**Curve shape:** peak at pivot_len=10 (OOS avgR=+0.341), 1/7 configs within 0.05R of peak → LONE SPIKE — likely overfit


---

## Multi-Degree Union {pivot_len ∈ 5, 10, 20}

Pools signals from three pivot_len detectors, deduplicates on entry bar index, enforces one-open-at-a-time.

| coin | n_full | OOS_n | OOS_avgR | null_p95 | null_pct | beats_null |
|------|--------|-------|----------|----------|----------|------------|
| BTCUSDT |     52 |    18 | -0.167 | +0.416 |  41.0% | no (weak_n) |
| ETHUSDT |     57 |    29 | -0.172 | +0.435 |  28.0% | no |
| SOLUSDT |     61 |    25 | -0.040 | +0.500 |  45.5% | no |
| XRPUSDT |     62 |    19 | -0.294 | +0.421 |  37.0% | no (weak_n) |


---

## Caveats

1. **46-day window is short for rare setups.** SFP+BOS at pivot_len≥20 fires infrequently; n<20 OOS is the norm. No statistical claim is valid at these sample sizes.
2. **No fee/slippage model.** Taker fee 0.019% × 2 legs ≈ 0.038% per trade (negligible vs 1R stop). Slippage on volatile 3m BOS bars is unmodelled; live slippage can be >1× the fee cost on fast moves (see stop-slippage memory).
3. **Stop-first conservative assumption.** Same-bar stop+TP resolved as loss. Slightly pessimistic, appropriate for stress-testing.
4. **Null baseline uses recent-low stop**, which gives comparable R units but is not a 'same-setup' null. It is a random-entry benchmark, not a causal null.
5. **Pivot_len changes the detector's lookback semantics**, not just sensitivity. Short pivot_lens (3,5) fire on micro-structure swings; long (50) fires on major swings. These are qualitatively different setups, not continuous deformations.
6. **Harness self-check:** see console output for whether pivot_len=50 reproduces the known 2026-06-28 live fires. If not, results should not be trusted.

---

*Research only. Not financial advice. Do not act on this without further validation.*
