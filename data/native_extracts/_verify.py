import sqlite3, sys
db = sys.argv[1]
con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
def has(t):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone() is not None
if has("bars_3m"):
    r = con.execute("SELECT COUNT(*), MIN(ts), MAX(ts), ROUND(SUM(close),3), ROUND(SUM(volume),3) FROM bars_3m").fetchone()
    print(f"bars_3m(Bybit): count={r[0]} min_ts={r[1]} max_ts={r[2]} sum_close={r[3]} sum_vol={r[4]}")
else:
    print("bars_3m(Bybit): ABSENT")
for t in ("bars_3m_bitunix", "bars_15m"):
    if has(t):
        r = con.execute(f'SELECT COUNT(*), MIN(ts), MAX(ts) FROM "{t}"').fetchone()
        print(f"{t}: count={r[0]} min_ts={r[1]} max_ts={r[2]}")
    else:
        print(f"{t}: ABSENT")
con.close()
