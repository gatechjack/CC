# Phase 2a · CP4 — atomic paper<->live promote/demote + flatten/ride + 3 MUST-TESTs (BUILT, not deployed)

> **LIVE-MONEY STATUS (leads):** `poly_kalshi_mlb` remains LIVE + ARMED, engine **untouched** — no
> restart, no prod mutation, no order, **zero live-broker action** (demote deliberately touches no
> broker). CP4 is branch-only. Endpoints exist but are not reachable until the CP6 deploy.

## What was built

### Promote (paper -> live) — `roster_split.promote_whale_to_live` + endpoint
- **FLATTEN-ON-PROMOTE (reuse, no new logic):** calls the EXISTING
  `polymarket_copy_trader.force_close_whale_positions` (the same path demote-from-watch uses,
  routes.py:3033) with `reason="promoted_to_live"` — synthetic sells close the paper book at mark and it
  resets `whale_state` to fresh baseline so a future move can't replay history.
- **ONE atomic 3-key move** (`db.set_agent_state_multi`, the CP2 primitive):
  `+poly_kalshi_mlb/live_whales`, `−polymarket_copy_trader/selected_whales`,
  `−polymarket_copy_trader/pinned_whales`. Removing from **pinned** is the §1.5 fix.
- Asserts `live ∩ paper == ∅` after.

### Demote (live -> paper) — `roster_split.demote_whale_to_paper` + endpoint
- **ONE atomic 3-key move:** `−live_whales`, `+selected_whales`, `+pinned_whales` (re-pin so the whale
  is eviction-safe in paper again). Asserts the invariant after.
- **RIDE-TO-SETTLEMENT — confirmed ZERO live-broker action:** no `force_close`, no KAREN-broker call.
  An open live position rides to natural settlement. This is safe **by construction** (verified in code):
  the mark poller `poly_kalshi_marks._fetch_open_positions` (poly_kalshi_marks.py:33-63) selects OPEN
  positions purely from `audit_event` (`poly_kalshi_order` / placed / entry / unresolved) — **never reads
  a roster**; and `run_settlement_sweep` (poly_kalshi_copy_trader.py:119) books off external Kalshi
  settlements into `StrategyState('poly_kalshi_mlb')` — **never reads a roster**. Demote only stops
  NEW-entry detection.

### Endpoints (thin wrappers) — `web/routes.py`
`POST /api/polymarket/whales/promote-live/{proxy_wallet}` and `.../demote-live/{proxy_wallet}` call the
roster_split core + emit an audit + return an action pill. **NO auto-promotion** — operator-triggered only.
The deliberate **flatten (paper) / ride (live) asymmetry** is preserved end to end.

## The three MUST-TESTs — ALL PASS (`tests/test_roster_split_cp4.py`, 6/6)

- **(a) PIN-BACK ROUND-TRIP** (`test_pin_back_round_trip_invariant_every_step`): promote -> demote ->
  re-promote. Asserts `live ∩ paper == ∅` **at every step**, the whale lands in exactly ONE roster each
  time, and **no orphaned duplicates accumulate** (final: exactly 1 live entry, 0 paper).
- **(b) WEEKLY-REFRESH-DOESN'T-RE-ADD** (`test_weekly_refresh_does_not_readd_promoted_whale`) — the §1.5
  test, a **DIFFERENT trigger** than (a): promote a whale, then run the **REAL**
  `refresh_polymarket_selection` (pins-only, offline via an empty-leaderboard fake client). A still-pinned
  PAPER whale IS re-written to `selected_whales` (proves the refresh works), but the promoted LIVE whale —
  removed from `pinned_whales` by the 3-key move — is **NOT re-added**. Proves the 3-key move defeats the
  silent-re-add path.
- **(c) DEMOTE-OPEN-LIVE** (`test_demote_open_live_still_marks_and_books`): seed an OPEN live position,
  demote the whale off `live_whales`, then assert end-to-end ride-to-settlement — the position is **still
  selected** by `_fetch_open_positions`, **still MARKED** by `run_mark_cycle` (unrealized = (0.6−0.5)×10 =
  +1.0 written to `poly_kalshi_mark_live`), and a settled −$120 loss **still BOOKS** to the LIVE division
  via `run_settlement_sweep` (trips the $100 halt) — all with the whale off the roster.
- Plus basic unit tests: promote 3-key move, demote 3-key + re-pin (no broker), flatten-on-promote empties
  the whale's open paper book.

## Verification (empirical)
- **CP4 tests: 6/6 pass.** Adjacent regression (roster CP2/CP3/CP4, promote/demote routes,
  poly_kalshi copy/marks, prediction-markets dashboard): **114 pass**.
- **Zero regressions — base-vs-branch diff:** full suite on branch tip (CP2+CP3+CP4) vs pristine base
  `386074c`. Base = 92 FAILED+ERROR; **branch = 92, `comm -13` new failures = EMPTY.** No CP4 test in the
  failure set. (The async-timing flake `test_position_state_sanity_poll` did not surface this run.)
- **Shared byte-locked files** (`kalshi_copy_trader.py`, `sports_team_mapping.py`, `kalshi_live.py`):
  `git diff 386074c` → **empty**.
- **Footprint:** `roster_split.py` (wallet_of + promote/demote core), `web/routes.py` (2 thin endpoints),
  new `tests/test_roster_split_cp4.py`. Nothing else.

## Not done (by design — later checkpoints)
CP5 (paper-Telegram kill at main.py:5056 + cutover .ps1 that seeds `live_whales`), CP6 (batched
operator-run deploy + re-arm verify). The endpoints are branch-only until the CP6 deploy.
