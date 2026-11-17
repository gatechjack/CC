echo "=== PM HEALTH RO PROBE 7 (freshness retry + timers + wallets for A3) ==="
date -u +%FT%TZ
ROOT=/home/azureuser/trading_corp
RO_PM="file:$ROOT/data/prediction_markets.db?mode=ro"

echo ""; echo "--- [1] freshest row per Polymarket-fed table (busy-timeout) ---"
echo "pm_closed_position:"; sqlite3 -cmd ".timeout 8000" "$RO_PM" "SELECT datetime(MAX(ingested_ts),'unixepoch') max_ingested, datetime(MAX(updated_ts),'unixepoch') max_updated FROM pm_closed_position;" 2>&1
echo "pm_paper_trade:"; sqlite3 -cmd ".timeout 8000" "$RO_PM" "SELECT datetime(MAX(opened_ts),'unixepoch') max_opened, datetime(MAX(last_observed_ts),'unixepoch') max_last_obs FROM pm_paper_trade;" 2>&1
echo "pm_analysis_cache:"; sqlite3 -cmd ".timeout 8000" "$RO_PM" "SELECT datetime(MAX(created_ts),'unixepoch') max_created FROM pm_analysis_cache;" 2>&1
echo "pm_loss_grounding_cache:"; sqlite3 -cmd ".timeout 8000" "$RO_PM" "SELECT datetime(MAX(grounded_ts),'unixepoch') max_grounded FROM pm_loss_grounding_cache;" 2>&1
echo "pm_category_stats:"; sqlite3 -cmd ".timeout 8000" "$RO_PM" "SELECT datetime(MAX(updated_ts),'unixepoch') max_updated FROM pm_category_stats;" 2>&1
echo "pm_closed_position ingest by day (last 12):"; sqlite3 -cmd ".timeout 8000" -cmd ".mode column" "$RO_PM" "SELECT date(ingested_ts,'unixepoch') d, COUNT(*) n FROM pm_closed_position WHERE ingested_ts IS NOT NULL GROUP BY d ORDER BY d DESC LIMIT 12;" 2>&1

echo ""; echo "--- [2] systemd timers (any PM cron-equivalent?) ---"
systemctl list-timers --all --no-pager 2>&1 | head -30

echo ""; echo "--- [3] 5 full wallet addrs (active attach) with v1-era rows, for A3 v2 probe ---"
sqlite3 -cmd ".timeout 8000" -cmd ".mode column" "$RO_PM" "SELECT a.wallet, COUNT(DISTINCT a.account_id||a.category) n_subs, (SELECT COUNT(*) FROM pm_closed_position c WHERE c.wallet=a.wallet) closed_rows, (SELECT COUNT(*) FROM pm_open_position o WHERE o.wallet=a.wallet) open_rows FROM pm_subdivision_attachment a WHERE a.active=1 GROUP BY a.wallet ORDER BY closed_rows DESC LIMIT 8;" 2>&1

echo ""; echo "--- [4] curl present on box? ---"
command -v curl 2>&1; command -v python3 2>&1

echo "=== END PROBE 7 ==="
