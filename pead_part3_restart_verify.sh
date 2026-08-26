echo "START pead_part3_restart_verify (ROOT via az run-command; single restart + all-division verify)"; date -u +%FT%TZ
ROOT="/home/azureuser/trading_corp"
echo "PRE MainPID=$(systemctl show -p MainPID --value trading-corp)"
echo "files staged: robinhood QuoteSymbolUnresolved=$(grep -c QuoteSymbolUnresolved "$ROOT/trading_corp/brokers/robinhood.py") pead reresolve=$(grep -c _reresolve_unresolved_symbols "$ROOT/trading_corp/agents/strategies/pead_strategy.py")"
echo "=== SINGLE RESTART ==="
systemctl restart trading-corp || { echo "RESTART_FAILED"; exit 1; }
sleep 20
echo "POST MainPID=$(systemctl show -p MainPID --value trading-corp) active=$(systemctl is-active trading-corp) NRestarts=$(systemctl show -p NRestarts --value trading-corp)"
echo "=== per-division registration (expect >=1 each) ==="
for D in bitunix_sfp robinhood_pead bitunix_futures kalshi_copy_trading robinhood_pmcc robinhood_mace; do
  echo "  $D=$(journalctl -u trading-corp --since '-160s' -o cat | grep -c "division=$D")"
done
echo "ERRORS(Traceback/FATAL/CRITICAL)=$(journalctl -u trading-corp --since '-160s' -o cat | grep -Eci 'Traceback|FATAL|CRITICAL|unhandled')"
echo "=== futures reattach (pre-restart 1 open) ==="
python3 -c "import sqlite3;print('bitunix_futures_open='+str(sqlite3.connect('file:$ROOT/data/trading_corp.db?mode=ro',uri=True).execute(\"SELECT COUNT(*) FROM paper_trade_record WHERE division='bitunix_futures' AND result IS NULL\").fetchone()[0]))"
echo "hook1_live(quote strict)=$(grep -c 'strict: bool = False' "$ROOT/trading_corp/brokers/robinhood.py")"
curl -s -o /dev/null -w "healthz=%{http_code}\n" http://127.0.0.1:8000/healthz
echo "=== key boot lines ==="
journalctl -u trading-corp --since '-160s' -o cat | grep -Ei "Registered .*division=|broker.*connected|equity=|armed|paper=False|Traceback|FATAL|CRITICAL" | tail -34
echo "DONE pead_part3_restart_verify"
