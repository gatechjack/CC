echo "=== PM HEALTH RO PROBE 1 ==="
date -u +%FT%TZ
ROOT=/home/azureuser/trading_corp
PMDB=$ROOT/data/prediction_markets.db
LEG=$ROOT/data/trading_corp.db
RO_PM="file:$PMDB?mode=ro"
RO_LEG="file:$LEG?mode=ro"
NOW=$(date +%s)
echo "NOW_EPOCH=$NOW"

echo ""; echo "--- [0] DF / ---"
df -h /

echo ""; echo "--- [1] trading-corp service ---"
systemctl show trading-corp -p MainPID -p NRestarts -p ActiveState -p SubState -p ExecMainStartTimestamp -p ActiveEnterTimestamp 2>&1

echo ""; echo "--- [2] prediction-markets-web service ---"
systemctl show prediction-markets-web -p MainPID -p NRestarts -p ActiveState -p SubState -p ExecMainStartTimestamp -p ActiveEnterTimestamp 2>&1

echo ""; echo "--- [3] box git tip + status ---"
git -C "$ROOT" rev-parse HEAD 2>&1 | head -1
git -C "$ROOT" log --oneline -1 2>&1 | head -1
git -C "$ROOT" status -sb 2>&1 | head -4

echo ""; echo "--- [4] DB files (size/mtime) ---"
ls -la "$ROOT/data"/prediction_markets.db* "$ROOT/data"/trading_corp.db* 2>&1

echo ""; echo "--- [5] PM schema sanity ---"
echo "user_version:"; sqlite3 "$RO_PM" "PRAGMA user_version;" 2>&1
echo "pm_ tables:"; sqlite3 "$RO_PM" "SELECT group_concat(name,' ') FROM sqlite_master WHERE type='table' AND name LIKE 'pm_%';" 2>&1

echo ""; echo "--- [6] schema pm_subdivision_order ---"
sqlite3 "$RO_PM" ".schema pm_subdivision_order" 2>&1

echo ""; echo "--- [7] ARM STATE (agent_state pm_live) ---"
echo "n_pm_live:"; sqlite3 "$RO_LEG" "SELECT COUNT(*) FROM agent_state WHERE agent='pm_live';" 2>&1
echo "-- 7a GLOBAL (raw) --"
sqlite3 "$RO_LEG" -cmd ".mode line" "SELECT key, value_json, updated_ts FROM agent_state WHERE agent='pm_live' AND key='arm:global';" 2>&1
echo "-- 7b per-sub parsed, ordered by ts (incident timeline) --"
sqlite3 "$RO_LEG" -cmd ".mode column" -cmd ".width 34 5 5 24 4 20" "SELECT key, json_extract(value_json,'\$.armed') AS armed, json_extract(value_json,'\$.latched') AS latch, json_extract(value_json,'\$.auto_trigger') AS trig, json_extract(value_json,'\$.manual_exit_required') AS mer, json_extract(value_json,'\$.ts') AS ts FROM agent_state WHERE agent='pm_live' AND key LIKE 'arm:%' AND key<>'arm:global' ORDER BY ts, key;" 2>&1
echo "-- 7c latch grouping (trigger x timestamp x count) --"
sqlite3 "$RO_LEG" -cmd ".mode column" "SELECT json_extract(value_json,'\$.latched') AS latch, json_extract(value_json,'\$.auto_trigger') AS trig, json_extract(value_json,'\$.ts') AS ts, COUNT(*) AS n FROM agent_state WHERE agent='pm_live' AND key LIKE 'arm:%' AND key<>'arm:global' GROUP BY latch, trig, ts ORDER BY ts;" 2>&1
echo "-- 7d armed-vs-latched totals --"
sqlite3 "$RO_LEG" -cmd ".mode column" "SELECT json_extract(value_json,'\$.armed') AS armed, json_extract(value_json,'\$.latched') AS latch, COUNT(*) AS n FROM agent_state WHERE agent='pm_live' AND key LIKE 'arm:%' AND key<>'arm:global' GROUP BY armed, latch;" 2>&1

echo ""; echo "--- [8] task heartbeat (per account) ---"
sqlite3 "$RO_PM" -cmd ".mode column" "SELECT account_id, last_cycle_ts, ($NOW-last_cycle_ts) AS age_s FROM pm_driver_task_heartbeat ORDER BY account_id;" 2>&1

echo ""; echo "--- [9] driver heartbeat (per account+category) ---"
sqlite3 "$RO_PM" -cmd ".mode column" "SELECT account_id, category, evaluated_ts, ($NOW-evaluated_ts) AS age_s, n_signals, placed, errors, ceiling_latched, state FROM pm_driver_heartbeat ORDER BY account_id, category;" 2>&1
echo "-- 9b heartbeat age summary per account --"
sqlite3 "$RO_PM" -cmd ".mode column" "SELECT account_id, COUNT(*) AS cats, MIN($NOW-evaluated_ts) AS min_age_s, MAX($NOW-evaluated_ts) AS max_age_s, SUM(n_signals) AS sig, SUM(placed) AS placed FROM pm_driver_heartbeat GROUP BY account_id;" 2>&1

echo ""; echo "--- [10] orders: last filled entry / last attempt / counts per account (live dry_run=0) ---"
sqlite3 "$RO_PM" -cmd ".mode column" "SELECT account_id, MAX(CASE WHEN outcome_status='filled' AND is_exit=0 THEN COALESCE(response_ts,submitted_ts) END) AS last_filled_entry_ts, MAX(COALESCE(response_ts,submitted_ts)) AS last_attempt_ts, COUNT(*) AS n_rows FROM pm_subdivision_order WHERE dry_run=0 GROUP BY account_id ORDER BY account_id;" 2>&1
echo "-- 10b human-readable last-attempt --"
sqlite3 "$RO_PM" -cmd ".mode column" "SELECT account_id, datetime(MAX(COALESCE(response_ts,submitted_ts)),'unixepoch') AS last_attempt_utc, datetime(MAX(CASE WHEN outcome_status='filled' AND is_exit=0 THEN COALESCE(response_ts,submitted_ts) END),'unixepoch') AS last_filled_entry_utc FROM pm_subdivision_order WHERE dry_run=0 GROUP BY account_id ORDER BY account_id;" 2>&1

echo ""; echo "--- [11] fills per account per day since 2026-10-16 (dry_run=0, filled) ---"
sqlite3 "$RO_PM" -cmd ".mode column" "SELECT date(COALESCE(response_ts,submitted_ts),'unixepoch') AS d, account_id, COUNT(*) AS fills FROM pm_subdivision_order WHERE dry_run=0 AND outcome_status='filled' AND COALESCE(response_ts,submitted_ts)>=strftime('%s','2026-10-16') GROUP BY d, account_id ORDER BY d, account_id;" 2>&1

echo ""; echo "--- [12] journal retention + traceback count ---"
echo "earliest retained line:"; journalctl -u trading-corp --no-pager -o short-iso 2>/dev/null | head -1 | cut -c1-32
echo "traceback count (retained):"; journalctl -u trading-corp --no-pager 2>/dev/null | grep -c "Traceback (most recent call last)"

echo ""; echo "--- [13] journal: auth/attestation/latch/boot_reconcile (tail 40) ---"
journalctl -u trading-corp --no-pager 2>/dev/null | grep -iE "attestation|403|401|auth_failure|boot.?reconcile|forbidden|unauthor|latch|OrderPlacementError" | tail -40

echo "=== END PROBE 1 ==="
