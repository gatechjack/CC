echo "=== observer online line since restart ==="
journalctl -u trading-corp.service --since "2026-05-24 03:38:00" --no-pager | grep -i "Sports Arb Observer online" | head -3
echo ""
echo "=== any sports_arb errors since restart ==="
journalctl -u trading-corp.service --since "2026-05-24 03:38:00" --no-pager | grep -iE "sports_arb.*ERROR|sports_arb.*Traceback" | head -10
echo ""
echo "=== current UTC + next expected cycle ==="
date -u
echo "Next cycle: ~04:38:44 UTC (12:38 AM ET) = 60 min after restart"
