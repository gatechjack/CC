# Phase 2b CP1 report — trigger journaling (flag-2)

**Status: BUILT, NOT DEPLOYED. Checkpoint STOP — awaiting operator review before CP2.**
Branch `poly-kalshi-phase2b-cp1-2026-08-16` (off `poly-kalshi-mlb-phase1-2026-08-15`); the 2 runtime
files edited matched `origin/prod-live` at branch creation (tracing live code).

## Live-money / live-loop status (lead)
- **Zero live activity.** No order placed, no prod mutation, no restart. Branch-only; the running
  engine (PID 756639) is untouched — this deploys only on a future operator go (with CP2/CP3).
- **Shared files byte-unchanged** — `git diff origin/prod-live` on `kalshi_copy_trader.py`,
  `sports_team_mapping.py`, `kalshi_live.py` is **empty**.

## What CP1 delivers
Persists the **triggering Poly bet** (the "why") on the `poly_kalshi_order` journal row, so it
survives restart and the dashboard can bind to it per position + a copy-moment feed. Previously the
trigger lived only in the in-memory `shadow_log` (lost on restart). Two division-own files:
- **`poly_kalshi_executor.py`** (+12/−4): `submit(...)` gains a `trigger` kwarg (`:290`), threaded to
  the placed/would-place `_record` (`:344`) exactly like CP3's `fill`; `_record` gains `trigger` and
  merges it into the row (`:347`,`:360`) — additive, never raises, distinct keys from `fill`.
- **`poly_kalshi_copy_trader.py`** (+6/−2): `_pipeline` builds `trigger = {poly_slug, poly_outcome,
  poly_side, poly_market_type}` from the Poly activity row and passes it to `submit` (`:198-200`).
  The `shadow_log` in-memory behavior is unchanged (still populated).

New payload fields on the row: `poly_slug`, `poly_outcome`, `poly_side`, `poly_market_type`.

## Scope note
The trigger is journaled on the **placed / DRY_RUN_would_place** row (the final `_record`, matching
the CP3 `fill` pattern). Blocked/skip/suppressed rows do **not** carry it — they are not open
positions or copy-moments, so the dashboard (which filters `status IN (placed, DRY_RUN_would_place)`
+ `action='entry'`) is fully served. (Threading it to every `_record` call for a complete
attempt-audit is a larger change, out of the minimal flag-2 scope.)

## Evidence
- **poly_kalshi suite: 65 passed / 0 failed** (executor + copy_trader + mlb_match + reconciliation),
  incl. 4 new flag-2 tests:
  - `test_flag2_trigger_journaled_on_the_row` — the row carries all 4 poly_* fields.
  - `test_flag2_absent_trigger_is_backward_compatible` — no trigger → no `poly_*` keys (old callers unaffected).
  - `test_flag2_trigger_and_fill_coexist_on_live_row` — a LIVE placement carries BOTH the trigger and the CP3 fill.
  - `test_flag2_pipeline_journals_the_poly_trigger` — the loop wires the Poly row's slug/outcome/side/type end-to-end.
- **Shared files byte-unchanged** (empty diff vs origin/prod-live).
- Changes confined to 4 files (+78/−4); the 2 runtime files are this division's own.

## Data-contract impact
`poly_slug` / `poly_outcome` / `poly_side` move from **NEEDS-BUILD(CP1)** to **AVAILABLE** on rows
written *after this deploys*. (Pre-CP1 rows — incl. the 3 pre-CP3 fills — never carry the trigger;
they predate it, same honest caveat as CP3's fill data.)

## NOT done (do not proceed without your go)
- **CP2** — mark-to-market poller + volatile cache (bounded history / sparkline) at ~60s. Not started.
- **CP3** — broker-free dashboard read + HTMX 60s live refresh + copy-moment feed. Not started.
- Deploy — none. This CP deploys with the Phase-2b set on an operator-run runner (drift-gate + patch
  + restart); the trigger journals going forward from that restart.

## Next
Your review. On go: CP2 (mark poller + volatile cache) in a fresh worktree.
