# Part 3 — backfill DONE + deploy runners STAGED (not run) 2026-08-26

## (c) instrument_id backfill — DONE (live DB, pure data-add)
The 34 open PEAD rows now carry `extra_json.instrument_id` (RH instrument, resolved via MCP search,
exact symbol match, all 34). So EXISTING positions auto-heal under hook 3, not just future entries.
- Pure data-add PROVEN on ADP: `ADP_COLUMNS_CHANGED=[]`, `ADP_EXTRA_JSON_KEYS_CHANGED={"instrument_id":
  [null,"a6f14684-..."]}` — only that one key added; symbol/qty/entry/stop/result/mode + every other
  extra key unchanged. `ROWS_WRITTEN=34 OPEN_TOTAL=34 NOW_HAVE_INSTRUMENT_ID=34 SKIPPED=NONE`.
- Guarded UPDATE `json_set(extra_json,'$.instrument_id',?)` WHERE order_id=? AND symbol=? AND result IS
  NULL AND instrument_id IS NULL. Evidence: `pead_backfill_out.txt` (symbol->id reconcile map, all 34).
- IA (the closed ISSC position) is NOT in the set (it exited). Note: the current 34 gained instrument_id
  by backfill; they'll also be re-persisted correctly on any future re-entry once hook 3 ships.

## (b) Part 3 deploy runners — STAGED, NOT RUN (deliberate window only)
Two runners so the ONE privileged restart is isolated and the file-deploy needs no sudo:

1. `cc\pead_part3_deploy.ps1` -> `pead_part3_deploy.sh` (files only, NO restart, NO sudo):
   - **Gate-A (hard):** box robinhood.py == `230e7807` AND pead_strategy.py == `9b9cfdad` (ABORT if
     drifted -> a MACE/other deploy landed -> rebase hook 1 over the new box, don't clobber); staged
     files == branch (`e90af223` / `fc3d6de6`).
   - **Pre-restart division-state DUMP** (open positions per division, pending_order, non-terminal
     proposed_order for mace/pmcc, RH pickle age, engine cmdline) with `GATE_FUTURES_OPEN` /
     `GATE_MACE_NONTERM` / `GATE_PMCC_NONTERM` / `GATE_PENDING_TOTAL` counters.
   - Backup both files `.bak_pre_part3_20260826`, install, md5-verify in place (auto-restore on mismatch).
   - Leaves the engine UNRESTARTED (import-time change is inert until restart).
   - **REVIEW before the deliberate run:** futures position tracking + SFP reconciler in-flight state may
     live outside `paper_trade_record` — confirm bitunix_futures/bitunix_sfp are flat/reconciled via
     their own state, and MACE/PMCC pending=0, from the dump before proceeding to restart.

2. `cc\pead_part3_restart_verify.ps1` -> `pead_part3_restart_verify.sh` (the ONE restart + verify;
   uses `sudo -n systemctl restart trading-corp` -> operator runs deliberately):
   - Confirms Part 3 files are in place, single restart, then ALL-DIVISION boot verify: per-division
     `division=X` registration counts (bitunix_sfp / robinhood_pead / bitunix_futures /
     kalshi_copy_trading / robinhood_pmcc / robinhood_mace), Traceback/FATAL scan, hook-1-live check,
     healthz. ROLLBACK = restore the two `.bak_pre_part3_20260826` + restart.

## (3) MACE coordination
Recorded in memory `robinhood-shared-part3-mace-coord-2026-08-26.md`: robinhood.py is SHARED; PEAD Part 3
builds hook 1 additively over MACE's deployed gross-BP snapshot (`230e7807`) and preserves it
(`strict` defaults False => MACE unaffected). A future MACE deploy must **Gate-A against the box, not
prod-live** (which is behind and lacks gross-BP). Whoever deploys robinhood.py next rebuilds over the
other's change; either way a full-engine restart.

## Status
Backfill applied (live). Deploy runners STAGED, NOT executed, NO restart. Awaiting your deliberate
deploy window.
