# SOL SFP × BTC-confirmation filter — pre-registered, 2026-06-26

**Theory:** BTC leads, alts follow. The SUBSET of SOL SFPs that fire while BTC is confirming the same
direction should be the ones that follow through (bigger excursion), partitioning SOL's SFPs into a
profitable subset — attacking the excursion root cause directly.

**Pre-registered (ONE shot, no sweep):** SOL SFPs (15m, REAL+CONS, long) from sol_scalping.db; BTC 15m
from btc_scalping.db. BTC bullish BOS = BTC body-close above its most-recent **pivot(50,50)** swing
high (k=1: pivot usable only at p+50). Window N=4 → BTC BOS on the SOL fire bar or the 4 prior bars.
Both entries tested (SFP→BOS, SFP-direct). Both DBs + frozen oracle read-only.

## ★ Alignment / k=1 (the critical correctness point) — clean
- Join strictly by **Unix-second ts** (both 15m, same grid; SOL 2025-11-01.., BTC ends 2026-06-19).
- BTC window uses `btc.ts <= sol_fire.ts`; SOL entry is next-bar-open (b+1). The latest BTC bar used
  (ts = fire ts) closes at the same instant the SOL entry bar opens → **no look-ahead**. BTC pivot
  highs are k=1 (confirmed 50 bars forward).
- **55 SOL fires; 54 aligned to a concurrent BTC bar; 1 unaligned** (fired after BTC's 2026-06-19 end).
  No mid-range gaps.

## Result — partition (the n problem)
**BTC-CONFIRMED = 1 of 54.  NOT-CONFIRMED = 53.** (BTC bullish-BOS base rate = 15.2% of bars; the
pivot(50,50) BOS + 5-bar window + coincidence-with-a-SOL-sweep is very rare.)

### SFP-DIRECT @2R
| subset | n | win | avgR | WF | median MFE (R / %) |
|---|---|---|---|---|---|
| BTC-CONFIRMED | **1** | 100% | +1.892 | thin | 35.8R / **16.0%** |
| NOT-CONFIRMED | 53 | 20.8% | −0.510 | STABLE− | 0.53R / 0.34% |
| ALL-aligned | 54 | 22.2% | −0.466 | STABLE− | 0.57R / 0.36% |

### SFP→BOS @2R
| subset | n | win | avgR | WF | median MFE (R / %) |
|---|---|---|---|---|---|
| BTC-CONFIRMED | **1** | 100% | +1.961 | thin | 12.2R / 15.1% |
| NOT-CONFIRMED | 11 | 27.3% | −0.236 | thin | 1.05R / 1.6% |
| ALL-aligned | 12 | 33.3% | −0.053 | thin | 1.09R / 1.7% |

## VERDICT (pre-registered, honest — discipline held)
- **Does BTC-confirmation partition SOL into a profitable, WF-stable subset at n≥30? NO — not
  testable at this definition.** The confirmed subset is **n=1**, far below the n≥30 bar. Per the
  pre-registered n-warning, this is **NOT verdict-grade and is NOT promoted.**
- **The single confirmed instance is a huge runner** (16% favorable move, +1.9R both entries) vs the
  not-confirmed median of 0.34% — *directionally* consistent with "BTC-confirmation selects the
  sweeps that run." But **n=1 is an anecdote, not evidence** (1-in-54 could be luck), so it cannot
  support the theory. The NOT-CONFIRMED bulk (n=53) is clearly negative (−0.510R, STABLE−),
  consistent with "most SOL sweeps don't follow through."
- **Root finding: the pre-registered BTC-confirm definition (pivot-50/50 BOS, a 25h-window structural
  break, within 1h) is too STRICT/rare to yield a usable subset on SOL** — it fires once. The theory
  is left UNTESTED here, not confirmed and not refuted.
- **No goalposts moved:** I did NOT sweep N or the BTC-confirm definition. SOL stays **monitor-only.**

## Honest next-step note (separate experiment, NOT run)
The mechanism is intriguing enough (the one BTC-confirmed sweep was the single biggest runner) that a
**looser, pre-registered BTC-confirm definition** — e.g. BTC two-candle swing BOS (the detector's own
BOS, far more frequent), or a simple BTC-trend/EMA-up gate — could yield n≥30 and actually test the
theory. That is a NEW pre-registered experiment; it is deliberately not done here (one shot, no sweep).
