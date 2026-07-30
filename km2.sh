true
DB=/home/azureuser/trading_corp/data/trading_corp.db
RO="sqlite3 -readonly $DB"
echo "=== A1. process uptime for PID 429030 (restart at ~18:00 would break settlement theory) ==="
ps -o pid,lstart,etimes,cmd -p 429030 2>&1 | head -5
echo "=== A2. all trading_corp python procs (confirm no restart spawned new PID) ==="
pgrep -af "trading_corp" 2>&1 | head -10
echo "=== A3. systemd service (name + ActiveEnterTimestamp = last (re)start) ==="
systemctl list-units --type=service --no-pager 2>/dev/null | grep -iE "trad|corp|kalshi" | head -5
echo "=== B1. discover log locations ==="
ls -la /home/azureuser/trading_corp 2>&1 | grep -iE "log|\.out" | head -20
find /home/azureuser/trading_corp -maxdepth 2 -name "*.log" -newermt "2026-07-29" 2>/dev/null | head -20
echo "=== C1. sqlite tables ==="
sqlite3 -readonly $DB ".tables" 2>&1
echo "=== C2. kalshi_round_trips for KXFEDDECISION (any settlement recorded our side) ==="
$RO "SELECT entry_ts, ticker, outcome_bet, qty, realized_pnl, won, resolved_ts, division FROM kalshi_round_trips WHERE ticker LIKE 'KXFEDDECISION%' ORDER BY entry_ts;"
echo "=== C3. any audit_event mentioning KXFEDDECISION-26JUL since 07-29 (settlement/resolve traces) ==="
$RO "SELECT ts, actor, kind FROM audit_event WHERE payload_json LIKE '%KXFEDDECISION-26JUL%' AND ts>='2026-07-29' ORDER BY ts LIMIT 20;"
echo "=== D1. STEP4 impact: kalshi_copy_trader NON-anomaly audit since 18:00 (did anything else fire?) ==="
$RO "SELECT ts, kind FROM audit_event WHERE actor='kalshi_copy_trader' AND ts>='2026-07-29T18:00' AND kind<>'kalshi_copy_feed_anomaly' ORDER BY ts;"
echo "=== D2. proposed_order kalshi_copy_trader since 18:00 (any synthetic exits placed?) ==="
$RO "SELECT ts, symbol, side, status, execution_mode FROM proposed_order WHERE strategy='kalshi_copy_trader' AND ts>='2026-07-29T18:00' ORDER BY ts;"
echo "=== D3. kalshi_copy_trading round_trips since 07-29 (any new closes?) ==="
$RO "SELECT entry_ts, ticker, outcome_bet, qty, realized_pnl, resolved_ts FROM kalshi_round_trips WHERE division='kalshi_copy_trading' AND entry_ts>='2026-07-29' ORDER BY entry_ts;"
echo "=== D4. broad error/fail/traceback audit last 6h (scanner health) ==="
$RO "SELECT actor, kind, COUNT(*) n FROM audit_event WHERE ts>=datetime('now','-6 hours') AND (kind LIKE '%error%' OR kind LIKE '%fail%' OR kind LIKE '%trace%' OR kind LIKE '%anomal%') GROUP BY actor, kind ORDER BY n DESC;"
echo "=== E1. strategies.yaml location + kalshi_copy_trader block (auto_execute + feed_health) ==="
YF=$(ls /home/azureuser/trading_corp/config/strategies.yaml 2>/dev/null || find /home/azureuser/trading_corp -maxdepth 3 -name strategies.yaml 2>/dev/null | head -1)
echo "YAML=$YF"
grep -n -A40 "kalshi_copy_trader:" "$YF" 2>&1 | head -50
echo "=== DONE km2 ==="
