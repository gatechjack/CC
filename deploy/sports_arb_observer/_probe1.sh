journalctl -u trading-corp.service --since "5 minutes ago" --no-pager | grep -E "Sports Arb|Sports Scout|sports_arb|ERROR|Traceback" | tail -40
