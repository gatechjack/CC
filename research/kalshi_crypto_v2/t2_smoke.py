"""T2 local smoke: prep a TEMP sqlite db (kcv2 tables + real 15m bars) and check
observer output. Never touches prod. Run the observer in between with
TRADING_CORP_DB_URL pointed at the temp db.

  run_capped python research/kalshi_crypto_v2/t2_smoke.py prep
  (run observer with KCV2_MAX_SECONDS=40 + TRADING_CORP_DB_URL=<temp>)
  run_capped python research/kalshi_crypto_v2/t2_smoke.py check
"""
from __future__ import annotations

import csv
import os
import sqlite3
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
TEMP_DB = os.path.join(_HERE, "_smoke.db")
BARS_CSV = os.path.join(_HERE, "bitunix_bars_export.csv")


def prep() -> int:
    from migrate_kcv2_tables import DDL
    if os.path.exists(TEMP_DB):
        os.remove(TEMP_DB)
    conn = sqlite3.connect(TEMP_DB)
    conn.executescript(DDL)
    conn.execute("""CREATE TABLE IF NOT EXISTS bitunix_bar_history(
        symbol TEXT, ts_ms INTEGER, timeframe TEXT, open REAL, high REAL, low REAL,
        close REAL, volume REAL, inserted_at TEXT, PRIMARY KEY(symbol,ts_ms,timeframe))""")
    n = 0
    if os.path.exists(BARS_CSV):
        with open(BARS_CSV, newline="") as f:
            for r in csv.DictReader(f):
                if r["timeframe"] != "15m":
                    continue
                conn.execute("INSERT OR IGNORE INTO bitunix_bar_history VALUES(?,?,?,?,?,?,?,?,?)",
                             (r["symbol"], int(r["ts_ms"]), "15m", float(r["open"]),
                              float(r["high"]), float(r["low"]), float(r["close"]),
                              float(r["volume"]), "smoke"))
                n += 1
    conn.commit()
    conn.close()
    print(f"prepped {TEMP_DB}: kcv2 tables created, {n} 15m bars loaded")
    print(f"now run: $env:TRADING_CORP_DB_URL='{TEMP_DB}'; $env:KCV2_MAX_SECONDS='40'; "
          "run observer")
    return 0


def check() -> int:
    conn = sqlite3.connect(TEMP_DB)
    for t in ("kcv2_index_ticks", "kcv2_quotes", "kcv2_signals", "kcv2_heartbeat"):
        n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"{t:20} rows={n}")
    print("\nheartbeat rows:")
    for r in conn.execute("SELECT cycle_id,rows_index,rows_quotes,rows_signals,"
                          "n_markets_active,index_ws_connected,alarm,note FROM kcv2_heartbeat "
                          "ORDER BY cycle_id"):
        print(f"  cycle={r[0]} idx={r[1]} quotes={r[2]} signals={r[3]} mkts={r[4]} "
              f"ws={r[5]} alarm={r[6]} note={r[7]}")
    print("\nsample quote rows (guard + band recorded):")
    for r in conn.execute("SELECT asset,cadence,market_ticker,moneyness,band_pct,yes_bid,yes_ask,"
                          "no_bid,no_ask,sum_to_1_ok FROM kcv2_quotes ORDER BY id LIMIT 6"):
        print(f"  {r[0]} {r[1]:13} {r[2]:26} mny={r[3]} band={r[4]} "
              f"y=({r[5]},{r[6]}) n=({r[7]},{r[8]}) sum1ok={r[9]}")
    print("\nsample signal rows (computed_bar_ts_ms present):")
    for r in conn.execute("SELECT asset,sfp_mode,state,computed_bar_ts_ms FROM kcv2_signals "
                          "ORDER BY id LIMIT 8"):
        print(f"  {r[0]} {str(r[1]):12} {r[2]:9} computed_bar_ts_ms={r[3]}")
    conn.close()
    return 0


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    raise SystemExit(prep() if mode == "prep" else check())
