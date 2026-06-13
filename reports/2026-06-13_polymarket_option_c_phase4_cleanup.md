# Polymarket option (c) Phase 4 — cleanup of the Phase 3 compat shim

**Date:** 2026-06-13
**Branch:** `polymarket-option-c-phase4-cleanup-2026-06-13` (base `main` `3d8cc1a` = the
Phase 3 unify merge)
**Mode:** local worktree only. No prod access, no SSH, no DB writes, no deploy, no merge
(disclosure per `82fda13`).
**Outcome:** the only superseded "old per-script path" was a referenced compat shim; removed
it (behavior-neutral) on explicit operator go. **Committed UNMERGED. STOP for operator review.**

---

## 1. Finding: there were no dead duplicated loops to delete

Phase 4 was scoped as *"delete the old duplicated `/activity`-walk loops in refresh/seed that
the Phase 3 shared module replaced."* On inspection that premise did **not** hold: Phase 3
(`41ca5b9`) was a **clean extraction, not an additive one** — it removed the inline loops by
rewiring both callers to `fetch_activity_window_for_candidates`. Every function in
`refresh_polymarket_whales.py` and `seed_polymarket_watchlist_deep.py` is referenced; the
legacy `compute_polymarket_stats` / `score_polymarket_whale` are still **live** (refresh's
dry-run naive-comparison column, `refresh_polymarket_whales.py:305-342`) — not dead, not
superseded by `whale_screening` (which is the activity-fetch layer, not the scorer).

The **only** residual "old path kept importable" was the one Phase 3 explicitly deferred to
"a later cleanup": `seed_polymarket_watchlist_deep` re-exported
`_fetch_wallet_activity_windowed` (used internally nowhere) purely so pre-existing imports
kept resolving. It was **still referenced** by three test files, so removing it is a
referenced-symbol migration, not dead-code deletion — surfaced as a fork; operator chose to
do the cleanup.

## 2. Change set (behavior-neutral)

| File | Δ | Change |
|---|---|---|
| `trading_corp/scripts/seed_polymarket_watchlist_deep.py` | −4/+1 | drop the `_fetch_wallet_activity_windowed` re-export; import only `fetch_activity_window_for_candidates` |
| `tests/test_refresh_polymarket_whales.py` | repoint | import `_fetch_wallet_activity_windowed` from `whale_screening` (was: from seed) |
| `tests/test_polymarket_watchlist_seed.py` | repoint | same repoint; `_aggregate_window_to_decisions` / `_select_resolved_buys_window` stay imported from seed (genuinely defined there) |
| `tests/test_whale_screening_equivalence.py` | reframe | `test_walk_is_single_source_reexported_from_seed` → `test_walk_is_single_source_in_whale_screening`: assert single-source identity **and** `not hasattr(seed_mod, "_fetch_wallet_activity_windowed")` |

No production runtime logic touched: seed's runtime only ever called the loop wrapper, so
dropping an unused import changes nothing. Roster output is byte-identical to Phase 3.
Other seed importers (`test_polymarket_data_api_client_retry.py`, the two
`scripts/verification/.../replay_*.py`) import only genuinely-seed-defined symbols
(`_merge_watchlists`, `_select_resolved_buys_window`, `_aggregate_window_to_decisions`) and
are unaffected.

## 3. Evidence

1. **Targeted caller/equivalence suites — 54 passed.**
   `test_whale_screening_equivalence` (5) + `test_refresh_polymarket_whales` +
   `test_polymarket_watchlist_seed` (49). `EXIT=0`.
2. **Full pytest gate vs pristine base, SAME env (per the Phase-3-D1 convention).** Ran the
   whole suite on this branch and on the stashed pristine base **in the same worktree**:
   FAILED/ERROR node sets are **identical** — `PRISTINE=31`, `EDITED=31`, `Compare-Object`
   diff empty. **Zero new regressions; none removed.**
   - Methodology note: a first attempt compared the worktree against the *main* checkout and
     showed a spurious +2 (`test_paper_run_tooling::test_readiness_check_*`). Cause: the main
     checkout has a (gitignored) `.env`, the fresh worktree does not, and those tests read
     production config. Re-running pristine-vs-edited **in the same worktree** (same missing
     `.env`) is the correct same-env comparison and is clean.

## 4. Hard stops

- "Deletes anything still referenced → STOP" — the shim **was** referenced; **stopped and
  surfaced**; proceeded only on explicit operator go (board-approved-equivalent).
- Non-byte-identical roster output → **not triggered** (no runtime logic changed).
- pytest regression → **none** (identical same-env fail/error set).
- Prod write / deploy / merge → **not performed** (operator-gated; committed unmerged).

## 5. Status

- Code + test migration committed on `polymarket-option-c-phase4-cleanup-2026-06-13`.
- Branch pushed to `origin`; **NOT merged** to main.
- **STOP — awaiting operator review.**

---

*Phase 4 cleanup artifact — committed unmerged. No prod, schema, SSH, or DB touched.*
