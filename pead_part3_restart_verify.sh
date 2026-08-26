echo "START pead_part3_restart_verify (RESTART + ALL-DIVISION VERIFY -- run in a DELIBERATE window)"; date -u +%FT%TZ
ROOT="$HOME/trading_corp"
echo "PRE MainPID:"; systemctl show -p MainPID --value trading-corp 2>/dev/null
echo "=== confirm Part 3 files are staged in place BEFORE restart ==="
grep -c "QuoteSymbolUnresolved" "$ROOT/trading_corp/brokers/robinhood.py" | sed 's/^/robinhood.py QuoteSymbolUnresolved lines=/'
grep -c "_reresolve_unresolved_symbols" "$ROOT/trading_corp/agents/strategies/pead_strategy.py" | sed 's/^/pead_strategy.py reresolve lines=/'
echo "=== SINGLE RESTART (privileged -- operator runs deliberately) ==="
sudo -n systemctl restart trading-corp || { echo "RESTART FAILED via sudo -n -- run the restart via your privileged path, then re-run the verify section."; exit 1; }
sleep 15
PID=$(systemctl show -p MainPID --value trading-corp 2>/dev/null)
echo "POST MainPID=$PID  active=$(systemctl is-active trading-corp)  NRestarts=$(systemctl show -p NRestarts --value trading-corp)"
echo "=== ALL-DIVISION boot lines (journal since restart) ==="
journalctl -u trading-corp --since "-120s" -o cat 2>/dev/null | grep -Ei "Registered .*division=|broker.*connected|paper=False|armed|FATAL|Traceback|CRITICAL" | tail -70
echo "=== per-division registration count (expect >=1 each) ==="
for D in bitunix_sfp robinhood_pead bitunix_futures kalshi_copy_trading robinhood_pmcc robinhood_mace; do
  n=$(journalctl -u trading-corp --since "-150s" -o cat 2>/dev/null | grep -c "division=$D")
  echo "  DIV $D registered_lines=$n"
done
echo "=== error scan (should be empty) ==="
journalctl -u trading-corp --since "-150s" -o cat 2>/dev/null | grep -Ei "Traceback|FATAL|CRITICAL|unhandled" | tail -20
echo "=== hook 1 live in the running code ==="
grep -c "strict: bool = False" "$ROOT/trading_corp/brokers/robinhood.py" | sed 's/^/quote(strict) present=/'
echo "=== healthz ==="
curl -s -o /dev/null -w "  healthz=%{http_code}\n" http://127.0.0.1:8000/healthz 2>/dev/null || echo "  (healthz curl failed -- check :8000 bind)"
echo "REVIEW gates: every division registered >=1; no Traceback/FATAL; MACE armed + gross-BP; PMCC re-armed; futures+SFP reconcilers clean; PEAD strict-quote live."
echo "ROLLBACK: restore both *.bak_pre_part3_20260826 then restart again."
echo "DONE pead_part3_restart_verify"
