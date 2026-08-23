#!/usr/bin/env bash
# PM P2 pre-CP1 runner: box-state re-verify (READ-ONLY to prod) + P1 test baseline on ~/pm_p2_scratch.
# Writes ONLY ~/pm_p2_scratch, ~/pm_p2_stage.tgz, /tmp/pm_p2_test_*.db (all deleted at end).
# Reads prod PM DB via mode=ro; reads systemctl/crontab/ss. Never touches prod artifacts/engine/legacy DB.
echo "=== PM P2 PRE-CP1 RUNNER (start) ==="
date -u
H="${HOME:-/home/azureuser}"
ROOT="$H/trading_corp"
DB="$ROOT/data/prediction_markets.db"
LEG="$ROOT/data/trading_corp.db"
VP="$ROOT/venv/bin/python"
S="$H/pm_p2_scratch"
STAGE="$H/pm_p2_stage.tgz"
TDB="/tmp/pm_p2_test_$$.db"
echo "H=$H ROOT=$ROOT S=$S VP=$VP"

echo ""
echo "=== [0] ENGINE BEFORE (baseline) ==="
systemctl show -p MainPID -p ActiveState -p SubState trading-corp.service 2>&1
PID0=$(systemctl show -p MainPID --value trading-corp.service 2>/dev/null)
echo "PID0=$PID0"

echo ""
echo "=== [1] BOX STATE (read-only) ==="
echo "--- [1a] azureuser crontab (FULL) ---"
crontab -l 2>&1
echo "--- (root crontab requires root; NOT readable as azureuser - reported, not bypassed) ---"
echo "--- [1b] systemd timers (weekly-slot clearance context) ---"
systemctl list-timers --all --no-pager 2>&1 | head -40
echo "--- [1c] port 8081 (expect FREE) ---"
if ss -ltnH 2>/dev/null | grep -Eq ':8081($|[^0-9])'; then echo "8081 IN USE:"; ss -ltn 2>/dev/null | grep ':8081'; else echo "8081 FREE"; fi
echo "--- [1d] PM DB ownership + sidecars (expect azureuser:azureuser) ---"
ls -l --time-style=full-iso "$DB" "$DB-wal" "$DB-shm" 2>&1
echo "--- [1e] refresh log (did the daily cron write?) ---"
ls -l --time-style=full-iso "$H"/pm_refresh*.log 2>&1
tail -n 6 "$H"/pm_refresh*.log 2>&1
echo "--- [1f] PM DB contents (mode=ro; schema_version/rows/whales/freshness) ---"
"$VP" -c "import sqlite3; con=sqlite3.connect('file:$DB?mode=ro',uri=True); q=con.cursor(); print('schema_version',q.execute('SELECT MAX(version) FROM schema_version').fetchone()[0]); print('closed_rows',q.execute('SELECT COUNT(*) FROM pm_closed_position').fetchone()[0]); print('whales',q.execute('SELECT COUNT(*) FROM pm_whale').fetchone()[0]); print('max_last_refresh_ts',q.execute('SELECT MAX(last_refresh_ts) FROM pm_whale').fetchone()[0]); print('max_resolved_ts',q.execute('SELECT MAX(resolved_ts) FROM pm_closed_position').fetchone()[0]); [print('  whale',r[0][:14],'complete='+str(r[1]),'pulled='+str(r[2]),'stored='+str(r[3])) for r in q.execute('SELECT wallet,backfill_complete,last_pulled,last_stored FROM pm_whale ORDER BY wallet').fetchall()]; con.close()" 2>&1
echo "--- [1g] engine divisions alive (journal grep; may be permission-limited) ---"
journalctl -u trading-corp.service -n 40 --no-pager 2>&1 | grep -iE 'poly_kalshi|pct|paper|mace' | tail -8
echo "--- [1h] legacy DB CONTEXT (EXPECTED TO DIFFER; engine writes it live, not me) ---"
stat -c '%n size=%s mtime=%Y owner=%U:%G mode=%a' "$LEG" "$LEG-wal" "$LEG-shm" 2>&1
LEGM0=$(stat -c '%Y' "$LEG" 2>/dev/null)
echo "LEG_MTIME_BEFORE=$LEGM0"

echo ""
echo "=== [2] CHAIN OF CUSTODY: staged tarball ==="
ls -l "$STAGE" 2>&1
echo "TARBALL_SHA256_BOX:"
sha256sum "$STAGE" 2>&1

