#!/bin/sh
# golive_kalshi_unit.sh - Phase K5 Kalshi copy-trading LIVE flip (ROOT unit edit + restart).
# Runs as ROOT under Azure classic Run Command (dash; no pipefail; ASCII only).
# Invoke:  powershell -ep bypass -f "$HOME\Desktop\runprod.ps1" golive_kalshi_unit.sh
# Adds 'kalshi' to --brokers and 'kalshi_copy_trading' to --live-divisions, then
# daemon-reload + restart trading-corp. Guarded: aborts (no restart) if the current
# ExecStart args do not match exactly, or if kalshi is already present.
set -e
UNIT=/etc/systemd/system/trading-corp.service
OLD='--brokers bitunix robinhood --live-divisions bitunix_sfp robinhood_pead bitunix_futures'
NEW='--brokers bitunix robinhood kalshi --live-divisions bitunix_sfp robinhood_pead bitunix_futures kalshi_copy_trading'

echo "== pre-check =="
n=$(grep -cF -- "$OLD" "$UNIT" || true)
if [ "$n" != "1" ]; then echo "ABORT: expected 1 match of current args, found $n"; exit 1; fi
if grep -qF -- 'kalshi_copy_trading' "$UNIT"; then echo "ABORT: kalshi_copy_trading already in unit -- already flipped?"; exit 1; fi

echo "== backup =="
cp "$UNIT" "$UNIT.bak-pre-k5-golive-2026-07-01"
ls -l "$UNIT.bak-pre-k5-golive-2026-07-01"

echo "== edit =="
sed -i "s#$OLD#$NEW#" "$UNIT"
m=$(grep -cF -- "$NEW" "$UNIT" || true)
o=$(grep -cF -- "$OLD" "$UNIT" || true)
if [ "$m" != "1" ] || [ "$o" != "0" ]; then
  echo "ABORT: post-edit check failed (new=$m old=$o) -- restoring backup, NO restart"
  cp "$UNIT.bak-pre-k5-golive-2026-07-01" "$UNIT"
  exit 1
fi
echo "new ExecStart:"; grep '^ExecStart=' "$UNIT"

echo "== daemon-reload + restart (GO-LIVE MOMENT) =="
systemctl daemon-reload
systemctl restart trading-corp
sleep 6

echo "== post-restart =="
systemctl show trading-corp -p MainPID -p ActiveState -p NRestarts
PID=$(systemctl show trading-corp -p MainPID --value)
echo "live args now:"
tr '\0' '\n' < /proc/$PID/cmdline 2>/dev/null | grep -E 'kalshi|brokers|live-divisions' | tr '\n' ' ' || true
echo ""
echo "DONE_GOLIVE"
