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

═══════════════════════════════════════════════════════════════════════════════════════════════
## ★★★ DEPLOYED LIVE 2026-09-12 ~21:00Z (Board-authorized; Jack warned co-tenants + ran the restart)
═══════════════════════════════════════════════════════════════════════════════════════════════
- **STEP 1 GRAFT** (Board-authorized `pm_b1stagger_graft.ps1`): drift-gate box==base `6561b569` PASS ->
  backup `~/pm_b1stagger_backup_20260912T205615Z_live_driver.py` -> applied -> post==target
  `265397e03ff2da97` PASS -> py_compile OK -> edits present (b1=1 stagger=1 b2trap=1). Inert until restart.
- **STEP 2 RESTART** (Board-authorized canonical `restart_tc.ps1`, root az): `Enable succeeded`. Engine
  **PID 351422 -> 370246**, boot **2026-09-12 21:00:28Z**, NRestarts=0, SubState=running.
- **THREE-WAY PROVE (CR-stripped)**: worktree == branch `1b71bf10` == box == **`265397e03ff2da97`**. All agree.
- **STEP 3 SMOKE (read-only) -- GREEN**:
  - ★ STAGGER TOOK (verbatim boot log): `refresh phase offset for kalshi_jack = 0s (index_refresh_sec=900)`
    and `... kalshi_karen = 450s (index_refresh_sec=900)` -- exactly half the interval.
  - PM LIVE DRIVER WIRED (jack 17 / karen 15 categories, skipped=[]); boot-reconcile BOTH
    `reconciled=True latched=False latched_categories=()`.
  - Heartbeats: task jack 7s / karen 12s; **category heartbeats fresh(<120s) = 32 of 32**.
  - Arm intact: `arm_global=1 armed_subs=30`.
  - Tracebacks since boot = **3, all telegram/httpx network retries at 21:05:32** (known boot noise, NOT
    from the change); pm_live_driver error/fault check EMPTY; NO 'refresh-phase computation failed' (the
    stagger helper ran clean -- karen=450 is the computed value, not a fallback). `index refresh failed`=0.
  - Co-tenants: bitunix (24/7) fully recovered (ws feed / HTF / SFP observers / reconcilers clean);
    MACE/PMCC/PEAD idle = EXPECTED (17:12 ET, post equity close). Engine healthy, no crash.
  - (Self-caught measurement bug: the first smoke's traceback count was an UNBOUNDED all-history grep
    reading 28855; re-run bounded to since-boot = 3. Corrected, not a real regression.)
- **STEP 4 RE-MEASURE (+~6h, PENDING, does NOT gate the fold)**: re-run `pm_hb_gap_ro.ps1` (same 6h journal
  method). Report SEPARATELY: (a) did the **>=214s gap tier COLLAPSE** (b1) and (b) did the two accounts'
  events **stop coinciding** (stagger). ★ If the >=214s tier PERSISTS, that is the finding -- the throttle
  is NOT page-count-driven and b2 becomes inevitable (Jack decides; **b2 remains UNAUTHORIZED**).
- **STEP 5 FOLD**: origin/prod-live **`489a9ddb` -> (this branch tip)** via `git push origin <tip>:prod-live`
  (fast-forward; prod-live was an ancestor). Deploy is complete once prod-live carries it.
- **ROLLBACK if ever needed**: restore `~/pm_b1stagger_backup_20260912T205615Z_live_driver.py` + restart ->
  back to `6561b569`.
