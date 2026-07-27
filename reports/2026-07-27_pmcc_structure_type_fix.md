# PMCC structure_type mislabel — FIX (held for deploy)

**Date:** 2026-07-27 · **Branch:** `claude-structtype-2026-07-27` off **prod-live `d553a3e`** (== prod).
**Diagnosis:** `reports/2026-07-27_pmcc_covered_call_mislabel_diagnosis.md`. **Fix commit:** `e97ebb0`.
**Scope:** display/classifier layer only — no roll logic, broker, auto_execute, or halt change.

## What changed (`web/data.py`, 2 files +175/−20)

`PMCCPair.structure_type` now classifies by **what covers the short**, not by the long leg's remaining DTE
(the old `leap.dte >= 180` discriminator was the mislabel bug — a real LEAP that aged below 180 days flipped
to `covered_call`):

| structure | rule (new) |
|---|---|
| `pmcc` | long call + short call — the long call covers, at **any** remaining DTE |
| `covered_call` | equity shares (**≥ 100 per short contract**) + short call, **no** long-call cover |
| `uncovered_leap` | long call only, no short — **any** DTE (retires the `naked_call` DTE flip) |
| `short_only` | short call with no cover (no long call, no shares) = naked short |
| `other` | no primary call legs |

- New `PMCCPair.underlying_shares` field + a `shares` arg on `_group_pmcc_pairs` (optional, backward-
  compatible), populated in `build_division_view` from `stock_holdings` (`{symbol.upper(): qty}`). A helper
  `_shares_cover_short()` checks `shares >= 100 × |short qty|`.
- The 180-DTE threshold is **gone** from the classifier (still used by `priority_score` for tile *sort*, a
  separate display axis — untouched).

**Effect on the live book:** TSLA/HOOD/MSTR/OPEN/BULL (2027-01-15 LEAPs, ~172 DTE) now classify `pmcc`
(were `covered_call`); IREN/RKLB/SMR/BLSH/RIOT unchanged (`pmcc`). Only a *genuine* shares-backed short
would be `covered_call` — currently none in this division (STRC holds 164.69 shares but no short call).

## Tests (+12, `tests/test_pmcc_structure_type.py`)

- **Aged LEAP is PMCC, not covered_call:** a 172-DTE LEAP + short → `pmcc`; boundary sweep (1/90/179/180/
  181/5000 DTE) all → `pmcc`.
- **Shares-backed → covered_call:** short + 164.69 shares → `covered_call`; a 2-contract short needs 200
  shares (164.69 → `short_only`; 250 → `covered_call`).
- **No-cover short → short_only** (None / 0 / 50 shares).
- **Long-call cover wins over shares** (LEAP + short + 1000 shares → `pmcc`).
- **uncovered_leap / naked_call don't flip on LEAP age:** long-call-only at 172/1639/10 DTE all →
  `uncovered_leap`; `naked_call` never returned.
- **`_group_pmcc_pairs` threads shares** end-to-end (covered_call for a shares+short STRC; pmcc for an aged
  TSLA LEAP pair; `underlying_shares` defaults None when the arg is omitted).

## Regression — apples-to-apples vs base `d553a3e`

Full suite: base **61 failed**, branch **61 failed** — **identical set** (`Compare-Object` = 0 differences),
i.e. **zero new failures**. The 61 are the pre-existing store-Python-3.14 harness failures (bitunix /
iron_condor / robinhood_multi_leg / prediction_markets_dashboard / webhooks / …), none PMCC. The +12 new
tests pass. Runs were sequential (concurrent runs flake the timing test `test_position_state_sanity_poll`).

**Held for deploy** — pushed, not deployed. No broker/auto_execute/halt touched; no re-roll.
