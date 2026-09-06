set -u
ROOT=/home/azureuser/trading_corp
VENV=$ROOT/venv/bin/python
MAIN=$ROOT/trading_corp/main.py
APP=$ROOT/trading_corp/web/app.py
PDB=$ROOT/trading_corp/persistence/db.py
SST=$ROOT/trading_corp/prediction_markets/shard_snapshot_task.py
EPID=$(systemctl show -p MainPID --value trading-corp.service 2>/dev/null)
PW=$(systemctl show -p MainPID --value prediction-markets-web.service 2>/dev/null)
DB=$(tr '\0' '\n' < /proc/$EPID/environ 2>/dev/null | sed -n 's/^PM_DB_PATH=//p' | head -1)
[ -z "$DB" ] && DB="$ROOT/data/prediction_markets.db"
cd "$ROOT"
echo "############################################################"
echo "### PM M3-CLOBBER AUDIT (READ-ONLY) ###"
date -u +"### OBSERVED: %Y-%m-%dT%H:%M:%SZ host=$(hostname) ###"
echo "############################################################"
echo "=== [1] services ==="
echo "  engine MainPID=$EPID NRestarts=$(systemctl show -p NRestarts --value trading-corp.service) State=$(systemctl show -p ActiveState --value trading-corp.service)/$(systemctl show -p SubState --value trading-corp.service)"
echo "  pm_web MainPID=$PW NRestarts=$(systemctl show -p NRestarts --value prediction-markets-web.service)"
echo "  PM DB = $DB"
echo "=== [2] box shared-file hashes (CR-stripped sha256 first16) ==="
echo "  main.py           = $(tr -d '\r' < "$MAIN" | sha256sum | cut -c1-16)  (expect 236a6be0.. = MACE-p2 + driver graft; NOT bba046e8/3f3f3df8)"
echo "  web/app.py        = $(tr -d '\r' < "$APP" | sha256sum | cut -c1-16)  (ref liveness 67c336f2a0e5db9d | mace-p2 d0fdde0373f69805)"
echo "  persistence/db.py = $(tr -d '\r' < "$PDB" | sha256sum | cut -c1-16)  (ref liveness 69318c25dfd614db | mace-p2 177d834a69ea5a55)"
echo "=== [3] main.py PM wiring accounting ==="
echo "  -- prediction_markets import lines (expect ONLY driver import; M3 import GONE) --"
grep -nE "from trading_corp.prediction_markets import" "$MAIN" | sed 's/^/    /'
echo "  -- driver markers (expect PRESENT) --"
for m in "scheduled_pm_live_loop" "plan_driver_tasks" "PM LIVE DRIVER WIRED" "active_driver_subdivisions"; do
  echo "    [$(grep -c "$m" "$MAIN")] $m"
done
echo "  -- M3 markers (expect ABSENT = 0) --"
for m in "scheduled_shard_snapshot_loop" "M3 shard-snapshot" "shard_snapshot_task_handle" "shard-snapshot writer WIRED"; do
  echo "    [$(grep -c "$m" "$MAIN")] $m"
done
echo "  total 'prediction_markets' refs in main.py = $(grep -c "prediction_markets" "$MAIN")  (ref-complete=2; expect 1 = driver only)"
echo "=== [4] PM markers in OTHER shared files (expect 0 -> no PM block lost there) ==="
echo "  web/app.py        PM refs = $(grep -cE "prediction_markets|shard_snapshot|pm_live_driver|pm_arm" "$APP")"
echo "  persistence/db.py PM refs = $(grep -cE "prediction_markets|shard_snapshot|pm_live_driver|pm_arm" "$PDB")"
echo "=== [5] M3 callee module INTACT on box (only the main.py wiring was clobbered) ==="
if [ -f "$SST" ]; then
  echo "  shard_snapshot_task.py present; defs [$(grep -cE "def scheduled_shard_snapshot_loop|def snapshot_once|def resolve_kalshi_keys" "$SST")]/3:"
  grep -nE "def scheduled_shard_snapshot_loop|def snapshot_once|def resolve_kalshi_keys" "$SST" | sed 's/^/    /'
else
  echo "  *** shard_snapshot_task.py MISSING -- M3 callee also clobbered ***"
fi
echo "=== [6] PM DB: schema head + mig-016 table + current shard-snapshot ages (the stale symptom) ==="
"$VENV" - "$DB" <<'PY'
import sys, sqlite3, time
c=sqlite3.connect("file:%s?mode=ro"%sys.argv[1], uri=True); c.row_factory=sqlite3.Row
head=c.execute("SELECT MAX(version) v FROM schema_version").fetchone()["v"]
print("  schema head = %s (expect 20)" % head)
has=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pm_shard_balance_snapshot'").fetchone() is not None
print("  pm_shard_balance_snapshot present = %s (migration 016)" % has)
if has:
    now=int(time.time())
    for r in c.execute("SELECT account_id, COUNT(*) n, MAX(snapshot_ts) mx FROM pm_shard_balance_snapshot GROUP BY account_id ORDER BY account_id"):
        d=dict(r); age=(now-d['mx'])/3600.0 if d['mx'] else -1
        print("    %-14s rows=%d newest_age=%.1fh %s" % (d['account_id'], d['n'], age, "*** STALE ***" if age>1 else ""))
    print("  -- latest 4 snapshot rows (the display source) --")
    for r in c.execute("SELECT account_id, snapshot_ts, total_dollars, by_shard_json, has_breakdown FROM pm_shard_balance_snapshot ORDER BY snapshot_ts DESC LIMIT 4"):
        d=dict(r); print("    %-14s ts=%d total=$%.2f hb=%s by_shard=%s" % (d['account_id'],d['snapshot_ts'],d['total_dollars'],d['has_breakdown'],d['by_shard_json']))
PY
echo "=== [7] graft anchor: box main.py context after the driver block (where M3 goes) ==="
END=$(grep -n "PM live driver wiring FAILED" "$MAIN" | head -1 | cut -d: -f1)
if [ -n "$END" ]; then
  echo "  driver-block end at line $END; window [$((END-2))..$((END+16))]:"
  awk -v a=$((END-2)) -v b=$((END+16)) 'NR>=a && NR<=b {printf "    %5d: %s\n", NR, $0}' "$MAIN"
else
  echo "  *** driver-block end marker not found -- driver block itself may be missing ***"
fi
echo "### END M3 audit ###"
