import sqlite3, os

DATA = r"C:\Users\AA Incorporado\cc\data"
COINS = ['btc','eth','sol','xrp']
dbs = {c: os.path.join(DATA, f"{c}_scalping.db") for c in COINS}

# load ts sets + ohlc integrity per coin
ts_sets = {}
print("=== OHLC integrity (bars_1h) ===")
for c, path in dbs.items():
    con = sqlite3.connect(path); cur = con.cursor()
    ts_sets[c] = set(r[0] for r in cur.execute("SELECT ts FROM bars_1h"))
    nulls = cur.execute("""SELECT COUNT(*) FROM bars_1h WHERE open IS NULL OR high IS NULL
        OR low IS NULL OR close IS NULL""").fetchone()[0]
    zeros = cur.execute("""SELECT COUNT(*) FROM bars_1h WHERE open<=0 OR high<=0
        OR low<=0 OR close<=0""").fetchone()[0]
    inv = cur.execute("""SELECT COUNT(*) FROM bars_1h WHERE high<low OR high<open OR high<close
        OR low>open OR low>close""").fetchone()[0]
    dup = cur.execute("SELECT COUNT(*)-COUNT(DISTINCT ts) FROM bars_1h").fetchone()[0]
    print(f"{c}: nulls={nulls} nonpos={zeros} inverted={inv} dup_ts={dup}")
    con.close()

# common overlap window = [max(min), min(max)]
mins = {c: min(s) for c,s in ts_sets.items()}
maxs = {c: max(s) for c,s in ts_sets.items()}
lo = max(mins.values()); hi = min(maxs.values())
import datetime as dt
iso = lambda v: dt.datetime.fromtimestamp(v, dt.UTC).strftime("%Y-%m-%d %H:%M:%S")
print("\n=== COMMON OVERLAP WINDOW ===")
print(f"latest_start = {lo} ({iso(lo)})  [{[c for c in COINS if mins[c]==lo]}]")
print(f"earliest_end = {hi} ({iso(hi)})  [{[c for c in COINS if maxs[c]==hi]}]")
expected = (hi-lo)//3600 + 1
print(f"expected hourly bars in window = {expected}")

# within window: per-coin count and gaps vs expected grid
print("\n=== per-coin coverage WITHIN overlap window ===")
grid = set(range(lo, hi+1, 3600))
in_win = {}
for c in COINS:
    w = set(t for t in ts_sets[c] if lo<=t<=hi)
    in_win[c] = w
    missing = grid - w
    print(f"{c}: bars_in_window={len(w)} missing_vs_grid={len(missing)}")

# cross-coin inner join on ts within window
common = set.intersection(*[in_win[c] for c in COINS])
print(f"\n=== INNER-JOIN across 4 coins (within window) ===")
print(f"common_ts_count = {len(common)}  (grid size = {len(grid)})")
for c in COINS:
    only = in_win[c] - common
    print(f"{c}: rows_lost_to_join = {len(only)}")
