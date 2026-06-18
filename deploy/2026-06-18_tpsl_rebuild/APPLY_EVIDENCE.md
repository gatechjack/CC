# Apply evidence — tpsl-rebuild deploy (2026-06-18, ~22:52 UTC)

Agent-driven over SSH (azureuser-owned files, no sudo). §4 lift active for this sequence. NO restart performed.

## Step 1 — Stage: PASS
scp 3 LF files → `/home/azureuser/trading_corp/_tpsl_rebuild_stage/trading_corp/...`. Staged md5 == target:
- brokers/bitunix.py = 74aa1b424dcb73840f9f636151098348
- observer = 19da15ff4401996ba31e50cf6f3d59a0
- reconciler = 707c682858f40245d06aee9dc8f94e00
Exactly 3 files staged (no stray files).

## Step 2 — Apply: PASS (all gates)
staged==target → preflight py_compile → drift guard prod==base → backup `*.bak-pre-tpsl-rebuild-2026-06-18` → md5-gated atomic-mv → re-verify → final py_compile. All OK.

Post-apply independent verify:
- 3 live files md5 == target (74aa1b42 / 19da15ff / 707c6828).
- backups present for all 3.
- main.py = f16e9c24f81e65c9eb9d98019eea4e23 (UNCHANGED), db.py = a2c2ff46b89ec3d30640552db19b962c (UNCHANGED).

## Pre-restart flat re-confirm: FLAT
- `position` COUNT(*) = 0.
- last bitunix_futures entry 2026-06-18T15:18:56 (filled); none since; no bitunix fill/exit/halt/reconcile/orphan in last 3h.
- engine PID 2926399 active/running (old code in memory until restart).

## NEXT — Step 3 restart (OPERATOR ONLY, az run-command)
```
az vm run-command invoke --resource-group rg-shared-prod --name tc-prod-vm --command-id RunShellScript --scripts "systemctl restart trading-corp"
```
Then Step 4 VERIFY (VERIFY.md). Flat confirmed ~22:52 UTC; if restart is delayed >~15 min, re-confirm flat first.

## Rollback (if needed)
```
cd /home/azureuser/trading_corp
for f in trading_corp/brokers/bitunix.py trading_corp/agents/divisions/bitunix_futures_observer.py trading_corp/agents/divisions/bitunix_position_reconciler.py; do mv "$f.bak-pre-tpsl-rebuild-2026-06-18" "$f"; done
```
then restart via az run-command.
