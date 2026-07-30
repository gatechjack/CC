true
echo "=== division arming / broker logins at boot (10:29-10:33) ==="
journalctl -u trading-corp.service --since "2026-07-30 10:29:00" --until "2026-07-30 10:33:30" --no-pager 2>&1 | grep -iE "scanner online|armed|paper=False|--live-divisions|logged in|login ok|robinhood|kalshi login|bitunix|pmcc|pead|standby|halt" | head -50
echo "=== total Traceback since boot (EXPECT 0) ==="
journalctl -u trading-corp.service --since "2026-07-30 10:29:00" --no-pager 2>&1 | grep -c "Traceback"
echo "=== ERROR lines since boot (excl known noise) ==="
journalctl -u trading-corp.service --since "2026-07-30 10:29:00" --no-pager 2>&1 | grep -E " ERROR " | grep -viE "yfinance|BTCUSDT|could not resolve symbol|EODHD|delisted|playwright" | head -20
echo "=== service ==="
systemctl show trading-corp.service -p MainPID -p ActiveState -p NRestarts 2>&1
echo "=== DONE v4 ==="
