echo "=== PV2 POST-RESTART VERIFY (READ-ONLY) ==="
date -u +%FT%TZ
ROOT=/home/azureuser/trading_corp
RO_PM="file:$ROOT/data/prediction_markets.db?mode=ro"
RO_LEG="file:$ROOT/data/trading_corp.db?mode=ro"
NOW=$(date +%s)
sha() { tr -d '\r' < "$1" | sha256sum | cut -c1-16; }

echo "--- [1] services: PIDs changed + started AFTER the applied file's mtime ---"
echo "pre-restart reference: engine MainPID=809209 (boot 2026-10-27), pm_web MainPID=698062 (boot 2026-10-11)"
systemctl show trading-corp -p MainPID -p ActiveState -p NRestarts -p ActiveEnterTimestamp 2>&1
systemctl show prediction-markets-web -p MainPID -p ActiveState -p NRestarts -p ActiveEnterTimestamp 2>&1
echo "client file mtime: $(stat -c %y "$ROOT/trading_corp/data/polymarket_data_api_client.py")"

echo "--- [2] live files still == TARGET ---"
echo "client=$(sha "$ROOT/trading_corp/data/polymarket_data_api_client.py") (want c88cfce6a16cc4a0)"
echo "loss_grounding=$(sha "$ROOT/trading_corp/prediction_markets/loss_grounding.py") (want 42d166575b2f39bd)"

echo "--- [3] journal since engine restart: ZERO 410s, v2 success lines, HARD-FAILs ---"
ST=$(systemctl show trading-corp -p ActiveEnterTimestamp --value | sed 's/^[A-Za-z]* //')
echo "since: $ST"
echo "v1-retired 410 count (MUST be 0):"
journalctl -u trading-corp --since "$ST" --no-pager 2>/dev/null | grep -c "Data API v1 has been retired"
echo "v2 data-api success lines (INFO 'rows in', should grow):"
journalctl -u trading-corp --since "$ST" --no-pager 2>/dev/null | grep -c "polymarket-data-api .*rows in"
echo "HARD-FAIL lines (should be 0; any line below is a problem):"
journalctl -u trading-corp --since "$ST" --no-pager 2>/dev/null | grep "HARD-FAIL" | tail -5
echo "positions fetch failures (should be 0):"
journalctl -u trading-corp --since "$ST" --no-pager 2>/dev/null | grep -c "/positions fetch failed"
echo "429 warnings (0 expected; a few tolerable, many = throttle problem):"
journalctl -u trading-corp --since "$ST" --no-pager 2>/dev/null | grep -c "HTTP 429"

echo "--- [4] boot-reconcile: all four accounts CLEAN, no new latch ---"
journalctl -u trading-corp --since "$ST" --no-pager 2>/dev/null | grep -E "boot-reconcile account=" | tail -8
echo "latch grouping (EXPECT unchanged: 14 boot_reconcile 2026-10-06 + 22 trey auth 10-22 + 22 marc auth 10-28):"
sqlite3 "$RO_LEG" -cmd ".mode column" "SELECT json_extract(value_json,'\$.latched') latch, json_extract(value_json,'\$.auto_trigger') trig, substr(json_extract(value_json,'\$.ts'),1,10) day, COUNT(*) n FROM agent_state WHERE agent='pm_live' AND key LIKE 'arm:%' AND key<>'arm:global' GROUP BY latch, trig, day ORDER BY day;" 2>&1
echo "arm:global (EXPECT armed:true):"
sqlite3 "$RO_LEG" "SELECT json_extract(value_json,'\$.armed') FROM agent_state WHERE agent='pm_live' AND key='arm:global';" 2>&1

echo "--- [5] signals flowing per account (heartbeats fresh; n_signals>0 where whales hold positions) ---"
sqlite3 "$RO_PM" -cmd ".mode column" "SELECT account_id, COUNT(*) cats, MIN($NOW-evaluated_ts) min_age_s, MAX($NOW-evaluated_ts) max_age_s, SUM(n_signals) sig_now, SUM(placed) placed_now FROM pm_driver_heartbeat GROUP BY account_id;" 2>&1
echo "jack:nba back in the driven set? (age should be small now)"
sqlite3 "$RO_PM" "SELECT account_id, category, ($NOW-evaluated_ts) age_s FROM pm_driver_heartbeat WHERE account_id='kalshi_jack' AND category='nba';" 2>&1

echo "--- [6] first fills since restart (leg_audit on each) ---"
sqlite3 "$RO_PM" -cmd ".mode line" "SELECT account_id, category, ticker, outcome_leg, fill_count, datetime(COALESCE(response_ts,submitted_ts),'unixepoch') ts, substr(leg_audit,1,160) leg_audit FROM pm_subdivision_order WHERE dry_run=0 AND outcome_status='filled' AND COALESCE(response_ts,submitted_ts)>=$NOW-21600 ORDER BY COALESCE(response_ts,submitted_ts) DESC LIMIT 6;" 2>&1

echo "--- [7] cash snapshots still updating ---"
sqlite3 "$RO_PM" -cmd ".mode column" "SELECT account_id, datetime(MAX(snapshot_ts),'unixepoch') latest FROM pm_shard_balance_snapshot GROUP BY account_id;" 2>&1
echo "=== END POST-VERIFY ==="
