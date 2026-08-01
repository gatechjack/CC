"""List all tables in the local DB (read-only), row counts, and flag any
bar/OHLCV/candle/kline/price tables usable for the SFP retro-test."""
from __future__ import annotations

import os
import sqlite3
import sys

DEFAULT_DB = r"C:\Users\AA Incorporado\cc\data\trading_corp.db"
FLAG = ("bar", "ohlc", "candle", "kline", "price", "spot", "vol")


def main() -> int:
    db_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DB
    uri = "file:" + db_path.replace("\\", "/").replace(" ", "%20") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    cur = conn.cursor()
    tabs = [r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
    print(f"{len(tabs)} tables total.\n")
    flagged = []
    for t in tabs:
        low = t.lower()
        if any(f in low for f in FLAG):
            try:
                n = cur.execute(f"SELECT COUNT(*) FROM '{t}'").fetchone()[0]
            except Exception as e:
                n = f"ERR {e}"
            flagged.append((t, n))
    print("=== bar/ohlcv/candle/kline/price/spot/vol tables ===")
    if flagged:
        for t, n in flagged:
            print(f"  {t:45} {n:>12,}" if isinstance(n, int) else f"  {t:45} {n}")
    else:
        print("  (none)")
    print("\n=== all table names ===")
    for t in tabs:
        print(f"  {t}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
