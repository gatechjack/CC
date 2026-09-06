set -u
ROOT=/home/azureuser/trading_corp
PMDB=$ROOT/data/prediction_markets.db
LDB=$ROOT/data/trading_corp.db
V=$ROOT/venv/bin/python
ST=$(systemctl show -p ActiveEnterTimestamp --value trading-corp 2>/dev/null | sed 's/^[A-Za-z]* //')
echo "### PM M3 RESTORE -- POST-CHECK (READ-ONLY, after Jack's ENGINE restart) since=$ST ###"
echo "run this ~5 min after the restart (boot+catalog+reconcile ~3.5min, then the first snapshot)"
echo "engine PID=$(systemctl show -p MainPID --value trading-corp 2>/dev/null) NRestarts=$(systemctl show -p NRestarts --value trading-corp 2>/dev/null) (expect a NEW PID vs 208950)"
echo
echo "## [1] M3-SPECIFIC PROOF -- writer WIRED + producing FRESH snapshots for BOTH accounts ##"
echo "  boot log 'M3 shard-snapshot writer WIRED':"
journalctl -u trading-corp --since "$ST" --no-pager 2>/dev/null | grep -E 'M3 shard-snapshot writer WIRED|M3 shard-snapshot writer:' | sed 's/^/    /' | tail -3
echo "  recent per-cycle 'shard-snapshot <acct>: total=...' info lines (the 5-min timer firing):"
journalctl -u trading-corp --since "$ST" --no-pager 2>/dev/null | grep -E 'shard-snapshot kalshi_' | sed 's/^/    /' | tail -6
cd "$ROOT" && PYTHONPATH="$ROOT" "$V" - "$PMDB" <<'PY'
import sqlite3, time, sys, json
c=sqlite3.connect("file:%s?mode=ro"%sys.argv[1], uri=True); c.row_factory=sqlite3.Row
now=int(time.time())
print("  -- shard-snapshot freshness (was ~48h stale; expect MINUTES now) --")
for r in c.execute("SELECT account_id, COUNT(*) n, MAX(snapshot_ts) mx FROM pm_shard_balance_snapshot GROUP BY account_id ORDER BY account_id"):
    d=dict(r); age=(now-d['mx']) if d['mx'] else -1
    tag="FRESH" if 0<=age<600 else "*** STILL STALE -- boot may not have taken the first snapshot yet; re-run in 2 min ***"
    print("    %-14s rows=%d newest_age=%dm%02ds %s" % (d['account_id'], d['n'], age//60, age%60, tag))
print("  -- shard-0-direction line: newest by_shard (current again) --")
for r in c.execute("SELECT account_id, snapshot_ts, total_dollars, by_shard_json FROM pm_shard_balance_snapshot WHERE snapshot_ts=(SELECT MAX(snapshot_ts) FROM pm_shard_balance_snapshot s2 WHERE s2.account_id=pm_shard_balance_snapshot.account_id) ORDER BY account_id"):
    d=dict(r); bs=json.loads(d['by_shard_json']); age=(now-d['snapshot_ts'])
    print("    %-14s total=$%.2f shard0=$%.2f shard3=$%.2f (age %dm)" % (d['account_id'], d['total_dollars'], bs.get('0',0.0), bs.get('3',0.0), age//60))
PY
echo
echo "## [2] roster line back (4 categories/account):"
journalctl -u trading-corp --since "$ST" --no-pager 2>/dev/null | grep -E 'PM LIVE DRIVER WIRED' | grep -ivE 'IBIT|Candle' | sed 's/^/    /' | tail -3
echo
echo "## [3] ALL 9 arm rows UNCHANGED from persisted (expect 9/9 armed, 0 latched, ts as SW11):"
$V - <<PY 2>/dev/null || echo "  (arm read failed)"
import sqlite3,json
c=sqlite3.connect("file:$LDB?mode=ro", uri=True)
rows=c.execute("SELECT key,value_json FROM agent_state WHERE key LIKE 'arm:%' ORDER BY key").fetchall()
lat=[k for k,v in rows if json.loads(v).get('latched')]
print("  armed=True:", sum(1 for k,v in rows if json.loads(v).get('armed') is True), "of", len(rows), "(expect 9/9)")
print("  latched:", lat if lat else "NONE (expect NONE)")
for k,v in rows:
    d=json.loads(v); print("   ", k, "armed=%s latched=%s ts=%s" % (d.get('armed'),d.get('latched'),d.get('ts') or d.get('updated_ts')))
PY
echo
echo "## [4] boot-reconcile clean (reconciled=True latched=False both accounts):"
journalctl -u trading-corp --since "$ST" --no-pager 2>/dev/null | grep -E 'boot.?reconcil|reconciled' | grep -iE 'prediction|pm_live|kalshi_jack|kalshi_karen' | grep -ivE 'IBIT|Candle|coinbase|bitunix|donchian' | sed 's/^/    /' | tail -6
echo
echo "## [5] LIVENESS PANEL still ALL-RUNNING (the health check; read_liveness):"
cd "$ROOT" && PYTHONPATH="$ROOT" "$V" - "$PMDB" <<'PY'
import sqlite3, time, sys
from trading_corp.prediction_markets import heartbeat as hb
c=sqlite3.connect("file:%s?mode=ro"%sys.argv[1], uri=True)
live=hb.read_liveness(c, now_ts=int(time.time()))
print("    " + ", ".join("%s/%s=%s"%(r.account_id.split('_')[1], r.category, r.state) for r in live))
print("    any_alarm=%s (expect False; RUNNING once cycles resume; BOOTING briefly right after restart is OK)" % hb.any_alarm(live))
PY
echo
echo "## [6] every division back incl MACE + error count:"
journalctl -u trading-corp --since "$ST" --no-pager 2>/dev/null | grep -iE 'mace .*wired|MACE reconcile|Poly->Kalshi MLB copy WIRED|bitunix_sfp observer wired|PEAD wired|Donchian scheduler online|Tail-Price|Web command center' | grep -ivE 'IBIT|Candle' | sed 's/^/    /' | tail -10
echo "  errors since restart: $(journalctl -u trading-corp --since "$ST" --no-pager 2>/dev/null | grep -icE 'Traceback|CRITICAL|wiring FAILED')"
echo "### M3 POST-CHECK DONE ###"
