set -u
ROOT=/home/azureuser/trading_corp
PMDB=$ROOT/data/prediction_markets.db
V=$ROOT/venv/bin/python
ST=$(systemctl show -p ActiveEnterTimestamp --value trading-corp 2>/dev/null | sed 's/^[A-Za-z]* //')
echo "### M3 WRITER DIAG (READ-ONLY) since=$ST ###"
echo "engine PID=$(systemctl show -p MainPID --value trading-corp 2>/dev/null) NRestarts=$(systemctl show -p NRestarts --value trading-corp 2>/dev/null)"
echo
echo "## [A] M3 wiring line -- did the writer wire with BOTH accounts or just one? ##"
journalctl -u trading-corp --since "$ST" --no-pager 2>/dev/null | grep -E 'M3 shard-snapshot writer WIRED|M3 shard-snapshot writer:|M3 shard-snapshot:' | sed 's/^/  /' | tail -6
echo
echo "## [B] ALL shard-snapshot log lines since restart (info = success per account; warning = fail/skip) ##"
journalctl -u trading-corp --since "$ST" --no-pager 2>/dev/null | grep -iE 'shard-snapshot' | grep -ivE 'IBIT|Candle' | sed 's/^/  /' | tail -30
echo
echo "## [C] per-account newest snapshot age + last 3 rows (Jack vs Karen) ##"
cd "$ROOT" && PYTHONPATH="$ROOT" "$V" - "$PMDB" <<'PY'
import sqlite3, time, sys, json
c=sqlite3.connect("file:%s?mode=ro"%sys.argv[1], uri=True); c.row_factory=sqlite3.Row
now=int(time.time())
for r in c.execute("SELECT account_id, COUNT(*) n, MAX(snapshot_ts) mx FROM pm_shard_balance_snapshot GROUP BY account_id ORDER BY account_id"):
    d=dict(r); age=(now-d['mx']) if d['mx'] else -1
    print("  %-14s rows=%d newest_age=%dm%02ds %s" % (d['account_id'], d['n'], age//60, age%60, "FRESH" if 0<=age<600 else "STALE"))
for a in ("kalshi_jack","kalshi_karen"):
    print("  last 3 %s:" % a)
    for r in c.execute("SELECT snapshot_ts, total_dollars, by_shard_json FROM pm_shard_balance_snapshot WHERE account_id=? ORDER BY snapshot_ts DESC LIMIT 3",(a,)):
        d=dict(r); age=(now-d['snapshot_ts']); print("    ts=%d age=%dm%02ds total=$%.2f by_shard=%s" % (d['snapshot_ts'], age//60, age%60, d['total_dollars'], d['by_shard_json']))
PY
echo
echo "## [D] any kalshi_jack-specific fail/disconnect/no-keys since restart ##"
journalctl -u trading-corp --since "$ST" --no-pager 2>/dev/null | grep -iE 'kalshi_jack' | grep -iE 'fail|error|disconnect|no keys|exception|timeout|refus' | grep -ivE 'IBIT|Candle' | sed 's/^/  /' | tail -12
echo "### DIAG DONE ###"
