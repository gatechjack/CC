true
echo "=== boot errors/tracebacks since restart 10:29 (excluding known noise) ==="
journalctl -u trading-corp.service --since "2026-07-30 10:29:00" --no-pager 2>&1 | grep -iE "traceback|error|exception|import" | grep -viE "yfinance|could not resolve symbol|EODHD|playwright|delisted|BTCUSDT" | head -40
echo "=== kalshi copy scanner online + trade-tape source ==="
journalctl -u trading-corp.service --since "2026-07-30 10:29:00" --no-pager 2>&1 | grep -iE "kalshi copy trader scanner online|trade-tape source|copy trader:" | head -6
echo "=== process ==="
systemctl show trading-corp.service -p MainPID -p ActiveState -p NRestarts 2>&1
echo "=== boot line count (sanity: engine producing logs) ==="
journalctl -u trading-corp.service --since "2026-07-30 10:29:00" --no-pager 2>&1 | wc -l
echo "=== DONE v1 ==="
