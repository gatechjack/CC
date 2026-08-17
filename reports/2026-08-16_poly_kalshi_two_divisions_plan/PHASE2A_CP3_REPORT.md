# Phase 2a · CP3 — split made real: config retarget + paper read-time subtract + boot invariant (BUILT, not deployed)

> **LIVE-MONEY STATUS (leads):** `poly_kalshi_mlb` remains LIVE + ARMED, engine **untouched** — no
> restart, no prod mutation, no order. CP3 is branch-only. The running live loop keeps reading its
> CURRENT roster (`polymarket_copy_trader/selected_whales`) until the CP6 deploy; the config retarget
> below is inert on the box until then.

## ⚠️ Ordering understood — CP3 does NOT deploy standalone
The config retarget points the live loop at `poly_kalshi_mlb/live_whales`, which is **EMPTY until CP5's
cutover .ps1 seeds it**. Deploying CP3 alone would leave the live loop watching nobody. CP3 ships in the
CP6 batch, and CP5's cutover (seed `live_whales` + remove the 4 from `selected_whales`/`pinned_whales`)
runs **right before** the CP6 restart. Confirmed.

## What was built

### 1. Live-loop retarget (config-only) — `config/strategies.yaml`
`poly_kalshi_mlb.roster_actor: poly_kalshi_mlb` / `roster_key: live_whales` (was
`polymarket_copy_trader` / `selected_whales`). **No live-loop code change** — main.py:1513-1514 passes
`roster_actor`/`roster_key` straight from the yaml into `PolyKalshiCopyTrader`, whose `_load_roster`
reads `load_agent_state(roster_actor, roster_key)`. Proof it now targets the new key:
`test_config_retargets_live_loop_to_live_whales` asserts the yaml values. Stale comment at
strategies.yaml:1779-1780 updated.

### 2. Paper-sim read-time subtract — `polymarket_copy_trader.py`
- New `_load_live_whale_wallets()` reads `poly_kalshi_mlb/live_whales` via the shared
  `roster_split.extract_wallets` (identical wallet identity, lowercased). Read failure → empty set (no
  subtract), never crashes the scan.
- In `run_scan_cycle`, **after** `_apply_autopause_filter` and **before** the copy loop, any whale whose
  wallet is in `live_whales` is filtered out of the iterated list. Applied at the copy-CONSUMPTION point
  ONLY — the stored `selected_whales` roster is untouched, so `_apply_autopause_filter`'s read-modify-write
  still operates on the raw roster (no behavior change there).
- **This is the §1.5 backstop:** even if the atomic 3-key move missed a key, or the weekly pins-only
  refresh re-added a live whale to `selected_whales`, the paper sim still never papers a live whale.

### 3. Boot invariant — `roster_split.assert_roster_invariant_boot` wired in `main.py`
- New `assert_roster_invariant_boot(db_url, *, logger)` (`roster_split.py`) calls CP2's
  `check_rosters_disjoint`, **NEVER raises**, returns True/False.
- Wired at `main.py` right after the poly_kalshi wiring block (guarded; helper never raises).

**Failure mode CHOSEN: log-loud-and-CONTINUE (not hard-fail).** Rationale (argued):
- The engine is ONE process hosting many divisions (MACE/PEAD/PMCC/bitunix/kalshi/poly_kalshi).
  Hard-failing boot over a roster-bookkeeping overlap would take down every unrelated division —
  disproportionate blast radius.
- The overlap **cannot itself cause a double-COPY**: the live loop reads only `live_whales`; the paper
  sim read-time-subtracts `live_whales`. So a live whale is never papered even when stored state is
  dirty. The boot check is therefore **detection + alerting**, not the primary guard.
- ⇒ It logs LOUD (`error`, "POLY_KALSHI ROSTER INVARIANT VIOLATED at boot …") for the operator to
  reconcile, and lets the engine come up healthy. (Option for a later CP: also push a Telegram on
  violation — deferred to keep CP3 tight and avoid an async failure surface at boot.)

## Verification (empirical)
- **CP3 tests** (`tests/test_roster_split_cp3.py`, 7/7 pass):
  - `test_paper_sim_excludes_live_whale` — whale in BOTH `selected_whales` and `live_whales` is **NOT**
    papered (recording stub proves the live wallet is never fetched); case-insensitive (`0xLIVE`
    excluded by `0xlive`).
  - `test_paper_sim_papers_all_when_live_roster_empty` — no live roster ⇒ no subtract ⇒ all papered.
  - `test_paper_sim_noop_when_all_selected_are_live` — clean no-op, nobody fetched.
  - `test_boot_invariant_disjoint_returns_true` / `..._overlap_logs_loud_and_continues` (returns False,
    logs error, **no raise**) / `..._read_error_is_non_blocking` (returns True on a bad db_url).
  - `test_config_retargets_live_loop_to_live_whales` — yaml points at `poly_kalshi_mlb/live_whales`.
- **Zero regressions — base-vs-branch diff (environment-independent):** full suite on branch tip
  (CP2+CP3) vs pristine base `386074c`. Base = 92 FAILED+ERROR; **clean branch run = 92, `comm -13` new
  failures = EMPTY.** A first branch run flagged one extra failure
  (`test_position_state_sanity_poll.py::test_loop_runs_multiple_ticks_under_normal_state`); it **passes
  in isolation**, did **not** recur on a re-run, and is an async-timing loop in the bitunix reconciler
  entirely unrelated to my surface — a timing flake, not a CP3 regression. The 3 pre-existing
  `test_polymarket_copy_trader` sizing failures (`0.9996 == 1.0`) are present on base too (environmental
  float drift under local Python 3.14), unchanged by CP3.
- **Shared byte-locked files** (`kalshi_copy_trader.py`, `sports_team_mapping.py`, `kalshi_live.py`):
  `git diff 386074c` → **empty**.
- **Footprint:** `config/strategies.yaml`, `polymarket_copy_trader.py`, `roster_split.py` (added boot
  helper), `main.py` (boot wiring), new `tests/test_roster_split_cp3.py`. Nothing else.

## Not done (by design — later checkpoints)
CP4 (atomic paper⇄live promote/demote endpoints + flatten-on-promote + pin-back & demote-open-live
MUST-TESTs), CP5 (paper-Telegram kill at main.py:5056 + cutover .ps1 that seeds `live_whales`), CP6
(batched operator-run deploy + re-arm verify). No promote/demote endpoints and no cutover exist yet.
