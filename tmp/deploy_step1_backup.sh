#!/bin/bash
# Step 1: Pre-deploy backup. Take a timestamped copy of the prod file
# being modified so rollback is a single mv away.
set -e

BASE=/home/azureuser/trading_corp
TAG=pre-v2-kline-fix-20260520
F=trading_corp/agents/paper_trade_replay.py

echo "=== md5 of CURRENT prod file (pre-deploy state) ==="
md5sum "$BASE/$F"

echo ""
echo "=== creating backup ==="
if [ -f "$BASE/$F.$TAG" ]; then
  echo "BACKUP ALREADY EXISTS at $BASE/$F.$TAG (idempotent skip)"
  md5sum "$BASE/$F.$TAG"
else
  sudo -u azureuser cp -p "$BASE/$F" "$BASE/$F.$TAG"
  echo "BACKUP CREATED at $BASE/$F.$TAG"
  ls -l "$BASE/$F.$TAG"
  md5sum "$BASE/$F.$TAG"
fi

echo ""
echo "=== verifying backup matches current prod file ==="
PROD_MD5=$(md5sum "$BASE/$F" | awk '{print $1}')
BAK_MD5=$(md5sum "$BASE/$F.$TAG" | awk '{print $1}')
if [ "$PROD_MD5" = "$BAK_MD5" ]; then
  echo "OK: backup md5 matches prod file md5: $BAK_MD5"
else
  echo "FAIL: backup md5 differs from prod"
  exit 2
fi

echo ""
echo "=== ROLLBACK RECIPE ==="
echo "Run on prod VM if needed:"
echo "  sudo -u azureuser mv $BASE/$F.$TAG $BASE/$F"
echo "  sudo systemctl restart trading-corp"

echo ""
echo "=== service health pre-deploy ==="
systemctl is-active trading-corp || true
sudo -u azureuser cat /home/azureuser/trading_corp/data/trading_corp.pid 2>/dev/null || echo "(no pid file)"
