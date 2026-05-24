echo "=== current UTC ==="
date -u
echo ""
echo "=== arb_observer activity in journal since 02:50 UTC ==="
journalctl -u trading-corp.service --since "2026-05-24 02:50:00" --no-pager | grep -iE "arb_observer|Arb Observer|sports_arb" | head -40
echo ""
echo "=== row counts ==="
sqlite3 /home/azureuser/trading_corp/data/trading_corp.db "SELECT kind, COUNT(*), MIN(ts), MAX(ts) FROM audit_event WHERE kind LIKE 'kalshi_sports_arb_%' GROUP BY kind;"
echo ""
echo "=== scout for comparison (sibling that we know works) ==="
sqlite3 /home/azureuser/trading_corp/data/trading_corp.db "SELECT kind, COUNT(*), MAX(ts) FROM audit_event WHERE kind LIKE 'kalshi_sports_scout%' AND ts > '2026-05-24 02:00:00' GROUP BY kind;"
