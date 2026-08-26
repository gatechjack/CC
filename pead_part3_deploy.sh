echo "START pead_part3_deploy (STAGED: Gate-A + backup + install 2 files; NO restart, NO sudo)"; date -u +%FT%TZ
ROOT="$HOME/trading_corp"
RHB="$ROOT/trading_corp/brokers/robinhood.py"
PS="$ROOT/trading_corp/agents/strategies/pead_strategy.py"
STAGE="$HOME/pead_part3_stage"
DB="$ROOT/data/trading_corp.db"
BOX_RHB_BASE="230e7807720da3cb71af74c77daf396a"   # deployed robinhood.py we built hook 1 OVER (has MACE gross-BP)
BOX_PS_BASE="9b9cfdadf8a86c2d5c0db6709127c155"    # deployed pead_strategy.py
NEW_RHB="e90af223ef645153971208523ef9a16a"        # branch robinhood.py (230e7807 + hook 1)
NEW_PS="fc3d6de66c8fd2e88ed6c6f2e36b6668"         # branch pead_strategy.py (9b9cfdad + hooks 2-3)
abort(){ echo "ABORT_DEPLOY: $1"; exit 0; }

echo "=== GATE-A (shared robinhood.py + pead_strategy.py == what we built against) ==="
cur_rhb=$(md5sum "$RHB"|cut -d' ' -f1); cur_ps=$(md5sum "$PS"|cut -d' ' -f1)
echo "box robinhood.py   =$cur_rhb  (need $BOX_RHB_BASE)"
echo "box pead_strategy.py=$cur_ps  (need $BOX_PS_BASE)"
[ "$cur_rhb" = "$BOX_RHB_BASE" ] || abort "robinhood.py drifted from 230e7807 (a MACE/other deploy landed) -- REBASE hook 1 over the new box version first, do NOT clobber"
[ "$cur_ps" = "$BOX_PS_BASE" ] || abort "pead_strategy.py drifted from 9b9cfdad -- rebase hooks 2-3 first"
echo "=== GATE-A (staged files == branch) ==="
s_rhb=$(md5sum "$STAGE/robinhood.py" 2>/dev/null|cut -d' ' -f1); s_ps=$(md5sum "$STAGE/pead_strategy.py" 2>/dev/null|cut -d' ' -f1)
echo "staged robinhood.py=$s_rhb (need $NEW_RHB)"; echo "staged pead_strategy.py=$s_ps (need $NEW_PS)"
[ "$s_rhb" = "$NEW_RHB" ] || abort "staged robinhood.py missing/!= branch (re-run the .ps1 to scp it)"
[ "$s_ps" = "$NEW_PS" ] || abort "staged pead_strategy.py missing/!= branch"

echo "=== PRE-RESTART DIVISION STATE (blast radius = ALL divisions restart) ==="
python3 - "$DB" <<'PY'
import sqlite3,sys
con=sqlite3.connect("file:%s?mode=ro"%sys.argv[1],uri=True); con.row_factory=sqlite3.Row; c=con.cursor()
def q(s,a=()):
  try: return c.execute(s,a).fetchall()
  except Exception as e: print("QERR",e); return []
fut=mace=pmcc=pend=0
print("-- OPEN positions per division (paper_trade_record result IS NULL) --")
for r in q("SELECT division,COUNT(*) n FROM paper_trade_record WHERE result IS NULL GROUP BY division ORDER BY division"):
  print("  OPEN %-22s %d"%(r['division'],r['n']))
  if r['division']=='bitunix_futures': fut=r['n']
print("-- pending_order rows (PEAD intents etc.) --")
for r in q("SELECT division,state,COUNT(*) n FROM pending_order GROUP BY division,state"):
  print("  PENDING %-22s %-12s %d"%(r['division'],r['state'],r['n'])); pend+=r['n']
print("-- proposed_order NON-terminal (new/queued/unconfirmed/partially_filled) --")
for r in q("SELECT strategy,status,COUNT(*) n FROM proposed_order WHERE status IN ('new','queued','unconfirmed','partially_filled') GROUP BY strategy,status"):
  print("  PROPOSED %-20s %-12s %d"%(r['strategy'],r['status'],r['n']))
  if 'mace' in (r['strategy'] or ''): mace+=r['n']
  if 'pmcc' in (r['strategy'] or ''): pmcc+=r['n']
print("GATE_FUTURES_OPEN=%d GATE_MACE_NONTERM=%d GATE_PMCC_NONTERM=%d GATE_PENDING_TOTAL=%d"%(fut,mace,pmcc,pend))
con.close()
PY
echo "REVIEW: confirm GATE_FUTURES_OPEN=0 (futures flat) and MACE/PMCC non-terminal=0 above."
echo "NOTE: futures position tracking + SFP reconciler in-flight state may live outside paper_trade_record"
echo "      -- confirm bitunix_futures/bitunix_sfp are flat/reconciled via their own state before restart."
echo "-- RH session pickle freshness (a STALE pickle risks a device-approval hang on restart) --"
ls -l --time-style=+%FT%TZ "$HOME"/.tokens/*.pickle 2>/dev/null || echo "  (no ~/.tokens/*.pickle -- confirm the service token location + freshness)"
echo "-- current engine (PID + live-divisions) --"
ps -eo pid,cmd | grep -E ' -m trading_corp ' | grep -v grep | head -1

echo "=== BACKUP + INSTALL (files only; engine NOT restarted) ==="
cp -p "$RHB" "$RHB.bak_pre_part3_20260826"
cp -p "$PS"  "$PS.bak_pre_part3_20260826"
cp "$STAGE/robinhood.py" "$RHB"
cp "$STAGE/pead_strategy.py" "$PS"
f_rhb=$(md5sum "$RHB"|cut -d' ' -f1); f_ps=$(md5sum "$PS"|cut -d' ' -f1)
echo "installed robinhood.py=$f_rhb pead_strategy.py=$f_ps"
if [ "$f_rhb" != "$NEW_RHB" ] || [ "$f_ps" != "$NEW_PS" ]; then
  echo "INSTALL VERIFY FAILED -> RESTORING BACKUPS"
  cp -p "$RHB.bak_pre_part3_20260826" "$RHB"; cp -p "$PS.bak_pre_part3_20260826" "$PS"
  abort "install md5 mismatch, restored to baseline"
fi
echo "DEPLOY_STAGED_OK: both files in place + md5-verified; engine NOT restarted (import-time change is INERT until restart)."
echo "NEXT (deliberate window): run pead_part3_restart_verify.ps1 (single restart + all-division verify)."
echo "ROLLBACK (no restart done yet): cp the two *.bak_pre_part3_20260826 back over the originals."
echo "DONE pead_part3_deploy"
