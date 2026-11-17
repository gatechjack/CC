echo "=== PV2 BASE PROBE (RO): box shas of the two build files ==="
date -u +%FT%TZ
ROOT=/home/azureuser/trading_corp
for f in trading_corp/data/polymarket_data_api_client.py trading_corp/prediction_markets/loss_grounding.py; do
  if [ -f "$ROOT/$f" ]; then s=$(tr -d '\r' < "$ROOT/$f" | sha256sum | cut -c1-16); l=$(wc -l < "$ROOT/$f"); else s=MISSING; l=0; fi
  printf '%-55s %s lines=%s\n' "$f" "$s" "$l"
done
echo "=== engine/pm_web unchanged since scope? ==="
systemctl show trading-corp -p MainPID -p ActiveState -p ExecMainStartTimestamp 2>&1
systemctl show prediction-markets-web -p MainPID -p ActiveState -p ExecMainStartTimestamp 2>&1
echo "=== df pre-guard (before any later copy/tar) ==="
df -h /
echo "=== END ==="
