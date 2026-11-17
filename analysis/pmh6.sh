echo "=== PM HEALTH RO PROBE 6 (arm-lists B2 + failing-today A1 DB evidence) ==="
date -u +%FT%TZ
ROOT=/home/azureuser/trading_corp
PMDB=$ROOT/data/prediction_markets.db
LEG=$ROOT/data/trading_corp.db
RO_PM="file:$PMDB?mode=ro"
RO_LEG="file:$LEG?mode=ro"
# per-account 30d-pre-incident window starts (anchor = account's last fill/latch)
J30=$(date -u -d 2026-10-09 +%s)   # jack last fill 11-08
K30=$(date -u -d 2026-10-04 +%s)   # karen last fill 11-03
M30=$(date -u -d 2026-09-28 +%s)   # marc latch 10-28
T30=$(date -u -d 2026-09-22 +%s)   # trey latch 10-22
echo "anchors J30=$J30 K30=$K30 M30=$M30 T30=$T30"

echo ""; echo "--- [1] FILLS per (account,category): total entry fills, last fill, 30d-pre-incident ---"
sqlite3 "$RO_PM" -cmd ".mode column" "SELECT account_id, category, COUNT(*) total_entry_fills, date(MAX(COALESCE(response_ts,submitted_ts)),'unixepoch') last_fill, SUM(CASE WHEN COALESCE(response_ts,submitted_ts) >= CASE account_id WHEN 'kalshi_jack' THEN $J30 WHEN 'kalshi_karen' THEN $K30 WHEN 'kalshi_marc' THEN $M30 WHEN 'kalshi_trey' THEN $T30 ELSE 0 END THEN 1 ELSE 0 END) fills_30d_pre FROM pm_subdivision_order WHERE dry_run=0 AND outcome_status='filled' AND is_exit=0 GROUP BY account_id, category ORDER BY account_id, total_entry_fills DESC;" 2>&1

echo ""; echo "--- [2] ACTIVE attachments per (account,category) ---"
sqlite3 "$RO_PM" -cmd ".mode column" "SELECT account_id, category, COUNT(*) n_active FROM pm_subdivision_attachment WHERE active=1 GROUP BY account_id, category ORDER BY account_id, category;" 2>&1

echo ""; echo "--- [3] ARM state per (account,category) ---"
sqlite3 "$RO_LEG" -cmd ".mode column" "SELECT substr(key,5) scope, json_extract(value_json,'\$.armed') armed, json_extract(value_json,'\$.latched') latch, json_extract(value_json,'\$.auto_trigger') trig FROM agent_state WHERE agent='pm_live' AND key LIKE 'arm:%' AND key<>'arm:global' ORDER BY key;" 2>&1

echo ""; echo "--- [4] Polymarket-fed table ts columns (to pin 'failing since') ---"
for t in pm_closed_position pm_paper_trade pm_open_position pm_analysis_cache pm_loss_grounding_cache pm_whale_score pm_category_stats; do
  echo "-- $t cols --"; sqlite3 "$RO_PM" "SELECT group_concat(name,' ') FROM pragma_table_info('$t');" 2>&1
done

echo ""; echo "--- [5] freshest row per Polymarket-fed table (A1 'failing since') ---"
echo "pm_closed_position:"; sqlite3 "$RO_PM" "SELECT datetime(MAX(ingested_ts),'unixepoch') max_ingested, datetime(MAX(updated_ts),'unixepoch') max_updated FROM pm_closed_position;" 2>&1
echo "pm_paper_trade:"; sqlite3 "$RO_PM" "SELECT datetime(MAX(opened_ts),'unixepoch') max_opened, datetime(MAX(updated_ts),'unixepoch') max_updated FROM pm_paper_trade;" 2>&1
echo "pm_open_position:"; sqlite3 "$RO_PM" "SELECT datetime(MAX(refreshed_ts),'unixepoch') max_refreshed FROM pm_open_position;" 2>&1
echo "pm_closed_position by day (last 12):"; sqlite3 "$RO_PM" -cmd ".mode column" "SELECT date(ingested_ts,'unixepoch') d, COUNT(*) n FROM pm_closed_position WHERE ingested_ts IS NOT NULL GROUP BY d ORDER BY d DESC LIMIT 12;" 2>&1

echo ""; echo "--- [6] pm_web (prediction-markets-web) journal: Data API / 410 / positions / activity (retained) ---"
echo "earliest pm_web journal line:"; journalctl -u prediction-markets-web --no-pager -o short-iso 2>/dev/null | head -1 | cut -c1-32
journalctl -u prediction-markets-web --no-pager 2>/dev/null | grep -iE "Data API v1|410|/positions|/activity|closed-position|leaderboard|retired" | tail -25
echo "pm_web 410 count:"; journalctl -u prediction-markets-web --no-pager 2>/dev/null | grep -c "Data API v1 has been retired"

echo "=== END PROBE 6 ==="
