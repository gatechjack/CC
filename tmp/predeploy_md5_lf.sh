#!/usr/bin/env bash
# Verify prod files match HEAD~1 (= prod state before my commit).
set -u
B=/home/azureuser/trading_corp
echo "=== prod md5s ==="
md5sum $B/trading_corp/data/polymarket_data_api_client.py 2>&1
md5sum $B/trading_corp/scripts/seed_polymarket_watchlist_deep.py 2>&1
echo
echo "=== systemd dir (expect no pm-watchlist-deep files yet) ==="
ls -la /etc/systemd/system/trading-corp-pm-watchlist-deep* 2>&1 || echo "(none — expected)"
echo
echo "=== timer list (sanity) ==="
systemctl list-timers --no-pager 2>&1 | grep -E "(NEXT|trading-corp)" | head -10
echo
echo "=== DONE ==="
