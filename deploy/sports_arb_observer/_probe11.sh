echo "=== current UTC ==="
date -u
echo ""
echo "=== all arb-observer audit rows ==="
sqlite3 /home/azureuser/trading_corp/data/trading_corp.db "SELECT kind, COUNT(*), MIN(ts), MAX(ts) FROM audit_event WHERE kind LIKE 'kalshi_sports_arb_%' GROUP BY kind ORDER BY MAX(ts) DESC;"
echo ""
echo "=== last scan summary payload ==="
sqlite3 /home/azureuser/trading_corp/data/trading_corp.db "SELECT ts, payload_json FROM audit_event WHERE kind='kalshi_sports_arb_scan' ORDER BY ts DESC LIMIT 1;"
