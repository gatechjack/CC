#!/usr/bin/env bash
# CP1 STAGE 2 LIVE APPLY (Option A): back up the live PM DB, deploy the 3 new PM files + pm_cli, apply migration
# 004 + rollup on the LIVE DB, verify. ALL as azureuser (SSH session = azureuser; NO root, NO az-run-command),
# so every artifact is azureuser-owned from birth (GOTCHA-1) -- proven by whoami + ownership checks after writes.
# Engine NOT restarted / NOT imported by the PM package. Legacy DB untouched. Additive. Abort-gated: a bad backup
# or a deploy hash mismatch STOPS before the DB is mutated (and restores the prior code on a deploy mismatch).
echo "=== PM P2 CP1 STAGE-2 LIVE APPLY (start) ==="
date -u
echo "whoami=$(whoami)   (MUST be azureuser)"
H="${HOME:-/home/azureuser}"
ROOT="$H/trading_corp"
LIVE="$ROOT/data/prediction_markets.db"
PKG="$ROOT/trading_corp/prediction_markets"
SCR="$ROOT/trading_corp/scripts"
VP="$ROOT/venv/bin/python"
S="$H/pm_p2_scratch"
STAGE="$H/pm_p2_stage.tgz"
TS=$(date +%Y%m%d_%H%M%S)
BAKDIR="$H/pm_p2_dbbak_$TS"
DBBAK="$BAKDIR/prediction_markets.db.pre004"
CODEBAK="$H/pm_p2_codebak_$TS"
EXP_DB=ef20a508d8f327ec7dc98822ec5c82a8e91bb964af6cdebfe93f7bcd580ed194
EXP_STATS=6d6e66049d8f3bbb56783ab9455d0812c3f35173ee75c47ed4d40d8a21cd7c20
EXP_CAT=b2b85b8eb12f154855c42e8edc52ca029b9d70506431bcfa822e282af9acca4a
EXP_CLI=d1e49e99bd0472a28d635a83e187580bfc5190c3b474cc43d87bc1383cede812
echo "H=$H ROOT=$ROOT TS=$TS"

echo ""
echo "=== [0] ENGINE BEFORE + LIVE schema (baseline) ==="
systemctl show -p MainPID -p ActiveState trading-corp.service 2>&1
PID0=$(systemctl show -p MainPID --value trading-corp.service 2>/dev/null); echo "PID0=$PID0"
"$VP" -c "import sqlite3
c=sqlite3.connect('file:$LIVE?mode=ro',uri=True); q=c.cursor()
print('live_schema_version', q.execute('SELECT MAX(version) FROM schema_version').fetchone()[0], '(expect 3 pre-apply)')
print('live_closed_rows', q.execute('SELECT COUNT(*) FROM pm_closed_position').fetchone()[0], '(expect 28319)')
c.close()" 2>&1

echo ""
echo "=== [1] BACK UP THE LIVE DB (azureuser, timestamped) -- abort if not byte-identical ==="
mkdir -p "$BAKDIR"
cp "$LIVE" "$DBBAK"
[ -f "$LIVE-wal" ] && cp "$LIVE-wal" "$DBBAK-wal" 2>&1
[ -f "$LIVE-shm" ] && cp "$LIVE-shm" "$DBBAK-shm" 2>&1
true
ls -l "$DBBAK" 2>&1
echo "DB_BACKUP_PATH=$DBBAK"
LIVE_SHA=$(sha256sum "$LIVE" | cut -d' ' -f1)
BAK_SHA=$(sha256sum "$DBBAK" | cut -d' ' -f1)
echo "live_sha=$LIVE_SHA"
echo "bak_sha =$BAK_SHA"
if [ "$LIVE_SHA" != "$BAK_SHA" ]; then echo "BACKUP NOT BYTE-IDENTICAL -- ABORT (nothing mutated)"; exit 4; fi
echo "BACKUP_OK (byte-identical)"

