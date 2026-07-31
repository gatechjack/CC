"""
00_corpus_audit.py  --  Read-only audit of the local crypto bar corpus.

Purpose: before any backtest, establish exactly which (coin, timeframe) cells
exist, their date ranges, freshness, provenance, and internal-gap health.
Reads the per-coin scalping DBs strictly read-only. Touches nothing else.

Run:  python 00_corpus_audit.py
"""
import sqlite3, os, json, sys

DATA_DIR = r"C:\Users\AA Incorporado\cc\data"
DBS = {
    "BTC": "btc_scalping.db",
    "ETH": "eth_scalping.db",
    "SOL": "sol_scalping.db",
    "XRP": "xrp_scalping.db",
}
# nominal seconds per timeframe, for gap detection
TF_SECS = {
    "bars_1m": 60, "bars_3m": 180, "bars_15m": 900,
    "bars_30m": 1800, "bars_1h": 3600, "bars_4h": 14400, "bars_1d": 86400,
}

def ro(db):
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True)

def cols_of(cur, tbl):
    return [r[1] for r in cur.execute(f"pragma table_info('{tbl}')")]

def pick(colnames, *cands):
    low = {c.lower(): c for c in colnames}
    for c in cands:
        if c in low:
            return low[c]
    return None

def audit_table(cur, tbl):
    cs = cols_of(cur, tbl)
    tcol = pick(cs, "ts", "timestamp", "open_time", "time", "t")
    dcol = pick(cs, "datetime_utc", "datetime", "date")
    n = cur.execute(f'select count(*) from "{tbl}"').fetchone()[0]
    out = {"table": tbl, "rows": n, "ts_col": tcol, "dt_col": dcol}
    if n == 0:
        return out
    # date range
    if dcol:
        mn = cur.execute(f'select min("{dcol}"), max("{dcol}") from "{tbl}"').fetchone()
        out["dt_min"], out["dt_max"] = mn
    if tcol:
        mn = cur.execute(f'select min("{tcol}"), max("{tcol}") from "{tbl}"').fetchone()
        out["ts_min"], out["ts_max"] = mn
        # normalize ts to seconds (detect ms)
        step = TF_SECS.get(tbl)
        tmn, tmx = mn
        scale = 1000 if tmx and tmx > 10_000_000_000 else 1  # >~2286 in sec => ms
        out["ts_unit"] = "ms" if scale == 1000 else "s"
        if step and n > 1:
            span_s = (tmx - tmn) / scale
            expected = span_s / step + 1
            out["expected_bars"] = round(expected)
            out["missing_bars"] = round(expected) - n
            out["coverage_pct"] = round(100.0 * n / expected, 2) if expected else None
    return out

def main():
    report = {}
    for coin, dbname in DBS.items():
        path = os.path.join(DATA_DIR, dbname)
        if not os.path.exists(path):
            report[coin] = {"error": "db missing", "path": path}
            continue
        con = ro(path); cur = con.cursor()
        tbls = [n for (n,) in cur.execute(
            "select name from sqlite_master where type='table' order by name")]
        bar_tbls = [t for t in tbls if t.startswith("bars_")]
        entry = {"db": dbname, "all_tables": tbls, "bar_tables": {}}
        for t in bar_tbls:
            entry["bar_tables"][t] = audit_table(cur, t)
        # provenance
        if "source_files" in tbls:
            try:
                sf_cols = cols_of(cur, "source_files")
                rows = cur.execute("select * from source_files").fetchall()
                entry["source_files"] = {"cols": sf_cols,
                                         "rows": [list(r) for r in rows]}
            except Exception as e:
                entry["source_files"] = {"error": str(e)}
        con.close()
        report[coin] = entry
    print(json.dumps(report, indent=2, default=str))

if __name__ == "__main__":
    main()
