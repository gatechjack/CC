set -u
ST="2026-09-06 00:30:50"
echo "### error + steady-state probe (READ-ONLY) ###"
echo "## the 18 'error' lines (Traceback|CRITICAL|wiring FAILED) -- what ARE they?:"
journalctl -u trading-corp --since "$ST" --no-pager 2>/dev/null | grep -nE 'Traceback|CRITICAL|wiring FAILED' | grep -ivE 'IBIT|Candle' | head -20 | sed 's/^/  /'
echo "## the PROCEEDS cross-check: is our_proceeds*100 == kalshi_revenue (units artifact) or a real gap?:"
journalctl -u trading-corp --since "$ST" --no-pager 2>/dev/null | grep -oE 'proceeds=[0-9.]+ vs kalshi revenue=[0-9.]+' | sort -u | sed 's/^/  /'
echo "## is the driver now STEADY-STATE polling/evaluating (most recent pm_live_driver lines)?:"
journalctl -u trading-corp --since "$ST" --no-pager 2>/dev/null | grep -E 'prediction_markets.live_driver|prediction_markets.execution' | grep -ivE 'IBIT|Candle' | tail -6 | sed 's/^/  /'
echo "## most recent 3 journal lines overall (engine alive + what it is doing NOW):"
journalctl -u trading-corp --no-pager 2>/dev/null | grep -vE 'IBIT|Candle|received message' | tail -3 | sed 's/^/  /'
echo "### DONE ###"
