set -u
echo "=== python trading processes (pid, elapsed, start, cmd) ==="
ps -eo pid,etimes,lstart,cmd 2>/dev/null | grep -iE 'trading_corp|main\.py|uvicorn|copy_trader' | grep -v grep | head -8
echo "=== systemd units matching trad/corp/engine/poly ==="
systemctl list-units --type=service --all 2>/dev/null | grep -iE 'trad|corp|engine|poly|copy' | head -8
echo "=== engine pids 621536 / 621550 alive? + start time ==="
ps -o pid,etimes,lstart,comm -p 621536 -p 621550 2>/dev/null
echo "=== recent copy-trader scan heartbeat (audit_event any kind, last 15m) ==="
DB=/home/azureuser/trading_corp/data/trading_corp.db
sqlite3 -readonly $DB "SELECT kind, substr(MAX(ts),1,19) last, COUNT(*) n FROM audit_event WHERE actor='polymarket_copy_trader' AND ts>=datetime('now','-6 hours') GROUP BY kind ORDER BY last DESC LIMIT 10;" 2>&1
echo "=== DONE ==="
