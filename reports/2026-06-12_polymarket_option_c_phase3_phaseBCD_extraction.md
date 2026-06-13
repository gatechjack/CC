# Polymarket option (c) Phase 3 — Phase B/C/D: Option 1 small extraction

**Date:** 2026-06-12
**Branch:** `polymarket-option-c-phase3-unify-2026-06-11` (base `main` `b1e4150`; Phase 1 `b137c03` + Phase 2 `1c0b52e` merged; Phase A artifact `1a7a996`)
**Code commit:** `41ca5b9` (refactor + equivalence test)
**Mode:** local worktree only. No prod access, no SSH, no DB writes, no deploy, no merge (disclosure per `82fda13`).
**Outcome:** Option 1 extraction implemented + proven byte-identical. **Committed UNMERGED. STOP for operator review.**

---

## 1. What this is

Phase A (`reports/2026-06-11_..._phaseA_duplication_map.md`, commit `1a7a996`)
found the original "both scripts duplicate the whole pipeline" premise
**overstated** — post-Phase-1/2 the compute (`build_audit_report`), scorer
(`score_whale_from_audit`), and paginated walk (`_fetch_wallet_activity_windowed`)
were already single-source shared. It stopped at a scope fork (hard-stop #4).
**Operator chose Option 1** (the small, byte-identical extraction of §5). This
is its implementation (Phase B), proof (Phase C), and commit (Phase D).

## 2. Change set (Phase B)

New module **`trading_corp/data/whale_screening.py`** (the shared
activity-acquisition layer):

- **`_fetch_wallet_activity_windowed` — MOVED here** (verbatim) from
  `seed_polymarket_watchlist_deep`, where it was *defined* and *imported from*
  by `refresh_polymarket_whales`. `seed` now **re-exports** it
  (`from ...whale_screening import _fetch_wallet_activity_windowed`) so every
  existing import — including `tests/test_refresh_polymarket_whales.py` and
  `tests/test_polymarket_watchlist_seed.py`, which import it *from seed* and
  call it directly — keeps resolving to the same object. **This removes the
  `refresh -> seed` script-imports-script coupling** (the real structural win):
  `refresh` no longer imports anything from `seed`.
- **`fetch_activity_window_for_candidates`** — the ~30-line per-candidate loop
  wrapper (the one genuine multi-line dup, refresh 218–258 / seed 514–567).
  Returns `(activity_by_wallet, truncated_by_wallet, all_condition_ids)`. The
  two caller behavior deltas are **PARAMETERIZED**, not erased:
  - `broad_catch=True` → `refresh`'s broad `except Exception` wrapper around the
    walk (broader than the walk's own `PolymarketDataAPIError` catch) + its
    per-wallet warning. `seed` passes `broad_catch=False`.
  - `on_termination(wallet, reason, acts)` → `seed`'s per-wallet
    `termination_reasons` + `with_activity` telemetry. `refresh` passes `None`.

Both callers rewired to call the helper. The post-loop `n_truncated` count + its
`log.warning` (whose **wording differs per caller**) stays in each caller, as do
all legitimately-different stages (leaderboard fetch/dedup, audit-input prep,
scoring invocation, selection/output — Phase A §4).

**Left untouched / out of scope:** the audit cache. Folding it into the shared
layer would collide `seed`'s windowed report with `refresh`'s full-window report
for the same `(wallet, activity_max_ts)` key (the key is scope-blind) — a
behavior change, not a byte-identical refactor. Filed as a new **P3 backlog
item** ("Polymarket audit-cache unification deferred … collision risk"). Old
per-script paths kept importable (no deletions — Phase 4 cleanup remains
separate/out of scope).

## 3. Byte-identical evidence (Phase C)

The mandate is "refactor, change nothing." Three independent layers prove it:

1. **Surgical equivalence test** — `tests/test_whale_screening_equivalence.py`
   (5 tests, all pass). It embeds **verbatim inline copies of each caller's
   PRE-refactor loop** as a golden reference, drives the same fixture through
   both the reference and the new helper, and asserts the outputs
   `(activity_by_wallet, truncated_by_wallet, all_condition_ids)` **plus** seed's
   `termination_reasons` + `with_activity` telemetry are identical. Coverage:
   every termination path — `exhausted` (partial + empty page), `max_pages_hit`,
   `target_buys_reached`, `fetch_error` (`PolymarketDataAPIError` mid-walk), and
   `refresh`'s `broad_catch` (a non-`PolymarketDataAPIError` `ValueError` that
   the walk does *not* catch). A 6th assertion pins the re-export identity
   (`seed._fetch_wallet_activity_windowed is whale_screening._fetch_wallet_activity_windowed`).

2. **Existing Phase-1/2 caller suites pass UNCHANGED** — 49 tests across
   `tests/test_refresh_polymarket_whales.py` (pinned-merge survives,
   inflation-gate-excludes on the copy roster, truncation containment, dry-run
   read-only, straddle reconstruction) and `tests/test_polymarket_watchlist_seed.py`
   (flag-only on the observation roster, fetch-error → window_truncated, floors,
   provisional, output-contract field set). These pin the exact downstream
   scores/ranks/flags/gating; passing unchanged is the end-to-end confirmation.

3. **Full pytest gate vs pristine base, SAME env** — ran the whole suite on the
   branch and on the stashed pristine base (`1a7a996`, refactor reverted) on this
   box with `--continue-on-collection-errors`. The FAILED+ERROR set is
   **identical**: the same **31 pre-existing** failures/errors, all in unrelated
   subsystems (bitunix collection-import errors ×3, robinhood_multi_leg ×15,
   tasty_options_iron_condor ×3, iron_condor_strategy ×3, paper_run_tooling ×2,
   webhooks_return_fast ×5) — **none Polymarket/whale; zero new; none removed.**
   (Per the D1-session convention, this box's env differs from the prod baseline,
   so the comparison is branch-vs-pristine-base in the same env, not the prod
   number. This pytest build emits no final tally line; the proof is the
   identical fail/error set diff, plus the 54 Polymarket/whale tests all green.)

## 4. Hard stops — none triggered

- Non-byte-identical roster output vs Phase-1/2 → **not triggered** (proven identical).
- Prod write / deploy / merge → **not performed** (operator-gated; committed unmerged).
- Bundling the cache unification → **not done** (filed as backlog instead).
- pytest regression → **none** (identical fail/error set).

## 5. Status

- Code + equivalence test committed: `41ca5b9` (unmerged on the branch).
- Docs (this report + the BACKLOG cache-unification P3 entry) committed alongside.
- Branch pushed to `origin` for review; **NOT merged** to main.
- **STOP — awaiting operator review.** Phase 4 cleanup (deleting the old
  per-script paths) and any merge/deploy remain operator-gated and out of scope.

---

*Phase B/C/D artifact — committed unmerged on
`polymarket-option-c-phase3-unify-2026-06-11`. No prod, schema, SSH, or DB
touched.*
