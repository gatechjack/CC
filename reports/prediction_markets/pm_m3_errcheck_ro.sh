set -u
ROOT=/home/azureuser/trading_corp
PMDB=$ROOT/data/prediction_markets.db
V=$ROOT/venv/bin/python
ST=$(systemctl show -p ActiveEnterTimestamp --value trading-corp 2>/dev/null | sed 's/^[A-Za-z]* //')
echo "### M3 POST -- JACK RETRY + ERROR BREAKDOWN (READ-ONLY) since=$ST now=$(date -u +%H:%M:%SZ) ###"
echo "engine PID=$(systemctl show -p MainPID --value trading-corp 2>/dev/null) NRestarts=$(systemctl show -p NRestarts --value trading-corp 2>/dev/null)"
echo
echo "## [1] Jack snapshot recovery (retries every 5-min tick after 19:17:43) ##"
journalctl -u trading-corp --since "$ST" --no-pager 2>/dev/null | grep -iE 'shard-snapshot (kalshi_|for kalshi_)' | grep -ivE 'IBIT|Candle' | sed 's/^/  /' | tail -12
cd "$ROOT" && PYTHONPATH="$ROOT" "$V" - "$PMDB" <<'PY'
import sqlite3, time, sys
c=sqlite3.connect("file:%s?mode=ro"%sys.argv[1], uri=True); c.row_factory=sqlite3.Row
now=int(time.time())
for r in c.execute("SELECT account_id, COUNT(*) n, MAX(snapshot_ts) mx FROM pm_shard_balance_snapshot GROUP BY account_id ORDER BY account_id"):
    d=dict(r); age=(now-d['mx']); print("  %-14s rows=%d newest_age=%dm%02ds %s" % (d['account_id'], d['n'], age//60, age%60, "FRESH" if age<600 else "STILL STALE"))
PY
echo
echo "## [2] classify the errors since restart ##"
echo "  Traceback lines        = $(journalctl -u trading-corp --since "$ST" --no-pager 2>/dev/null | grep -c 'Traceback')"
echo "  Server disconnected    = $(journalctl -u trading-corp --since "$ST" --no-pager 2>/dev/null | grep -c 'Server disconnected')"
echo "  wiring FAILED          = $(journalctl -u trading-corp --since "$ST" --no-pager 2>/dev/null | grep -c 'wiring FAILED')"
echo "  CRITICAL               = $(journalctl -u trading-corp --since "$ST" --no-pager 2>/dev/null | grep -c 'CRITICAL')"
echo "  prediction_markets err = $(journalctl -u trading-corp --since "$ST" --no-pager 2>/dev/null | grep -iE 'prediction_markets|shard_snapshot|live_driver' | grep -icE 'error|Traceback|fail')"
echo "  -- exception final-line types (what actually threw) --"
journalctl -u trading-corp --since "$ST" --no-pager 2>/dev/null | grep -iE 'Error:|Exception:|disconnected|Timeout' | grep -ivE 'IBIT|Candle' | sed -E 's/.*[0-9]{2}:[0-9]{2}:[0-9]{2}[^ ]* //; s/[0-9]+//g' | sort | uniq -c | sort -rn | head -12
echo
echo "## [3] boot-only or ongoing? errors in the LAST 120s (steady-state should be ~0) ##"
echo "  Traceback/disconnect in last 120s = $(journalctl -u trading-corp --since '120 seconds ago' --no-pager 2>/dev/null | grep -icE 'Traceback|Server disconnected')"
echo "  latest 6 error-ish lines:"
journalctl -u trading-corp --since "$ST" --no-pager 2>/dev/null | grep -iE 'Traceback|Server disconnected|CRITICAL|wiring FAILED' | grep -ivE 'IBIT|Candle' | tail -6 | sed -E 's/(.{160}).*/\1/' | sed 's/^/  /'
echo "### DONE ###"
