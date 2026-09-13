set -u
ROOT=/home/azureuser/trading_corp
PY="$ROOT/venv/bin/python3"
LEGACY="$ROOT/data/trading_corp.db"
echo "### PHASE-5 kcv2 ARCHIVE PREP (READ-ONLY, mode=ro) $(date -u +%FT%TZ) ###"
echo "## DB + WAL + SHM sizes (bytes) ##"
ls -l "$ROOT/data/trading_corp.db" "$ROOT/data/trading_corp.db-wal" "$ROOT/data/trading_corp.db-shm" 2>/dev/null | awk '{print "  "$5"  "$NF}'
echo "## sqlite disk free ##"; df -h "$ROOT/data" | tail -1 | awk '{print "  avail="$4" use="$5" mount="$6}'
echo "## PRAGMAs + kcv2 enumeration + counts + dbstat (mode=ro) ##"
"$PY" - "$LEGACY" <<'PYEOF'
import sqlite3,sys
c=sqlite3.connect("file:%s?mode=ro"%sys.argv[1],uri=True)
print("  journal_mode:", c.execute("PRAGMA journal_mode").fetchone()[0], "(WAL => mode=ro reader does NOT block the live writer)")
ps=c.execute("PRAGMA page_size").fetchone()[0]; pc=c.execute("PRAGMA page_count").fetchone()[0]
print("  page_size=%d page_count=%d db_bytes~=%d (%.2f GB) freelist=%d wal_autockpt=%s"%(ps,pc,ps*pc,ps*pc/1e9,c.execute("PRAGMA freelist_count").fetchone()[0],c.execute("PRAGMA wal_autocheckpoint").fetchone()[0]))
print("  == kcv2 objects (schema-enumerated, NOT name-guessed) ==")
objs=list(c.execute("SELECT type,name FROM sqlite_master WHERE name LIKE 'kcv2%' ORDER BY type,name"))
for typ,name in objs: print("    %-7s %s"%(typ,name))
tabs=[n for t,n in objs if t=='table']
print("  kcv2 tables=%d  kcv2 indexes=%d"%(len(tabs),len([1 for t,n in objs if t=='index'])))
print("  == row counts + rowid range ==")
for t in tabs:
    n=c.execute("SELECT COUNT(*) FROM %s"%t).fetchone()[0]
    mn,mx=c.execute("SELECT MIN(rowid),MAX(rowid) FROM %s"%t).fetchone()
    print("    %-20s rows=%-10d rowid[%s..%s]"%(t,n,mn,mx))
print("  == dbstat bytes (kcv2 objects; whole-DB page scan) ==")
tot=0
for name,b in c.execute("SELECT name,SUM(pgsize) FROM dbstat WHERE name LIKE 'kcv2%' GROUP BY name ORDER BY 2 DESC"):
    tot+=b; print("    %-30s %14d (%8.1f MB)"%(name,b,b/1e6))
print("    kcv2_TOTAL=%d bytes (%.2f GB)"%(tot,tot/1e9))
c.close()
PYEOF
echo "## OBSERVER WRITING? prove from rowid growth over 35s (obs cycle ~30s) -- short connections, no long read ##"
q0=$("$PY" -c 'import sqlite3,sys;c=sqlite3.connect("file:%s?mode=ro"%sys.argv[1],uri=True);print(c.execute("SELECT MAX(rowid) FROM kcv2_quotes").fetchone()[0])' "$LEGACY")
h0=$("$PY" -c 'import sqlite3,sys;c=sqlite3.connect("file:%s?mode=ro"%sys.argv[1],uri=True);print(c.execute("SELECT MAX(rowid) FROM kcv2_heartbeat").fetchone()[0])' "$LEGACY")
sleep 35
q1=$("$PY" -c 'import sqlite3,sys;c=sqlite3.connect("file:%s?mode=ro"%sys.argv[1],uri=True);print(c.execute("SELECT MAX(rowid) FROM kcv2_quotes").fetchone()[0])' "$LEGACY")
h1=$("$PY" -c 'import sqlite3,sys;c=sqlite3.connect("file:%s?mode=ro"%sys.argv[1],uri=True);print(c.execute("SELECT MAX(rowid) FROM kcv2_heartbeat").fetchone()[0])' "$LEGACY")
echo "  kcv2_quotes max rowid: $q0 -> $q1  (delta $((q1-q0)))"
echo "  kcv2_heartbeat max rowid: $h0 -> $h1  (delta $((h1-h0)))"
if [ "$q1" -gt "$q0" ] || [ "$h1" -gt "$h0" ]; then echo "  OBSERVER_STILL_WRITING=YES (kcv2_* is a MOVING TARGET; Phase 7 must stop the observer first)"; else echo "  OBSERVER_STILL_WRITING=NO"; fi
echo "  -- observer service journal tail --"
journalctl -u trading-corp-kcv2-observer.service --no-pager -n 4 2>/dev/null || echo "  (journal n/a)"
echo "## COMPRESSION SAMPLE: 300k kcv2_quotes rows -> SQL INSERT text -> gzip-9, extrapolate ##"
"$PY" - "$LEGACY" <<'PYEOF'
import sqlite3,sys,gzip
c=sqlite3.connect("file:%s?mode=ro"%sys.argv[1],uri=True)
total_rows=c.execute("SELECT COUNT(*) FROM kcv2_quotes").fetchone()[0]
raw=0; N=0; chunks=[]
for row in c.execute("SELECT * FROM kcv2_quotes WHERE rowid<=300000"):
    vals=",".join("NULL" if v is None else (repr(v) if isinstance(v,str) else repr(v)) for v in row)
    b=("INSERT INTO kcv2_quotes VALUES(%s);\n"%vals).encode(); chunks.append(b); raw+=len(b); N+=1
data=b"".join(chunks); gz=gzip.compress(data,9)
c.close()
bpr_raw=raw/max(1,N); bpr_gz=len(gz)/max(1,N)
print("  sample_rows=%d raw=%.1fMB gz=%.1fMB ratio=%.1fx bytes/row raw=%.1f gz=%.1f"%(N,raw/1e6,len(gz)/1e6,raw/max(1,len(gz)),bpr_raw,bpr_gz))
print("  kcv2_quotes total_rows=%d => EST full SQL-dump raw~%.2f GB, gz~%.2f GB"%(total_rows,bpr_raw*total_rows/1e9,bpr_gz*total_rows/1e9))
PYEOF
echo "### DONE ###"
