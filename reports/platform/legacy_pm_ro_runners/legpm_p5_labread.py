import sqlite3, sys, gzip, os, urllib.request
p = r"C:\Users\AA Incorporado\cc-2026-08-02-wt\research\kalshi_crypto_v2\lab\kcv2_lab.db"
size = os.path.getsize(p)
print("lab_db path=%s" % p)
print("lab_db size_bytes=%d size_MB=%.1f" % (size, size/1e6))
uri = "file:" + urllib.request.pathname2url(p) + "?mode=ro&immutable=1"
c = sqlite3.connect(uri, uri=True)
print("== lab tables + row counts ==")
for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
    try: n = c.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0]
    except Exception as e: n = "ERR:%s" % e
    print("  %-26s %s" % (t, n))
ov = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'kcv2\\_%' ESCAPE '\\'")]
print("== overlap check: kcv2_*-named tables in lab ==")
print("  ", ov if ov else "NONE -> lab tables (lab_*) are DISJOINT from prod kcv2_* by namespace")
c.close()
# compression estimate: sample first 120MB, extrapolate (avoid full 407MB write pre-ruling)
n = min(120*1024*1024, size)
with open(p, 'rb') as f: chunk = f.read(n)
gz = gzip.compress(chunk, 6)
print("== compression estimate (first %.0f MB sampled) ==" % (n/1e6))
print("  sample_raw=%.1fMB sample_gz=%.1fMB ratio=%.2fx" % (n/1e6, len(gz)/1e6, n/len(gz)))
print("  EST full lab gz ~%.0f MB (level 6)" % ((len(gz)/n*size)/1e6))
