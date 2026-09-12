# DEPLOY MANIFEST -- b1 (2-day settled lookback) + refresh STAGGER -- 2026-09-12

## ★★★ TOP-OF-MANIFEST WARNING -- THIS RESTART BOUNCES EVERY DIVISION
This is ENGINE work (`trading_corp/prediction_markets/live_driver.py`). Taking effect needs a
`trading-corp.service` restart, which **bounces ALL co-tenant divisions** -- MACE, PMCC, PEAD,
bitunix (24/7), coinbase, iron condors -- for the ~3.5 min boot. **30 PM sub-divisions are ARMED
and TRADING throughout.** Warn co-tenants before the restart. The 30 subs re-arm from persisted arm
state on boot (arm persists across restart); boot-reconcile runs; open positions ride to settlement.

## What ships
- **ONE file**: `trading_corp/prediction_markets/live_driver.py`.
  - base (deployed, box-confirmed) CR-sha256(16) = `6561b569316f6cdc`
  - target (this branch)        CR-sha256(16) = `265397e03ff2da97`
- **NOT shipped** (repo-only): the test file + these docs (tests do not run in the engine).
- **NO schema/migration, NO DB write, NO arm change, NO shared-trio (main.py) change** -- PM-only,
  single-file graft. The graft can only break PM's driver, never MACE/PMCC/PEAD/bitunix boot.

## The change (see HEARTBEAT_STALL_INVESTIGATION_2026-09-12.md for the measurement + trace)
1. **b1**: `_SETTLED_LOOKBACK_SEC` 160d -> 2d (+ rationale comment). Shrinks the MLB settled fetch
   ~20k->~1k markets / ~19->~3 pages -- the dominant Kalshi GET burst that tripped shared-IP
   rate-limit backoff into ~200s whole-account stalls (~6x/6h). Settled ctx is functionally inert
   (traced); OPEN fetch is date-unbounded (unaffected).
2. **STAGGER**: `_account_refresh_phase_sec` ranks the account over the DRIVER ROSTER
   (`active_driver_subdivisions`) -> `rank*interval/N` (jack->0s, karen->450s), seeded at boot as
   `last_idx = time - phase`. Deterministic + restart-stable -> the two accounts refresh OUT OF
   PHASE, so a throttle window hits ONE account at a time (halves per-event blast radius,
   independent of b1).
3. **b2-trap comment** at the refresh await: any durable fix must unblock the LOOP not the BEAT, and
   stay OFF the placement path (M1 account-cap invariant).

## Pre-deploy proof (done, read-only, box-scratch -- engine 351422 untouched)
- py_compile OK; 9 new tests pass (b1 min_close_ts + OPEN-unbounded; stagger incl orphan-exclusion;
  ctx-pagination regression).
- Full `tests/prediction_markets` suite: the SAME 21 pre-existing UI-render failures on the DEPLOYED
  baseline AND this branch -> **0 new regressions** (the 21 are pm_web nav-string drift, unrelated
  to the engine driver).
- Adversarial review: finding #1 (population ranked over the roster, not raw pm_account) FIXED;
  other findings NON-ISSUE / documented.

## DEPLOY SEQUENCE (each box step HALTS for Jack's authorization)
- **STEP 1 -- GRAFT** (`pm_b1stagger_graft.ps1`): scp the edited file to /tmp, drift-gate box==base
  `6561b569`, backup the deployed file, apply, re-verify post-sha==target `265397e0`, py_compile,
  confirm the two edits present. Aborts + restores on any mismatch. **Inert until restart** (the
  running PID 351422 keeps executing the old in-memory code until the process restarts).
- **STEP 2 -- RESTART** (Jack's canonical `C:\Users\AA Incorporado\Desktop\restart_tc.ps1` =
  `az vm run-command ... systemctl restart trading-corp`, runs as root). Bounces all divisions.
  Jack warns co-tenants + times it. This is when the new code loads and the 30 subs re-arm.
- **STEP 3 -- POST-RESTART SMOKE** (`pm_b1stagger_smoke_ro.ps1`, read-only): new PID + boot time;
  the phase-offset LOG line (expect kalshi_jack=0s, kalshi_karen=450s) == **the stagger took**;
  all PM categories cycling (fresh heartbeats) + all co-tenant divisions alive; 30 subs armed +
  global armed; 0 latched; boot-reconcile reconciled=True; no PM tracebacks.
- **STEP 4 -- RE-MEASURE (+~6h)** (`pm_hb_gap_ro.ps1`, the SAME retrospective 6h journal measurement):
  ACCEPTANCE = the **>=214s gap tier COLLAPSES** (b1 worked); reported SEPARATELY, the two accounts'
  events **no longer coincide** (stagger took). If the >=214s tier persists, that is the finding --
  the throttle is not page-count-driven and b2 becomes inevitable (Jack decides; b2 NOT authorized).
- **STEP 5 -- FOLD to prod-live** (same session): three-way prove box==prod-live(new)==branch on
  live_driver.py CR-sha; FF `git push origin 1b71bf10:prod-live` (linear descendant of 489a9ddb);
  ledger commit (this manifest + the deploy record).

## Rollback
- Pre-restart: the graft leaves `~/pm_b1stagger_backup_<ts>_live_driver.py`; `cp` it back = revert
  the deployed file (then no restart needed, since the old code is still running).
- Post-restart: restore the backup file + restart -> back to `6561b569`.

## Refs
- Branch `pm-heartbeat-stall-invest-2026-09-12` @ `1b71bf10` (code) on prod-live `489a9ddb`.
- Investigation: `reports/prediction_markets/HEARTBEAT_STALL_INVESTIGATION_2026-09-12.md`.
