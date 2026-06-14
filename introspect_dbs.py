"""READ-ONLY backtest-DB inventory (mode=ro, SELECT/PRAGMA only — no writes, no backtest).
Reports tables, schema, row counts, time ranges, and a per-month volatility proxy
(median & p90 of (high-low)/close per bar) so we can see whether a high-vol BTC regime
is present to pair with the low-vol 2026-06-09..14 window."""
import sqlite3, statistics, datetime as dt, os

DBS = [
    r"C:\Users\AA Incorporado\cc\data\btc_scalping.db",
    r"C:\Users\AA Incorporado\cc\data\trading_corp.db",
]
TIME_COLS = ("ts_ms", "open_time", "ts", "timestamp", "time", "start", "bucket_start")

def to_date(v):
    try:
        n = float(v)
        if n > 1e12: return dt.datetime.utcfromtimestamp(n/1000).strftime("%Y-%m-%d")
        if n > 1e9:  return dt.datetime.utcfromtimestamp(n).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        s = str(v)
        return s[:10] if len(s) >= 10 else s
    return str(v)
def month_of(v):
    return to_date(v)[:7]

for path in DBS:
    print("\n" + "=" * 78)
    print(f"DB: {path}")
    if not os.path.exists(path):
        print("  !! does not exist"); continue
    print(f"  size: {os.path.getsize(path)/1e6:.1f} MB")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    tabs = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    print(f"  tables ({len(tabs)}): {', '.join(tabs)}")
    for t in tabs:
        cols = [r[1] for r in con.execute(f"PRAGMA table_info('{t}')")]
        try:
            n = con.execute(f"SELECT COUNT(*) FROM '{t}'").fetchone()[0]
        except Exception as e:
            print(f"  - {t}: count failed ({e})"); continue
        tcol = next((c for c in TIME_COLS if c in cols), None)
        has_ohlc = all(c in cols for c in ("high", "low", "close"))
        line = f"  - {t}: rows={n}  cols=[{', '.join(cols)}]"
        if tcol and n:
            mn = con.execute(f"SELECT MIN({tcol}) FROM '{t}'").fetchone()[0]
            mx = con.execute(f"SELECT MAX({tcol}) FROM '{t}'").fetchone()[0]
            line += f"  span={to_date(mn)}..{to_date(mx)}"
        print(line)
        # vol proxy per month for OHLC bar tables (cap scan for speed)
        if tcol and has_ohlc and n:
            tf = next((c for c in ("timeframe", "interval", "tf") if c in cols), None)
            tfs = [r[0] for r in con.execute(f"SELECT DISTINCT {tf} FROM '{t}'")] if tf else [None]
            for tfv in tfs:
                where = f"WHERE {tf}=?" if tf else ""
                args = (tfv,) if tf else ()
                rows = con.execute(
                    f"SELECT {tcol} tc, high, low, close FROM '{t}' {where} "
                    f"ORDER BY {tcol}", args).fetchall()
                bym = {}
                for r in rows:
                    try:
                        c = float(r["close"]);
                        if c <= 0: continue
                        rng = (float(r["high"]) - float(r["low"])) / c * 100.0
                    except (TypeError, ValueError): continue
                    bym.setdefault(month_of(r["tc"]), []).append(rng)
                label = f"{t}" + (f" [{tfv}]" if tf else "")
                print(f"      vol-proxy (bar (H-L)/C %) by month — {label}:")
                for m in sorted(bym):
                    v = bym[m]
                    med = statistics.median(v)
                    p90 = sorted(v)[int(len(v) * 0.9)] if len(v) > 9 else max(v)
                    print(f"        {m}: n={len(v):5d}  median={med:.3f}%  p90={p90:.3f}%")
    con.close()
print("\n=== DONE INTROSPECT (read-only) ===")
