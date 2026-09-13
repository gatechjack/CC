set -u
ROOT=/home/azureuser/trading_corp
PY="$ROOT/venv/bin/python3"
LEGACY="$ROOT/data/trading_corp.db"
PMDB="$ROOT/data/prediction_markets.db"
echo "### PHASE-3 INVESTIGATION (READ-ONLY) $(date -u +%FT%TZ) ###"
echo
echo "## A. PM schema head re-verify ##"
"$PY" - "$PMDB" <<'PYEOF'
import sqlite3,sys
p=sqlite3.connect("file:%s?mode=ro"%sys.argv[1],uri=True)
print("schema_head MAX(version)=", p.execute("SELECT MAX(version) FROM schema_version").fetchone()[0])
p.close()
PYEOF
echo
echo "## B. FULL FLAG MATRIX (strategy top-level enabled/auto_execute vs division enabled) ##"
"$PY" - "$ROOT/config/strategies.yaml" "$ROOT/config/divisions.yaml" <<'PYEOF'
import yaml,sys
strat=yaml.safe_load(open(sys.argv[1],encoding="utf-8")) or {}
divf=yaml.safe_load(open(sys.argv[2],encoding="utf-8")) or {}
divs={d.get("slug"):d for d in (divf.get("divisions") or [])}
blocks=["polymarket_arbitrage","polymarket_copy_trader","kalshi_tail_price_arb","kalshi_temporal_bucket_arb","kalshi_llm_arbitrage","kalshi_weather_arb","kalshi_crypto_arb","kalshi_sports_scout","kalshi_sports_arb_observer","kalshi_copy_trader","poly_kalshi_mlb","pm_live_driver"]
# strategy block -> its division slug
divof={"polymarket_copy_trader":"polymarket_copy_trading","kalshi_tail_price_arb":"kalshi_arbitrage","kalshi_temporal_bucket_arb":"kalshi_arbitrage","kalshi_sports_arb_observer":"kalshi_arbitrage","kalshi_weather_arb":"kalshi_weather","kalshi_crypto_arb":"kalshi_crypto","kalshi_copy_trader":"kalshi_copy_trading","polymarket_arbitrage":"polymarket_arbitrage","kalshi_llm_arbitrage":"kalshi_llm_arbitrage","poly_kalshi_mlb":"poly_kalshi_mlb","kalshi_sports_scout":None,"pm_live_driver":None}
print("%-28s %-14s %-16s %-16s %s" % ("strategy_block","strat.enabled","strat.auto_exec","division","div.enabled"))
for b in blocks:
    sb=strat.get(b) or {}
    se=sb.get("enabled"); sa=sb.get("auto_execute")
    dv=divof.get(b); de=(divs.get(dv) or {}).get("enabled") if dv else "-"
    flag=""
    if se is True or sa is True: flag=" <-- STILL ARMED AT STRATEGY LAYER"
    print("%-28s %-14s %-16s %-16s %s%s" % (b, se, sa, dv, de, flag))
PYEOF
echo
echo "## C. What stops kalshi_llm + kalshi_copy TODAY (journal boot lines + recency + last live placement) ##"
echo "-- boot 'scanner online' lines (shows enabled/auto at wire time) --"
journalctl -u trading-corp.service --since "2026-09-12 20:55:00" --no-pager 2>/dev/null | grep -iE "Kalshi LLM.*online|Kalshi copy.*online|kalshi_copy.*online|LLM arb.*online" | head -6
echo "-- recent (since 09-13) skip/no-broker for these loops --"
journalctl -u trading-corp.service --since "2026-09-13 00:00:00" --no-pager 2>/dev/null | grep -iE "kalshi_llm|kalshi_copy|no broker.*kalshi|skipping cycle" | tail -8 || echo "  (no recent lines)"
"$PY" - "$LEGACY" <<'PYEOF'
import sqlite3,sys
c=sqlite3.connect("file:%s?mode=ro"%sys.argv[1],uri=True)
print("  kalshi_copy last_poll_ts:", (c.execute("SELECT updated_ts FROM agent_state WHERE agent='kalshi_copy_trader' AND key='last_poll_ts'").fetchone() or [None])[0])
print("  kalshi_copy whp last   :", c.execute("SELECT MAX(ts) FROM audit_event WHERE actor='kalshi_copy_trader' AND kind='would_have_placed'").fetchone()[0])
print("  kalshi_copy live last  :", c.execute("SELECT MAX(ts) FROM audit_event WHERE actor='kalshi_copy_trader' AND kind='kalshi_copy_placed_live'").fetchone()[0])
print("  kalshi_llm  whp last   :", c.execute("SELECT MAX(ts) FROM audit_event WHERE actor='kalshi_llm_arbitrage' AND kind='would_have_placed'").fetchone()[0])
print("  kalshi_llm  scan last  :", c.execute("SELECT MAX(ts) FROM audit_event WHERE actor='kalshi_llm_arbitrage'").fetchone()[0])
# open live positions for these (money-safety)
print("  kalshi_copy RTs unresolved:", c.execute("SELECT COUNT(*) FROM kalshi_round_trips WHERE division='kalshi_copy_trading' AND resolved_ts IS NULL").fetchone()[0])
print("  kalshi_llm  RTs unresolved:", c.execute("SELECT COUNT(*) FROM kalshi_round_trips WHERE division='kalshi_llm_arbitrage' AND resolved_ts IS NULL").fetchone()[0])
c.close()
PYEOF
echo
echo "## D. ITEM 3 -- timer stop+disable verification ##"
for u in trading-corp-watchlist-stats trading-corp-watchlist-deep trading-corp-pm-watchlist-deep trading-corp-pct-pruner; do
  echo -n "  $u.timer active="; systemctl is-active "$u.timer" 2>&1 | tr '\n' ' '
  echo -n " enabled="; systemctl is-enabled "$u.timer" 2>&1 | tr '\n' ' '
  echo -n " | service active="; systemctl is-active "$u.service" 2>&1
done
echo "  -- timers.target.wants symlinks (should NOT list these 4) --"
ls -1 /etc/systemd/system/timers.target.wants/ 2>/dev/null | grep -iE "trading|watchlist|pct" || echo "    (none of the 4 present = disabled/persistent)"
echo "  -- list-timers active view --"
systemctl list-timers --no-legend 2>/dev/null | grep -iE "trading|watchlist|pct" || echo "    (none in ACTIVE timers list)"
echo
echo "## E. APIFY cessation evidence ##"
"$PY" - "$LEGACY" <<'PYEOF'
import sqlite3,sys
c=sqlite3.connect("file:%s?mode=ro"%sys.argv[1],uri=True)
for a,k in [("kalshi_copy_trader","apify_visibility_cache"),("kalshi_copy_trader","watch_only_deep_metadata"),("kalshi_copy_trader","watch_only_stats"),("polymarket_copy_trader","watch_only_whales_metadata")]:
    r=c.execute("SELECT updated_ts FROM agent_state WHERE agent=? AND key=?", (a,k)).fetchone()
    print("  %s/%s updated_ts=%s" % (a,k, r[0] if r else None))
c.close()
PYEOF
echo -n "  now=$(date -u +%FT%TZ)  "
echo "(Apify-write timestamps should all be BEFORE Jack's ~15:1xZ timer-disable; none since = ceased)"
echo "  -- cron re-trigger check --"
crontab -l 2>/dev/null | grep -iE "watchlist|apify|refresh_kalshi|seed_|prune_stale" || echo "    (no cron re-triggers)"
echo "### DONE ###"
