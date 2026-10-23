# MACE durability fix — DEPLOY RUNBOOK (RESERVED for Jack; box-only graft + restart)

NOT executed by the agent. Box-only per standing MACE ruling (no prod-live FF required to run; a prod-live
ledger fold can follow separately). config_hash is UNCHANGED (optional-with-defaults) → **no mace.yaml edit**.

## Files to graft (5; all from worktree `cc-mace-durability-fix-2026-10-23-wt`)
| file | scope |
|---|---|
| trading_corp/mace/config.py | MACE |
| trading_corp/mace/execution.py | MACE |
| trading_corp/mace/manager.py | MACE |
| trading_corp/mace/loops.py | MACE |
| trading_corp/main.py | **SHARED** (3 additive MACE-task lines only) |

## Procedure (reuse the proven scp+tar graft shape — pm_itf_graft.{ps1,sh})
1. **Drift-gate**: on the box, CR-stripped md5 of the 5 target files == the base this branch was built on
   (strategy/domain untouched; the 4 mace files + main.py are the base's current bytes). ABORT on any mismatch.
2. **Stage binary-exact**: `tar -czf` the 5 files in the worktree → `scp` → `tar xzf` into `/tmp/stage` on the box
   (NEVER Get-Content-stream — these files carry non-ASCII em-dashes).
3. **Backup**: copy the 5 live files to `/home/azureuser/mace_durfix_predeploy_backup_<ts>/` (revert path).
4. **Apply**: `cp "$STAGE/$rel" "$ROOT/$rel"` for each; re-verify CR-stripped md5 of each applied file ==
   the staged/worktree md5 (the worktree CR-stripped md5s: config `4a47188b` / execution `71f1a828` /
   manager `0116e860` / loops `765e3360` / main `f486de42`).
5. **Syntax gate**: `venv/bin/python -m py_compile` the 5 files. ABORT (and roll back from step-3 backup) on fail.
6. **Restart** (Jack's reserved action): `powershell -ep bypass -f "C:\Users\AA Incorporado\Desktop\restart_tc.ps1"`
   (bounces ALL divisions ~3.5 min — coordinate with the PEAD + PM agents; not near an equity open).
7. **Boot-verify (RO)**: new PID / NRestarts; `config_hash 931a8214be50` still logged; the 5 CR-stripped md5s ==
   worktree; journald shows `MACE watchdog scheduler online` (the new loop) + the 4 existing schedulers online;
   confirm `mace_manage_timeout` / `mace_manage_stall` are WIRED (grep the audit kinds) and no boot tracebacks.
8. **Live-verify (next market tick)**: `agent_state robinhood_mace/manage_tick_heartbeat` advances each tick;
   watchdog stays quiet during healthy ticks.

## Rollback
`cp` the 5 files back from the step-3 backup dir → `py_compile` → restart. (Pure code revert; config_hash never moved.)

## Optional later tuning (SEPARATE change — MOVES config_hash)
Add to `config/mace.yaml` under `management:` to override the defaults — this changes the sha256 → config_hash
moves (flag) and needs its own restart:
```yaml
  manage_tick_timeout_sec: 600
  leg_quote_timeout_sec: 30
  watchdog:
    enabled: true
    poll_interval_sec: 60
    stall_alert_sec: 900
    marks_dead_alert_sec: 900
```
