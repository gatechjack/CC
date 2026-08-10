set -u
echo "=== engine service: MainPID / NRestarts / ActiveEnter (no restart this session?) ==="
systemctl show trading_corp -p MainPID -p NRestarts -p ActiveState -p ActiveEnterTimestamp 2>/dev/null
echo "=== app listener on :8000 ==="
ss -ltnp 2>/dev/null | grep ':8000' | head -2
echo "=== temp harnesses cleaned? (expect none) ==="
ls -la /tmp/pct_*.py 2>/dev/null || echo "none (clean)"
echo "=== config strategies.yaml mtime (unchanged = not today) ==="
stat -c '%y %n' /home/azureuser/trading_corp/config/strategies.yaml 2>/dev/null
echo "=== any trading_corp package .py modified today (expect none = no code deploy) ==="
find /home/azureuser/trading_corp/trading_corp -name '*.py' -newermt '2026-08-10 00:00:00' 2>/dev/null | head -8
echo "(end code-mtime list)"
echo "=== reload/scan evidence: journal polymarket_copy activity in last ~8 min ==="
journalctl -u trading_corp --since "8 min ago" 2>/dev/null | grep -iE 'polymarket_copy|selected_whales|scan' | tail -6 || echo "no-journal-access"
echo "=== agent_state roster keys updated_ts (only these 3 should be ~03:43) ==="
DB=/home/azureuser/trading_corp/data/trading_corp.db
RO="sqlite3 -readonly $DB"
$RO "SELECT key||' '||updated_ts FROM agent_state WHERE agent='polymarket_copy_trader' AND key IN ('selected_whales','pinned_whales','watch_only_whales') ORDER BY key;"
echo "=== DONE ==="
