# GROUP 2 DEPLOY MANIFEST -- attachment span history (migration 024) -- 2026-09-15

Branch `pm-attach-span-g2-2026-09-15 @ (tip)` off `origin/prod-live ecb4791e` (pushed). **pm_web + pm_cli-owned;
MIGRATION 024 (additive); ONE pm_web restart; NO engine restart.** 31 sub-divisions stay armed/trading.

## What ships (4 runtime files + 1 migration; tests repo-only)
- `trading_corp/prediction_markets/db.py` -- MIGRATION_024 `pm_subdivision_attachment_event` (append-only) + head 23->24.
- `trading_corp/prediction_markets/farm_actions.py` -- `promote_to_live`/`detach_from_live` write the attach/detach
  event ATOMICALLY (both now wrap BEGIN IMMEDIATE...COMMIT); NEW `read_attachment_events` reader; `actor` param.
- `trading_corp/prediction_markets/web/app.py` -- threads `actor=current_identity(request)` through `_promote_live`/
  `_detach_live`. ★ COLLIDES with Group 1's app.py (see DEPLOY ORDERING).
- `trading_corp/scripts/pm_cli.py` -- passes `actor='cli'`.

## The change (item 2)
`pm_subdivision_attachment` holds ONE row per (account,category,wallet); a re-attach reactivates it (keeps the
original `added_ts`, clears `removed_ts`) so the earlier span's DATES are lost. The append-only event log records
every attach/detach -> full span history recoverable (pair each `attach` with the next `detach`; a trailing
`attach` = the current open span). ENGINE-NEUTRAL: the driver's roster read is `pm_subdivision_attachment WHERE
active=1`; it NEVER reads the event table (verified by grep across trading_corp/ -- referenced only in db.py +
farm_actions.py). farm_actions is the SINGLE writer of the attachment table (verified: exactly 2 writes).

## Verification (box-scratch, isolated, live tree sha unchanged)
- IMPORT-CLOSURE: farm_actions imports NO broker/pykalshi.
- `test_attachment_span_history.py` 8/8: mig-024 at head; attach event; repeat writes none; **detach->re-attach
  records two spans the single row can't**; no-op detach silent; **driver roster query unaffected**; **detach
  ATOMICITY (injected event-INSERT failure rolls back the active=0 flip)**; reader.
- `test_rung3_observability.py` 11/11 (head pin bumped 23->24, contiguous).
- DIFFERENTIAL vs clean prod-live: **0 NEW failures** (same 21 pre-existing; 398->406 passed).
- 2 adversarial skeptics: the one real finding (detach non-atomicity) FIXED + tested; engine-neutrality,
  single-choke-point, migration-safety, actor-arity all verified clean.

## ★★ DEPLOY ORDERING -- G2 GRAFTS **AFTER** G1 LANDS (shared app.py)
Both G1 and G2 edit `web/app.py`. G1 is already grafted to the box (app.py sha `55beb8a3`); G2's worktree app.py is
off `ecb4791e` (base `808123ac`) + G2's actor edits. The two edits are NON-OVERLAPPING hunks (G1: import + tile
filter ~L36/682/1239; G2: actor threading ~L798/876/904/1168) -> a clean rebase. But a blind blob-overwrite graft
of G2's app.py would REVERT G1's tile filter. THEREFORE:
1. G1 must FF to prod-live FIRST (Jack: pm_web restart + FF). prod-live advances to G1's tip.
2. REBASE G2 onto the G1-advanced prod-live (`git rebase <g1-tip>`); the app.py rebase is clean (non-overlapping).
   The rebased app.py = G1's tile filter + G2's actor threading.
3. THEN build/finalize the G2 graft (base = the G1-advanced app.py sha; targets recomputed off the rebase) and
   deploy. The graft runner + final shas are produced at THIS point (they depend on G1's landed app.py).
The other 3 files (db.py, farm_actions.py, pm_cli.py) do NOT collide with G1 -- their bases are the ecb4791e shas.

## DEPLOY SEQUENCE (after G1 lands; each box step halts for board authorization)
- **STEP 0 -- REBASE** G2 onto G1's prod-live tip (clean; re-run the box-scratch to re-confirm 0-new post-rebase).
- **STEP 1 -- GRAFT** (drift-gated scp+tar, box==rebased-base on all 4 files, backup, apply, re-verify target,
  py_compile, rollback-on-mismatch). Built at STEP 0 with the rebased shas.
- **STEP 2 -- MIGRATION 024** self-applies via `db.init_db` from the pm_cli crons (STOP-if-head!=23 drift-check;
  expect the cron to win the race -- same as mig-023). Additive CREATE-only; the engine (old db.py in memory)
  is backward-compatible with a head-24 DB (it never reads the new table).
- **STEP 3 -- RESTART pm_web** (RESERVED -- Jack; az-root) -> loads the new db.py/farm_actions/app.py. Engine untouched.
- **STEP 4 -- POST-CHECK** (read-only): head==24; an attach + a detach via the CLI write one event each (two spans
  on a re-attach); driver roster query byte-unchanged; engine PID == pre-deploy; no new pm_web tracebacks.
- **STEP 5 -- FOLD** to prod-live (RESERVED -- Jack): FF + ledger.

## STOP CONDITIONS / ROLLBACK
- Rebase conflict on app.py (should be clean) -> STOP, resolve, re-review before graft.
- Graft drift/mismatch -> abort + rollback to base (box left consistent).
- Post-check: head != 24, engine PID changed, driver roster query changed, or new tracebacks -> rollback backup +
  restart pm_web. (Migration 024 is additive; a rollback of the CODE leaves the empty table harmlessly present.)
