echo "=== scout's call signature (post-parallel-patch) ==="
grep -A4 "kalshi_broker.list_markets" /home/azureuser/trading_corp/trading_corp/agents/strategies/kalshi_sports_scout.py | head -10
echo ""
echo "=== observer's call signature ==="
grep -A4 "kalshi_broker.list_markets" /home/azureuser/trading_corp/trading_corp/agents/strategies/kalshi_sports_arb_observer.py | head -10
echo ""
echo "=== scout's most recent scan summary ==="
sqlite3 /home/azureuser/trading_corp/data/trading_corp.db "SELECT ts, payload_json FROM audit_event WHERE kind='kalshi_sports_scout_scan' ORDER BY ts DESC LIMIT 1;"
echo ""
echo "=== scout's strategy.yaml block (esp series_filter / discovery) ==="
grep -A12 "kalshi_sports_scout:" /home/azureuser/trading_corp/config/strategies.yaml | head -15
