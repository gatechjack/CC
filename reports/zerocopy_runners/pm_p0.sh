PY=/home/azureuser/trading_corp/venv/bin/python
ROOT=/home/azureuser/trading_corp
echo "=== engine systemd ==="; systemctl show trading-corp -p MainPID -p NRestarts -p ActiveEnterTimestamp 2>&1
echo "=== box CR-sha16 (tr -d CR | sha256sum) + mtime of key PM files ==="
for f in settlement execution live_driver boot_reconcile arm; do
  p="$ROOT/trading_corp/prediction_markets/$f.py"
  h=$(tr -d '\r' < "$p" | sha256sum | cut -c1-16); m=$(stat -c '%y' "$p")
  echo "  $f.py  csha=$h  mtime=$m"
done
echo "=== settlement.py line count (for re-read sanity) ==="
wc -l "$ROOT/trading_corp/prediction_markets/settlement.py"
echo "=== schema head + migration numbers (db.py) ==="
"$PY" - <<'PY'
import sqlite3
pm=sqlite3.connect('file:/home/azureuser/trading_corp/data/prediction_markets.db?mode=ro',uri=True)
print("schema_version max:", pm.execute("select max(version) from schema_version").fetchone()[0])
print("schema_version all:", sorted(r[0] for r in pm.execute("select version from schema_version")))
PY
echo "--- SCHEMA_HEAD + migration ids in db.py ---"
grep -nE "SCHEMA_HEAD|MIGRATION_0(2[0-9])|# MIGRATION 0|_migration_02" "$ROOT/trading_corp/prediction_markets/db.py" | head -20
echo "=== re-book idempotency: KXUFCFIGHT net_open per account (expect 0 -> skipped_flat) ==="
"$PY" - <<'PY'
import sqlite3
pm=sqlite3.connect('file:/home/azureuser/trading_corp/data/prediction_markets.db?mode=ro',uri=True)
T='KXUFCFIGHT-26SEP29BULVIS-VIS'
for r in pm.execute("select account_id, sum(case when is_exit=0 and outcome_status='filled' then fill_count else 0 end) ent, sum(case when is_exit=1 and outcome_status='filled' then fill_count else 0 end) ex from pm_subdivision_order where ticker=? and dry_run=0 group by account_id",(T,)):
    print("  %-14s entered=%s exited=%s net_open=%s"%(r[0],r[1],r[2],(r[1] or 0)-(r[2] or 0)))
print("  is_exit=1 close rows for ticker:", pm.execute("select count(*) from pm_subdivision_order where ticker=? and is_exit=1",(T,)).fetchone()[0])
PY
echo "=== DONE p0 ==="
