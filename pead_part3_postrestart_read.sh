echo "POSTRESTART READ"; date -u +%FT%TZ
R=~/trading_corp
echo "MainPID=$(systemctl show -p MainPID --value trading-corp) active=$(systemctl is-active trading-corp) NRestarts=$(systemctl show -p NRestarts --value trading-corp) (pre-restart PID was 37596)"
echo "=== all division registrations (journal since restart) ==="
for D in bitunix_sfp robinhood_pead bitunix_futures kalshi_copy_trading robinhood_pmcc robinhood_mace; do
  echo "  $D=$(journalctl -u trading-corp --since '-6min' -o cat 2>&1 | grep -c "division=$D")"
done
echo "=== robinhood broker + mace + hook1 evidence ==="
journalctl -u trading-corp --since '-6min' -o cat 2>&1 | grep -Ei "RobinhoodBroker connected|division=robinhood|MACE|gross|available_buying_power|QuoteSymbolUnresolved" | tail -20
echo "ERRORS(Traceback/FATAL/CRITICAL)=$(journalctl -u trading-corp --since '-6min' -o cat 2>&1 | grep -Eci 'Traceback|FATAL|CRITICAL|unhandled')"
echo "hook1_live(quote strict in deployed file)=$(grep -c 'strict: bool = False' $R/trading_corp/brokers/robinhood.py) reresolve=$(grep -c _reresolve_unresolved_symbols $R/trading_corp/agents/strategies/pead_strategy.py)"
python3 -c "import sqlite3,os;print('futures_open='+str(sqlite3.connect('file:'+os.path.expanduser('~/trading_corp/data/trading_corp.db')+'?mode=ro',uri=True).execute(\"SELECT COUNT(*) FROM paper_trade_record WHERE division='bitunix_futures' AND result IS NULL\").fetchone()[0])+' pead_open='+str(sqlite3.connect('file:'+os.path.expanduser('~/trading_corp/data/trading_corp.db')+'?mode=ro',uri=True).execute(\"SELECT COUNT(*) FROM paper_trade_record WHERE division='robinhood_pead' AND result IS NULL\").fetchone()[0]))"
curl -s -o /dev/null -w "healthz=%{http_code}\n" http://127.0.0.1:8000/healthz
echo "DONE POSTRESTART READ"
