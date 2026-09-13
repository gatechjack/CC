set -u
echo "### PHASE-2 STOP LEGACY TIMERS (WRITE via NOPASSWD sudo systemctl stop) $(date -u +%FT%TZ) ###"
echo "## NOTE: 'disable' is NOT in the NOPASSWD allowlist -> stop only (reversible); Jack disables for reboot-persistence ##"
echo
for t in trading-corp-watchlist-stats.timer trading-corp-watchlist-deep.timer trading-corp-pm-watchlist-deep.timer trading-corp-pct-pruner.timer; do
  echo "-- $t --"
  echo -n "  pre  is-active : "; systemctl is-active "$t" 2>&1
  echo -n "  STOP           : "; sudo -n systemctl stop "$t" 2>&1; echo "    stop_rc=$?"
  echo -n "  post is-active : "; systemctl is-active "$t" 2>&1
  echo -n "  is-enabled     : "; systemctl is-enabled "$t" 2>&1
done
echo
echo "## list-timers after (should show these gone from the active list) ##"
systemctl list-timers --all --no-legend 2>/dev/null | grep -iE "trading|watchlist|pct|pm[-_]" || echo "(no matching timers in active list)"
echo "### DONE ###"
