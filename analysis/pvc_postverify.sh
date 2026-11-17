echo "=== PVC POST-RESTART VERIFY mode=${MODE:-v2} (READ-ONLY) ==="
date -u +%FT%TZ
case "${MODE:-}" in v2|v2chase) ;; *) echo "ABORT: MODE must be v2 or v2chase"; exit 1;; esac
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
if [ "$MODE" = "v2chase" ]; then
  echo "execution=$(sha "$ROOT/trading_corp/prediction_markets/execution.py") (want b4f75cc64793c3fb)"
  echo "live_driver=$(sha "$ROOT/trading_corp/prediction_markets/live_driver.py") (want 69dc1040abe53bc9)"
  echo "heartbeat=$(sha "$ROOT/trading_corp/prediction_markets/heartbeat.py") (want 227eda723301c288)"
  echo "db=$(sha "$ROOT/trading_corp/prediction_markets/db.py") (want a4d8ddfd2254830a)"
fi

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

echo "--- [6] first fills since restart (leg_audit on each) ---"
sqlite3 "$RO_PM" -cmd ".mode line" "SELECT account_id, category, ticker, outcome_leg, fill_count, datetime(COALESCE(response_ts,submitted_ts),'unixepoch') ts, substr(leg_audit,1,160) leg_audit FROM pm_subdivision_order WHERE dry_run=0 AND outcome_status='filled' AND COALESCE(response_ts,submitted_ts)>=$NOW-21600 ORDER BY COALESCE(response_ts,submitted_ts) DESC LIMIT 6;" 2>&1

echo "--- [7] cash snapshots still updating ---"
sqlite3 "$RO_PM" -cmd ".mode column" "SELECT account_id, datetime(MAX(snapshot_ts),'unixepoch') latest FROM pm_shard_balance_snapshot GROUP BY account_id;" 2>&1

if [ "$MODE" = "v2chase" ]; then
  echo "--- [8] GATE C (chase) visibility: migration 025 + the chase_skips counter PRESENT and MOVING ---"
  echo "schema head (EXPECT 25; 24 = pm_cli cron has not yet self-applied 025 -- code runs on defaults, re-check in ~15min):"
  sqlite3 "$RO_PM" "SELECT MAX(version) FROM schema_version;" 2>&1
  echo "pm_subdivision chase columns (EXPECT both listed; values NULL = code defaults 2.0c abs / relative off):"
  sqlite3 "$RO_PM" "SELECT name FROM pragma_table_info('pm_subdivision') WHERE name IN ('max_chase_cents','chase_upside_pct');" 2>&1
  sqlite3 "$RO_PM" -cmd ".mode column" "SELECT account_id, category, max_chase_cents, chase_upside_pct FROM pm_subdivision WHERE active=1 AND (max_chase_cents IS NOT NULL OR chase_upside_pct IS NOT NULL);" 2>&1
  echo "pm_driver_heartbeat.chase_skips column (EXPECT listed):"
  sqlite3 "$RO_PM" "SELECT name FROM pragma_table_info('pm_driver_heartbeat') WHERE name='chase_skips';" 2>&1
  echo "chase_skips sample 1 (NULL = pre-025 write path; 0 = evaluated, no skips; >0 = gate C blocking):"
  sqlite3 "$RO_PM" -cmd ".mode column" "SELECT account_id, category, chase_skips, n_signals, ($NOW-evaluated_ts) age_s, updated_ts FROM pm_driver_heartbeat ORDER BY account_id, category;" 2>&1
  echo "sleeping 20s for the MOVING check (driver cycles ~7s; updated_ts must advance + chase_skips stay NOT NULL)..."
  sleep 20
  NOW2=$(date +%s)
  echo "chase_skips sample 2:"
  sqlite3 "$RO_PM" -cmd ".mode column" "SELECT account_id, category, chase_skips, n_signals, ($NOW2-evaluated_ts) age_s, updated_ts FROM pm_driver_heartbeat ORDER BY account_id, category;" 2>&1
  echo "VERDICT guide: MOVING = updated_ts advanced between samples AND chase_skips is NOT NULL on evaluated rows."
  echo "gate-C skip log lines since restart (rate-limited to 1/60s per sub; the per-cycle truth is the counter):"
  journalctl -u trading-corp --since "$ST" --no-pager 2>/dev/null | grep -c "gate-C CHASE skip"
  journalctl -u trading-corp --since "$ST" --no-pager 2>/dev/null | grep "gate-C CHASE skip" | tail -3
  echo "chase_unpriced skips (EXPECT ~0: v2 serves avg_price on every row; many = a client regression):"
  journalctl -u trading-corp --since "$ST" --no-pager 2>/dev/null | grep -c "chase_unpriced"
else
  echo "--- [8] gate C: NOT DEPLOYED in mode=v2 (chase files untouched) -- section skipped ---"
fi
echo "=== END POST-VERIFY ==="
