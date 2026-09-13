import gzip, sqlite3, sys, os, time, hashlib
GZ, SCRATCH = sys.argv[1], sys.argv[2]
con = sqlite3.connect(SCRATCH)
con.execute("PRAGMA journal_mode=OFF"); con.execute("PRAGMA synchronous=OFF"); con.execute("PRAGMA cache_size=-262144")
buf=[]; sz=0; nlines=0; idx=[]; t0=time.time()
def flush():
    global buf,sz
    if buf: con.executescript("".join(buf)); buf=[]; sz=0
with gzip.open(GZ,'rt',encoding='utf-8') as f:
    for line in f:
        nlines+=1
        s=line.lstrip(); st=s.rstrip()
        if st in ("BEGIN;","COMMIT;") or s.startswith("PRAGMA") or s.startswith("--"):
            continue                              # strip control lines; executescript manages txns per chunk
        if s.startswith("CREATE INDEX") or s.startswith("CREATE UNIQUE INDEX"):
            idx.append(line); continue          # defer index build to the end (bulk = fast)
        buf.append(line); sz+=len(line)
        if sz>67108864 and line.rstrip().endswith(';'): flush()
flush()
con.commit()
print("data restored: gz_lines=%d in %.1fs" % (nlines, time.time()-t0))
t1=time.time()
for st in idx: con.executescript(st)
con.commit()
print("indexes built: %d in %.1fs" % (len(idx), time.time()-t1))
print("== restored row counts + max rowid ==")
res={}
for t in ['kcv2_heartbeat','kcv2_index_ticks','kcv2_signals','kcv2_quotes']:
    n=con.execute('SELECT COUNT(*) FROM %s'%t).fetchone()[0]
    mx=con.execute('SELECT MAX(rowid) FROM %s'%t).fetchone()[0]
    res[t]=(n,mx); print("  %-20s rows=%-10d max_rowid=%s" % (t,n,mx))
print("== restored schema objects (kcv2*) ==")
for typ,name in con.execute("SELECT type,name FROM sqlite_master WHERE name LIKE 'kcv2%' ORDER BY type,name"):
    print("  %-7s %s" % (typ,name))
# spot rows from kcv2_quotes extremes+middle (compare to source separately)
print("== kcv2_quotes spot rows (rowid 1 / 6000000 / max) ==")
for rid in (1, 6000000, res['kcv2_quotes'][1]):
    row=con.execute("SELECT rowid,* FROM kcv2_quotes WHERE rowid=?", (rid,)).fetchone()
    print("  rowid=%s -> %s" % (rid, row))
con.close()
print("scratch_db=%s size_MB=%.1f" % (SCRATCH, os.path.getsize(SCRATCH)/1e6))
