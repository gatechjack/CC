# Bitunix side of the PEAD shared-restart bundle (drop-in, read-only guards)

Bitunix contributes **ZERO restart payload** to the PEAD-only bundle. Its role is
halt → confirm flat → contribute boot-smoke/collision assertions → unhalt. These
scripts are the drop-in Bitunix-side artifacts so the window is mechanical.

## Restart sequence (agreed) — where each script slots in
| Step | Action | Script (run on prod) |
|---|---|---|
| 1 | **Halt** Bitunix entries (`auto_execute→false`, hot) | `~/fee_coupled_deploy/halt.sh` *(existing)* |
| 2 | **Confirm flat** (mc=0, open_live=0) | `bitunix_flat_confirm.sh` → must PASS |
| 3a | After PEAD's additive `strategies.yaml` edit: **collision guard** | `bitunix_preserve_check.sh <pead-backup>` → must PASS (else ABORT) |
| 3b | Combined boot-smoke vs prod config (dry) — PEAD harness | *(PEAD)* |
| 4 | Operator runs `scp` + `restart` | *(PEAD/operator)* |
| 5 | **Bitunix boot-smoke** (post-restart, pre-unhalt) | `bitunix_bootsmoke.sh` → all GREEN |
| 6 | **Unhalt** Bitunix entries (`auto_execute→true`, hot) | `~/fee_coupled_deploy/unhalt.sh` *(existing)* |

## What the guards assert (the 4 hard requirements)
- **#1 strategies.yaml** — `bitunix_preserve_check.sh`: bitunix block byte-identical
  pre/post PEAD; `taker_pct 0.00019` + `tp1_min_profit_multiplier 3.75` + `auto_execute`
  + staleness gate intact. **Exit 9 = ABORT** if the fee-coupled change was reverted.
- **#2 main.py** — `bitunix_bootsmoke.sh`: `--live-divisions` still contains
  `bitunix_futures`.
- **#3 models.py FillEvent** — `bitunix_bootsmoke.sh`: broker `paper=False`, no
  FillEvent/role/import traceback at boot, reconciler `mc=0/miss=0/orph=0`.
- **#4 metrics_epoch** — untouched by design (set `2026-06-23T01:17:17`, per-division);
  no script needed; do NOT re-activate.

## Pre-window baseline (captured 2026-06-23)
- prod PID 3355276 (NRestarts=0, active), strategies.yaml md5 **544458b2** (drift baseline),
  observer 2647fccc, **FLAT** (open_live=0, reconciler mc=0/0/0), armed (`auto_execute=true`).
- Bitunix trades normally until the window; halt happens at window time only.

## Notes
- All scripts here are **READ-ONLY** (halt/unhalt are the existing hot, no-restart
  config flips in `fee_coupled_deploy/`).
- `wr-toggle` (division.html, b2ed1d2) is independent of this bundle — hot, no restart.
- NOT YET: no halt/restart until the operator sets the window and both sides agree.
