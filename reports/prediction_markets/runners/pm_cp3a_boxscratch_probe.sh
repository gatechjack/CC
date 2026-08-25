#!/usr/bin/env bash
# PM CP3a box-scratch runner: full pytest (incl. paper poller/adjudicator/seed) on ~/pm_cp3a_scratch +
# MIGRATION + SEED PREVIEW on a COPY of the live PM DB (live DB UNTOUCHED; source read via mode=ro).
# Writes ONLY ~/pm_cp3a_scratch, ~/pm_cp3a_stage.tgz, /tmp/pm_cp3a_*.db (all deleted at end).
# Reads prod PM DB (mode=ro) + legacy agent_state (mode=ro). NEVER touches prod artifacts/engine/legacy DB.
# Reuses the banked P2/P3 harness structure (do not re-derive). Pure ASCII; streamed via CR+BOM-stripping ssh.
echo "=== PM CP3a BOX-SCRATCH RUNNER (start) ==="
date -u
H="${HOME:-/home/azureuser}"
ROOT="$H/trading_corp"
DB="$ROOT/data/prediction_markets.db"
LEG="$ROOT/data/trading_corp.db"
VP="$ROOT/venv/bin/python"
S="$H/pm_cp3a_scratch"
STAGE="$H/pm_cp3a_stage.tgz"
TDB="/tmp/pm_cp3a_test_$$.db"
PREV="/tmp/pm_cp3a_preview_$$.db"
echo "H=$H ROOT=$ROOT S=$S VP=$VP"

echo ""
echo "=== [0] ENGINE BEFORE (baseline; expect MainPID 969439) ==="
systemctl show -p MainPID -p ActiveState -p SubState trading-corp.service 2>&1
PID0=$(systemctl show -p MainPID --value trading-corp.service 2>/dev/null)
echo "PID0=$PID0"

echo ""
echo "=== [1] BOX STATE (read-only) ==="
echo "--- [1a] port 8081 (pm_web; NOT touched by this run) ---"
if ss -ltnH 2>/dev/null | grep -Eq ':8081($|[^0-9])'; then echo "8081 IN USE (pm_web live - expected)"; else echo "8081 FREE"; fi
echo "--- [1b] live PM DB ownership + sidecars (expect azureuser:azureuser) ---"
ls -l --time-style=full-iso "$DB" "$DB-wal" "$DB-shm" 2>&1
echo "--- [1c] live PM DB (mode=ro; schema_version EXPECT 4 = migration NOT applied to live) ---"
"$VP" -c "import sqlite3; c=sqlite3.connect('file:$DB?mode=ro',uri=True); q=c.cursor(); print('live_schema_version', q.execute('SELECT MAX(version) FROM schema_version').fetchone()[0]); print('live_closed_rows', q.execute('SELECT COUNT(*) FROM pm_closed_position').fetchone()[0]); print('live_whales', q.execute('SELECT COUNT(*) FROM pm_whale').fetchone()[0]); c.close()" 2>&1
echo "--- [1d] legacy DB context (EXPECTED TO DIFFER; engine writes it live, not me) ---"
LEGM0=$(stat -c '%Y' "$LEG" 2>/dev/null); echo "LEG_MTIME_BEFORE=$LEGM0"

echo ""
echo "=== [2] CHAIN OF CUSTODY: staged tarball ==="
ls -l "$STAGE" 2>&1
echo "TARBALL_SHA256_BOX (compare to [local] stage sha256 printed by the .ps1):"
sha256sum "$STAGE" 2>&1

