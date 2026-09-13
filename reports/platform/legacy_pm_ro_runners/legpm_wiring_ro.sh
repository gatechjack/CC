set -u
ROOT=/home/azureuser/trading_corp
echo "### LEGACY-PM WIRING / HALT / TIMERS READ (READ-ONLY) $(date -u +%FT%TZ) ###"
echo
echo "## BOX config/strategies.yaml -- enabled/auto_execute for legacy PM + live driver ##"
cd "$ROOT" 2>/dev/null || echo "cd ROOT failed"
for key in polymarket_arbitrage polymarket_copy_trader kalshi_tail_price_arb kalshi_temporal_bucket_arb kalshi_llm_arbitrage kalshi_weather_arb kalshi_crypto_arb kalshi_sports_scout kalshi_sports_arb_observer kalshi_copy_trader poly_kalshi_mlb pm_live_driver; do
  echo "-- $key --"
  awk -v k="^$key:" '$0 ~ k {f=1} f && /enabled:|auto_execute:|standby:|division:|roster_actor:|roster_key:/ {print "   "$0} f && NR>1 && /^[a-zA-Z]/ && $0 !~ k {if(seen){exit}} {if($0 ~ k)seen=1}' config/strategies.yaml 2>/dev/null | head -6
done
echo
echo "## BOX divisions.yaml -- legacy PM division enabled/standby ##"
grep -nE "slug:|enabled:|standby:" config/divisions.yaml 2>/dev/null | grep -iA2 -E "polymarket|kalshi|poly_kalshi" | head -60
echo
echo "## poly_kalshi_mlb config block (verbatim, box) ##"
awk '/^poly_kalshi_mlb:/{f=1} f{print} f&&/^[a-z]/&&!/^poly_kalshi_mlb:/{c++; if(c>1)exit}' config/strategies.yaml 2>/dev/null | head -20
echo
echo "## ENGINE BOOT LOG wiring lines (current PID 370246) ##"
echo "-- from logs/ files --"
grep -rhoE "Poly->Kalshi MLB copy (WIRED|.*not wired).*|PM LIVE DRIVER WIRED.*|PM live driver:.*not wired.*|kalshi_copy.*WIRED.*" logs/ 2>/dev/null | tail -8
echo "-- from journalctl (may need privileges) --"
journalctl -u trading-corp.service --since "2026-09-12 20:55:00" 2>/dev/null | grep -E "Poly->Kalshi|PM LIVE DRIVER|PM live driver:|not wired|WIRED" | head -20 || echo "(journalctl unavailable)"
echo
echo "## SYSTEMD TIMERS (all) ##"
systemctl list-timers --all --no-legend 2>/dev/null | grep -iE "trading|watchlist|pct|pm[-_]|kcv2" || echo "(no matching timers)"
echo
echo "## Legacy PM systemd unit ExecStart + timer wiring ##"
for u in trading-corp-pct-pruner trading-corp-pm-watchlist-deep trading-corp-watchlist-deep trading-corp-watchlist-stats trading-corp-kcv2-observer; do
  echo "-- $u --"
  systemctl cat "$u.service" 2>/dev/null | grep -E "ExecStart=|WorkingDirectory=|User=" | head -4
  systemctl cat "$u.timer" 2>/dev/null | grep -E "OnCalendar=|OnUnitActiveSec=|Persistent=" | head -4
  systemctl show "$u.timer" -p LastTriggerUSec,NextElapseUSecRealtime 2>/dev/null | tr '\n' ' '; echo
  systemctl show "$u.service" -p ExecMainStatus,Result,ActiveEnterTimestamp 2>/dev/null | tr '\n' ' '; echo
done
echo
echo "## poly_kalshi_mlb + kalshi_copy audit recency + open (audit_event, READ-ONLY) ##"
PY="$ROOT/venv/bin/python3"
"$PY" - "$ROOT/data/trading_corp.db" <<'PYEOF'
import sqlite3, sys
c=sqlite3.connect("file:%s?mode=ro"%sys.argv[1], uri=True)
def show(label, q, args=()):
    try:
        rows=list(c.execute(q, args))
        print(label, rows[:12])
    except Exception as e:
        print(label, "ERR", e)
cols=[r[1] for r in c.execute("PRAGMA table_info(audit_event)")]
print("audit_event cols:", cols)
# recency of poly_kalshi + kalshi_copy + polymarket_copy events
show("distinct actors (LIKE poly/copy/arb):",
     "SELECT DISTINCT actor FROM audit_event WHERE actor LIKE '%poly_kalshi%' OR actor LIKE '%copy%' OR actor LIKE '%arb%' OR actor LIKE '%weather%' OR actor LIKE '%crypto%' OR actor LIKE '%sports%'")
show("poly_kalshi kinds+recency:",
     "SELECT actor,kind,COUNT(*),MAX(ts) FROM audit_event WHERE actor LIKE '%poly_kalshi%' GROUP BY actor,kind ORDER BY MAX(ts) DESC LIMIT 15")
show("last 8 poly_kalshi events:",
     "SELECT ts,actor,kind FROM audit_event WHERE actor LIKE '%poly_kalshi%' ORDER BY ts DESC LIMIT 8")
show("kalshi_copy last placement/live kinds:",
     "SELECT kind,COUNT(*),MAX(ts) FROM audit_event WHERE actor='kalshi_copy_trader' AND kind LIKE '%placed%' GROUP BY kind")
# open poly_kalshi positions: look for a poly_kalshi order/position table
tabs=[r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
pk=[t for t in tabs if 'poly_kalshi' in t.lower()]
print("poly_kalshi tables:", pk)
c.close()
PYEOF
echo "### DONE ###"
