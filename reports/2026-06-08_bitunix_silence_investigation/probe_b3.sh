#!/usr/bin/env bash
# Thread B call 3: B3 Robinhood pickle state (isolated; call-2 was az head-truncated by an
# over-broad B4 grep that matched routine "...failed" API warnings). Pickle file info is
# printed LAST so it survives any az tail-truncation. READ-ONLY.
set -uo pipefail

echo "=== B3a: robinhood-related journal lines (last 72h, UTC) — context (bounded) ==="
journalctl -u trading-corp --since "72 hours ago" --utc --no-pager 2>/dev/null \
  | grep -iE "robinhood|robin_stocks" | tail -12
echo "  ^ empty = no robinhood log lines in 72h"
echo

echo "=== B3b: Robinhood pickle file(s) — mtime + size (KEY: when did it last rotate?) ==="
echo "-- /home/azureuser/trading_corp/data/*.pickle --"
ls -la --time-style=full-iso /home/azureuser/trading_corp/data/*.pickle 2>/dev/null || echo "  (none in data/)"
echo "-- /home/azureuser/.tokens/*.pickle --"
ls -la --time-style=full-iso /home/azureuser/.tokens/*.pickle 2>/dev/null || echo "  (none in ~/.tokens/)"
echo "-- any *.pickle under /home/azureuser (bounded, mtime UTC) --"
find /home/azureuser -maxdepth 4 -iname "*.pickle" -printf "%TY-%Tm-%TdT%TH:%TM:%TSZ  %10s b  %p\n" 2>/dev/null | head -15
echo "=== END call B3 (read-only) ==="
