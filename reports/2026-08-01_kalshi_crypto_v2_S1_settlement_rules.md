# kalshi_crypto_v2 — S1 Settlement-Mechanics Verification (gates all backtests)

**Date:** 2026-08-01 · **Branch:** `claude-2026-08-01b` · **Script:** `research/kalshi_crypto_v2/s1_settlement.py`
(pulled live from the Kalshi API; rules quoted verbatim). Evidence only — no verdict.

## Top-line (the three literature-review questions, resolved)
1. **Source = CF Benchmarks Real-Time Index (RTI), per asset — NOT the daily BRR.** Confirmed in
   `settlement_sources` ("CF Benchmarks") and `rules_secondary` ("based on CF Benchmarks' corresponding
   **Real Time Index (RTI)** … continuous pricing data").
2. **Window = 60 seconds** ("the sixty seconds … before <time>"; "60 RTI prices are collected").
3. **★ METHOD CORRECTION: SIMPLE average of the 60 prices — NOT a trimmed mean.** rules_primary:
   "the **simple average** of the sixty seconds"; rules_secondary: "The official and final value is the
   **average** of these prices, rounded to the nearest N decimal places." Our Phase-1 KT doc / memory said
   "trimmed mean (drop top/bottom 20%, average 48)" — **incorrect; corrected here.** This does NOT contradict
   the 60s-average gate, and it MATCHES the cfbenchmarks feed's `avg_60s_data` (a plain 60s average) — so
   the settlement is directly reproducible from our forward corpus with no trim model. **Flagged for the
   operator to rule on; not treated as a hard-STOP because the 60s-average core holds.**

## Settlement-rules table (verbatim-sourced)
| series | cadence | asset | index (CF RTI) | window | method | comparison | rounding |
|---|---|---|---|---|---|---|---|
| KXBTC15M | 15-min up/down | BTC | BRTI | 60s | simple average | close-60s-avg ≥ open-60s-avg (relative) | 2 dp |
| KXETH15M | 15-min up/down | ETH | ETHUSDRTI (a.k.a. "ERTI") | 60s | simple average | relative | 2 dp |
| KXSOL15M | 15-min up/down | SOL | SOLUSDRTI | 60s | simple average | relative | 4 dp |
| KXXRP15M | 15-min up/down | XRP | XRPUSDRTI | 60s | simple average | relative | 4 dp |
| KXBTC | hourly ladder | BTC | BRTI | 60s | simple average | 60s-avg vs fixed strike (above/below per bucket) | — |
| KXETH | hourly ladder | ETH | ERTI | 60s | simple average | vs strike | — |
| KXSOLE | hourly ladder | SOL | SOLUSD_RTI | 60s | simple average | vs strike | — |
| KXXRP | hourly ladder | XRP | XRPUSD_RTI | 60s | simple average | vs strike; **no/incomplete data → strike resolves No** | — |

**Per-series differences:** (a) 15-min up/down is RELATIVE (close-window avg vs open-window avg → the
`floor_strike` is the open 60s-avg); hourly ladder is ABSOLUTE (60s-avg vs a fixed strike, direction
above/below per bucket). (b) Rounding: BTC/ETH 2 dp, SOL/XRP 4 dp (15-min). (c) XRP hourly adds an explicit
"no data → No" clause. (d) Rules-text index names vary cosmetically (ETHUSDRTI vs "ERTI"); the CANONICAL
feed index_ids from the cfbenchmarks probe are `BRTI / ETHUSD_RTI / SOLUSD_RTI / XRPUSD_RTI`.

## Hand-verifications (3, incl. one settled CLOSE to strike) — all MATCH to the cent
| market | index (settle) | strike | move% | result | check |
|---|---|---|---|---|---|
| `KXBTC15M-26AUG011900-00` | 62778.81 | 62791.67 | −0.0205% | no | MATCH |
| `KXETH15M-26AUG011900-00` | 1845.84 | 1846.33 | −0.0265% | no | MATCH |
| `KXBTC15M-26JUL182115-15` (closest) | 64739.54 | 64739.60 | **−0.0001%** (~6¢) | no | MATCH |

The razor-close case (6¢ apart on $64.7k) resolves correctly under `index ≥ strike → yes`, confirming the
resolution logic to the cent even at the boundary.

## Gate status
**PROCEED.** The 60s-average understanding holds (source = CF RTI per asset, window = 60s, resolution
logic verified 3/3 incl. boundary). The only correction is **simple-average (not trimmed)** — favorable
and reproducible from `avg_60s_data`. Backtest labels should use `index ≥ strike` (15m: close-60s-avg vs
open-60s-avg = `floor_strike`) with settlement = the published `expiration_value`.
