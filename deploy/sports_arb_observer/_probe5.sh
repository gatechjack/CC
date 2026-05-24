echo "=== current UTC time ==="
date -u
echo ""
echo "=== arb-observer audit row counts ==="
sqlite3 /home/azureuser/trading_corp/data/trading_corp.db "SELECT kind, COUNT(*), MIN(ts), MAX(ts) FROM audit_event WHERE kind LIKE 'kalshi_sports_arb_%' GROUP BY kind;"
