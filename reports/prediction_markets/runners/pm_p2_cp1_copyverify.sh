#!/usr/bin/env bash
# CP1 COPY-VERIFY: apply migration 004 + rollup to a byte-COPY of the live PM DB on the real 28,319 rows.
# The LIVE DB is only READ (cp); its schema is NEVER changed here. Writes ONLY ~/pm_p2_scratch (deleted at end).
# NO code deploy to prod, NO engine touch, NO legacy DB touch. Runs as azureuser. Proves 004 is idempotent,
# rows are intact, and the caveat columns populate with REAL values -- before any live schema change (SEPARATE
# approval; the live change is coupled to a code deploy or the daily refresh cron would silent-zero the columns).
echo "=== PM P2 CP1 COPY-VERIFY (start) ==="
date -u
H="${HOME:-/home/azureuser}"
ROOT="$H/trading_corp"
LIVE="$ROOT/data/prediction_markets.db"
VP="$ROOT/venv/bin/python"
S="$H/pm_p2_scratch"
STAGE="$H/pm_p2_stage.tgz"
COPY="$S/copy.db"
echo "H=$H LIVE=$LIVE S=$S"

echo ""
echo "=== [0] ENGINE BEFORE + LIVE schema (read-only) ==="
systemctl show -p MainPID -p ActiveState trading-corp.service 2>&1
PID0=$(systemctl show -p MainPID --value trading-corp.service 2>/dev/null); echo "PID0=$PID0"
"$VP" -c "import sqlite3
c=sqlite3.connect('file:$LIVE?mode=ro',uri=True); q=c.cursor()
print('live_schema_version', q.execute('SELECT MAX(version) FROM schema_version').fetchone()[0])
print('live_closed_rows', q.execute('SELECT COUNT(*) FROM pm_closed_position').fetchone()[0])
c.close()" 2>&1

