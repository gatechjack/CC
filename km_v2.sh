true
DB=/home/azureuser/trading_corp/data/trading_corp.db
RO="sqlite3 -readonly $DB"
R0='2026-07-30T10:29:08+00:00'
echo "=== prod clock ==="; date -u +%Y-%m-%dT%H:%M:%SZ
echo "=== 1. Maggie snapshot: position count + updated_ts (expect 1 ticker, fresh) ==="
$RO "SELECT key, (SELECT COUNT(*) FROM json_each(value_json)) n_positions, updated_ts FROM agent_state WHERE agent='kalshi_copy_trader' AND key='positions:MaggieTheEagle';"
echo "--- Maggie tickers held (expect ONLY KXFEDDECISION-26SEP-H0) ---"
$RO "SELECT je.key FROM agent_state ast, json_each(ast.value_json) je WHERE ast.agent='kalshi_copy_trader' AND ast.key='positions:MaggieTheEagle';"
echo "=== 2. Maggie mass_disappearance events AFTER restart (exact-ISO) — EXPECT 0 ==="
$RO "SELECT COUNT(*) FROM audit_event WHERE actor='kalshi_copy_trader' AND kind='kalshi_copy_feed_anomaly' AND payload_json LIKE '%Maggie%' AND ts > '$R0';"
echo "=== 2b. ANY feed_anomaly after restart (any whale/reason) ==="
$RO "SELECT ts, substr(payload_json,1,150) FROM audit_event WHERE actor='kalshi_copy_trader' AND kind='kalshi_copy_feed_anomaly' AND ts > '$R0' ORDER BY ts;"
echo "=== 3. AI.EDGE snapshot (expect fresh updated_ts post-10:39, ~16 positions) ==="
$RO "SELECT key, (SELECT COUNT(*) FROM json_each(value_json)) n_positions, updated_ts FROM agent_state WHERE agent='kalshi_copy_trader' AND key='positions:AI.EDGE';"
echo "=== 4. last_poll_ts (expect advanced past 10:39) ==="
$RO "SELECT value_json, updated_ts FROM agent_state WHERE agent='kalshi_copy_trader' AND key='last_poll_ts';"
echo "=== 5. feed_anomaly_streak:* (expect NONE — R1 cleared it) ==="
$RO "SELECT key, value_json, updated_ts FROM agent_state WHERE agent='kalshi_copy_trader' AND key LIKE 'feed_anomaly_streak:%';"
echo "=== 6. kalshi_copy_trader audit kinds AFTER restart ==="
$RO "SELECT kind, COUNT(*) n, MIN(ts), MAX(ts) FROM audit_event WHERE actor='kalshi_copy_trader' AND ts > '$R0' GROUP BY kind ORDER BY n DESC;"
echo "=== 7. proposed_order kalshi_copy_trader since restart (expect none) ==="
$RO "SELECT ts, symbol, side, status, execution_mode FROM proposed_order WHERE strategy='kalshi_copy_trader' AND ts > '$R0' ORDER BY ts;"
echo "=== 8. journald: kalshi copy scan lines since 10:38 (apify rows / anomaly) ==="
journalctl -u trading-corp.service --since "2026-07-30 10:38:00" --no-pager 2>&1 | grep -iE "FEED ANOMALY|apify open_positions|kalshi_copy" | head -20
echo "=== DONE v2 ==="
