#!/usr/bin/env bash
set -u
B=/home/azureuser/trading_corp
echo "=== UTC time ===" ; date -u
echo
echo "=== md5 verification post-deploy ==="
md5sum $B/trading_corp/data/polymarket_data_api_client.py
md5sum $B/trading_corp/scripts/seed_polymarket_watchlist_deep.py
echo
echo "=== systemd timer state ==="
echo -n "enabled: "; systemctl is-enabled trading-corp-pm-watchlist-deep.timer
echo -n "active:  "; systemctl is-active  trading-corp-pm-watchlist-deep.timer
echo "next-fire:"
systemctl list-timers trading-corp-pm-watchlist-deep.timer --no-pager | head -3
echo
echo "=== Service still healthy ==="
systemctl is-active trading-corp
ps -ef | grep -E 'python.*trading_corp' | grep -v grep | head -2
echo
echo "=== DONE ==="
