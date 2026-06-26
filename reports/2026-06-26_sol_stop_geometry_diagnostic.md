# SOL SFP stop/entry-geometry diagnostic (read-only, 2026-06-26)

**Operator concern:** an SFP stop should be tiny (enter near the reclaim, stop just below the swept
wick → small R → high r:R). The SOL results imply a wide stop (~0.4–1.0%). What did the harness
actually use?

## The code (frozen oracle `confluence_exp6_p6_sfp_bos_2026-06-24.py`)
- **L31** `BUF, PIV, BTB = 0.001, 50, 4`
- **L96** (`sfp_events`, REAL long) `if l[b] < sl and c[b] > sl: fired=True; swept = l[b]` → **`swept` = the
  sweep bar's LOW (the wick)**; `sl` = the swept pivot-low level.
- **L115** `entry = oo[ei]` → entry = the **OPEN of bar `ei`**.
- **L117** (long) `stop = swept - BUF*entry; R = entry - stop` → **stop = swept_wick − 0.1%·entry**, so
  **R = (entry − wick) + 0.1%·entry**.
- `ei` = **b+1** for SFP-direct (`raw_trade` L178) / **bos_w+1** for SFP→BOS (`watch_A` L148).

Note: on crypto perps `open[b+1] == close[b]` (continuous) — verified in the data (entry == reclaim_close
in every direct row). So **SFP-direct entry = the reclaim close** (top of the sweep bar), and the stop is
below the wick (bottom) → **R = the whole sweep bar's range.**

## Actual SOL 15m geometry — entry→stop distance (the R unit)
| entry style | n | median R% | min | max | median entry→wick% |
|---|---|---|---|---|---|
| SFP-direct | 55 | **0.522%** | 0.172% | 2.863% | 0.422% |
| SFP→BOS | 12 | **1.339%** | 0.526% | 4.812% | 1.239% |

**Both are WIDE** — not the ~0.1–0.3% of a tight SFP. (R% = entry→wick% + the flat 0.1% buffer.)

## Decomposition: WHY is it wide? (entry above the wick, not a deep wick)
entry→wick% = **wick-depth-below-level** + **entry-above-level**. From the per-trade data:
- **The wick is SHALLOW** — median depth below the swept level ≈ **0.15–0.19%** for *both* styles. So it
  is NOT (b) "the wick is far from the level."
- **SFP-direct:** entry-above-level median ≈ **0.17%** (the reclaim closed ~0.17% above the level). So
  R ≈ wick-depth (0.17%) + reclaim-above-level (0.17%) + buffer (0.1%) ≈ 0.52% = **the full sweep bar**.
- **SFP→BOS:** entry-above-level median ≈ **1.05%** (range up to **4.36%**!). The BOS-wait lets price
  DRIFT up from the reclaim to the BOS bar before entering (drift reclaim→entry median ~0.8%, max
  3.67%), so entry sits ~1% above the level while the stop is still below the original wick → **R ≈ 3×
  the direct R.** Worst case 2026-02-02: reclaim 97.04 → BOS entry 100.6 (drifted 3.67%), wick 95.86 →
  **R = 4.81%.**

## ANSWER
- **Entry-to-stop is WIDE, not tiny** (direct median 0.52%, BOS median 1.34%).
- **Cause = (a) the entry sits ABOVE the wick, not (b) a deep wick.** The wick is shallow (~0.16% below
  the level). The width comes from the entry being the **reclaim close** (direct, ~0.17% above level =
  the full sweep-bar range) and, far worse, the **BOS-wait drifting entry ~1%+ above the level** (BOS).
- This directly explains the negative R: with R = the whole sweep bar (~0.5–1.3%), the median favorable
  excursion (~0.4–1.0%) is **< R** → reward < risk. The signal isn't getting a fair r:R because the
  **entry/stop geometry is wide by construction**, not because SOL sweeps don't move.

## Implication (a concrete next experiment — NOT run here)
A **limit entry at/near the swept level** (instead of market at the reclaim close / drifted BOS bar)
would make **R ≈ wick-depth + buffer ≈ 0.16% + 0.1% ≈ 0.26%** — the tight SFP the operator described.
The SAME ~0.4–1.0% favorable excursion would then be **~2–4 R** instead of <1 R. That is the lever to
test next (pre-registered: limit-at-level entry, tight stop) — it could flip the verdict without
touching the signal. Read-only; nothing changed.
