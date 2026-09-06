set -u
ROOT=/home/azureuser/trading_corp
MAIN=$ROOT/trading_corp/main.py
echo "### BOX main.py PULL + HASH (READ-ONLY) $(date -u +%Y%m%dT%H%M%SZ) ###"
echo "  box main.py CR-stripped sha256(16) = $(tr -d '\r' < "$MAIN" | sha256sum | cut -c1-16)"
echo "  raw sha256(16) (autocrlf-sensitive, do NOT compare raw)= $(sha256sum "$MAIN" | cut -c1-16)"
echo "  mtime=$(stat -c '%y' "$MAIN" | cut -d. -f1)  size=$(stat -c '%s' "$MAIN")"
echo "  PM driver markers present? scheduled_pm_live_loop=$(grep -c 'scheduled_pm_live_loop' "$MAIN") plan_driver_tasks=$(grep -c 'plan_driver_tasks' "$MAIN") pm_live_driver=$(grep -c 'pm_live_driver' "$MAIN") (expect 0/0/0 = clobbered)"
echo "  M3 shard-snapshot markers? pm_shard_balance_snapshot=$(grep -c 'pm_shard_balance_snapshot' "$MAIN") shard_snapshot_task=$(grep -c 'shard_snapshot_task' "$MAIN")"
echo "  ANCHOR present? poly_kalshi FAILED line=$(grep -c 'Poly->Kalshi MLB copy wiring FAILED' "$MAIN")  M3-snapshot-comment=$(grep -c 'per-account SHARD-BALANCE SNAPSHOTS' "$MAIN")"
echo "  MACE Phase-2 markers (must SURVIVE the graft) dxfeed(i)=$(grep -ic 'dxfeed' "$MAIN") tastytrade-chart=$(grep -ic 'tastytrade' "$MAIN") mace(i)=$(grep -ic 'mace' "$MAIN")"
echo "### DONE ###"
