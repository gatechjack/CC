#!/usr/bin/env bash
# READ-ONLY Kalshi key auth test (box side). Discovers KEY_VAULT_URI from the engine's OWN service env
# (azureuser reads its own process environ), runs the scp'd test with it set, brackets the engine PID,
# removes the test file. No orders (KalshiBroker has no place_order). No writes to prod. No restart.
echo "=== KALSHI KEY AUTH TEST (read-only; existing vs new; NO orders) $(date -u) ==="
P0=$(systemctl show -p MainPID --value trading-corp.service 2>/dev/null); echo "ENGINE_BEFORE=$P0"
ROOT=/home/azureuser/trading_corp
VP=$ROOT/venv/bin/python

# Discover KEY_VAULT_URI from the engine's service env (the engine runs as azureuser, so we can read its
# /proc/<pid>/environ). Try MainPID, its children, and any trading_corp process; fall back to systemd Environment=.
KVU=""
for pid in $P0 $(pgrep -P "$P0" 2>/dev/null) $(pgrep -f 'trading_corp' 2>/dev/null); do
  [ -r "/proc/$pid/environ" ] || continue
  v=$(tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null | grep -m1 '^KEY_VAULT_URI=' | cut -d= -f2-)
  if [ -n "$v" ]; then KVU="$v"; break; fi
done
if [ -z "$KVU" ]; then
  KVU=$(systemctl show trading-corp.service -p Environment 2>/dev/null | tr ' ' '\n' | grep -m1 '^KEY_VAULT_URI=' | cut -d= -f2-)
fi
if [ -n "$KVU" ]; then echo "KEY_VAULT_URI: discovered from engine service env"; else echo "KEY_VAULT_URI: NOT DISCOVERABLE (test will report and stop)"; fi

echo ""
( cd "$ROOT" && KEY_VAULT_URI="$KVU" PYTHONPATH="$ROOT" "$VP" /home/azureuser/pm_kalshi_key_test.py 2>&1 )
echo ""

rm -f /home/azureuser/pm_kalshi_key_test.py
P1=$(systemctl show -p MainPID --value trading-corp.service 2>/dev/null); echo "ENGINE_AFTER=$P1 BEFORE=$P0"
if [ "$P0" = "$P1" ] && [ -n "$P0" ]; then echo "ENGINE_UNCHANGED=GOOD"; else echo "ENGINE_CHANGED=INVESTIGATE"; fi
echo "=== END KALSHI KEY AUTH TEST ==="
