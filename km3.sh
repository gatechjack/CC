true
echo "=== A. trading-corp.service start facts (restart at ~18:00?) ==="
systemctl show trading-corp.service -p MainPID -p ActiveEnterTimestamp -p ExecMainStartTimestamp -p NRestarts 2>&1
echo "=== B. actual engine PID start times ==="
ps -o pid,lstart,etime,etimes -p 450695 2>&1
ps -o pid,lstart,etime,etimes -p 450709 2>&1
echo "=== C. journald: restart/anomaly/apify around 17:50-18:20 UTC (may need perms) ==="
journalctl -u trading-corp.service --since "2026-07-29 17:50:00" --until "2026-07-29 18:20:00" --no-pager 2>&1 | grep -iE "apify|open_positions|anomaly|FEED ANOMALY|Started|Stopped|Main process" | head -40
echo "=== D. journald: apify open_positions row-count lines 17:45-18:15 (batch size drop?) ==="
journalctl -u trading-corp.service --since "2026-07-29 17:45:00" --until "2026-07-29 18:15:00" --no-pager 2>&1 | grep -iE "apify|open_positions" | head -30
echo "=== E. journald perms probe (1 recent line) ==="
journalctl -u trading-corp.service -n 1 --no-pager 2>&1 | head -3
echo "=== DONE km3 ==="
