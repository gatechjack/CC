true
DB=/home/azureuser/trading_corp/data/trading_corp.db
RO="sqlite3 -readonly $DB"
R0='2026-07-30T10:29:08+00:00'
echo "=== prod clock ==="; date -u +%Y-%m-%dT%H:%M:%SZ
echo "=== 1. Maggie mass_disappearance since restart — EXPECT 0 (cycles 1,2,3) ==="
$RO "SELECT COUNT(*) FROM audit_event WHERE actor='kalshi_copy_trader' AND kind='kalshi_copy_feed_anomaly' AND payload_json LIKE '%Maggie%' AND ts > '$R0';"
echo "=== 2. ANY feed_anomaly (any whale/reason) since restart — EXPECT none ==="
$RO "SELECT ts, substr(payload_json,1,150) FROM audit_event WHERE actor='kalshi_copy_trader' AND kind='kalshi_copy_feed_anomaly' AND ts > '$R0' ORDER BY ts;"
echo "=== 3. Maggie snapshot (expect 1 ticker 26SEP-H0, updated_ts advancing each cycle) ==="
$RO "SELECT key, (SELECT COUNT(*) FROM json_each(value_json)) n_positions, updated_ts FROM agent_state WHERE agent='kalshi_copy_trader' AND key='positions:MaggieTheEagle';"
$RO "SELECT je.key FROM agent_state ast, json_each(ast.value_json) je WHERE ast.agent='kalshi_copy_trader' AND ast.key='positions:MaggieTheEagle';"
echo "=== 4. AI.EDGE updated_ts (expect fresh, advancing) ==="
$RO "SELECT key, (SELECT COUNT(*) FROM json_each(value_json)) n_positions, updated_ts FROM agent_state WHERE agent='kalshi_copy_trader' AND key='positions:AI.EDGE';"
echo "=== 5. last_poll_ts (expect ~10:59) ==="
$RO "SELECT value_json, updated_ts FROM agent_state WHERE agent='kalshi_copy_trader' AND key='last_poll_ts';"
echo "=== 6. feed_anomaly_streak:* (expect none) ==="
$RO "SELECT key, value_json FROM agent_state WHERE agent='kalshi_copy_trader' AND key LIKE 'feed_anomaly_streak:%';"
echo "=== 7. proposed_order since restart (expect none) ==="
$RO "SELECT ts, symbol, side, status FROM proposed_order WHERE strategy='kalshi_copy_trader' AND ts > '$R0' ORDER BY ts;"
echo "=== 8. kalshi_copy_trader audit kinds since restart ==="
$RO "SELECT kind, COUNT(*) n, MIN(ts), MAX(ts) FROM audit_event WHERE actor='kalshi_copy_trader' AND ts > '$R0' GROUP BY kind ORDER BY n DESC;"
echo "=== 9. journald: apify scans since 10:38 (expect 3, each ~17 rows) ==="
journalctl -u trading-corp.service --since "2026-07-30 10:38:00" --no-pager 2>&1 | grep "apify open_positions" | head
echo "--- FEED ANOMALY lines since restart (EXPECT NONE) ---"
journalctl -u trading-corp.service --since "2026-07-30 10:29:00" --no-pager 2>&1 | grep -c "FEED ANOMALY"
echo "=== DONE v3 ==="
