import sqlite3, os, importlib.util, datetime as dt

# ---- library check ----
print("=== LIBS ===")
for m in ['numpy','pandas','scipy','statsmodels']:
    spec = importlib.util.find_spec(m)
    if spec is None:
        print(f"{m}: MISSING")
    else:
        try:
            mod = __import__(m)
            print(f"{m}: {getattr(mod,'__version__','?')}")
        except Exception as e:
            print(f"{m}: import-error {e}")

DATA = r"C:\Users\AA Incorporado\cc\data"
COINS = ['btc','eth','sol','xrp']
dbs = {c: os.path.join(DATA, f"{c}_scalping.db") for c in COINS}

# ---- schema + table list per db ----
print("\n=== TABLES (each db) ===")
for c, path in dbs.items():
    con = sqlite3.connect(path)
    cur = con.cursor()
    tabs = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    print(f"{c}: {tabs}")
    con.close()

print("\n=== bars_1h schema (btc) ===")
con = sqlite3.connect(dbs['btc'])
for r in con.execute("PRAGMA table_info(bars_1h)"):
    print(r)
con.close()

def ts_to_iso(v):
    if v is None: return None
    try:
        x = float(v)
        # detect ms vs s
        if x > 1e12: x = x/1000.0
        return dt.datetime.utcfromtimestamp(x).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(v)

# ---- per coin: count, min/max ts, sample ts type ----
print("\n=== bars_1h coverage ===")
stats = {}
for c, path in dbs.items():
    con = sqlite3.connect(path)
    cur = con.cursor()
    try:
        n, mn, mx = cur.execute("SELECT COUNT(*), MIN(ts), MAX(ts) FROM bars_1h").fetchone()
    except Exception as e:
        print(f"{c}: ERROR {e}")
        con.close(); continue
    sample = cur.execute("SELECT ts FROM bars_1h ORDER BY ts LIMIT 3").fetchall()
    stats[c] = dict(n=n, mn=mn, mx=mx)
    print(f"{c}: rows={n} min_ts={mn} ({ts_to_iso(mn)}) max_ts={mx} ({ts_to_iso(mx)}) sample={[s[0] for s in sample]}")
    con.close()

# ---- timestamp grid spacing check: modal delta ----
print("\n=== ts spacing (modal delta seconds) ===")
for c, path in dbs.items():
    con = sqlite3.connect(path)
    rows = [r[0] for r in con.execute("SELECT ts FROM bars_1h ORDER BY ts")]
    con.close()
    if len(rows) < 3:
        print(f"{c}: too few"); continue
    # normalize to seconds
    def norm(v):
        x=float(v); return x/1000.0 if x>1e12 else x
    secs=[norm(r) for r in rows]
    deltas={}
    for i in range(1,len(secs)):
        d=round(secs[i]-secs[i-1])
        deltas[d]=deltas.get(d,0)+1
    top=sorted(deltas.items(), key=lambda kv:-kv[1])[:4]
    print(f"{c}: top-deltas(sec,count)={top}")
