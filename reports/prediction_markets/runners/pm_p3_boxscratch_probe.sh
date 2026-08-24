#!/usr/bin/env bash
# PM P3 box-scratch runner: full test suite (incl. Phase-3 drill/names/whale) on ~/pm_p3_scratch +
# LIVE drill-reconciliation (READ-ONLY mode=ro). Reuses the banked P2 harness structure (do not re-derive).
# Writes ONLY ~/pm_p3_scratch, ~/pm_p3_stage.tgz, /tmp/pm_p3_test_*.db (all deleted at end).
# Reads prod PM DB via mode=ro; reads systemctl/ss. Never touches prod artifacts/engine/legacy DB.
echo "=== PM P3 BOX-SCRATCH RUNNER (start) ==="
date -u
H="${HOME:-/home/azureuser}"
ROOT="$H/trading_corp"
DB="$ROOT/data/prediction_markets.db"
LEG="$ROOT/data/trading_corp.db"
VP="$ROOT/venv/bin/python"
S="$H/pm_p3_scratch"
STAGE="$H/pm_p3_stage.tgz"
TDB="/tmp/pm_p3_test_$$.db"
echo "H=$H ROOT=$ROOT S=$S VP=$VP"

echo ""
echo "=== [0] ENGINE BEFORE (baseline) ==="
systemctl show -p MainPID -p ActiveState -p SubState trading-corp.service 2>&1
PID0=$(systemctl show -p MainPID --value trading-corp.service 2>/dev/null)
echo "PID0=$PID0"

echo ""
echo "=== [1] BOX STATE (read-only) ==="
echo "--- [1a] port 8081 (pm_web; NOT touched by this run) ---"
if ss -ltnH 2>/dev/null | grep -Eq ':8081($|[^0-9])'; then echo "8081 IN USE (pm_web live - expected):"; ss -ltn 2>/dev/null | grep ':8081'; else echo "8081 FREE"; fi
echo "--- [1b] PM DB ownership + sidecars (expect azureuser:azureuser) ---"
ls -l --time-style=full-iso "$DB" "$DB-wal" "$DB-shm" 2>&1
echo "--- [1c] PM DB contents (mode=ro; schema_version/rows/whales/freshness) ---"
"$VP" -c "import sqlite3; con=sqlite3.connect('file:$DB?mode=ro',uri=True); q=con.cursor(); print('schema_version',q.execute('SELECT MAX(version) FROM schema_version').fetchone()[0]); print('closed_rows',q.execute('SELECT COUNT(*) FROM pm_closed_position').fetchone()[0]); print('whales',q.execute('SELECT COUNT(*) FROM pm_whale').fetchone()[0]); print('whales_with_user_name',q.execute('SELECT COUNT(*) FROM pm_whale WHERE user_name IS NOT NULL AND user_name<>\"\"').fetchone()[0]); con.close()" 2>&1
echo "--- [1d] legacy DB CONTEXT (EXPECTED TO DIFFER; engine writes it live, not me) ---"
LEGM0=$(stat -c '%Y' "$LEG" 2>/dev/null)
echo "LEG_MTIME_BEFORE=$LEGM0"

echo ""
echo "=== [2] CHAIN OF CUSTODY: staged tarball ==="
ls -l "$STAGE" 2>&1
echo "TARBALL_SHA256_BOX (compare to [local] stage sha256 printed by the .ps1):"
sha256sum "$STAGE" 2>&1

