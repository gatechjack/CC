echo "=== PM HEALTH RO PROBE 4 ==="
date -u +%FT%TZ
ROOT=/home/azureuser/trading_corp
PMDB=$ROOT/data/prediction_markets.db
RO_PM="file:$PMDB?mode=ro"

echo ""; echo "--- [1] ERROR-priority journal (excludes WARNING 410 flood), tail 40 ---"
journalctl -u trading-corp -p err --no-pager -o short-iso 2>/dev/null | tail -40

echo ""; echo "--- [2] ERROR-priority line count + distinct message heads (retained) ---"
journalctl -u trading-corp -p err --no-pager 2>/dev/null | grep -oE "trading_corp[.a-z_]*:" | sort | uniq -c | sort -rn | head -20
echo "total err lines:"; journalctl -u trading-corp -p err --no-pager 2>/dev/null | wc -l

echo ""; echo "--- [3] /positions 410: earliest+latest in retained journal + per-hour count ---"
journalctl -u trading-corp --no-pager -o short-iso 2>/dev/null | grep "Data API v1 has been retired" | head -1 | cut -c1-30
journalctl -u trading-corp --no-pager -o short-iso 2>/dev/null | grep "Data API v1 has been retired" | tail -1 | cut -c1-30
echo "410 count (retained ~2d):"; journalctl -u trading-corp --no-pager 2>/dev/null | grep -c "Data API v1 has been retired"

echo ""; echo "--- [4] signal-death triangulation: last Polymarket data flow ---"
echo "-- schemas --"
sqlite3 "$RO_PM" "SELECT name FROM pragma_table_info('pm_open_position');" 2>&1 | tr '\n' ' '; echo ""
echo "pm_paper_trade cols:"; sqlite3 "$RO_PM" "SELECT name FROM pragma_table_info('pm_paper_trade');" 2>&1 | tr '\n' ' '; echo ""
echo "-- pm_open_position latest (Polymarket ingested positions) --"
sqlite3 "$RO_PM" -cmd ".mode column" "SELECT datetime(MAX(as_of_ts),'unixepoch') max_as_of FROM pm_open_position;" 2>&1
sqlite3 "$RO_PM" -cmd ".mode column" "SELECT datetime(MAX(updated_ts),'unixepoch') max_updated FROM pm_open_position;" 2>&1
echo "-- pm_paper_trade latest --"
sqlite3 "$RO_PM" -cmd ".mode column" "SELECT datetime(MAX(ts),'unixepoch') max_ts FROM pm_paper_trade;" 2>&1
echo "-- pm_subdivision_order: last filled per day, final 12 days --"
sqlite3 "$RO_PM" -cmd ".mode column" "SELECT date(COALESCE(response_ts,submitted_ts),'unixepoch') d, COUNT(*) fills FROM pm_subdivision_order WHERE dry_run=0 AND outcome_status='filled' GROUP BY d ORDER BY d DESC LIMIT 12;" 2>&1

echo "=== END PROBE 4 ==="