echo ""
echo "=== [3] SCRATCH SETUP (under \$H only; never prod) ==="
case "$S" in
  /home/*/pm_p2_scratch|/root/pm_p2_scratch) : ;;
  *) echo "REFUSE bad scratch path: $S"; exit 2 ;;
esac
rm -rf "$S"
mkdir -p "$S"
tar -xzf "$STAGE" -C "$S" && echo "extracted OK"
echo "--- [3b] trading_corp/__init__.py PRESENT + INERT (deliberate no-coupling proof) ---"
ls -l "$S/trading_corp/__init__.py" 2>&1
echo "import lines in __init__ (expect NONE):"
grep -nE '^[[:space:]]*(import|from)[[:space:]]' "$S/trading_corp/__init__.py" 2>&1 || echo "  (none - inert)"
echo "--- [3c] BOX per-file sha256 (compare to LOCAL reference) ---"
( cd "$S" && sha256sum pyproject.toml trading_corp/__init__.py trading_corp/prediction_markets/db.py trading_corp/prediction_markets/stats.py trading_corp/prediction_markets/ingest.py trading_corp/prediction_markets/category.py trading_corp/prediction_markets/rosters.py trading_corp/scripts/pm_cli.py tests/prediction_markets/test_db.py tests/prediction_markets/test_integrity.py tests/prediction_markets/test_caveat_analytics.py tests/conftest.py 2>&1 )
echo "--- [3d] conftest files shipped + content ---"
find "$S/tests" -name conftest.py 2>&1
echo "----- BEGIN tests/conftest.py -----"
cat "$S/tests/conftest.py" 2>&1
echo "----- END tests/conftest.py -----"

echo ""
echo "=== [4] PYTEST (box venv, CWD=scratch, PM_DB_PATH=tmp FILE) ==="
export PM_DB_PATH="$TDB"
echo "PM_DB_PATH=$PM_DB_PATH"
if [ ! -x "$VP" ]; then echo "WARN venv python not executable at $VP; listing venv/bin:"; ls -la "$ROOT/venv/bin" 2>&1 | head; fi
( cd "$S" && PYTHONPATH="$S" "$VP" -m pytest tests/prediction_markets/ -p no:pytest_ethereum -q --junitxml="$S/junit.xml" 2>&1 )
RC=$?
echo "PYTEST_RC=$RC"
echo "--- [4b] legacy-guard assertion FIRES (targeted -v: test_refuses_legacy_db_path) ---"
( cd "$S" && PYTHONPATH="$S" "$VP" -m pytest tests/prediction_markets/test_db.py -p no:pytest_ethereum -k legacy -v 2>&1 | tail -20 )
echo "--- [4c] junit testsuite line ---"
grep -oE '<testsuite [^>]*>' "$S/junit.xml" 2>&1 | head -1

echo ""
echo "=== [4d] e5 REAL-DATA characterization (READ-ONLY mode=ro on live PM DB; NO migration, NO rollup-write) ==="
echo "-- raw live data must yield the KNOWN two-sided figures; the fixture tests above prove rollup WRITES them (not silent-zero) --"
"$VP" -c "import sqlite3
c=sqlite3.connect('file:$DB?mode=ro',uri=True); q=c.cursor()
print('== two_sided_pct per WALLET (expect Kickstand7 0xd1acd3925d89 ~0.37, BetMechanic 0xa6a856a8c8a7 ~0.71) ==')
for w,nc,nt in q.execute('SELECT wallet,COUNT(*),SUM(CASE WHEN n_out>1 THEN 1 ELSE 0 END) FROM (SELECT wallet,condition_id,COUNT(DISTINCT outcome_index) n_out FROM pm_closed_position GROUP BY wallet,condition_id) GROUP BY wallet ORDER BY wallet').fetchall(): print('  %-16s n_cond=%-6s n_two=%-6s two_sided_pct=%.4f'%(w[:16],nc,nt,(nt/nc if nc else 0)))
print('== one_sided SCOREABLE n per (wallet,category), n>=50, desc (expect BetMechanic nba ~1132) ==')
for w,cat,n in q.execute('SELECT p.wallet,p.category,COUNT(*) FROM pm_closed_position p JOIN (SELECT wallet,category,condition_id FROM pm_closed_position GROUP BY wallet,category,condition_id HAVING COUNT(DISTINCT outcome_index)=1) oc ON p.wallet=oc.wallet AND p.category=oc.category AND p.condition_id=oc.condition_id WHERE p.pnl_suspect=0 GROUP BY p.wallet,p.category HAVING COUNT(*)>=50 ORDER BY COUNT(*) DESC').fetchall(): print('  %-16s %-10s one_sided_n=%s'%(w[:16],cat,n))
c.close()" 2>&1

echo ""
echo "=== [5] ISOLATION PROOFS ==="
echo "--- any *.db/-wal/-shm under scratch (expect NONE) ---"
find "$S" \( -name '*.db' -o -name '*.db-wal' -o -name '*.db-shm' \) 2>&1
echo "(end find)"
echo "--- resolved test-DB location (in /tmp, not scratch/prod) ---"
ls -l "$TDB"* 2>&1 || echo "  (no persisted test db at $TDB - tests used tmp_path DBs)"
echo "--- legacy DB mtime AFTER (differs=EXPECTED; engine live, I never opened it) ---"
LEGM1=$(stat -c '%Y' "$LEG" 2>/dev/null)
echo "LEG_MTIME_AFTER=$LEGM1 LEG_MTIME_BEFORE=$LEGM0"

echo ""
echo "=== [6] CLEANUP + PROOF ==="
cd "$H"
rm -rf "$S"
rm -f "$STAGE"
rm -f "$TDB"*
if [ -e "$S" ]; then echo "SCRATCH_STILL_THERE=BAD"; else echo "SCRATCH_CONFIRMED_GONE"; fi
if [ -e "$STAGE" ]; then echo "STAGE_STILL_THERE=BAD"; else echo "STAGE_CONFIRMED_GONE"; fi

echo ""
echo "=== [7] ENGINE AFTER (unchanged proof) ==="
systemctl show -p MainPID -p ActiveState trading-corp.service 2>&1
PID1=$(systemctl show -p MainPID --value trading-corp.service 2>/dev/null)
echo "PID1=$PID1 PID0=$PID0"
if [ "$PID0" = "$PID1" ] && [ -n "$PID0" ]; then echo "ENGINE_PID_UNCHANGED=GOOD"; else echo "ENGINE_PID_CHANGED_OR_UNKNOWN=INVESTIGATE"; fi
echo "=== PM P2 PRE-CP1 RUNNER (done) ==="
