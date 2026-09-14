set -u
ROOT=/home/azureuser/trading_corp
DB=$ROOT/data/trading_corp.db
PY=$ROOT/venv/bin/python3
echo "### PHASE-7 DB STATS + VACUUM/OBSERVER EVIDENCE (READ-ONLY) $(date -u +%FT%TZ) ###"
echo "=== DB file sizes ==="
ls -la "$DB" "$DB"-wal "$DB"-shm 2>/dev/null
echo "=== disk free on data fs (need ~5.3GB for full-DB backup) ==="
df -h "$ROOT/data" 2>/dev/null
echo "=== does the ENGINE hold trading_corp.db open? (decides VACUUM feasibility) ==="
EPID=$(systemctl show trading-corp -p MainPID --value 2>/dev/null); echo "engine PID=$EPID"
if [ -n "$EPID" ] && [ -d /proc/$EPID/fd ]; then
  echo "  engine fds referencing trading_corp.db:"; ls -l /proc/$EPID/fd 2>/dev/null | grep 'trading_corp.db' | sed 's/^/    /' || echo "    (none visible / permission)"
else echo "  (cannot read /proc/$EPID/fd)"; fi
echo "  fuser on the DB (which PIDs have it open):"; fuser "$DB" 2>&1 | sed 's/^/    /' || echo "    (fuser n/a)"
echo "=== observer: service state + PID + still-writing (kcv2_quotes MAX rowid vs Phase-5 HWM 12,034,120) ==="
systemctl show trading-corp-kcv2-observer.service -p Id,LoadState,ActiveState,SubState,MainPID 2>/dev/null || echo "  (systemctl show returned nonzero)"
echo "  pgrep kcv2 observer:"; pgrep -af 'kalshi_crypto_v2_observer' 2>/dev/null || echo "    (none)"
echo "=== PRAGMAs + kcv2 stats + full table list (mode=ro) ==="
$PY - "$DB" <<'PYEOF'
import sqlite3,sys
c=sqlite3.connect("file:%s?mode=ro"%sys.argv[1],uri=True)
def q1(s):
  try: return list(c.execute(s))[0][0]
  except Exception as e: return "ERR:%s"%str(e)[:60]
ps=q1("PRAGMA page_size"); pc=q1("PRAGMA page_count"); fl=q1("PRAGMA freelist_count"); av=q1("PRAGMA auto_vacuum"); jm=q1("PRAGMA journal_mode")
print("  page_size=%s page_count=%s freelist_count=%s auto_vacuum=%s journal_mode=%s"%(ps,pc,fl,av,jm))
if isinstance(ps,int) and isinstance(pc,int):
  print("  file bytes (page_size*page_count) = %d (%.2f GB)"%(ps*pc, ps*pc/1e9))
  if isinstance(fl,int):
    print("  free bytes (page_size*freelist)   = %d (%.2f GB)"%(ps*fl, ps*fl/1e9))
    print("  est LIVE-only bytes (pc-fl)        = %.2f GB"%((ps*(pc-fl))/1e9))
print("  === kcv2_* tables: rowcount + max(rowid) ===")
kt=[r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'kcv2%' ORDER BY name")]
print("  kcv2 tables:",kt)
for t in kt:
  print("    %-22s rows=%s max_rowid=%s"%(t,q1("SELECT COUNT(*) FROM %s"%t),q1("SELECT MAX(rowid) FROM %s"%t)))
print("  === kcv2 indexes ===")
for r in c.execute("SELECT name,tbl_name FROM sqlite_master WHERE type='index' AND (name LIKE 'kcv2%' OR tbl_name LIKE 'kcv2%') ORDER BY tbl_name,name"):
  print("    idx %s on %s"%(r[0],r[1]))
allt=[r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
print("  === ALL tables (count=%d) -- enumerate before drop ==="%len(allt))
print("  ",allt)
print("  === agent_state: arm-row count + poly_kalshi persist-halt (broad scan; NOT just pm_live) ===")
print("    distinct agents:", [r[0] for r in c.execute("SELECT DISTINCT agent FROM agent_state ORDER BY agent")])
print("    pm_live arm:%% rows:", q1("SELECT COUNT(*) FROM agent_state WHERE agent='pm_live' AND key LIKE 'arm:%'"))
print("    agent_state TOTAL rows:", q1("SELECT COUNT(*) FROM agent_state"))
for r in c.execute("SELECT agent,key,value_json,updated_ts FROM agent_state WHERE key LIKE '%poly_kalshi%' OR agent LIKE '%poly_kalshi%' OR value_json LIKE '%poly_kalshi%'"):
    print("    poly_kalshi row: agent=%s key=%s updated_ts=%s value=%s"%(r[0],r[1],r[3],str(r[2])[:200]))
c.close()
PYEOF
echo "### DONE ###"
