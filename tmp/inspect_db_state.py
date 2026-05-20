"""One-shot DB state inspection for the BitUnix 1m resume task."""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB = Path(r"C:\Users\AA Incorporado\cc\data\btc_scalping.db")
con = sqlite3.connect(DB)
cur = con.cursor()

print("TABLES:")
for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall():
    print(" ", r[0])
print()

def iso(ts):
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

for t in ("bars_1m", "bars_3m", "bars_15m"):
    row = cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()
    if not row:
        print(f"  {t}: MISSING")
        continue
    n = cur.execute(f"SELECT COUNT(1) FROM {t}").fetchone()[0]
    mn, mx = cur.execute(f"SELECT MIN(ts), MAX(ts) FROM {t}").fetchone()
    print(f"  {t}: rows={n}  min={iso(mn)}  max={iso(mx)}")

con.close()
