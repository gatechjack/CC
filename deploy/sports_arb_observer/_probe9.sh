echo "=== _SCOUT_SERIES_FILTER definition ==="
grep -B1 -A6 "_SCOUT_SERIES_FILTER" /home/azureuser/trading_corp/trading_corp/agents/strategies/kalshi_sports_scout.py | head -15
echo ""
echo "=== KalshiBroker.list_markets signature ==="
grep -A8 "def list_markets" /home/azureuser/trading_corp/trading_corp/brokers/kalshi.py | head -15
echo ""
echo "=== discover_by_categories signature ==="
grep -A12 "def discover_by_categories" /home/azureuser/trading_corp/trading_corp/data/kalshi_market_map.py | head -15
