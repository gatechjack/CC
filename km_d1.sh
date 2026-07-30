true
R=/home/azureuser/trading_corp/trading_corp
CFG=/home/azureuser/trading_corp/config/strategies.yaml
DB=/home/azureuser/trading_corp/data/trading_corp.db
chk(){ got=$(tr -d '\r' < "$1" | md5sum | cut -d' ' -f1); if [ "$got" = "$2" ]; then echo "PASS  $1"; else echo "FAIL  $1  got=$got  exp=$2"; fi; }
echo "=== GATE-A: prod files == pre-change baseline (LF-md5)? ==="
chk "$R/agents/strategies/kalshi_copy_trader.py" 720df3d8c5cadef044176566a09db3b9
chk "$R/main.py" 302c06e7776ae62fba576c8d66039ad1
chk "$CFG" 6af510f67425a82f4208677a5c4558ef
chk "$R/brokers/kalshi.py" 18626cf0ddcdf6c3663be7d9602abbba
echo "=== service (expect MainPID 450695, NRestarts 0) ==="
systemctl show trading-corp.service -p MainPID -p NRestarts -p ExecMainStartTimestamp 2>&1
echo "=== auto_execute (expect true, will NOT be touched) ==="
grep -n "auto_execute" "$CFG" | grep -i -A0 "" | sed -n '1,3p'
grep -n -A3 "^kalshi_copy_trader:" "$CFG" | grep auto_execute
echo "=== Maggie latch still firing? (anomaly count last 60m) ==="
sqlite3 -readonly $DB "SELECT COUNT(*) n, MIN(ts) first, MAX(ts) last FROM audit_event WHERE actor='kalshi_copy_trader' AND kind='kalshi_copy_feed_anomaly' AND ts>=datetime('now','-60 minutes');"
echo "=== existing backups present? ==="
ls -la $R/agents/strategies/*.bak_feedhealth_r1r2* $R/*.bak_feedhealth_r1r2* $CFG.bak_feedhealth_r1r2* 2>&1 | head
echo "=== DONE d1 ==="