echo ""
echo "=== [3] SCRATCH SETUP (under \$H only; never prod) ==="
case "$S" in
  /home/*/pm_p3_scratch|/root/pm_p3_scratch) : ;;
  *) echo "REFUSE bad scratch path: $S"; exit 2 ;;
esac
rm -rf "$S"
mkdir -p "$S"
tar -xzf "$STAGE" -C "$S" && echo "extracted OK"
echo "--- [3b] trading_corp/__init__.py PRESENT + INERT (no-coupling proof) ---"
grep -nE '^[[:space:]]*(import|from)[[:space:]]' "$S/trading_corp/__init__.py" 2>&1 || echo "  (none - inert)"
echo "--- [3c] BOX per-file sha256 (Phase-3 modules + tests; record) ---"
( cd "$S" && sha256sum pyproject.toml trading_corp/prediction_markets/db.py trading_corp/prediction_markets/stats.py trading_corp/prediction_markets/positions.py trading_corp/prediction_markets/names.py trading_corp/prediction_markets/category.py trading_corp/prediction_markets/web/app.py trading_corp/scripts/pm_cli.py tests/prediction_markets/test_drill_reconcile.py tests/prediction_markets/test_names.py tests/prediction_markets/test_whale_detail.py tests/conftest.py 2>&1 )
echo "--- [3d] Phase-3 web templates shipped (shared renderer + whale pages) ---"
ls -l "$S"/trading_corp/prediction_markets/web/templates/partials/pm_position_rows.html "$S"/trading_corp/prediction_markets/web/templates/pm_whale.html 2>&1

echo ""
echo "=== [4] PYTEST (box venv, CWD=scratch, PM_DB_PATH=tmp FILE) ==="
export PM_DB_PATH="$TDB"
echo "PM_DB_PATH=$PM_DB_PATH"
( cd "$S" && PYTHONPATH="$S" "$VP" -m pytest tests/prediction_markets/ -p no:pytest_ethereum -q --junitxml="$S/junit.xml" 2>&1 )
RC=$?
echo "PYTEST_RC=$RC"
echo "--- [4b] junit testsuite line (pass/fail/skip counts) ---"
grep -oE '<testsuite [^>]*>' "$S/junit.xml" 2>&1 | head -1
echo "--- [4c] Phase-3 test files collected (sanity) ---"
( cd "$S" && PYTHONPATH="$S" "$VP" -m pytest tests/prediction_markets/test_drill_reconcile.py tests/prediction_markets/test_names.py tests/prediction_markets/test_whale_detail.py -p no:pytest_ethereum -q 2>&1 | tail -4 )

echo ""
echo "=== [4d] e5 REAL-DATA anchors (READ-ONLY mode=ro; two-sided/one-sided still hold) ==="
"$VP" -c "import sqlite3
c=sqlite3.connect('file:$DB?mode=ro',uri=True); q=c.cursor()
print('== one_sided SCOREABLE n per (wallet,category), n>=50 desc (expect BetMechanic nba ~1132) ==')
for w,cat,n in q.execute('SELECT p.wallet,p.category,COUNT(*) FROM pm_closed_position p JOIN (SELECT wallet,category,condition_id FROM pm_closed_position GROUP BY wallet,category,condition_id HAVING COUNT(DISTINCT outcome_index)=1) oc ON p.wallet=oc.wallet AND p.category=oc.category AND p.condition_id=oc.condition_id WHERE p.pnl_suspect=0 GROUP BY p.wallet,p.category HAVING COUNT(*)>=50 ORDER BY COUNT(*) DESC LIMIT 8').fetchall(): print('  %-16s %-10s one_sided_n=%s'%(w[:16],cat,n))
c.close()" 2>&1

echo ""
echo "=== [4e] P3 DRILL RECONCILIATION on LIVE data (READ-ONLY mode=ro; the HARD BAR on REAL rows) ==="
echo "-- every drill's count must reconcile with its pm_category_stats aggregate; ALL_OK must be True --"
( cd "$S" && PYTHONPATH="$S" "$VP" -c "import sqlite3
from trading_corp.prediction_markets import positions as P
con=sqlite3.connect('file:$DB?mode=ro',uri=True); con.row_factory=sqlite3.Row
top=[(r['wallet'],r['category']) for r in con.execute('SELECT wallet,category FROM pm_category_stats WHERE n_resolved>=50 ORDER BY n_resolved DESC LIMIT 6').fetchall()]
def chk(w,c,d):
 rr=P.drill_rows(con,w,c,d); rec=P.reconcile(con,w,c,d,rr); print('  %-14s %-8s %-11s ok=%s expected=%s actual=%s'%(w[:14],c,d,rec['ok'],rec['expected'],rec['actual'])); return rec['ok']
res=[chk(w,c,d) for (w,c) in top for d in (['scoreable','won','two_sided','quarantined']+(['single_game'] if c not in ('fed','unknown') else []))]
print('P3_LIVE_RECONCILE_ALL_OK=%s'%all(res))
con.close()" 2>&1 )

echo ""
echo "=== [5] ISOLATION PROOFS ==="
echo "--- any *.db/-wal/-shm under scratch (expect NONE) ---"
find "$S" \( -name '*.db' -o -name '*.db-wal' -o -name '*.db-shm' \) 2>&1
echo "(end find)"

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
PID1=$(systemctl show -p MainPID --value trading-corp.service 2>/dev/null)
LEGM1=$(stat -c '%Y' "$LEG" 2>/dev/null)
echo "PID1=$PID1 PID0=$PID0  LEG_MTIME_AFTER=$LEGM1 LEG_MTIME_BEFORE=$LEGM0"
if [ "$PID0" = "$PID1" ] && [ -n "$PID0" ]; then echo "ENGINE_PID_UNCHANGED=GOOD"; else echo "ENGINE_PID_CHANGED_OR_UNKNOWN=INVESTIGATE"; fi
echo "=== PM P3 BOX-SCRATCH RUNNER (done) ==="
