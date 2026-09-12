# MACE #2 GDX time-exit/PT cap — DEPLOYED box-only + boot-verify GREEN (2026-09-11)

DEPLOYED + VERIFIED. BOX-ONLY (NO FF-push; git reconcile deferred). Branch mace-gdx-cap-boxbase-2026-09-11 @ c2593e34
(base box-truth 7683f59b = deployed #1). ★ The box-truth for MACE files is now c2593e34.

## Sequence (board-run, post-close)
- PRE-GATE: GREEN (baseline 2 / modified 2 / NEW 0; 6 GDX-cap tests pass). Box-scratch, engine untouched.
- BACKUP: ~/gdxcap_backup_20260911T224928Z + gdxcap_rollback_20260911T224928Z.sh (md5-verified).
- GRAFT: box==7683f59b base (OK all 4) -> wrote 4 -> box==c2593e34 (OK all 4). FORK-PRESERVE OK
  (mace_missed_exit=1, exit_disposition_line=4). MACE-scoped: only config/mace.yaml + mace/{config,execution,manager}.py.
- RESTART (Jack): PID 344155 -> 346127, start 2026-09-11 22:51:14Z.
- BOOT-VERIFY: GREEN. config_hash 49476b0e -> bfde856f (engine logged config_hash=bfde856f1c46, 4 loops online).
  execution.py==073b6eed, winner branch live, #1 fork preserved. Shared trio UNCHANGED (main cbf4b928 / data_exec
  fc8ab253 / robinhood a26b8d0c == #1-deployed). rungs 9 open/9 closed/3 abandoned; MACE/PEAD/PMCC/PM healthy; 0 tracebacks.

## Fix now LIVE
Winner (TIME>14 / PT) close prices at MID capped at mid+exit_winner_band(0.10), never crosses the spread; STOP
natural (unchanged); TIME defers unfilled -> forces natural at time_exit_defer_floor_dte(14); PT defers no-floor.
GDX-style closes will no longer give ~$50 to the spread. New config_hash bfde856f1c468d11.

## Deferred / next
- NO FF-push (prod-live 7220e32f divergent; git-truth reconcile is its own session after PM).
- ★ PM deploys LAST, rebased onto c2593e34 (the FINAL MACE box-truth), NOT 7683f59b/7220e32f.
