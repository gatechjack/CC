"""STEP 2 fetch: Coinbase INTX public 15m klines, 230d, 4 perps. PUBLIC candles ONLY
(no auth, no account). Backward pagination (cap=300/call, newest-first). Saves
data/COINBASE_INTX_{SYMBOL}_15m_230d.csv (ts_ms,open,high,low,close,volume). Verifies
count, ts monotonic + 900s spacing (gaps), OHLC internal consistency."""
from __future__ import annotations
import csv, json, os, time, urllib.request as u, datetime as dt

BASE = "https://api.international.coinbase.com/api/v1"
SYMS = ["BTC-PERP", "ETH-PERP", "SOL-PERP", "XRP-PERP"]
DATA = os.path.join(os.path.dirname(__file__), "data")
STEP_MS = 900_000
END = dt.datetime(2026, 7, 6, 6, 0, tzinfo=dt.timezone.utc)
START = END - dt.timedelta(days=230)
END_MS, START_MS = int(END.timestamp()*1000), int(START.timestamp()*1000)
UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

def iso_ms(s): return int(dt.datetime.fromisoformat(s.replace("Z","+00:00")).timestamp()*1000)

def page(sym, end_iso):
    q = (f"{BASE}/instruments/{sym}/candles?granularity=FIFTEEN_MINUTE"
         f"&start={START.strftime('%Y-%m-%dT%H:%M:%SZ')}&end={end_iso}")
    d = json.loads(u.urlopen(u.Request(q, headers=UA), timeout=30).read().decode())
    return d if isinstance(d, list) else d.get("candles", d.get("aggregations", d.get("data", [])))

def fetch(sym):
    rows = {}
    end = END
    while True:
        p = page(sym, end.strftime("%Y-%m-%dT%H:%M:%SZ"))
        if not p:
            break
        for k in p:
            t = iso_ms(k["start"])
            if START_MS <= t <= END_MS:
                rows[t] = (t, float(k["open"]), float(k["high"]), float(k["low"]),
                           float(k["close"]), float(k.get("volume", 0)))
        oldest = min(iso_ms(k["start"]) for k in p)
        if oldest <= START_MS:
            break
        end = dt.datetime.fromtimestamp(oldest/1000, dt.timezone.utc)
        time.sleep(0.12)
    return [rows[t] for t in sorted(rows)]

def verify(sym, bars):
    n = len(bars)
    gaps = [(bars[i-1][0], bars[i][0]) for i in range(1, n) if bars[i][0]-bars[i-1][0] != STEP_MS]
    mono = all(bars[i][0] > bars[i-1][0] for i in range(1, n))
    bad = sum(1 for b in bars if not (b[3] <= min(b[1],b[4]) and max(b[1],b[4]) <= b[2] and b[3] <= b[2]))
    span = (bars[-1][0]-bars[0][0])/86400000 if n else 0
    f = dt.datetime.fromtimestamp(bars[0][0]/1000, dt.timezone.utc).date() if n else "-"
    l = dt.datetime.fromtimestamp(bars[-1][0]/1000, dt.timezone.utc).date() if n else "-"
    print(f"{sym}: n={n} span={span:.1f}d gaps={len(gaps)} monotonic={mono} ohlc_bad={bad} [{f}..{l}]")
    if gaps[:3]:
        print(f"    first gaps: {gaps[:3]}")
    return n, len(gaps), bad

def main():
    os.makedirs(DATA, exist_ok=True)
    results = {}
    for s in SYMS:
        bars = fetch(s)
        with open(os.path.join(DATA, f"COINBASE_INTX_{s}_15m_230d.csv"), "w", newline="") as f:
            csv.writer(f).writerows(bars)
        results[s] = verify(s, bars)
    ok = all(n >= 21000 and g == 0 and b == 0 for n, g, b in results.values())
    print("VERIFY_PASS" if ok else "VERIFY_FLAG")

if __name__ == "__main__":
    main()
