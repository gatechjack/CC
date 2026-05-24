echo "=== full traceback context at 02:11:17 (15 lines after) ==="
journalctl -u trading-corp.service --since "2026-05-24 02:11:00" --until "2026-05-24 02:12:00" --no-pager | head -40
echo ""
echo "=== count + classify all tracebacks since deploy ==="
journalctl -u trading-corp.service --since "2026-05-24 02:00:00" --no-pager | grep -B1 -A6 "Traceback" | head -80
