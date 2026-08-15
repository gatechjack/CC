# MACE 6-active deploy evidence — 2026-08-14 ~23:38 UTC

Deployed LIVE: build_condor width-fallback fix (XLE/GDX/IWM) + 6-active universe enable
(FXI/IWM/SPY) + FXI ex-div seed / blackout-deferred. engine 721092 -> 722750, config_hash
e9c0499886c4 -> 5e1647f96692, universe [IBIT,XLE,GDX] -> [IBIT,XLE,GDX,FXI,IWM,SPY],
prod-live 3772d5b -> 2528aaa, main 8d5ed90 -> 2d08afc.

Deploy_log entry: `runbooks/deploy_log.md` (2026-08-14 ~23:38 UTC entry, on prod-live 2528aaa /
main 2d08afc). Memory: `mace-6active-widthfix-deploy-2026-08-14`.

## Gate evidence (as-run az run-command outputs)
- `_mace_6a_deploy_out.txt` — GATE 2/3/4-swap: PRE-gate (prod == 3772d5b base, all 3 runtime
  files OK), backup dir `/home/azureuser/mace_6active_bak_20260814_233644` + rollback.sh, py_compile,
  swap, POST-gate (== 3d1c284 target blobs: strategy fbbf6d14 / mace.yaml 1753a9c8 / exdiv 12877dc4).
- `_mace_6a_restart_out.txt` — GATE 4-restart: ET re-assert (server 19:38 EDT, outside 15:40-15:58),
  MainPID 721092 -> 722750, 0 tracebacks, `config_hash=5e1647f96692` logged at boot.
- `_mace_6a_verify_out.txt` — GATE 5 boot verify ALL GREEN: /mace 200 6-active, 2 SPY W33 rungs intact
  (`('SPY','open',2)`), halt latch ARM->HALT->ARM (2x halt + 2x arm audit), FXI enabled+guard+dates+
  blackout=() no fail-closed, divisions healthy. (Cross-checked against the operator's /mace screenshot.)
- `_mace_6active_fullsuite2.txt` — full-suite gate: 90 failed / 12 error, ZERO MACE / ex-div fails
  (failure set identical to the pre-6active branch; all pre-existing non-MACE).

## As-run tooling (drift-gate/deploy/verify .sh were generated + removed post-deploy; runners kept)
- `_mace_6a_restart.sh` — the restart + boot-capture payload (as-run).
- `mace_6a_rollback.ps1` — the ROLLBACK runner (invokes the server-side rollback.sh; restores 3772d5b,
  restarts, ET-guarded). The WORKING copy stays in `C:\Users\AA Incorporado\cc\`; this is the archived copy.

## Rollback
`powershell -ep bypass -f .\mace_6a_rollback.ps1` (from cc) -> `bash
/home/azureuser/mace_6active_bak_20260814_233644/rollback.sh` — restores the 3 runtime files to the
3772d5b md5s + restarts (refuses inside 15:35-16:00 ET). Config-only rollback: revert universe/enables
+ restart. Hot kill-switches: auto_execute:false, standby:true, UI halt button.
