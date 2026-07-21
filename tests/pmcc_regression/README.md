# PMCC Bucket-B regression harness (Phase 0)

Structural pathology detectors + a frozen baseline for the PMCC Bucket-B build
(`planning/pmcc_option2_bucketB_plan.md`). **No `pmcc_robinhood.py` behavior is
touched by this package** — Phase 0 is instrumentation only.

## What this is
Each detector in `detectors.py` is a **pure function over a `RecRecord`**. The
same detectors run against two inputs:
1. the frozen 157-row audit history (`planning/pmcc_rec_history.csv`) — the
   structural **regression baseline** (`baseline.py::BASELINE`), asserted by
   `test_baseline.py`;
2. **synthetic** `RecRecord`s built in later phases — each fix asserts its
   targeted pathology is now **absent** while the others are **unchanged**.
   Phase 1 builds records from the code's ACTUAL proposed legs via
   `RecRecord.from_legs(...)`.

## Detector -> fix -> baseline
| detector | fix | baseline | target |
|---|---|---|---|
| `close_without_recover` | B4 | **51** (32.5% of recs / 40.5% of rolls) | 0 |
| &nbsp;&nbsp;`b4_uncovered` | B4 | 31 | 0 |
| &nbsp;&nbsp;`b4_fully_naked` | B4 | 20 (all flat) | 0 |
| &nbsp;&nbsp;`b4_naked_short` | B4 | **0** | stay 0 |
| `hold_overridden` | B1 | 50 (32% of 157) | 0 unjustified |
| `same_expiry_roll` | B7 | 18 | 0 |
| `net_debit_roll` | B2 | 37 | 0 unjustified |
| `cost_ignorant_leap_roll` | B3 | 38 (33 zero-priced + 5 leg-absent) | 0 |
| `holiday_scan` | B11 | 6 (3 dates) | 0 |
| `short_delta_ge_040` | B5 | 24 | 0 outside band |
| `itm_target_strike_bypass` | B6 | None (not persisted) | 0 (synthetic) |

## Reconciliation notes
- **`close_without_recover` denominator:** 51/157 = 32.5% of all recs (== the
  plan's "~30% book"); 51/126 = 40.5% of rolls. The earlier "~30%" and "40.5%"
  are the SAME finding under different denominators — not a discrepancy.
- **B4 sub-severities (Phase-0 drill of the leg data):** 31 uncovered (long
  remains, no covering short) + 20 fully_naked (old LEAP sold and not replaced;
  ALL 20 were flat = both legs closed = a roll that collapsed to a full close)
  + **0 naked_short** (LEAP sold with a short left open — never happened; the
  deterministic close-short leg fires whenever a short exists).
- **`holiday_scan` = 6 recs across 3 FULL closures** (Memorial Day 2026-05-25,
  Juneteenth 06-19, Independence-observed 07-03; verified against SPY — none are
  half-days). Supersedes the earlier CIFR-only "2".
- **`cost_ignorant_leap_roll` = 38 not computable**, data-backed: 33 old-LEAP
  sell legs priced 0.0 + 5 roll_leaps with no old-LEAP sell leg at all.

## Data gap
- `target_strike` + spot were never persisted, so `itm_target_strike_bypass`
  has no historical numeric baseline; it is validated synthetically in Phase 2.

## Run
    pytest tests/pmcc_regression/ -q
