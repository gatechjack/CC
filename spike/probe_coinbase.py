"""STEP 1 PROBE (read-only, PUBLIC market-data ONLY - no auth, no account, no orders).
Identify Coinbase INTX (International, perp) reachability + exact perp symbols for
BTC/ETH/SOL/XRP + oldest 15m candle ts (confirm 230d back exists). CFM noted if INTX fails."""
from __future__ import annotations
import json, urllib.request as u, datetime as dt

UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
INTX = "https://api.international.coinbase.com/api/v1"
TARGET = ["BTC", "ETH", "SOL", "XRP"]
END = dt.datetime(2026, 7, 6, 6, 0, tzinfo=dt.timezone.utc)
BACK230 = END - dt.timedelta(days=230)

def get(url):
    with u.urlopen(u.Request(url, headers=UA), timeout=25) as r:
        return json.loads(r.read().decode())

def err(e):
    b = ""
    try: b = e.read().decode()[:160]
    except Exception: pass
    return f"{getattr(e,'code',type(e).__name__)} {b[:150]!r}"

def main():
    print("=== INTX /instruments (public) ===")
    try:
        inst = get(f"{INTX}/instruments")
    except Exception as e:
        print("  INSTRUMENTS ERR:", err(e)); return
    items = inst if isinstance(inst, list) else inst.get("instruments", inst.get("data", []))
    print(f"  total instruments: {len(items)}")
    perps = {}
    for it in items:
        sym = it.get("symbol") or it.get("instrument_id") or ""
        typ = (it.get("type") or it.get("instrument_type") or "").upper()
        base = (it.get("base_asset_name") or it.get("base") or "").upper()
        if "PERP" in typ or sym.endswith("-PERP"):
            for t in TARGET:
                if sym.startswith(t + "-") or base == t:
                    perps[t] = sym
    print("  target perp symbols:", perps)
    print(f"\n=== per-symbol 15m candle depth probe (want data at {BACK230.date()}) ===")
    for t in TARGET:
        sym = perps.get(t)
        if not sym:
            print(f"  {t}: NO PERP SYMBOL FOUND"); continue
        s = BACK230.strftime("%Y-%m-%dT%H:%M:%SZ")
        e = (BACK230 + dt.timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
        url = f"{INTX}/instruments/{sym}/candles?granularity=FIFTEEN_MINUTE&start={s}&end={e}"
        try:
            d = get(url)
            rows = d if isinstance(d, list) else d.get("candles", d.get("data", d.get("aggregations", [])))
            n = len(rows)
            first = rows[0] if n else None
            print(f"  {t} ({sym}): 230d-back window rows={n} sample={str(first)[:80]}")
        except Exception as ex:
            print(f"  {t} ({sym}): CANDLE ERR {err(ex)}")

if __name__ == "__main__":
    main()
