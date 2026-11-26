#!/usr/bin/env bash
# secrev_batch7_rollback.sh -- reinstall the exact purged package versions after a Batch 7 failure/regret.
# JACK RUNS (az vm run-command, root). Usage: secrev_batch7_rollback.sh /home/azureuser/secrev_batch7_reinstall_<TS>.txt
# (Alternative rollback = restore the pre-Batch-7 OS snapshot.) Reads a saved `pkg=version` list; reinstalls exactly those.
set -u
exec > >(tee /home/azureuser/secrev_batch7_rollback_lastrun.log) 2>&1
RE="${1:?usage: secrev_batch7_rollback.sh <reinstall_list_file>}"
PROTECTED="trading-corp prediction-markets-web sfp-card-watcher market-context-recorder caddy authelia"
echo "=== BATCH7 ROLLBACK $(date -u +%Y%m%dT%H%M%SZ) from $RE ==="
[ -r "$RE" ] || { echo "[ABORT] no $RE"; exit 2; }
N=$(grep -c . "$RE"); echo "[restore] reinstalling $N exact-version packages (NEEDRESTART_MODE=l) ..."
PKGS=$(tr '\n' ' ' < "$RE")
DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l apt-get install --reinstall -y --allow-downgrades $PKGS 2>&1 | tail -20
echo "[verify] still-missing after reinstall:"
MISS=0; while IFS= read -r pv; do p="${pv%%=*}"; dpkg -s "$p" >/dev/null 2>&1 || { echo "  MISSING: $p"; MISS=$((MISS+1)); }; done < "$RE"
echo "  missing=$MISS (expect 0)"
echo "[verify] protected services:"
for u in $PROTECTED; do echo "  $u active=$(systemctl is-active $u) PID=$(systemctl show $u -p MainPID --value)"; done
echo "  Xvfb :99 pid=$(pgrep -f 'Xvfb :99' | head -1)"
echo "=== ROLLBACK DONE -- if packages are unavailable from the index, restore the OS snapshot instead ==="
