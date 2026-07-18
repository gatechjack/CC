#!/usr/bin/env bash
# Post-restart boot-smoke for the RH-auth batch. Run AFTER the scheduled restart.
# Operator paste (ONE line, read-only):
#   ssh azureuser@trading.jacksumner.com 'bash -s' < .\deploy_rh_auth\bootsmoke_rh_auth.sh
# (or scp it up and run). Verifies RH auth resolves from KV post-restart + ITEM 2/3 present.
set -uo pipefail
echo "================= RH-AUTH BATCH BOOT-SMOKE ================="
BOOT=$(systemctl show trading-corp -p ActiveEnterTimestamp --value)
echo "--- engine ---"
systemctl show trading-corp -p MainPID,ActiveState,SubState,NRestarts,ActiveEnterTimestamp | sed 's/^/  /'
SINCE=$(date -u -d "$BOOT" '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo "10 min ago")

echo "--- [1] KV secrets loaded (EXPECT 35; was 33 pre-batch -> +2 = RH creds now from KV) ---"
sudo -n journalctl -u trading-corp --since "$SINCE" --no-pager 2>/dev/null \
  | grep -i 'Key Vault: loaded' | tail -1 | sed 's/tc-prod-vm xvfb-run\[[0-9]*\]: //' | sed 's/^/  /'

echo "--- [2] RH login + binds (EXPECT user + 4 accounts: 461391328/934310442/116637293063/680725082) ---"
sudo -n journalctl -u trading-corp --since "$SINCE" --no-pager 2>/dev/null \
  | grep -iE 'RobinhoodBroker logged in|RobinhoodBroker bound' | sed 's/tc-prod-vm xvfb-run\[[0-9]*\]: //' | sed 's/^/  /'

echo -n "--- [3] 401 Client Error since boot (EXPECT 0): "
sudo -n journalctl -u trading-corp --since "$SINCE" --no-pager 2>/dev/null | grep -c '401 Client Error'

echo "--- [4] tracebacks touching the batch (EXPECT none) ---"
H=$(sudo -n journalctl -u trading-corp --since "$SINCE" --no-pager 2>/dev/null \
  | grep -iE 'traceback|Exception|Error' | grep -iE 'robinhood|routes|data_exec|secrets|rh_auth|_attempt_reauth|_auth_alert' | tail -8)
[ -n "$H" ] && echo "$H" | sed 's/^/  /' || echo "  (none)"

echo "--- [5] ITEM 2 routes (web on :8000; may need ~90s after restart to listen) ---"
for i in 1 2 3; do
  code=$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/api/rh/session-health 2>/dev/null || echo 000)
  echo "  GET /api/rh/session-health -> HTTP $code"
  [ "$code" = "200" ] && break
  sleep 20
done

echo "--- [6] ITEM 3 latch present (module wired) ---"
cd /home/azureuser/trading_corp && PYTHONPATH=/home/azureuser/trading_corp venv/bin/python -c \
  'from trading_corp.brokers import robinhood as r; print("  _auth_down=",r._auth_down," hook_wired=",r._auth_alert_hook is not None)' 2>&1 | sed 's/^/  /'

echo "--- [7] Bitunix unaffected (last event recent) ---"
sqlite3 /home/azureuser/trading_corp/data/trading_corp.db \
  "SELECT '  '||actor||' last='||MAX(substr(ts,1,19)) FROM audit_event WHERE actor IN ('bitunix_sfp','bitunix_futures') GROUP BY actor;" 2>/dev/null
echo "================= END BOOT-SMOKE ================="
echo "PASS = [1] 35 loaded, [2] user+4 binds, [3] 0, [4] none, [5] 200, [6] hook_wired=True, [7] recent."