echo ""
echo "=== [2] SHIP NEW CODE TO SCRATCH + BACK UP EXISTING PROD CODE ==="
case "$S" in
  /home/*/pm_p2_scratch|/root/pm_p2_scratch) : ;;
  *) echo "REFUSE bad scratch: $S"; exit 2 ;;
esac
rm -rf "$S"; mkdir -p "$S"
echo "stage sha256:"; sha256sum "$STAGE" 2>&1
tar -xzf "$STAGE" -C "$S" && echo "extracted"
mkdir -p "$CODEBAK/prediction_markets" "$CODEBAK/scripts"
cp "$PKG/db.py" "$CODEBAK/prediction_markets/db.py"
cp "$PKG/stats.py" "$CODEBAK/prediction_markets/stats.py"
cp "$PKG/category.py" "$CODEBAK/prediction_markets/category.py"
cp "$SCR/pm_cli.py" "$CODEBAK/scripts/pm_cli.py"
echo "CODE_BACKUP_DIR=$CODEBAK"
echo "existing prod code sha256 (pre-deploy, rollback ref):"
sha256sum "$PKG/db.py" "$PKG/stats.py" "$PKG/category.py" "$SCR/pm_cli.py" 2>&1

echo ""
echo "=== [3] DEPLOY the 3 PM files + pm_cli to prod (as azureuser) -- then chain-of-custody GATE ==="
cp "$S/trading_corp/prediction_markets/db.py" "$PKG/db.py"
cp "$S/trading_corp/prediction_markets/stats.py" "$PKG/stats.py"
cp "$S/trading_corp/prediction_markets/category.py" "$PKG/category.py"
cp "$S/trading_corp/scripts/pm_cli.py" "$SCR/pm_cli.py"
echo "deployed. prod hashes (must match approved refs):"
GOT_DB=$(sha256sum "$PKG/db.py" | cut -d' ' -f1)
GOT_STATS=$(sha256sum "$PKG/stats.py" | cut -d' ' -f1)
GOT_CAT=$(sha256sum "$PKG/category.py" | cut -d' ' -f1)
GOT_CLI=$(sha256sum "$SCR/pm_cli.py" | cut -d' ' -f1)
echo "  db.py       got=$GOT_DB    exp=$EXP_DB"
echo "  stats.py    got=$GOT_STATS exp=$EXP_STATS"
echo "  category.py got=$GOT_CAT   exp=$EXP_CAT"
echo "  pm_cli.py   got=$GOT_CLI   exp=$EXP_CLI"
DEPLOY_OK=1
[ "$GOT_DB" = "$EXP_DB" ] || DEPLOY_OK=0
[ "$GOT_STATS" = "$EXP_STATS" ] || DEPLOY_OK=0
[ "$GOT_CAT" = "$EXP_CAT" ] || DEPLOY_OK=0
[ "$GOT_CLI" = "$EXP_CLI" ] || DEPLOY_OK=0
if [ "$DEPLOY_OK" != "1" ]; then
  echo "DEPLOY HASH MISMATCH -- restoring prior code + ABORT (DB NOT mutated)"
  cp "$CODEBAK/prediction_markets/db.py" "$PKG/db.py"
  cp "$CODEBAK/prediction_markets/stats.py" "$PKG/stats.py"
  cp "$CODEBAK/prediction_markets/category.py" "$PKG/category.py"
  cp "$CODEBAK/scripts/pm_cli.py" "$SCR/pm_cli.py"
  echo "prior code restored."
  exit 3
fi
echo "DEPLOY_OK (all hashes match approved refs)"
echo "ownership (must be azureuser:azureuser):"
ls -l "$PKG/db.py" "$PKG/stats.py" "$PKG/category.py" "$SCR/pm_cli.py" 2>&1

echo ""
echo "=== [4]+[5] APPLY 004 + rollup on the LIVE DB via the DEPLOYED code (as azureuser) ==="
( cd "$ROOT" && PYTHONPATH=. "$VP" trading_corp/scripts/pm_cli.py --db "$LIVE" rollup 2>&1 )
echo "LIVE_ROLLUP_RC=$?"
echo "ownership after write (must be azureuser:azureuser):"
ls -l "$LIVE" "$LIVE-wal" "$LIVE-shm" 2>&1

echo ""
echo "=== [6] VERIFY the LIVE DB (schema / rows intact / caveat cols populated == Stage 1) ==="
"$VP" -c "import sqlite3
c=sqlite3.connect('file:$LIVE?mode=ro',uri=True); q=c.cursor()
print('schema_version', q.execute('SELECT MAX(version) FROM schema_version').fetchone()[0], '(expect 4)')
print('closed_rows', q.execute('SELECT COUNT(*) FROM pm_closed_position').fetchone()[0], '(expect 28319 intact)')
print('schema_version_row_count', q.execute('SELECT COUNT(*) FROM schema_version').fetchone()[0], '(expect 4)')
print('nonzero_two_sided_pct_rows', q.execute('SELECT COUNT(*) FROM pm_category_stats WHERE two_sided_pct>0').fetchone()[0], '(>0)')
print('onesided_rows', q.execute('SELECT COUNT(*) FROM pm_category_onesided_stats').fetchone()[0])
for nc,nt in q.execute('SELECT COUNT(*),SUM(CASE WHEN n_out>1 THEN 1 ELSE 0 END) FROM (SELECT condition_id,COUNT(DISTINCT outcome_index) n_out FROM pm_closed_position WHERE wallet LIKE ? GROUP BY condition_id)',('0xd1acd3925d89%',)).fetchall(): print('Kickstand7 agg two_sided %.4f (%s/%s) expect 0.3721'%((nt/nc if nc else 0),nt,nc))
for cat,nr,roi in q.execute('SELECT category,n_resolved,roi FROM pm_category_onesided_stats WHERE wallet LIKE ? AND category=?',('0xa6a856a8c8a7%','nba')).fetchall(): print('BetMechanic nba one-sided n_resolved=%s roi=%s expect 1132 / ~0.33'%(nr,roi))
print('fed single_game_pct NULL', q.execute('SELECT COUNT(*) FROM pm_category_stats WHERE category=? AND single_game_pct IS NULL').fetchone()[0], 'of', q.execute('SELECT COUNT(*) FROM pm_category_stats WHERE category=?',('fed',)).fetchone()[0])
c.close()" 2>&1

echo ""
echo "=== [7] CLEANUP scratch (KEEP backups) + ENGINE AFTER ==="
cd "$H"
rm -rf "$S"
rm -f "$STAGE"
if [ -e "$S" ]; then echo "SCRATCH_STILL_THERE=BAD"; else echo "SCRATCH_CONFIRMED_GONE"; fi
echo "RETAINED backups: DB=$DBBAK  CODE=$CODEBAK"
PID1=$(systemctl show -p MainPID --value trading-corp.service 2>/dev/null)
echo "PID1=$PID1 PID0=$PID0"
if [ "$PID0" = "$PID1" ] && [ -n "$PID0" ]; then echo "ENGINE_PID_UNCHANGED=GOOD"; else echo "ENGINE_PID_CHANGED=INVESTIGATE"; fi
echo "=== STAGE-2 LIVE APPLY (done) ==="
