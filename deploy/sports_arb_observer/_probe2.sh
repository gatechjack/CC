echo "=== service status ==="
systemctl is-active trading-corp.service
echo ""
echo "=== last 60 lines from journal (unfiltered) ==="
journalctl -u trading-corp.service --since "10 minutes ago" --no-pager | tail -60
