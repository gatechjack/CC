# MACE OQ-2 + 3-active + halt button — deploy evidence archive (2026-08-13 session)

Deployed LIVE 2026-08-14 04:02-04:10 UTC (2026-08-13 night ET). prod-live `b11af9b` -> `a7ec388`,
engine 697735 -> 707835, config_hash `fe177fcd3882` -> `e9c0499886c4`, universe [IBIT, XLE, GDX].
Deploy_log entry: `runbooks/deploy_log.md` (the 2026-08-14 04:02-04:10 UTC entry, commit a7ec388).
CP4 report: `reports/2026-08-13_mace_oq2_checkpoint4.md`. Board memo:
`planning/mace_3active_oq2_board_memo_2026-08-13.md`.

## Verification artifacts
- `mace_oq2_phase4_junit.xml` — full-suite junit (3436: 3333p/91f/12e; name-diff vs golive
  baseline EMPTY both directions, 0 MACE deltas)
- `_junit_diff_oq2.py` — the junit name-diff tool used for the CP4 gate
- `mace_oq2_deploy_out.txt` / `mace_oq2_restart_out.txt` / `mace_oq2_verify_out.txt` — az
  run-command outputs: self-gated deploy (PRE/STAGE/POST + py_compile), restart (MainPID
  697735->707835), boot verify ALL GREEN (halt latch ARM->HALT->ARM PASS, SPY open=2, GLD 0)

## Deploy tooling (as-run)
- `_mace_oq2_payloadgen.py` — payload generator (HEAD-pin + clean-tree + double-manifest gates)
- `_mace_oq2_deploy_payload.sh` — the self-gated deploy payload actually executed (embedded
  tar.gz b64 of the 8 files)
- `_mace_oq2_manifest.py`, `_mace_oq2_driftgate.sh` — drift-gate tooling (prod == b11af9b proof)
- `_mace_oq2_restart.sh`, `_mace_oq2_verify.sh` — restart + boot-verify payloads
- `mace_oq2_deploy.ps1`, `mace_oq2_restart.ps1`, `mace_oq2_verify.ps1` — operator runners (spent)
- `_mace_oq2_deploylog_entry.md` — deploy_log entry draft (final text lives in runbooks/deploy_log.md)

## Blackout-calendar remediation (same session, operator-ratified)
- `_mace_cal_refresh.sh` / `mace_cal_refresh_out.txt` — CLI calendar refresh: 45 rows seeded
  (FOMC 8 / CPI 12 / NFP 12 + LPR_FIX 13), /mace panel checks pass
- `_mace_opec_add.sh` / `mace_opec_add_out.txt` — OPEC 2026-09-07 add (2026-09-06 Sunday meeting
  weekend-rolled; raw weekend rows proven INERT) + end-to-end is_blackout proof on deployed code
- `mace_cal_check_out.txt` — first read-only calendar inspection (found the table EMPTY: 0 rows,
  weekly refresh had never run — Sunday-only loop, no Sunday since MACE loops came online 08-11)

## Live operator runners — the WORKING copies STAY in C:\Users\AA Incorporado\cc (copies here for the record)
- `mace_shadow_eval_am.ps1` (+ dependency `_mace_oq2_shadow_am.sh`) — 2026-08-14 >=09:35 ET
  read-only shadow-eval confidence check
- `mace_oq2_rollback.ps1` — rollback to b11af9b via /home/azureuser/mace_oq2_bak_20260813/rollback.sh
  (ET-window-guarded restart)
- `_mace_cal_check.sh` — Monday 2026-08-17 re-run: ANY mace_calendar_refresh audit row proves the
  Sunday 08-16 weekly loop ran (the CLI seed path writes NO audit row)
