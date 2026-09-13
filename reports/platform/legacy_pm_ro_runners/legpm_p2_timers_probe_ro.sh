set -u
ROOT=/home/azureuser/trading_corp
echo "### PHASE-2 TIMER OWNERSHIP + APIFY + PRIVILEGE PROBE (READ-ONLY) $(date -u +%FT%TZ) ###"
echo
echo "## A. timer -> service -> ExecStart + is-enabled ##"
for u in trading-corp-watchlist-stats trading-corp-watchlist-deep trading-corp-pm-watchlist-deep trading-corp-pct-pruner; do
  echo "-- $u --"
  echo -n "  timer is-enabled: "; systemctl is-enabled "$u.timer" 2>&1
  echo -n "  timer is-active : "; systemctl is-active "$u.timer" 2>&1
  systemctl cat "$u.service" 2>/dev/null | grep -E "ExecStart=" | sed 's/^/  /'
  systemctl show "$u.timer" -p LastTriggerUSec 2>/dev/null | sed 's/^/  /'
done
echo
echo "## B. Apify usage per script (grep box source for apify import/use) ##"
for f in refresh_kalshi_watchlist_stats seed_kalshi_watchlist_deep seed_polymarket_watchlist_deep prune_stale_pct_entries; do
  p="$ROOT/trading_corp/scripts/$f.py"
  echo "-- $f.py --"
  if [ -f "$p" ]; then
    echo -n "    apify refs: "; grep -icE "apify|kalshi_apify_client" "$p"
    echo -n "    polymarket_data_api refs: "; grep -icE "polymarket_data_api_client" "$p"
    echo -n "    prediction_markets(live) refs: "; grep -icE "prediction_markets" "$p"
    grep -nE "^from |^import " "$p" | grep -iE "apify|polymarket_data_api|prediction_markets|kalshi_whale_stats" | sed 's/^/      /' | head -8
  else
    echo "    FILE ABSENT at $p"
  fi
done
echo
echo "## C. pm-watchlist-deep ownership: does seed_polymarket_watchlist_deep touch the LIVE PM division? ##"
p="$ROOT/trading_corp/scripts/seed_polymarket_watchlist_deep.py"
echo -n "  writes agent_state actor: "; grep -oE "polymarket_copy_trader|pm_live|prediction_markets" "$p" 2>/dev/null | sort -u | tr '\n' ' '; echo
echo "  (legacy polymarket PCT watchlist if polymarket_copy_trader; live PM only if pm_live/prediction_markets)"
echo
echo "## D. any CRON entry re-triggering these scripts? ##"
crontab -l 2>/dev/null | grep -iE "watchlist|apify|prune_stale_pct|refresh_kalshi|seed_" || echo "  (none in crontab)"
echo
echo "## E. in-engine kalshi_copy loop: still calling Apify? (strategy enabled vs division; last poll) ##"
PY="$ROOT/venv/bin/python3"; LEGACY="$ROOT/data/trading_corp.db"
"$PY" - "$LEGACY" <<'PYEOF'
import sqlite3,sys
c=sqlite3.connect("file:%s?mode=ro"%sys.argv[1],uri=True)
for k in ("last_poll_ts","apify_visibility_cache","watch_only_deep_metadata"):
    r=c.execute("SELECT updated_ts FROM agent_state WHERE agent='kalshi_copy_trader' AND key=?", (k,)).fetchone()
    print("  kalshi_copy_trader/%s updated_ts=%s" % (k, r[0] if r else None))
print("  kalshi_copy would_have_placed last:", c.execute("SELECT MAX(ts) FROM audit_event WHERE actor='kalshi_copy_trader' AND kind='would_have_placed'").fetchone()[0])
c.close()
PYEOF
echo
echo "## F. PRIVILEGE PROBE: can azureuser manage system units? (READ-ONLY probe) ##"
echo -n "  id groups: "; id -Gn 2>/dev/null
echo -n "  sudo -n true rc: "; sudo -n true 2>&1; echo "rc=$?"
echo "  sudo -n -l (allowed, non-interactive):"; sudo -n -l 2>&1 | head -15 | sed 's/^/    /'
echo "### DONE ###"
