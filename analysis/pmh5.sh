echo "=== PM HEALTH RO PROBE 5 (pin signal-death) ==="
date -u +%FT%TZ
ROOT=/home/azureuser/trading_corp
RO_PM="file:$ROOT/data/prediction_markets.db?mode=ro"

echo ""; echo "--- pm_open_position: last Polymarket ingest (refreshed_ts) ---"
sqlite3 "$RO_PM" -cmd ".mode column" "SELECT datetime(MAX(refreshed_ts),'unixepoch') last_refresh, COUNT(*) rows_total FROM pm_open_position;" 2>&1
echo "-- distinct refresh days, last 15 --"
sqlite3 "$RO_PM" -cmd ".mode column" "SELECT date(refreshed_ts,'unixepoch') d, COUNT(*) n FROM pm_open_position GROUP BY d ORDER BY d DESC LIMIT 15;" 2>&1

echo ""; echo "--- pm_paper_trade: last observed/updated (whale paper capture) ---"
sqlite3 "$RO_PM" -cmd ".mode column" "SELECT datetime(MAX(last_observed_ts),'unixepoch') last_obs, datetime(MAX(updated_ts),'unixepoch') last_upd, datetime(MAX(opened_ts),'unixepoch') last_open FROM pm_paper_trade;" 2>&1
echo "-- paper opens by day, last 15 --"
sqlite3 "$RO_PM" -cmd ".mode column" "SELECT date(opened_ts,'unixepoch') d, COUNT(*) n FROM pm_paper_trade WHERE opened_ts IS NOT NULL GROUP BY d ORDER BY d DESC LIMIT 15;" 2>&1

echo ""; echo "--- pm_closed_position: last close ingest ---"
sqlite3 "$RO_PM" "SELECT name FROM pragma_table_info('pm_closed_position');" 2>&1 | tr '\n' ' '; echo ""

echo "=== END PROBE 5 ==="
