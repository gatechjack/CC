echo "=== PART A PROBE: today's trading (RO) ==="
date -u +%FT%TZ
DB=/home/azureuser/trading_corp/data/trading_corp.db
echo ""
echo "--- [0] engine now ---"
ps -eo pid,etime,cmd | grep -E 'python -X utf8 -m trading_corp' | grep -v grep
journalctl -u trading-corp --since '2026-11-17 21:07:00 UTC' --until '2026-11-17 21:12:00 UTC' --no-pager 2>&1 | grep -viE 'pykalshi|polymarket|pm_' | grep -iE 'config_hash|live|mace|boot|start' | head -12
echo ""
echo "--- [1] manage heartbeat + schema ---"
sqlite3 -readonly "$DB" "SELECT key, value_json, updated_ts FROM agent_state WHERE agent='robinhood_mace';" 2>&1
sqlite3 -readonly "$DB" ".schema mace_rung" 2>&1 | head -40
echo ""
echo "--- [2] census ---"
sqlite3 -readonly "$DB" "SELECT status, count(*) FROM mace_rung GROUP BY status;" 2>&1
sqlite3 -readonly "$DB" -cmd ".mode line" "SELECT * FROM mace_rung WHERE status NOT IN ('closed') ORDER BY symbol;" 2>&1
echo ""
echo "--- [3] live marks/spots (last tick written) ---"
sqlite3 -readonly "$DB" "SELECT rung_id, symbol, mark, spot, ts FROM mace_rung_live ORDER BY symbol;" 2>&1
echo ""
echo "--- [4] the SPY rung (full) ---"
sqlite3 -readonly "$DB" -cmd ".mode line" "SELECT * FROM mace_rung WHERE rung_id LIKE '%SPY%' AND exit_ts>='2026-11-17' ;" 2>&1
echo ""
echo "--- [5] audit 16:10-16:40Z full payloads ---"
sqlite3 -readonly "$DB" "SELECT ts, kind, substr(payload_json,1,420) FROM audit_event WHERE ts>='2026-11-17T16:10' AND ts<='2026-11-17T16:40' AND kind LIKE 'mace%' ORDER BY ts;" 2>&1
echo ""
echo "--- [6] leg-quote timeouts + watchdog events today ---"
sqlite3 -readonly "$DB" "SELECT ts, kind, substr(payload_json,1,300) FROM audit_event WHERE ts>='2026-11-17' AND kind IN ('mace_leg_quote_timeout','mace_marks_dead','mace_manage_stall','mace_manage_timeout','mace_mark_unavailable') ORDER BY ts;" 2>&1
echo ""
echo "--- [7] today's mace kind counts ---"
sqlite3 -readonly "$DB" "SELECT kind, count(*) FROM audit_event WHERE ts>='2026-11-17' AND kind LIKE 'mace%' GROUP BY kind ORDER BY 2 DESC;" 2>&1
echo ""
echo "--- [8] snapshot + entry eval + daily summary today ---"
sqlite3 -readonly "$DB" "SELECT ts, kind, substr(payload_json,1,420) FROM audit_event WHERE ts>='2026-11-17' AND kind IN ('mace_equity_snapshot','mace_daily_summary') ORDER BY ts;" 2>&1
sqlite3 -readonly "$DB" "SELECT ts, substr(payload_json,1,300) FROM audit_event WHERE ts>='2026-11-17T20:40' AND ts<='2026-11-17T21:00' AND kind='mace_entry_eval' ORDER BY ts;" 2>&1
echo ""
echo "--- [9] exits + entries today, day realized ---"
sqlite3 -readonly "$DB" "SELECT rung_id, status, exit_reason, exit_ts, exit_debit, realized_pnl FROM mace_rung WHERE exit_ts>='2026-11-17';" 2>&1
sqlite3 -readonly "$DB" "SELECT coalesce(sum(realized_pnl),0) FROM mace_rung WHERE exit_ts>='2026-11-17';" 2>&1
sqlite3 -readonly "$DB" "SELECT ts, kind, substr(payload_json,1,200) FROM audit_event WHERE ts>='2026-11-17' AND kind IN ('mace_entry_start','mace_entry_fill','mace_entry_standdown') ORDER BY ts;" 2>&1
echo ""
echo "--- [10] session health: last auth events + reconcile errors today by hour + since restart ---"
sqlite3 -readonly "$DB" "SELECT ts, kind FROM audit_event WHERE kind LIKE 'rh_auth%' ORDER BY ts DESC LIMIT 4;" 2>&1
sqlite3 -readonly "$DB" "SELECT strftime('%H',ts) h, count(*) FROM audit_event WHERE kind='mace_reconcile_open_orders_error' AND ts>='2026-11-17' GROUP BY 1 ORDER BY 1;" 2>&1
echo "errors since 21:00Z (post-restart):"
sqlite3 -readonly "$DB" "SELECT count(*) FROM audit_event WHERE ts>='2026-11-17T21:00' AND (kind LIKE '%error%' OR kind LIKE '%fail%') AND kind LIKE 'mace%';" 2>&1
echo ""
echo "--- [11] journal: the exit window 16:15-16:35Z (mace lines) ---"
journalctl -u trading-corp --since '2026-11-17 16:15:00 UTC' --until '2026-11-17 16:35:00 UTC' --no-pager 2>&1 | grep -viE 'pykalshi|polymarket' | grep -iE 'mace|SPY' | head -60
echo ""
echo "--- [12] journal: last lines before the 16:29:35Z cancel ---"
journalctl -u trading-corp --since '2026-11-17 16:25:40 UTC' --until '2026-11-17 16:30:30 UTC' --no-pager 2>&1 | grep -viE 'pykalshi|polymarket' | tail -30
echo "=== END PART A PROBE ==="
