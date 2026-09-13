import sqlite3, sys, gzip, os, json, time, hashlib
LEGACY, PM, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
CHUNK = 500000
def ro(p): return sqlite3.connect("file:%s?mode=ro" % p, uri=True)
def sqlval(v):
    if v is None: return "NULL"
    if isinstance(v, bool): return "1" if v else "0"
    if isinstance(v, int): return str(v)
    if isinstance(v, float): return repr(v)
    if isinstance(v, bytes): return "X'" + v.hex() + "'"
    return "'" + str(v).replace("'", "''") + "'"
def pm_health():
    for attempt in (1, 2):
        try:
            c = ro(PM); now = int(time.time())
            hb = c.execute("SELECT MAX(updated_ts) FROM pm_driver_heartbeat").fetchone()[0]
            oc = c.execute("SELECT COUNT(*) FROM pm_subdivision_order WHERE is_exit=0 AND dry_run=0 AND fill_count>0 AND settled_ts IS NULL").fetchone()[0]
            c.close()
            age = now - int(hb or 0)
            return (age < 300 and oc > 0), age, oc
        except Exception as e:
            if attempt == 2: return False, -1, "ERR:%s" % e
            time.sleep(2)
os.makedirs(os.path.dirname(OUT), exist_ok=True)
tables = ['kcv2_heartbeat', 'kcv2_index_ticks', 'kcv2_signals', 'kcv2_quotes']
manifest = {}
gz = gzip.open(OUT, 'wt', encoding='utf-8', compresslevel=6)
gz.write("-- kcv2 prod-tables point-in-time archive; source=%s; utc=%s\n" % (LEGACY, time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())))
gz.write("PRAGMA foreign_keys=OFF;\nBEGIN;\n")
for t in tables:
    c = ro(LEGACY)
    hwm = c.execute("SELECT MAX(rowid) FROM %s" % t).fetchone()[0] or 0
    cnt = c.execute("SELECT COUNT(*) FROM %s WHERE rowid<=%d" % (t, hwm)).fetchone()[0]
    ddls = [r[0] for r in c.execute("SELECT sql FROM sqlite_master WHERE tbl_name=? AND sql IS NOT NULL ORDER BY (type='table') DESC", (t,))]
    c.close()
    manifest[t] = {"hwm": hwm, "count_at_hwm": cnt}
    gz.write("DROP TABLE IF EXISTS %s;\n" % t)
    for d in ddls: gz.write(d.rstrip().rstrip(';') + ";\n")
    sys.stderr.write("[%s] hwm=%d count=%d ddl_objs=%d\n" % (t, hwm, cnt, len(ddls))); sys.stderr.flush()
    lo = 0; written = 0
    while lo < hwm:
        c = ro(LEGACY)
        rows = list(c.execute("SELECT * FROM %s WHERE rowid>%d AND rowid<=%d ORDER BY rowid" % (t, lo, lo + CHUNK)))
        c.close()
        for row in rows:
            gz.write("INSERT INTO %s VALUES(%s);\n" % (t, ",".join(sqlval(v) for v in row)))
        written += len(rows); lo += CHUNK
        ok, age, oc = pm_health()
        sys.stderr.write("  [%s] rowid<=%d written=%d | PM_ok=%s hb_age=%ss open=%s\n" % (t, lo, written, ok, age, oc)); sys.stderr.flush()
        if not ok:
            gz.close(); sys.stderr.write("!!! PM UNHEALTHY mid-dump -> ABORT (archive incomplete)\n"); sys.exit(3)
    manifest[t]["rows_written"] = written
gz.write("COMMIT;\n"); gz.close()
h = hashlib.sha256()
with open(OUT, 'rb') as f:
    for b in iter(lambda: f.read(1 << 20), b''): h.update(b)
print(json.dumps({"out": OUT, "out_bytes": os.path.getsize(OUT), "sha256": h.hexdigest(), "manifest": manifest}))
