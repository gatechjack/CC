# SETTLEMENT-CLOSE PATH -- PHASE 3 DEPLOY + PHASE 4 RECORD (2026-10-08)

Build: branch pm-settlement-close-2026-10-08 @ 403d774a (off prod-live 074ef365). settlement.py only,
BASE a1abe0e3b04a85e4 -> TARGET a0eb5a437166b875. Design: PM_SETTLEMENT_CLOSE_DESIGN_2026-10-08.md.

## 3.1 gate (RO, before apply)
Time 11:47Z (~07:47 ET, pre-market window OK). Reconcile 0 DIFFS all 4 (jack 15/15, karen 13/13, marc 5/5,
trey 5/5). Box settlement.py == BASE a1abe0e3. APPLY GATE OPEN.

## 3.1 apply (settlement.py only, no restart) -- clean
scp binary-exact -> drift-gate (box==BASE / staged==TARGET) -> backup
/home/azureuser/pm_settclose_backup_20261008T114934Z.settlement.py (11,870 B, left in place) -> apply
CR-stripped -> live==TARGET a0eb5a43 -> py_compile+import clean -> no rollback. Engine unchanged at apply
(PID 664274), file mtime 11:49:34Z.

## 3.2 restart (Jack ran restart_tc.ps1) + boot-verify
MainPID 664274 -> 675073, NRestarts 0, ActiveEnter 2026-10-08 12:13:48Z (POSTDATES the 11:49:34Z file mtime ->
running the NEW code, not just on-disk). box settlement.py == TARGET a0eb5a43. boot_reconcile reconciled=True
latched=False on ALL FOUR (marc/karen/trey 12:14:22, jack 12:14:24) -- the scalar booking held through a restart
with the new code live, no re-latch. Arm survived: arm:global armed, 57 armed, 35 latched (the deliberately-off
set). Divisions: MACE wired (4 loops) / PMCC loop / PEAD online / bitunix reconcilers up. No Traceback, no
NON-STANDARD WARN. (bitunix flagged a pre-existing 1-orphan divergence -> its own entry-halt; UNRELATED to this
PM change.)

## 3.3 first scan vs PREDICTION -> MATCH
PREDICTION (written before): first scan books NOTHING via the new branch (today all {yes,no}; both scalar tickers
flat -> skipped_flat); expected n_nonstandard=0. RESULT: new-branch rows since restart = 0; only 3 normal
'settlement' closes; no NON-STANDARD WARN. Fills flowing (karen 2 / marc 1 / trey 1 in-window, pre-market).

## 3.4 FF (Jack's; the deploy is NOT complete until prod-live carries it)
Box runs TARGET but git prod-live is still 074ef365 (settlement.py a1abe0e3) -> box-ahead drift until FF.
  git push origin 403d774a:prod-live
  git tag pm-settlement-close-deploy-2026-10-08 403d774a
  git push origin pm-settlement-close-deploy-2026-10-08

## Phase 4 -- HONEST RECORD
PROVEN BY TEST (6/6) + BY CONSTRUCTION + BY A CLEAN RESTART with the new code live -- **NOT YET BY A REAL SCALAR
SETTLEMENT.** All 6 scalars in history are already booked; the live non-binary branch cannot be exercised until a
NEW scalar/refund/void settlement occurs. What would confirm it: a future non-binary settlement booking
automatically (close_source=settlement_<class>, won=NULL, net flat, a WARN) with boot_reconcile staying clean.
STANDING WATCH (daily): any settlement with market_result outside {yes,no,void}, booked or not; any
n_nonstandard>0 or a settlement_<nonstandard> close_source row. That is the only signal that confirms the live path.
CARRY-FORWARD unchanged (settlement-close path is now BUILT; items 2-9 remain): preserve prior armed state in the
latch; account-wide latch blast radius; widen settled-rollup close_source filters (now incl settlement_scalar);
STOP reading `revenue` in the proceeds cross-check (the 22/day WARN); fund marc+trey; 429 storm; pm_open_position
stale; NULL caps jack boxing/f1; the 33 disarmed subs.
Runners (RO + the guarded graft): pm_p0, pm_p1enum, pm_c1scalars, pm_p3recon, pm_settclose_graft, pm_p3verify.
