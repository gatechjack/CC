#!/usr/bin/env bash
# READ-ONLY. Bitunix-clean assertions for the COMBINED post-restart boot-smoke
# (hard requirements #2 main.py wiring + #3 models.py FillEvent). Run after the
# restart, before unhalt. Eyeball each line GREEN; any FillEvent/role/traceback
# at boot, or missing bitunix wiring, = ABORT (roll back, do not unhalt).
set -uo pipefail
DB="${TC_DB:-/home/azureuser/trading_corp/data/trading_corp.db}"
echo "=== Bitunix post-restart boot-smoke (read-only) ==="
echo "--- service / new PID / restart count ---"
systemctl show trading-corp -p MainPID -p ActiveState -p NRestarts 2>/dev/null
echo "--- #2 main.py: bitunix_futures wired into --live-divisions? ---"
systemctl show trading-corp -p ExecStart 2>/dev/null | grep -o 'live-divisions[^\"]*' || echo "  (check ExecStart manually)"
echo "--- bitunix broker registered paper=False + connected? ---"
journalctl -u trading-corp --no-pager --since '10 min ago' 2>&1 \
  | grep -E 'Registered bitunix_futures broker|BitunixBroker connected' | tail -3
echo "--- #3 models.py: FillEvent/role/import boot errors? (want NONE) ---"
journalctl -u trading-corp --no-pager --since '10 min ago' 2>&1 \
  | grep -iE 'traceback|fillevent|role.*(error|attribute)|importerror|exception' | tail -8 \
  || echo "  none"
echo "--- reconciler clean post-restart? (want mc=0/miss=0/orph=0) ---"
sqlite3 "$DB" "SELECT ts||'  '||substr(payload_json,1,70) FROM audit_event WHERE actor='bitunix_position_reconciler' AND kind='position_state_reconciled' ORDER BY id DESC LIMIT 1;"
echo "=== ALL lines GREEN (new PID, bitunix wired, paper=False, no FillEvent/role error, reconciler clean) before unhalt ==="
