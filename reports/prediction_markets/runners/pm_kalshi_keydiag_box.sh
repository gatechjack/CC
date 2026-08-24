#!/usr/bin/env bash
# READ-ONLY: distinguish (a) stale in-memory key vs (b) order-path defect for poly_kalshi's 401.
# Compares the engine's materialized KALSHI-KAREN PEM tempfile to the current vault BY HASH (fingerprint
# only, never a value); timestamp fallback if PrivateTmp hides the tempfile. NO orders, NO restart, NO config.
echo "=== KALSHI KEY-DIAG (read-only; stale-key vs order-path; NO orders/restart) $(date -u) ==="
P0=$(systemctl show -p MainPID --value trading-corp.service 2>/dev/null); echo "ENGINE_BEFORE=$P0"
START=$(systemctl show -p ExecMainStartTimestamp --value trading-corp.service 2>/dev/null)
PT=$(systemctl show -p PrivateTmp --value trading-corp.service 2>/dev/null)
echo "ENGINE_START=$START   PrivateTmp=$PT"
ROOT=/home/azureuser/trading_corp
VP=$ROOT/venv/bin/python

KVU=""
for pid in $P0 $(pgrep -P "$P0" 2>/dev/null) $(pgrep -f 'trading_corp' 2>/dev/null); do
  [ -r "/proc/$pid/environ" ] || continue
  v=$(tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null | grep -m1 '^KEY_VAULT_URI=' | cut -d= -f2-)
  if [ -n "$v" ]; then KVU="$v"; break; fi
done
[ -n "$KVU" ] && echo "KEY_VAULT_URI: discovered from engine env" || echo "KEY_VAULT_URI: NOT discoverable"

echo "--- .env override check (a .env would override vault; NAMES counted, never values) ---"
if [ -f "$ROOT/.env" ]; then
  echo ".env EXISTS -- KALSHI_KAREN key-name lines present: $(grep -cE '^KALSHI_KAREN_(API_KEY_ID|PRIVATE_KEY_PEM)=' "$ROOT/.env" 2>/dev/null)"
else
  echo ".env absent -- engine loads secrets from KeyVault (a restart would reload current vault)."
fi
echo "--- engine PEM tempfile visibility (readable here == not PrivateTmp-isolated) ---"
ls -la /tmp/kalshi_*.pem 2>/dev/null || echo "  (no /tmp/kalshi_*.pem visible in this namespace)"

echo ""
( cd "$ROOT" && KEY_VAULT_URI="$KVU" ENGINE_START="$START" PYTHONPATH="$ROOT" "$VP" /home/azureuser/pm_kalshi_keydiag.py 2>&1 )
echo ""

rm -f /home/azureuser/pm_kalshi_keydiag.py
P1=$(systemctl show -p MainPID --value trading-corp.service 2>/dev/null); echo "ENGINE_AFTER=$P1 BEFORE=$P0"
if [ "$P0" = "$P1" ] && [ -n "$P0" ]; then echo "ENGINE_UNCHANGED=GOOD"; else echo "ENGINE_CHANGED=INVESTIGATE"; fi
echo "=== END KALSHI KEY-DIAG ==="
