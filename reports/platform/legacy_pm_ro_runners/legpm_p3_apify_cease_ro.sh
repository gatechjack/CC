set -u
echo "### PHASE-3 APIFY CESSATION CHECK (READ-ONLY) $(date -u +%FT%TZ) ###"
echo "## kalshi_copy_trader Apify/FEED-DOWN journal lines SINCE the flip (15:37:00Z) -- expect NONE ##"
journalctl -u trading-corp.service --since "2026-09-13 15:37:00" --no-pager 2>/dev/null | grep -iE "kalshi_copy_trader.*apify|kalshi_copy_trader.*FEED DOWN|apify open_positions" | tail -12 || echo "  (grep found nothing)"
echo "-- count of kalshi_copy Apify-failure lines since flip --"
journalctl -u trading-corp.service --since "2026-09-13 15:37:00" --no-pager 2>/dev/null | grep -icE "kalshi_copy_trader.*apify open_positions fetch failed"
echo "-- LAST kalshi_copy Apify-failure line anywhere today (timestamp) --"
journalctl -u trading-corp.service --since "2026-09-13 00:00:00" --no-pager 2>/dev/null | grep -E "apify open_positions fetch failed" | tail -1
echo "-- most recent kalshi_copy_trader journal line (any) --"
journalctl -u trading-corp.service --since "2026-09-13 15:30:00" --no-pager 2>/dev/null | grep -E "kalshi_copy_trader" | tail -2 || echo "  (none since 15:30)"
echo "### DONE ###"
