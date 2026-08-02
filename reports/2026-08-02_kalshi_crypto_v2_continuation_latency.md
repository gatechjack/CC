# S4 Continuation — Latency / Decay Sensitivity

**Date:** 2026-08-02 · **Standing:** read-only; on-disk; lab DB only; evidence only — no verdict.

Same continuation rule as `continuation_exec` (buy the continuation side after a ≥threshold Binance move in minutes 1-3), but **entry is delayed `delay` minutes after the qualifying minute** — so at delay≥1 the fill price is a candle STRICTLY AFTER the signal completes (removing the intra-minute ordering risk). Holdout (last 20%), fees in, taker + maker legs, maker fill_rate shown. **delay=0 = enter at the qualifying minute** (≈ the continuation_exec baseline).

> ★ **T5 basis caveat:** move = Binance, settlement = CF-Benchmarks RTI. The proxy mismatch sits under every number; if the edge IS a Binance→RTI lead-lag, its decay across delay also traces how long that lead persists.

## BTC

HOLDOUT, primary threshold **0.10%** — decay across entry delay:

| delay | n trades | taker win% | taker@traded (t) | taker@quote (t) | maker per-ATTEMPT (t) | maker fill_rate |
|---|---|---|---|---|---|---|
| m+0 | 232 | 81.0% | +0.1246 (t=+4.9) | +0.1690 (t=+6.6) | +0.1071 (t=+4.3) | 80.6% |
| m+1 | 232 | 81.0% | -0.0032 (t=-0.1) | +0.0162 (t=+0.6) | +0.0129 (t=+0.5) | 94.4% |
| m+2 | 232 | 81.0% | -0.0350 (t=-1.4) | +0.0133 (t=+0.5) | +0.0049 (t=+0.2) | 90.5% |
| m+3 | 232 | 81.0% | -0.0384 (t=-1.6) | +0.0173 (t=+0.7) | +0.0095 (t=+0.4) | 90.1% |

taker@traded (holdout) across threshold × delay:

| Threshold | m+0 | m+1 | m+2 | m+3 |
|---|---|---|---|---|
| 0.05% | +0.0698 (t=+3.9) | -0.0414 (t=-2.3) | -0.0790 (t=-4.5) | -0.0765 (t=-4.5) |
| 0.10% ★ | +0.1246 (t=+4.9) | -0.0032 (t=-0.1) | -0.0350 (t=-1.4) | -0.0384 (t=-1.6) |
| 0.15% | +0.1212 (t=+3.2) | -0.0165 (t=-0.4) | -0.0403 (t=-1.1) | -0.0386 (t=-1.1) |
| 0.20% | +0.0510 (t=+0.8) | -0.0939 (t=-1.5) | -0.1127 (t=-1.7) | -0.1071 (t=-1.7) |

## ETH

HOLDOUT, primary threshold **0.10%** — decay across entry delay:

| delay | n trades | taker win% | taker@traded (t) | taker@quote (t) | maker per-ATTEMPT (t) | maker fill_rate |
|---|---|---|---|---|---|---|
| m+0 | 407 | 73.2% | +0.0613 (t=+2.8) | +0.1165 (t=+5.4) | +0.0595 (t=+2.9) | 83.5% |
| m+1 | 407 | 73.2% | -0.0601 (t=-2.9) | -0.0423 (t=-2.0) | -0.0485 (t=-2.3) | 93.6% |
| m+2 | 407 | 73.2% | -0.0955 (t=-4.6) | -0.0399 (t=-1.9) | -0.0447 (t=-2.2) | 92.6% |
| m+3 | 407 | 73.2% | -0.0884 (t=-4.3) | -0.0343 (t=-1.7) | -0.0482 (t=-2.5) | 88.0% |

taker@traded (holdout) across threshold × delay:

| Threshold | m+0 | m+1 | m+2 | m+3 |
|---|---|---|---|---|
| 0.05% | +0.0480 (t=+3.0) | -0.0609 (t=-3.9) | -0.1021 (t=-6.6) | -0.0956 (t=-6.4) |
| 0.10% ★ | +0.0613 (t=+2.8) | -0.0601 (t=-2.9) | -0.0955 (t=-4.6) | -0.0884 (t=-4.3) |
| 0.15% | +0.0630 (t=+2.2) | -0.0655 (t=-2.4) | -0.0910 (t=-3.3) | -0.0834 (t=-3.1) |
| 0.20% | +0.0930 (t=+2.4) | -0.0538 (t=-1.5) | -0.0716 (t=-2.0) | -0.0658 (t=-1.9) |