echo ""
echo "=== [3] SCRATCH SETUP (under \$H only; never prod) ==="
case "$S" in
  /home/*/pm_cp3a_scratch|/root/pm_cp3a_scratch) : ;;
  *) echo "REFUSE bad scratch path: $S"; exit 2 ;;
esac
rm -rf "$S"; mkdir -p "$S"
tar -xzf "$STAGE" -C "$S" && echo "extracted OK"
echo "--- [3b] trading_corp/__init__.py PRESENT + INERT (no-coupling proof) ---"
grep -nE '^[[:space:]]*(import|from)[[:space:]]' "$S/trading_corp/__init__.py" 2>&1 || echo "  (none - inert)"
echo "--- [3c] BOX per-file sha256 (CP3a modules + tests + provenance; compare to local from the .ps1) ---"
( cd "$S" && sha256sum pyproject.toml trading_corp/prediction_markets/db.py trading_corp/prediction_markets/paper.py trading_corp/scripts/pm_cli.py config/pm_farm_pin_provenance.yaml tests/prediction_markets/test_db.py tests/prediction_markets/test_paper.py tests/conftest.py 2>&1 )
echo "--- [3d] provenance yaml + paper.py shipped (present) ---"
ls -l "$S/config/pm_farm_pin_provenance.yaml" "$S/trading_corp/prediction_markets/paper.py" 2>&1

echo ""
echo "=== [4] PYTEST (box venv, CWD=scratch, PM_DB_PATH=tmp FILE) ==="
export PM_DB_PATH="$TDB"; echo "PM_DB_PATH=$PM_DB_PATH"
( cd "$S" && PYTHONPATH="$S" "$VP" -m pytest tests/prediction_markets/ -p no:pytest_ethereum -q --junitxml="$S/junit.xml" 2>&1 )
RC=$?; echo "PYTEST_RC=$RC"
echo "--- [4b] junit testsuite line (pass/fail/skip/error counts) ---"
grep -oE '<testsuite [^>]*>' "$S/junit.xml" 2>&1 | head -1
echo "--- [4c] CP3a test files collected (paper + db) ---"
( cd "$S" && PYTHONPATH="$S" "$VP" -m pytest tests/prediction_markets/test_paper.py tests/prediction_markets/test_db.py -p no:pytest_ethereum -q 2>&1 | tail -4 )

echo ""
echo "=== [4d] MIGRATION-ON-REAL-DATA + SEED PREVIEW on a COPY (live DB UNTOUCHED) ==="
echo "--- [4d-i] backup live PM DB (mode=ro source) -> preview copy (WAL-safe online backup) ---"
"$VP" -c "import sqlite3; s=sqlite3.connect('file:$DB?mode=ro',uri=True); d=sqlite3.connect('$PREV'); s.backup(d); d.close(); s.close(); print('backup OK ->', '$PREV')" 2>&1
echo "--- [4d-ii] apply migrations 005+006 to the COPY (schema 4->6; rows intact; paper/roster tables created) ---"
( cd "$S" && PYTHONPATH="$S" "$VP" -c "from trading_corp.prediction_markets import db; import sqlite3
c=sqlite3.connect('$PREV'); print('copy_before schema', c.execute('SELECT MAX(version) FROM schema_version').fetchone()[0], 'closed_rows', c.execute('SELECT COUNT(*) FROM pm_closed_position').fetchone()[0]); c.close()
db.init_db('$PREV')
c=sqlite3.connect('$PREV'); print('copy_after  schema', c.execute('SELECT MAX(version) FROM schema_version').fetchone()[0], 'closed_rows', c.execute('SELECT COUNT(*) FROM pm_closed_position').fetchone()[0])
tabs={r[0] for r in c.execute('SELECT name FROM sqlite_master').fetchall()}
print('paper_trade_exists', 'pm_paper_trade' in tabs, 'paper_config', 'pm_paper_config' in tabs, 'roster', 'pm_roster' in tabs, 'watchlist', 'pm_watchlist' in tabs); c.close()" 2>&1 )
echo "--- [4d-iii] SEED via migrate-roster (reads REAL agent_state mode=ro; seeds EVERY (wallet,category) in pm_category_stats for the migrated whales; C2.4 REVERSED -- no floor, unknown included, nothing unresolved) ---"
( cd "$S" && PYTHONPATH="$S" "$VP" trading_corp/scripts/pm_cli.py --db "$PREV" migrate-roster --legacy-db "$LEG" 2>&1 )
MR_RC=$?; echo "MIGRATE_ROSTER_RC=$MR_RC (expect 0)"
echo "--- [4d-iv] FULL SEEDED EYEBALL TABLE (user_name, category, rows_in_category, status, wallet) -- EVERY pair, for Jack ---"
"$VP" -c "import sqlite3; c=sqlite3.connect('$PREV'); c.row_factory=sqlite3.Row
rows=c.execute(\"SELECT w.wallet, r.user_name, w.category, s.n_resolved, w.status FROM pm_watchlist w LEFT JOIN pm_roster r ON w.wallet=r.wallet AND w.category=r.category LEFT JOIN pm_category_stats s ON w.wallet=s.wallet AND w.category=s.category WHERE w.status='pinned' ORDER BY r.user_name, w.category\").fetchall()
print('PINNED_PAIRS_TOTAL', len(rows))
for r in rows: print('  %-14s %-8s rows=%-6s %-8s %s'%((r['user_name'] or '')[:14], r['category'], (r['n_resolved'] if r['n_resolved'] is not None else 0), r['status'], r['wallet']))
c.close()" 2>&1

echo ""
echo "=== [5] ISOLATION PROOFS ==="
echo "--- any *.db/-wal/-shm under scratch (expect NONE; test DB + preview are in /tmp) ---"
find "$S" \( -name '*.db' -o -name '*.db-wal' -o -name '*.db-shm' \) 2>&1; echo "(end find)"
echo "--- live PM DB schema STILL 4 (migration only touched the /tmp copy, NOT live) ---"
"$VP" -c "import sqlite3; c=sqlite3.connect('file:$DB?mode=ro',uri=True); print('live_schema_after_run', c.execute('SELECT MAX(version) FROM schema_version').fetchone()[0]); c.close()" 2>&1

echo ""
echo "=== [6] CLEANUP + PROOF ==="
cd "$H"; rm -rf "$S"; rm -f "$STAGE"; rm -f "$TDB"* "$PREV"*
if [ -e "$S" ]; then echo "SCRATCH_STILL_THERE=BAD"; else echo "SCRATCH_CONFIRMED_GONE"; fi
if [ -e "$STAGE" ]; then echo "STAGE_STILL_THERE=BAD"; else echo "STAGE_CONFIRMED_GONE"; fi
if [ -e "$PREV" ]; then echo "PREVIEW_STILL_THERE=BAD"; else echo "PREVIEW_CONFIRMED_GONE"; fi

echo ""
echo "=== [7] ENGINE AFTER (unchanged proof) ==="
PID1=$(systemctl show -p MainPID --value trading-corp.service 2>/dev/null)
LEGM1=$(stat -c '%Y' "$LEG" 2>/dev/null)
echo "PID1=$PID1 PID0=$PID0  LEG_MTIME_AFTER=$LEGM1 LEG_MTIME_BEFORE=$LEGM0"
if [ "$PID0" = "$PID1" ] && [ -n "$PID0" ]; then echo "ENGINE_PID_UNCHANGED=GOOD"; else echo "ENGINE_PID_CHANGED_OR_UNKNOWN=INVESTIGATE"; fi
echo "=== PM CP3a BOX-SCRATCH RUNNER (done) ==="
