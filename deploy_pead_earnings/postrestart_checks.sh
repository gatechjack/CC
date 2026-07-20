set +e
cd /home/azureuser/trading_corp
DB=data/trading_corp.db
echo "===== CHECK 1: PANEL RENDER (LIVE ENGINE :8000, not the harness) ====="
curl -s --max-time 12 localhost:8000/telemetry/pead/partials/live > /tmp/panel.html
echo "bytes=$(wc -c </tmp/panel.html)"
echo "markers: UpcomingEarnings=$(grep -c 'Upcoming Earnings' /tmp/panel.html) SUE_plausibility=$(grep -c 'SUE plausibility' /tmp/panel.html) JustReported=$(grep -c 'Just reported' /tmp/panel.html)"
echo "--- raw Upcoming watchlist (header + first rows, pre-report) ---"
awk '/Upcoming Earnings/,/Just reported/' /tmp/panel.html | grep -oE 'font-semibold">[A-Z.]{1,6}<|>plausible<|>low<|>BMO<|>AMC<|SUE plausibility' | head -28 | tr '\n' ' '; echo
echo "--- raw Just-reported symbols (exact SUE rows) ---"
awk '/Just reported/,/screened out|<\/section>/' /tmp/panel.html | grep -oE 'font-semibold">[A-Z.]{1,6}' | sed 's/.*">//' | head -14 | tr '\n' ' '; echo

echo "===== CHECK 2 (engine ledger side): JBHT qty + stop ====="
sqlite3 -header -column "$DB" "SELECT symbol, qty, round(entry_reference_price,2) entry, json_extract(extra_json,'$.stop_price') stop, json_extract(extra_json,'$.next_earnings_date') next_earn, execution_mode FROM paper_trade_record WHERE division='robinhood_pead' AND result IS NULL;"

echo "===== CHECK 3: execution_mode LIVE + robinhood in --brokers ====="
echo "--- ExecStart (full) ---"; systemctl cat trading-corp | grep -E 'ExecStart' | head -1
echo "--- robinhood_pead yaml mode ---"; awk '/robinhood_pead:/{c=6} c&&c-->0{print}' config/strategies.yaml | grep -E 'auto_execute|execution_mode'

echo "===== CHECK 4: RH session authenticated POST-BOOT ====="
echo "--- account \$ values in the panel (equity requires a real broker.snapshot RH call; None if 401) ---"
grep -oE '\$[0-9][0-9,]*\.[0-9]{2}' /tmp/panel.html | head -6 | tr '\n' ' '; echo
echo "--- robinhood health token in panel ---"; grep -oiE 'robinhood[^<]{0,30}|>live<|>down<' /tmp/panel.html | head -4 | tr '\n' ' '; echo
echo "--- /api/rh/session-health (its sentinel does a RAW RH request_get -> 401 would show down) ---"
curl -s --max-time 10 localhost:8000/api/rh/session-health | grep -oiE 'valid|down|last good[^<]*' | head -4 | tr '\n' ' '; echo

echo "===== CHECK 5: Bitunix flat + division clean ====="
echo "--- open bitunix positions (MUST be empty) ---"
sqlite3 "$DB" "SELECT division,symbol,qty FROM paper_trade_record WHERE division LIKE 'bitunix%' AND result IS NULL;"
echo "(end bitunix open)"
echo "--- engine health ---"; echo "is-active=$(systemctl is-active trading-corp) NRestarts=$(systemctl show -p NRestarts --value trading-corp) MainPID=$(systemctl show -p MainPID --value trading-corp)"
echo "--- bitunix bar cache freshness (DB, not logs; proves the bitunix feed came up) ---"
sqlite3 "$DB" "SELECT symbol, max(open_time) latest_bar FROM bitunix_bar_history GROUP BY symbol;" 2>/dev/null | head -6