## SOL

HOLDOUT, primary threshold **0.10%** — decay across entry delay:

| delay | n trades | taker win% | taker@traded (t) | taker@quote (t) | maker per-ATTEMPT (t) | maker fill_rate |
|---|---|---|---|---|---|---|
| m+0 | 408 | 72.5% | +0.0788 (t=+3.7) | +0.1235 (t=+5.9) | +0.0580 (t=+2.9) | 80.4% |
| m+1 | 408 | 72.5% | -0.0469 (t=-2.2) | -0.0305 (t=-1.5) | -0.0424 (t=-2.1) | 91.7% |
| m+2 | 408 | 72.5% | -0.0776 (t=-3.7) | -0.0199 (t=-1.0) | -0.0346 (t=-1.7) | 88.2% |
| m+3 | 408 | 72.5% | -0.0699 (t=-3.4) | -0.0144 (t=-0.7) | -0.0316 (t=-1.6) | 86.0% |

taker@traded (holdout) across threshold × delay:

| Threshold | m+0 | m+1 | m+2 | m+3 |
|---|---|---|---|---|
| 0.05% | +0.0543 (t=+3.6) | -0.0503 (t=-3.4) | -0.0862 (t=-5.9) | -0.0751 (t=-5.3) |
| 0.10% ★ | +0.0788 (t=+3.7) | -0.0469 (t=-2.2) | -0.0776 (t=-3.7) | -0.0699 (t=-3.4) |
| 0.15% | +0.0898 (t=+2.9) | -0.0429 (t=-1.4) | -0.0690 (t=-2.3) | -0.0696 (t=-2.4) |
| 0.20% | +0.1003 (t=+2.6) | -0.0498 (t=-1.3) | -0.0698 (t=-1.8) | -0.0637 (t=-1.7) |

## XRP

HOLDOUT, primary threshold **0.10%** — decay across entry delay:

| delay | n trades | taker win% | taker@traded (t) | taker@quote (t) | maker per-ATTEMPT (t) | maker fill_rate |
|---|---|---|---|---|---|---|
| m+0 | 361 | 76.7% | +0.1030 (t=+4.8) | +0.1461 (t=+6.8) | +0.0882 (t=+4.2) | 81.7% |
| m+1 | 361 | 76.7% | -0.0352 (t=-1.7) | -0.0163 (t=-0.8) | -0.0229 (t=-1.1) | 92.5% |
| m+2 | 361 | 76.7% | -0.0615 (t=-2.9) | -0.0010 (t=-0.0) | -0.0133 (t=-0.7) | 89.5% |
| m+3 | 361 | 76.7% | -0.0501 (t=-2.4) | +0.0065 (t=+0.3) | -0.0103 (t=-0.5) | 86.1% |

taker@traded (holdout) across threshold × delay:

| Threshold | m+0 | m+1 | m+2 | m+3 |
|---|---|---|---|---|
| 0.05% | +0.0604 (t=+3.7) | -0.0635 (t=-3.9) | -0.1018 (t=-6.4) | -0.0918 (t=-5.9) |
| 0.10% ★ | +0.1030 (t=+4.8) | -0.0352 (t=-1.7) | -0.0615 (t=-2.9) | -0.0501 (t=-2.4) |
| 0.15% | +0.1101 (t=+3.5) | -0.0357 (t=-1.2) | -0.0563 (t=-1.8) | -0.0447 (t=-1.5) |
| 0.20% | +0.1094 (t=+2.5) | -0.0477 (t=-1.2) | -0.0614 (t=-1.5) | -0.0517 (t=-1.3) |

## Reading this (evidence, not verdict)

- **Edge persists at delay≥1** ⇒ a real multi-minute continuation, post-signal, tolerant of some execution latency (not an intra-minute ordering artifact). **Edge collapses at delay=1** ⇒ it was intra-minute / single-tick and not harvestable with realistic latency.
- The decay RATE (m+0→m+3) also bounds how much latency the edge can absorb, and — under the T5 lens — how long any Binance→RTI lead lasts.
- Maker per-ATTEMPT (no-fills@$0) with its fill_rate remains the honest maker number; taker@traded is the spread-crossing executable leg.