echo ""
echo "=== [1] SCRATCH + SHIP NEW CODE (chain of custody) ==="
case "$S" in
  /home/*/pm_p2_scratch|/root/pm_p2_scratch) : ;;
  *) echo "REFUSE bad scratch: $S"; exit 2 ;;
esac
rm -rf "$S"; mkdir -p "$S"
echo "stage sha256:"; sha256sum "$STAGE" 2>&1
tar -xzf "$STAGE" -C "$S" && echo "extracted"
( cd "$S" && echo "code hashes (compare to local ref):" && sha256sum trading_corp/prediction_markets/db.py trading_corp/prediction_markets/stats.py trading_corp/prediction_markets/category.py trading_corp/scripts/pm_cli.py 2>&1 )

echo ""
echo "=== [2] COPY live DB -> scratch (cp; live is only READ, never written) ==="
cp "$LIVE" "$COPY" && echo "copied live -> COPY"
[ -f "$LIVE-wal" ] && cp "$LIVE-wal" "$COPY-wal" 2>&1
[ -f "$LIVE-shm" ] && cp "$LIVE-shm" "$COPY-shm" 2>&1
true
ls -l "$COPY" 2>&1
"$VP" -c "import sqlite3
c=sqlite3.connect('file:$COPY?mode=ro',uri=True); q=c.cursor()
print('copy_schema_version_BEFORE', q.execute('SELECT MAX(version) FROM schema_version').fetchone()[0])
print('copy_rows_BEFORE', q.execute('SELECT COUNT(*) FROM pm_closed_position').fetchone()[0])
c.close()" 2>&1

echo ""
echo "=== [3] APPLY 004 + rollup on the COPY (new scratch code; init_db self-migrates 3->4) ==="
( cd "$S" && PYTHONPATH="$S" "$VP" trading_corp/scripts/pm_cli.py --db "$COPY" rollup 2>&1 )
echo "APPLY_RC=$?"

echo ""
echo "=== [4] VERIFY on the COPY (schema / rows intact / caveat cols populated-REAL) ==="
"$VP" -c "import sqlite3
c=sqlite3.connect('file:$COPY?mode=ro',uri=True); q=c.cursor()
print('schema_version', q.execute('SELECT MAX(version) FROM schema_version').fetchone()[0], '(expect 4)')
print('closed_rows', q.execute('SELECT COUNT(*) FROM pm_closed_position').fetchone()[0], '(expect 28319 intact)')
cols=[r[1] for r in q.execute('PRAGMA table_info(pm_category_stats)')]
need=['n_condition_ids','n_two_sided','two_sided_pct','n_single_game','n_futures_like','single_game_pct','market_type_source']
print('new_cols_present', all(x in cols for x in need))
print('onesided_rows', q.execute('SELECT COUNT(*) FROM pm_category_onesided_stats').fetchone()[0])
print('nonzero_two_sided_pct_rows', q.execute('SELECT COUNT(*) FROM pm_category_stats WHERE two_sided_pct>0').fetchone()[0], '(>0 proves NOT silent-zero)')
print('== Kickstand7 (0xd1acd3925d89%) two_sided_pct by category ==')
for cat,tsp,nc,nt in q.execute('SELECT category,two_sided_pct,n_condition_ids,n_two_sided FROM pm_category_stats WHERE wallet LIKE ? ORDER BY n_condition_ids DESC',('0xd1acd3925d89%',)).fetchall(): print('  ',cat,'two_sided_pct=%.4f n_cond=%s n_two=%s'%((tsp if tsp is not None else -1),nc,nt))
print('== Kickstand7 per-WALLET agg two_sided (expect 0.3721 = 489/1314, matches [4d]) ==')
for nc,nt in q.execute('SELECT COUNT(*),SUM(CASE WHEN n_out>1 THEN 1 ELSE 0 END) FROM (SELECT condition_id,COUNT(DISTINCT outcome_index) n_out FROM pm_closed_position WHERE wallet LIKE ? GROUP BY condition_id)',('0xd1acd3925d89%',)).fetchall(): print('   agg=%.4f (%s/%s)'%(((nt/nc) if nc else 0),nt,nc))
print('== BetMechanic (0xa6a856a8c8a7%) nba one-sided (expect n_resolved 1132) ==')
for cat,nr,roi in q.execute('SELECT category,n_resolved,roi FROM pm_category_onesided_stats WHERE wallet LIKE ? AND category=?',('0xa6a856a8c8a7%','nba')).fetchall(): print('  ',cat,'n_resolved=%s roi=%s'%(nr,roi))
print('== fed single_game_pct (expect NULL for all -- OQ-2) ==')
for w,sgp in q.execute('SELECT wallet,single_game_pct FROM pm_category_stats WHERE category=?',('fed',)).fetchall(): print('  ',w[:16],'single_game_pct=',sgp)
c.close()" 2>&1

echo ""
echo "=== [5] IDEMPOTENT re-run on the COPY (rollup again; schema stays 4, rows stable, 1 row/migration) ==="
( cd "$S" && PYTHONPATH="$S" "$VP" trading_corp/scripts/pm_cli.py --db "$COPY" rollup 2>&1 )
echo "RERUN_RC=$?"
"$VP" -c "import sqlite3
c=sqlite3.connect('file:$COPY?mode=ro',uri=True); q=c.cursor()
print('schema_version_after_rerun', q.execute('SELECT MAX(version) FROM schema_version').fetchone()[0], '(expect 4)')
print('rows_after_rerun', q.execute('SELECT COUNT(*) FROM pm_closed_position').fetchone()[0], '(expect 28319)')
print('schema_version_row_count', q.execute('SELECT COUNT(*) FROM schema_version').fetchone()[0], '(expect 4 -- one row per migration, no dup)')
c.close()" 2>&1

echo ""
echo "=== [6] LIVE DB UNTOUCHED (schema must still be 3) ==="
"$VP" -c "import sqlite3
c=sqlite3.connect('file:$LIVE?mode=ro',uri=True); q=c.cursor()
print('live_schema_version_AFTER', q.execute('SELECT MAX(version) FROM schema_version').fetchone()[0], '(expect 3 -- copy-verify never touched live)')
c.close()" 2>&1
ls -l --time-style=full-iso "$LIVE" 2>&1

echo ""
echo "=== [7] CLEANUP + ENGINE AFTER ==="
cd "$H"
rm -rf "$S"
rm -f "$STAGE"
if [ -e "$S" ]; then echo "SCRATCH_STILL_THERE=BAD"; else echo "SCRATCH_CONFIRMED_GONE"; fi
PID1=$(systemctl show -p MainPID --value trading-corp.service 2>/dev/null)
echo "PID1=$PID1 PID0=$PID0"
if [ "$PID0" = "$PID1" ] && [ -n "$PID0" ]; then echo "ENGINE_PID_UNCHANGED=GOOD"; else echo "ENGINE_PID_CHANGED=INVESTIGATE"; fi
echo "=== PM P2 CP1 COPY-VERIFY (done) ==="
