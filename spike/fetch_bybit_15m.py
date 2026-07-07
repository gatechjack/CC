"""PART 2 fetch: Bybit public 15m klines, 230d, 4 coins. No auth, read-only external.
Saves data/BYBIT_{COIN}_15m_230d.csv (ts_ms,open,high,low,close,volume). Verifies
row count, ts monotonic+contiguous (900s), OHLC sanity. Bybit = original venue source
(prior spike CSV filename); provenance caveat pre-registered in the report."""
from __future__ import annotations
import csv, json, os, time, urllib.request, datetime as dt

COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
DATA = os.path.join(os.path.dirname(__file__), "data")
STEP_MS = 900_000
END_MS = int(dt.datetime(2026, 7, 6, 6, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)
START_MS = END_MS - 230 * 24 * 3600 * 1000
URL = "https://api.bybit.com/v5/market/kline"

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
def fetch_page(symbol, end_ms):
    q = f"{URL}?category=linear&symbol={symbol}&interval=15&end={end_ms}&limit=1000"
    req = urllib.request.Request(q, headers={"User-Agent": _UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.loads(r.read().decode())
    if d.get("retCode") != 0:
        raise RuntimeError(f"{symbol}: retCode={d.get('retCode')} {d.get('retMsg')}")
    return d["result"]["list"]   # newest-first: [startMs,o,h,l,c,vol,turnover]

def fetch_all(symbol):
    rows = {}
    end = END_MS
    while True:
        page = fetch_page(symbol, end)
        if not page:
            break
        for k in page:
            t = int(k[0])
            if START_MS <= t <= END_MS:
                rows[t] = (t, float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]))
        oldest = min(int(k[0]) for k in page)
        if oldest <= START_MS:
            break
        end = oldest - 1
        time.sleep(0.12)
    return [rows[t] for t in sorted(rows)]

def verify(symbol, bars):
    n = len(bars)
    gaps = sum(1 for i in range(1, n) if bars[i][0] - bars[i-1][0] != STEP_MS)
    mono = all(bars[i][0] > bars[i-1][0] for i in range(1, n))
    bad = sum(1 for b in bars if not (b[3] <= b[1] <= b[2] and b[3] <= b[4] <= b[2] and b[3] <= b[2]))
    span_d = (bars[-1][0] - bars[0][0]) / 86400000 if n else 0
    first = dt.datetime.fromtimestamp(bars[0][0]/1000, dt.timezone.utc).isoformat() if n else "-"
    last = dt.datetime.fromtimestamp(bars[-1][0]/1000, dt.timezone.utc).isoformat() if n else "-"
    print(f"{symbol}: n={n} span={span_d:.1f}d gaps={gaps} monotonic={mono} ohlc_bad={bad}")
    print(f"    first={first} last={last}")
    return gaps, bad

def main():
    os.makedirs(DATA, exist_ok=True)
    for c in COINS:
        bars = fetch_all(c)
        path = os.path.join(DATA, f"BYBIT_{c}_15m_230d.csv")
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            for b in bars:
                w.writerow(b)
        verify(c, bars)
    print("DONE")

if __name__ == "__main__":
    main()
